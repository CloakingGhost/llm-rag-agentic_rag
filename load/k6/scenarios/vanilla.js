import { buildOptions, makeFlow } from '../lib/scenario.js';

// p95 25s: 실측 베이스라인(2026-10-01, 1 VU×10회)은 18.07s였다. run_vanilla()의 generate
// 호출이 sampling_args()를 전혀 안 써서 reasoning_effort가 설정되지 않는다 — Critic/라우터에
// 적용했던 "숨은 추론 지연" 수정(cdb7277)이 simple.py(vanilla/native)는 건드리지 않았다.
// 의도적으로 고치지 않은 상태다(generate류 호출의 effort 튜닝은 보류 항목,
// docs/10_observability_load_test_plan.md) — 이 임계치는 "정상 목표"가 아니라 "이보다
// 더 나빠지면 이상 신호"로 여유 있게 잡은 값이다
export const options = buildOptions('vanilla', 25000);
export const flow = makeFlow('vanilla', 'vanilla');
