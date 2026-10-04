// vanilla/native/agentic 3개 시나리오가 공유하는 뼈대.
// 차이는 요청 mode 값과 임계치뿐이라 팩토리로 뺐다.

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter } from 'k6/metrics';
import { newClientId, newConversationId, pickKey, pickQuestion } from './common.js';

// run_done 없이 끝난 요청 수. error_type 태그가 붙어 Grafana/Prometheus에서 원인별로 갈린다
const pipelineErrors = new Counter('pipeline_errors');

const BASE_URL = __ENV.API_BASE_URL || 'http://localhost:8100';
const MODEL = 'gpt-5.6-luna'; // 부하테스트 모델 고정 (2026-10-01 결정)

// 요청 하나가 최대 RUN_TIMEOUT_SEC(60초)까지 걸린다. 램프다운 때 진행 중인 반복이 끊기지 않게
// 그보다 길게 기다려 준다 (기본 30초면 agentic이 도중에 interrupted로 잘려 실패처럼 보인다)
const GRACEFUL = '70s';

// 로컬 고정 램프: 세마포어(20) 범위 안에서 동시성 확인
const RAMP = [
  { duration: '30s', target: 1 },
  { duration: '1m', target: 3 },
  { duration: '1m', target: 6 },
  { duration: '30s', target: 0 },
];

// 배포 환경 램프는 최대 VU를 LOAD_MAX_VUS로 바꿀 수 있다 (k6 예약 변수 K6_VUS와 겹치지 않게 접두사를 다르게 둔다).
//   단일 파이프라인 요청은 1건 = 1 run 이라 서비스 동시 상한은 3인스턴스 x 20 = 60.
//   scaleout: 20 — Cloud Run 오토스케일러가 인스턴스를 늘리는 과정을 본다 (모든 파이프라인 동일)
//   limit   : 파이프라인마다 다르다. "서버 한계"를 재려면 서버보다 OpenAI 한도가 먼저 막히면 안 된다.
//     Luna 한도는 키당 500k TPM, 2키 합계 1M TPM (load/openai_limits.py). 2026-10-04 실측 요청당 토큰으로 계산하면
//       vanilla  ≈ 840토큰  → TPM으로는 259 VU까지 괜찮고, Cloud Run 동시성(60)이 약 65 VU에서 먼저 막힌다 → 75 VU로 상한을 넘긴다
//       native   ≈ 2,000토큰 → TPM이 약 37 VU에서 먼저 막힌다 → 35 VU (CPU 포화를 본다)
//       agentic  ≈ 11,100토큰 → TPM이 약 48 VU에서 먼저 막힌다 → 40 VU
//     native·agentic을 75 VU로 밀면 서버가 아니라 OpenAI 429가 먼저 나와 "서버 한계"로 오판하게 된다.
const SHAPES = {
  scaleout: { defaultMax: () => 20, steps: [['1m', 0.25], ['2m', 0.5], ['2m', 1], ['1m', 1], ['1m', 0]] },
  limit: {
    defaultMax: (pipeline) => ({ vanilla: 75, native: 35, agentic: 40 })[pipeline] || 40,
    steps: [['1m', 0.15], ['2m', 0.4], ['2m', 1], ['2m', 1], ['1m', 0]],
  },
};

function stagesFor(mode, pipeline) {
  if (mode === 'ramp') return RAMP;
  const shape = SHAPES[mode];
  if (!shape) return null;
  const max = Number(__ENV.LOAD_MAX_VUS || shape.defaultMax(pipeline));
  return shape.steps.map(([duration, fraction]) => ({
    duration,
    target: fraction === 0 ? 0 : Math.max(1, Math.round(max * fraction)),
  }));
}

