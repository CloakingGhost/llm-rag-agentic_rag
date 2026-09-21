"""사내 API 도구 목록 (논문 3.5절 Lightweight ReAct).

ToolLLM의 복잡한 트리 탐색을 빼고, **호출할 수 있는 도구 목록과 인자 스키마를 좁게 제한**한다.
스키마를 벗어난 호출은 실행하지 않고 오류를 돌려주어 모델이 스스로 고치게 한다.

도구는 기본으로 꺼져 있다. 논문 4.1절이 최종 평가에서 이 모듈을 비활성화했기 때문이다.
`TOOLS_ENABLED=true`로 켜면 논문 9.2절의 '도구 편향'을 재현할 수 있다.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.tools import mock_store


class OrderIdArgs(BaseModel):
    model_config = {"extra": "forbid"}
    order_id: str = Field(pattern=r"^ORD-\d{8}-\d{4}$", description="주문번호. 예: ORD-20260901-0001")


class RefundArgs(BaseModel):
    model_config = {"extra": "forbid"}
    order_id: str = Field(pattern=r"^ORD-\d{8}-\d{4}$")
    reason: str = Field(min_length=2, max_length=200)


class ListOrdersArgs(BaseModel):
    model_config = {"extra": "forbid"}
    limit: int = Field(default=10, ge=1, le=20)


class NoArgs(BaseModel):
    model_config = {"extra": "forbid"}


ORDER_ID_SCHEMA = {
    "type": "string",
    "pattern": r"^ORD-\d{8}-\d{4}$",
    "description": "주문번호. 예: ORD-20260901-0001",
}


def _fn(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


TOOL_SPECS: list[dict] = [
    _fn("get_customer_profile", "로그인한 고객의 프로필(이름·등급·가입일)을 조회한다.", {}, []),
    _fn(
        "list_orders",
        "고객의 최근 주문 목록을 조회한다. 주문번호를 모를 때 먼저 부른다.",
        {"limit": {"type": "integer", "minimum": 1, "maximum": 20, "description": "최대 건수"}},
        [],
    ),
    _fn(
        "get_order",
        "주문번호로 주문 1건의 상세(상품명·금액·배송일·상태)를 조회한다.",
        {"order_id": ORDER_ID_SCHEMA},
        ["order_id"],
    ),
    _fn(
        "get_refund_status",
        "주문번호로 환불 접수 상태를 조회한다.",
        {"order_id": ORDER_ID_SCHEMA},
        ["order_id"],
    ),
    _fn(
        "create_refund_request",
        "주문에 대한 환불을 접수한다. 고객이 명시적으로 환불 접수를 요청했을 때만 부른다.",
        {"order_id": ORDER_ID_SCHEMA, "reason": {"type": "string", "description": "환불 사유"}},
        ["order_id", "reason"],
    ),
]

TOOL_NAMES = [spec["function"]["name"] for spec in TOOL_SPECS]

_HANDLERS: dict[str, tuple[type[BaseModel], Callable[..., dict]]] = {
    "get_customer_profile": (NoArgs, lambda: mock_store.get_customer_profile()),
    "list_orders": (ListOrdersArgs, lambda limit=10: mock_store.list_orders(limit)),
    "get_order": (OrderIdArgs, lambda order_id: mock_store.get_order(order_id)),
    "get_refund_status": (OrderIdArgs, lambda order_id: mock_store.get_refund_status(order_id)),
    "create_refund_request": (
        RefundArgs,
        lambda order_id, reason: mock_store.create_refund_request(order_id, reason),
    ),
}


def dispatch(name: str, raw_arguments: str) -> dict[str, Any]:
    """도구 1개를 실행한다. 스키마 위반은 실행하지 않고 오류를 돌려준다."""
    handler = _HANDLERS.get(name)
    if handler is None:
        return {
            "error": "unknown_tool",
            "message": f"{name}은(는) 없는 도구입니다. 허용된 도구: {', '.join(TOOL_NAMES)}",
        }

    model, run = handler
    try:
        payload = json.loads(raw_arguments or "{}")
    except json.JSONDecodeError as exc:
        return {"error": "invalid_json", "message": f"인자가 JSON이 아닙니다: {exc}"}

    try:
        args = model(**payload)
    except ValidationError as exc:
        # 논문 3.5: 스키마를 벗어나면 예외를 돌려주어 모델이 스스로 교정하게 한다
        detail = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
        return {"error": "schema_error", "message": f"인자가 스키마에 맞지 않습니다. {detail}"}

    return run(**args.model_dump())
