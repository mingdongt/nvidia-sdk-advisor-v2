"""Conversational REPL for the SDK Advisor agent.

Multi-turn loop: user types NL input -> agent decides to ask, call tools,
or present plan -> output streamed to terminal -> back to prompt.

Phase 2b migration: the inner tool-use loop is delegated to AgentShell.
Phase 2e cleanup: removed the host-side hardware probe (G4 fix) and
the dead _STEP_LABELS entries for tools that no longer exist (G7 fix).
This module is now strictly REPL UX (input prompting, trace rendering,
file saving). Helpers in src/agent.py (SYSTEM_PROMPT, _build_tools,
_call_with_retry) are no longer imported here.
"""
from __future__ import annotations

import asyncio
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


def _opening_message() -> str:
    """Return the static REPL welcome line.

    Phase 2e (G4 fix): previously this function ran a host-side
    detect_connected_hardware call to personalize the opener ("Detected
    Orin NX..."), then the agent would re-call the same tool on its first
    turn — wasting one subprocess probe per session AND fighting G1's
    sliding-window strategy (any synthetic state injected to suppress the
    re-probe would get pruned anyway after max_user_turns).

    Cleaner architecture: the agent does its own detection on the first
    turn via SYSTEM_PROMPT's standing instruction. The opener becomes
    generic. UX cost is small (less personalized first line) and the
    probe still happens, just owned by the agent loop.
    """
    return (
        "Hi - what NVIDIA hardware are you working with? "
        "I'll detect connected devices when we get started."
    )


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
        console.print(Panel(
            _opening_message(), title="NVIDIA SDK Advisor", border_style="green",
        ))

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

            if not shell.messages:
                first_input_hint = "continue conversation"

            result = await shell.turn(
                user_input,
                on_step=_print_tool_step,
                on_thinking=_print_thought,
            )
            console.print(result.text)
            saved = _save_outputs(result.text, label_hint=user_input[:40])
            for kind, path in saved.items():
                console.print(f"[green]+[/green] saved {kind}: {path}")


if __name__ == "__main__":
    asyncio.run(run_repl())
