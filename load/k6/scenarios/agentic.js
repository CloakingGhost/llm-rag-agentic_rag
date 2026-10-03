import { buildOptions, makeFlow } from '../lib/scenario.js';

// p95 45s. 2026-10-03 같은 날 로컬/배포 1 VU x 10회 실측이 둘 다 p95 38.5s였다.
// agentic은 Critic을 몇 번 도느냐로 지연이 갈린다: 1회 통과 평균 13.8s / 2회 26.4s / 3회 소진 33.9s.
// k6 질문 풀에서는 10건 중 6건이 3회를 소진(unverified)해 중앙값이 32s다. 처음 35s로 잡았던 건
// "3회 소진 ≈ 30s"라는 추정이었고 실측 꼬리(최대 40.7s)를 못 덮었다.
// 이 값은 정상 목표가 아니라 "이보다 더 나빠지면 이상 신호"이고, 서버 타임아웃(RUN_TIMEOUT_SEC=60s)과의 여유는 약 20s다.
export const options = buildOptions('agentic', 45000);
export const flow = makeFlow('agentic', 'agentic');
