"""단일 서버 병목 탐색 (docs/11_deployment_test_env_plan.md 계획 A) — OpenAI 비용 0.

스텁(가짜 OpenAI)과 앱 1프로세스를 직접 띄우고, k6로 VU를 단계별로 올리며 레벨마다 잰다:
  - 처리량·응답시간·실패       k6 요약
  - 앱 프로세스 CPU           psutil (요청당 CPU-초와 평균 사용률)
  - 이벤트 루프 지연           /api/health를 0.1초마다 두드린 응답시간 (루프가 막히면 이게 먼저 커진다)
  - 호출 수                   스텁이 센 종류별 OpenAI 호출 수 (요청당 호출이 실제와 비슷한지 확인)

    uv run --directory apps/api --with psutil python ../../load/stub/bench.py --pipeline native --levels 10,20,40,60

주의: 로컬 머신의 CPU는 Cloud Run 1 vCPU와 속도가 다르다. 절대값이 아니라 "어느 부하에서 꺾이는가"와
요청당 CPU의 상대 비교(개선 전후)를 본다. 배포 환경의 실측(native 요청당 약 0.12 vCPU-초)과 맞춰 환산한다.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import psutil

ROOT = Path(__file__).resolve().parents[2]
API_DIR = ROOT / "apps" / "api"
STUB_PORT, APP_PORT = 8199, 8101


def wait_ready(url: str, timeout: float = 90.0) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            if httpx.get(url, timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"{url} 가 {timeout:.0f}초 안에 뜨지 않았습니다")


class Sampler(threading.Thread):
    """앱 프로세스 CPU와 /api/health 응답시간을 측정 구간 동안 모은다."""

    def __init__(self, proc: psutil.Process) -> None:
        super().__init__(daemon=True)
        self.proc, self.stop_flag = proc, threading.Event()
        self.cpu: list[tuple[float, float]] = []  # (시각, 누적 CPU 초)
        self.lag: list[tuple[float, float]] = []  # (시각, 응답시간 ms)
        self.rss_mb = 0.0
        self.slots: list[tuple[float, float]] = []  # (시각, cdq_semaphore_in_use)

    def run(self) -> None:
        client = httpx.Client(base_url=f"http://127.0.0.1:{APP_PORT}", timeout=30)
        last_cpu = 0.0
        while not self.stop_flag.is_set():
            now = time.time()
            if now - last_cpu >= 1.0:
                # 윈도우의 venv python.exe는 진짜 인터프리터를 자식으로 띄우는 런처라, 자식까지 합쳐야 앱의 CPU가 된다
                procs = [self.proc, *self.proc.children(recursive=True)]
                self.cpu.append((now, sum(p.cpu_times().user + p.cpu_times().system for p in procs)))
                self.rss_mb = max(self.rss_mb, sum(p.memory_info().rss for p in procs) / 1e6)
                last_cpu = now
                try:
                    body = client.get("/metrics").text
                    m = re.search(r"^cdq_semaphore_in_use ([0-9.]+)", body, re.M)
                    if m:
                        self.slots.append((now, float(m.group(1))))
                except httpx.HTTPError:
                    pass
            t0 = time.perf_counter()
            try:
                client.get("/api/health")
                self.lag.append((now, (time.perf_counter() - t0) * 1000))
            except httpx.HTTPError:
                self.lag.append((now, 30000.0))
            time.sleep(0.1)


def pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round((len(ordered) - 1) * q)))] if ordered else float("nan")


def run_level(pipeline: str, vus: int, duration: int, app: psutil.Process) -> dict:
    httpx.post(f"http://127.0.0.1:{STUB_PORT}/stub/reset", timeout=5)
    sampler = Sampler(app)
    sampler.start()
    t_start = time.time()
    env = {
        **os.environ,
        "API_BASE_URL": f"http://localhost:{APP_PORT}",
        "K6_STUB": "1",
        "K6_TARGET": "stub-local",
        "K6_NO_WARMUP": "1",
        "LOAD_VUS": str(vus),
        "LOAD_DURATION": f"{duration}s",
    }
    out = subprocess.run(
        # "bash"만 쓰면 윈도우가 System32의 WSL 런처를 먼저 잡는다 — which로 찾은 Git Bash 전체 경로를 쓴다
        [shutil.which("bash") or "bash", str(ROOT / "load" / "k6" / "run.sh"), pipeline, "steady"],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=ROOT,
    )
    t_end = time.time()
    sampler.stop_flag.set()
    sampler.join(timeout=5)

    m = re.search(r"load/k6/results/(\S+\.json)", out.stdout)
    if not m:
        raise RuntimeError(
            f"k6 요약 경로를 못 찾았습니다 (bash={shutil.which('bash')}, exit={out.returncode})\n"
            f"stdout: {out.stdout[-600:]}\nstderr: {out.stderr[-600:]}"
        )
    summary = json.loads((ROOT / "load" / "k6" / "results" / m.group(1)).read_text(encoding="utf-8"))["metrics"]
    dur = summary[f"http_req_duration{{pipeline:{pipeline}}}"]
    iterations = summary["iterations"]["count"]
    checks = summary["checks"]

    # 정상 상태 구간: 시작 직후 한꺼번에 몰리는 구간(25%)과 종료 후 마무리 구간을 뺀다
    w0, w1 = t_start + 0.25 * duration, t_start + duration
    cpu = [(t, c) for t, c in sampler.cpu if w0 <= t <= w1]
    cpu_pct = (cpu[-1][1] - cpu[0][1]) / (cpu[-1][0] - cpu[0][0]) * 100 if len(cpu) >= 2 else float("nan")
    lag = [ms for t, ms in sampler.lag if w0 <= t <= w1]
    total_cpu = (sampler.cpu[-1][1] - sampler.cpu[0][1]) if len(sampler.cpu) >= 2 else float("nan")
    slots = [v for t, v in sampler.slots if w0 <= t <= w1]
    calls = httpx.get(f"http://127.0.0.1:{STUB_PORT}/stub/stats", timeout=5).json()["calls"]

    return {
        "pipeline": pipeline,
        "vus": vus,
        "duration_sec": duration,
        "wall_sec": round(t_end - t_start, 1),
        "iterations": iterations,
        "throughput_rps": round(iterations / (t_end - t_start), 2),
        "latency_med_s": round(dur["med"] / 1000, 2),
        "latency_p95_s": round(dur["p(95)"] / 1000, 2),
        "latency_max_s": round(dur["max"] / 1000, 2),
        "failed_pct": round(summary["http_req_failed"]["value"] * 100, 2),
        "checks_pct": round(checks["passes"] / max(1, checks["passes"] + checks["fails"]) * 100, 1),
        "app_cpu_pct": round(cpu_pct, 1),
        "cpu_sec_per_request": round(total_cpu / max(1, iterations), 4),
        "loop_lag_p50_ms": round(pct(lag, 0.5), 1),
        "loop_lag_p99_ms": round(pct(lag, 0.99), 1),
        "loop_lag_max_ms": round(max(lag), 1) if lag else float("nan"),
        "slots_avg": round(statistics.fmean(slots), 1) if slots else float("nan"),
        "slots_max": max(slots) if slots else float("nan"),
        "rss_mb": round(sampler.rss_mb),
        "openai_calls_per_request": round(sum(calls.values()) / max(1, iterations), 1),
        "stub_calls": calls,
        "k6_exit": out.returncode,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pipeline", required=True, choices=["vanilla", "native", "agentic"])
    ap.add_argument("--levels", default="10,20,40,60", help="VU 단계, 쉼표 구분")
    ap.add_argument("--duration", type=int, default=45, help="레벨당 유지 시간(초). agentic은 100 이상 권장")
    ap.add_argument("--scale", default="1.0", help="스텁 지연 배율 (0이면 지연 없음)")
    ap.add_argument("--max-runs", default="20", help="앱의 MAX_CONCURRENT_RUNS (세마포어 크기, 운영 기본 20)")
    ap.add_argument("--langfuse", action="store_true", help="LangFuse 트레이싱을 켠다 (실제 LangFuse로 전송되니 주의)")
    args = ap.parse_args()

    stub = subprocess.Popen(
        [sys.executable, str(ROOT / "load" / "stub" / "llm_stub.py")],
        env={**os.environ, "STUB_LATENCY_SCALE": args.scale, "PORT": str(STUB_PORT)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    app_env = {
        **os.environ,
        "OPENAI_BASE_URL": f"http://127.0.0.1:{STUB_PORT}/v1",
        "METRICS_ENABLED": "true",
        "MAX_CONCURRENT_RUNS": args.max_runs,
        "LANGFUSE_ENABLED": "true" if args.langfuse else "false",
    }
    app = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(APP_PORT)],
        cwd=API_DIR,
        env=app_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    results: list[dict] = []
    try:
        wait_ready(f"http://127.0.0.1:{STUB_PORT}/health")
        wait_ready(f"http://127.0.0.1:{APP_PORT}/api/health")
        proc = psutil.Process(app.pid)
        print(
            f"앱 PID {app.pid}, MAX_CONCURRENT_RUNS={args.max_runs}, 스텁 지연 배율 {args.scale}, LangFuse {'켬' if args.langfuse else '끔'}\n"
        )
        head = f"{'VU':>4} {'처리량':>7} {'med':>6} {'p95':>6} {'max':>6} {'실패%':>5} {'체크%':>5} {'CPU%':>5} {'CPU초/요청':>10} {'루프지연p50':>11} {'p99':>7} {'max':>7} {'호출/요청':>8} {'슬롯평균':>7} {'최대':>4}"
        print(head)
        print("-" * len(head))
        for vus in [int(v) for v in args.levels.split(",")]:
            r = run_level(args.pipeline, vus, args.duration, proc)
            results.append(r)
            print(
                f"{r['vus']:>4} {r['throughput_rps']:>6.2f}/s {r['latency_med_s']:>5.1f}s {r['latency_p95_s']:>5.1f}s "
                f"{r['latency_max_s']:>5.1f}s {r['failed_pct']:>5.1f} {r['checks_pct']:>5.1f} {r['app_cpu_pct']:>5.1f} "
                f"{r['cpu_sec_per_request']:>10.4f} {r['loop_lag_p50_ms']:>9.1f}ms {r['loop_lag_p99_ms']:>5.0f}ms "
                f"{r['loop_lag_max_ms']:>5.0f}ms {r['openai_calls_per_request']:>8.1f}",
                flush=True,
            )
            time.sleep(3)
    finally:
        for p in (app, stub):
            p.terminate()
        for p in (app, stub):
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()

    out_dir = ROOT / "load" / "stub" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{args.pipeline}_{datetime.now(UTC):%Y%m%d-%H%M%S}.json"
    out.write_text(
        json.dumps(
            {
                "args": vars(args),
                "levels": results,
                "stat_median_cpu_sec_per_request": statistics.median(r["cpu_sec_per_request"] for r in results),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n저장: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
