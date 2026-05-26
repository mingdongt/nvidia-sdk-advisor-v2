"""SDK Advisor agent core.

Connects to the nvidia-knowledge MCP server via stdio, exposes its tools to
Claude, runs a multi-turn tool-use loop. Higher-level conversational logic
(decide when to ask the user) lives in src/repl.py.
"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Callable, Optional

import anthropic
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

_KNOWLEDGE_SERVER = Path(__file__).parent / "knowledge_server.py"
_RAG_SERVER = Path(__file__).parent / "rag_server.py"

# Versioned prompt assets live in prompts/<version>/ so the system prompt can
# be git-diffed and revised without touching code. Bump the version directory
# when introducing a non-backward-compatible prompt revision.
_PROMPT_DIR = Path(__file__).parent / "prompts" / "1.0.0"
SYSTEM_PROMPT = (_PROMPT_DIR / "system-prompt.md").read_text(encoding="utf-8")

# Cap the tool-use loop so a tool that keeps erroring can't make Claude loop
# indefinitely. Successful runs typically use 6-12 turns; 50 is wide margin.
MAX_TURNS = 50


def _build_tools(mcp_tools) -> list:
    return [
        {"name": t.name, "description": t.description, "input_schema": t.inputSchema}
        for t in mcp_tools
    ]


def _call_with_retry(client, model, tools, messages, max_attempts=4):
    """Anthropic call with exponential backoff on rate limit."""
    for attempt in range(max_attempts):
        try:
            return client.messages.create(
                model=model, max_tokens=4096,
                system=SYSTEM_PROMPT, tools=tools, messages=messages,
            )
        except anthropic.RateLimitError:
            if attempt == max_attempts - 1:
                raise
            time.sleep(15 * (attempt + 1))


async def run_agent_single_turn(
    user_input: str,
    on_step: Optional[Callable] = None,
    on_thinking: Optional[Callable[[str], None]] = None,
) -> str:
    """Single-prompt agent run. Backend dispatch via ANTHROPIC_BACKEND env var.

    - 'sdk' (default): anthropic Python SDK, connects to both MCP servers (current path)
    - 'cli':           claude CLI subprocess with MCP servers attached
    - 'cli-no-tools':  claude CLI subprocess, NO tools (baseline for 3-way comparison)
    """
    backend = os.getenv("ANTHROPIC_BACKEND", "sdk").lower()
    if backend == "cli":
        from src import cli_backend
        return await asyncio.to_thread(cli_backend.run_with_tools, user_input, SYSTEM_PROMPT)
    if backend == "cli-no-tools":
        from src import cli_backend
        return await asyncio.to_thread(cli_backend.run_no_tools, user_input)

    # Default: anthropic SDK
    import json
    # MCP's stdio_client uses a small env whitelist (PATH/HOME/etc.) and drops
    # everything else when StdioServerParameters.env is None. Forward FastMCP
    # control vars explicitly so settings like FASTMCP_SHOW_SERVER_BANNER=false
    # (used by the demo recording) reach the spawned server process.
    _mcp_env = {k: v for k, v in os.environ.items() if k.startswith("FASTMCP_")} or None
    k_params = StdioServerParameters(command="python", args=[str(_KNOWLEDGE_SERVER)], env=_mcp_env)
    r_params = StdioServerParameters(command="python", args=[str(_RAG_SERVER)], env=_mcp_env)

    async with stdio_client(k_params) as (kr, kw), stdio_client(r_params) as (rr, rw):
        async with ClientSession(kr, kw) as k_session, ClientSession(rr, rw) as r_session:
            await k_session.initialize()
            await r_session.initialize()

            k_tools = (await k_session.list_tools()).tools
            r_tools = (await r_session.list_tools()).tools

            # Tool dispatch table: tool_name -> session
            session_map: dict[str, ClientSession] = {}
            for t in k_tools:
                session_map[t.name] = k_session
            for t in r_tools:
                session_map[t.name] = r_session

            tools = _build_tools(list(k_tools) + list(r_tools))

            client = anthropic.Anthropic()
            model = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
            messages = [{"role": "user", "content": user_input}]

            import asyncio as _asyncio
            for _ in range(MAX_TURNS):
                response = await _asyncio.to_thread(_call_with_retry, client, model, tools, messages)
                if response.stop_reason == "end_turn":
                    return next((b.text for b in response.content if hasattr(b, "text")), "")
                tool_results = []
                # Process blocks in order so on_thinking and on_step fire
                # interleaved exactly as the model produced them — gives the
                # demo trace a natural "thinking → tool call → result" flow.
                for block in response.content:
                    if block.type == "text":
                        if on_thinking and getattr(block, "text", None):
                            on_thinking(block.text)
                        continue
                    if block.type != "tool_use":
                        continue
                    sess = session_map.get(block.name)
                    if sess is None:
                        result_text = json.dumps({"error": f"unknown tool: {block.name}"})
                    else:
                        result = await sess.call_tool(block.name, arguments=block.input)
                        result_text = result.content[0].text if result.content else "{}"
                    if on_step:
                        on_step(block.name, block.input, result_text)
                    tool_results.append({
                        "type": "tool_result", "tool_use_id": block.id, "content": result_text,
                    })
                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": tool_results})
            raise RuntimeError(
                f"agent exceeded MAX_TURNS={MAX_TURNS} without stop_reason='end_turn' "
                f"— likely stuck in a tool error loop"
            )
