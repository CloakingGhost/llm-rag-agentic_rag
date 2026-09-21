"""검증에 실패한 표만 LLM으로 행 대응을 다시 맞춘다 (kb_build_proposal.md §3 C안).

입력은 이미 열이 분리된 텍스트다. 모델은 새 내용을 만들 필요가 없고 행 짝만 맞추면 된다.
결과는 다시 자동 검증을 거치고, 또 실패하면 표 전체를 한 청크로 둔다.
"""

from __future__ import annotations

import json

from openai import OpenAI

from app.config import cost_usd, get_settings

from .models import ParsedTable, TableRow
from .validate import passed, validate_table

SYSTEM = """너는 대한민국 소비자분쟁해결기준 별표의 표를 구조화하는 도구다.
표는 '분쟁유형 | 해결기준 | 비고' 3열이고, 열별 텍스트를 따로 준다.
규칙:
1. 주어진 텍스트에 있는 문장만 사용한다. 새 문장을 만들거나 요약하지 않는다.
2. 숫자, 기간, 비율, 단위를 절대 바꾸지 않는다. 하나도 빠뜨리지 않는다.
3. 열을 절대 섞지 않는다. dispute_path에는 분쟁유형 열 텍스트만, resolution에는 해결기준 열 텍스트만 넣는다.
4. 각 열의 텍스트는 PDF 폭 때문에 중간에서 줄바꿈된다. 마커(1), ①, -, ㆍ, ㅇ, o)로 시작하지 않는 줄은
   앞 줄의 이어진 부분이므로 붙여서 한 문장으로 만든다.
5. 분쟁유형의 계층(1) → ① → - → ㆍ)을 dispute_path 배열로 표현한다.
6. 해결기준 항목(ㅇ, o)이 어느 분쟁유형에 붙는지 판단해 행을 만든다.
7. 비고는 표 전체에 붙는 설명이므로 notes 배열에 그대로 둔다."""

SCHEMA = {
    "name": "parsed_table",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["rows", "notes"],
        "properties": {
            "rows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["dispute_path", "resolution"],
                    "properties": {
                        "dispute_path": {"type": "array", "items": {"type": "string"}},
                        "resolution": {"type": "string"},
                    },
                },
            },
            "notes": {"type": "array", "items": {"type": "string"}},
        },
    },
    "strict": True,
}


def fix_table(table: ParsedTable, client: OpenAI) -> tuple[ParsedTable, float]:
    """표 하나를 보정하고 (보정된 표, 비용)을 돌려준다."""
    settings = get_settings()
    columns = table.raw_columns or {"dispute": table.raw_text, "resolution": "", "notes": ""}

    user = json.dumps(
        {
            "title": table.title,
            "dispute_column": columns.get("dispute", ""),
            "resolution_column": columns.get("resolution", ""),
            "notes_column": columns.get("notes", ""),
        },
        ensure_ascii=False,
    )

    response = client.chat.completions.create(
        model=settings.chat_model,
        messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
        response_format={"type": "json_schema", "json_schema": SCHEMA},
        temperature=0,
    )

    usage = response.usage
    cached = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0) or 0
    spent = cost_usd(usage.prompt_tokens, usage.completion_tokens, cached, settings)

    payload = json.loads(response.choices[0].message.content or "{}")
    fixed = table.model_copy(deep=True)
    fixed.rows = [
        TableRow(dispute_path=[p for p in row.get("dispute_path", []) if p], resolution=row.get("resolution", ""))
        for row in payload.get("rows", [])
    ]
    fixed.notes = [n for n in payload.get("notes", []) if n]
    fixed.method = "llm"

    fixed = validate_table(fixed)
    # 보정본이 더 낫지 않으면 원본을 유지한다.
    if not passed(fixed) and len(fixed.failures) >= len(table.failures):
        table.method = "whole"
        return table, spent
    return fixed, spent


def fix_tables(tables: list[ParsedTable]) -> tuple[list[ParsedTable], float, int]:
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY가 없습니다. apps/api/.env를 확인하세요.")

    client = OpenAI(api_key=settings.openai_api_key)
    total_cost = 0.0
    fixed_count = 0
    result: list[ParsedTable] = []

    for table in tables:
        if passed(table) or table.kind != "dispute":
            result.append(table)
            continue
        fixed, spent = fix_table(table, client)
        total_cost += spent
        if passed(fixed):
            fixed_count += 1
        result.append(fixed)

    return result, total_cost, fixed_count
