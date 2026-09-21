"""파이프라인 공통: 실행 기록(노드 단계·토큰·비용)과 이벤트 발행.

관측 도구를 쓰지 않기로 했으므로(04 §4-1-3) 여기서 남기는 기록이 유일한 근거다.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from app.config import cost_usd, get_settings
from app.kb.retriever import RetrievedChunk

Pipeline = Literal["vanilla", "native", "agentic"]
Outcome = Literal["answered", "rejected", "fallback", "failed", "canceled"]
Emit = Callable[[str, dict[str, Any]], Awaitable[None]]

PROMPT_DIR = Path(__file__).resolve().parents[2] / "config" / "prompts"


def load_prompts(name: str = "paper") -> dict:
    return yaml.safe_load((PROMPT_DIR / f"{name}.yaml").read_text(encoding="utf-8"))


@dataclass
class StepRecord:
    seq: int
    node: str
    attempt: int
    latency_ms: int
    tokens_in: int = 0
    tokens_out: int = 0
    cached_in: int = 0
    output: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunRecord:
    run_id: str
    pipeline: Pipeline
    model: str = ""
    outcome: Outcome | None = None
    answer: str = ""
    route: str | None = None
    fallback_used: bool = False
    critic_attempts: int = 0
    error_type: str | None = None
    error_message: str | None = None
    steps: list[StepRecord] = field(default_factory=list)
    chunks: list[RetrievedChunk] = field(default_factory=list)
    started_at: float = field(default_factory=time.monotonic)
    tokens_in: int = 0
    tokens_out: int = 0
    cached_in: int = 0
    extra_cost: float = 0.0

    @property
    def latency_ms(self) -> int:
        return int((time.monotonic() - self.started_at) * 1000)

    @property
    def cost_usd(self) -> float:
        return cost_usd(self.tokens_in, self.tokens_out, self.cached_in, self.model) + self.extra_cost

    def add_step(
        self,
        node: str,
        started: float,
        *,
        attempt: int = 1,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cached_in: int = 0,
        output: dict[str, Any] | None = None,
    ) -> StepRecord:
        step = StepRecord(
            seq=len(self.steps) + 1,
            node=node,
            attempt=attempt,
            latency_ms=int((time.monotonic() - started) * 1000),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cached_in=cached_in,
            output=output or {},
        )
        self.steps.append(step)
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out
        self.cached_in += cached_in
        return step

    def metrics(self) -> dict[str, Any]:
        return {
            "latencyMs": self.latency_ms,
            "tokensIn": self.tokens_in,
            "tokensOut": self.tokens_out,
            "costUsd": round(self.cost_usd, 6),
            "model": self.model,
            "priceVersion": get_settings().price_version,
        }

    def trace(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for step in self.steps:
            if step.node == "router" and step.output:
                payload["route"] = step.output
            if step.node == "memory" and step.output:
                payload["disputeTarget"] = step.output
            if step.node == "critic" and step.output:
                payload.setdefault("critics", []).append({"attempt": step.attempt, **step.output})
        if self.chunks:
            payload["retrievals"] = [
                {
                    "attempt": 1,
                    "query": next(
                        (s.output.get("query", "") for s in self.steps if s.node == "retrieve"), ""
                    ),
                    "chunks": [
                        {
                            "chunkId": c.chunk_id,
                            "path": c.path,
                            "textSnapshot": c.text[:2000],
                            "vectorScore": c.vector_score,
                            "graphHit": c.graph_hit,
                            "rerankRank": c.rerank_rank,
                            "selected": c.selected,
                        }
                        for c in self.chunks[:20]
                    ],
                }
            ]
        if self.fallback_used:
            payload["fallbackReason"] = "Critic 기준 미달 → Native RAG 결과로 대체"
        return payload


def format_references(chunks: list[RetrievedChunk]) -> str:
    lines = []
    for index, chunk in enumerate([c for c in chunks if c.selected], start=1):
        score = f"{chunk.vector_score:.2f}" if chunk.vector_score is not None else "-"
        lines.append(f"[참조 {index}] ({chunk.path}, 유사도 {score})\n{chunk.text}")
    return "\n\n".join(lines)
