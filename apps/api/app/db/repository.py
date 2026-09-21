"""로그 저장·조회. DB가 없거나 실패해도 답변은 나가야 하므로 예외를 삼킨다."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.db.models import Conversation, Request, RetrievedChunkRow, Run, Step
from app.db.session import session_scope
from app.pipelines.base import RunRecord

log = logging.getLogger(__name__)

def _utcnow() -> datetime:
    return datetime.now(UTC)


CHUNK_TEXT_LIMIT = 2000
CHUNK_LIMIT = 20


async def save_conversation(
    conversation_id: str,
    client_id: str = "",
    *,
    model: str = "",
    turn_count: int | None = None,
    product_name: str | None = None,
    dispute_type: str | None = None,
    closed: bool | None = None,
) -> None:
    """대화 세션 기록. 분쟁 대상은 화면에 보여 주기 위한 스냅샷이다."""
    fields: dict[str, Any] = {"id": conversation_id, "last_turn_at": _utcnow()}
    if client_id:
        fields["client_id"] = client_id
    if model:
        fields["model"] = model
    if turn_count is not None:
        fields["turn_count"] = turn_count
    if product_name is not None:
        fields["product_name"] = product_name[:120]
    if dispute_type is not None:
        fields["dispute_type"] = dispute_type[:60]
    if closed is not None:
        fields["closed"] = closed
    try:
        async with session_scope() as session:
            await session.merge(Conversation(**fields))
    except Exception as exc:  # noqa: BLE001
        log.warning("대화 기록 실패: %s", exc)


async def save_request(
    request_id: str,
    mode: str,
    question: str,
    client_id: str,
    build_id: str,
    status: str = "running",
    conversation_id: str | None = None,
    turn_index: int = 1,
) -> None:
    # DB 종류에 기대지 않도록 merge를 쓴다 (운영 Postgres, 로컬 SQLite 모두 동작)
    try:
        async with session_scope() as session:
            await session.merge(
                Request(
                    id=request_id,
                    mode=mode,
                    question=question,
                    client_id=client_id,
                    build_id=build_id,
                    status=status,
                    conversation_id=conversation_id,
                    turn_index=turn_index,
                )
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("요청 기록 실패: %s", exc)


async def save_run(request_id: str, record: RunRecord) -> None:
    settings = get_settings()
    try:
        async with session_scope() as session:
            await session.merge(
                Run(
                    id=record.run_id,
                    request_id=request_id,
                    pipeline=record.pipeline,
                    outcome=record.outcome,
                    answer=record.answer,
                    route=record.route,
                    fallback_used=record.fallback_used,
                    fallback_source_run_id=getattr(record, "fallback_source_run_id", None),
                    critic_attempts=record.critic_attempts,
                    tool_calls=record.tool_calls,
                    no_info_flag=getattr(record, "no_info_flag", False),
                    error_type=record.error_type,
                    error_message=record.error_message,
                    latency_ms=record.latency_ms,
                    tokens_in=record.tokens_in,
                    tokens_out=record.tokens_out,
                    cached_in=record.cached_in,
                    cost_usd=record.cost_usd,
                    price_version=settings.price_version,
                    model=record.model,
                )
            )
            for step in record.steps:
                session.add(
                    Step(
                        run_id=record.run_id,
                        seq=step.seq,
                        node=step.node,
                        attempt=step.attempt,
                        latency_ms=step.latency_ms,
                        tokens_in=step.tokens_in,
                        tokens_out=step.tokens_out,
                        cached_in=step.cached_in,
                        output=step.output,
                    )
                )
            for chunk in record.chunks[:CHUNK_LIMIT]:
                session.add(
                    RetrievedChunkRow(
                        run_id=record.run_id,
                        chunk_id=chunk.chunk_id,
                        path=chunk.path,
                        text_snapshot=chunk.text[:CHUNK_TEXT_LIMIT],
                        vector_score=chunk.vector_score,
                        graph_hit=chunk.graph_hit,
                        rrf_score=chunk.rrf_score,
                        rerank_rank=chunk.rerank_rank,
                        selected=chunk.selected,
                    )
                )
    except Exception as exc:  # noqa: BLE001
        log.warning("실행 기록 실패: %s", exc)


async def list_requests(
    *, mode: str | None, pipeline: str | None, outcome: str | None, q: str | None, page: int, size: int = 30
) -> dict[str, Any]:
    async with session_scope() as session:
        stmt = select(Request).options(selectinload(Request.runs)).order_by(Request.created_at.desc())
        if mode:
            stmt = stmt.where(Request.mode == mode)
        if q:
            stmt = stmt.where(Request.question.contains(q))

        rows = (await session.execute(stmt.limit(500))).scalars().all()

        if pipeline:
            rows = [r for r in rows if any(run.pipeline == pipeline for run in r.runs)]
        if outcome:
            rows = [r for r in rows if any(run.outcome == outcome for run in r.runs)]

        total = len(rows)
        page_rows = rows[(page - 1) * size : page * size]
        return {
            "total": total,
            "page": page,
            "items": [
                {
                    "requestId": r.id,
                    "createdAt": r.created_at.isoformat(),
                    "question": r.question,
                    "mode": r.mode,
                    "clientId": r.client_id,
                    "conversationId": r.conversation_id,
                    "turnIndex": r.turn_index,
                    "buildId": r.build_id,
                    "status": r.status,
                    "outcomes": [
                        {"pipeline": run.pipeline, "outcome": run.outcome, "model": run.model} for run in r.runs
                    ],
                    "model": next((run.model for run in r.runs), ""),
                    "latencyMs": max((run.latency_ms for run in r.runs), default=0),
                    "costUsd": sum(run.cost_usd for run in r.runs),
                }
                for r in page_rows
            ],
        }


async def get_request_detail(request_id: str) -> dict[str, Any] | None:
    async with session_scope() as session:
        stmt = (
            select(Request)
            .where(Request.id == request_id)
            .options(selectinload(Request.runs).selectinload(Run.steps))
            .options(selectinload(Request.runs).selectinload(Run.chunks))
        )
        row = (await session.execute(stmt)).scalars().first()
        if row is None:
            return None

        return {
            "requestId": row.id,
            "createdAt": row.created_at.isoformat(),
            "question": row.question,
            "mode": row.mode,
            "clientId": row.client_id,
            "conversationId": row.conversation_id,
            "turnIndex": row.turn_index,
            "buildId": row.build_id,
            "status": row.status,
            "runs": [
                {
                    "runId": run.id,
                    "pipeline": run.pipeline,
                    "outcome": run.outcome,
                    "answer": run.answer,
                    "route": run.route,
                    "fallbackUsed": run.fallback_used,
                    "criticAttempts": run.critic_attempts,
                    "toolCalls": run.tool_calls,
                    "model": run.model,
                    "errorType": run.error_type,
                    "errorMessage": run.error_message,
                    "metrics": {
                        "latencyMs": run.latency_ms,
                        "tokensIn": run.tokens_in,
                        "tokensOut": run.tokens_out,
                        "costUsd": run.cost_usd,
                        "model": run.model,
                        "priceVersion": run.price_version,
                    },
                    "steps": [
                        {
                            "seq": s.seq,
                            "node": s.node,
                            "attempt": s.attempt,
                            "latencyMs": s.latency_ms,
                            "tokensIn": s.tokens_in,
                            "tokensOut": s.tokens_out,
                            "output": s.output,
                        }
                        for s in sorted(run.steps, key=lambda s: s.seq)
                    ],
                    "chunks": [
                        {
                            "chunkId": c.chunk_id,
                            "path": c.path,
                            "textSnapshot": c.text_snapshot,
                            "vectorScore": c.vector_score,
                            "graphHit": c.graph_hit,
                            "rerankRank": c.rerank_rank,
                            "selected": c.selected,
                        }
                        for c in sorted(run.chunks, key=lambda c: c.rerank_rank)
                    ],
                }
                for run in row.runs
            ],
        }
