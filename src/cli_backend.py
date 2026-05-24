"""Claude CLI backend.

Runs queries through local `claude` CLI (subscription-based) instead of the
Anthropic SDK (API-based). Lets us use Opus 4.7 without hitting API quota that
Claude Code itself drains.

Two modes:
- `run_with_tools(user_input, system_prompt)` — claude CLI + our 2 MCP servers
  (parity with the SDK backend's agent loop).
- `run_no_tools(user_input)` — claude CLI alone, no MCP. Used as the baseline
  in our 3-way comparison: how much does our tool layer add vs raw model?
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_VENV_PYTHON = _PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"

# MCP server config — claude CLI spawns these as stdio subprocesses
_MCP_CONFIG = {
    "mcpServers": {
        "nvidia-knowledge": {
            "command": str(_VENV_PYTHON if _VENV_PYTHON.exists() else "python"),
            "args": ["-m", "src.knowledge_server"],
            "cwd": str(_PROJECT_ROOT),
        },
        "nvidia-corpus-rag": {
            "command": str(_VENV_PYTHON if _VENV_PYTHON.exists() else "python"),
            "args": ["-m", "src.rag_server"],
            "cwd": str(_PROJECT_ROOT),
        },
    }
}


def _model_arg() -> list[str]:
    """Return ['--model', '<id>'] if ANTHROPIC_MODEL is set; else empty list."""
    model = os.getenv("ANTHROPIC_MODEL", "").strip()
    if model:
        return ["--model", model]
    return []


def _locate_claude() -> str:
    """Find claude executable. On Windows, must use .exe to avoid subprocess hang."""
    import shutil
    return shutil.which("claude.exe") or shutil.which("claude") or "claude"


def _run_claude_cli(args: list[str], timeout: int = 300) -> dict:
    """Run claude CLI, parse JSON output, return result dict.

    On Windows, claude.exe hangs in subprocess.run unless stdin is closed.
    We always pass stdin=DEVNULL.

    Raises RuntimeError on non-zero exit or unparseable output.
    """
    exe = _locate_claude()
    cmd = [exe, *args]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "`claude` CLI not found on PATH. Install Claude Code or fall back to "
            "ANTHROPIC_BACKEND=sdk."
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"claude CLI timed out after {timeout}s: {e}") from e

    if proc.returncode != 0:
        raise RuntimeError(
            f"claude CLI exit {proc.returncode}: stderr={proc.stderr[:500]}"
        )

    # --output-format json returns a single JSON object
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        # Fallback: maybe stream-json or plain text
        return {"result": proc.stdout.strip()}


def run_with_tools(user_input: str, system_prompt: str, timeout: int = 300) -> str:
    """Run query via claude CLI + our 2 MCP servers. Returns assistant final text."""
    text, _ = _run_with_tools_internal(user_input, system_prompt, timeout, trace=False)
    return text


def run_with_tools_traced(user_input: str, system_prompt: str, timeout: int = 300) -> tuple[str, list[str]]:
    """Same as run_with_tools but ALSO returns the ordered list of tool names that fired.

    Used by the cross-backend tool-usage comparison report.
    """
    return _run_with_tools_internal(user_input, system_prompt, timeout, trace=True)


def _run_with_tools_internal(user_input: str, system_prompt: str, timeout: int, trace: bool) -> tuple[str, list[str]]:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8",
    ) as f:
        json.dump(_MCP_CONFIG, f)
        mcp_config_path = f.name

    try:
        output_fmt = "stream-json" if trace else "json"
        args = [
            "-p", user_input,
            "--append-system-prompt", system_prompt,
            "--mcp-config", mcp_config_path,
            "--allowedTools", "mcp__nvidia-knowledge", "mcp__nvidia-corpus-rag",
            "--dangerously-skip-permissions",
            "--output-format", output_fmt,
            "--no-session-persistence",
            *(["--verbose"] if trace else []),  # stream-json requires --verbose
            *_model_arg(),
        ]
        if not trace:
            result = _run_claude_cli(args, timeout=timeout)
            return result.get("result", ""), []

        # stream-json: each line is a JSON event
        exe = _locate_claude()
        proc = subprocess.run(
            [exe, *args], capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"claude CLI exit {proc.returncode}: {proc.stderr[:500]}")

        final_text = ""
        tools_called: list[str] = []
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            etype = evt.get("type")
            if etype == "assistant" and isinstance(evt.get("message"), dict):
                for block in evt["message"].get("content", []):
                    if block.get("type") == "tool_use":
                        # Tool names from MCP are like "mcp__nvidia-knowledge__lookup_target_id"
                        name = block.get("name", "")
                        if "__" in name:
                            name = name.split("__")[-1]
                        tools_called.append(name)
            elif etype == "result":
                final_text = evt.get("result", "") or final_text
        return final_text, tools_called
    finally:
        try:
            os.unlink(mcp_config_path)
        except OSError:
            pass


_NO_TOOLS_INSTRUCTION = """You are NVIDIA SDK Advisor. Help the user pick the right SDK Manager configuration.

For each request, respond with:
1. A brief explanation paragraph.
2. The full sdkmanager command in a ```bash code block, with --product, --version,
   --target, --target-os, --host, --additional-sdk[] flags.
3. The response file content in a ```ini code block with [client_arguments] section.

Use the same flag names and value vocabulary as `sdkmanager --cli`. Target IDs follow
the pattern JETSON_<MODEL>_TARGETS (e.g. JETSON_ORIN_NANO_TARGETS).

Be specific. Generate the command directly; do not ask clarifying questions."""


def run_no_tools(user_input: str, timeout: int = 120) -> str:
    """Run query via claude CLI alone, no MCP.

    This is the BASELINE for our comparison: what does the raw model know
    about NVIDIA SDKs without our retrieval layer?

    We DO pass a brief instruction prompt so the model knows the expected
    output format (sdkmanager command + .ini), to make the comparison fair
    against the with-tools version. The model still has to invent target IDs,
    version strings, and SDK names from training-time knowledge alone.
    """
    args = [
        "-p", user_input,
        "--append-system-prompt", _NO_TOOLS_INSTRUCTION,
        "--output-format", "json",
        "--no-session-persistence",
        *_model_arg(),
    ]
    result = _run_claude_cli(args, timeout=timeout)
    return result.get("result", "")


if __name__ == "__main__":
    # Smoke test from command line
    if len(sys.argv) < 2:
        print("usage: python -m src.cli_backend [--no-tools] <query>", file=sys.stderr)
        sys.exit(2)
    no_tools = "--no-tools" in sys.argv
    query = " ".join(a for a in sys.argv[1:] if a != "--no-tools")
    if no_tools:
        print(run_no_tools(query))
    else:
        from src.agent import SYSTEM_PROMPT
        print(run_with_tools(query, SYSTEM_PROMPT))
