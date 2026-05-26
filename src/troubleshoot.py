"""Conversational troubleshoot mode.

Flow:
1. parse_install_log → LogExcerpt (in-process, fast). This is purely
   structural: filename metadata + tail of log content. No pre-classification.
2. Show the user the extracted context.
3. Claude reads the raw log tail directly and identifies the failure
   itself. Web search is used to ground the fix recommendation in real
   expert sources (forum threads, docs, Stack Exchange).
4. Optional: write fix.sh + diagnosis.md to output/.

Design rationale: we deliberately do NOT pre-classify errors into
'error_class' or 'stage' tags. Earlier versions did, with a curated
log_patterns.yaml — but every classification we encoded was either a
guess (the NVIDIA-specific strings) or redundant (the agent could read
the same content itself). The simpler architecture lets the agent
identify failures from the actual log text, exactly what a human
expert does when triaging a forum post.

## Why this module does NOT use AgentShell

troubleshoot's call shape is fundamentally different from the main
agent loop covered by src/agent_shell.py:

  - Single Anthropic call per session (not a multi-turn tool-use loop)
  - Server-side web_search_20250305 tool (NO MCP)
  - Entire context as one user message (no SYSTEM_PROMPT)
  - Response blocks include server_tool_use + web_search_tool_result
    types that AgentShell's MCP-oriented dispatch doesn't recognize

Forcing troubleshoot into AgentShell would require adding spawn_mcp /
extra_tools / system_prompt_override parameters plus a second
block-handling code path — the abstraction would harm clarity, not
improve it. Instead we SHARE the G9 infrastructure (TokenBudget,
imported from agent_shell) and document the boundary explicitly.
See docs/agent-design.md for the broader refactor design.
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import anthropic
from rich.console import Console
from rich.panel import Panel

from src import log_parser

console = Console()


_TROUBLESHOOT_PROMPT_TEMPLATE = """You are NVIDIA SDK Advisor in troubleshoot mode.

The user's SDK Manager install failed. Here is what we extracted from their
log file (deterministically — filename metadata + raw log tail). The agent
(you) is responsible for identifying the failure itself.

## Context (parsed from filename)

- Log archive: {source_path}
- Target board: {target_or_unknown}
- JetPack version: {jetpack_or_unknown}
- Host OS: {host_os_or_unknown}
- Export timestamp: {timestamp_or_unknown}
- Files scanned: {file_count} ({size_kb} KB total)

## Log tail (last ~200 lines, verbatim)

```
{tail_text}
```

## What you MUST do

1. Read the log tail above carefully. Identify the actual failure: what
   line(s) describe the error, what stage the install was in, what tool
   was running.
