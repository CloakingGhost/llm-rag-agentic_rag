// vanilla/native/agentic 3개 시나리오가 공유하는 뼈대.
// 차이는 요청 mode 값과 임계치뿐이라 팩토리로 뺐다.

import http from 'k6/http';
import { check, sleep } from 'k6';
import { newClientId, newConversationId, pickKey, pickQuestion } from './common.js';

const BASE_URL = __ENV.API_BASE_URL || 'http://localhost:8100';
const MODEL = 'gpt-5.6-luna'; // 부하테스트 모델 고정 (2026-10-01 결정)

const RAMP_STAGES = [
  { duration: '30s', target: 1 },
  { duration: '1m', target: 3 },
  { duration: '1m', target: 6 },
  { duration: '30s', target: 0 },
];

// K6_SCENARIO=baseline(기본) : 1 VU × 10회 — 파이프라인별 p50/p95 베이스라인
// K6_SCENARIO=ramp           : 0→1→3→6→0 VU 램프 — 세마포어(20) 범위 내 동시성 확인
export function buildOptions(pipelineTag, p95Ms) {
  const mode = __ENV.K6_SCENARIO || 'baseline';
  const scenario =
    mode === 'ramp'
      ? { executor: 'ramping-vus', exec: 'flow', startVUs: 0, stages: RAMP_STAGES }
      : { executor: 'shared-iterations', exec: 'flow', vus: 1, iterations: 10, maxDuration: '10m' };

  return {
    scenarios: { main: scenario },
    thresholds: {
      http_req_failed: ['rate<0.05'],
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
