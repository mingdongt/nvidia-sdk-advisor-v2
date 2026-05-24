"""Smoke test: import server module, check tool count, dispatch a few tools in-process."""
import json
from src import knowledge_server


def test_server_has_expected_tools():
    tools = knowledge_server.list_tool_names()
    expected = {
        "list_products", "list_releases", "get_release", "list_hardware", "lookup_target_id",
        "detect_connected_hardware",
        "estimate_resources", "check_constraints", "validate_combo",
        "generate_response_file", "validate_against_official_sample",
        "generate_command", "parse_install_log",
    }
    assert set(tools) == expected, f"diff: {expected ^ set(tools)}"


def test_lookup_target_id_via_server():
    result_json = knowledge_server.call_tool("lookup_target_id", {"board_name": "orin nano 8gb"})
    result = json.loads(result_json)
    assert result["target_id"] == "JETSON_ORIN_NANO_TARGETS"


def test_generate_response_file_via_server():
    """Typed-args call (no JSON-in-string anti-pattern)."""
    result = json.loads(knowledge_server.call_tool(
        "generate_response_file",
        {
            "product": "Jetson", "version": "6.1", "target": "JETSON_ORIN_NX_TARGETS",
            "additional_sdks": ["DeepStream 7.0"],
        },
    ))
    assert "[client_arguments]" in result["content"]


def test_generate_command_via_server():
    """Regression test for the JSON-in-string bug fixed by typed args.
    Earlier signature took `config_json: str` and the LLM occasionally
    produced malformed JSON. Typed args make that class of error impossible.
    """
    result = json.loads(knowledge_server.call_tool(
        "generate_command",
        {
            "product": "Jetson", "version": "6.1", "target": "JETSON_ORIN_NX_TARGETS",
            "additional_sdks": ["DeepStream 7.0"],
        },
    ))
    assert "sdkmanager" in result["command"]


def test_validate_combo_via_server():
    result = json.loads(knowledge_server.call_tool(
        "validate_combo",
        {"product": "Jetson", "version": "6.1", "target": "JETSON_ORIN_NX_TARGETS"}
    ))
    assert result["valid"] is True


def test_validate_combo_unsupported_target_via_server():
    result = json.loads(knowledge_server.call_tool(
        "validate_combo",
        {"product": "Jetson", "version": "7.1", "target": "JETSON_ORIN_NANO_TARGETS"}
    ))
    assert result["valid"] is False


def test_parse_install_log_in_tools():
    tools = knowledge_server.list_tool_names()
    assert "parse_install_log" in tools
    assert len(tools) == 13  # Plan A (11) + validate_combo (12) + parse_install_log (13)


def test_parse_install_log_via_server():
    """parse_install_log returns a LogExcerpt JSON with structural fields only —
    no classification (failed_stage/error_class were removed; agent reads tail_text)."""
    import os
    fixture = os.path.join(os.path.dirname(__file__), "fixtures", "apt_missing_package.log")
    result_json = knowledge_server.call_tool("parse_install_log", {"log_path_or_archive": fixture})
    result = json.loads(result_json)
    # LogExcerpt fields
    assert "tail_text" in result
    assert "file_count" in result
    assert "source_path" in result
    assert result["file_count"] == 1
    assert "Unable to locate package" in result["tail_text"]
    # No classification fields anymore
    assert "failed_stage" not in result
    assert "error_class" not in result
