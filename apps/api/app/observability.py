"""LangFuse 트레이싱 (docs/10_observability_load_test_plan.md 3단계).

켜면: OpenAI 호출 하나하나가 generation으로, 파이프라인 한 번이 trace 하나로 묶인다.
끄면(키 없음 또는 LANGFUSE_ENABLED=false): 이 모듈의 모든 함수가 아무것도 하지 않아 기존
동작과 완전히 같다. `langfuse.openai`는 import하는 순간 openai SDK 전체를 패치하므로 꺼진
상태에선 아예 import하지 않는다 — 켠/끈 상태를 k6로 비교할 때 끈 쪽이 깨끗해야 한다.

SDK는 트레이스를 백그라운드 스레드에서 배치로 보낸다. 요청 경로에서 네트워크를 기다리지 않는다.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from openai import AsyncOpenAI

from app.config import get_settings

if TYPE_CHECKING:
    from app.pipelines.base import RunRecord

log = logging.getLogger("cdq.observability")

_langfuse: Any = None


def init_langfuse() -> None:
    global _langfuse
    settings = get_settings()
    if not settings.langfuse_active:
        log.info("LangFuse 꺼짐 (키 없음 또는 LANGFUSE_ENABLED=false)")
        return
    from langfuse import Langfuse

    _langfuse = Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        base_url=settings.langfuse_base_url,
    )
    log.info("LangFuse 켜짐: %s", settings.langfuse_base_url)


def shutdown_langfuse() -> None:
    if _langfuse is not None:
        _langfuse.shutdown()


def make_openai_client(api_key: str) -> AsyncOpenAI:
    if _langfuse is None:
        return AsyncOpenAI(api_key=api_key)
    from langfuse.openai import AsyncOpenAI as TracedAsyncOpenAI

    return TracedAsyncOpenAI(api_key=api_key)


def lf(name: str) -> dict[str, str]:
    """`client.chat.completions.create(..., **lf("critic"))`처럼 호출에 이름을 붙인다.

    LangFuse 래퍼만 받는 인자라서, 꺼져 있을 땐 일반 openai 클라이언트가 TypeError를 내지 않도록 비운다.
    """
    return {"name": name} if _langfuse is not None else {}


class TraceHandle:
    def __init__(self, span: Any | None) -> None:
        self._span = span

    def finish(self, record: RunRecord) -> None:
        if self._span is None:
            return
        failed = record.outcome == "failed"
        metrics = record.metrics()
        self._span.update(
            output=record.answer,
            level="ERROR" if failed else "DEFAULT",
            status_message=record.error_message if failed else None,
            metadata={
                "outcome": record.outcome,
                "critic_attempts": record.critic_attempts,
                "fallback_used": record.fallback_used,
                "latency_ms": metrics["latencyMs"],
                "tokens_in": metrics["tokensIn"],
                "tokens_out": metrics["tokensOut"],
                "cost_usd": metrics["costUsd"],
            },
        )
        self._span.set_trace_io(output=record.answer)


@contextlib.contextmanager
def pipeline_trace(
    *, pipeline: str, request_id: str, conversation_id: str, client_id: str, model: str, question: str
) -> Iterator[TraceHandle]:
    """파이프라인 한 번(vanilla/native/agentic 중 하나)을 trace 하나로 묶는다.

    session=대화 ID, user=익명 clientId. BYOK 키는 어디에도 싣지 않는다.
    """
    if _langfuse is None:
        yield TraceHandle(None)
        return

    from langfuse import propagate_attributes

    with propagate_attributes(
        trace_name=f"chat:{pipeline}",
        session_id=conversation_id,
        user_id=client_id,
        tags=[pipeline, model],
        metadata={"request_id": request_id},
    ):
        with _langfuse.start_as_current_observation(
            name=f"pipeline:{pipeline}", as_type="span", input=question
        ) as span:
            span.set_trace_io(input=question)
            yield TraceHandle(span)
