import json
from src import rag_server


def test_server_has_expected_tools():
    """Server B ships 2 tools: NGC catalog lookup (Tier 1) + GitHub README RAG (Tier 2).

    Tier 3 (forum + docs) is left to the underlying Claude's native web search.
    """
    tools = rag_server.list_tool_names()
    assert "lookup_container_reqs" in tools
    assert "search_3p_sample_repos" in tools
    assert len(tools) == 2


def test_lookup_container_reqs_hit():
    """Use a known-good container — read first entry from the JSONL to avoid hardcoding."""
    from pathlib import Path
    ngc_path = Path(__file__).resolve().parents[1] / "data" / "corpus" / "ngc" / "containers.jsonl"
    first_line = ngc_path.read_text(encoding="utf-8").splitlines()[0]
    sample_name = json.loads(first_line)["name"]

    result_json = rag_server.call_tool(
        "lookup_container_reqs",
        {"container_id": sample_name},
    )
    result = json.loads(result_json)
    assert "display_name" in result
    assert result["name"] == sample_name


def test_lookup_container_reqs_miss():
    result_json = rag_server.call_tool(
        "lookup_container_reqs",
        {"container_id": "nvidia/this-does-not-exist-12345"},
    )
    result = json.loads(result_json)
    assert "error" in result


def test_lookup_container_reqs_suffix_match():
    """User may give just the name without org prefix."""
    from pathlib import Path
    ngc_path = Path(__file__).resolve().parents[1] / "data" / "corpus" / "ngc" / "containers.jsonl"
    first_line = ngc_path.read_text(encoding="utf-8").splitlines()[0]
    full_name = json.loads(first_line)["name"]
    just_name = full_name.split("/", 1)[1]

    result_json = rag_server.call_tool(
        "lookup_container_reqs",
        {"container_id": just_name},
    )
    result = json.loads(result_json)
    # Either direct hit (suffix match works) OR multi-match candidates
    assert "display_name" in result or "matches" in result


def test_search_3p_sample_repos_returns_hits():
    """Query against the real (committed) Chroma index."""
    result_json = rag_server.call_tool(
        "search_3p_sample_repos",
        {"query": "object detection on Jetson Orin Nano", "k": 3},
    )
    result = json.loads(result_json)
    assert "hits" in result
    assert len(result["hits"]) > 0
    assert "repo" in result["hits"][0]["metadata"]
    assert "score" in result["hits"][0]
