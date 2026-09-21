"""Agentic RAG: LangGraph 순환 그래프 (논문 4.2절 최종 구성).

구조: 분쟁 대상 추출 → 의도 라우팅 → (거절) | 검색 → 생성 → Critic 검증
      검증 미달이면 1회차는 재검색, 2회차는 재생성. 3회를 넘기면 Native RAG로 폴백한다.
도구 호출과 가상 고객 DB는 논문 최종 평가에서 제외됐으므로 여기에도 없다.
"""

from __future__ import annotations

import json
import time
from typing import Annotated, Any, Literal

from langgraph.graph import END, START, StateGraph
from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from app.config import get_settings, sampling_args
from app.kb.retriever import hybrid_search
from app.kb.store import KnowledgeBase

from .base import Emit, RunRecord, format_references, load_prompts


class DisputeTarget(BaseModel):
    product_name: str | None = Field(default=None, description="분쟁 대상 상품·서비스명")
    dispute_type: str | None = Field(default=None, description="분쟁 유형 (청약철회/환불/교환/가격보상/기타)")


class IntentResult(BaseModel):
    route: Literal["policy_inquiry", "out_of_domain"]
    reason: str = Field(description="분류 이유 한 줄 설명")


class CriticResult(BaseModel):
    feedback: str = Field(description="부족한 점. 판정 전에 먼저 쓴다 (논문 4.4절 CoT)")
    is_grounded: bool
    is_relevant: bool
    is_complete: bool


class AgenticState(TypedDict, total=False):
    question: str
    dispute: DisputeTarget
    route: str
    answer: str
    feedback: str
    attempt: int
    critic_count: int
    action: Annotated[str, "다음 경로: pass | retry_retrieve | retry_generate | fallback"]


SCHEMAS = {
    "dispute": {
        "name": "dispute_target",
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["product_name", "dispute_type"],
            "properties": {
                "product_name": {"type": ["string", "null"]},
                "dispute_type": {"type": ["string", "null"]},
            },
        },
        "strict": True,
    },
    "intent": {
        "name": "intent_result",
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["route", "reason"],
            "properties": {
                "route": {"type": "string", "enum": ["policy_inquiry", "out_of_domain"]},
                "reason": {"type": "string"},
            },
        },
        "strict": True,
    },
    "critic": {
        "name": "critic_result",
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["feedback", "is_grounded", "is_relevant", "is_complete"],
            "properties": {
                "feedback": {"type": "string"},
                "is_grounded": {"type": "boolean"},
                "is_relevant": {"type": "boolean"},
                "is_complete": {"type": "boolean"},
            },
        },
        "strict": True,
    },
}


async def _structured(
    client: AsyncOpenAI, system: str, user: str, schema: dict, model: str
) -> tuple[dict, Any]:
    response = await client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        response_format={"type": "json_schema", "json_schema": schema},
        **sampling_args(model),
    )
    return json.loads(response.choices[0].message.content or "{}"), response.usage


def _cached(usage) -> int:
    return getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0) or 0