2. Use `web_search` (mandatory, at least one call) to find expert fixes
   for the failure you identified. Construct queries from the actual
   error strings you see in the log. Prefer authoritative sources:
     - `forums.developer.nvidia.com` (NVIDIA's own forums, accepted answers)
     - `docs.nvidia.com` / `developer.nvidia.com`
     - `askubuntu.com` (apt / dpkg / Linux issues)
     - `stackoverflow.com`, `unix.stackexchange.com`, `serverfault.com`
     - `github.com` issues from NVIDIA-AI-IOT, dusty-nv, NVIDIA-ISAAC-ROS
3. If a search returns nothing relevant, refine and try again (up to 5
   searches total).
4. Skip AI-generated SEO blogs / content farms.

## Output format

## Diagnosis
(one paragraph: what the log shows actually went wrong — be specific
about which line(s) you read this from)

## Recommended fix
```bash
# numbered shell commands the user can run
```

## Why this works
(one paragraph; cite the actual URL(s) you retrieved via web_search. If
web_search returned nothing usable, say so explicitly and fall back to
log evidence — do NOT invent citations.)

## Risks / when not to run
(one paragraph; flag sudo / destructive commands explicitly)

Be specific. Cite the actual URLs inline. Do not invent commands not
grounded in the log content or in retrieved sources.
"""


def _format_excerpt_for_terminal(excerpt: dict) -> str:
    """Pretty-print a LogExcerpt dict for terminal output (before synthesis)."""
    size_kb = excerpt.get("total_size_bytes", 0) / 1024
    tail = excerpt.get("tail_text", "")
    tail_lines = tail.count("\n") + 1 if tail else 0
    return (
        f"Target:      {excerpt.get('target') or '(filename did not encode)'}\n"
        f"JetPack:     {excerpt.get('jetpack_version') or '(filename did not encode)'}\n"
        f"Host OS:     {excerpt.get('host_os') or '(filename did not encode)'}\n"
        f"Timestamp:   {excerpt.get('timestamp') or '(filename did not encode)'}\n"
        f"Files:       {excerpt.get('file_count', 0)} ({size_kb:.1f} KB total)\n"
        f"Tail lines:  {tail_lines}"
    )


def _build_prompt(excerpt: dict) -> str:
    return _TROUBLESHOOT_PROMPT_TEMPLATE.format(
        source_path=excerpt.get("source_path", "(unknown)"),
        target_or_unknown=excerpt.get("target") or "(unknown — filename did not encode)",
        jetpack_or_unknown=excerpt.get("jetpack_version") or "(unknown)",
        host_os_or_unknown=excerpt.get("host_os") or "(unknown)",
        timestamp_or_unknown=excerpt.get("timestamp") or "(unknown)",
        file_count=excerpt.get("file_count", 0),
        size_kb=f"{excerpt.get('total_size_bytes', 0) / 1024:.1f}",
        tail_text=excerpt.get("tail_text") or "(log file empty or unreadable)",
    )


def _synthesize_fix_sync(excerpt: dict) -> tuple[str, dict]:
    """Synthesize fix via the configured ANTHROPIC_BACKEND.

    - 'sdk' (default): Anthropic Python SDK + server-side web_search tool
    - 'cli':           claude CLI subprocess (subscription, uses Claude CLI's
                       built-in WebSearch)
    - 'cli-no-tools':  claude CLI subprocess, no tools (baseline; agent
                       relies on training knowledge only)

    Returns (synthesized_markdown_text, usage_info_dict). usage_info has
    keys: input_tokens / output_tokens / cache_read_tokens /
    estimated_cost_usd / backend / web_search_fallback. For the CLI
    backends, usage counts are 0 because the subscription path does not
    expose them — backend='cli' / 'cli-no-tools' marks that case so
    downstream telemetry can skip the row instead of recording a misleading
    zero-cost call.
    """
    # G9 (capture per-API-call token usage) applied to the troubleshoot
    # path too — see the "Why this module does NOT use AgentShell" docstring
    # for why we share TokenBudget rather than route through the shell.
    from src.agent_shell import TokenBudget
    budget = TokenBudget()

    prompt = _build_prompt(excerpt)
    backend = os.getenv("ANTHROPIC_BACKEND", "sdk").lower()
    model = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")

    if backend in ("cli", "cli-no-tools"):
        from src.cli_backend import run_no_tools
        text = run_no_tools(prompt, timeout=180)
        return text, {
            "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
            "estimated_cost_usd": 0.0, "backend": backend,
            "web_search_fallback": False,
        }

    # Default: SDK path with server-side web_search.
    client = anthropic.Anthropic()
    tools = [{
        "type": "web_search_20250305",
        "name": "web_search",
        "max_uses": 5,
    }]
    web_search_fallback = False
    try:
        resp = client.messages.create(
            model=model, max_tokens=2000,
            tools=tools,
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.BadRequestError as e:
        console.print(
            f"[yellow]web_search unavailable ({type(e).__name__}); "
            f"falling back to training-knowledge-only synthesis[/yellow]"
        )
        web_search_fallback = True
        resp = client.messages.create(
            model=model, max_tokens=2000,
            messages=[{
                "role": "user",
                "content": prompt + (
                    "\n\n[SYSTEM NOTE: web_search is unavailable in this run; "
                    "ground the fix in the log evidence and your training "
                    "knowledge, and say so explicitly in 'Why this works'.]"
                ),
            }],
        )

    if resp.usage is not None:
        budget.add_usage(resp.usage)

    # Stream-print the response blocks in their natural order (reasoning,
    # web_search calls, URLs, more reasoning, ...) so the demo viewer sees
    # the agent's troubleshoot work *as steps*, matching Phase 1's rhythm.
    # Returns the FULL concatenated synthesis (all text blocks joined) so
    # the caller can save it verbatim to diagnosis.md + extract the bash
    # block via _extract_fix_script.
    text = _stream_print_troubleshoot_response(resp)
    return text, {
        "input_tokens": budget.used_input,
        "output_tokens": budget.used_output,
        "cache_read_tokens": budget.used_cache_read,
        "estimated_cost_usd": budget.estimated_cost_usd(model),
        "backend": backend,
        "web_search_fallback": web_search_fallback,
    }


def _step_pause() -> None:
    """Demo-only pause after each printed step. Gated on SDK_ADVISOR_STEP_PAUSE
    (seconds); silent in normal CLI use. Duplicated from orchestrator.py to
    avoid a circular import (orchestrator imports troubleshoot)."""
    pause = os.getenv("SDK_ADVISOR_STEP_PAUSE")
    if pause:
        try:
            import time
            time.sleep(float(pause))
        except ValueError:
            pass


def _stream_print_troubleshoot_response(resp) -> str:
    """Walk response.content in order, stream-printing each block with a
    step pause so demo viewers see the troubleshoot work as discrete steps.

    Returns the FULL concatenated synthesis (all text blocks joined) so:
      - the caller can save it verbatim to diagnosis.md
      - _extract_fix_script can find the bash code block (which may be in any
        text block, not just the last — earlier versions tried "last block only"
        and lost the fix when the model put Risks/Why-this-works last)

    Block types we expect (Anthropic server-side web_search):
      - text                       — reasoning OR diagnosis content
      - server_tool_use            — a web_search invocation
      - web_search_tool_result     — the URLs that came back
    """
    step = 0
    text_parts: list[str] = []
    for b in getattr(resp, "content", []) or []:
        btype = getattr(b, "type", None)
        if btype == "text":
            text = getattr(b, "text", "")
            stripped = text.strip()
            if not stripped:
                continue
            text_parts.append(text)
            # Show a compact one-line preview in the live trace so the demo
            # has visible progression here too, without duplicating the full
            # diagnosis content (the full text gets shown later via the
            # diagnosis Panel rendered by the caller).
            compact = " ".join(stripped.split())
            if len(compact) > 200:
                compact = compact[:197] + "..."
            console.print(f"  [italic dim cyan]reasoning:[/italic dim cyan] [italic]{compact}[/italic]")
            _step_pause()
        elif btype == "server_tool_use" and getattr(b, "name", None) == "web_search":
            step += 1
            q = (getattr(b, "input", {}) or {}).get("query", "")
            q_show = q if len(q) <= 90 else q[:87] + "..."
            console.print(f"  [bold cyan]\\[{step}][/bold cyan] [cyan]→[/cyan] web_search([dim]query=[/dim]{q_show!r})")
            _step_pause()
        elif btype == "web_search_tool_result":
            urls: list[str] = []
            for r in getattr(b, "content", []) or []:
                url = getattr(r, "url", None) or (r.get("url") if isinstance(r, dict) else None)
                if url:
                    urls.append(url)
            for url in urls[:3]:
                short = url.replace("https://", "").replace("http://", "")
                if len(short) > 88:
                    short = short[:85] + "..."
                console.print(f"        [green]←[/green] [dim]{short}[/dim]")
            if len(urls) > 3:
                console.print(f"        [dim]... +{len(urls) - 3} more[/dim]")
            _step_pause()

    if step == 0:
        console.print("[dim]web_search: (no calls — agent answered from log evidence alone)[/dim]")

    return "\n\n".join(text_parts)


def _safe_filename(label: str) -> str:
    return re.sub(r"[^\w\-]", "_", label.lower())[:40] or "fix"


def _extract_fix_script(synthesized: str) -> Optional[tuple[str, str]]:
    """Pull the first executable code block out of the synthesized markdown.

    Returns (script_text, extension) or None. Supports bash and PowerShell —
    when the user's host is Windows the agent often writes powershell/winget
    commands, not bash. Extension is '.sh' or '.ps1' accordingly.
    """
    # Try language-tagged blocks in priority order
    for lang, ext in [("bash", ".sh"), ("sh", ".sh"),
                       ("powershell", ".ps1"), ("pwsh", ".ps1"), ("ps1", ".ps1")]:
        m = re.search(rf"```{lang}\s*\n(.*?)```", synthesized, re.DOTALL | re.IGNORECASE)
        if m:
            return (m.group(1).strip(), ext)
    return None


def _label_from_excerpt(excerpt: dict) -> str:
    """Derive a filename label from the excerpt (target + jp + timestamp)."""
    target = (excerpt.get("target") or "unknown").lower()
    target = re.sub(r"^jetson_", "", target).replace("_targets", "")
    jp = excerpt.get("jetpack_version") or "unk"
    return _safe_filename(f"{target}_jp{jp}")


def _write_outputs(excerpt: dict, synthesized: str) -> dict:
    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)
    label = _label_from_excerpt(excerpt)

    diag_path = output_dir / f"{label}_diagnosis.md"
    diag_path.write_text(
        f"# Troubleshoot diagnosis: {label}\n\n"
        f"## Extracted context\n\n```\n{_format_excerpt_for_terminal(excerpt)}\n```\n\n"
        f"## Log tail (last ~200 lines)\n\n```\n{excerpt.get('tail_text', '')}\n```\n\n"
        f"## Recommended fix\n\n{synthesized}\n",
        encoding="utf-8",
    )

    fix_extract = _extract_fix_script(synthesized)
    fix_path = None
    if fix_extract:
        fix_script, ext = fix_extract
        fix_path = output_dir / f"{label}_fix{ext}"
        if ext == ".sh":
            header = (
                "#!/bin/bash\n"
                f"# Generated by SDK Advisor troubleshoot mode\n"
                f"# Source log: {excerpt.get('source_path', '?')}\n"
                f"# Review before running. Some commands need sudo.\n\n"
            )
        else:  # .ps1
            header = (
                f"# Generated by SDK Advisor troubleshoot mode (PowerShell)\n"
                f"# Source log: {excerpt.get('source_path', '?')}\n"
                f"# Review before running. Some commands need Administrator privileges.\n\n"
            )
        fix_path.write_text(header + fix_script + "\n", encoding="utf-8")

    # Return key reflects actual extension produced
    return {
        "diagnosis_md": str(diag_path),
        "fix_script": str(fix_path) if fix_path else None,
    }


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

    Returns dict with keys: excerpt, fix_recommendation, outputs.
    """
    console.print(Panel(
        f"Parsing SDK Manager log: {log_path}",
        title="Troubleshoot mode", border_style="yellow",
    ))

    # Step 1: structural extraction (in-process, deterministic)
    excerpt_obj = log_parser.parse_install_log(log_path)
    excerpt = asdict(excerpt_obj)
    console.print(_format_excerpt_for_terminal(excerpt))

    if not excerpt.get("tail_text"):
        console.print("\n[red]Log file empty or unreadable.[/red]")
        return {"excerpt": excerpt, "fix_recommendation": "", "outputs": {}}

    # Step 2: agent reads the tail + searches web + synthesizes (one call).
    # _synthesize_fix_sync stream-prints reasoning + web_search activity in
    # order (with per-step pauses), then returns the final synthesized
    # fix + a usage dict so the caller can expose telemetry alongside the
    # text result.
    console.print("\n[dim]→ synthesizing diagnosis + fix (streaming agent's reasoning + web_search):[/dim]\n")
    synthesized, usage = await asyncio.to_thread(_synthesize_fix_sync, excerpt)
    console.print()
    try:
        console.print(Panel(synthesized, title="Diagnosis + fix", border_style="green"))
    except (UnicodeEncodeError, UnicodeDecodeError):
        safe = synthesized.encode("ascii", errors="replace").decode("ascii")
        console.print(Panel(safe, title="Diagnosis + fix", border_style="green"))
    # Optional demo-only pause so the final Panel stays on screen long enough
    # before --full mode prints the next phase panel.
    # Controlled by SDK_ADVISOR_DEMO_PAUSE (seconds); silent in normal use.
    _pause = os.getenv("SDK_ADVISOR_DEMO_PAUSE")
    if _pause:
        try:
            await asyncio.sleep(float(_pause))
        except ValueError:
            pass

    # Step 3: optionally write outputs
    outputs = {}
    if write_fix:
        if auto_confirm or _confirm("Write fix script + diagnosis.md to output/?"):
            outputs = _write_outputs(excerpt, synthesized)
            for kind, path in outputs.items():
                if not path:
                    continue
                try:
                    console.print(f"[green]✓[/green] {kind}: {path}")
                except (UnicodeEncodeError, UnicodeDecodeError):
                    # Windows GBK console can't render ✓; use ASCII fallback.
                    console.print(f"[green]+[/green] {kind}: {path}")

    return {
        "excerpt": excerpt,
        "fix_recommendation": synthesized,
        "outputs": outputs,
        "usage": usage,  # G9: per-session token + cost telemetry
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        console.print("[red]usage: python -m src.troubleshoot <log_path>[/red]")
        sys.exit(2)
    asyncio.run(run_troubleshoot(sys.argv[1]))
