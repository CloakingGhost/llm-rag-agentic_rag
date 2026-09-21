"""FastAPI 진입점."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.chat import router as chat_router
from app.api.misc import router as misc_router
from app.config import get_settings
from app.db.session import create_all
from app.kb.store import get_kb

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

    yield


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
