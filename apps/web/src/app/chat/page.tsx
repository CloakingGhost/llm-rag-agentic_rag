"use client";

import { KeyRound, Loader2, MessageSquarePlus, Send, Square } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ModelPicker, useModelChoice } from "@/components/chat/model-picker";
import { RunCard } from "@/components/chat/run-card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { cancelChat, checkHealth, endConversation, streamChat } from "@/lib/api";
import { getClientId, useApiKey } from "@/lib/api-key";
import { EXAMPLE_QUESTIONS } from "@/lib/examples";
import {
  MODE_PIPELINES,
  PIPELINE_LABEL,
  type ChatMode,
  type Pipeline,
  type RequestState,
  type RunState,
} from "@/lib/types";
import { cn } from "@/lib/utils";

const MODE_TABS: { value: ChatMode; label: string; hint: string }[] = [
  { value: "all", label: "전체 파이프라인 보기", hint: "같은 질문을 3개 파이프라인이 동시에 처리합니다" },
  { value: "vanilla", label: "Vanilla", hint: "검색 없이 모델 지식만으로 답합니다" },
  { value: "rag", label: "RAG", hint: "규정을 검색해 그 내용만으로 답합니다" },
  { value: "agentic", label: "Agentic", hint: "라우팅·검색·자체 검증을 거쳐 답합니다" },
];

const emptyHistories: Record<ChatMode, RequestState[]> = { all: [], vanilla: [], rag: [], agentic: [] };
// 대화 ID는 모드마다 따로 둔다. 탭을 바꾸면 다른 대화로 친다
const emptyConversations: Record<ChatMode, string | null> = {
  all: null,
  vanilla: null,
  rag: null,
  agentic: null,
};

