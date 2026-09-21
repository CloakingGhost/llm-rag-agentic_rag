import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import type { ReactNode } from "react";
import "./globals.css";

import { ApiKeyPanel } from "@/components/api-key-panel";
import { SiteChrome } from "@/components/site-chrome";
import { ThemeProvider } from "@/components/theme-provider";
import { Toaster } from "@/components/ui/sonner";
import { ApiKeyProvider } from "@/lib/api-key";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "소비자 분쟁 Q&A 챗봇",
  description:
    "Vanilla LLM · Native RAG · Agentic RAG 세 파이프라인을 같은 질문으로 비교해 보는 소비자 분쟁 상담 챗봇",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html
      lang="ko"
      suppressHydrationWarning
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="flex min-h-full flex-col">
        <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
          <ApiKeyProvider>
            <SiteChrome />
            <main className="flex flex-1 flex-col">{children}</main>
            <ApiKeyPanel />
            <Toaster position="top-center" />
          </ApiKeyProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
