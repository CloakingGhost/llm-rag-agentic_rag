"""Agentic RAG: LangGraph 순환 그래프 (논문 4.2절 최종 구성).

구조: 분쟁 대상 추출 → 의도 라우팅 → (거절) | (도구 실행) | 검색 → 생성 → Critic 검증
      검증 미달이면 1회차는 재검색, 2회차는 재생성. 3회를 넘기면 논문 9.2절대로 복구 없이
      마지막 답변을 검증 미통과(unverified) 상태로 내보낸다.

도구 호출(논문 3.5 Lightweight ReAct)과 가상 고객 DB는 **기본으로 꺼져 있다**.
논문 4.1절이 최종 평가에서 그 모듈을 비활성화했기 때문이다. `TOOLS_ENABLED=true`로 켜면
라우터가 3분기(논문 부록 2-B)가 되고 `system_action` 경로에 ReAct 노드가 붙는다.

대화 ID를 주면 `MemorySaver` 체크포인터를 붙여 분쟁 대상을 다음 턴으로 넘긴다 (논문 3.4).
"""

from __future__ import annotations

import json
import time
from typing import Annotated, Any, Literal

from langgraph.graph import END, START, StateGraph
from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from app.config import get_settings, sampling_args, tool_sampling_args
from app.kb.retriever import hybrid_search
from app.kb.store import KnowledgeBase
from app.tools.registry import TOOL_SPECS, dispatch

from .base import Emit, RunRecord, format_references, load_prompts
from .session_memory import get_session_memory


class DisputeTarget(BaseModel):
    """논문 부록 2-A: 주문번호, 상품명 등 분쟁 대상을 추적한다."""

    product_name: str | None = Field(default=None, description="분쟁 대상 상품·서비스명")
    dispute_type: str | None = Field(default=None, description="분쟁 유형 (청약철회/환불/교환/가격보상/기타)")
    order_id: str | None = Field(default=None, description="주문번호. 질문에 있을 때만. 도구를 켰을 때 쓰인다")


class IntentResult(BaseModel):
    route: Literal["policy_inquiry", "system_action", "out_of_domain"]
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
    account: str  # 도구가 조회한 고객·주문 정보 (논문 부록 2-C)
    answer: str
    feedback: str
    attempt: int
    critic_count: int
    action: Annotated[str, "다음 경로: pass | retry_retrieve | retry_generate | exhausted"]


