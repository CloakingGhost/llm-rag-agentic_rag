"""앱을 cProfile 아래에서 돌리며 부하를 걸고, CPU를 가장 많이 쓰는 함수를 뽑는다 (계획 A, 비용 0).

    uv run --directory apps/api python ../../load/stub/profile_server.py --pipeline native --vus 40 --duration 40

스텁을 같이 띄우고, k6로 부하를 건 뒤, 끝나면 tottime(함수 자신이 쓴 시간) 상위와
cumtime(그 함수가 부른 것까지) 상위를 출력한다. 이벤트 루프는 메인 스레드 하나라 거기서 시간을 쓰는 곳이 곧 병목 후보다.
cProfile 자체가 2배쯤 느리게 하므로 숫자는 절대값이 아니라 "비율"로 읽는다.
"""

from __future__ import annotations

import argparse
import cProfile
import os
import pstats
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import uvicorn

ROOT = Path(__file__).resolve().parents[2]
STUB_PORT, APP_PORT = 8199, 8102


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pipeline", default="native", choices=["vanilla", "native", "agentic"])
    ap.add_argument("--vus", type=int, default=40)
    ap.add_argument("--duration", type=int, default=40)
    ap.add_argument("--top", type=int, default=22)
    ap.add_argument(
        "--max-runs", default="60", help="앱의 MAX_CONCURRENT_RUNS (세마포어를 풀어야 CPU가 병목으로 드러난다)"
    )
    args = ap.parse_args()

    stub = subprocess.Popen(
        [sys.executable, str(ROOT / "load" / "stub" / "llm_stub.py")],
        env={**os.environ, "PORT": str(STUB_PORT)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    os.environ.update(
        {
            "OPENAI_BASE_URL": f"http://127.0.0.1:{STUB_PORT}/v1",
            "LANGFUSE_ENABLED": "false",
            "METRICS_ENABLED": "true",
            "MAX_CONCURRENT_RUNS": args.max_runs,
        }
    )
    server = uvicorn.Server(uvicorn.Config("app.main:app", port=APP_PORT, log_level="warning"))

    def drive() -> None:
        try:
            for url in (f"http://127.0.0.1:{STUB_PORT}/health", f"http://127.0.0.1:{APP_PORT}/api/health"):
                while True:
                    try:
                        if httpx.get(url, timeout=2).status_code == 200:
                            break
                    except httpx.HTTPError:
                        time.sleep(0.5)
            time.sleep(1)
            env = {
                **os.environ,
                "API_BASE_URL": f"http://localhost:{APP_PORT}",
                "K6_STUB": "1",
                "K6_TARGET": "profile",
                "K6_NO_WARMUP": "1",
                "LOAD_VUS": str(args.vus),
                "LOAD_DURATION": f"{args.duration}s",
            }
            subprocess.run(
                [shutil.which("bash") or "bash", str(ROOT / "load" / "k6" / "run.sh"), args.pipeline, "steady"],
                env=env,
                capture_output=True,
                cwd=ROOT,
            )
        finally:
            server.should_exit = True

    threading.Thread(target=drive, daemon=True).start()
    profiler = cProfile.Profile()
    profiler.enable()
    server.run()
    profiler.disable()
    stub.terminate()

    stats = pstats.Stats(profiler)
    total = stats.total_tt
    print(f"\n프로파일 합계 {total:.1f}s (메인 스레드, {args.pipeline} {args.vus} VU {args.duration}s)\n")
    for title, key in (
        ("자기 자신이 쓴 시간(tottime) 상위", "tottime"),
        ("호출한 것까지 포함(cumtime) 상위", "cumtime"),
    ):
        print(f"== {title} ==")
        stats.sort_stats(key)
        rows = []
        for func, (_cc, nc, tt, ct, _callers) in stats.stats.items():
            name = f"{Path(func[0]).name}:{func[2]}" if func[0] not in ("~", "") else func[2]
            rows.append((tt if key == "tottime" else ct, nc, name))
        rows.sort(reverse=True)
        for value, calls, name in rows[: args.top]:
            print(f"  {value:7.2f}s {value / total * 100:5.1f}%  {calls:>9,}회  {name}")
        print()


if __name__ == "__main__":
    main()
