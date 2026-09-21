"""Vanilla LLM과 Native RAG (논문 4.5절 비교 대조군)."""

from __future__ import annotations

import time

from openai import AsyncOpenAI

from app.config import get_settings
from app.kb.retriever import hybrid_search, vector_retrieve
from app.kb.store import KnowledgeBase

from .base import Emit, RunRecord, format_references, load_prompts


async def run_vanilla(client: AsyncOpenAI, record: RunRecord, question: str, emit: Emit) -> RunRecord:
    prompts = load_prompts()

    started = time.monotonic()
    await emit("run_step", {"runId": record.run_id, "pipeline": "vanilla", "node": "generate", "attempt": 1})

    response = await client.chat.completions.create(
        model=record.model,
        messages=[
            {"role": "system", "content": prompts["vanilla"]["system"]},
            {"role": "user", "content": question},
        ],
    )
    usage = response.usage
    cached = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0) or 0
    record.add_step(
        "generate", started, tokens_in=usage.prompt_tokens, tokens_out=usage.completion_tokens, cached_in=cached
    )

    record.answer = response.choices[0].message.content or ""
    record.outcome = "answered"
    return record


async def run_native(
    client: AsyncOpenAI, kb: KnowledgeBase, record: RunRecord, question: str, emit: Emit
) -> RunRecord:
    prompts = load_prompts()

    paper_mode = get_settings().native_mode == "paper"

    started = time.monotonic()
    await emit("run_step", {"runId": record.run_id, "pipeline": "native", "node": "retrieve", "attempt": 1})
    # 논문 부록 4-B는 검색 결과를 정제 없이 전량 주입한다. 리랭킹으로 5개만 고르면
    # 논문이 보고한 토큰량(평균 10,091)과 비교 조건이 달라진다 (docs/paper_conformance.md 쟁점 3)
    retrieval = (
        await vector_retrieve(client, kb, question)
        if paper_mode
        else await hybrid_search(client, kb, question, model=record.model)
    )
    record.chunks = retrieval.chunks
    record.extra_cost += retrieval.cost_usd
    record.add_step(
        "retrieve",
        started,
        tokens_in=retrieval.tokens_in,
        tokens_out=retrieval.tokens_out,
        output={
            "query": retrieval.query,
            "vectorCount": retrieval.vector_count,
            "graphCount": retrieval.graph_count,
            "injection": "all" if paper_mode else "reranked_top_k",
        },
    )

    started = time.monotonic()
    await emit("run_step", {"runId": record.run_id, "pipeline": "native", "node": "generate", "attempt": 1})
    references = format_references(retrieval.chunks)
    response = await client.chat.completions.create(
        model=record.model,
        messages=[
            {"role": "system", "content": prompts["native"]["system"]},
            {
                "role": "user",
                "content": prompts["native"]["user"].format(references=references, question=question),
            },
        ],
    )
    usage = response.usage
    cached = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0) or 0
    record.add_step(
        "generate", started, tokens_in=usage.prompt_tokens, tokens_out=usage.completion_tokens, cached_in=cached
    )

    record.answer = response.choices[0].message.content or ""
    record.outcome = "answered"
    return record
