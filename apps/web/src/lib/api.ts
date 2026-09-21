// 백엔드 호출. 목업을 대체한다 (docs/mock_inventory.md)
// 계약은 docs/04_system_design.md §4-2, 타입은 lib/types.ts

import type { ChatMode, Outcome, Pipeline, RunMetrics, RunTrace } from "@/lib/types";

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8100";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { credentials: "include", ...init });
  if (!response.ok) {
    const detail = await response.json().catch(() => null);
    throw new ApiError(detail?.detail ?? "요청에 실패했습니다.", response.status);
  }
  return (await response.json()) as T;
}

/** 서버가 잠들어 있으면 깨우는 데 시간이 걸린다 (무료 호스팅) */
export function checkHealth() {
  return request<{ status: string; kb: string; buildId: string }>("/api/health");
}

export function validateKey(apiKey: string) {
  return request<{ valid: boolean; reason?: string }>("/api/key/validate", {
    method: "POST",
    headers: { "X-OpenAI-Key": apiKey },
  });
}

export interface ModelOption {
  id: string;
  label: string;
  note: string;
  pricePer1M: { input: number; output: number };
}

export function fetchModels() {
  return request<{ default: string; models: ModelOption[] }>("/api/models");
}

export interface KbInfo {
  buildId: string;
  status: string;
  chunkCount: number;
  graphNodes: number;
  documents: { name: string; detail: string }[];
}

export function fetchKbInfo() {
  return request<KbInfo>("/api/kb/info");
}

export interface DashboardData {
  period: string;
  model: string | null;
  byModel: {
    model: string;
    label: string;
    requests: number;
    latencyP50Ms: number;
    avgTokens: number;
    avgCostUsd: number;
    byPipeline: { pipeline: Pipeline; requests: number; avgCostUsd: number; fallbackRate: number }[];
  }[];
  priceVersion: string;
  totalRequests: number;
  summaries: {
    pipeline: Pipeline;
    requests: number;
    latencyP50Ms: number;
    latencyP95Ms: number;
    avgTokens: number;
    avgCostUsd: number;
  }[];
  nodeLatency: { node: string; ms: number }[];
  routes: { oodRejectRate: number; fallbackRate: number; nativeNoInfoRate: number };
}

export function fetchDashboard(period: string, model?: string) {
  const query = new URLSearchParams({ period });
  if (model && model !== "any") query.set("model", model);
  return request<DashboardData>(`/api/dashboard?${query}`);
}

export interface LogRow {
  requestId: string;
  createdAt: string;
  question: string;
  mode: ChatMode;
  clientId: string;
  buildId: string;
  status: string;
  model: string;
  outcomes: { pipeline: Pipeline; outcome: Outcome; model: string }[];
  latencyMs: number;
  costUsd: number;
}

export function adminLogin(id: string, pw: string) {
  return request<{ ok: boolean }>("/api/admin/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id, pw }),
  });
}

export function adminLogout() {
  return request<{ ok: boolean }>("/api/admin/logout", { method: "POST" });
}

export function fetchLogs(params: { mode?: string; pipeline?: string; outcome?: string; q?: string; page?: number }) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value && value !== "any") search.set(key, String(value));
  }
  return request<{ total: number; page: number; items: LogRow[] }>(`/api/admin/logs?${search}`);
}

export interface LogDetailRun {
  runId: string;
  pipeline: Pipeline;
  outcome: Outcome;
  answer: string;
  route: string | null;
  fallbackUsed: boolean;
  criticAttempts: number;
  model: string;
  errorType: string | null;
  errorMessage: string | null;
  metrics: RunMetrics & { priceVersion?: string };
  steps: {
    seq: number;
    node: string;
    attempt: number;
    latencyMs: number;
    tokensIn: number;
    tokensOut: number;
    output: Record<string, unknown>;
  }[];
  chunks: {
    chunkId: string;
    path: string;
    textSnapshot: string;
    vectorScore: number | null;
    graphHit: boolean;
    rerankRank: number;
    selected: boolean;
  }[];
}

export interface LogDetail extends Omit<LogRow, "outcomes" | "latencyMs" | "costUsd"> {
  runs: LogDetailRun[];
}

export function fetchLogDetail(requestId: string) {
  return request<LogDetail>(`/api/admin/logs/${requestId}`);
}

// ---------------------------------------------------------------- 챗봇 SSE

export type ChatEvent =
  | {
      type: "request_created";
      requestId: string;
      conversationId: string;
      turn: number;
      buildId: string;
      model: string;
      runs: { runId: string; pipeline: Pipeline }[];
    }
  | { type: "run_step"; runId: string; pipeline: Pipeline; node: string; attempt: number }
  | { type: "run_done"; runId: string; pipeline: Pipeline; outcome: Outcome; answer: string; metrics: RunMetrics; trace: RunTrace }
  | { type: "run_error"; runId: string; pipeline: Pipeline; errorType: string; message: string }
  | { type: "done"; requestId: string; conversationId: string; status: string };

export async function streamChat(options: {
  mode: ChatMode;
  question: string;
  clientId: string;
  apiKey: string;
  model: string;
  /** 같은 대화의 후속 질문이면 앞선 응답에서 받은 값을 넘긴다 (논문 3.4 세션 메모리) */
  conversationId?: string | null;
  signal: AbortSignal;
  onEvent: (event: ChatEvent) => void;
}): Promise<void> {
  // 헤더에 키를 실어야 하므로 EventSource 대신 fetch 스트림을 쓴다 (04 문서 §4-2)
  const response = await fetch(`${API_BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-OpenAI-Key": options.apiKey },
    body: JSON.stringify({
      mode: options.mode,
      question: options.question,
      clientId: options.clientId,
      model: options.model,
      conversationId: options.conversationId ?? null,
    }),
    signal: options.signal,
  });

  if (!response.ok || !response.body) {
    const detail = await response.json().catch(() => null);
    throw new ApiError(detail?.detail ?? "요청에 실패했습니다.", response.status);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const raw = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      boundary = buffer.indexOf("\n\n");

      let eventName = "";
      let data = "";
      for (const line of raw.split("\n")) {
        if (line.startsWith("event: ")) eventName = line.slice(7).trim();
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      if (eventName && data) {
        options.onEvent({ type: eventName, ...JSON.parse(data) } as ChatEvent);
      }
    }
  }
}

/** 대화 종료. 서버가 세션 메모리를 버린다 (논문 9.3: 세션이 끝나면 맥락이 초기화된다) */
export function endConversation(conversationId: string) {
  return fetch(`${API_BASE}/api/chat/conversation/${conversationId}/end`, { method: "POST" }).catch(
    () => null,
  );
}

export function cancelChat(requestId: string, clientId: string) {
  return fetch(`${API_BASE}/api/chat/${requestId}/cancel`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ clientId }),
    keepalive: true,
  });
}
