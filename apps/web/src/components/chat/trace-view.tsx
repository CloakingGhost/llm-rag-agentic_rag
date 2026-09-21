"use client";

import { Check, X } from "lucide-react";

import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { Badge } from "@/components/ui/badge";
import type { RunTrace } from "@/lib/types";

function Criterion({ label, ok }: { label: string; ok: boolean }) {
  return (
    <span className="inline-flex items-center gap-1 text-xs">
      {ok ? (
        <Check className="size-3 text-emerald-600 dark:text-emerald-400" />
      ) : (
        <X className="size-3 text-destructive" />
      )}
      {label}
    </span>
  );
}

export function TraceView({ trace, defaultOpen }: { trace: RunTrace; defaultOpen?: boolean }) {
  const items: string[] = [];
  if (trace.route) items.push("route");
  if (trace.retrievals?.length) items.push("retrieval");
  if (trace.critics?.length) items.push("critic");
  if (!items.length) return null;

  return (
    <Accordion
      type="multiple"
      defaultValue={defaultOpen ? items : []}
      className="w-full border-t pt-1"
    >
      {trace.route ? (
        <AccordionItem value="route">
          <AccordionTrigger className="py-2 text-xs">
            라우터 판정
            <Badge variant={trace.route.route === "out_of_domain" ? "destructive" : "secondary"}>
              {trace.route.route === "out_of_domain" ? "도메인 밖" : "정책 문의"}
            </Badge>
          </AccordionTrigger>
          <AccordionContent className="text-xs text-muted-foreground">
            {trace.route.reason}
            {trace.disputeTarget?.productName ? (
              <div className="mt-2">
                분쟁 대상: {trace.disputeTarget.productName} · {trace.disputeTarget.disputeType}
                {trace.disputeTarget.carriedOver ? (
                  <Badge variant="outline" className="ml-2">
                    이전 턴에서 이어받음
                  </Badge>
                ) : null}
              </div>
            ) : null}
          </AccordionContent>
        </AccordionItem>
      ) : null}

      {trace.retrievals?.length ? (
        <AccordionItem value="retrieval">
          <AccordionTrigger className="py-2 text-xs">
            검색 근거
            <Badge variant="secondary">
              {trace.retrievals[trace.retrievals.length - 1].chunks.filter((c) => c.selected).length}건 사용
            </Badge>
          </AccordionTrigger>
          <AccordionContent className="max-h-64 space-y-3 overflow-y-auto overscroll-contain pr-1">
            {trace.retrievals.map((r) => (
              <div key={r.attempt} className="space-y-2">
                <p className="text-xs text-muted-foreground">
                  {trace.retrievals!.length > 1 ? `${r.attempt}회차 · ` : ""}쿼리: {r.query}
                </p>
                {r.chunks
                  .filter((c) => c.selected)
                  .map((c) => (
                    <div key={c.chunkId} className="rounded-md border bg-muted/40 p-2">
                      <div className="flex flex-wrap items-center gap-1 text-[11px] text-muted-foreground">
                        <span className="font-medium text-foreground">{c.path}</span>
                        {c.vectorScore !== null ? <span>벡터 {c.vectorScore.toFixed(2)}</span> : null}
                        {c.graphHit ? <Badge variant="outline">그래프</Badge> : null}
                        <span>리랭킹 {c.rerankRank}위</span>
                      </div>
                      <p className="mt-1 text-xs leading-relaxed">{c.textSnapshot}</p>
                    </div>
                  ))}
              </div>
            ))}
          </AccordionContent>
        </AccordionItem>
      ) : null}

      {trace.critics?.length ? (
        <AccordionItem value="critic">
          <AccordionTrigger className="py-2 text-xs">
            Critic 판정
            <Badge variant={trace.critics.some((c) => !c.passed) ? "destructive" : "secondary"}>
              {trace.critics.length}회
            </Badge>
          </AccordionTrigger>
          <AccordionContent className="max-h-64 space-y-2 overflow-y-auto overscroll-contain pr-1">
            {trace.critics.map((c) => (
              <div key={c.attempt} className="rounded-md border p-2">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-xs font-medium">{c.attempt}회차</span>
                  <Criterion label="근거" ok={c.isGrounded} />
                  <Criterion label="부합" ok={c.isRelevant} />
                  <Criterion label="완결" ok={c.isComplete} />
                </div>
                <p className="mt-1 text-xs text-muted-foreground">{c.feedback}</p>
              </div>
            ))}
            {trace.fallbackReason ? (
              <p className="text-xs text-destructive">{trace.fallbackReason}</p>
            ) : null}
          </AccordionContent>
        </AccordionItem>
      ) : null}
    </Accordion>
  );
}
