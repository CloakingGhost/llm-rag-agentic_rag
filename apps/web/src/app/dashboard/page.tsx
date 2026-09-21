"use client";

import { Info } from "lucide-react";
import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, LabelList, XAxis, YAxis } from "recharts";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { fetchDashboard, fetchModels, type DashboardData, type ModelOption } from "@/lib/api";
import { PIPELINE_LABEL } from "@/lib/types";

const PERIODS = [
  { value: "all", label: "전체" },
  { value: "30d", label: "최근 30일" },
  { value: "7d", label: "최근 7일" },
];

const SMALL_SAMPLE_THRESHOLD = 10;

const nodeChartConfig = { ms: { label: "평균 소요", color: "var(--chart-1)" } } satisfies ChartConfig;
const tokenChartConfig = { avgTokens: { label: "평균 토큰", color: "var(--chart-2)" } } satisfies ChartConfig;

const NODE_LABEL: Record<string, string> = {
  memory: "분쟁 대상",
  router: "라우팅",
  retrieve: "검색",
  rerank: "리랭킹",
  generate: "생성",
  critic: "검증",
  reject: "거절",
  fallback: "폴백",
};

function percent(value: number) {
  return `${(value * 100).toFixed(1)}%`;
}

export default function DashboardPage() {
  const [period, setPeriod] = useState("all");
  const [model, setModel] = useState("any");
  const [models, setModels] = useState<ModelOption[]>([]);

  useEffect(() => {
    let alive = true;
    fetchModels()
      .then(({ models: list }) => alive && setModels(list))
      .catch(() => null);
    return () => {
      alive = false;
    };
  }, []);
  // 어느 기간의 응답인지 함께 들고 있어야 기간을 바꿨을 때 이전 수치가 남지 않는다
  const [result, setResult] = useState<{ key: string; data: DashboardData | null; error: string | null }>({
    key: "",
    data: null,
    error: null,
  });

  useEffect(() => {
    let alive = true;
    const key = `${period}|${model}`;
    fetchDashboard(period, model)
      .then((payload) => alive && setResult({ key, data: payload, error: null }))
      .catch(() => alive && setResult({ key, data: null, error: "수치를 불러오지 못했습니다." }));
    return () => {
      alive = false;
    };
  }, [period, model]);

  const fresh = result.key === `${period}|${model}`;
  const data = fresh ? result.data : null;
  const error = fresh ? result.error : null;

  const smallSample = data?.summaries.some((s) => s.requests > 0 && s.requests < SMALL_SAMPLE_THRESHOLD);
  const noData = data && data.totalRequests === 0;

  return (
    <div className="mx-auto w-full max-w-6xl space-y-5 px-4 py-6">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="font-heading text-lg font-semibold">대시보드</h1>
        {data ? <Badge variant="secondary">요청 {data.totalRequests.toLocaleString()}건</Badge> : null}
        <Select value={model} onValueChange={setModel}>
          <SelectTrigger size="sm" className="ml-auto w-44">
            <SelectValue placeholder="모델" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="any">모든 모델</SelectItem>
            {models.map((m) => (
              <SelectItem key={m.id} value={m.id}>
                {m.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Tabs value={period} onValueChange={setPeriod}>
          <TabsList>
            {PERIODS.map((p) => (
              <TabsTrigger key={p.value} value={p.value} className="text-xs">
                {p.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      </div>

      {error ? (
        <Alert variant="destructive">
          <Info className="size-4" />
          <AlertDescription className="flex items-center gap-3">
            {error}
            <Button variant="outline" size="sm" onClick={() => setPeriod((p) => p)}>
              다시 시도
            </Button>
          </AlertDescription>
        </Alert>
      ) : null}

      {!data && !error ? (
        <div className="grid gap-3 md:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-44 w-full" />
          ))}
        </div>
      ) : null}

      {noData ? (
        <div className="rounded-lg border border-dashed p-10 text-center">
          <p className="text-sm text-muted-foreground">아직 기록된 요청이 없습니다.</p>
        </div>
      ) : null}

      {data && !noData ? (
        <>
          {smallSample ? (
            <Alert>
              <Info className="size-4" />
              <AlertDescription>
                이 기간은 파이프라인별 요청이 {SMALL_SAMPLE_THRESHOLD}건 미만입니다. 수치가 불안정할 수 있습니다.
              </AlertDescription>
            </Alert>
          ) : null}

          <div className="grid gap-3 md:grid-cols-3">
            {data.summaries.map((s) => (
              <Card key={s.pipeline} className="gap-2">
                <CardHeader className="pb-0">
                  <CardTitle className="text-sm">{PIPELINE_LABEL[s.pipeline]}</CardTitle>
                </CardHeader>
                <CardContent>
                  <dl className="grid grid-cols-2 gap-y-2 text-sm">
                    <dt className="text-muted-foreground">요청</dt>
                    <dd className="text-right">{s.requests.toLocaleString()}건</dd>
                    <dt className="text-muted-foreground">지연 p50</dt>
                    <dd className="text-right">{(s.latencyP50Ms / 1000).toFixed(1)}초</dd>
                    <dt className="text-muted-foreground">지연 p95</dt>
                    <dd className="text-right">{(s.latencyP95Ms / 1000).toFixed(1)}초</dd>
                    <dt className="text-muted-foreground">평균 토큰</dt>
                    <dd className="text-right">{s.avgTokens.toLocaleString()}</dd>
                    <dt className="text-muted-foreground">평균 비용</dt>
                    <dd className="text-right">${s.avgCostUsd.toFixed(5)}</dd>
                  </dl>
                </CardContent>
              </Card>
            ))}
          </div>

          <div className="grid gap-3 md:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">요청당 평균 토큰</CardTitle>
              </CardHeader>
              <CardContent>
                <ChartContainer config={tokenChartConfig} className="h-[200px] w-full">
                  <BarChart
                    data={data.summaries.map((s) => ({
                      pipeline: PIPELINE_LABEL[s.pipeline].replace(" LLM", "").replace(" RAG", ""),
                      avgTokens: s.avgTokens,
                    }))}
                    layout="vertical"
                    margin={{ left: 8, right: 44 }}
                  >
                    <CartesianGrid horizontal={false} />
                    <XAxis type="number" hide />
                    <YAxis dataKey="pipeline" type="category" tickLine={false} axisLine={false} width={70} />
                    <ChartTooltip content={<ChartTooltipContent />} />
                    <Bar dataKey="avgTokens" fill="var(--color-avgTokens)" radius={4}>
                      <LabelList dataKey="avgTokens" position="right" className="fill-foreground text-xs" />
                    </Bar>
                  </BarChart>
                </ChartContainer>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-sm">노드별 평균 소요 시간</CardTitle>
              </CardHeader>
              <CardContent>
                <ChartContainer config={nodeChartConfig} className="h-[200px] w-full">
                  <BarChart
                    data={data.nodeLatency.map((n) => ({ node: NODE_LABEL[n.node] ?? n.node, ms: n.ms }))}
                    margin={{ left: 0, right: 8 }}
                  >
                    <CartesianGrid vertical={false} />
                    <XAxis dataKey="node" tickLine={false} axisLine={false} className="text-xs" />
                    <YAxis hide />
                    <ChartTooltip content={<ChartTooltipContent />} />
                    <Bar dataKey="ms" fill="var(--color-ms)" radius={4} />
                  </BarChart>
                </ChartContainer>
                <p className="mt-2 text-xs text-muted-foreground">단위: 밀리초</p>
              </CardContent>
            </Card>
          </div>

          {data.byModel.length > 0 ? (
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">모델별 비교</CardTitle>
              </CardHeader>
              <CardContent>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>모델</TableHead>
                      <TableHead className="text-right">실행</TableHead>
                      <TableHead className="text-right">지연 p50</TableHead>
                      <TableHead className="text-right">평균 토큰</TableHead>
                      <TableHead className="text-right">평균 비용</TableHead>
                      <TableHead className="text-right">Agentic 폴백률</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.byModel.map((m) => {
                      const agentic = m.byPipeline.find((p) => p.pipeline === "agentic");
                      return (
                        <TableRow key={m.model}>
                          <TableCell className="text-sm">{m.label}</TableCell>
                          <TableCell className="text-right text-sm">{m.requests.toLocaleString()}</TableCell>
                          <TableCell className="text-right text-sm">
                            {(m.latencyP50Ms / 1000).toFixed(1)}초
                          </TableCell>
                          <TableCell className="text-right text-sm">{m.avgTokens.toLocaleString()}</TableCell>
                          <TableCell className="text-right text-sm">${m.avgCostUsd.toFixed(5)}</TableCell>
                          <TableCell className="text-right text-sm">
                            {agentic && agentic.requests > 0 ? percent(agentic.fallbackRate) : "-"}
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          ) : null}

          <Card>
            <CardHeader>
              <CardTitle className="text-sm">경로 분포</CardTitle>
            </CardHeader>
            <CardContent className="grid gap-4 sm:grid-cols-3">
              <div>
                <p className="text-2xl font-semibold">{percent(data.routes.oodRejectRate)}</p>
                <p className="text-sm text-muted-foreground">Agentic 도메인 밖 거절</p>
              </div>
              <div>
                <p className="text-2xl font-semibold">{percent(data.routes.fallbackRate)}</p>
                <p className="text-sm text-muted-foreground">Agentic 폴백</p>
              </div>
              <div>
                <p className="text-2xl font-semibold">{percent(data.routes.nativeNoInfoRate)}</p>
                <p className="text-sm text-muted-foreground">
                  Native &apos;정보 없음&apos; 응답 <span className="text-xs">(추정)</span>
                </p>
              </div>
            </CardContent>
          </Card>

          <p className="text-xs text-muted-foreground">단가 기준: {data.priceVersion}</p>
        </>
      ) : null}
    </div>
  );
}
