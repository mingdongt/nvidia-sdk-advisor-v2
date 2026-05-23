import json
from src import rag_server


def test_server_has_expected_tools():
    """Plan B.2 ships only lookup_container_reqs. Other tools added in B.6 and B.8."""
    tools = rag_server.list_tool_names()
    assert "lookup_container_reqs" in tools


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


def test_search_3p_tools_registered():
    assert "search_3p_sample_repos" in rag_server.list_tool_names()


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


def test_search_forum_threads_in_tools():
    assert "search_forum_threads" in rag_server.list_tool_names()


def test_search_docs_in_tools():
    assert "search_docs" in rag_server.list_tool_names()


def test_server_b_tool_count_is_4():
    assert len(rag_server.list_tool_names()) == 4


def test_search_forum_threads_mocked():
    """Mock brave_search to keep this test offline."""
    from unittest.mock import patch
    fake_hits = [
        {"title": "How to flash Orin Nano", "url": "https://forums.developer.nvidia.com/t/12345", "snippet": "..."},
    ]
    with patch("src.rag_server.brave_search", return_value=fake_hits):
        result_json = rag_server.call_tool(
            "search_forum_threads",
            {"query": "flash Orin Nano", "k": 3, "mode": "general"},
        )
        result = json.loads(result_json)
        assert "hits" in result
        assert len(result["hits"]) == 1
        assert "forums.developer.nvidia.com" in result["hits"][0]["url"]
        assert result["mode"] == "general"


def test_search_forum_threads_troubleshoot_mode_appends_keywords():
    """troubleshoot mode should bias query toward fix/error keywords."""
    from unittest.mock import patch
    captured_query = []
    def fake_brave(q, k, site):
        captured_query.append(q)
        return []
    with patch("src.rag_server.brave_search", side_effect=fake_brave):
        rag_server.call_tool(
            "search_forum_threads",
            {"query": "apt nvidia-jetpack", "k": 3, "mode": "troubleshoot"},
        )
    # Query should have been augmented
    assert "error" in captured_query[0] or "fix" in captured_query[0] or "solution" in captured_query[0]


def test_search_docs_mocked():
    from unittest.mock import patch
    fake_hits = [
        {"title": "JetPack 6.1 Release Notes", "url": "https://docs.nvidia.com/jetson/jetpack-61/", "snippet": "..."},
    ]
    with patch("src.rag_server.brave_search", return_value=fake_hits):
        result_json = rag_server.call_tool("search_docs", {"query": "JetPack 6.1 release notes", "k": 3})
        result = json.loads(result_json)
        assert "hits" in result
        assert len(result["hits"]) == 1
        assert "docs.nvidia.com" in result["hits"][0]["url"]


def test_search_brave_error_returns_friendly_dict():
    """If brave_search raises, the tool should return {error: ..., hits: []} rather than crash."""
    from unittest.mock import patch
    from src.brave_search import BraveSearchError
    with patch("src.rag_server.brave_search", side_effect=BraveSearchError("BRAVE_API_KEY not set")):
        result_json = rag_server.call_tool("search_forum_threads", {"query": "anything", "k": 3, "mode": "general"})
        result = json.loads(result_json)
        assert "error" in result
        assert "BRAVE_API_KEY" in result["error"]
        assert result["hits"] == []
