import json
import tempfile
from pathlib import Path

from ingest.build_github_vectordb import chunk_markdown, build_index
from src.vector_search import VectorStore


def test_chunk_markdown_splits_on_h2_h3():
    md = """# Title

Intro paragraph.

## Section A

Content of A.
Multiple lines.

## Section B

### Sub B1

Sub content.

### Sub B2

More.
"""
    chunks = chunk_markdown(md, repo="test/x", url="http://example")
    # Expect at least 3 chunks: intro, A, B-Sub-B1, B-Sub-B2 (or B + B1 + B2 depending on impl)
    assert len(chunks) >= 3
    # Each chunk preserves source metadata
    for c in chunks:
        assert c["metadata"]["repo"] == "test/x"
        assert c["metadata"]["source"] == "github"
        assert "section" in c["metadata"]


def test_build_index_end_to_end():
    """Write 2 fake READMEs, build an index, search it."""
    readmes = [
        {"repo": "x/yolo", "url": "u1", "text": "# YOLO\n\nObject detection on Jetson with YOLO models."},
        {"repo": "y/slam", "url": "u2", "text": "# SLAM\n\nVisual SLAM for autonomous robots using stereo cameras."},
    ]
    # Use ignore_cleanup_errors=True to handle Windows file locking from Chroma SQLite
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        readme_path = Path(tmp) / "readmes.jsonl"
        with open(readme_path, "w", encoding="utf-8") as f:
            for r in readmes:
                f.write(json.dumps(r) + "\n")
        chroma_dir = Path(tmp) / "chroma"
        build_index(readme_path, chroma_dir, collection="github_test")

        # Query
        store = VectorStore(persist_dir=chroma_dir, collection="github_test")
        results = store.search("autonomous robot navigation", k=2)
        assert results[0]["metadata"]["repo"] == "y/slam"

        # Ensure store is closed before temp cleanup
        try:
            store.close()
        except Exception:
            pass
