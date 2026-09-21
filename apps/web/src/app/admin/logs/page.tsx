"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { AdminBar, AdminGuard } from "@/components/admin/admin-guard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { fetchLogs, type LogRow } from "@/lib/api";
import type { ChatMode, Outcome, Pipeline } from "@/lib/types";

const MODE_LABEL: Record<ChatMode, string> = {
  all: "전체 보기",
  vanilla: "Vanilla",
  rag: "RAG",
  agentic: "Agentic",
};

const OUTCOME_LABEL: Record<Outcome, string> = {
  answered: "답변",
  rejected: "거절",
  unverified: "검증 미통과",
  fallback: "폴백",
  failed: "실패",
  canceled: "중지",
};

const PIPELINE_SHORT: Record<Pipeline, string> = { vanilla: "V", native: "R", agentic: "A" };

function outcomeVariant(outcome: Outcome) {
  if (outcome === "failed" || outcome === "fallback" || outcome === "unverified") return "destructive" as const;
  if (outcome === "answered") return "secondary" as const;
  return "outline" as const;
}

export default function AdminLogsPage() {
  const [mode, setMode] = useState("any");
  const [outcome, setOutcome] = useState("any");
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<LogRow[] | null>(null);

  useEffect(() => {
    let alive = true;
    const timer = setTimeout(() => {
      fetchLogs({ mode, outcome, q })
        .then((result) => alive && setRows(result.items))
        .catch(() => alive && setRows([]));
    }, 250);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [mode, outcome, q]);

  return (
    <AdminGuard>
      <div className="mx-auto w-full max-w-6xl space-y-4 px-4 py-6">
        <AdminBar title="로그" />

        <div className="flex flex-wrap items-center gap-2">
          <Input
            placeholder="질문 키워드"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            className="w-full sm:w-56"
          />
          <Select value={mode} onValueChange={setMode}>
            <SelectTrigger size="sm" className="w-32">
              <SelectValue placeholder="모드" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="any">모든 모드</SelectItem>
              {Object.entries(MODE_LABEL).map(([value, label]) => (
                <SelectItem key={value} value={value}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={outcome} onValueChange={setOutcome}>
            <SelectTrigger size="sm" className="w-32">
              <SelectValue placeholder="결과" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="any">모든 결과</SelectItem>
              {Object.entries(OUTCOME_LABEL).map(([value, label]) => (
                <SelectItem key={value} value={value}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {rows ? <span className="text-xs text-muted-foreground">{rows.length}건</span> : null}
        </div>

        {!rows ? (
          <Skeleton className="h-64 w-full" />
        ) : rows.length === 0 ? (
          <div className="rounded-lg border border-dashed p-10 text-center">
            <p className="text-sm text-muted-foreground">조건에 맞는 로그가 없습니다.</p>
            <Button
              variant="outline"
              size="sm"
              className="mt-3"
              onClick={() => {
                setMode("any");
                setOutcome("any");
                setQ("");
              }}
            >
              필터 초기화
            </Button>
          </div>
        ) : (
          <div className="rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-36">시각</TableHead>
                  <TableHead>질문</TableHead>
                  <TableHead className="w-24">모드</TableHead>
                  <TableHead className="w-44">결과</TableHead>
                  <TableHead className="w-20 text-right">지연</TableHead>
                  <TableHead className="w-24 text-right">비용</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((row) => (
                  <TableRow key={row.requestId}>
                    <TableCell className="text-xs text-muted-foreground">
                      <Link href={`/admin/logs/${row.requestId}`} className="block">
                        {new Date(row.createdAt).toLocaleString("ko-KR", {
                          dateStyle: "short",
                          timeStyle: "short",
                        })}
                      </Link>
                    </TableCell>
                    <TableCell className="max-w-0">
                      <Link href={`/admin/logs/${row.requestId}`} className="block truncate text-sm">
                        {row.question}
                      </Link>
                    </TableCell>
                    <TableCell className="text-xs">{MODE_LABEL[row.mode]}</TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-1">
                        {row.outcomes.map((o) => (
                          <Badge key={o.pipeline} variant={outcomeVariant(o.outcome)} className="text-[10px]">
                            {PIPELINE_SHORT[o.pipeline]} {OUTCOME_LABEL[o.outcome]}
                          </Badge>
                        ))}
                      </div>
                    </TableCell>
                    <TableCell className="text-right text-xs">{(row.latencyMs / 1000).toFixed(1)}초</TableCell>
                    <TableCell className="text-right text-xs">${row.costUsd.toFixed(5)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </div>
    </AdminGuard>
  );
}
