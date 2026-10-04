"""가짜 OpenAI API — 부하테스트에서 OpenAI 비용을 0으로 만든다 (docs/11_deployment_test_env_plan.md).

앱은 코드 변경 없이 환경변수 `OPENAI_BASE_URL=http://<스텁>/v1` 만 바꾸면 이 서버를 OpenAI로 본다.
호출 종류별 실측 지연(2026-10-04 k6·LangFuse·DB steps 기준)만큼 기다렸다가 앱이 기대하는 모양의 응답을 돌려준다.
그래서 **서버가 하는 일(검색·직렬화·SSE·DB·트레이싱)은 실제와 같고 비용만 0**이다.

재지 못하는 것: OpenAI 레이트리밋(TPM/RPM), LLM 지연의 실제 편차. 이건 이미 실측해 둔 값을 쓴다.

    uv run --directory apps/api python ../../load/stub/llm_stub.py            # 로컬 8199
    STUB_LATENCY_SCALE=0 ...        # 지연 없이 즉시 응답 (서버 순수 처리량 측정)

환경변수
    STUB_LATENCY_SCALE  지연 배율 (기본 1.0)
    STUB_CRITIC_PASS    Critic이 한 번에 통과할 확률 (기본 0.22 — k6 148건 실측 분포와 맞춘다)
    PORT                기본 8199
표준 라이브러리 + fastapi + uvicorn 만 쓴다 (Cloud Run용 이미지를 가볍게 만들려고).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import random
import struct
import time
from collections import Counter

from fastapi import FastAPI, Request

SCALE = float(os.environ.get("STUB_LATENCY_SCALE", "1.0"))
CRITIC_PASS = float(os.environ.get("STUB_CRITIC_PASS", "0.22"))
EMBED_DIM = 3072  # text-embedding-3-large

# (평균 지연 초, 완료 토큰 수) — 실측: memory 1.0 / router 1.1 / 엔티티 1.2 / 리랭킹 약 1.5 / critic 3.9 / generate 4.9
# native generate는 native 평균 3.46s에서 임베딩을 뺀 값, vanilla는 k6 중앙값 약 12s(reasoning medium 포함)
KINDS = {
    "memory": (0.95, 28),
    "router": (1.1, 46),
    "entities": (1.2, 40),
    "rerank": (1.5, 205),
    "critic": (3.9, 260),
    "generate_agentic": (4.9, 365),
    "generate_native": (3.2, 230),
    "generate_vanilla": (12.0, 743),
    "embedding": (0.25, 0),
}

ANSWER = (
    "문서에 따르면 구입 후 7일 이내에 발견한 하자는 제품 교환 또는 환불을 요청할 수 있습니다. "
    "판매처에 하자 내용을 알리고 증빙 사진과 함께 교환 또는 환불을 신청하세요. "
    "판매자가 응하지 않으면 한국소비자원에 분쟁조정을 신청할 수 있습니다. "
)

app = FastAPI(title="가짜 OpenAI", docs_url=None, redoc_url=None)
calls: Counter[str] = Counter()
started = time.time()


def _wait(kind: str) -> float:
    mean, _ = KINDS[kind]
    return max(0.0, random.gauss(mean, 0.2 * mean)) * SCALE


def _text(messages: list[dict]) -> str:
    return " ".join(str(m.get("content", "")) for m in messages)


def _kind_for_text(messages: list[dict]) -> str:
    system = next((str(m.get("content", "")) for m in messages if m.get("role") == "system"), "")
    if "[참조]" in system:
        return "generate_native"
    if "쇼핑몰 CS" in system:
        return "generate_agentic"
    return "generate_vanilla"


def _structured(name: str, messages: list[dict]) -> tuple[str, str, dict]:
    """json_schema 이름으로 호출 종류를 알아내고 그 스키마에 맞는 응답을 만든다."""
    if name == "dispute_target":
        return "memory", "", {"product_name": "노트북", "dispute_type": "환불", "order_id": None}
    if name == "intent_result":
        return "router", "", {"route": "policy_inquiry", "reason": "상품 하자에 따른 환불·교환 문의"}
    if name == "entities":
        return "entities", "", {"entities": ["노트북", "전자제품", "환불", "교환", "청약철회", "하자"]}
    if name == "rerank":
        # user 메시지의 documents[].id 를 뒤집어 돌려준다 (리랭킹이 실제로 순서를 바꾸는 것처럼)
        try:
            docs = json.loads(messages[-1]["content"])["documents"]
            return "rerank", "", {"ranking": [d["id"] for d in reversed(docs)]}
        except Exception:  # noqa: BLE001
            return "rerank", "", {"ranking": []}
    if name == "critic_result":
        ok = random.random() < CRITIC_PASS
        return (
            "critic",
            "",
            {
                "feedback": "답변이 검색 문서의 내용에 근거하며 질문에 맞는지 확인했다.",
                "is_grounded": ok,
                "is_relevant": True,
                "is_complete": ok,
            },
        )
    return "generate_agentic", "", {}


def _chat_body(model: str, content: str, prompt_tokens: int, completion_tokens: int) -> dict:
    return {
        "id": "chatcmpl-stub",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "prompt_tokens_details": {"cached_tokens": 0},
            "completion_tokens_details": {"reasoning_tokens": 0},
        },
    }


@app.post("/v1/chat/completions")
async def chat(request: Request) -> dict:
    body = await request.json()
    messages = body.get("messages", [])
    prompt_tokens = max(1, len(_text(messages)) // 2)  # 한글은 대략 글자 2개에 토큰 1개
    schema = (body.get("response_format") or {}).get("json_schema")
    if schema:
        kind, _, payload = _structured(schema.get("name", ""), messages)
        content = json.dumps(payload, ensure_ascii=False)
        completion_tokens = KINDS[kind][1]
    else:
        kind = _kind_for_text(messages)
        content = ANSWER * (8 if kind == "generate_vanilla" else 7)  # 실제 답변 약 900~1100자
        completion_tokens = KINDS[kind][1]
    calls[kind] += 1
    await asyncio.sleep(_wait(kind))
    return _chat_body(body.get("model", "stub"), content, prompt_tokens, completion_tokens)


@app.post("/v1/embeddings")
async def embeddings(request: Request) -> dict:
    body = await request.json()
    text = body.get("input", "")
    text = text if isinstance(text, str) else " ".join(map(str, text))
    # 같은 입력은 같은 벡터. 값 자체는 의미 없지만 Chroma가 하는 일(거리 계산)은 실제와 같다
    rng = random.Random(int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big"))
    vector = [rng.uniform(-1.0, 1.0) for _ in range(EMBED_DIM)]
    norm = sum(v * v for v in vector) ** 0.5
    vector = [v / norm for v in vector]
    calls["embedding"] += 1
    await asyncio.sleep(_wait("embedding"))
    # openai SDK는 encoding_format을 안 주면 base64를 요청하고 그대로 디코딩한다
    data = (
        base64.b64encode(struct.pack(f"<{EMBED_DIM}f", *vector)).decode()
        if body.get("encoding_format") == "base64"
        else vector
    )
    tokens = max(1, len(text) // 2)
    return {
        "object": "list",
        "data": [{"object": "embedding", "index": 0, "embedding": data}],
        "model": body.get("model", "stub"),
        "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
    }


@app.get("/stub/stats")
async def stats() -> dict:
    return {
        "uptime_sec": round(time.time() - started),
        "latency_scale": SCALE,
        "critic_pass": CRITIC_PASS,
        "calls": dict(calls),
    }


@app.post("/stub/reset")
async def reset() -> dict:
    calls.clear()
    return {"ok": True}


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8199")), log_level="warning")
