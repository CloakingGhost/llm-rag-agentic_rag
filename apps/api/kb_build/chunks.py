"""청크 생성.

화면에 보여줄 텍스트와 임베딩에 넣을 텍스트를 분리한다 (data_feasibility.md §2).
별표Ⅱ 해결기준은 중앙값 17자로 매우 짧아서, 경로와 비고를 합쳐야 검색이 동작한다.
"""

from __future__ import annotations

import re

from .models import Chunk, ParsedTable

CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
ARTICLE = re.compile(r"^제(\d+)조(?:의(\d+))?\(([^)]*)\)", re.M)
NOTES_LIMIT = 600


def law_chunks(text: str, law_name: str) -> list[Chunk]:
    """법령 본문을 조 → 항 단위로 쪼갠다."""
    matches = list(ARTICLE.finditer(text))
    chunks: list[Chunk] = []

    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.start() : end].strip()
        article_no = match.group(1) + (f"의{match.group(2)}" if match.group(2) else "")
        head = f"제{article_no}조({match.group(3)})"

        positions = [(m.start(), CIRCLED.index(m.group(0)) + 1) for m in re.finditer(f"[{CIRCLED}]", body)]
        if not positions:
            chunks.append(
                Chunk(
                    chunk_id=f"law-{article_no}",
                    source="law",
                    path=f"{law_name} > {head}",
                    text=body,
                    embed_text=f"{law_name} {head}. {body}",
                    meta={"article": article_no, "article_title": match.group(3)},
                )
            )
            continue

        for pos_index, (start, hang_no) in enumerate(positions):
            stop = positions[pos_index + 1][0] if pos_index + 1 < len(positions) else len(body)
            hang_text = body[start:stop].strip()
            path = f"{law_name} > {head} > 제{hang_no}항"
            chunks.append(
                Chunk(
                    chunk_id=f"law-{article_no}-{hang_no}",
                    source="law",
                    path=path,
                    text=hang_text,
                    embed_text=f"{path.replace(' > ', ' ')}. {hang_text}",
                    meta={"article": article_no, "article_title": match.group(3), "hang": str(hang_no)},
                )
            )

    return chunks


def grid_chunks(table: ParsedTable) -> list[Chunk]:
    """별표Ⅰ·Ⅲ·Ⅳ처럼 행 구분선이 있는 표: 행 하나가 청크 하나."""
    base_path = f"소비자분쟁해결기준 {table.appendix}"
    chunks: list[Chunk] = []

    for row_index, row in enumerate(table.rows):
        cells = [c for c in row.dispute_path if c]
        if not cells:
            continue
        # 별표Ⅰ은 첫 열이 '번호'라서 두 번째 열(업종)을 제목으로 쓴다
        label = cells[1] if table.columns[:1] == ["번호"] and len(cells) > 1 else cells[0]
        body = " / ".join(
            f"{table.columns[i]}: {cells[i]}" if i < len(table.columns) else cells[i]
            for i in range(1, len(cells))
        )
        path = f"{base_path} > {label}"
        chunks.append(
            Chunk(
                chunk_id=f"{table.table_id}-g{row_index}",
                source="standard",
                path=path,
                text=body or label,
                embed_text=f"{path.replace(' > ', ' ')}. {body}",
                page=table.page_start,
                table_id=table.table_id,
                meta={"appendix": table.appendix, "title": table.title, "method": table.method},
            )
        )
    return chunks


def table_chunks(table: ParsedTable) -> list[Chunk]:
    """검증을 통과한 표는 행 단위로, 실패한 표는 통째로 한 청크."""
    if table.kind == "grid" and table.rows:
        return grid_chunks(table)

    notes = " ".join(table.notes)[:NOTES_LIMIT]
    base_path = f"소비자분쟁해결기준 {table.appendix} > {table.title}"

    if not table.rows:
        return [
            Chunk(
                chunk_id=f"{table.table_id}-whole",
                source="standard",
                path=base_path,
                text=table.raw_text,
                embed_text=f"{base_path}. {table.raw_text}",
                page=table.page_start,
                table_id=table.table_id,
                meta={"appendix": table.appendix, "method": table.method},
            )
        ]

    chunks: list[Chunk] = []
    for row_index, row in enumerate(table.rows):
        dispute = " > ".join(row.dispute_path)
        path = f"{base_path} > {dispute}" if dispute else base_path
        display = row.resolution
        embed_parts = [path.replace(" > ", " "), f"해결기준: {row.resolution}"]
        if notes:
            embed_parts.append(f"비고: {notes}")

        chunks.append(
            Chunk(
                chunk_id=f"{table.table_id}-r{row_index}",
                source="standard",
                path=path,
                text=display,
                embed_text=". ".join(embed_parts),
                page=table.page_start,
                table_id=table.table_id,
                meta={
                    "appendix": table.appendix,
                    "title": table.title,
                    "dispute": dispute,
                    "notes": notes,
                    "method": table.method,
                },
            )
        )
    return chunks
