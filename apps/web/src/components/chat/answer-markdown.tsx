"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** LLM 답변은 마크다운을 섞어 쓴다. 기호가 그대로 보이지 않도록 렌더링한다. */
export function AnswerMarkdown({ children }: { children: string }) {
  return (
    <div className="space-y-2 text-sm leading-relaxed">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: ({ children }) => <p className="whitespace-pre-wrap">{children}</p>,
          strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
          ul: ({ children }) => <ul className="list-disc space-y-1 pl-5">{children}</ul>,
          ol: ({ children }) => <ol className="list-decimal space-y-1 pl-5">{children}</ol>,
          li: ({ children }) => <li className="pl-0.5">{children}</li>,
          h1: ({ children }) => <p className="font-heading font-semibold">{children}</p>,
          h2: ({ children }) => <p className="font-heading font-semibold">{children}</p>,
          h3: ({ children }) => <p className="font-heading font-semibold">{children}</p>,
          a: ({ children, href }) => (
            <a href={href} className="underline underline-offset-2" target="_blank" rel="noreferrer">
              {children}
            </a>
          ),
          code: ({ children }) => (
            <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs">{children}</code>
          ),
          blockquote: ({ children }) => (
            <blockquote className="border-l-2 pl-3 text-muted-foreground">{children}</blockquote>
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
