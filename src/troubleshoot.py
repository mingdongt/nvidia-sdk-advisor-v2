"""Conversational troubleshoot mode.

Flow:
1. parse_install_log → LogDiagnosis (in-process, fast)
2. If failed_stage == 'unknown', show raw tail and exit.
3. search_forum_threads(mode='troubleshoot') via Server B (Brave Search).
   If Brave is unavailable, gracefully synthesize without forum context.
4. Claude synthesizes a fix recommendation.
5. Optional: write fix.sh + diagnosis.md to output/.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import anthropic
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from rich.console import Console
from rich.panel import Panel

from src import log_parser

_RAG_SERVER = Path(__file__).parent / "rag_server.py"

console = Console()


_TROUBLESHOOT_PROMPT_TEMPLATE = """You are NVIDIA SDK Advisor in troubleshoot mode.

The user's SDK Manager install failed. Here is the parsed diagnosis:

```json
{diagnosis}
```

Here are the top {n_threads} forum threads from forums.developer.nvidia.com that match
this error class:

{threads}

Synthesize a fix recommendation. Format:

## Diagnosis
(one-paragraph plain-English explanation of what went wrong)

## Recommended fix
```bash
# numbered shell commands the user can run
```

## Why this works
(one paragraph citing the forum thread URL(s) the recommendation is based on; if no threads were available, cite the documented behavior instead)

## Risks / when not to run
(one paragraph — if any command needs sudo, mark it; if the fix is destructive, warn explicitly)

