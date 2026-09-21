"use client";

import Link from "next/link";
import { useSyncExternalStore } from "react";

import { Button } from "@/components/ui/button";
import { adminSession } from "@/lib/admin-session";
import { adminLogout } from "@/lib/api";

/** 로그인 상태가 아니면 로그인 화면으로 보낸다. (04 문서: 미로그인 접근은 /admin으로) */
export function AdminGuard({ children }: { children: React.ReactNode }) {
  const session = useSyncExternalStore(
    adminSession.subscribe,
    adminSession.getSnapshot,
    adminSession.getServerSnapshot,
  );

  if (!session) {
    return (
      <div className="mx-auto flex w-full max-w-sm flex-1 flex-col items-center justify-center gap-3 px-4 py-16">
        <p className="text-sm text-muted-foreground">관리자 로그인이 필요합니다.</p>
        <Button asChild>
          <Link href="/admin">로그인하러 가기</Link>
        </Button>
      </div>
    );
  }

  return <>{children}</>;
}

export function AdminBar({ title }: { title: string }) {
  return (
    <div className="flex items-center gap-3">
      <h1 className="font-heading text-lg font-semibold">{title}</h1>
      <Button
        variant="ghost"
        size="sm"
        className="ml-auto text-xs"
        onClick={async () => {
          await adminLogout().catch(() => null);
          adminSession.clear();
        }}
      >
        로그아웃
      </Button>
    </div>
  );
}
