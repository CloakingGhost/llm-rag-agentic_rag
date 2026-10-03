#!/usr/bin/env bash
# load/k6/run.sh <vanilla|native|agentic> [baseline|ramp|scaleout|limit]
#
#   baseline (기본) : 1 VU x 10회 (BASELINE_ITERATIONS로 횟수 변경) — 파이프라인별 p50/p95 베이스라인
#   ramp            : 0->1->3->6->0 VU — 로컬 세마포어(20) 범위 내 동시성 확인
#   scaleout        : 최대 20 VU(LOAD_MAX_VUS), 약 7분 — 배포 환경에서 인스턴스 확장 관찰
#   limit           : 최대 75 VU(LOAD_MAX_VUS), 약 8분 — 서비스 동시 상한 60을 넘겨 보는 한계 테스트
#                     (실패율 20%를 넘으면 k6가 스스로 멈춘다)
#
# .env(K6_OPENAI_KEY_1/2, API_BASE_URL)를 읽어 k6에 그대로 넘긴다.
#
# 환경변수
#   K6_TARGET   결과에 붙는 태그. 기본 local. 배포 환경은 K6_TARGET=loadtest 처럼 주면 Grafana에서 가를 수 있다
#   K6_PROM_RW  1이면 결과를 로컬 Prometheus로 실시간 전송 -> Grafana "k6 부하테스트" 대시보드
#               (docker compose의 prometheus가 떠 있어야 한다)
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCENARIO="${1:?시나리오를 지정하세요: vanilla | native | agentic}"
K6_SCENARIO="${2:-baseline}"

if [[ ! -f "${DIR}/scenarios/${SCENARIO}.js" ]]; then
  echo "알 수 없는 시나리오: ${SCENARIO} (vanilla | native | agentic)" >&2
  exit 1
fi

set -a
source "${DIR}/.env"
set +a

export K6_SCENARIO
export K6_TARGET="${K6_TARGET:-local}"

OUT_ARGS=()
if [[ "${K6_PROM_RW:-0}" == "1" ]]; then
  export K6_PROMETHEUS_RW_SERVER_URL="${K6_PROMETHEUS_RW_SERVER_URL:-http://localhost:9090/api/v1/write}"
  export K6_PROMETHEUS_RW_TREND_STATS="${K6_PROMETHEUS_RW_TREND_STATS:-p(50),p(95),p(99),avg,max}"
  export K6_PROMETHEUS_RW_PUSH_INTERVAL="${K6_PROMETHEUS_RW_PUSH_INTERVAL:-5s}"
  OUT_ARGS=(--out experimental-prometheus-rw)
fi

echo "▶ 대상: ${API_BASE_URL} (target=${K6_TARGET}, scenario=${K6_SCENARIO}, pipeline=${SCENARIO})"
k6 run ${OUT_ARGS[@]+"${OUT_ARGS[@]}"} "${DIR}/scenarios/${SCENARIO}.js"
