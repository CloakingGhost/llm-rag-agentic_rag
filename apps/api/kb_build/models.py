"""빌드 산출물 스키마. 화면(apps/web/src/lib/types.ts)의 검색 결과 표와 대응한다."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Source = Literal["law", "standard"]


class TableRow(BaseModel):
    """별표Ⅱ 표의 행 하나 (분쟁유형 경로 → 해결기준)."""

    dispute_path: list[str] = Field(description="1) → ① → - 순서의 분쟁유형 계층")
    resolution: str = Field(description="해결기준 원문")


class ParsedTable(BaseModel):
    table_id: str
    appendix: str = Field(description="별표Ⅰ~Ⅳ")
    page_start: int
    page_end: int
    title: str = Field(description="업종·품목 제목")
    kind: Literal["dispute", "grid", "aux"] = Field(
        default="dispute",
        description="dispute=분쟁유형 표, grid=별표Ⅰ·Ⅲ·Ⅳ의 행 구분선 표, aux=요율표 등 부속 표",
    )
    columns: list[str] = Field(default=[], description="grid 표의 헤더")
    method: Literal["rule", "llm", "whole"] = "rule"
    rows: list[TableRow] = []
    notes: list[str] = Field(default=[], description="표에 붙은 비고")
    raw_text: str = Field(default="", description="검증과 보정에 쓰는 표 영역 원문")
    raw_columns: dict[str, str] = Field(default={}, description="열별 원문 (3열 표만)")
    validations: dict[str, bool] = {}
    failures: list[str] = Field(default=[], description="통과 못 하면 LLM 보정 대상")
    warnings: list[str] = Field(default=[], description="내용은 보존됐지만 행 분리가 모호한 경우")


class Chunk(BaseModel):
    chunk_id: str
    source: Source
    path: str = Field(description="화면에 보여줄 경로. 예: 전자상거래법 > 제17조(청약철회등) > 제3항")
    text: str = Field(description="화면에 보여줄 원문")
    embed_text: str = Field(description="임베딩에 넣는 텍스트 (경로·비고 포함)")
    page: int | None = None
    table_id: str | None = None
    meta: dict[str, str] = {}


class BuildReport(BaseModel):
    build_id: str
    built_at: str
    source_hashes: dict[str, str]
    tables_total: int = 0
    tables_rule_ok: int = 0
    tables_llm_fixed: int = 0
    tables_whole: int = 0
    chunk_counts: dict[str, int] = {}
    failures: list[dict[str, str]] = []
