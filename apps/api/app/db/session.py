"""DB 연결. Neon 풀링 주소를 쓰므로 asyncpg 준비문 캐시를 끈다 (04_system_design.md)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import inspect
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


# 이미 만들어진 표에 뒤늦게 추가된 열. create_all은 열을 더해 주지 않는다.
# 배포 전에 Alembic으로 옮긴다 (docs/improvement_actions.md IMP-17)
LATE_COLUMNS: dict[str, dict[str, str]] = {
    "requests": {"conversation_id": "VARCHAR(40)", "turn_index": "INTEGER DEFAULT 1"},
}


def _add_late_columns(conn) -> None:
    inspector = inspect(conn)
    tables = set(inspector.get_table_names())
    for table, columns in LATE_COLUMNS.items():
        if table not in tables:
            continue
        present = {c["name"] for c in inspector.get_columns(table)}
        for name, ddl in columns.items():
            if name not in present:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


async def create_all() -> None:
    """MVP 단계에서는 마이그레이션 대신 테이블을 만든다. 배포 전에 Alembic으로 옮긴다."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_late_columns)
