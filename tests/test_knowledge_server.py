"""Smoke test: import server module, check tool count, dispatch a few tools in-process."""
import json
from src import knowledge_server


def test_server_has_expected_tools():
    tools = knowledge_server.list_tool_names()
    expected = {
        "list_products", "list_releases", "get_release", "list_hardware", "lookup_target_id",
        "detect_connected_hardware",
        "estimate_resources", "check_constraints",
        "generate_response_file", "validate_against_official_sample",
        "generate_command",
    }
    assert set(tools) == expected, f"diff: {expected ^ set(tools)}"


def test_lookup_target_id_via_server():
    result_json = knowledge_server.call_tool("lookup_target_id", {"board_name": "orin nano 8gb"})
    result = json.loads(result_json)
    assert result["target_id"] == "JETSON_ORIN_NANO_TARGETS"


def test_generate_response_file_via_server():
    config_json = json.dumps({
        "product": "Jetson", "version": "6.1", "target": "JETSON_ORIN_NX_TARGETS",
        "additional_sdks": ["DeepStream 7.0"],
    })
    result = json.loads(knowledge_server.call_tool("generate_response_file", {"config_json": config_json}))
    assert "[client_arguments]" in result["content"]
