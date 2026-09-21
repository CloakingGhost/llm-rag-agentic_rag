"""별표 PDF의 표를 구조를 보존하며 읽는다 (kb_build_proposal.md §3 C안의 규칙 단계).

별표Ⅱ의 표는 열 구분선만 있고 행 구분선이 없다. 그래서 단어 좌표로 줄을 만들고,
분쟁유형 열의 계층 마커(1) → ① → - → ㆍ)로 행을 복원한다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pdfplumber

from .models import ParsedTable, TableRow

TYPE_MARK = re.compile(r"^(\d+\)|[①-⑳]|-\s|ㆍ|·\s)")
RES_MARK = re.compile(r"^(ㅇ|o\s|ｏ|○)")
APPENDIX_MARK = re.compile(r"<별표\s*([ⅠⅡⅢⅣ])>")
ROMAN = {"Ⅰ": "별표Ⅰ", "Ⅱ": "별표Ⅱ", "Ⅲ": "별표Ⅲ", "Ⅳ": "별표Ⅳ"}
CONT_SUFFIX = re.compile(r"\s*\(\s*\d+\s*[-–]\s*\d+\s*\)\s*$")


def _lines(page, bbox, tol: float = 2.5):
    words = page.crop(bbox).extract_words(keep_blank_chars=False, use_text_flow=False)
    words.sort(key=lambda w: (round(w["top"]), w["x0"]))
    lines: list[dict] = []
    for w in words:
        if lines and abs(lines[-1]["top"] - w["top"]) <= tol:
            lines[-1]["words"].append(w)
        else:
            lines.append({"top": w["top"], "words": [w]})
    return lines


def is_dispute_table(table) -> bool:
    """머리글이 '분쟁유형 | 해결기준 | 비고'인 표만 분쟁유형 표로 본다."""
    for cells in table.extract()[:3]:
        text = re.sub(r"\s+", "", " ".join(str(c) for c in cells if c))
        if "분쟁유형" in text and "해결기준" in text:
            return True
    return False


# '(27)건축자재(위생기구)'는 제목이지만 '(파손, 작동불량)'은 앞 표의 이어진 줄이다.
HEADING_REJECT = re.compile(r"^(\d+\)|[①-⑳]?\s*\d+\)|-|ㆍ|ㅇ|o\s|\*|※|等|등\)|\((?!\d+\)))")


def _is_heading(text: str) -> bool:
    """표 위의 품목 제목만 고른다. 앞 표의 마지막 줄이 제목으로 잡히지 않게 한다."""
    cleaned = text.strip()
    if len(cleaned) < 3 or "법제처" in cleaned or cleaned.startswith("분 쟁"):
        return False
    if HEADING_REJECT.match(cleaned):
        return False
    # 해결기준 항목이 섞인 줄은 제목이 아니다
    return not re.search(r"(ㅇ|○|\so\s)", cleaned)


def title_inside_table(table) -> str:
    """품목 제목이 표 테두리 안 첫 줄에 들어 있는 경우가 많다 (예: '⑤자 동 차 (2-1)')."""
    grid = table.extract()
    for cells in grid[:3]:
        filled = [c for c in cells if c]
        if len(filled) != 1:
            continue
        text = str(filled[0]).replace("\n", " ").strip()
        if _is_heading(text):
            return text
    return ""


def table_title(page, table) -> str:
    """표 바로 위에 있는 품목 제목을 찾는다. 한 쪽에 표가 여러 개인 경우가 있다."""
    inside = title_inside_table(table)
    if inside:
        return inside

    top = table.bbox[1]
    candidates = [
        line
        for line in page.extract_text_lines()
        if line["top"] < top - 1 and _is_heading(line["text"])
    ]
    if candidates:
        # 품목 제목(①, (27) 등)이 묶음 제목(5. 공산품)보다 구체적이므로 우선한다.
        specific = [c for c in candidates if not re.match(r"^\d+\.\s", c["text"].strip())]
        chosen = specific[-1] if specific else candidates[-1]
        return chosen["text"].strip().strip("<>").strip()
    return page_title(page)


def page_title(page) -> str:
    for line in (page.extract_text() or "").split("\n"):
        cleaned = line.strip().strip("<>").strip()
        if _is_heading(cleaned):
            return cleaned
    return ""


def normalize_title(title: str) -> str:
    """'⑤자 동 차 (2 - 1)' 처럼 쪽이 나뉜 표 제목에서 연속 표시를 떼어 낸다."""
    return CONT_SUFFIX.sub("", title).strip()


def parse_three_column_table(page, table) -> tuple[list[TableRow], list[str], str, dict[str, str]]:
    """분쟁유형 | 해결기준 | 비고 표에서 행과 비고를 복원한다."""
    header = next((r for r in table.rows if len([c for c in r.cells if c]) == 3), None)
    if header is None:
        return [], [], "", {}

    # 줄 끝 글자가 표 테두리를 살짝 넘어가는 경우가 있어 오른쪽을 조금 넓혀 자른다.
    right = min(page.width, table.bbox[2] + 6)
    xs = [c[0] for c in header.cells] + [right]
    bbox = (table.bbox[0], header.bbox[3], right, table.bbox[3])
    raw = page.crop(bbox).extract_text() or ""
    columns = {
        name: page.crop((xs[i], bbox[1], xs[i + 1], bbox[3])).extract_text() or ""
        for i, name in enumerate(("dispute", "resolution", "notes"))
    }

    rows: list[TableRow] = []
    notes: list[str] = []
    stack: dict[int, str] = {}

    for line in _lines(page, bbox):
        cols = ["", "", ""]
        for w in line["words"]:
            idx = max(i for i in range(3) if w["x0"] >= xs[i] - 1)
            cols[idx] += (" " if cols[idx] else "") + w["text"]
        dispute, resolution, note = cols

        if dispute:
            if TYPE_MARK.match(dispute):
                depth = 0 if re.match(r"^\d+\)", dispute) else 1 if re.match(r"^[①-⑳]", dispute) else 2
                stack[depth] = dispute
                for deeper in [d for d in list(stack) if d > depth]:
                    stack.pop(deeper)
                rows.append(TableRow(dispute_path=[stack[d] for d in sorted(stack)], resolution=""))
            elif rows and rows[-1].dispute_path:
                # 분쟁유형이 여러 줄로 이어지는 경우. 해결기준이 이미 붙었더라도 이어 붙인다.
                rows[-1].dispute_path[-1] += " " + dispute
                if stack:
                    stack[max(stack)] = rows[-1].dispute_path[-1]
            else:
                rows.append(TableRow(dispute_path=[dispute], resolution=""))

        if resolution:
            if not rows:
                rows.append(TableRow(dispute_path=[], resolution=""))
            joiner = " " if rows[-1].resolution else ""
            rows[-1].resolution += joiner + resolution

        if note:
            if note.startswith("*") or not notes:
                notes.append(note)
            else:
                notes[-1] += " " + note

    return [r for r in rows if r.resolution or r.dispute_path], notes, raw, columns


def parse_simple_table(page, table) -> tuple[list[list[str]], str]:
    """행 구분선이 있는 표(별표Ⅰ·Ⅲ·Ⅳ). 세로 병합 셀은 위 값으로 채운다."""
    data = table.extract()
    filled: list[list[str]] = []
    previous: list[str] = []
    for row in data:
        cells = [(c or "").replace("\n", " ").strip() for c in row]
        merged = [cells[i] or (previous[i] if i < len(previous) else "") for i in range(len(cells))]
        filled.append(merged)
        previous = merged
    raw = page.crop(table.bbox).extract_text() or ""
    return filled, raw


def parse_appendix_pdf(pdf_path: Path) -> list[ParsedTable]:
    tables: list[ParsedTable] = []
    current_appendix = "별표Ⅰ"

    with pdfplumber.open(pdf_path) as pdf:
        for page_index, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            marker = APPENDIX_MARK.search(text)
            if marker and page_index > 1:
                current_appendix = ROMAN[marker.group(1)]

            for table_index, table in enumerate(page.find_tables()):
                table_id = f"{current_appendix}-p{page_index}-{table_index}"
                title = normalize_title(table_title(page, table))

                if is_dispute_table(table):
                    rows, notes, raw, columns = parse_three_column_table(page, table)
                    tables.append(
                        ParsedTable(
                            table_id=table_id,
                            appendix=current_appendix,
                            page_start=page_index,
                            page_end=page_index,
                            title=title,
                            kind="dispute",
                            rows=rows,
                            notes=notes,
                            raw_text=raw,
                            raw_columns=columns,
                        )
                    )
                    continue

                grid, raw = parse_simple_table(page, table)
                header = grid[0] if grid else []
                is_grid = current_appendix in ("별표Ⅰ", "별표Ⅲ", "별표Ⅳ") and len(header) >= 2
                tables.append(
                    ParsedTable(
                        table_id=table_id,
                        appendix=current_appendix,
                        page_start=page_index,
                        page_end=page_index,
                        title=title or (header[0] if header else ""),
                        kind="grid" if is_grid else "aux",
                        columns=header if is_grid else [],
                        rows=[TableRow(dispute_path=cells, resolution="") for cells in grid[1:] if any(cells)]
                        if is_grid
                        else [],
                        raw_text=raw,
                    )
                )

    return merge_continued_tables(tables)


def merge_continued_tables(tables: list[ParsedTable]) -> list[ParsedTable]:
    """'(2-1)', '(2-2)'처럼 쪽이 나뉜 같은 제목의 표를 하나로 합친다 (검증 V5)."""
    merged: list[ParsedTable] = []
    for table in tables:
        previous = merged[-1] if merged else None
        # 같은 쪽 안의 다른 표는 다른 품목이다. 쪽을 넘어간 같은 제목만 이어 붙인다.
        same = (
            previous is not None
            and previous.appendix == "별표Ⅱ" == table.appendix
            and previous.title == table.title
            and 0 < table.page_start - previous.page_end <= 1
        )
        if same and previous is not None:
            previous.rows.extend(table.rows)
            previous.notes.extend(table.notes)
            previous.raw_text += "\n" + table.raw_text
            for key, value in table.raw_columns.items():
                previous.raw_columns[key] = previous.raw_columns.get(key, "") + "\n" + value
            previous.page_end = table.page_end
            continue
        merged.append(table)
    return merged
