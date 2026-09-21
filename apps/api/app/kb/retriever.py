"""하이브리드 검색: 벡터 + 그래프 → RRF 병합 → LLM 리랭킹 (논문 3.6, 4.3).

화면의 검색 결과 표(경로·벡터·그래프·순위·사용)에 그대로 대응하는 값을 남긴다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from openai import AsyncOpenAI

from app.config import cost_usd, get_settings, sampling_args
from app.kb.store import KnowledgeBase

RRF_K = 60


@dataclass
class RetrievedChunk:
    chunk_id: str
    path: str
    text: str
    vector_score: float | None = None
    graph_hit: bool = False
    rrf_score: float = 0.0
    rerank_rank: int = 0
    selected: bool = False
    meta: dict = field(default_factory=dict)


@dataclass
class RetrievalResult:
    query: str
    chunks: list[RetrievedChunk]
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    vector_count: int = 0
    graph_count: int = 0


ENTITY_SCHEMA = {
    "name": "entities",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["entities"],
        "properties": {"entities": {"type": "array", "items": {"type": "string"}}},
    },
    "strict": True,
}

RERANK_SCHEMA = {
    "name": "rerank",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["ranking"],
        "properties": {"ranking": {"type": "array", "items": {"type": "string"}}},
    },
    "strict": True,
}


async def extract_entities(client: AsyncOpenAI, question: str, model: str) -> tuple[list[str], int, int]:
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "질문에서 소비자 분쟁 규정 검색에 쓸 핵심 개념을 3~7개 뽑아라. "
                    "품목명, 분쟁 유형, 법률 용어를 우선한다. 일반적인 낱말은 넣지 않는다."
                ),
            },
            {"role": "user", "content": question},
        ],
        response_format={"type": "json_schema", "json_schema": ENTITY_SCHEMA},
        **sampling_args(model),
    )
    usage = response.usage
    payload = json.loads(response.choices[0].message.content or "{}")
    return payload.get("entities", []), usage.prompt_tokens, usage.completion_tokens


def _vector_search(
    kb: KnowledgeBase, embedding: list[float], top_k: int, titles: list[str] | None = None
) -> list[RetrievedChunk]:
    where = {"title": {"$in": titles}} if titles else None
    result = kb.collection.query(  # type: ignore[union-attr]
        query_embeddings=[embedding],
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
        **({"where": where} if where else {}),
    )
    chunks: list[RetrievedChunk] = []
    for chunk_id, document, metadata, distance in zip(
        result["ids"][0], result["documents"][0], result["metadatas"][0], result["distances"][0], strict=True
    ):
        chunks.append(
            RetrievedChunk(
                chunk_id=chunk_id,
                path=str(metadata.get("path", "")),
                text=document,
                vector_score=round(1 - float(distance), 4),  # 코사인 거리 → 유사도
                meta=dict(metadata),
            )
        )
    return chunks


def _graph_search(kb: KnowledgeBase, entities: list[str], hops: int, limit: int) -> list[str]:
    """개념 노드에서 이웃을 따라가며 연결된 청크 ID를 모은다."""
    seeds = [e for e in entities if kb.graph.has_node(e)]
    if not seeds:
        # 부분 일치로 한 번 더 찾는다 (예: '스마트폰 하자' → '스마트폰')
        seeds = [n for n in kb.graph.nodes if any(e in n or n in e for e in entities)][:5]

    visited: set[str] = set()
    frontier = list(seeds)
    for _ in range(hops):
        nxt: list[str] = []
        for node in frontier:
            if node in visited:
                continue
            visited.add(node)
            nxt.extend(kb.graph.neighbors(node))
        frontier = nxt

    chunk_ids: list[str] = []
    for node in visited:
        for chunk_id in kb.node_chunks.get(node, []):
            if chunk_id not in chunk_ids:
                chunk_ids.append(chunk_id)
    return chunk_ids[:limit]


def _rrf(
    vector: list[RetrievedChunk],
    graph_ids: list[str],
    by_id: dict[str, RetrievedChunk],
    item_ids: list[str] | None = None,
) -> list[RetrievedChunk]:
    scores: dict[str, float] = {}
    for rank, chunk in enumerate(vector, start=1):
        scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0) + 1 / (RRF_K + rank)
    for rank, chunk_id in enumerate(graph_ids, start=1):
        scores[chunk_id] = scores.get(chunk_id, 0) + 1 / (RRF_K + rank)
        if chunk_id in by_id:
            by_id[chunk_id].graph_hit = True
    # 품목이 특정된 경우 그 품목 표 안의 검색 결과를 한 축 더 얹는다
    for rank, chunk_id in enumerate(item_ids or [], start=1):
        scores[chunk_id] = scores.get(chunk_id, 0) + 1 / (RRF_K + rank)

    merged = []
    for chunk_id, score in sorted(scores.items(), key=lambda kv: kv[1], reverse=True):
        chunk = by_id.get(chunk_id)
        if chunk is None:
            continue
        chunk.rrf_score = round(score, 5)
        merged.append(chunk)
    return merged


async def _rerank(
    client: AsyncOpenAI, question: str, candidates: list[RetrievedChunk], top_k: int, model: str
) -> tuple[list[RetrievedChunk], int, int]:
    listing = [
        {"id": c.chunk_id, "path": c.path, "text": c.text[:400]} for c in candidates[: min(len(candidates), 20)]
    ]
    response = await client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "소비자 분쟁 질문에 답하는 데 도움이 되는 순서로 문서 id를 정렬하라. "
                    "기준은 '문제 해결 기여도'와 '정보 밀도'다. 단순히 낱말이 겹치는 문서를 위로 올리지 마라. "
                    "질문에 품목이 드러나면(예: 노트북 → 전자제품·사무용기기) 그 품목의 조항을 "
                    "다른 품목의 비슷한 조항보다 반드시 위에 둔다."
                ),
            },
            {"role": "user", "content": json.dumps({"question": question, "documents": listing}, ensure_ascii=False)},
        ],
        response_format={"type": "json_schema", "json_schema": RERANK_SCHEMA},
        **sampling_args(model),
    )
    usage = response.usage
    order = json.loads(response.choices[0].message.content or "{}").get("ranking", [])

    by_id = {c.chunk_id: c for c in candidates}
    ranked = [by_id[i] for i in order if i in by_id]
    ranked += [c for c in candidates if c.chunk_id not in {r.chunk_id for r in ranked}]

    for rank, chunk in enumerate(ranked, start=1):
        chunk.rerank_rank = rank
        chunk.selected = rank <= top_k
    return ranked, usage.prompt_tokens, usage.completion_tokens


async def hybrid_search(
    client: AsyncOpenAI,
    kb: KnowledgeBase,
    question: str,
    dispute_type: str | None = None,
    model: str | None = None,
) -> RetrievalResult:
    settings = get_settings()
    model = model or settings.chat_model
    query = f"{dispute_type} {question}".strip() if dispute_type else question
    # 별표Ⅰ 매핑으로 품목 묶음 이름을 덧붙인다 (예: 노트북 → 전자제품, 사무용기기)
    expansions = kb.expand_query(question)
    if expansions:
        query = f"{query} ({' '.join(expansions)})"

    tokens_in = tokens_out = 0

    embedding_response = await client.embeddings.create(model=settings.embed_model, input=query[:7000])
    embed_tokens = embedding_response.usage.total_tokens
    embedding = embedding_response.data[0].embedding

    vector_chunks = _vector_search(kb, embedding, settings.retrieve_top_k)
    by_id = {c.chunk_id: c for c in vector_chunks}

    # 품목이 특정되면 그 품목 표 안에서도 검색한다 (같은 임베딩을 재사용하므로 추가 비용 없음)
    item_titles = kb.matching_titles(question)
    item_chunks = _vector_search(kb, embedding, 8, titles=item_titles) if item_titles else []
    for chunk in item_chunks:
        by_id.setdefault(chunk.chunk_id, chunk)

    entities, e_in, e_out = await extract_entities(client, question, model)
    tokens_in += e_in
    tokens_out += e_out

    graph_ids = _graph_search(kb, entities, hops=2, limit=10) if kb.graph.number_of_nodes() else []
    missing = [cid for cid in graph_ids if cid not in by_id]
    if missing:
        extra = kb.collection.get(ids=missing, include=["documents", "metadatas"])  # type: ignore[union-attr]
        for chunk_id, document, metadata in zip(
            extra["ids"], extra["documents"], extra["metadatas"], strict=True
        ):
            by_id[chunk_id] = RetrievedChunk(
                chunk_id=chunk_id,
                path=str(metadata.get("path", "")),
                text=document,
                graph_hit=True,
                meta=dict(metadata),
            )

    merged = _rrf(vector_chunks, graph_ids, by_id, [c.chunk_id for c in item_chunks])
    ranked, r_in, r_out = await _rerank(client, question, merged, settings.rerank_top_k, model)
    tokens_in += r_in
    tokens_out += r_out

    cost = cost_usd(tokens_in, tokens_out, model_id=model)
    cost += embed_tokens * settings.price_embedding / 1_000_000

    return RetrievalResult(
        query=query,
        chunks=ranked,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost,
        vector_count=len(vector_chunks),
        graph_count=len(graph_ids),
    )
