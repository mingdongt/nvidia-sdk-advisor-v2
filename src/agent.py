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


SYSTEM_PROMPT = """You are NVIDIA SDK Advisor — a conversational agent helping a developer pick the right SDK Manager configuration for their hardware and use case.

You have access to MCP tools that talk to NVIDIA's own catalog and detect connected hardware. Your reasoning loop:

1. If hardware is unknown, call detect_connected_hardware first. If that finds nothing useful, ask the user.
2. Resolve any board name to a canonical target_id via lookup_target_id.
3. List products/releases as needed to ground your recommendation.
4. If the user gave resource constraints (disk, RAM), call estimate_resources and check_constraints. If constraints are violated, propose specific trade-offs.
5. When you have product+version+target+flash decision settled, call generate_response_file and generate_command.
6. Present the final plan as a brief summary plus the generated command in a code block.

Never:
- Invent target IDs or versions — always go through lookup_target_id / list_releases
- Silently assume flash=true (flashing reformats the board — always ask)
- Skip the validation step (validate_against_official_sample after generation)
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
    """Single-prompt agent run. Used by tests; REPL has its own loop."""
    params = StdioServerParameters(command="python", args=[str(_KNOWLEDGE_SERVER)])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools_result = await session.list_tools()
            tools = _build_tools(tools_result.tools)

            client = anthropic.Anthropic()
            model = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
            messages = [{"role": "user", "content": user_input}]

            while True:
                response = _call_with_retry(client, model, tools, messages)
                if response.stop_reason == "end_turn":
                    return next((b.text for b in response.content if hasattr(b, "text")), "")
                tool_results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue
                    result = await session.call_tool(block.name, arguments=block.input)
                    result_text = result.content[0].text if result.content else "{}"
                    if on_step:
                        on_step(block.name, result_text)
                    tool_results.append({
                        "type": "tool_result", "tool_use_id": block.id, "content": result_text,
                    })
                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": tool_results})