def build_agentic_graph(client: AsyncOpenAI, kb: KnowledgeBase, record: RunRecord, emit: Emit):
    settings = get_settings()
    prompts = load_prompts()["agentic"]

    async def step_event(node: str, attempt: int = 1) -> None:
        await emit("run_step", {"runId": record.run_id, "pipeline": "agentic", "node": node, "attempt": attempt})

    async def memory_node(state: AgenticState) -> AgenticState:
        started = time.monotonic()
        await step_event("memory")
        payload, usage = await _structured(
            client, prompts["memory_system"], state["question"], SCHEMAS["dispute"], record.model
        )
        dispute = DisputeTarget(**payload)
        record.add_step(
            "memory",
            started,
            tokens_in=usage.prompt_tokens,
            tokens_out=usage.completion_tokens,
            cached_in=_cached(usage),
            output={"productName": dispute.product_name, "disputeType": dispute.dispute_type},
        )
        return {"dispute": dispute, "attempt": 0}

    async def router_node(state: AgenticState) -> AgenticState:
        started = time.monotonic()
        await step_event("router")
        payload, usage = await _structured(
            client, prompts["router_system"], state["question"], SCHEMAS["intent"], record.model
        )
        intent = IntentResult(**payload)
        record.add_step(
            "router",
            started,
            tokens_in=usage.prompt_tokens,
            tokens_out=usage.completion_tokens,
            cached_in=_cached(usage),
            output={"route": intent.route, "reason": intent.reason},
        )
        record.route = intent.route
        return {"route": intent.route}

    async def reject_node(state: AgenticState) -> AgenticState:
        started = time.monotonic()
        await step_event("reject")
        record.add_step("reject", started)
        record.outcome = "rejected"
        return {"answer": prompts["reject_message"].strip()}

    async def retrieve_node(state: AgenticState) -> AgenticState:
        attempt = state.get("attempt", 0) + 1
        started = time.monotonic()
        await step_event("retrieve", attempt)

        question = state["question"]
        if state.get("feedback"):
            question = f"{question}\n(보완 요청: {state['feedback']})"

        dispute = state.get("dispute") or DisputeTarget()
        retrieval = await hybrid_search(client, kb, question, dispute.dispute_type, model=record.model)
        record.chunks = retrieval.chunks
        record.extra_cost += retrieval.cost_usd
        record.add_step(
            "retrieve",
            started,
            attempt=attempt,
            tokens_in=retrieval.tokens_in,
            tokens_out=retrieval.tokens_out,
            output={
                "query": retrieval.query,
                "vectorCount": retrieval.vector_count,
                "graphCount": retrieval.graph_count,
            },
        )
        return {"attempt": attempt}

    async def generate_node(state: AgenticState) -> AgenticState:
        # 재생성 경로에서는 검색 회차가 그대로이므로 검증 횟수를 기준으로 회차를 매긴다
        attempt = max(state.get("attempt", 1), state.get("critic_count", 0) + 1)
        started = time.monotonic()
        await step_event("generate", attempt)

        dispute = state.get("dispute") or DisputeTarget()
        feedback_block = (
            f"<critic_feedback>{state['feedback']}</critic_feedback>" if state.get("feedback") else ""
        )
        user = prompts["generate_user"].format(
            dispute_target=json.dumps(dispute.model_dump(), ensure_ascii=False),
            references=format_references(record.chunks),
            feedback_block=feedback_block,
            question=state["question"],
        )
        response = await client.chat.completions.create(
            model=record.model,
            messages=[
                {"role": "system", "content": prompts["generate_system"]},
                {"role": "user", "content": user},
            ],
        )
        usage = response.usage
        record.add_step(
            "generate",
            started,
            attempt=attempt,
            tokens_in=usage.prompt_tokens,
            tokens_out=usage.completion_tokens,
            cached_in=_cached(usage),
        )
        return {"answer": response.choices[0].message.content or ""}

    async def critic_node(state: AgenticState) -> AgenticState:
        # 검증 횟수는 따로 센다. 재생성 경로에서는 검색 회차가 늘지 않기 때문이다.
        attempt = state.get("critic_count", 0) + 1
        started = time.monotonic()
        await step_event("critic", attempt)

        user = prompts["critic_user"].format(
            question=state["question"],
            references=format_references(record.chunks),
            answer=state.get("answer", ""),
        )
        payload, usage = await _structured(client, prompts["critic_system"], user, SCHEMAS["critic"], record.model)
        critic = CriticResult(**payload)
        passed = critic.is_grounded and critic.is_relevant and critic.is_complete

        record.add_step(
            "critic",
            started,
            attempt=attempt,
            tokens_in=usage.prompt_tokens,
            tokens_out=usage.completion_tokens,
            cached_in=_cached(usage),
            output={
                "isGrounded": critic.is_grounded,
                "isRelevant": critic.is_relevant,
                "isComplete": critic.is_complete,
                "feedback": critic.feedback,
                "passed": passed,
            },
        )
        record.critic_attempts = attempt

        if passed:
            record.outcome = "answered"
            return {"action": "pass", "critic_count": attempt}
        if attempt >= settings.critic_max_attempts:
            return {"action": "fallback", "feedback": critic.feedback, "critic_count": attempt}
        # 1회차 실패는 재검색, 2회차 실패는 재생성 (논문 4.2.3)
        return {
            "action": "retry_retrieve" if attempt == 1 else "retry_generate",
            "feedback": critic.feedback,
            "critic_count": attempt,
        }

    graph = StateGraph(AgenticState)
    graph.add_node("memory", memory_node)
    graph.add_node("router", router_node)
    graph.add_node("reject", reject_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("generate", generate_node)
    graph.add_node("critic", critic_node)

    graph.add_edge(START, "memory")
    graph.add_edge("memory", "router")
    graph.add_conditional_edges(
        "router",
        lambda s: "reject" if s.get("route") == "out_of_domain" else "retrieve",
        {"reject": "reject", "retrieve": "retrieve"},
    )
    graph.add_edge("reject", END)
    graph.add_edge("retrieve", "generate")
    graph.add_edge("generate", "critic")
    graph.add_conditional_edges(
        "critic",
        lambda s: s.get("action", "pass"),
        {"pass": END, "retry_retrieve": "retrieve", "retry_generate": "generate", "fallback": END},
    )
    return graph.compile()


async def run_agentic(
    client: AsyncOpenAI, kb: KnowledgeBase, record: RunRecord, question: str, emit: Emit
) -> RunRecord:
    """Critic이 끝내 통과하지 못하면 호출자가 Native 결과로 폴백한다 (04 문서)."""
    compiled = build_agentic_graph(client, kb, record, emit)
    final = await compiled.ainvoke({"question": question})

    record.answer = final.get("answer", "")
    if record.outcome is None:
        record.outcome = "answered" if final.get("action") == "pass" else "fallback"
        record.fallback_used = record.outcome == "fallback"
    return record
