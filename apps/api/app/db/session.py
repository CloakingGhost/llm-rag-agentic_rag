"""DB 연결. Neon 풀링 주소를 쓰므로 asyncpg 준비문 캐시를 끈다 (04_system_design.md)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

from .models import Base

_engine = None
_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    global _engine, _factory
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            pool_pre_ping=True,
            connect_args={"statement_cache_size": 0} if "asyncpg" in settings.database_url else {},
        )
        _factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    get_engine()
    assert _factory is not None
    async with _factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def create_all() -> None:
    """MVP 단계에서는 마이그레이션 대신 테이블을 만든다. 배포 전에 Alembic으로 옮긴다."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
