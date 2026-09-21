"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { adminSession } from "@/lib/admin-session";
import { adminLogin } from "@/lib/api";

export default function AdminLoginPage() {
  const router = useRouter();
  const [id, setId] = useState("");
  const [pw, setPw] = useState("");
  const [error, setError] = useState(false);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    setError(false);
    try {
      await adminLogin(id, pw);
      adminSession.markLoggedIn();
      router.push("/admin/logs");
    } catch {
      setError(true);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto flex w-full max-w-sm flex-1 items-center px-4 py-16">
      <Card className="w-full">
        <CardHeader>
          <CardTitle className="text-base">관리자 로그인</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <Input placeholder="아이디" value={id} onChange={(e) => setId(e.target.value)} autoComplete="off" />
          <Input
            type="password"
            placeholder="비밀번호"
            value={pw}
            onChange={(e) => setPw(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
            autoComplete="off"
          />
          {error ? <p className="text-sm text-destructive">아이디 또는 비밀번호가 올바르지 않습니다.</p> : null}
          <Button className="w-full" onClick={submit} disabled={busy}>
            {busy ? "확인 중…" : "로그인"}
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
