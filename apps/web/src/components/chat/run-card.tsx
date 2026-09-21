"use client";

import { AlertCircle, Ban, KeyRound, Loader2, RotateCw } from "lucide-react";

import { AnswerMarkdown } from "@/components/chat/answer-markdown";
import { TraceView } from "@/components/chat/trace-view";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { PIPELINE_LABEL, type RunState } from "@/lib/types";
import { cn } from "@/lib/utils";

function formatMetrics(run: RunState) {
  if (!run.metrics) return null;
  const { latencyMs, tokensIn, tokensOut, costUsd } = run.metrics;
  return `${(latencyMs / 1000).toFixed(1)}초 · ${(tokensIn + tokensOut).toLocaleString()} 토큰 · $${costUsd.toFixed(4)}`;
}

function OutcomeBadge({ run }: { run: RunState }) {
  if (run.outcome === "rejected") return <Badge variant="outline">도메인 밖</Badge>;
  if (run.outcome === "fallback") return <Badge variant="destructive">폴백됨</Badge>;
  if (run.outcome === "canceled") return <Badge variant="outline">중지됨</Badge>;
  if (run.outcome === "failed") return <Badge variant="destructive">실패</Badge>;
  return null;
}

function ErrorBlock({ run, onRetry, onOpenKey }: { run: RunState; onRetry?: () => void; onOpenKey?: () => void }) {
  const isKey = run.errorType === "invalid_key";
  return (
    <div className="space-y-3 rounded-md border border-destructive/40 bg-destructive/5 p-3">
      <p className="flex items-start gap-2 text-sm">
        {isKey ? <KeyRound className="mt-0.5 size-4" /> : <AlertCircle className="mt-0.5 size-4" />}
        <span>{run.errorMessage}</span>
      </p>
      {isKey ? (
        <Button size="sm" variant="outline" onClick={onOpenKey}>
          키 다시 입력
        </Button>
      ) : run.errorType === "quota_exceeded" ? (
        <p className="text-xs text-muted-foreground">
          OpenAI 결제 설정에서 사용 한도를 확인해 주세요.
        </p>
      ) : (
        <Button size="sm" variant="outline" onClick={onRetry}>
          <RotateCw className="size-3.5" /> 다시 시도
        </Button>
      )}
    </div>
  );
}

export function RunCard({
  run,
  traceDefaultOpen,
  /** 전체 보기처럼 3열로 늘어설 때는 카드 높이를 묶고 안에서 스크롤한다. */
  boundedHeight,
  onRetry,
  onOpenKey,
  className,
}: {
  run: RunState;
  traceDefaultOpen?: boolean;
  boundedHeight?: boolean;
  onRetry?: () => void;
  onOpenKey?: () => void;
  className?: string;
}) {
  return (
    <Card className={cn("gap-3", className)}>
      <CardHeader className="pb-0">
        <CardTitle className="flex items-center gap-2 text-sm">
          {PIPELINE_LABEL[run.pipeline]}
          <OutcomeBadge run={run} />
          {/* 진행도(단계 이름·회차)는 표시하지 않는다. 처리 과정은 완료 후 트레이스에서 본다. */}
          {run.status !== "done" ? (
            <Loader2 className="ml-auto size-3.5 animate-spin text-muted-foreground" />
          ) : null}
        </CardTitle>
      </CardHeader>

      <CardContent
        className={cn(
          "space-y-3",
          boundedHeight && "max-h-[26rem] overflow-y-auto overscroll-contain",
        )}
      >
        {run.status !== "done" ? (
          <div className="space-y-2">
            <div className="h-3 w-4/5 animate-pulse rounded bg-muted" />
            <div className="h-3 w-3/5 animate-pulse rounded bg-muted" />
          </div>
        ) : run.outcome === "failed" ? (
          <ErrorBlock run={run} onRetry={onRetry} onOpenKey={onOpenKey} />
        ) : run.outcome === "canceled" ? (
          <p className="flex items-center gap-2 text-sm text-muted-foreground">
            <Ban className="size-4" /> 중지되었습니다.
          </p>
        ) : (
          <AnswerMarkdown>{run.answer ?? ""}</AnswerMarkdown>
        )}

        {run.status === "done" && run.trace ? (
          <TraceView trace={run.trace} defaultOpen={traceDefaultOpen} />
        ) : null}
      </CardContent>

      {run.status === "done" && run.metrics ? (
        <CardFooter className="pt-0">
          <p className="text-xs text-muted-foreground">{formatMetrics(run)}</p>
        </CardFooter>
      ) : null}
    </Card>
  );
}
