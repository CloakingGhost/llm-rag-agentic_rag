"""도구 편향(Tool-use Bias) 재현 실험 — 논문 9.2절.

논문은 도구를 붙이자 "일반적인 법률 질의를 특정 사용자의 주문 처리 요청으로 오분류하여
불필요하게 가상 DB를 조회하고, 내역이 없다는 이유로 오답을 반환하는" 사례를 관찰했고,
그래서 최종 평가에서 도구 모듈을 껐다(논문 4.1).

같은 질문을 도구 끔/켬 두 조건으로 돌려 그 현상이 우리 구현에서도 나오는지 본다.
Agentic 파이프라인만 실행한다 (대조군은 도구와 무관하므로).

    uv run python -m cli.tool_bias --policy 8 --model gpt-5.6-luna
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import UTC, datetime

import httpx

from app.config import DEFAULT_MODEL, MODEL_CATALOG, get_settings
from app.tools import mock_store
from cli.compare import pick_questions
from kb_build.paths import REPO_DIR

API = "http://localhost:8100"
NEWLINE = chr(10)

# 도구를 쓰는 것이 맞는 질문. 라우터가 system_action으로 보내야 한다
SYSTEM_ACTION_QUESTIONS = [
    "내 주문 내역 좀 보여주세요.",
    "ORD-20260901-0001 주문이 언제 배송됐는지 알려주세요.",
    "ORD-20260815-0003 환불 어디까지 진행됐나요?",
    "ORD-20260910-0002 주문 환불 접수해 주세요.",
]

# 계정 데이터에 끌려간 답변인지 보는 표지
ACCOUNT_MARKERS = ("주문 내역", "주문번호", "조회되지", "확인되지 않", "내역이 없", "확인할 수 없")


async def ask(client: httpx.AsyncClient, question: str, model: str, key: str, tools: bool) -> dict:
    body = {
        "mode": "agentic",
        "question": question,
        "clientId": f"tool-bias-{'on' if tools else 'off'}",
        "model": model,
        "tools": tools,
    }
    run: dict = {}
    async with client.stream(
        "POST", f"{API}/api/chat", json=body, headers={"X-OpenAI-Key": key}, timeout=420
    ) as response:
        response.raise_for_status()
        event = ""
        async for line in response.aiter_lines():
            if line.startswith("event: "):
                event = line[7:].strip()
            elif line.startswith("data: "):
                payload = json.loads(line[6:])
                if event == "run_done":
                    run = payload
                elif event == "run_error":
                    run = {"outcome": "failed", "answer": payload.get("message", ""), "metrics": {}, "trace": {}}
    return run


def summarize(rows: list[dict], kind: str) -> dict:
    scoped = [r for r in rows if r["kind"] == kind]
    if not scoped:
        return {}
    routes: dict[str, int] = {}
    tools_used = schema_errors = not_found = fallbacks = account_pulled = 0
    tokens = latency = 0.0
    for row in scoped:
        run = row["run"]
        trace = run.get("trace") or {}
        route = (trace.get("route") or {}).get("route", "?")
        routes[route] = routes.get(route, 0) + 1
        calls = trace.get("toolCalls") or []
        tools_used += len(calls)
        schema_errors += sum(1 for c in calls if c.get("error") in ("schema_error", "invalid_json"))
        not_found += sum(1 for c in calls if c.get("error") == "not_found")
        fallbacks += 1 if run.get("outcome") == "fallback" else 0
        metrics = run.get("metrics") or {}
        tokens += (metrics.get("tokensIn") or 0) + (metrics.get("tokensOut") or 0)
        latency += (metrics.get("latencyMs") or 0) / 1000
        if any(marker in (run.get("answer") or "") for marker in ACCOUNT_MARKERS):
            account_pulled += 1
    n = len(scoped)
    return {
        "n": n,
        "routes": routes,
        "toolCalls": tools_used,
        "schemaErrors": schema_errors,
        "notFound": not_found,
        "fallbacks": fallbacks,
        "accountPulled": account_pulled,
        "avgTokens": tokens / n,
        "avgLatency": latency / n,
    }


def _route_text(routes: dict[str, int]) -> str:
    label = {"policy_inquiry": "정책", "system_action": "시스템", "out_of_domain": "도메인밖", "?": "미상"}
    return " / ".join(f"{label.get(k, k)} {v}" for k, v in sorted(routes.items()))


def render(rows: list[dict], model: str, started: str) -> str:
    out = ["# 도구 편향 재현 실험 (논문 9.2)", ""]
    out.append(f"- 생성: {started}")
    out.append(f"- 모델: **{MODEL_CATALOG[model].label}** (`{model}`)")
    out.append("- 같은 질문을 도구 **끔 / 켬** 두 조건으로 Agentic 파이프라인에 넣었습니다.")
    out.append("- 도구를 켜면 라우터가 3분기가 되고 `system_action` 경로에 Mock 주문 DB 조회가 붙습니다.")
    out.append("")

    for kind, title in (("policy", "정책 질의"), ("system", "시스템 액션 질의")):
        out.append(f"## {title}")
        out.append("")
        out.append(
            "| 조건 | 건수 | 라우팅 | 도구 호출 | 스키마 오류 | 내역 없음 | 폴백 | 계정 문구 답변 |"
            " 평균 토큰 | 평균 지연 |"
        )
        out.append("|---|---|---|---|---|---|---|---|---|---|")
        for tools in (False, True):
            stat = summarize([r for r in rows if r["tools"] is tools], kind)
            if not stat:
                continue
            out.append(
                f"| 도구 {'켬' if tools else '끔'} | {stat['n']} | {_route_text(stat['routes'])} | "
                f"{stat['toolCalls']} | {stat['schemaErrors']} | {stat['notFound']} | {stat['fallbacks']} | "
                f"{stat['accountPulled']} | {stat['avgTokens']:,.0f} | {stat['avgLatency']:.1f}초 |"
            )
        out.append("")

    out.append("'계정 문구 답변'은 답변에 `주문 내역`, `조회되지`, `확인되지 않` 같은 표현이 들어간 건수입니다.")
    out.append("정책 질의에서 이 값이 커지면 논문이 말한 '가상 DB에 끌려간 오답'입니다.")
    out.append("")
    out.append("---")
    out.append("")

    for row in rows:
        if row["tools"] is not True:
            continue
        run = row["run"]
        trace = run.get("trace") or {}
        route = (trace.get("route") or {}).get("route", "?")
        calls = trace.get("toolCalls") or []
        out.append(f"## [{row['kind']}] {row['question'][:60]}")
        out.append("")
        out.append(f"- 도구 켬 라우팅: **{route}** / 도구 호출 {len(calls)}건")
        off = next((r for r in rows if r["question"] == row["question"] and r["tools"] is False), None)
        if off:
            off_route = ((off["run"].get("trace") or {}).get("route") or {}).get("route", "?")
            out.append(f"- 도구 끔 라우팅: {off_route}")
        for call in calls:
            result = json.dumps(call["result"], ensure_ascii=False)[:160]
            out.append(f"  - `{call['tool']}` {call['arguments']} → {result}")
        out.append("")
        out.append("**도구 켬 답변**")
        out.append("")
        out.append((run.get("answer") or "").strip()[:700] or "(빈 답변)")
        out.append("")
        if off:
            out.append("**도구 끔 답변**")
            out.append("")
            out.append((off["run"].get("answer") or "").strip()[:700] or "(빈 답변)")
            out.append("")
        out.append("---")
        out.append("")

    return NEWLINE.join(out)


async def main_async(args: argparse.Namespace) -> None:
    settings = get_settings()
    if not settings.openai_api_key:
        raise SystemExit("OPENAI_API_KEY가 없습니다.")

    policy = [
        {"kind": "policy", "question": row["질문"]}
        for _, row in pick_questions(1, args.min_rows).head(args.policy).iterrows()
    ]
    system = [{"kind": "system", "question": q} for q in SYSTEM_ACTION_QUESTIONS[: args.system]]
    plan = policy + system
    print(f"질문 {len(plan)}개 (정책 {len(policy)} + 시스템 {len(system)}) × 2조건, 모델 {args.model}")

    rows: list[dict] = []
    async with httpx.AsyncClient() as client:
        for tools in (False, True):
            mock_store.reset()  # 접수된 환불을 원래대로 돌린다
            for index, item in enumerate(plan, start=1):
                started = time.monotonic()
                try:
                    run = await ask(client, item["question"], args.model, settings.openai_api_key, tools)
                except Exception as exc:  # noqa: BLE001
                    print(f"  [도구{'켬' if tools else '끔'} {index}/{len(plan)}] 실패: {exc}")
                    continue
                rows.append({**item, "tools": tools, "run": run})
                route = ((run.get("trace") or {}).get("route") or {}).get("route", "?")
                calls = len((run.get("trace") or {}).get("toolCalls") or [])
                print(
                    f"  [도구{'켬' if tools else '끔'} {index}/{len(plan)}] {item['kind']} "
                    f"{time.monotonic() - started:.0f}초 route={route} tools={calls} {run.get('outcome')}"
                )

    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M")
    out_dir = REPO_DIR / "compare"
    out_dir.mkdir(exist_ok=True)
    md_path = out_dir / f"도구편향_{args.model}_{stamp}.md"
    md_path.write_text(render(rows, args.model, datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")), encoding="utf-8")
    json_path = out_dir / f"도구편향_{args.model}_{stamp}.json"
    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{NEWLINE}완료: {md_path}")
    print(f"      {json_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="도구 편향 재현 실험")
    parser.add_argument("--policy", type=int, default=8, help="정책 질의 수")
    parser.add_argument("--system", type=int, default=4, help="시스템 액션 질의 수 (최대 4)")
    parser.add_argument("--min-rows", type=int, default=10)
    parser.add_argument("--model", default=DEFAULT_MODEL, choices=list(MODEL_CATALOG))
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
