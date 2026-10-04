#!/usr/bin/env bash
# load/k6/run.sh <vanilla|native|agentic> [baseline|ramp|scaleout|limit]
#
#   baseline (기본) : 1 VU x 10회 (BASELINE_ITERATIONS로 횟수 변경) — 파이프라인별 p50/p95 베이스라인
#   ramp            : 0->1->3->6->0 VU — 로컬 세마포어(20) 범위 내 동시성 확인
#   scaleout        : 최대 20 VU(LOAD_MAX_VUS), 약 7분 — 배포 환경에서 인스턴스 확장 관찰
#   limit           : 약 8분 — 한계 테스트. 최대 VU는 파이프라인별 기본(vanilla 75 / native 35 / agentic 40)이고
#                     LOAD_MAX_VUS로 바꾼다. native·agentic은 서버보다 OpenAI 토큰 한도(TPM)가 먼저 막혀서 낮게 잡았다
#                     (실패율 20% 또는 run_done 누락 20%를 넘으면 k6가 스스로 멈춘다)
#
# .env(K6_OPENAI_KEY_1/2, API_BASE_URL)를 읽어 k6에 그대로 넘긴다.
#
# 환경변수
#   API_BASE_URL  셸에서 주면 .env 값보다 우선한다 — .env를 고치지 않고 대상을 바꿀 수 있다.
#                 예) API_BASE_URL=https://cdq-api-loadtest-....run.app K6_TARGET=loadtest bash load/k6/run.sh native
#   K6_TARGET     결과에 붙는 태그. 기본 local. 배포 환경은 loadtest 처럼 주면 Grafana에서 가를 수 있다
#   K6_PROM_RW    1이면 결과를 로컬 Prometheus로 실시간 전송 -> Grafana "k6 부하테스트" 대시보드
#                 (docker compose의 prometheus가 떠 있어야 한다)
#   K6_NO_WARMUP  1이면 시작 전 /api/health 워밍업을 건너뛴다 (콜드스타트까지 재고 싶을 때)
#   ALLOW_ANY_TARGET  1이면 scaleout/limit를 localhost·loadtest 이외의 주소에도 허용한다 (운영 서비스를 때리는 길이다)
#
# 결과 요약(JSON)은 load/k6/results/ 에 저장된다.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCENARIO="${1:?시나리오를 지정하세요: vanilla | native | agentic}"
K6_SCENARIO="${2:-baseline}"

if [[ ! -f "${DIR}/scenarios/${SCENARIO}.js" ]]; then
  echo "알 수 없는 시나리오: ${SCENARIO} (vanilla | native | agentic)" >&2
  exit 1
fi

# 셸에서 준 API_BASE_URL이 .env보다 우선하도록 source 전에 챙겨 둔다
PRESET_API_BASE_URL="${API_BASE_URL:-}"

set -a
source "${DIR}/.env"
set +a

API_BASE_URL="${PRESET_API_BASE_URL:-${API_BASE_URL}}"
export API_BASE_URL
export K6_SCENARIO
export K6_TARGET="${K6_TARGET:-local}"

# 부하를 크게 거는 모드는 localhost 또는 이름에 loadtest가 든 주소에만 허용한다 (운영 서비스 보호)
if [[ "${K6_SCENARIO}" == "scaleout" || "${K6_SCENARIO}" == "limit" ]]; then
  if [[ "${API_BASE_URL}" != *localhost* && "${API_BASE_URL}" != *127.0.0.1* && "${API_BASE_URL}" != *loadtest* \
        && "${ALLOW_ANY_TARGET:-0}" != "1" ]]; then
    echo "중단: ${K6_SCENARIO} 모드는 localhost 또는 'loadtest'가 들어간 주소에만 쓸 수 있습니다 (대상: ${API_BASE_URL})." >&2
    echo "      운영 서비스를 때리려는 게 맞다면 ALLOW_ANY_TARGET=1 을 명시하세요." >&2
    exit 1
  fi
fi

if [[ "${K6_NO_WARMUP:-0}" != "1" ]]; then
  # 서비스가 0으로 줄어 있으면 첫 요청이 콜드스타트(약 20초)를 겪어 베이스라인 p95를 왜곡한다.
  # health는 앱이 완전히 뜬 뒤에야 응답하므로 이걸로 미리 깨운다. 걸린 시간 자체가 콜드스타트 측정값이다
  echo -n "▶ 워밍업 ${API_BASE_URL}/api/health ... "
  curl -s -o /dev/null -w "HTTP %{http_code}, %{time_total}s\n" --max-time 120 "${API_BASE_URL}/api/health" \
    || echo "실패(계속 진행)"
fi

OUT_ARGS=()
if [[ "${K6_PROM_RW:-0}" == "1" ]]; then
  export K6_PROMETHEUS_RW_SERVER_URL="${K6_PROMETHEUS_RW_SERVER_URL:-http://localhost:9090/api/v1/write}"
  export K6_PROMETHEUS_RW_TREND_STATS="${K6_PROMETHEUS_RW_TREND_STATS:-p(50),p(95),p(99),avg,max}"
  export K6_PROMETHEUS_RW_PUSH_INTERVAL="${K6_PROMETHEUS_RW_PUSH_INTERVAL:-5s}"
  OUT_ARGS=(--out experimental-prometheus-rw)
fi

mkdir -p "${DIR}/results"
SUMMARY="${DIR}/results/${K6_TARGET}_${SCENARIO}_${K6_SCENARIO}_$(date +%Y%m%d-%H%M%S).json"

echo "▶ 대상: ${API_BASE_URL} (target=${K6_TARGET}, scenario=${K6_SCENARIO}, pipeline=${SCENARIO})"
# k6가 임계치 실패로 non-zero를 내도 요약 파일 경로는 알려 준다
set +e
k6 run --summary-export "${SUMMARY}" ${OUT_ARGS[@]+"${OUT_ARGS[@]}"} "${DIR}/scenarios/${SCENARIO}.js"
STATUS=$?
set -e
echo "▶ 요약 JSON: load/k6/results/$(basename "${SUMMARY}")"
exit "${STATUS}"
