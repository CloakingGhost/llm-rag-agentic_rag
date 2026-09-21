"use client";

import { createContext, useCallback, useContext, useMemo, useState, useSyncExternalStore } from "react";

import { validateKey } from "@/lib/api";

// 02_business_data_analysis.md: API 키와 익명 브라우저 ID는 브라우저 localStorage에만 둔다.
const KEY_STORAGE = "cdq.openai-key";
const CLIENT_ID_STORAGE = "cdq.client-id";

/** localStorage를 구독 가능한 외부 스토어로 감싼다 (effect 안에서 setState 하지 않기 위함). */
const listeners = new Set<() => void>();
const keyStore = {
  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
  getSnapshot: () => window.localStorage.getItem(KEY_STORAGE),
  getServerSnapshot: () => null,
  set(value: string) {
    window.localStorage.setItem(KEY_STORAGE, value);
    listeners.forEach((l) => l());
  },
  clear() {
    window.localStorage.removeItem(KEY_STORAGE);
    listeners.forEach((l) => l());
  },
};

/** 요청을 보낼 때만 필요하므로 렌더 중이 아니라 호출 시점에 만든다. */
export function getClientId() {
  let id = window.localStorage.getItem(CLIENT_ID_STORAGE);
  if (!id) {
    id = crypto.randomUUID();
    window.localStorage.setItem(CLIENT_ID_STORAGE, id);
  }
  return id;
}

export type KeyStatus = "empty" | "checking" | "valid" | "invalid";

interface ApiKeyContextValue {
  apiKey: string | null;
  status: KeyStatus;
  panelOpen: boolean;
  openPanel: () => void;
  closePanel: () => void;
  saveKey: (key: string) => Promise<void>;
  clearKey: () => void;
  markInvalid: () => void;
}

const ApiKeyContext = createContext<ApiKeyContextValue | null>(null);

export function ApiKeyProvider({ children }: { children: React.ReactNode }) {
  const apiKey = useSyncExternalStore(keyStore.subscribe, keyStore.getSnapshot, keyStore.getServerSnapshot);
  const [transient, setTransient] = useState<"idle" | "checking" | "invalid">("idle");
  const [panelOpen, setPanelOpen] = useState(false);

  const status: KeyStatus =
    transient === "checking" ? "checking" : transient === "invalid" ? "invalid" : apiKey ? "valid" : "empty";

  const saveKey = useCallback(async (key: string) => {
    setTransient("checking");
    const result = await validateKey(key).catch(() => ({ valid: false }));
    if (!result.valid) {
      setTransient("invalid");
      return;
    }
    keyStore.set(key);
    setTransient("idle");
    setPanelOpen(false);
  }, []);

  const clearKey = useCallback(() => {
    keyStore.clear();
    setTransient("idle");
  }, []);

  const value = useMemo<ApiKeyContextValue>(
    () => ({
      apiKey,
      status,
      panelOpen,
      openPanel: () => setPanelOpen(true),
      closePanel: () => setPanelOpen(false),
      saveKey,
      clearKey,
      markInvalid: () => setTransient("invalid"),
    }),
    [apiKey, status, panelOpen, saveKey, clearKey],
  );

  return <ApiKeyContext.Provider value={value}>{children}</ApiKeyContext.Provider>;
}

export function useApiKey() {
  const ctx = useContext(ApiKeyContext);
  if (!ctx) throw new Error("useApiKey must be used within ApiKeyProvider");
  return ctx;
}
