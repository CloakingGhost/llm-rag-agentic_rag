"""지식 그래프 구축 (논문 방식 G1).

청크마다 LLM이 개념(노드)과 관계(엣지)를 뽑고, NetworkX로 모아 Louvain 군집을 매긴다.
이전 구현은 문서 앞부분 20여 개 청크로만 그래프를 만들어 사실상 동작하지 않았다.
여기서는 지식베이스 전체를 대상으로 한다.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import networkx as nx
from openai import OpenAI
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import cost_usd, get_settings

from .models import Chunk
from .paths import build_dir

CHUNKS_PER_CALL = 5
WORKERS = 6
PARTIAL = "graph_partial.jsonl"

SYSTEM = """너는 대한민국 소비자 분쟁 규정에서 지식 그래프를 만드는 도구다.
각 항목에서 핵심 개념과 관계를 뽑는다.
규칙:
1. 개념은 규정에서 실제로 쓰인 표현만 쓴다. 예: 청약철회, 품질보증기간, 스마트폰, 계약해제, 위약금.
2. 개념은 항목당 3~8개. 너무 일반적인 말(소비자, 사업자, 경우)은 넣지 않는다.
3. 관계는 (주체, 관계명, 대상) 형태로, 항목 안에서 실제로 성립하는 것만 만든다.
4. 관계명은 짧은 한국어로 쓴다. 예: 적용대상, 해결기준, 기간, 근거조항."""

SCHEMA = {
    "name": "graph_extraction",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["items"],
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["chunk_id", "concepts", "relations"],
                    "properties": {
                        "chunk_id": {"type": "string"},
                        "concepts": {"type": "array", "items": {"type": "string"}},
                        "relations": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["source", "relation", "target"],
                                "properties": {
                                    "source": {"type": "string"},
                                    "relation": {"type": "string"},
                                    "target": {"type": "string"},
                                },
                            },
                        },
                    },
                },
            }
        },
    },
    "strict": True,
}


@retry(
    retry=retry_if_exception_type(Exception),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=20),
    reraise=True,
)
def _extract(client: OpenAI, batch: list[Chunk]) -> tuple[list[dict], float]:
    settings = get_settings()
    payload = [
        {"chunk_id": c.chunk_id, "path": c.path, "text": c.text[:1200]}
        for c in batch
    ]
    response = client.chat.completions.create(
        model=settings.chat_model,
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        response_format={"type": "json_schema", "json_schema": SCHEMA},
        temperature=0,
    )
    usage = response.usage
    cached = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0) or 0
    spent = cost_usd(usage.prompt_tokens, usage.completion_tokens, cached, settings)
    items = json.loads(response.choices[0].message.content or "{}").get("items", [])
    return items, spent


def _load_partial(path: Path) -> tuple[list[dict], set[str]]:
    """이미 추출해 둔 결과를 읽는다. 네트워크가 끊겨도 다시 돌리면 이어서 한다."""
    if not path.exists():
        return [], set()
    items: list[dict] = []
    done_ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        items.append(item)
        done_ids.add(item.get("chunk_id", ""))
    return items, done_ids


def build_graph(build_id: str, chunks: list[Chunk], progress=None) -> tuple[nx.Graph, float, dict]:
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY가 없습니다. apps/api/.env를 확인하세요.")

    client = OpenAI(api_key=settings.openai_api_key)
    directory = build_dir(build_id)
    partial_path = directory / PARTIAL

    current_ids = {c.chunk_id for c in chunks}
    cached_items, done_ids = _load_partial(partial_path)
    # 파싱을 고치면 청크 ID가 바뀐다. 사라진 ID를 가리키는 옛 추출 결과는 버린다.
    cached_items = [item for item in cached_items if item.get("chunk_id") in current_ids]
    done_ids &= current_ids
    remaining = [c for c in chunks if c.chunk_id not in done_ids]
    batches = [remaining[i : i + CHUNKS_PER_CALL] for i in range(0, len(remaining), CHUNKS_PER_CALL)]

    graph = nx.Graph()
    total_cost = 0.0
    done = 0
    collected: list[dict] = list(cached_items)

    # 배치가 끝날 때마다 바로 파일에 적어 둔다.
    with ThreadPoolExecutor(max_workers=WORKERS) as pool, partial_path.open("a", encoding="utf-8") as sink:
        for items, spent in pool.map(lambda b: _extract(client, b), batches):
            total_cost += spent
            done += 1
            for item in items:
                sink.write(json.dumps(item, ensure_ascii=False) + "\n")
            sink.flush()
            collected.extend(items)
            if progress:
                progress(done, len(batches))

    for item in collected:
        chunk_id = item.get("chunk_id", "")
        for concept in item.get("concepts", []):
            name = concept.strip()
            if not name:
                continue
            if not graph.has_node(name):
                graph.add_node(name, chunks=[])
            if chunk_id and chunk_id not in graph.nodes[name]["chunks"]:
                graph.nodes[name]["chunks"].append(chunk_id)
        for relation in item.get("relations", []):
            source, target = relation.get("source", "").strip(), relation.get("target", "").strip()
            if not source or not target or source == target:
                continue
            for name in (source, target):
                if not graph.has_node(name):
                    graph.add_node(name, chunks=[])
            graph.add_edge(source, target, relation=relation.get("relation", ""))

    # Louvain 군집 (논문의 지식 군집화)
    communities = nx.community.louvain_communities(graph, seed=42) if graph.number_of_nodes() else []
    for index, group in enumerate(communities):
        for name in group:
            graph.nodes[name]["community"] = index

    payload = {
        "nodes": [
            {"name": name, "chunks": data.get("chunks", []), "community": data.get("community", -1)}
            for name, data in graph.nodes(data=True)
        ],
        "edges": [
            {"source": s, "target": t, "relation": d.get("relation", "")}
            for s, t, d in graph.edges(data=True)
        ],
    }
    (build_dir(build_id) / "graph.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )

    stats = {
        "nodes": graph.number_of_nodes(),
        "edges": graph.number_of_edges(),
        "communities": len(communities),
    }
    return graph, total_cost, stats
