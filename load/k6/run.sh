#!/usr/bin/env bash
# load/k6/run.sh <vanilla|native|agentic> [baseline|ramp]
#
#   baseline (기본) : 1 VU × 10회 — 파이프라인별 p50/p95 베이스라인
#   ramp            : 0→1→3→6→0 VU 램프 — 로컬 세마포어(20) 범위 내 동시성 확인
#
# .env(K6_OPENAI_KEY_1/2, API_BASE_URL)를 읽어 k6에 그대로 넘긴다.
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
k6 run "${DIR}/scenarios/${SCENARIO}.js"
