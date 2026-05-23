"""SDK Advisor agent core.

Connects to the nvidia-knowledge MCP server via stdio, exposes its tools to
Claude, runs a multi-turn tool-use loop. Higher-level conversational logic
(decide when to ask the user) lives in src/repl.py.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable, Optional

import anthropic
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

_KNOWLEDGE_SERVER = Path(__file__).parent / "knowledge_server.py"
_RAG_SERVER = Path(__file__).parent / "rag_server.py"


SYSTEM_PROMPT = """You are NVIDIA SDK Advisor — a conversational agent helping a developer pick the right SDK Manager configuration for their hardware and use case.

You have access to MCP tools that talk to NVIDIA's own catalog and detect connected hardware.

## Default behavior: produce a plan in a single response when possible

Most inputs include enough info to produce a useful plan immediately. For each user message:

1. Call detect_connected_hardware once (if not already done in this conversation).
2. Resolve any board name to a canonical target_id via lookup_target_id. Save the result as `target`.
3. List products/releases as needed to pick a JetPack version that supports the hardware. Prefer the most recent compatible version unless the user specified one. Save `product` (typically "Jetson") and `version`.
4. If the user gave resource constraints (disk, RAM), call estimate_resources and check_constraints; otherwise skip.
5. Build a JSON config object for generate_response_file and generate_command with these EXACT fields:
   ```json
   {
     "product": "Jetson",
     "version": "6.0",
     "target": "JETSON_ORIN_NANO_TARGETS",
     "target_os": "Linux",
     "host": true,
     "flash": false,
     "additional_sdks": ["DeepStream 7.0"]
   }
   ```
   Do NOT use: target_id, jetpack_version, release_version, hardware, hardware_id, device_id, board, sdks (use additional_sdks instead).
6. Call generate_response_file(config_json) and generate_command(config_json) with your config.
7. Present the result as: a brief explanation paragraph, then the sdkmanager command in a ```bash code block, then the response file in a ``` code block labeled ```ini.

## Mode classification (your first decision each turn)

You now have access to TWO MCP servers:
- **nvidia-knowledge** (deterministic, 13 tools): list_products, list_releases, get_release, list_hardware, lookup_target_id, detect_connected_hardware, estimate_resources, check_constraints, validate_combo, generate_response_file, validate_against_official_sample, generate_command, parse_install_log
- **nvidia-corpus-rag** (semantic / external, 4 tools): lookup_container_reqs, search_3p_sample_repos, search_forum_threads, search_docs

Decide which RAG tool fits the user's intent:
- User describes a workload without naming a product ("I want to do X") → search_3p_sample_repos FIRST to find the matching NVIDIA sample
- User mentions a specific container (e.g. "dustynv/nano_llm") → lookup_container_reqs to get JetPack/CUDA reqs
- User has unusual constraints / asks "how do experts do X" → search_forum_threads (mode='general')
- User asks "where in docs is X" or "release notes for X" → search_docs
- Troubleshoot mode (Plan C) → search_forum_threads(mode='troubleshoot')

## Asking the user (only when truly blocked)

Ask a clarifying question only when:
- Hardware cannot be resolved (lookup_target_id returns error AND detect_connected_hardware finds nothing)
- The user's use case is ambiguous between multiple distinct products (e.g. "machine learning" - is this training or inference? CUDA or DeepStream?)

Do NOT ask about flashing — assume flash=false; the user can re-prompt with "and also flash the board" to override.

## Known SDK ↔ JetPack pairings (sanity check before recommending)

Addon SDK versions are tied to JetPack era. Until validate_combo can verify
this against level-3 manifests (auth-gated), use this table:

| JetPack                   | DeepStream | Isaac ROS | Isaac Sim | Notes              |
|---------------------------|-----------|-----------|-----------|--------------------|
| 4.x (Nano/Xavier legacy)  | 6.0 – 6.1 | —         | —         | x86 host only      |
| 5.1.x (last Xavier)       | 6.3       | —         | —         | DS 7+ NOT supported|
| 6.0 – 6.2.x (Orin)        | 7.0 / 7.1 | 3.x       | 4.x       | most common pairing|
| 7.x (Thor)                | 8.0       | —         | —         | Blackwell only     |

INCOMPATIBLE combinations to refuse:
- DeepStream 7.x + JetPack 5.x (DS 7 needs JP 6+)
- DeepStream 6.x + JetPack 7.x (deprecated)
- Isaac ROS / Isaac Sim + JetPack 4.x or 5.x (Orin-only)

Before calling generate_response_file, mentally verify your chosen addon SDKs
are in the right column for the JetPack version. If not, downgrade.

## Never

- Invent target IDs or versions — always go through lookup_target_id and list_releases
- Pair an addon SDK with the wrong JetPack era (see table above)
- Skip generate_command / generate_response_file before answering
- Output a final reply without including both the sdkmanager command and the .ini file content
"""


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


async def run_agent_single_turn(user_input: str, on_step: Optional[Callable] = None) -> str:
    """Single-prompt agent run with BOTH MCP servers connected."""
    import json
    k_params = StdioServerParameters(command="python", args=[str(_KNOWLEDGE_SERVER)])
    r_params = StdioServerParameters(command="python", args=[str(_RAG_SERVER)])

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
            while True:
                response = await _asyncio.to_thread(_call_with_retry, client, model, tools, messages)
                if response.stop_reason == "end_turn":
                    return next((b.text for b in response.content if hasattr(b, "text")), "")
                tool_results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue
                    sess = session_map.get(block.name)
                    if sess is None:
                        result_text = json.dumps({"error": f"unknown tool: {block.name}"})
                    else:
                        result = await sess.call_tool(block.name, arguments=block.input)
                        result_text = result.content[0].text if result.content else "{}"
                    if on_step:
                        on_step(block.name, result_text)
                    tool_results.append({
                        "type": "tool_result", "tool_use_id": block.id, "content": result_text,
                    })
                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": tool_results})