SCHEMAS = {
    "dispute": {
        "name": "dispute_target",
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["product_name", "dispute_type", "order_id"],
            "properties": {
                "product_name": {"type": ["string", "null"]},
                "dispute_type": {"type": ["string", "null"]},
                "order_id": {"type": ["string", "null"]},
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


def _intent_schema(tools: bool) -> dict:
    """도구를 껐으면 `system_action`을 아예 고를 수 없게 한다 (논문 4.1의 최종 구성)."""
    schema = json.loads(json.dumps(SCHEMAS["intent"]))
    if tools:
        schema["schema"]["properties"]["route"]["enum"] = [
            "policy_inquiry",
            "system_action",
            "out_of_domain",
        ]
    return schema


def _format_account(calls: list[dict]) -> str:
    """도구 관찰 결과를 생성 노드가 읽을 블록으로 만든다 (논문 부록 2-C의 '주입된 고객 정보')."""
    if not calls:
        return "<account_data>(도구를 호출하지 않았습니다)</account_data>"
    lines = ["<account_data>"]
    for call in calls:
        lines.append(f"[{call['tool']}] 인자: {call['arguments']}")
        lines.append(f"결과: {json.dumps(call['result'], ensure_ascii=False)}")
    lines.append("</account_data>")
    return chr(10).join(lines)


async def _structured(client: AsyncOpenAI, system: str, user: str, schema: dict, model: str) -> tuple[dict, Any]:
    """메모리·라우터·Critic이 쓰는 판정용 호출. 깊은 사고가 필요 없는 분류·추출 작업이라
    `effort="none"`을 준다. 실측상 GPT-5.6 계열은 이 값이 없으면 짧은 판정에도 기본값(가장
    높은 추론 강도로 보임)이 켜져 호출 하나당 약 2배 느려진다."""
    response = await client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        response_format={"type": "json_schema", "json_schema": schema},
        **sampling_args(model, effort="none"),
    )
    return json.loads(response.choices[0].message.content or "{}"), response.usage


def _cached(usage) -> int:
    return getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0) or 0


def build_agentic_graph(
    client: AsyncOpenAI,
    kb: KnowledgeBase,
    record: RunRecord,
    emit: Emit,
    checkpointer=None,
    tools: bool | None = None,
):
    settings = get_settings()
    prompts = load_prompts()["agentic"]
    use_tools = settings.tools_enabled if tools is None else tools

    async def step_event(node: str, attempt: int = 1) -> None:
        await emit("run_step", {"runId": record.run_id, "pipeline": "agentic", "node": node, "attempt": attempt})

    async def memory_node(state: AgenticState) -> AgenticState:
        started = time.monotonic()
        await step_event("memory")

        # 체크포인터가 붙어 있으면 지난 턴의 분쟁 대상이 그대로 들어온다
        prior = state.get("dispute")
        if isinstance(prior, dict):
            prior = DisputeTarget(**prior)
        user = prompts["memory_user"].format(
            previous=json.dumps(prior.model_dump() if prior else {}, ensure_ascii=False),
            question=state["question"],
        )
        payload, usage = await _structured(client, prompts["memory_system"], user, SCHEMAS["dispute"], record.model)
        fresh = DisputeTarget(**payload)
        # 논문 3.4의 '핵심 속성만 갱신': 이번 턴에 안 나온 속성은 지난 값을 지킨다
        dispute = DisputeTarget(
            product_name=fresh.product_name or (prior.product_name if prior else None),
            dispute_type=fresh.dispute_type or (prior.dispute_type if prior else None),
            order_id=fresh.order_id or (prior.order_id if prior else None),
        )
        # 이번 질문에 없는데 지난 턴 값이 그대로 남은 항목이 있으면 '이어받음'으로 본다
        carried = bool(prior) and any(
            value and value == getattr(prior, field) and value not in state["question"]
            for field in ("product_name", "dispute_type", "order_id")
            if (value := getattr(dispute, field))
        )
        record.add_step(
            "memory",
            started,
            tokens_in=usage.prompt_tokens,
            tokens_out=usage.completion_tokens,
            cached_in=_cached(usage),
            output={
                "productName": dispute.product_name,
                "disputeType": dispute.dispute_type,
                "orderId": dispute.order_id,
                "carriedOver": carried,
            },
        )
        return {"dispute": dispute, "attempt": 0}

    async def router_node(state: AgenticState) -> AgenticState:
        started = time.monotonic()
        await step_event("router")
        payload, usage = await _structured(
            client,
            prompts["router_system_tools"] if use_tools else prompts["router_system"],
            state["question"],
            _intent_schema(use_tools),
            record.model,
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

    async def act_node(state: AgenticState) -> AgenticState:
        """Lightweight ReAct (논문 3.5). 생각 → 도구 호출 → 관찰을 정해진 횟수만 돈다.

        트리 탐색은 없다. 도구 목록과 인자 스키마가 좁고, 스키마를 어기면 오류가
        관찰 결과로 돌아가 모델이 스스로 고친다.
        """
        started = time.monotonic()
        await step_event("act")

        dispute = state.get("dispute") or DisputeTarget()
        messages: list[dict] = [
            {"role": "system", "content": prompts["act_system"]},
            {
                "role": "user",
                "content": prompts["act_user"].format(
                    dispute_target=json.dumps(dispute.model_dump(), ensure_ascii=False),
                    question=state["question"],
                ),
            },
        ]

        calls: list[dict] = []
        tokens_in = tokens_out = cached = 0
        for _ in range(settings.tool_max_steps):
            response = await client.chat.completions.create(
                model=record.model,
                messages=messages,
                tools=TOOL_SPECS,
                tool_choice="auto",
                **tool_sampling_args(record.model),
            )
            usage = response.usage
            tokens_in += usage.prompt_tokens
            tokens_out += usage.completion_tokens
            cached += _cached(usage)

            message = response.choices[0].message
            if not message.tool_calls:
                break

            messages.append(
                {
                    "role": "assistant",
                    "content": message.content or "",
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {"name": call.function.name, "arguments": call.function.arguments},
                        }
                        for call in message.tool_calls
                    ],
                }
            )
            for call in message.tool_calls:
                result = dispatch(call.function.name, call.function.arguments)
                calls.append(
                    {
                        "tool": call.function.name,
                        "arguments": call.function.arguments,
                        "error": result.get("error"),
                        "result": result,
                    }
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

        record.add_step(
            "act",
            started,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cached_in=cached,
            output={
                "calls": calls,
                "toolCount": len(calls),
                "errorCount": sum(1 for c in calls if c["error"]),
            },
        )
        record.tool_calls = len(calls)
        return {"account": _format_account(calls), "attempt": 0}

    async def retrieve_node(state: AgenticState) -> AgenticState:
        attempt = state.get("attempt", 0) + 1
        started = time.monotonic()
        await step_event("retrieve", attempt)

        question = state["question"]
        if state.get("feedback"):
            question = f"{question}\n(보완 요청: {state['feedback']})"

        dispute = state.get("dispute") or DisputeTarget()
        retrieval = await hybrid_search(
            client,
            kb,
            question,
            dispute.dispute_type,
            model=record.model,
            product_name=dispute.product_name,
        )
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
            priced=False,
        )
        return {"attempt": attempt}

    async def generate_node(state: AgenticState) -> AgenticState:
        # 재생성 경로에서는 검색 회차가 그대로이므로 검증 횟수를 기준으로 회차를 매긴다
        attempt = max(state.get("attempt", 1), state.get("critic_count", 0) + 1)
        started = time.monotonic()
        await step_event("generate", attempt)

        dispute = state.get("dispute") or DisputeTarget()
        feedback_block = f"<critic_feedback>{state['feedback']}</critic_feedback>" if state.get("feedback") else ""
        account_block = state.get("account", "") if use_tools else ""
        user = prompts["generate_user"].format(
            dispute_target=json.dumps(dispute.model_dump(), ensure_ascii=False),
            account_block=account_block,
            references=format_references(record.chunks),
            feedback_block=feedback_block,
            question=state["question"],
        )
        system = prompts["generate_system_tools"] if use_tools else prompts["generate_system"]
        # effort는 건드리지 않는다 — 실제 사용자 답변을 쓰는 자리라 추론 강도를 낮추면
        # 품질에 직접 영향을 준다. sampling_args()만 빠져 있던 것을 바로잡는다(4o의 temperature=0
        # 이 여태 적용 안 되고 있었다)
        response = await client.chat.completions.create(
            model=record.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            **sampling_args(record.model),
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

        evidence = format_references(record.chunks)
        if use_tools and state.get("account"):
            evidence = state["account"] + chr(10) * 2 + evidence
        user = prompts["critic_user"].format(
            question=state["question"],
            references=evidence,
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
            # 논문 9.2: 재시도를 넘기면 복구 경로 없이 그대로 끝난다
            return {"action": "exhausted", "feedback": critic.feedback, "critic_count": attempt}
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

    if use_tools:
        graph.add_node("act", act_node)

    graph.add_edge(START, "memory")
    graph.add_edge("memory", "router")

    def _branch(state: AgenticState) -> str:
        route = state.get("route")
        if route == "out_of_domain":
            return "reject"
        # system_action은 검색을 거치지 않고 도구 결과만으로 답한다 (논문 부록 C의 정책 경로와 분리)
        if use_tools and route == "system_action":
            return "act"
        return "retrieve"

    targets = {"reject": "reject", "retrieve": "retrieve"}
    if use_tools:
        targets["act"] = "act"
    graph.add_conditional_edges("router", _branch, targets)
    graph.add_edge("reject", END)
    graph.add_edge("retrieve", "generate")
    if use_tools:
        graph.add_edge("act", "generate")
    graph.add_edge("generate", "critic")
    graph.add_conditional_edges(
        "critic",
        lambda s: s.get("action", "pass"),
        {"pass": END, "retry_retrieve": "retrieve", "retry_generate": "generate", "exhausted": END},
    )
    return graph.compile(checkpointer=checkpointer)


async def run_agentic(
    client: AsyncOpenAI,
    kb: KnowledgeBase,
    record: RunRecord,
    question: str,
    emit: Emit,
    thread_id: str | None = None,
    tools: bool | None = None,
) -> RunRecord:
    """Critic이 끝내 통과하지 못하면 호출자가 Native 결과로 폴백한다 (04 문서).

    `thread_id`(대화 ID)를 주면 같은 대화의 지난 상태를 이어받는다.
    `tools`를 주면 서버 기본값 대신 그 값으로 도구 사용 여부를 정한다 (재현 실험용).
    """
    memory = get_session_memory()
    compiled = build_agentic_graph(
        client, kb, record, emit, checkpointer=memory.saver if thread_id else None, tools=tools
    )
    # 분쟁 대상만 이어받고 나머지 순환 상태는 턴마다 초기화한다
    turn_state: AgenticState = {
        "question": question,
        "answer": "",
        "feedback": "",
        "attempt": 0,
        "critic_count": 0,
        "action": "",
        "account": "",
    }
    config = memory.config(thread_id) if thread_id else None
    final = await compiled.ainvoke(turn_state, config=config)

    record.answer = final.get("answer", "")
    if record.outcome is None:
        # 논문 9.2절은 재시도를 넘겼을 때의 복구 경로를 두지 않았다.
        # 마지막 생성 답변을 검증 미통과(unverified) 상태로 그대로 내보낸다.
        # Native RAG 우회는 논문 9.4절의 향후 과제이며 CRITIC_EXHAUSTED로 켠다 (호출자가 처리)
        record.outcome = "answered" if final.get("action") == "pass" else "unverified"
    return record