export default function ChatPage() {
  const { apiKey, status, saveKey, openPanel, markInvalid } = useApiKey();
  const { models, model, choose } = useModelChoice();
  const [mode, setMode] = useState<ChatMode>("all");
  const [histories, setHistories] = useState(emptyHistories);
  const [conversations, setConversations] = useState(emptyConversations);
  const [question, setQuestion] = useState("");
  const [keyDraft, setKeyDraft] = useState("");
  const [running, setRunning] = useState(false);
  const [warming, setWarming] = useState(true);
  const [mobilePipeline, setMobilePipeline] = useState<Pipeline>("vanilla");
  const abortRef = useRef<AbortController | null>(null);
  const requestIdRef = useRef<string | null>(null);

  // 화면에 들어오면 서버를 깨운다 (Cloud Run은 쉬면 0으로 줄어든다)
  useEffect(() => {
    let alive = true;
    checkHealth()
      .catch(() => null)
      .then(() => {
        if (alive) setWarming(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  const history = histories[mode];

  const patchRun = useCallback(
    (targetMode: ChatMode, requestId: string, runId: string, patch: Partial<RunState>) => {
      setHistories((prev) => ({
        ...prev,
        [targetMode]: prev[targetMode].map((req) =>
          req.requestId !== requestId
            ? req
            : { ...req, runs: req.runs.map((run) => (run.runId === runId ? { ...run, ...patch } : run)) },
        ),
      }));
    },
    [],
  );

  const send = useCallback(
    async (text: string) => {
      const value = text.trim();
      if (!value || running || !apiKey || !model) return;

      const localId = `local_${crypto.randomUUID().slice(0, 8)}`;
      const pipelines = MODE_PIPELINES[mode];
      const request: RequestState = {
        requestId: localId,
        question: value,
        mode,
        createdAt: Date.now(),
        status: "running",
        runs: pipelines.map((pipeline) => ({ runId: `${localId}-${pipeline}`, pipeline, status: "pending" })),
      };

      setHistories((prev) => ({ ...prev, [mode]: [...prev[mode], request] }));
      setQuestion("");
      setRunning(true);
      setMobilePipeline(pipelines[0]);

      const controller = new AbortController();
      abortRef.current = controller;
      let activeId = localId;

      try {
        await streamChat({
          mode,
          question: value,
          clientId: getClientId(),
          apiKey,
          model,
          conversationId: conversations[mode],
          signal: controller.signal,
          onEvent: (event) => {
            switch (event.type) {
              case "request_created": {
                requestIdRef.current = event.requestId;
                // 같은 대화를 이어 가려면 이 값을 다음 질문에 그대로 실어 보낸다
                setConversations((prev) => ({ ...prev, [mode]: event.conversationId }));
                // 서버가 매긴 실행 ID로 바꿔 둔다 (취소·로그 추적에 쓰인다)
                setHistories((prev) => ({
                  ...prev,
                  [mode]: prev[mode].map((req) =>
                    req.requestId !== activeId
                      ? req
                      : {
                          ...req,
                          requestId: event.requestId,
                          runs: event.runs.map((r) => ({
                            runId: r.runId,
                            pipeline: r.pipeline,
                            status: "pending" as const,
                          })),
                        },
                  ),
                }));
                activeId = event.requestId;
                break;
              }
              case "run_step":
                patchRun(mode, activeId, event.runId, { status: "running" });
                break;
              case "run_done":
                patchRun(mode, activeId, event.runId, {
                  status: "done",
                  outcome: event.outcome,
                  answer: event.answer,
                  metrics: event.metrics,
                  trace: event.trace,
                });
                break;
              case "run_error":
                patchRun(mode, activeId, event.runId, {
                  status: "done",
                  outcome: "failed",
                  errorType: event.errorType as RunState["errorType"],
                  errorMessage: event.message,
                });
                if (event.errorType === "invalid_key") markInvalid();
                break;
              case "done":
                setHistories((prev) => ({
                  ...prev,
                  [mode]: prev[mode].map((req) =>
                    req.requestId === activeId ? { ...req, status: event.status as RequestState["status"] } : req,
                  ),
                }));
                break;
            }
          },
        });
      } catch (error) {
        const message = error instanceof Error ? error.message : "요청에 실패했습니다.";
        setHistories((prev) => ({
          ...prev,
          [mode]: prev[mode].map((req) =>
            req.requestId !== activeId
              ? req
              : {
                  ...req,
                  status: "failed",
                  runs: req.runs.map((run) =>
                    run.status === "done"
                      ? run
                      : { ...run, status: "done" as const, outcome: "failed" as const, errorMessage: message },
                  ),
                },
          ),
        }));
      } finally {
        setRunning(false);
        abortRef.current = null;
        requestIdRef.current = null;
      }
    },
    [apiKey, conversations, markInvalid, mode, model, patchRun, running],
  );

  /** 새 대화. 서버의 세션 메모리를 버리고 화면도 비운다 */
  const startNewConversation = useCallback(() => {
    const current = conversations[mode];
    if (current) void endConversation(current);
    setConversations((prev) => ({ ...prev, [mode]: null }));
    setHistories((prev) => ({ ...prev, [mode]: [] }));
  }, [conversations, mode]);

  const stop = useCallback(() => {
    const requestId = requestIdRef.current;
    if (requestId) void cancelChat(requestId, getClientId()).catch(() => null);
    abortRef.current?.abort();
    abortRef.current = null;
    setRunning(false);
  }, []);

  const isAllMode = mode === "all";
  const modeHint = useMemo(() => MODE_TABS.find((t) => t.value === mode)?.hint ?? "", [mode]);

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-4 px-4 py-4">
      <Tabs value={mode} onValueChange={(v) => !running && setMode(v as ChatMode)}>
        <TabsList className="h-auto w-full flex-wrap justify-start gap-1">
          {MODE_TABS.map((tab) => (
            <TabsTrigger key={tab.value} value={tab.value} disabled={running} className="text-xs sm:text-sm">
              {tab.label}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
      <div className="-mt-2 flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground">{modeHint}</p>
        <div className="flex items-center gap-2">
          {history.length > 0 ? (
            <Button variant="ghost" size="sm" onClick={startNewConversation} disabled={running}>
              <MessageSquarePlus className="size-4" /> 새 대화
            </Button>
          ) : null}
          <ModelPicker models={models} model={model} onChange={choose} disabled={running} />
        </div>
      </div>

      <div className="flex-1 space-y-6">
        {history.length === 0 ? (
          <div className="rounded-lg border border-dashed p-6 text-center">
            <p className="text-sm text-muted-foreground">
              소비자 분쟁에 대해 물어보세요. 아래 예시를 눌러도 됩니다.
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              같은 대화 안에서는 품목과 분쟁 유형을 기억합니다. 이어서 &ldquo;그럼 환불은요?&rdquo;처럼 물어도 됩니다.
            </p>
            <div className="mt-3 flex flex-wrap justify-center gap-2">
              {EXAMPLE_QUESTIONS.map((q) => (
                <Button key={q} variant="outline" size="sm" className="text-xs" onClick={() => setQuestion(q)}>
                  {q}
                </Button>
              ))}
            </div>
          </div>
        ) : null}

        {history.map((request) => (
          <div key={request.requestId} className="space-y-3">
            <div className="flex justify-end">
              <p className="max-w-[85%] rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground">
                {request.question}
              </p>
            </div>

            {isAllMode ? (
              <>
                <div className="hidden items-start gap-3 md:grid md:grid-cols-3">
                  {request.runs.map((run) => (
                    <RunCard
                      key={run.runId}
                      run={run}
                      traceDefaultOpen
                      boundedHeight
                      onOpenKey={openPanel}
                      onRetry={() => send(request.question)}
                    />
                  ))}
                </div>
                <div className="md:hidden">
                  <Tabs value={mobilePipeline} onValueChange={(v) => setMobilePipeline(v as Pipeline)}>
                    <TabsList className="w-full">
                      {request.runs.map((run) => (
                        <TabsTrigger key={run.runId} value={run.pipeline} className="flex-1 gap-1 text-xs">
                          <span
                            className={cn(
                              "size-1.5 rounded-full",
                              run.status === "done"
                                ? run.outcome === "failed"
                                  ? "bg-destructive"
                                  : "bg-emerald-500"
                                : "animate-pulse bg-muted-foreground",
                            )}
                          />
                          {PIPELINE_LABEL[run.pipeline].split(" ")[0]}
                        </TabsTrigger>
                      ))}
                    </TabsList>
                  </Tabs>
                  <div className="mt-3">
                    {request.runs
                      .filter((run) => run.pipeline === mobilePipeline)
                      .map((run) => (
                        <RunCard
                          key={run.runId}
                          run={run}
                          traceDefaultOpen
                          onOpenKey={openPanel}
                          onRetry={() => send(request.question)}
                        />
                      ))}
                  </div>
                </div>
              </>
            ) : (
              request.runs.map((run) => (
                <RunCard key={run.runId} run={run} onOpenKey={openPanel} onRetry={() => send(request.question)} />
              ))
            )}
          </div>
        ))}
      </div>

      <div className="sticky bottom-0 space-y-2 border-t bg-background pt-3 pb-4">
        {warming ? (
          <p className="flex items-center gap-2 text-xs text-muted-foreground">
            <Loader2 className="size-3.5 animate-spin" />
            서버를 깨우는 중입니다. 무료 호스팅이라 한동안 쉬면 잠듭니다.
          </p>
        ) : null}

        {!apiKey ? (
          <div className="space-y-2 rounded-lg border p-3">
            <p className="flex items-center gap-2 text-sm font-medium">
              <KeyRound className="size-4" /> OpenAI API 키를 입력하면 질문할 수 있습니다
            </p>
            <div className="flex gap-2">
              <Input
                type="password"
                placeholder="sk-..."
                value={keyDraft}
                onChange={(e) => setKeyDraft(e.target.value)}
                autoComplete="off"
              />
              <Button onClick={() => saveKey(keyDraft)} disabled={status === "checking"}>
                {status === "checking" ? "확인 중…" : "저장"}
              </Button>
            </div>
            {status === "invalid" ? (
              <p className="text-xs text-destructive">유효하지 않은 키입니다.</p>
            ) : (
              <p className="text-xs text-muted-foreground">
                키는 이 브라우저에만 저장됩니다. 질문 비용은 입력하신 키로 청구됩니다.
              </p>
            )}
          </div>
        ) : (
          <div className="flex items-end gap-2">
            <Textarea
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send(question);
                }
              }}
              placeholder={running ? "답변이 끝나면 다시 질문할 수 있습니다" : "예: 어제 받은 노트북이 켜지지 않아요"}
              disabled={running}
              rows={2}
              className="min-h-11 resize-none"
            />
            {running ? (
              <Button variant="destructive" onClick={stop} className="h-11">
                <Square className="size-4" /> 중지
              </Button>
            ) : (
              <Button onClick={() => send(question)} disabled={!question.trim()} className="h-11">
                <Send className="size-4" /> 보내기
              </Button>
            )}
          </div>
        )}

        {running ? (
          <p className="text-xs text-muted-foreground">
            실행 중에는 새 질문과 탭 전환이 잠깁니다. 중지하면 다시 쓸 수 있습니다.
          </p>
        ) : null}
      </div>
    </div>
  );
}
