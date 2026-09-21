import { BookText, KeyRound, ShieldCheck } from "lucide-react";
import Link from "next/link";

import { PaperNote, PipelineDiagram } from "@/components/pipeline-diagram";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { fetchKbInfo } from "@/lib/api";

const FALLBACK_DOCUMENTS = [
  { name: "소비자분쟁해결기준", detail: "공정거래위원회고시 제2025-14호 · 2025.12.18 시행" },
  { name: "전자상거래 등에서의 소비자보호에 관한 법률", detail: "법률 제21312호 · 2026.7.21 시행" },
];

export default async function Home() {
  // 서버가 잠들어 있으면 소개 페이지는 기본값으로 보여준다
  const info = await fetchKbInfo().catch(() => null);
  const documents = info?.documents ?? FALLBACK_DOCUMENTS;
  return (
    <div className="mx-auto w-full max-w-5xl space-y-10 px-4 py-10">
      <section className="space-y-4 text-center">
        <h1 className="font-heading text-2xl font-semibold sm:text-3xl">
          같은 질문, 세 가지 방식의 답
        </h1>
        <p className="mx-auto max-w-2xl text-sm text-muted-foreground sm:text-base">
          소비자 분쟁 상담을 Vanilla LLM · Native RAG · Agentic RAG 세 가지로 처리해 보고, 답과 비용과
          속도가 어떻게 달라지는지 직접 확인합니다.
        </p>
        <Button asChild size="lg">
          <Link href="/chat">시작하기</Link>
        </Button>
      </section>

      <section className="space-y-3">
        <h2 className="font-heading text-lg font-semibold">세 가지 방식의 차이</h2>
        <PipelineDiagram />
        <PaperNote />
      </section>

      <section className="grid gap-3 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-sm">
              <KeyRound className="size-4" /> 사용 방법과 비용
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm text-muted-foreground">
            <p>
              질문하려면 OpenAI API 키가 필요합니다. 모든 요청은 입력하신 키로 처리되고,{" "}
              <strong className="text-foreground">비용도 본인 키로 청구됩니다.</strong>
            </p>
            <p>
              키는 이 브라우저에만 저장되고 서버에는 저장되지 않습니다. 언제든 상단 열쇠 아이콘에서 지울 수
              있습니다.
            </p>
            <p>
              모든 단계에 <code className="rounded bg-muted px-1 py-0.5 text-xs">gpt-4o-mini</code>를 사용합니다.
              질문 한 건에 대략 $0.001 안팎이 듭니다.
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-sm">
              <BookText className="size-4" /> 무엇을 근거로 답하나요
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm text-muted-foreground">
            <ul className="space-y-2">
              {documents.map((doc) => (
                <li key={doc.name}>
                  <p className="text-foreground">{doc.name}</p>
                  <p className="text-xs">{doc.detail}</p>
                </li>
              ))}
            </ul>
            <p className="flex items-start gap-1.5 text-xs">
              <ShieldCheck className="mt-0.5 size-3.5 shrink-0" />
              검색과 검증을 거친 답에는 어떤 조문과 어떤 별표 항목을 봤는지 함께 보여 드립니다.
            </p>
            {info ? (
              <p className="text-xs">
                현재 지식베이스: 조항·기준 {info.chunkCount.toLocaleString()}건, 개념 그래프{" "}
                {info.graphNodes.toLocaleString()}개
              </p>
            ) : null}
          </CardContent>
        </Card>
      </section>
    </div>
  );
}
