import { buildOptions, makeFlow } from '../lib/scenario.js';

// p95 35s: reasoning_effort="none" 적용 후 Critic 3회 소진 케이스 실측(약 30s) + 여유
export const options = buildOptions('agentic', 35000);
export const flow = makeFlow('agentic', 'agentic');