Be specific. Cite the actual forum thread URL(s) inline when available. Do not make up commands not grounded in the diagnosis or threads.
"""


def _format_diagnosis(diag: dict) -> str:
    """Pretty-print a LogDiagnosis dict for terminal output."""
    return (
        f"Failed stage:    {diag.get('failed_stage')}\n"
        f"Error class:     {diag.get('error_class')}\n"
        f"Error signature: {diag.get('error_signature')}\n"
        f"Target:          {diag.get('target') or '(unknown)'}\n"
        f"Host OS:         {diag.get('host_os') or '(unknown)'}\n"
        f"JetPack:         {diag.get('jetpack_version') or '(unknown)'}\n"
        f"Timestamp:       {diag.get('timestamp') or '(unknown)'}\n"
        f"Last success:    {diag.get('last_successful_step') or '(none)'}"
    )


async def _search_forums(query: str, k: int = 5) -> list[dict]:
    """Invoke search_forum_threads via Server B. Returns [] if Brave unavailable."""
    params = StdioServerParameters(command="python", args=[str(_RAG_SERVER)])
    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(
                    "search_forum_threads",
                    arguments={"query": query, "k": k, "mode": "troubleshoot"},
                )
                data = json.loads(result.content[0].text)
                if "error" in data:
                    console.print(f"[dim yellow]forum search unavailable: {data['error']}[/dim yellow]")
                    return []
                return data.get("hits", [])
    except Exception as e:
        console.print(f"[dim yellow]forum search error: {e}[/dim yellow]")
        return []


def _synthesize_fix_sync(diagnosis: dict, threads: list[dict]) -> str:
    """Synchronous Anthropic call — wrapped in asyncio.to_thread by caller to avoid stdio deadlock."""
    client = anthropic.Anthropic()
    model = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
    threads_text = "\n\n".join(
        f"[{t.get('title', '?')}]({t.get('url', '')})\n{t.get('snippet', '')}"
        for t in threads
    )
    prompt = _TROUBLESHOOT_PROMPT_TEMPLATE.format(
        diagnosis=json.dumps(diagnosis, indent=2),
        n_threads=len(threads),
        threads=threads_text or "(no forum threads found — Brave Search unavailable; rely on diagnosis alone)",
    )
    resp = client.messages.create(
        model=model, max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    return next((b.text for b in resp.content if hasattr(b, "text")), "")


def _safe_filename(label: str) -> str:
    return re.sub(r"[^\w\-]", "_", label.lower())[:40] or "fix"


def _extract_fix_script(synthesized: str) -> Optional[str]:
    """Pull the ```bash ... ``` block out of the synthesized markdown."""
    m = re.search(r"```bash\s*\n(.*?)```", synthesized, re.DOTALL)
    return m.group(1).strip() if m else None


def _write_outputs(diagnosis: dict, synthesized: str, threads: list[dict]) -> dict:
    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)
    label = _safe_filename(diagnosis.get("error_class", "unknown"))

    diag_path = output_dir / f"{label}_diagnosis.md"
    threads_md = "\n".join(f"- [{t.get('title', '?')}]({t.get('url', '')})" for t in threads) or "_(no forum threads available)_"
    diag_path.write_text(
        f"# Troubleshoot diagnosis: {diagnosis.get('error_class')}\n\n"
        f"## Diagnosis\n\n```\n{_format_diagnosis(diagnosis)}\n```\n\n"
        f"## Raw excerpt\n\n```\n{diagnosis.get('raw_excerpt', '')}\n```\n\n"
        f"## Forum threads referenced\n\n{threads_md}\n\n"
        f"## Recommended fix\n\n{synthesized}\n",
        encoding="utf-8",
    )

    fix_script = _extract_fix_script(synthesized)
    fix_path = None
    if fix_script:
        fix_path = output_dir / f"{label}_fix.sh"
        fix_path.write_text(
            "#!/bin/bash\n"
            f"# Generated by SDK Advisor troubleshoot mode\n"
            f"# Error class: {diagnosis.get('error_class')}\n"
            f"# Review before running. Some commands need sudo.\n\n"
            f"{fix_script}\n",
            encoding="utf-8",
        )

    return {"diagnosis_md": str(diag_path), "fix_sh": str(fix_path) if fix_path else None}


def _confirm(question: str) -> bool:
    try:
        ans = input(f"{question} [y/N] ").strip().lower()
    except EOFError:
        return False
    return ans in ("y", "yes")


async def run_troubleshoot(
    log_path: str, auto_confirm: bool = False, write_fix: bool = True
) -> dict:
    """Top-level troubleshoot orchestrator.

    Returns dict with keys: diagnosis, forum_threads, fix_recommendation, outputs.
    """
    console.print(Panel(f"Parsing SDK Manager log: {log_path}", title="Troubleshoot mode", border_style="yellow"))

    # Step 1: parse (in-process, fast)
    diagnosis_obj = log_parser.parse_install_log(log_path)
    diagnosis = asdict(diagnosis_obj)
    console.print(_format_diagnosis(diagnosis))

    if diagnosis["failed_stage"] == "unknown":
        console.print("\n[yellow]Could not classify failure. Showing last excerpt:[/yellow]")
        console.print(diagnosis.get("raw_excerpt", ""))
        return {"diagnosis": diagnosis, "forum_threads": [], "fix_recommendation": "", "outputs": {}}

    # Step 2: search forum (troubleshoot mode)
    search_query = " ".join(diagnosis.get("search_terms", []))
    if not search_query:
        search_query = diagnosis["error_signature"][:120]
    console.print(f"\n[dim]→ search_forum_threads(query={search_query!r}, mode=troubleshoot)[/dim]")
    threads = await _search_forums(search_query, k=5)
    console.print(f"  found {len(threads)} thread(s)")

    # Step 3: synthesize (wrapped in asyncio.to_thread to avoid blocking stdio event loop)
    console.print("[dim]→ synthesizing fix recommendation...[/dim]")
    synthesized = await asyncio.to_thread(_synthesize_fix_sync, diagnosis, threads)
    console.print(Panel(synthesized, title="Recommended fix", border_style="green"))

    # Step 4: optionally write outputs
    outputs = {}
    if write_fix:
        if auto_confirm or _confirm("Write fix script + diagnosis.md to output/?"):
            outputs = _write_outputs(diagnosis, synthesized, threads)
            for kind, path in outputs.items():
                if path:
                    console.print(f"[green]✓[/green] {kind}: {path}")

    return {
        "diagnosis": diagnosis,
        "forum_threads": threads,
        "fix_recommendation": synthesized,
        "outputs": outputs,
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        console.print("[red]usage: python -m src.troubleshoot <log_path>[/red]")
        sys.exit(2)
    asyncio.run(run_troubleshoot(sys.argv[1]))
