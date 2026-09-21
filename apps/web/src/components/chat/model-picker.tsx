"use client";

import { Cpu } from "lucide-react";
import { useEffect, useState } from "react";

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { fetchModels, type ModelOption } from "@/lib/api";

const STORAGE = "cdq.model";

/** 논문 7.1절의 3개 모델 중 선택. 고른 값은 이 브라우저에 기억한다. */
export function useModelChoice() {
  const [models, setModels] = useState<ModelOption[]>([]);
  const [model, setModel] = useState<string>("");

  useEffect(() => {
    let alive = true;
    fetchModels()
      .then(({ default: fallback, models: list }) => {
        if (!alive) return;
        const stored = window.localStorage.getItem(STORAGE);
        const valid = stored && list.some((m) => m.id === stored) ? stored : fallback;
        setModels(list);
        setModel(valid);
      })
      .catch(() => null);
    return () => {
      alive = false;
    };
  }, []);

  const choose = (value: string) => {
    window.localStorage.setItem(STORAGE, value);
    setModel(value);
  };

  return { models, model, choose };
}

export function ModelPicker({
  models,
  model,
  onChange,
  disabled,
}: {
  models: ModelOption[];
  model: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}) {
  if (!models.length) return null;
  const current = models.find((m) => m.id === model);

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Cpu className="size-3.5 text-muted-foreground" />
      <span className="text-xs text-muted-foreground">모델</span>
      <Select value={model} onValueChange={onChange} disabled={disabled}>
        <SelectTrigger size="sm" className="w-48">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {models.map((m) => (
            <SelectItem key={m.id} value={m.id}>
              {m.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {current ? (
        <span className="text-xs text-muted-foreground">
          1M당 입력 ${current.pricePer1M.input} · 출력 ${current.pricePer1M.output}
        </span>
      ) : null}
    </div>
  );
}
