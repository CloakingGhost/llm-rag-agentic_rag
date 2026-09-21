"""지식베이스 로더. 실행 중에는 읽기만 한다 (04_system_design.md).

기동 시 매니페스트를 확인하고, 문제가 있으면 RAG·Agentic만 비활성한다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import chromadb
import networkx as nx
import yaml

from app.config import get_settings
from kb_build.paths import ARTIFACTS_DIR

COLLECTION = "kb_chunks"


@dataclass
class KnowledgeBase:
    build_id: str
    chunk_count: int
    collection: object | None = None
    graph: nx.Graph = field(default_factory=nx.Graph)
    node_chunks: dict[str, list[str]] = field(default_factory=dict)
    # 별표Ⅰ 대상품목 매핑: '노트북' 같은 일상어 → '전자제품, 사무용기기' 업종
    item_index: dict[str, str] = field(default_factory=dict)
    titles: list[str] = field(default_factory=list)
    status: str = "ok"
    reason: str = ""

    @property
    def available(self) -> bool:
        return self.status == "ok"

    def matching_titles(self, question: str) -> list[str]:
        """질문에서 품목이 특정되면 그 품목 표의 제목을 돌려준다.

        별표Ⅱ는 품목마다 문구가 거의 같아서, 품목을 좁히지 않으면
        '자전거'와 '전자제품'의 하자 조항이 구분되지 않는다.
        """
        tokens: list[str] = []
        for keyword, group in self.item_index.items():
            if keyword in question:
                tokens.extend([keyword, *group.split()])

        hits: list[str] = []
        for title in self.titles:
            plain = title.replace(" ", "")
            for token in tokens:
                if len(token) >= 2 and token.replace(" ", "") in plain and title not in hits:
                    hits.append(title)
        return hits[:5]

    def expand_query(self, question: str) -> list[str]:
        """질문에 나온 품목 이름을 별표Ⅰ 업종·품종으로 넓힌다.

        별표Ⅱ는 '① 전자제품, 사무용기기'처럼 묶음 이름을 쓰기 때문에
        '노트북'이라는 말만으로는 해당 표를 찾지 못한다.
        """
        found: list[str] = []
        for keyword, group in self.item_index.items():
            if keyword in question and group not in found:
                found.append(group)
        return found[:3]


def _latest_build() -> Path | None:
    builds = sorted(p for p in ARTIFACTS_DIR.glob("kb_*") if p.is_dir())
    return builds[-1] if builds else None


def load_kb() -> KnowledgeBase:
    settings = get_settings()
    directory = (
        ARTIFACTS_DIR / settings.__dict__.get("kb_build_id", "") if settings.__dict__.get("kb_build_id") else None
    ) or _latest_build()

    if directory is None or not directory.exists():
        return KnowledgeBase(build_id="", chunk_count=0, status="unavailable", reason="빌드 산출물이 없습니다")

    try:
        chroma = chromadb.PersistentClient(path=str(directory / "chroma"))
        collection = chroma.get_collection(COLLECTION)
        chunk_count = collection.count()
    except Exception as exc:  # 색인이 없거나 깨진 경우
        return KnowledgeBase(
            build_id=directory.name, chunk_count=0, status="unavailable", reason=f"벡터 색인 오류: {exc}"
        )

    graph = nx.Graph()
    node_chunks: dict[str, list[str]] = {}
    graph_file = directory / "graph.json"
    if graph_file.exists():
        payload = json.loads(graph_file.read_text(encoding="utf-8"))
        for node in payload.get("nodes", []):
            graph.add_node(node["name"], community=node.get("community", -1))
            node_chunks[node["name"]] = node.get("chunks", [])
        for edge in payload.get("edges", []):
            graph.add_edge(edge["source"], edge["target"], relation=edge.get("relation", ""))

    if chunk_count == 0:
        return KnowledgeBase(
            build_id=directory.name, chunk_count=0, status="unavailable", reason="색인된 청크가 없습니다"
        )

    return KnowledgeBase(
        build_id=directory.name,
        chunk_count=chunk_count,
        collection=collection,
        graph=graph,
        node_chunks=node_chunks,
        item_index=_load_item_index(directory),
        titles=_load_titles(collection),
    )


def _load_titles(collection) -> list[str]:
    """색인에 들어 있는 별표 품목 제목 목록 (메타데이터 필터용)."""
    try:
        rows = collection.get(include=["metadatas"], limit=20000)
    except Exception:  # noqa: BLE001
        return []
    seen: list[str] = []
    for metadata in rows.get("metadatas", []) or []:
        title = str((metadata or {}).get("title", "")).strip()
        if title and title not in seen:
            seen.append(title)
    return seen


ITEM_LINE = re.compile(
    r"업\s*종:\s*(?P<group>[^/]+)/\s*품\s*종:\s*(?P<kind>[^/]+)/\s*해\s*당\s*품\s*목:\s*(?P<items>.+)"
)
SPLIT_ITEMS = re.compile(r"[,、·ㆍ/]|\s{2,}")


SYNONYM_FILE = Path(__file__).resolve().parents[2] / "config" / "synonyms.yaml"


def _load_synonyms() -> dict[str, str]:
    if not SYNONYM_FILE.exists():
        return {}
    data = yaml.safe_load(SYNONYM_FILE.read_text(encoding="utf-8")) or {}
    return {str(k): str(v) for k, v in data.items()}


def _load_item_index(directory: Path) -> dict[str, str]:
    """별표Ⅰ(대상품목) 청크에서 '품목 이름 → 업종' 사전을 만들고, 일상어 동의어를 얹는다."""
    index: dict[str, str] = dict(_load_synonyms())
    chunks_file = directory / "chunks.jsonl"
    if not chunks_file.exists():
        return index

    for line in chunks_file.read_text(encoding="utf-8").splitlines():
        if not line or "별표Ⅰ" not in line:
            continue
        chunk = json.loads(line)
        match = ITEM_LINE.search(chunk.get("text", ""))
        if not match:
            continue
        group = match.group("group").strip()
        for raw in SPLIT_ITEMS.split(match.group("items")):
            name = raw.strip().strip("등().")
            if 2 <= len(name) <= 12 and name not in index:
                index[name] = group
    return index


_kb: KnowledgeBase | None = None


def get_kb() -> KnowledgeBase:
    global _kb
    if _kb is None:
        _kb = load_kb()
    return _kb
