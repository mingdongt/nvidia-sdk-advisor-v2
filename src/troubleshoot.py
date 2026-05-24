"""Conversational troubleshoot mode.

Flow:
1. parse_install_log → LogDiagnosis (in-process, fast).
2. If failed_stage == 'unknown', show raw tail and exit.
3. Claude synthesizes a fix recommendation grounded in real NVIDIA-official
   sources. Web search is mandatory and domain-restricted to:
     - forums.developer.nvidia.com
     - docs.nvidia.com
     - developer.nvidia.com
   - SDK backend: web_search_20250305 server-side tool attached (paid per call;
     falls back to training-knowledge synthesis with a disclaimer if the tool
     is unavailable, e.g. region-restricted).
   - CLI backend: Claude CLI's built-in WebSearch handles the same role.
4. Optional: write fix.sh + diagnosis.md to output/.
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

Search terms hand-curated for this error class: {search_terms}

## What you MUST do

Before recommending any fix, issue at least one `web_search` call to look up how this error class is solved in practice. The search tool is restricted to NVIDIA-official domains: `forums.developer.nvidia.com`, `docs.nvidia.com`, `developer.nvidia.com`.

Construct queries from the search terms above plus the error signature. Examples:

- `"nvidia-jetpack apt unable to locate"`
- `"flash failed errCode" Orin recovery`

If the first search returns nothing useful, refine and try again (up to 5 searches). Only AFTER you have grounded the recommendation in at least one real source should you write the fix.

## Output format

## Diagnosis
(one-paragraph plain-English explanation of what went wrong)

## Recommended fix
```bash
# numbered shell commands the user can run
```

## Why this works
(one paragraph — cite the actual forum thread URL(s) or doc page URL(s) you retrieved via web_search. If web search returned nothing usable, say so explicitly and fall back to general principles; do NOT pretend to cite a source you did not retrieve.)

## Risks / when not to run
(one paragraph — if any command needs sudo, mark it; if the fix is destructive, warn explicitly)

Be specific. Cite the actual URLs inline. Do not make up commands not grounded in retrieved sources.
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
    """Forum retrieval is deferred to the underlying Claude's native web search
    during synthesis. We no longer maintain a dedicated MCP tool for this — the
    synthesis prompt includes a `site:forums.developer.nvidia.com` hint and Claude
    decides whether to issue a WebSearch call.

    This function is kept as a no-op stub for backward compatibility with the
    orchestrator's flow; the synthesized fix references the diagnosis directly
    plus any pattern-derived search_terms.
    """
    return []


def _synthesize_fix_sync(diagnosis: dict, threads: list[dict]) -> str:
    """Synthesize fix via the configured ANTHROPIC_BACKEND.

    - 'sdk' (default): Anthropic Python SDK
    - 'cli':           claude CLI subprocess (uses subscription, avoids API quota)
    - 'cli-no-tools':  claude CLI subprocess, no MCP (same as 'cli' here since
                       this function doesn't need tools — synthesis is one-shot)

    `threads` is kept as a parameter for backward compatibility with the orchestrator;
    in the current design _search_forums returns [] (forum retrieval deferred to
    Claude's native web search at synthesis time).
    """
    search_terms = diagnosis.get("search_terms") or []
    prompt = _TROUBLESHOOT_PROMPT_TEMPLATE.format(
        diagnosis=json.dumps(diagnosis, indent=2),
        search_terms=", ".join(search_terms) if search_terms else "(none — diagnosis is generic)",
    )

    backend = os.getenv("ANTHROPIC_BACKEND", "sdk").lower()
    if backend in ("cli", "cli-no-tools"):
        # synthesis doesn't need tools — use the no-tools CLI path
        from src.cli_backend import run_no_tools
        return run_no_tools(prompt, timeout=180)

    # Default: SDK path with mandatory web_search tool.
    # web_search_20250305 is a server-side tool — Anthropic performs the search
    # internally; no client-side dispatch loop needed. Results are embedded as
    # citations in the response content.
    client = anthropic.Anthropic()
    model = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
    tools = [{
        "type": "web_search_20250305",
        "name": "web_search",
        "max_uses": 5,
        "allowed_domains": [
            "forums.developer.nvidia.com",
            "docs.nvidia.com",
            "developer.nvidia.com",
        ],
    }]
    try:
        resp = client.messages.create(
            model=model, max_tokens=2000,
            tools=tools,
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.BadRequestError as e:
        # web_search may be region-restricted or require explicit opt-in.
        # Fall back to no-tool synthesis with an honest disclaimer in the prompt.
        console.print(f"[yellow]web_search unavailable ({type(e).__name__}); falling back to training-knowledge-only synthesis[/yellow]")
        resp = client.messages.create(
            model=model, max_tokens=2000,
            messages=[{
                "role": "user",
                "content": prompt + "\n\n[SYSTEM NOTE: web_search is unavailable in this run; ground your fix in the diagnosis and your training knowledge, and say so explicitly in the 'Why this works' section.]",
            }],
        )
    # Concatenate ALL text blocks. With server-side web_search, the response
    # has interleaved text + server_tool_use + web_search_tool_result blocks;
    # the final fix recommendation is in later text blocks after the searches.
    text_parts = [b.text for b in resp.content if getattr(b, "type", None) == "text" and getattr(b, "text", None)]
    return "\n\n".join(text_parts)


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
    try:
        console.print(Panel(synthesized, title="Recommended fix", border_style="green"))
    except (UnicodeEncodeError, UnicodeDecodeError):
        # Fallback for terminals with narrow encoding (e.g. Windows GBK)
        safe = synthesized.encode("ascii", errors="replace").decode("ascii")
        console.print(Panel(safe, title="Recommended fix", border_style="green"))

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
