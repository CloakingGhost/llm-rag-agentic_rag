"""국가법령정보센터가 내려주는 .doc(실제로는 RTF) 파일에서 본문 텍스트를 뽑는다.

표준 RTF 라이브러리는 \\binN 으로 끼워 넣은 이미지에서 넘어지므로 직접 훑는다.
"""

from __future__ import annotations

import re
from pathlib import Path

BS = 0x5C  # 역슬래시
DEST_SKIP = {
    b"fonttbl",
    b"colortbl",
    b"stylesheet",
    b"info",
    b"pict",
    b"fldinst",
    b"*",
    b"header",
    b"footer",
    b"object",
}
CTRL = re.compile(rb"\x5c([a-z]+)(-?\d+)? ?")


def rtf_to_text(data: bytes) -> str:
    i, n, out = 0, len(data), []
    skip_uc = 1
    depth = 0
    skipdepth: int | None = None

    while i < n:
        b = data[i]

        if b == 0x7B:  # {
            depth += 1
            i += 1
            m = CTRL.match(data, i)
            if skipdepth is None and ((m and m.group(1) in DEST_SKIP) or data[i : i + 2] == b"\x5c*"):
                skipdepth = depth
            continue

        if b == 0x7D:  # }
            if skipdepth == depth:
                skipdepth = None
            depth -= 1
            i += 1
            continue

        if b == BS:
            m = CTRL.match(data, i)
            if m:
                word, arg = m.group(1), m.group(2)
                i = m.end()
                if word == b"bin":
                    i += int(arg or 0)
                    continue
                if skipdepth is not None:
                    continue
                if word == b"u":
                    value = int(arg or 0)
                    if value < 0:
                        value += 65536
                    out.append(chr(value))
                    remaining = skip_uc
                    while remaining and i < n:
                        if data[i] == BS and data[i + 1 : i + 2] == b"'":
                            i += 4
                        else:
                            i += 1
                        remaining -= 1
                elif word in (b"par", b"line", b"row"):
                    out.append("\n")
                elif word == b"cell":
                    out.append(" | ")
                elif word == b"tab":
                    out.append("\t")
                elif word == b"uc":
                    skip_uc = int(arg or 1)
                continue

            nxt = data[i + 1 : i + 2]
            if nxt == b"'":
                if skipdepth is None:
                    out.append(bytes([int(data[i + 2 : i + 4], 16)]).decode("cp1252", "replace"))
                i += 4
                continue
            if skipdepth is None and nxt in (b"\x5c", b"{", b"}"):
                out.append(nxt.decode())
            i += 2
            continue

        if skipdepth is None and b not in (0x0D, 0x0A):
            out.append(chr(b))
        i += 1

    text = "".join(out)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text)


def read_rtf(path: Path) -> str:
    return rtf_to_text(path.read_bytes())