// K6_SCENARIO=baseline(기본) : 1 VU x BASELINE_ITERATIONS(기본 10)회 — 파이프라인별 p50/p95 베이스라인
// K6_SCENARIO=ramp | scaleout | limit : 위 RAMP / SHAPES 참고
export function buildOptions(pipelineTag, p95Ms) {
  const mode = __ENV.K6_SCENARIO || 'baseline';
  let scenario;
  if (mode === 'baseline') {
    scenario = {
      executor: 'shared-iterations',
      exec: 'flow',
      vus: 1,
      iterations: Number(__ENV.BASELINE_ITERATIONS || 10),
      maxDuration: '15m',
    };
  } else if (stagesFor(mode, pipelineTag)) {
    scenario = {
      executor: 'ramping-vus',
      exec: 'flow',
      startVUs: 0,
      stages: stagesFor(mode, pipelineTag),
      gracefulRampDown: GRACEFUL,
      gracefulStop: GRACEFUL,
    };
  } else {
    throw new Error(`알 수 없는 K6_SCENARIO: ${mode} (baseline | ramp | scaleout | limit)`);
  }

  // 배포 환경 단계에서는 서버가 무너지기 시작하면 스스로 멈춘다 — 부하 도구가 서버를 끝까지 밀어붙이지 않게
  const aborting = mode === 'scaleout' || mode === 'limit';
  const failThreshold = aborting
    ? [{ threshold: 'rate<0.20', abortOnFail: true, delayAbortEval: '30s' }]
    : ['rate<0.05'];

  // /api/chat은 SSE라서 파이프라인이 타임아웃으로 실패해도 HTTP 상태는 200이다 — http_req_failed로는 안 보인다
  // (2026-10-04 scaleout에서 3/148건이 그랬다). run_done 체크가 이걸 잡으므로 checks 비율에도 임계치를 건다.
  // 체크는 요청당 2개(200 응답, run_done)라 파이프라인 실패 20% = checks 0.90.
  const checksThreshold = aborting
    ? [{ threshold: 'rate>0.90', abortOnFail: true, delayAbortEval: '30s' }]
    : ['rate>0.95'];

  return {
    scenarios: { main: scenario },
    // 요약에 p99까지 넣는다 (기본은 p90/p95까지만 나온다)
    summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
    // 모든 지표에 붙는 태그 — Grafana에서 로컬/배포, 단계별로 거르려고 둔다
    tags: { mode, target: __ENV.K6_TARGET || 'local' },
    thresholds: {
      http_req_failed: failThreshold,
      checks: checksThreshold,
      [`http_req_duration{pipeline:${pipelineTag}}`]: [`p(95)<${p95Ms}`],
    },
  };
}

// requestMode: 서버 /api/chat의 mode 값 (vanilla | rag | agentic)
// pipelineTag: k6 태그·대시보드 라벨용 이름 (vanilla | native | agentic) — server의
//   RunRecord.pipeline과 맞춘다. rag 요청이 실제로는 "native" 파이프라인을 돈다
//   (app/api/chat.py의 MODE_PIPELINES 매핑)
export function makeFlow(requestMode, pipelineTag) {
  return function flow() {
    const payload = JSON.stringify({
      mode: requestMode,
      question: pickQuestion(),
      clientId: newClientId(),
      conversationId: newConversationId(),
      model: MODEL,
    });
    const params = {
      headers: { 'Content-Type': 'application/json', 'x-openai-key': pickKey() },
      tags: { pipeline: pipelineTag },
      timeout: '70s',
    };
    const res = http.post(`${BASE_URL}/api/chat`, payload, params);
    const done = !!res.body && res.body.includes('event: run_done');
    check(res, {
      '200 응답': (r) => r.status === 200,
      'run_done 이벤트 포함': () => done,
    });
    if (!done) {
      // 실패 원인을 남긴다: 서버가 run_error 이벤트에 싣는 errorType (timeout / quota_exceeded / invalid_key …)
      const m = res.body && res.body.match(/"errorType":"([a-z_]+)"/);
      const errorType = m ? m[1] : `http_${res.status}`;
      pipelineErrors.add(1, { pipeline: pipelineTag, error_type: errorType });
      console.warn(`파이프라인 실패 pipeline=${pipelineTag} error_type=${errorType} duration=${Math.round(res.timings.duration)}ms`);
    }
    sleep(1);
  };
}
