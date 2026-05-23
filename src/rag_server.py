"""nvidia-corpus-rag MCP server. Hybrid 3-tier retrieval.

Tier 1: lookup_container_reqs       - structured NGC catalog lookup (this task)
Tier 2: search_3p_sample_repos      - vector search over GitHub READMEs (added in B.6)
Tier 3: search_forum_threads        - Brave Search API (added in B.8)
        search_docs                  - Brave Search API filtered to docs.nvidia.com (added in B.8)

Run as stdio MCP server: python -m src.rag_server
Tools also callable in-process via rag_server.call_tool(name, args).
"""
import json
import sys
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastmcp import FastMCP
from src.brave_search import brave_search, BraveSearchError

mcp = FastMCP("nvidia-corpus-rag")

_NGC_PATH = Path(__file__).resolve().parents[1] / "data" / "corpus" / "ngc" / "containers.jsonl"


@lru_cache(maxsize=1)
def _ngc_index() -> dict:
    """Read containers.jsonl once into a dict keyed by lowercased name."""
    out = {}
    if not _NGC_PATH.exists():
        return out
    with open(_NGC_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            out[rec["name"].lower()] = rec
    return out


@mcp.tool()
def lookup_container_reqs(container_id: str) -> str:
    """Look up NVIDIA NGC container metadata: JetPack/CUDA/TensorRT requirements, image size, architecture support.

    container_id is typically 'org/name' (e.g. 'nvidia/deepstream-l4t', 'dustynv/nano_llm').
    Suffix match also supported (e.g. 'nano_llm' matches 'dustynv/nano_llm').
    """
    q = container_id.lower().strip()
    index = _ngc_index()

    # Direct match
    if q in index:
        return json.dumps(index[q])

    # Suffix match (user gave just the name)
    for full_name, rec in index.items():
        if full_name.endswith("/" + q):
            return json.dumps(rec)

    # Substring fallback
    matches = [rec for full_name, rec in index.items() if q in full_name]
    if matches:
        return json.dumps({"matches": matches[:5], "note": "multiple candidates; agent should pick one"})

    known_sample = list(index.keys())[:10]
    return json.dumps({"error": f"no NGC entry for '{container_id}'", "known_sample": known_sample})


_GITHUB_CHROMA = Path(__file__).resolve().parents[1] / "data" / "chroma_db"


@lru_cache(maxsize=1)
def _github_store():
    from src.vector_search import VectorStore
    return VectorStore(persist_dir=_GITHUB_CHROMA, collection="github")


@mcp.tool()
def search_3p_sample_repos(query: str, k: int = 5) -> str:
    """Semantic search across 30 NVIDIA-AI-IOT, dusty-nv, isaac-ros sample repos.

    Returns top-k chunks from README.md files. Use this to find which sample/container
    matches a user's workload (e.g. 'I want to run YOLO' -> jetson-inference DetectNet).
    """
    try:
        hits = _github_store().search(query, k=k)
    except Exception as e:
        return json.dumps({"error": f"search failed: {e}", "hits": []})
    return json.dumps({"hits": hits})


@mcp.tool()
def search_forum_threads(query: str, k: int = 5, mode: str = "general") -> str:
    """Search NVIDIA Developer Forums (forums.developer.nvidia.com) via Brave Search.

    mode='general' for advice/best-practices queries.
    mode='troubleshoot' adds error/fix keywords to bias toward fix-related threads.
    """
    q = query
    if mode == "troubleshoot":
        q = f"{q} error fix solution"
    try:
        hits = brave_search(q, k=k, site="forums.developer.nvidia.com")
    except BraveSearchError as e:
        return json.dumps({"error": str(e), "hits": []})
    return json.dumps({"hits": hits, "mode": mode})


@mcp.tool()
def search_docs(query: str, k: int = 5) -> str:
    """Search NVIDIA documentation (docs.nvidia.com) via Brave Search.

    Use for: release notes, system requirements, SDK install guides, FAQs.
    """
    try:
        hits = brave_search(query, k=k, site="docs.nvidia.com")
    except BraveSearchError as e:
        return json.dumps({"error": str(e), "hits": []})
    return json.dumps({"hits": hits})


# In-process helpers (Plan A pattern from knowledge_server.py)
_UNDECORATED_TOOLS = {
    "lookup_container_reqs": lookup_container_reqs,
    "search_3p_sample_repos": search_3p_sample_repos,
    "search_forum_threads": search_forum_threads,
    "search_docs": search_docs,
}


def list_tool_names() -> list[str]:
    return list(_UNDECORATED_TOOLS.keys())


def call_tool(name: str, args: dict) -> str:
    if name not in _UNDECORATED_TOOLS:
        raise ValueError(f"Unknown tool: {name}")
    return _UNDECORATED_TOOLS[name](**args)


if __name__ == "__main__":
    mcp.run()
