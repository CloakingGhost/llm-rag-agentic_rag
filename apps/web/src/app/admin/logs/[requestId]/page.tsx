"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { use, useEffect, useState } from "react";

import { AdminBar, AdminGuard } from "@/components/admin/admin-guard";
import { RunCard } from "@/components/chat/run-card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { fetchLogDetail, type LogDetail, type LogDetailRun } from "@/lib/api";
import { PIPELINE_LABEL, type RunState, type RunTrace } from "@/lib/types";

const NODE_LABEL: Record<string, string> = {
  memory: "분쟁 대상",
  router: "라우팅",
  retrieve: "검색",
  rerank: "리랭킹",
  generate: "생성",
  critic: "검증",
  reject: "거절 응답",
  unverified: "검증 미통과",
  fallback: "폴백",
  act: "도구 실행",
};

/** 로그 상세는 챗봇과 같은 카드를 재사용한다. */
function toRunState(run: LogDetailRun): RunState {
  const retrieval = run.chunks.length
    ? [
        {
          attempt: 1,
          query: String(run.steps.find((s) => s.node === "retrieve")?.output?.query ?? ""),
          chunks: run.chunks,
        },
      ]
    : undefined;

  const router = run.steps.find((s) => s.node === "router")?.output as
    | { route?: "policy_inquiry" | "system_action" | "out_of_domain"; reason?: string }
    | undefined;
  const act = run.steps.find((s) => s.node === "act")?.output as
    | { calls?: RunTrace["toolCalls"] }
    | undefined;
  const memory = run.steps.find((s) => s.node === "memory")?.output as
    | { productName?: string | null; disputeType?: string | null; carriedOver?: boolean }
    | undefined;
  const critics = run.steps
    .filter((s) => s.node === "critic")
    .map((s) => {
      const output = s.output as Record<string, unknown>;
      return {
        attempt: s.attempt,
        isGrounded: Boolean(output.isGrounded),
        isRelevant: Boolean(output.isRelevant),
        isComplete: Boolean(output.isComplete),
        feedback: String(output.feedback ?? ""),
        passed: Boolean(output.passed),
      };
    });

  return {
    runId: run.runId,
    pipeline: run.pipeline,
    status: "done",
    outcome: run.outcome,
    answer: run.answer,
    metrics: run.metrics,
    errorType: (run.errorType ?? undefined) as RunState["errorType"],
    errorMessage: run.errorMessage ?? undefined,
    trace: {
      route: router?.route ? { route: router.route, reason: router.reason ?? "" } : undefined,
      disputeTarget: memory
        ? {
            productName: memory.productName ?? null,
            disputeType: memory.disputeType ?? null,
            carriedOver: memory.carriedOver ?? false,
          }
        : undefined,
      toolCalls: act?.calls,
      retrievals: retrieval,
      critics: critics.length ? critics : undefined,
      fallbackReason: run.fallbackUsed ? "Critic 기준 미달 → Native RAG 결과로 대체" : undefined,
    },
  };
}

