import { buildOptions, makeFlow } from '../lib/scenario.js';

// 서버 /api/chat에는 mode="rag"로 보낸다 — 실제로 도는 파이프라인 이름은 "native"
// (app/api/chat.py의 MODE_PIPELINES: {"rag": ["native"]})
//
// 주의: run_native()의 generate 호출도 vanilla와 같은 이유로 sampling_args() 없이 호출된다
// (scenarios/vanilla.js 주석 참고) — 이 6s 임계치는 실측 전 추정값이라 첫 실행에서
// 깨질 수 있다. 깨지면 버그가 아니라 "재측정해서 임계치를 올려야 한다"는 신호다
export const options = buildOptions('native', 6000);
export const flow = makeFlow('rag', 'native');
