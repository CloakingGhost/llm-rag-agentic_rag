"""운영 DB 스키마 (02_business_data_analysis.md 2-2).

대화 → 요청 → 실행 → 단계 → 검색 결과. 로그는 무기한 보관한다.
사용자 API 키는 어디에도 저장하지 않는다.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


class Conversation(Base):
    """대화 세션 (논문 3.4 Stateful Memory Bank).

    여기 적히는 분쟁 대상은 화면·로그에 보여 주기 위한 스냅샷이다.
    다음 턴의 추론에 실제로 쓰이는 상태는 프로세스 메모리의 `MemorySaver`에 있고,
    서버가 내려가면 함께 사라진다 (논문 9.3의 '세션 종료 시 초기화').
    """

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    last_turn_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    turn_count: Mapped[int] = mapped_column(Integer, default=0)
    product_name: Mapped[str | None] = mapped_column(String(120))
    dispute_type: Mapped[str | None] = mapped_column(String(60))
    model: Mapped[str] = mapped_column(String(40), default="")
    closed: Mapped[bool] = mapped_column(Boolean, default=False)


class Request(Base):
    __tablename__ = "requests"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    mode: Mapped[str] = mapped_column(String(16))
    # 대화 ID와 턴 번호. 같은 대화의 질문은 같은 conversation_id를 갖는다
    conversation_id: Mapped[str | None] = mapped_column(String(40), index=True)
    turn_index: Mapped[int] = mapped_column(Integer, default=1)
    question: Mapped[str] = mapped_column(Text)
    client_id: Mapped[str] = mapped_column(String(64), index=True)
    build_id: Mapped[str] = mapped_column(String(40), default="")
    status: Mapped[str] = mapped_column(String(16), default="running")

    runs: Mapped[list[Run]] = relationship(back_populates="request", cascade="all, delete-orphan")


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    request_id: Mapped[str] = mapped_column(ForeignKey("requests.id", ondelete="CASCADE"), index=True)
    pipeline: Mapped[str] = mapped_column(String(16), index=True)
    outcome: Mapped[str | None] = mapped_column(String(16), index=True)
    answer: Mapped[str] = mapped_column(Text, default="")
    route: Mapped[str | None] = mapped_column(String(24))
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False)
    fallback_source_run_id: Mapped[str | None] = mapped_column(String(48))
    critic_attempts: Mapped[int] = mapped_column(Integer, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    no_info_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    error_type: Mapped[str | None] = mapped_column(String(24))
    error_message: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cached_in: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    price_version: Mapped[str] = mapped_column(String(40), default="")
    model: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)

    request: Mapped[Request] = relationship(back_populates="runs")
    steps: Mapped[list[Step]] = relationship(back_populates="run", cascade="all, delete-orphan")
    chunks: Mapped[list[RetrievedChunkRow]] = relationship(back_populates="run", cascade="all, delete-orphan")


class Step(Base):
    __tablename__ = "steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    node: Mapped[str] = mapped_column(String(24), index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cached_in: Mapped[int] = mapped_column(Integer, default=0)
    output: Mapped[dict] = mapped_column(JSON, default=dict)

    run: Mapped[Run] = relationship(back_populates="steps")


class RetrievedChunkRow(Base):
    """검색 결과는 원문 스냅샷으로 저장한다. 지식베이스를 다시 만들어도 로그가 당시 근거를 보여준다."""

    __tablename__ = "retrieved_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    chunk_id: Mapped[str] = mapped_column(String(64))
    path: Mapped[str] = mapped_column(Text)
    text_snapshot: Mapped[str] = mapped_column(Text)
    vector_score: Mapped[float | None] = mapped_column(Float)
    graph_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    rrf_score: Mapped[float] = mapped_column(Float, default=0.0)
    rerank_rank: Mapped[int] = mapped_column(Integer, default=0)
    selected: Mapped[bool] = mapped_column(Boolean, default=False)

    run: Mapped[Run] = relationship(back_populates="chunks")


Index("ix_runs_pipeline_created", Run.pipeline, Run.created_at)
