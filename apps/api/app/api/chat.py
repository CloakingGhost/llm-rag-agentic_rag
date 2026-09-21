"""POST /api/chat (SSE).

- 모드에 따라 1개 또는 3개 파이프라인을 서버가 동시에 실행한다.
- 대화 ID(conversationId)를 받아 Agentic 실행에 세션 메모리를 붙인다 (논문 3.4).
  Vanilla·Native는 논문의 대조군 그대로 상태를 갖지 않는다.
- 끝나는 순서대로 이벤트를 내보낸다.
- Agentic이 폴백하면 같은 요청에서 돌고 있는 Native 결과를 재사용한다 (추가 비용 없음).
- 중지는 취소 API와 연결 끊김 둘 다로 받는다.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator
from typing import Any, Literal

import orjson
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from openai import APIStatusError, AsyncOpenAI
from pydantic import BaseModel, Field

from app.config import DEFAULT_MODEL, MODEL_CATALOG, get_settings
from app.db.repository import save_conversation, save_request, save_run
from app.kb.store import get_kb
from app.pipelines.agentic import run_agentic
from app.pipelines.base import RunRecord
from app.pipelines.session_memory import get_session_memory
from app.pipelines.simple import run_native, run_vanilla

router = APIRouter(prefix="/api", tags=["chat"])

MODE_PIPELINES: dict[str, list[str]] = {
    "all": ["vanilla", "native", "agentic"],
    "vanilla": ["vanilla"],
    "rag": ["native"],
    "agentic": ["agentic"],
}

NO_INFO_PATTERNS = ("정보가 없", "내용이 없", "답변을 제공할 수 없", "포함되어 있지 않")

_running: dict[str, asyncio.Event] = {}
_client_running: set[str] = set()
_semaphore: asyncio.Semaphore | None = None


def _sem() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(get_settings().max_concurrent_runs)
    return _semaphore


class ChatRequest(BaseModel):
    mode: Literal["all", "vanilla", "rag", "agentic"] = "all"
    question: str = Field(min_length=1, max_length=2000)
    client_id: str = Field(alias="clientId", min_length=1, max_length=64)
    # 같은 대화의 후속 질문이면 앞선 응답에서 받은 값을 그대로 보낸다. 없으면 새 대화로 연다
    conversation_id: str | None = Field(default=None, alias="conversationId", max_length=40)
    # 논문 7.1절의 3개 모델 중 선택 (기본 GPT-5.6 Luna)
    model: str = DEFAULT_MODEL
    # 도구 호출 사용 여부. 생략하면 서버 기본값(TOOLS_ENABLED, 기본 꺼짐)을 쓴다.
    # 논문 9.2절의 '도구 편향'을 켜고 끄며 비교하기 위한 실험용 항목이다 (cli/tool_bias.py)
    tools: bool | None = None

    model_config = {"populate_by_name": True, "protected_namespaces": ()}


def _sse(event: str, data: dict[str, Any]) -> bytes:
    return b"event: " + event.encode() + b"\ndata: " + orjson.dumps(data) + b"\n\n"


def _error_type(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, TimeoutError):
        return "timeout", "60초 안에 답하지 못했습니다."
    if isinstance(exc, APIStatusError):
        if exc.status_code == 401:
            return "invalid_key", "API 키가 유효하지 않습니다."
        if exc.status_code == 429:
            body = str(getattr(exc, "body", "") or "")
            if "quota" in body or "billing" in body:
                return "quota_exceeded", "OpenAI 사용 한도를 초과했습니다."
            return "timeout", "요청이 몰려 처리하지 못했습니다. 잠시 후 다시 시도해 주세요."
    return "unknown", "오류가 발생했습니다."


@router.post("/chat")
async def chat(payload: ChatRequest, request: Request, x_openai_key: str = Header(default="")) -> StreamingResponse:
    if not x_openai_key:
        raise HTTPException(status_code=401, detail="API 키가 필요합니다.")
    if payload.client_id in _client_running:
        raise HTTPException(status_code=409, detail="이미 진행 중인 요청이 있습니다.")

    if payload.model not in MODEL_CATALOG:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 모델입니다: {payload.model}")

    settings = get_settings()
    kb = get_kb()
    pipelines = MODE_PIPELINES[payload.mode]
    if not kb.available and any(p in ("native", "agentic") for p in pipelines):
        if payload.mode == "all":
            pipelines = ["vanilla"]
        else:
            raise HTTPException(status_code=503, detail=f"지식베이스를 쓸 수 없습니다: {kb.reason}")

    request_id = f"req_{uuid.uuid4().hex[:12]}"
    memory = get_session_memory()
    conversation_id = payload.conversation_id or f"conv_{uuid.uuid4().hex[:12]}"
    turn = memory.begin_turn(conversation_id)
    cancel = asyncio.Event()
    _running[request_id] = cancel
    _client_running.add(payload.client_id)

    async def stream() -> AsyncIterator[bytes]:
        queue: asyncio.Queue[tuple[str, dict] | None] = asyncio.Queue()
        client = AsyncOpenAI(api_key=x_openai_key)
        records: dict[str, RunRecord] = {
            pipeline: RunRecord(
                run_id=f"{request_id}-{pipeline}",
                pipeline=pipeline,  # type: ignore[arg-type]
                model=payload.model,
            )
            for pipeline in pipelines
        }
        native_done = asyncio.Event()

        async def emit(event: str, data: dict) -> None:
            await queue.put((event, data))

        async def run_one(pipeline: str) -> None:
            record = records[pipeline]
            try:
                async with _sem():
                    if cancel.is_set():
                        raise asyncio.CancelledError
                    coro = (
                        run_vanilla(client, record, payload.question, emit)
                        if pipeline == "vanilla"
                        else run_native(client, kb, record, payload.question, emit)
                        if pipeline == "native"
                        else run_agentic(
                            client,
                            kb,
                            record,
                            payload.question,
                            emit,
                            thread_id=conversation_id,
                            tools=payload.tools,
                        )
                    )
                    await asyncio.wait_for(coro, timeout=settings.run_timeout_sec)

                # 논문 9.4절이 제안한 Native 우회. 기본값(paper)에서는 하지 않는다
                if (
                    pipeline == "agentic"
                    and record.outcome == "unverified"
                    and settings.critic_exhausted == "native_fallback"
                ):
                    await _apply_fallback(record, records, native_done, client, kb, payload.question, emit)

            except asyncio.CancelledError:
                record.outcome = "canceled"
                raise
            except Exception as exc:  # noqa: BLE001 - 오류 유형을 화면 문구로 옮긴다
                record.outcome = "failed"
                record.error_type, record.error_message = _error_type(exc)
                await queue.put(
                    (
                        "run_error",
                        {
                            "runId": record.run_id,
                            "pipeline": pipeline,
                            "errorType": record.error_type,
                            "message": record.error_message,
                        },
                    )
                )
            finally:
                if pipeline == "native":
                    native_done.set()
                if record.outcome != "failed":
                    record.no_info_flag = any(p in record.answer for p in NO_INFO_PATTERNS)
                    await queue.put(
                        (
                            "run_done",
                            {
                                "runId": record.run_id,
                                "pipeline": pipeline,
                                "outcome": record.outcome,
                                "answer": record.answer,
                                "metrics": record.metrics(),
                                "trace": record.trace(),
                            },
                        )
                    )
                await save_run(request_id, record)

        async def runner() -> None:
            await queue.put(
                (
                    "request_created",
                    {
                        "requestId": request_id,
                        "conversationId": conversation_id,
                        "turn": turn,
                        "tools": settings.tools_enabled if payload.tools is None else payload.tools,
                        "buildId": kb.build_id,
                        "model": payload.model,
                        "runs": [{"runId": r.run_id, "pipeline": r.pipeline} for r in records.values()],
                    },
                )
            )
            await save_conversation(
                conversation_id, payload.client_id, model=payload.model, turn_count=turn
            )
            await save_request(
                request_id,
                payload.mode,
                payload.question,
                payload.client_id,
                kb.build_id,
                conversation_id=conversation_id,
                turn_index=turn,
            )

            tasks = [asyncio.create_task(run_one(p)) for p in pipelines]
            watcher = asyncio.create_task(cancel.wait())
            done, pending = await asyncio.wait([*tasks, watcher], return_when=asyncio.FIRST_COMPLETED)

            if watcher in done:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            else:
                watcher.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

            status = _request_status(records, cancel.is_set())
            await queue.put(
                ("done", {"requestId": request_id, "conversationId": conversation_id, "status": status})
            )
            await save_request(
                request_id,
                payload.mode,
                payload.question,
                payload.client_id,
                kb.build_id,
                status,
                conversation_id=conversation_id,
                turn_index=turn,
            )
            await _save_dispute_snapshot(conversation_id, payload, records, turn)
            await queue.put(None)

        task = asyncio.create_task(runner())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                event, data = item
                yield _sse(event, data)
                if await request.is_disconnected():
                    cancel.set()
        finally:
            cancel.set()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            _running.pop(request_id, None)
            _client_running.discard(payload.client_id)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _apply_fallback(record, records, native_done, client, kb, question, emit) -> None:
    """전체 보기에서는 동시에 돌던 Native 결과를 재사용하고, 단일 모드에서는 새로 실행한다."""
    native = records.get("native")
    if native is not None:
        await native_done.wait()
        if native.outcome == "answered":
            record.answer = native.answer
            record.outcome = "fallback"
            record.fallback_used = True
            record.fallback_source_run_id = native.run_id
            return

    spare = RunRecord(run_id=f"{record.run_id}-fb", pipeline="native", model=record.model)
    await run_native(client, kb, spare, question, emit)
    record.answer = spare.answer
    record.outcome = "fallback"
    record.fallback_used = True
    record.tokens_in += spare.tokens_in
    record.tokens_out += spare.tokens_out
    record.extra_cost += spare.extra_cost


async def _save_dispute_snapshot(
    conversation_id: str, payload: ChatRequest, records: dict[str, RunRecord], turn: int
) -> None:
    """이번 턴에 추적된 분쟁 대상을 대화 기록에 남긴다 (로그 표시용)."""
    agentic = records.get("agentic")
    if agentic is None:
        return
    output = next((s.output for s in agentic.steps if s.node == "memory"), {})
    await save_conversation(
        conversation_id,
        payload.client_id,
        model=payload.model,
        turn_count=turn,
        product_name=output.get("productName"),
        dispute_type=output.get("disputeType"),
    )


def _request_status(records: dict[str, RunRecord], canceled: bool) -> str:
    outcomes = [r.outcome for r in records.values()]
    if canceled or "canceled" in outcomes:
        return "canceled"
    if all(o == "failed" for o in outcomes):
        return "failed"
    if any(o == "failed" for o in outcomes):
        return "partial"
    return "completed"


@router.post("/chat/conversation/{conversation_id}/end")
async def end_conversation(conversation_id: str) -> dict[str, bool]:
    """대화 종료. 세션 메모리를 버린다 (논문 9.3: 세션이 끝나면 맥락이 초기화된다)."""
    existed = get_session_memory().end(conversation_id)
    if existed:
        await save_conversation(conversation_id, closed=True)
    return {"closed": True}


@router.post("/chat/{request_id}/cancel")
async def cancel_run(request_id: str) -> dict[str, bool]:
    event = _running.get(request_id)
    if event is None:
        raise HTTPException(status_code=404, detail="진행 중인 요청이 아닙니다.")
    event.set()
    return {"canceled": True}
