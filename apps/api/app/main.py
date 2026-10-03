"""FastAPI 진입점."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator
from prometheus_fastapi_instrumentator.metrics import default as default_metric

from app.api.chat import router as chat_router
from app.api.misc import router as misc_router
from app.config import get_settings
from app.db.session import create_all
from app.kb.store import get_kb
from app.observability import init_langfuse, shutdown_langfuse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s :: %(message)s")
log = logging.getLogger("cdq")


@asynccontextmanager
async def lifespan(app: FastAPI):
    kb = get_kb()
    if kb.available:
        log.info(
            "지식베이스 %s 로드: 청크 %s개, 그래프 노드 %s개",
            kb.build_id,
            kb.chunk_count,
            kb.graph.number_of_nodes(),
        )
    else:
        # RAG·Agentic만 막고 Vanilla·대시보드·로그는 계속 쓸 수 있게 한다 (04_system_design.md)
        log.warning("지식베이스를 쓸 수 없습니다: %s", kb.reason)

    try:
        await create_all()
    except Exception as exc:  # noqa: BLE001 - DB가 없어도 서버는 떠야 한다
        log.warning("DB 준비 실패(로그 기록이 비활성화됩니다): %s", exc)

    init_langfuse()

    yield

    shutdown_langfuse()


app = FastAPI(title="소비자 분쟁 Q&A API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(chat_router)
app.include_router(misc_router)

# /metrics: HTTP 요청수·지연 히스토그램을 자동으로 만든다. 파이프라인 단위 지표는
# app/metrics.py에 따로 둔다(한 요청이 파이프라인을 최대 3개 동시에 돌려서 HTTP 단위로는 안 잡힘).
# 기본 버킷(0.1/0.5/1초)은 챗봇 응답(수 초~수십 초)엔 너무 촘촘해서 거의 다 +Inf로 몰린다 —
# /api/chat 지연 범위(최대 RUN_TIMEOUT_SEC=60초)에 맞춰 다시 잡는다
_instrumentator = Instrumentator()
_instrumentator.add(default_metric(latency_lowr_buckets=(0.5, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 120)))
_instrumentator.instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
