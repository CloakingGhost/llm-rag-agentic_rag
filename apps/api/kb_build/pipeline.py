"""빌드 단계 실행기. CLI(cli/kb.py)가 호출한다."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from .chunks import law_chunks, table_chunks
from .models import BuildReport, Chunk, ParsedTable
from .paths import LAW_NAME, build_dir, file_hash, source_paths
from .rtf import read_rtf
from .tables import parse_appendix_pdf
from .validate import passed, validate_table


def new_build_id() -> str:
    return datetime.now(UTC).strftime("kb_%Y%m%d_%H%M")


def _write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def run_parse(build_id: str) -> tuple[list[ParsedTable], BuildReport]:
    """원문 → 표 구조 복원 → 자동 검증."""
    sources = source_paths()
    directory = build_dir(build_id)

    tables = [validate_table(table) for table in parse_appendix_pdf(sources["appendix"])]

    report = BuildReport(
        build_id=build_id,
        built_at=datetime.now(UTC).isoformat(timespec="seconds"),
        source_hashes={name: file_hash(path) for name, path in sources.items()},
        tables_total=len(tables),
        tables_rule_ok=sum(1 for t in tables if passed(t)),
        failures=[
            {"table_id": t.table_id, "title": t.title, "page": str(t.page_start), "why": "; ".join(t.failures)}
            for t in tables
            if not passed(t)
        ],
    )

    _write_json(directory / "tables.json", [t.model_dump() for t in tables])
    _write_json(directory / "parse_report.json", report.model_dump())
    return tables, report


def run_fix(build_id: str) -> tuple[list[ParsedTable], BuildReport, float]:
    """검증에 실패한 표만 LLM으로 보정한다 (운영자 키)."""
    from .llm_fix import fix_tables

    directory = build_dir(build_id)
    tables = [ParsedTable(**raw) for raw in json.loads((directory / "tables.json").read_text(encoding="utf-8"))]
    report = BuildReport(**json.loads((directory / "parse_report.json").read_text(encoding="utf-8")))

    fixed, cost, fixed_count = fix_tables(tables)

    report.tables_llm_fixed = fixed_count
    report.failures = [
        {"table_id": t.table_id, "title": t.title, "page": str(t.page_start), "why": "; ".join(t.failures)}
        for t in fixed
        if not passed(t)
    ]

    _write_json(directory / "tables.json", [t.model_dump() for t in fixed])
    _write_json(directory / "parse_report.json", report.model_dump())
    return fixed, report, cost


def run_chunk(build_id: str) -> tuple[list[Chunk], BuildReport]:
    """표와 법령 본문을 청크로 만든다."""
    directory = build_dir(build_id)
    sources = source_paths()

    tables = [ParsedTable(**raw) for raw in json.loads((directory / "tables.json").read_text(encoding="utf-8"))]
    report = BuildReport(**json.loads((directory / "parse_report.json").read_text(encoding="utf-8")))

    chunks: list[Chunk] = []
    chunks.extend(law_chunks(read_rtf(sources["law"]), LAW_NAME))
    chunks.extend(law_chunks(read_rtf(sources["standard"]), "소비자분쟁해결기준"))

    whole = 0
    for table in tables:
        if not passed(table):
            table.rows = []  # 검증 실패한 표는 통째로 한 청크 (근거를 버리지는 않는다)
            table.method = "whole"
            whole += 1
        chunks.extend(table_chunks(table))

    # 같은 조문 번호가 두 번 실린 경우(예: 제45조)가 있어 ID 유일성을 보장한다.
    seen: dict[str, int] = {}
    for chunk in chunks:
        count = seen.get(chunk.chunk_id, 0) + 1
        seen[chunk.chunk_id] = count
        if count > 1:
            chunk.chunk_id = f"{chunk.chunk_id}-{count}"

    report.tables_whole = whole
    report.chunk_counts = {
        "law": sum(1 for c in chunks if c.source == "law"),
        "standard": sum(1 for c in chunks if c.source == "standard"),
        "total": len(chunks),
    }

    with (directory / "chunks.jsonl").open("w", encoding="utf-8") as handle:
        for chunk in chunks:
            handle.write(json.dumps(chunk.model_dump(), ensure_ascii=False) + "\n")
    _write_json(directory / "parse_report.json", report.model_dump())

    return chunks, report


def load_chunks(build_id: str) -> list[Chunk]:
    path = build_dir(build_id) / "chunks.jsonl"
    return [Chunk(**json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line]
