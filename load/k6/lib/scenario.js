// vanilla/native/agentic 3개 시나리오가 공유하는 뼈대.
// 차이는 요청 mode 값과 임계치뿐이라 팩토리로 뺐다.

import http from 'k6/http';
import { check, sleep } from 'k6';
import { newClientId, newConversationId, pickKey, pickQuestion } from './common.js';

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
//   scaleout: 상한의 1/3 — Cloud Run 오토스케일러가 인스턴스를 늘리는 과정을 본다
//   limit   : 상한의 1.25배(75) — 60을 넘겨서 429/503이 "설계된 상한"인지 "진짜 장애"인지 가른다
//   agentic은 요청당 OpenAI 호출이 5~11번이라 limit을 75로 두면 키당 500 RPM에 먼저 닿을 수 있다
//   (LOAD_MAX_VUS=40 정도로 낮추거나, 서버 CPU 한계는 native로 본다)
const SHAPES = {
  scaleout: { defaultMax: 20, steps: [['1m', 0.25], ['2m', 0.5], ['2m', 1], ['1m', 1], ['1m', 0]] },
  limit: { defaultMax: 75, steps: [['1m', 0.15], ['2m', 0.4], ['2m', 1], ['2m', 1], ['1m', 0]] },
};

function stagesFor(mode) {
  if (mode === 'ramp') return RAMP;
  const shape = SHAPES[mode];
  if (!shape) return null;
  const max = Number(__ENV.LOAD_MAX_VUS || shape.defaultMax);
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
  } else if (stagesFor(mode)) {
    scenario = {
      executor: 'ramping-vus',
      exec: 'flow',
      startVUs: 0,
      stages: stagesFor(mode),
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

  return {
    scenarios: { main: scenario },
    // 요약에 p99까지 넣는다 (기본은 p90/p95까지만 나온다)
    summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
    // 모든 지표에 붙는 태그 — Grafana에서 로컬/배포, 단계별로 거르려고 둔다
    tags: { mode, target: __ENV.K6_TARGET || 'local' },
    thresholds: {
      http_req_failed: failThreshold,
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
    check(res, {
      '200 응답': (r) => r.status === 200,
      'run_done 이벤트 포함': (r) => !!r.body && r.body.includes('event: run_done'),
    });
    sleep(1);
  };
}
