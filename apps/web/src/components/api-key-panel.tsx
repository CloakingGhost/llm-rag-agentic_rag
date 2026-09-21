"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { useApiKey } from "@/lib/api-key";

export function ApiKeyPanel() {
  const { panelOpen, closePanel, apiKey, status, saveKey, clearKey } = useApiKey();
  const [draft, setDraft] = useState("");

  return (
    <Dialog open={panelOpen} onOpenChange={(open) => (open ? null : closePanel())}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>OpenAI API 키</DialogTitle>
          <DialogDescription>
            질문 비용은 입력하신 키로 청구됩니다. 키는 이 브라우저에만 저장되고 서버에는 저장되지 않습니다.
          </DialogDescription>
        </DialogHeader>

        {apiKey ? (
          <div className="space-y-3">
            <p className="text-sm text-muted-foreground">
              현재 키: <span className="font-mono">{apiKey.slice(0, 7)}…{apiKey.slice(-4)}</span>
            </p>
            <Button variant="outline" onClick={clearKey}>
              키 지우기
            </Button>
          </div>
        ) : (
          <div className="space-y-2">
            <Input
              type="password"
              placeholder="sk-..."
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              autoComplete="off"
            />
            {status === "invalid" ? (
              <p className="text-sm text-destructive">유효하지 않은 키입니다.</p>
            ) : null}
            <p className="text-xs text-muted-foreground">
              키는 platform.openai.com에서 발급할 수 있습니다.
            </p>
          </div>
        )}

        <DialogFooter>
          {apiKey ? (
            <Button onClick={closePanel}>닫기</Button>
          ) : (
            <Button onClick={() => saveKey(draft)} disabled={status === "checking" || draft.length === 0}>
              {status === "checking" ? "확인 중…" : "저장"}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
