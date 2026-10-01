"""커스텀 Prometheus 지표.

HTTP 단위 기본 지표(요청 수·지연)는 main.py의 Instrumentator가 자동으로 만든다.
여기는 그걸로 안 잡히는 것만 추가한다 — 한 HTTP 요청(`/api/chat`)이 내부적으로
파이프라인을 1~3개(mode=all) 동시에 돌리므로, 파이프라인 단위 지표는 따로 있어야
Grafana에서 vanilla/native/agentic을 구분해서 볼 수 있다.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

pipeline_runs_total = Counter(
    "cdq_pipeline_runs_total",
    "파이프라인 실행 종료 횟수",
    ["pipeline", "outcome", "model"],
)

pipeline_latency_seconds = Histogram(
    "cdq_pipeline_latency_seconds",
    "파이프라인 종단 지연(초)",
    ["pipeline"],
    buckets=(1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 120),
)

# max_concurrent_runs(기본 20) 대비 점유율을 보려고 둔다. 부하테스트로 상한에
# 닿는지 확인하는 게 목적이라 절대값 자체가 핵심 지표다 (docs/10_observability_load_test_plan.md)
semaphore_in_use = Gauge(
    "cdq_semaphore_in_use",
    "동시 실행 중인 파이프라인 수",
)
