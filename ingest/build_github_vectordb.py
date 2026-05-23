"""Build Chroma vector index from scraped GitHub READMEs.

Chunking strategy: split on Markdown ## and ### headings. Each chunk
preserves repo + section metadata. Max chunk size ~800 tokens (we
approximate via character count; transformers tokenizer skipped for speed).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from src.vector_search import VectorStore

_MAX_CHUNK_CHARS = 3200  # ~800 tokens by rule of thumb (4 chars/token)


def chunk_markdown(text: str, repo: str, url: str) -> list[dict]:
    """Split markdown by ## and ### headings. Returns list of {id, text, metadata}."""
    lines = text.splitlines()
    chunks = []
    current_section = "intro"
    buf = []

    def flush():
        if not buf:
            return
        body = "\n".join(buf).strip()
        if not body:
            buf.clear()
            return
        # Hard cap to MAX
        body = body[:_MAX_CHUNK_CHARS]
        chunk_id = f"{repo}::{current_section}::{len(chunks)}"
        chunks.append({
            "id": chunk_id,
            "text": f"[{repo} / {current_section}]\n{body}",
            "metadata": {
                "source": "github",
                "repo": repo,
                "section": current_section,
                "url": url,
            },
        })
        buf.clear()

    for line in lines:
        m = re.match(r"^(#{2,3})\s+(.+?)\s*$", line)
        if m:
            flush()
            current_section = m.group(2).strip()[:80]
        else:
            buf.append(line)
    flush()

    return chunks


def build_index(readmes_path: Path, chroma_dir: Path, collection: str = "github") -> int:
    """Read JSONL, chunk, embed, upsert. Returns chunk count."""
    store = VectorStore(persist_dir=chroma_dir, collection=collection)

    all_ids, all_texts, all_metas = [], [], []
    with open(readmes_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            chunks = chunk_markdown(r["text"], repo=r["repo"], url=r["url"])
            for c in chunks:
                all_ids.append(c["id"])
                all_texts.append(c["text"])
                all_metas.append(c["metadata"])

    if not all_ids:
        print("nothing to index")
        return 0

    # Batch upsert
    BATCH = 64
    for i in range(0, len(all_ids), BATCH):
        store.upsert(
            ids=all_ids[i:i+BATCH],
            texts=all_texts[i:i+BATCH],
            metadatas=all_metas[i:i+BATCH],
        )
        print(f"  upserted {min(i+BATCH, len(all_ids))}/{len(all_ids)}")

    print(f"\nIndex built: {store.count()} chunks at {chroma_dir / collection}")
    return store.count()


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    readmes = project_root / "data" / "corpus" / "github" / "readmes.jsonl"
    chroma = project_root / "data" / "chroma_db"
    build_index(readmes, chroma)


if __name__ == "__main__":
    main()
