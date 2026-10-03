"""Cloud Run 서버 쪽 지표를 Cloud Monitoring에서 읽어 요약한다 (docs/11_deployment_test_env_plan.md).

k6는 클라이언트가 본 값(응답시간·상태코드)만 안다. 인스턴스가 몇 개까지 늘었는지, CPU·메모리가 얼마나 찼는지,
새 인스턴스가 뜨는 데 얼마나 걸렸는지(콜드스타트)는 서버 쪽 지표로만 알 수 있다. 부하테스트 직후에 돌린다.

    python load/gcp_metrics.py --service cdq-api-loadtest --minutes 15
    python load/gcp_metrics.py --service cdq-api-loadtest --start 2026-10-03T06:00:00Z --end 2026-10-03T06:20:00Z

`gcloud auth print-access-token`으로 받은 토큰으로 Monitoring REST API를 직접 부른다.
추가 설치 없이 표준 라이브러리만 쓴다.
프로젝트는 --project, 없으면 deploy/.env의 PROJECT_ID, 그것도 없으면 gcloud 기본 프로젝트를 쓴다.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = "https://monitoring.googleapis.com/v3/projects/{project}/timeSeries"
PERIOD = 60  # 초. 1분 단위로 맞춰 본다


def gcloud(*args: str) -> str:
    # Windows에서는 gcloud가 .cmd라 shell=True가 필요하다
    out = subprocess.run(["gcloud", *args], capture_output=True, text=True, check=True, shell=True)
    return out.stdout.strip()


def project_id(cli_value: str | None) -> str:
    if cli_value:
        return cli_value
    env = ROOT / "deploy" / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("PROJECT_ID="):
                return line.split("=", 1)[1].strip().strip('"')
    return gcloud("config", "get-value", "project")


def fetch(token: str, project: str, service: str, metric: str, start: str, end: str, **agg: str) -> list[dict]:
    params = {
        "filter": f'metric.type="{metric}" AND resource.labels.service_name="{service}"',
        "interval.startTime": start,
        "interval.endTime": end,
        "aggregation.alignmentPeriod": f"{PERIOD}s",
        **{f"aggregation.{k}": v for k, v in agg.items()},
    }
    url = API.format(project=project) + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp).get("timeSeries", [])


def value(point: dict) -> float:
    v = point["value"]
    return float(v.get("doubleValue", v.get("int64Value", 0)))


def per_minute(series: list[dict]) -> dict[str, float]:
    """여러 시계열을 시각별로 합친다 (시각 -> 값)."""
    acc: dict[str, float] = {}
    for s in series:
        for p in s["points"]:
            acc[p["interval"]["endTime"]] = acc.get(p["interval"]["endTime"], 0.0) + value(p)
    return dict(sorted(acc.items()))


def percentile_series(token, project, service, metric, start, end, pct: int) -> dict[str, float]:
    series = fetch(
        token,
        project,
        service,
        metric,
        start,
        end,
        perSeriesAligner="ALIGN_DELTA",
        crossSeriesReducer=f"REDUCE_PERCENTILE_{pct}",
    )
    return per_minute(series)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--service", required=True, help="Cloud Run 서비스 이름 (예: cdq-api-loadtest)")
    parser.add_argument("--minutes", type=int, default=15, help="지금부터 거슬러 올라갈 분 (기본 15)")
    parser.add_argument("--start", help="UTC ISO (예: 2026-10-03T06:00:00Z). 주면 --minutes 무시")
    parser.add_argument("--end", help="UTC ISO. 기본 지금")
    parser.add_argument("--project")
    args = parser.parse_args()

    end_dt = datetime.fromisoformat(args.end.replace("Z", "+00:00")) if args.end else datetime.now(UTC)
    start_dt = (
        datetime.fromisoformat(args.start.replace("Z", "+00:00"))
        if args.start
        else end_dt - timedelta(minutes=args.minutes)
    )
    start, end = start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"), end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    project = project_id(args.project)
    token = gcloud("auth", "print-access-token")

    print(f"Cloud Run 서버 지표  service={args.service}  project={project}")
    print(f"구간 {start} ~ {end} (UTC, {PERIOD}초 단위)\n")

    # 인스턴스 수: active = 요청 처리 중, idle = 대기 중. 둘을 합친 게 실제 떠 있는 개수
    inst = fetch(
        token,
        project,
        args.service,
        "run.googleapis.com/container/instance_count",
        start,
        end,
        perSeriesAligner="ALIGN_MAX",
        crossSeriesReducer="REDUCE_SUM",
        groupByFields="metric.labels.state",
    )
    by_state = {s["metric"]["labels"].get("state", "?"): per_minute([s]) for s in inst}
    total = per_minute(inst)
    peak_active = max(by_state.get("active", {}).values(), default=0)
    print(f"인스턴스 수  최대 {max(total.values(), default=0):.0f}개  (active 최대 {peak_active:.0f})")

    # 요청 수: 응답 코드별
    reqs = fetch(
        token,
        project,
        args.service,
        "run.googleapis.com/request_count",
        start,
        end,
        perSeriesAligner="ALIGN_SUM",
        crossSeriesReducer="REDUCE_SUM",
        groupByFields="metric.labels.response_code",
    )
    print("\n요청 수 (응답 코드별 합계)")
    if not reqs:
        print("  (구간에 요청 없음)")
    for s in sorted(reqs, key=lambda x: x["metric"]["labels"].get("response_code", "")):
        print(f"  {s['metric']['labels'].get('response_code', '?'):>5s}  {sum(value(p) for p in s['points']):8.0f}건")

    # 분포형 지표: 분당 p50/p99 중 구간 내 최댓값
    rows = [
        ("요청 지연 (서버측, ms)", "run.googleapis.com/request_latencies", 1.0, "{:8.0f}"),
        ("CPU 사용률 (인스턴스별)", "run.googleapis.com/container/cpu/utilizations", 100.0, "{:7.0f}%"),
        ("메모리 사용률 (인스턴스별)", "run.googleapis.com/container/memory/utilizations", 100.0, "{:7.0f}%"),
        ("인스턴스당 동시 요청 수", "run.googleapis.com/container/max_request_concurrencies", 1.0, "{:8.1f}"),
        ("콜드스타트 소요 (ms)", "run.googleapis.com/container/startup_latencies", 1.0, "{:8.0f}"),
    ]
    print("\n분포형 지표 — 분당 값 중 구간 최댓값 (p50 / p99)")
    for label, metric, scale, fmt in rows:
        p50 = percentile_series(token, project, args.service, metric, start, end, 50)
        p99 = percentile_series(token, project, args.service, metric, start, end, 99)
        if not p99:
            print(f"  {label:26s} (데이터 없음)")
            continue
        print(
            f"  {label:26s} p50 "
            + fmt.format(max(p50.values()) * scale)
            + "   p99 "
            + fmt.format(max(p99.values()) * scale)
        )

    # 콜드스타트는 횟수도 중요하다 (몇 번 새 인스턴스가 떴는가)
    cold = fetch(
        token,
        project,
        args.service,
        "run.googleapis.com/container/startup_latencies",
        start,
        end,
        perSeriesAligner="ALIGN_DELTA",
    )
    started = sum(int(p["value"].get("distributionValue", {}).get("count", 0)) for s in cold for p in s["points"])
    print(f"\n새로 뜬 인스턴스(콜드스타트) {started}회")

    print("\n분 단위 인스턴스 수 추이 (UTC)")
    for ts, n in total.items():
        print(f"  {ts[11:19]}  {'#' * int(n)} {n:.0f}")


if __name__ == "__main__":
    main()
