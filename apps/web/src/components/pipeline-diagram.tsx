import { ArrowRight, RotateCcw } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

function Step({ children, muted }: { children: React.ReactNode; muted?: boolean }) {
  return (
    <span
      className={
        muted
          ? "rounded-md border border-dashed px-2 py-1 text-xs text-muted-foreground"
          : "rounded-md border bg-muted px-2 py-1 text-xs"
      }
    >
      {children}
    </span>
  );
}

function Arrow() {
  return <ArrowRight className="size-3.5 shrink-0 text-muted-foreground" />;
}

const PIPELINES = [
  {
    name: "Vanilla LLM",
    summary: "검색 없이 모델이 아는 것만으로 답합니다.",
    trait: "빠르지만 근거가 없고, 조문을 잘못 말할 수 있습니다.",
    steps: ["질문", "생성", "답변"],
  },
  {
    name: "Native RAG",
    summary: "규정을 검색해 찾은 내용만으로 답합니다.",
    trait: "검색 결과를 추리지 않고 그대로 넣는 대조군입니다. 검색이 빗나가면 엉뚱한 조문을 근거로 삼습니다.",
    steps: ["질문", "검색", "전량 주입", "생성", "답변"],
  },
  {
    name: "Agentic RAG",
    summary: "질문을 분류하고, 검색한 뒤, 스스로 답을 검증합니다.",
    trait: "관련 없는 질문은 거절합니다. 3회를 넘기면 검증 미통과로 표시한 채 답을 내보냅니다.",
    steps: ["질문", "기억", "라우팅", "검색", "생성", "검증", "답변"],
    loop: true,
  },
];

export function PipelineDiagram() {
  return (
    <div className="grid gap-3 md:grid-cols-3">
      {PIPELINES.map((p) => (
        <Card key={p.name} className="gap-3">
          <CardHeader className="pb-0">
            <CardTitle className="text-sm">{p.name}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-sm text-muted-foreground">{p.summary}</p>

            <div className="flex flex-wrap items-center gap-1.5">
              {p.steps.map((s, i) => (
                <span key={s} className="flex items-center gap-1.5">
                  <Step muted={s === "질문"}>{s}</Step>
                  {i < p.steps.length - 1 ? <Arrow /> : null}
                </span>
              ))}
            </div>

            {p.loop ? (
              <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <RotateCcw className="size-3" />
                기준 미달이면 1회차는 재검색, 2회차는 재생성 (최대 3회)
              </p>
            ) : null}

            <p className="text-xs leading-relaxed">{p.trait}</p>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

export function PaperNote() {
  return (
    <p className="text-xs text-muted-foreground">
      <Badge variant="outline" className="mr-1.5 text-[10px]">
        출처
      </Badge>
      구조와 비교 방식은 「실무 환경에서의 Agentic RAG 파이프라인 구축 및 다중 계층 평가 체계에 관한 연구」를 따랐습니다.
    </p>
  );
}
