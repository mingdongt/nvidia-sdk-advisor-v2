"""nvidia-knowledge MCP server. Exposes 11 deterministic tools.

Run as stdio MCP server: python -m src.knowledge_server
Tools also callable in-process via knowledge_server.call_tool(name, args).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastmcp import FastMCP
from src.manifests import KnowledgeBase
from src.models import InstallConfig
from src import sdkm_probe, resource_estimator, response_file, command_gen

_MANIFEST_DIR = Path(__file__).resolve().parents[1] / "data" / "manifests"
_TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "data" / "response_templates"

mcp = FastMCP("nvidia-knowledge")
_kb = KnowledgeBase(_MANIFEST_DIR)


@mcp.tool()
def list_products() -> str:
    """List all NVIDIA products available in the SDK Manager catalog."""
    return json.dumps(_kb.list_products())


@mcp.tool()
def list_releases(product: str) -> str:
    """List all releases for a product (e.g. 'Jetson')."""
    return json.dumps([
        {"title": r.get("title"), "releaseVersion": r.get("releaseVersion"),
         "supportedHardware": r.get("supportedHardware")}
        for r in _kb.list_releases(product)
    ])


@mcp.tool()
def get_release(product: str, version: str) -> str:
    """Get full release metadata for a product+version."""
    r = _kb.get_release(product, version)
    return json.dumps(r) if r else json.dumps({"error": f"release not found: {product} {version}"})


@mcp.tool()
def list_hardware(family: str) -> str:
    """List hardware target series for a family (e.g. 'Jetson')."""
    return json.dumps(_kb.list_hardware(family))


@mcp.tool()
def lookup_target_id(board_name: str) -> str:
    """Resolve a free-text board name to a canonical target_id."""
    r = _kb.lookup_target_id(board_name)
    return json.dumps(r) if r else json.dumps({"error": f"unknown board: {board_name}"})


@mcp.tool()
def detect_connected_hardware() -> str:
    """Detect NVIDIA hardware connected via USB. Calls NvSDKManager.exe --list-connected."""
    return json.dumps(sdkm_probe.detect_connected_hardware())


@mcp.tool()
def estimate_resources(config_json: str) -> str:
    """Estimate disk + RAM for an InstallConfig."""
    cfg = InstallConfig(**json.loads(config_json))
    return json.dumps(resource_estimator.estimate_resources(cfg))


@mcp.tool()
def check_constraints(config_json: str, available_disk_gb: float, available_ram_gb: float) -> str:
    """Check if config fits within disk + RAM budget."""
    cfg = InstallConfig(**json.loads(config_json))
    return json.dumps(resource_estimator.check_constraints(cfg, available_disk_gb, available_ram_gb))


@mcp.tool()
def generate_response_file(config_json: str) -> str:
    """Generate a 3-section .ini response file matching NVIDIA's template."""
    cfg = InstallConfig(**json.loads(config_json))
    return json.dumps({"content": response_file.generate_response_file(cfg)})


@mcp.tool()
def validate_against_official_sample(generated_ini: str, product: str) -> str:
    """Validate generated INI structure against NVIDIA's official template."""
    return json.dumps(response_file.validate_against_official_sample(generated_ini, product, _TEMPLATE_DIR))


@mcp.tool()
def generate_command(config_json: str) -> str:
    """Build the sdkmanager --cli command string."""
    cfg = InstallConfig(**json.loads(config_json))
    return json.dumps({"command": command_gen.generate_command(cfg)})


# In-process helpers for tests (no MCP transport)
# Store references to the undecorated functions before @mcp.tool decorates them
_UNDECORATED_TOOLS = {
    "list_products": list_products,
    "list_releases": list_releases,
    "get_release": get_release,
    "list_hardware": list_hardware,
    "lookup_target_id": lookup_target_id,
    "detect_connected_hardware": detect_connected_hardware,
    "estimate_resources": estimate_resources,
    "check_constraints": check_constraints,
    "generate_response_file": generate_response_file,
    "validate_against_official_sample": validate_against_official_sample,
    "generate_command": generate_command,
}


def list_tool_names() -> list[str]:
    """Return list of all tool names."""
    return list(_UNDECORATED_TOOLS.keys())


def call_tool(name: str, args: dict) -> str:
    """Invoke a tool by name (in-process, for tests). Production uses MCP stdio."""
    if name not in _UNDECORATED_TOOLS:
        raise ValueError(f"Unknown tool: {name}")
    return _UNDECORATED_TOOLS[name](**args)


if __name__ == "__main__":
    mcp.run()
