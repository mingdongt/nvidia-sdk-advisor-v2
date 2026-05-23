"""Chroma + sentence-transformers wrapper.

Single class VectorStore owns:
- The Chroma persistent client
- The embedding model (lazy-loaded, cached at module level)
- upsert + search semantics with source-tagged metadata
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


def _resolve_model_path(model_name: str) -> str:
    """Return a local cache path for the model if it exists, else the HF Hub name.

    Loading from a local path avoids a HF Hub network round-trip that deadlocks
    on Windows Python 3.14 when the process has piped stdin/stdout (MCP stdio).
    """
    import os
    from pathlib import Path as _Path

    cache_dir = _Path(os.path.expanduser("~")) / ".cache" / "huggingface" / "hub"
    # Convert "org/name" → "models--org--name"
    slug = "models--" + model_name.replace("/", "--")
    snapshots_dir = cache_dir / slug / "snapshots"
    if snapshots_dir.is_dir():
        candidates = sorted(snapshots_dir.iterdir())
        if candidates:
            return str(candidates[-1])  # pick the latest snapshot
    return model_name  # fall back to HF Hub name


@lru_cache(maxsize=1)
def _embedder():
    """Module-level cached embedder (90MB, ~1s first load).

    Imported lazily to avoid deadlocking subprocess stdio pipes on Windows
    when sentence-transformers / PyTorch are imported at module level.
    Uses a local cache path when available to bypass HF Hub network checks.
    """
    from sentence_transformers import SentenceTransformer  # lazy import
    return SentenceTransformer(_resolve_model_path(_MODEL_NAME))


class VectorStore:
    def __init__(self, persist_dir: Path, collection: str):
        import chromadb  # lazy import — avoids heavy init at module-import time
        self._client = chromadb.PersistentClient(path=str(persist_dir))
        self._collection = self._client.get_or_create_collection(
            name=collection,
            metadata={"hnsw:space": "cosine"},
        )

    def close(self) -> None:
        """Explicitly close the Chroma client (important on Windows)."""
        try:
            if hasattr(self._client, "_producer"):
                # Chroma PersistentClient has a producer to cleanup
                self._client._producer = None
        except Exception:
            pass
        try:
            if hasattr(self._client, "delete"):
                self._client.delete()
            elif hasattr(self._client, "close"):
                self._client.close()
        except Exception:
            pass

    def upsert(self, ids: list[str], texts: list[str], metadatas: list[dict]) -> None:
        assert len(ids) == len(texts) == len(metadatas), "ids/texts/metadatas length mismatch"
        embeddings = _embedder().encode(texts, convert_to_numpy=True, show_progress_bar=False).tolist()
        self._collection.upsert(
            ids=ids,
            embeddings=embeddings,
            documents=texts,
            metadatas=metadatas,
        )

    def search(self, query: str, k: int = 5, where: Optional[dict] = None) -> list[dict]:
        emb = _embedder().encode([query], convert_to_numpy=True, show_progress_bar=False).tolist()
        result = self._collection.query(
            query_embeddings=emb,
            n_results=k,
            where=where,
        )
        # Chroma returns parallel lists; flatten to records
        out = []
        if not result["ids"] or not result["ids"][0]:
            return out
        ids = result["ids"][0]
        docs = result["documents"][0]
        metas = result["metadatas"][0]
        dists = result["distances"][0]
        for i, doc, meta, dist in zip(ids, docs, metas, dists):
            out.append({
                "id": i,
                "text": doc,
                "metadata": meta or {},
                "score": 1.0 - dist,  # cosine: dist 0 = identical, dist 1 = orthogonal
            })
        return out

    def count(self) -> int:
        return self._collection.count()
