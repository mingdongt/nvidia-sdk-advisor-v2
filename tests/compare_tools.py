"""Cross-backend tool-usage comparison report.

For each smoke-eval case, run the agent twice:
  A: SDK + Haiku 4.5  (default backend)
  B: CLI + Opus 4.7   (claude CLI subprocess)

Capture: which MCP tools fired (in order), and the final score.
Print a side-by-side table so you can see what each model actually chose to call.

Usage:
  python -m tests.compare_tools
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

load_dotenv()

from tests.run_smoke_eval import _CASES, _extract_fields, _score  # reuse logic

console = Console()


# ---------------------------------------------------------------- SDK path

def _sdk_trace(user_input: str) -> tuple[str, list[str]]:
    """Run via SDK backend (uses ANTHROPIC_MODEL from env). Collect tools via on_step."""
    os.environ["ANTHROPIC_BACKEND"] = "sdk"
    from src.agent import run_agent_single_turn
    tools: list[str] = []

    def on_step(tool_name: str, _result_text: str) -> None:
        tools.append(tool_name)

    text = asyncio.run(run_agent_single_turn(user_input, on_step=on_step))
    return text, tools


# ---------------------------------------------------------------- CLI path

def _cli_trace(user_input: str) -> tuple[str, list[str]]:
    """Run via CLI backend with stream-json. claude CLI's model defaults from its config."""
    from src.agent import SYSTEM_PROMPT
    from src.cli_backend import run_with_tools_traced
    return run_with_tools_traced(user_input, SYSTEM_PROMPT)


# ---------------------------------------------------------------- Main

def _shorten(t: str, n: int = 28) -> str:
    if not t:
        return ""
    return t if len(t) <= n else t[: n - 1] + "…"


def main() -> None:
    cases = [json.loads(line) for line in _CASES.read_text(encoding="utf-8").splitlines() if line.strip()]

    rows = []
    for i, c in enumerate(cases, 1):
        console.print(f"[dim]case {i}/{len(cases)}: {c['input'][:60]}…[/dim]")

        console.print("  [cyan]→ SDK + Haiku…[/cyan]")
        sdk_text, sdk_tools = _sdk_trace(c["input"])
        sdk_actual = _extract_fields(sdk_text)
        sdk_score, sdk_total, _ = _score(sdk_actual, c["expected"])

        console.print("  [cyan]→ CLI + Opus 4.7…[/cyan]")
        try:
            cli_text, cli_tools = _cli_trace(c["input"])
        except Exception as e:
            console.print(f"  [red]CLI failed: {e}[/red]")
            cli_text, cli_tools = "", []
        cli_actual = _extract_fields(cli_text)
        cli_score, cli_total, _ = _score(cli_actual, c["expected"])

        rows.append({
            "i": i, "input": c["input"],
            "sdk_score": f"{sdk_score}/{sdk_total}", "sdk_tools": sdk_tools,
            "cli_score": f"{cli_score}/{cli_total}", "cli_tools": cli_tools,
        })

    # Render
    table = Table(title="Tool-usage comparison: SDK+Haiku vs CLI+Opus 4.7", show_lines=True)
    table.add_column("#", width=3)
    table.add_column("Input", width=30)
    table.add_column("Haiku score", justify="center")
    table.add_column("Haiku tools (in order)")
    table.add_column("Opus score", justify="center")
    table.add_column("Opus tools (in order)")

    for r in rows:
        table.add_row(
            str(r["i"]),
            _shorten(r["input"], 30),
            r["sdk_score"],
            ", ".join(r["sdk_tools"]) or "(none)",
            r["cli_score"],
            ", ".join(r["cli_tools"]) or "(none)",
        )
    console.print(table)

    # Per-tool counts
    from collections import Counter
    sdk_counter = Counter(t for r in rows for t in r["sdk_tools"])
    cli_counter = Counter(t for r in rows for t in r["cli_tools"])
    all_tools = sorted(set(sdk_counter) | set(cli_counter))

    summary = Table(title="Tool call frequency across all 5 cases")
    summary.add_column("Tool")
    summary.add_column("Haiku", justify="right")
    summary.add_column("Opus", justify="right")
    for t in all_tools:
        summary.add_row(t, str(sdk_counter.get(t, 0)), str(cli_counter.get(t, 0)))
    console.print(summary)


if __name__ == "__main__":
    main()
