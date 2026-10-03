"""load/k6/results/ 의 k6 요약 JSON을 읽어 로컬 vs 배포(loadtest) 결과를 나란히 보여 준다.

    python load/k6/compare.py                  # 모드 baseline, (대상, 파이프라인)마다 가장 최근 파일
    python load/k6/compare.py --mode scaleout

파일 이름 규칙은 run.sh 가 만든다: <대상>_<파이프라인>_<모드>_<날짜-시각>.json
k6 요약의 thresholds 값은 "실패했는가"다 (true = 임계치를 넘음).
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

RESULTS = Path(__file__).resolve().parent / "results"
NAME = re.compile(r"^(?P<target>[^_]+)_(?P<pipeline>[^_]+)_(?P<mode>[^_]+)_(?P<ts>\d{8}-\d{6})\.json$")
PIPELINES = ("vanilla", "native", "agentic")


def load_latest(mode: str) -> dict[tuple[str, str], tuple[str, dict]]:
    latest: dict[tuple[str, str], tuple[str, dict]] = {}
    for path in sorted(RESULTS.glob("*.json")):
        m = NAME.match(path.name)
        if not m or m["mode"] != mode:
            continue
        key = (m["target"], m["pipeline"])
        if key not in latest or m["ts"] > latest[key][0]:
            latest[key] = (m["ts"], json.loads(path.read_text(encoding="utf-8")))
    return latest


def stats(summary: dict, pipeline: str) -> dict:
    metrics = summary["metrics"]
    dur = metrics[f"http_req_duration{{pipeline:{pipeline}}}"]
    failed = metrics["http_req_failed"]
    checks = metrics["checks"]
    crossed = [k for k, v in {**dur.get("thresholds", {}), **failed.get("thresholds", {})}.items() if v]
    return {
        "n": metrics["iterations"]["count"],
        "med": dur["med"] / 1000,
        "p95": dur["p(95)"] / 1000,
        "p99": dur.get("p(99)", float("nan")) / 1000,
        "max": dur["max"] / 1000,
        "fail": failed["value"] * 100,
        "checks": checks["passes"] / max(1, checks["passes"] + checks["fails"]) * 100,
        "crossed": crossed,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="baseline")
    args = parser.parse_args()

    latest = load_latest(args.mode)
    if not latest:
        raise SystemExit(f"{RESULTS} 에 모드 '{args.mode}' 결과가 없습니다")

    print(f"모드 {args.mode} — (대상, 파이프라인)별 가장 최근 실행, 단위 초\n")
    cols = f"{'med':>8s}{'p95':>8s}{'p99':>8s}{'max':>8s}{'실패%':>7s}{'체크%':>7s}"
    header = f"{'파이프라인':9s}{'대상':10s}{'횟수':>5s}{cols}  임계치 초과"
    print(header)
    print("-" * len(header))
    table: dict[tuple[str, str], dict] = {}
    for pipeline in PIPELINES:
        for target in sorted({t for t, p in latest if p == pipeline}):
            s = stats(latest[(target, pipeline)][1], pipeline)
            table[(target, pipeline)] = s
            crossed = ", ".join(s["crossed"]) or "-"
            print(
                f"{pipeline:9s}{target:10s}{s['n']:>5d}{s['med']:>8.2f}{s['p95']:>8.2f}{s['p99']:>8.2f}{s['max']:>8.2f}"
                f"{s['fail']:>7.1f}{s['checks']:>7.1f}  {crossed}"
            )

    pairs = [p for p in PIPELINES if ("local", p) in table and ("loadtest", p) in table]
    if pairs:
        print("\n배포 − 로컬 (같은 날 같은 조건으로 나란히 돌린 경우에 의미 있다)")
        for p in pairs:
            a, b = table[("local", p)], table[("loadtest", p)]
            print(
                f"  {p:8s} med {b['med'] - a['med']:+6.2f}s ({b['med'] / a['med']:.2f}x)   "
                f"p95 {b['p95'] - a['p95']:+6.2f}s ({b['p95'] / a['p95']:.2f}x)   max {b['max'] - a['max']:+6.2f}s"
            )


if __name__ == "__main__":
    main()
