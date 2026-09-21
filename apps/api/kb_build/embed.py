"""청크를 임베딩해 ChromaDB에 넣는다 (논문 기준: text-embedding-3-large)."""

from __future__ import annotations

import hashlib
import json

import chromadb
from openai import OpenAI

from app.config import get_settings

from .models import Chunk
from .paths import build_dir

BATCH = 96
COLLECTION = "kb_chunks"


def embed_chunks(build_id: str, chunks: list[Chunk], progress=None, force: bool = False) -> tuple[int, float]:
    """바뀐 청크만 다시 임베딩한다. 파싱을 고칠 때마다 전체 비용을 내지 않기 위함이다."""
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY가 없습니다. apps/api/.env를 확인하세요.")

    client = OpenAI(api_key=settings.openai_api_key)
    directory = build_dir(build_id)
    store = chromadb.PersistentClient(path=str(directory / "chroma"))

    hash_file = directory / "embed_hashes.json"
    previous: dict[str, str] = (
        json.loads(hash_file.read_text(encoding="utf-8")) if hash_file.exists() and not force else {}
    )
    current = {c.chunk_id: hashlib.sha256(c.embed_text.encode()).hexdigest()[:16] for c in chunks}

    existing = [c.name for c in store.list_collections()]
    if force or COLLECTION not in existing or not previous:
        if COLLECTION in existing:
            store.delete_collection(COLLECTION)
        collection = store.create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})
        targets = chunks
    else:
        collection = store.get_collection(COLLECTION)
        targets = [c for c in chunks if previous.get(c.chunk_id) != current[c.chunk_id]]
        removed = [cid for cid in previous if cid not in current]
        if removed:
            collection.delete(ids=removed)

    total_tokens = 0
    for start in range(0, len(targets), BATCH):
        batch = targets[start : start + BATCH]
        response = client.embeddings.create(
            model=settings.embed_model,
            input=[c.embed_text[:7000] for c in batch],
        )
        total_tokens += response.usage.total_tokens
        collection.upsert(
            ids=[c.chunk_id for c in batch],
            embeddings=[item.embedding for item in response.data],
            documents=[c.text for c in batch],
            metadatas=[
                {
                    "path": c.path,
                    "source": c.source,
                    "page": c.page or 0,
                    "table_id": c.table_id or "",
                    **{k: v for k, v in c.meta.items() if isinstance(v, str)},
                }
                for c in batch
            ],
        )
        if progress:
            progress(min(start + BATCH, len(targets)), len(targets))

    hash_file.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")
    cost = total_tokens * settings.price_embedding / 1_000_000
    return total_tokens, cost