export default function AdminLogDetailPage({
  params,
}: {
  params: Promise<{ requestId: string }>;
}) {
  const { requestId } = use(params);
  const [detail, setDetail] = useState<LogDetail | null>(null);
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    let alive = true;
    fetchLogDetail(requestId)
      .then((result) => alive && setDetail(result))
      .catch(() => alive && setMissing(true));
    return () => {
      alive = false;
    };
  }, [requestId]);

  return (
    <AdminGuard>
      <div className="mx-auto w-full max-w-6xl space-y-5 px-4 py-6">
        <Button asChild variant="ghost" size="sm" className="w-fit">
          <Link href="/admin/logs">
            <ArrowLeft className="size-4" /> 목록
          </Link>
        </Button>
        <AdminBar title="로그 상세" />

        {missing ? <p className="text-sm text-muted-foreground">로그를 찾을 수 없습니다.</p> : null}
        {!detail && !missing ? <Skeleton className="h-96 w-full" /> : null}

        {detail ? (
          <>
            <Card>
              <CardContent className="space-y-3 pt-6">
                <p className="text-sm">{detail.question}</p>
                <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
                  <Badge variant="outline">{detail.requestId}</Badge>
                  <span>{new Date(detail.createdAt).toLocaleString("ko-KR")}</span>
                  <span>브라우저 {detail.clientId}</span>
                  <span>빌드 {detail.buildId}</span>
                  <Badge variant="secondary">{detail.status}</Badge>
                </div>
              </CardContent>
            </Card>

            <section className="space-y-2">
              <h2 className="font-heading text-sm font-semibold">답변 비교</h2>
              <div className="grid items-start gap-3 md:grid-cols-3">
                {detail.runs.map((run) => (
                  <RunCard key={run.runId} run={toRunState(run)} traceDefaultOpen boundedHeight />
                ))}
              </div>
            </section>

            <section className="space-y-2">
              <h2 className="font-heading text-sm font-semibold">노드 타임라인</h2>
              <div className="grid gap-3 lg:grid-cols-2">
                {detail.runs.map((run) => (
                  <Card key={run.runId}>
                    <CardHeader className="pb-0">
                      <CardTitle className="text-sm">{PIPELINE_LABEL[run.pipeline]}</CardTitle>
                    </CardHeader>
                    <CardContent>
                      <Table>
                        <TableHeader>
                          <TableRow>
                            <TableHead className="w-10">#</TableHead>
                            <TableHead className="w-24">노드</TableHead>
                            <TableHead className="w-12">회차</TableHead>
                            <TableHead className="w-20 text-right">소요</TableHead>
                            <TableHead className="w-24 text-right">토큰</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {run.steps.map((step) => (
                            <TableRow key={step.seq}>
                              <TableCell className="text-xs text-muted-foreground">{step.seq}</TableCell>
                              <TableCell className="text-xs">{NODE_LABEL[step.node] ?? step.node}</TableCell>
                              <TableCell className="text-xs">{step.attempt}</TableCell>
                              <TableCell className="text-right text-xs">
                                {(step.latencyMs / 1000).toFixed(2)}초
                              </TableCell>
                              <TableCell className="text-right text-xs">
                                {(step.tokensIn + step.tokensOut).toLocaleString()}
                              </TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </CardContent>
                  </Card>
                ))}
              </div>
            </section>

            <section className="space-y-2">
              <h2 className="font-heading text-sm font-semibold">검색 결과 (원문 스냅샷)</h2>
              {detail.runs
                .filter((run) => run.chunks.length > 0)
                .map((run) => (
                  <Card key={run.runId}>
                    <CardHeader className="pb-0">
                      <CardTitle className="text-sm">{PIPELINE_LABEL[run.pipeline]}</CardTitle>
                    </CardHeader>
                    <CardContent>
                      <Table>
                        <TableHeader>
                          <TableRow>
                            <TableHead className="w-16">순위</TableHead>
                            <TableHead className="w-64">경로</TableHead>
                            <TableHead className="w-20 text-right">벡터</TableHead>
                            <TableHead className="w-16">그래프</TableHead>
                            <TableHead className="w-16">사용</TableHead>
                            <TableHead>원문</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {run.chunks.map((c) => (
                            <TableRow key={c.chunkId}>
                              <TableCell className="text-xs">{c.rerankRank}</TableCell>
                              <TableCell className="text-xs">{c.path}</TableCell>
                              <TableCell className="text-right text-xs">
                                {c.vectorScore !== null ? c.vectorScore.toFixed(2) : "-"}
                              </TableCell>
                              <TableCell className="text-xs">{c.graphHit ? "○" : "-"}</TableCell>
                              <TableCell className="text-xs">{c.selected ? "○" : "-"}</TableCell>
                              <TableCell className="text-xs text-muted-foreground">{c.textSnapshot}</TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </CardContent>
                  </Card>
                ))}
            </section>
          </>
        ) : null}
      </div>
    </AdminGuard>
  );
}
