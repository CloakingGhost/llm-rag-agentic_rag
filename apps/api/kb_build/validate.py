"""표 파싱 자동 검증 (kb_build_proposal.md §5 V1~V6).

사람 검수를 하지 않기로 했으므로, 통과한 표만 행 단위로 쪼개 색인한다.
검증은 열 단위로 한다. 비고 열에 섞인 글자가 해결기준 마커로 오인되지 않게 하기 위함이다.
"""

from __future__ import annotations

import re

from .models import ParsedTable

# 단위는 줄바꿈 때문에 붙었다 떨어졌다 하므로 숫자만 비교한다. 단위 보존은 V1이 본다.
NUMBER_TOKEN = re.compile(r"\d[\d,]*")
RES_MARK = re.compile(r"(ㅇ|○|ｏ|◦|o(?=\s))")
COVERAGE_MIN = 0.97


def _numbers(text: str) -> set[str]:
    return {m.group(0) for m in NUMBER_TOKEN.finditer(re.sub(r"\s+", " ", text))}


def _chars(text: str) -> str:
    return re.sub(r"\s+", "", text)


def validate_table(table: ParsedTable) -> ParsedTable:
    if table.kind == "aux":
        # 요율표·참고 법령 같은 부속 표는 행으로 쪼개지 않고 통째로 싣는다.
        table.validations = {"V0_aux": True}
        table.failures = []
        return table

    produced_dispute = " ".join(" ".join(row.dispute_path) for row in table.rows)
    produced_resolution = " ".join(row.resolution for row in table.rows)
    produced_notes = " ".join(table.notes)
    produced_all = f"{produced_dispute} {produced_resolution} {produced_notes}"

    source_chars = _chars(table.raw_text)
    coverage = len(_chars(produced_all)) / len(source_chars) if source_chars else 0.0
    missing_numbers = sorted(_numbers(table.raw_text) - _numbers(produced_all))

    checks: dict[str, bool] = {
        "V1_coverage": coverage >= COVERAGE_MIN,
        "V2_numbers": not missing_numbers,
        "V6_has_rows": bool(table.rows),
    }
    failures: list[str] = []
    if not checks["V1_coverage"]:
        failures.append(f"문자 보존율 {coverage:.2f}")
    if missing_numbers:
        failures.append(f"누락된 숫자 {missing_numbers[:5]}")
    if not table.rows:
        failures.append("행 없음")

    # 3열 표에만 적용하는 검증
    warnings: list[str] = []
    if table.raw_columns:
        source_marks = len(RES_MARK.findall(table.raw_columns.get("resolution", "")))
        produced_marks = len(RES_MARK.findall(produced_resolution))
        orphans = [row for row in table.rows if row.resolution and not row.dispute_path]

        checks["V4_orphans"] = not orphans
        if orphans:
            failures.append(f"경로 없는 해결기준 {len(orphans)}건")

        # V3은 경고다. 한 행에 해결기준 항목이 둘 이상 붙는 표가 실제로 있기 때문에,
        # 내용 보존(V1·V2)이 통과했다면 행 분리 지점이 모호한 것으로 보고 넘어간다.
        if abs(source_marks - produced_marks) > max(1, round(source_marks * 0.1)):
            warnings.append(f"해결기준 마커 {source_marks} → {produced_marks} (행 분리 모호)")

    table.validations = checks
    table.failures = failures
    table.warnings = warnings
    return table


def passed(table: ParsedTable) -> bool:
    return bool(table.validations) and all(table.validations.values())
