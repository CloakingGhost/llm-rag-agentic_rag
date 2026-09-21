// 04_system_design.md §4-2 API 명세와 1:1로 맞춘 타입.
// API를 붙일 때 이 파일이 계약서 역할을 한다. 필드 이름을 바꾸면 백엔드도 같이 바꿔야 한다.

export type Pipeline = "vanilla" | "native" | "agentic";
export type ChatMode = "all" | "vanilla" | "rag" | "agentic";

export type Outcome =
  | "answered"
  | "rejected"
  | "fallback"
  | "failed"
  | "canceled";

export type ErrorType =
  | "invalid_key"
  | "quota_exceeded"
  | "timeout"
  | "kb_unavailable"
  | "busy"
  | "unknown";

export type NodeName =
  | "memory"
  | "router"
  | "retrieve"
  | "rerank"
  | "generate"
  | "critic"
  | "reject"
  | "fallback";

export interface RunMetrics {
  latencyMs: number;
  tokensIn: number;
  tokensOut: number;
  costUsd: number;
}

export interface RetrievedChunk {
  chunkId: string;
  path: string; // 예: 별표Ⅱ > 이동통신서비스업 > 통화품질 불량 > 가입 14일 이내
  textSnapshot: string;
  vectorScore: number | null;
  graphHit: boolean;
  rerankRank: number;
  selected: boolean;
}

export interface CriticJudgement {
  attempt: number;
  isGrounded: boolean;
  isRelevant: boolean;
  isComplete: boolean;
  feedback: string;
  passed: boolean;
}

export interface RunTrace {
  route?: { route: "policy_inquiry" | "out_of_domain"; reason: string };
  // carriedOver: 이번 질문에 없던 항목을 지난 턴에서 이어받았다는 뜻 (논문 3.4 세션 메모리)
  disputeTarget?: { productName: string | null; disputeType: string | null; carriedOver?: boolean };
  retrievals?: { attempt: number; query: string; chunks: RetrievedChunk[] }[];
  critics?: CriticJudgement[];
  fallbackReason?: string;
}

/** 화면이 들고 있는 실행 1건의 상태. SSE 이벤트를 누적해서 만든다. */
export interface RunState {
  runId: string;
  pipeline: Pipeline;
  status: "pending" | "running" | "done";
  currentNode?: NodeName;
  currentAttempt?: number;
  startedAt?: number;
  outcome?: Outcome;
  answer?: string;
  metrics?: RunMetrics;
  trace?: RunTrace;
  errorType?: ErrorType;
  errorMessage?: string;
}

export interface RequestState {
  requestId: string;
  question: string;
  mode: ChatMode;
  createdAt: number;
  runs: RunState[];
  status: "running" | "completed" | "partial" | "failed" | "canceled";
}

export const PIPELINE_LABEL: Record<Pipeline, string> = {
  vanilla: "Vanilla LLM",
  native: "Native RAG",
  agentic: "Agentic RAG",
};

export const NODE_LABEL: Record<NodeName, string> = {
  memory: "분쟁 대상 추출",
  router: "라우팅",
  retrieve: "검색",
  rerank: "리랭킹",
  generate: "생성",
  critic: "검증",
  reject: "거절 응답",
  fallback: "폴백",
};

export const MODE_PIPELINES: Record<ChatMode, Pipeline[]> = {
  all: ["vanilla", "native", "agentic"],
  vanilla: ["vanilla"],
  rag: ["native"],
  agentic: ["agentic"],
};
