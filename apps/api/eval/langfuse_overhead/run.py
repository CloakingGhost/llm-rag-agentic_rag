"""LangFuse SDK가 요청 경로에 얹는 오버헤드 측정 (docs/10_observability_load_test_plan.md 3단계).

실제 LLM 호출은 지연이 수 초 단위로 흔들려서(vanilla 9.7~19.3s) 수 ms짜리 SDK 비용이 묻힌다.
그래서 응답이 즉시 오는 로컬 스텁 서버를 상대로 같은 호출을 plain / traced 두 클라이언트로
반복해 차이만 잰다. `langfuse.openai`는 import하는 순간 openai SDK를 전역 패치하므로 두 arm은
반드시 별도 프로세스로 돌린다.

한 "요청" = 부모 span 하나 + OpenAI 호출 CALLS_PER_REQUEST번 (agentic 한 번이 12~15 호출).
traced arm의 트레이스는 실제 LangFuse로 나간다 — environment="overhead-bench"로 구분된다.

실행:
    cd apps/api
    uv run python eval/langfuse_overhead/run.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

RESULTS_DIR = Path(__file__).parent / "results"
CALLS_PER_REQUEST = 12
SEQUENTIAL_REQUESTS = 25  # 300 호출
CONCURRENCY = 20  # 서비스 동시 처리 상한(세마포어 20)과 맞춘다
CONCURRENT_ROUNDS = 5  # 20 x 5 x 12 = 1200 호출
PAIRS = 3  # plain/traced를 번갈아 3번씩

# 실제 Native RAG 전량 주입 크기(평균 1만 토큰 ≈ 한글 2만 자)와 비슷한 입력, 수백 토큰 출력
PROMPT = "소비자분쟁해결기준 조항 본문 예시입니다. 품목별 교환 환불 기준과 보증기간을 설명합니다. " * 250
ANSWER = "문서에 따르면 구입 후 7일 이내 하자는 교환 또는 환불이 가능합니다. 판매처에 먼저 연락하세요. " * 12


def start_stub(port: int):
    import uvicorn
    from fastapi import FastAPI

    app = FastAPI()
    body = {
        "id": "chatcmpl-stub",
        "object": "chat.completion",
        "created": 0,
        "model": "gpt-5.6-luna",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": ANSWER}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10000, "completion_tokens": 300, "total_tokens": 10300},
    }

    @app.post("/v1/chat/completions")
    async def chat() -> dict:
        return body

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    return server


def pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round((len(ordered) - 1) * q)))]


def summarize(call_ms: list[float], request_ms: list[float]) -> dict:
    return {
        "calls": len(call_ms),
        "call_mean_ms": round(statistics.fmean(call_ms), 3),
        "call_p50_ms": round(pct(call_ms, 0.5), 3),
        "call_p95_ms": round(pct(call_ms, 0.95), 3),
        "call_p99_ms": round(pct(call_ms, 0.99), 3),
        "request_mean_ms": round(statistics.fmean(request_ms), 2),
    }


async def run_arm(arm: str, port: int) -> dict:
    traced = arm == "traced"
    if traced:
        from app.config import get_settings

        s = get_settings()
        from langfuse import Langfuse

        lf = Langfuse(
            public_key=s.langfuse_public_key,
            secret_key=s.langfuse_secret_key,
            base_url=s.langfuse_base_url,
            environment="overhead-bench",
        )
        from langfuse.openai import AsyncOpenAI
    else:
        from openai import AsyncOpenAI

    client = AsyncOpenAI(base_url=f"http://127.0.0.1:{port}/v1", api_key="stub")
    messages = [{"role": "system", "content": "당신은 소비자 분쟁 전문가입니다."}, {"role": "user", "content": PROMPT}]
    call_ms: list[float] = []

    async def call() -> None:
        t = time.perf_counter()
        extra = {"name": "bench"} if traced else {}
        await client.chat.completions.create(model="gpt-5.6-luna", messages=messages, **extra)
        call_ms.append((time.perf_counter() - t) * 1000)

    async def one_request() -> float:
        t0 = time.perf_counter()
        if traced:
            with lf.start_as_current_observation(name="bench-request", as_type="span", input="q"):
                for _ in range(CALLS_PER_REQUEST):
                    await call()
        else:
            for _ in range(CALLS_PER_REQUEST):
                await call()
        return (time.perf_counter() - t0) * 1000

    await one_request()  # 워밍업 (첫 호출의 지연 초기화 비용 제외)
    call_ms.clear()

    seq_requests = [await one_request() for _ in range(SEQUENTIAL_REQUESTS)]
    sequential = summarize(call_ms, seq_requests)

    call_ms.clear()
    conc_requests: list[float] = []
    t0 = time.perf_counter()
    for _ in range(CONCURRENT_ROUNDS):
        conc_requests += await asyncio.gather(*[one_request() for _ in range(CONCURRENCY)])
    wall = time.perf_counter() - t0
    concurrent = summarize(call_ms, conc_requests)
    concurrent["wall_sec"] = round(wall, 2)
    concurrent["calls_per_sec"] = round(len(call_ms) / wall, 1)

    flush_sec = None
    if traced:
        t = time.perf_counter()
        lf.flush()
        flush_sec = round(time.perf_counter() - t, 2)
        lf.shutdown()

    return {"arm": arm, "sequential": sequential, "concurrent": concurrent, "flush_sec": flush_sec}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arm", choices=["plain", "traced"])
    parser.add_argument("--port", type=int, default=8199)
    args = parser.parse_args()

    if args.arm:
        print(json.dumps(asyncio.run(run_arm(args.arm, args.port))))
        return

    start_stub(args.port)
    runs: dict[str, list[dict]] = {"plain": [], "traced": []}
    for i in range(PAIRS):
        for arm in ("plain", "traced"):
            out = subprocess.run(
                [sys.executable, __file__, "--arm", arm, "--port", str(args.port)],
                capture_output=True,
                text=True,
                check=True,
            )
            result = json.loads(out.stdout.strip().splitlines()[-1])
            runs[arm].append(result)
            print(
                f"[{i + 1}/{PAIRS}] {arm:6s} 순차 호출 mean={result['sequential']['call_mean_ms']}ms "
                f"동시 호출 mean={result['concurrent']['call_mean_ms']}ms "
                f"처리량={result['concurrent']['calls_per_sec']}/s flush={result['flush_sec']}"
            )

    print()
    print("=" * 72)
    print(f"스텁 서버 대상 오버헤드 (요청당 호출 {CALLS_PER_REQUEST}회, {PAIRS}쌍 중앙값)")
    print("=" * 72)
    summary: dict[str, dict] = {}
    for phase in ("sequential", "concurrent"):
        summary[phase] = {}
        for metric in ("call_mean_ms", "call_p95_ms", "call_p99_ms", "request_mean_ms"):
            plain = statistics.median(r[phase][metric] for r in runs["plain"])
            traced = statistics.median(r[phase][metric] for r in runs["traced"])
            summary[phase][metric] = {"plain": plain, "traced": traced, "diff": round(traced - plain, 3)}
            print(f"  {phase:10s} {metric:16s} plain={plain:9.3f}  traced={traced:9.3f}  차이={traced - plain:+9.3f}")
    plain_tp = statistics.median(r["concurrent"]["calls_per_sec"] for r in runs["plain"])
    traced_tp = statistics.median(r["concurrent"]["calls_per_sec"] for r in runs["traced"])
    summary["concurrent_calls_per_sec"] = {"plain": plain_tp, "traced": traced_tp}
    print(f"  동시 처리량(호출/초)  plain={plain_tp}  traced={traced_tp}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"{datetime.now(UTC).strftime('%Y-%m-%d_%H%M%S')}.json"
    config = {
        "calls_per_request": CALLS_PER_REQUEST,
        "sequential_requests": SEQUENTIAL_REQUESTS,
        "concurrency": CONCURRENCY,
        "concurrent_rounds": CONCURRENT_ROUNDS,
        "pairs": PAIRS,
        "prompt_chars": len(PROMPT),
    }
    out_path.write_text(
        json.dumps({"config": config, "summary": summary, "runs": runs}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n전체 원문(쌍별 수치 포함)을 저장했다: {out_path}")


if __name__ == "__main__":
    main()
