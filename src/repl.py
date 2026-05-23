"""Conversational REPL for the SDK Advisor agent.

Multi-turn loop: user types NL input -> agent decides to ask, call tools, or
present plan -> output streamed to terminal -> back to prompt.
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path
from typing import Optional

import anthropic
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from prompt_toolkit import PromptSession
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

from src.agent import SYSTEM_PROMPT, _build_tools, _call_with_retry

_KNOWLEDGE_SERVER = Path(__file__).parent / "knowledge_server.py"
console = Console()

_STEP_LABELS = {
    "list_products": "Listing products",
    "list_releases": "Listing releases",
    "get_release": "Fetching release metadata",
    "list_hardware": "Listing hardware",
    "lookup_target_id": "Resolving hardware name",
    "detect_connected_hardware": "Detecting connected hardware",
    "estimate_resources": "Estimating resources",
    "check_constraints": "Checking constraints",
    "generate_response_file": "Generating response file",
    "validate_against_official_sample": "Validating against template",
    "generate_command": "Generating command",
}


def _print_tool_step(name: str, result_text: str) -> None:
    label = _STEP_LABELS.get(name, name)
    console.print(f"  [cyan]->[/cyan] {label}")


def _safe_filename(label: str) -> str:
    return re.sub(r"[^\w\-]", "_", label.lower())[:40] or "plan"


def _save_outputs(final_text: str, label_hint: str) -> dict:
    """Extract generated command + ini from the assistant's final text and save them."""
    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)
    safe = _safe_filename(label_hint)

    saved = {}
    blocks = re.findall(r"```[^\n]*\n(.*?)```", final_text, re.DOTALL)
    for block in blocks:
        stripped = block.strip()
        if stripped.startswith("sdkmanager"):
            path = output_dir / f"{safe}.command"
            path.write_text(stripped, encoding="utf-8")
            saved["command"] = path
        elif stripped.startswith("[client_arguments]"):
            path = output_dir / f"{safe}.ini"
            path.write_text(stripped, encoding="utf-8")
            saved["ini"] = path
    return saved


async def _opening_probe(session: ClientSession) -> str:
    """Auto-detect on startup; return an opening line to print to the user."""
    result = await session.call_tool("detect_connected_hardware", arguments={})
    import json
    data = json.loads(result.content[0].text)
    if not data.get("available"):
        return "Hi - what NVIDIA hardware are you working with? I can also help if it's not connected yet."
    devices = data.get("devices", [])
    if len(devices) == 0:
        return "No devices currently connected. What hardware are we planning for?"
    if len(devices) == 1:
        return f"Detected {devices[0]['name']} connected ({devices[0]['port']}). What do you want to do with it?"
    names = ", ".join(d["name"] for d in devices)
    return f"Detected {len(devices)} devices: {names}. Which one are we configuring?"


async def run_repl() -> None:
    """Main conversational loop."""
    if not os.getenv("ANTHROPIC_API_KEY"):
        console.print("[red]ANTHROPIC_API_KEY is not set. Copy .env.example to .env.[/red]")
        sys.exit(1)

    params = StdioServerParameters(command="python", args=[str(_KNOWLEDGE_SERVER)])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools_result = await session.list_tools()
            tools = _build_tools(tools_result.tools)

            client = anthropic.Anthropic()
            model = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
            messages: list[dict] = []

            opening = await _opening_probe(session)
            console.print(Panel(opening, title="NVIDIA SDK Advisor", border_style="green"))

            prompt_session = PromptSession()
            first_input_hint = "describe hardware + use case"

            while True:
                try:
                    user_input = await prompt_session.prompt_async(f"[{first_input_hint}] > ")
                except (EOFError, KeyboardInterrupt):
                    console.print("\n[dim]goodbye[/dim]")
                    return
                if not user_input.strip():
                    continue
                if user_input.strip().lower() in ("exit", "quit"):
                    return

                if not messages:
                    user_input = f"{opening}\n\nUser response: {user_input}"
                    first_input_hint = "continue conversation"
                messages.append({"role": "user", "content": user_input})

                # Inner tool-use loop
                while True:
                    response = _call_with_retry(client, model, tools, messages)
                    if response.stop_reason == "end_turn":
                        final_text = next((b.text for b in response.content if hasattr(b, "text")), "")
                        console.print(final_text)
                        saved = _save_outputs(final_text, label_hint=user_input[:40])
                        for kind, path in saved.items():
                            console.print(f"[green]+[/green] saved {kind}: {path}")
                        messages.append({"role": "assistant", "content": response.content})
                        break
                    tool_results = []
                    for block in response.content:
                        if block.type != "tool_use":
                            continue
                        result = await session.call_tool(block.name, arguments=block.input)
                        result_text = result.content[0].text if result.content else "{}"
                        _print_tool_step(block.name, result_text)
                        tool_results.append({
                            "type": "tool_result", "tool_use_id": block.id, "content": result_text,
                        })
                    messages.append({"role": "assistant", "content": response.content})
                    messages.append({"role": "user", "content": tool_results})


if __name__ == "__main__":
    asyncio.run(run_repl())
