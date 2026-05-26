"""Conversational REPL for the SDK Advisor agent.

Multi-turn loop: user types NL input -> agent decides to ask, call tools,
or present plan -> output streamed to terminal -> back to prompt.

Phase 2b migration: the inner tool-use loop is delegated to AgentShell.
This module is now strictly REPL UX (input prompting, trace rendering,
file saving). Helpers in src/agent.py (SYSTEM_PROMPT, _build_tools,
_call_with_retry) are no longer imported here.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path

from prompt_toolkit import PromptSession
from rich.console import Console
from rich.panel import Panel

from src.agent_shell import AgentShell

console = Console()

# User-facing labels for each MCP tool's live trace line. The live tool
# names that ever get dispatched come from the MCP server-side tool list;
# unknown names fall back to the bare tool name in _print_tool_step.
#
# search_forum_threads / search_docs are dead labels (the MCP wrappers
# were removed in favor of the model's native web_search). They remain
# here only to avoid changing the dict shape during the Phase 2b
# migration; G7 (dead label cleanup) removes them in Phase 2e.
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
    "lookup_container_reqs": "Looking up container requirements",
    "search_3p_sample_repos": "Searching sample repos",
    "search_forum_threads": "Searching forum threads",  # dead, see comment above
    "search_docs": "Searching NVIDIA docs",              # dead, see comment above
}


def _print_tool_step(name: str, args: dict, result_text: str) -> None:
    label = _STEP_LABELS.get(name, name)
    arg_str = ""
    if args:
        # Show first 2 args, truncated values
        items = [(k, str(v)[:50]) for k, v in list(args.items())[:2]]
        arg_str = " " + ", ".join(f"{k}={v}" for k, v in items)
    console.print(f"  [cyan]→[/cyan] [bold]{label}[/bold][dim]{arg_str}[/dim]")
    # Truncated result preview
    preview = result_text.replace("\n", " ")[:140]
    if len(result_text) > 140:
        preview += "…"
    console.print(f"    [dim]↳ {preview}[/dim]")


def _print_thought(text: str) -> None:
    """Print Claude's intermediate reasoning text (between tool calls)."""
    text = text.strip()
    if not text:
        return
    console.print(f"[italic yellow]💭 {text}[/italic yellow]")


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


async def _opening_probe(shell: AgentShell) -> str:
    """Auto-detect on startup; return an opening line to print to the user.

    Calls detect_connected_hardware directly on the knowledge MCP session.
    The structured result is currently discarded after formatting — this is
    the G4 (double probe) bug. Phase 2e will inject the result into
    shell.messages as a synthetic tool_use/tool_result pair so the agent
    doesn't re-call the tool on its first turn.
    """
    result = await shell.knowledge_session.call_tool(
        "detect_connected_hardware", arguments={}
    )
    data = json.loads(result.content[0].text)
    if not data.get("available"):
        return (
            "Hi - what NVIDIA hardware are you working with? "
            "I can also help if it's not connected yet."
        )
    devices = data.get("devices", [])
    if len(devices) == 0:
        return "No devices currently connected. What hardware are we planning for?"
    if len(devices) == 1:
        return (
            f"Detected {devices[0]['name']} connected ({devices[0]['port']}). "
            f"What do you want to do with it?"
        )
    names = ", ".join(d["name"] for d in devices)
    return f"Detected {len(devices)} devices: {names}. Which one are we configuring?"


async def run_repl() -> None:
    """Main conversational loop. Delegates the tool-use loop to AgentShell.

    The shell lives for the entire REPL session — message history, token
    budget, and tool call traces accumulate across user turns. This is the
    REPL's stateful nature: each turn can reference earlier ones.
    """
    if not os.getenv("ANTHROPIC_API_KEY"):
        console.print("[red]ANTHROPIC_API_KEY is not set. Copy .env.example to .env.[/red]")
        sys.exit(1)

    async with AgentShell(mode="repl") as shell:
        opening = await _opening_probe(shell)
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

            raw_user_input = user_input  # preserve for label generation

            # G4 (double probe) preserved: the first turn embeds the opening
            # text into the user message as natural language. The agent will
            # re-call detect_connected_hardware unnecessarily because
            # SYSTEM_PROMPT instructs it to. Phase 2e fixes this by injecting
            # the host-side probe result as a synthetic tool_result into
            # shell.messages BEFORE the first turn() — at which point the
            # opening text no longer needs to be re-embedded.
            if not shell.messages:
                user_input = f"{opening}\n\nUser response: {user_input}"
                first_input_hint = "continue conversation"

            result = await shell.turn(
                user_input,
                on_step=_print_tool_step,
                on_thinking=_print_thought,
            )
            console.print(result.text)
            saved = _save_outputs(result.text, label_hint=raw_user_input[:40])
            for kind, path in saved.items():
                console.print(f"[green]+[/green] saved {kind}: {path}")


if __name__ == "__main__":
    asyncio.run(run_repl())
