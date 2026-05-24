"""End-to-end orchestrator: --full mode.

Chains the four verbs into one continuous flow:

  configure → install → (if fail) troubleshoot → apply fix → retry → verify

The --mock-install flag swaps the real NvSDKManager subprocess for a
deterministic mock that:
  - on first invocation: returns exit 100 + emits a canned apt-failure log
  - on retry (after fix.sh):  returns exit 0 + emits a canned success log

This is for end-to-end demo recording without a connected Jetson. It is
explicitly tagged in --full output and in the README. Not honest enough
to call 'a real install'.

Real-hardware --full mode (without --mock-install) is a future addition;
today it would just shell out to NvSDKManager.exe and stream output, with
all the safety considerations that --execute already implements.
"""
from __future__ import annotations

import asyncio
import re
import sys
import time
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel

console = Console()


# Demo defaults baked into mock log filenames so log_parser extracts metadata.
# The mock filename follows SDK Manager's official export convention:
#   SDKM_logs_JetPack_<jp>_<host>_for_Jetson_<board>_<date>_<time>.log
# log_parser's _FILENAME_RE recognizes this pattern.
_MOCK_TARGET_BOARD = "Orin_NX_16GB"
_MOCK_JETPACK = "6.1"
_MOCK_HOST = "Linux"


def _query_to_filename(user_input: str) -> str:
    """Turn a free-text user query into a filesystem-safe filename stem."""
    stem = re.sub(r"[^\w\-]", "_", user_input.lower())[:40].strip("_")
    return stem or "plan"


def _extract_code_blocks(text: str) -> dict[str, str]:
    """Extract sdkmanager command + .ini content from agent's chat response.

    Returns dict with keys 'command' and 'ini' (either may be missing).
    The chat-rendered .ini may omit [section] headers — see SYSTEM_PROMPT
    in src/agent.py which now requires them, but real-world rendering can
    still drop blank-line spacing. We save whatever is in the code block.
    """
    out: dict[str, str] = {}
    # Match ```<lang>\n<content>``` blocks
    for m in re.finditer(r"```([^\n]*)\n(.*?)```", text, re.DOTALL):
        lang = m.group(1).strip().lower()
        body = m.group(2).strip()
        if "sdkmanager" in body[:200] or lang == "bash":
            if body.startswith("sdkmanager"):
                out["command"] = body
        if lang == "ini" or "[client_arguments]" in body[:200] or "action = install" in body[:200]:
            out["ini"] = body
    return out


def _save_agent_artifacts(response_text: str, user_input: str) -> dict[str, Optional[Path]]:
    """Extract code blocks from agent response and save as .command / .ini files.

    Returns {'command': Path or None, 'ini': Path or None}.
    """
    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)
    stem = _query_to_filename(user_input)

    blocks = _extract_code_blocks(response_text)
    saved: dict[str, Optional[Path]] = {"command": None, "ini": None}

    if cmd := blocks.get("command"):
        path = output_dir / f"{stem}.command"
        path.write_text(cmd, encoding="utf-8")
        saved["command"] = path

    if ini := blocks.get("ini"):
        path = output_dir / f"{stem}.ini"
        path.write_text(ini, encoding="utf-8")
        saved["ini"] = path

    return saved


# Canned mock log: a first-time apt failure that troubleshoot can recover from.
# Mirrors the format of real SDK Manager logs (a tail of timestamped lines plus
# the apt error block). Real install log format is closely modeled but not
# byte-identical to any single real run — see README for the honesty disclaimer.
MOCK_FAILURE_LOG = """\
=== SDK Manager Install Log ===
target: JETSON_ORIN_NX_TARGETS
Host OS: ubuntu22.04
JetPack: 6.1
2026-05-24 14:30:00 SDK Manager 2.4.0 starting
2026-05-24 14:30:02 Reading manifest for Jetson 6.1
2026-05-24 14:30:05 Detecting target hardware via USB
2026-05-24 14:30:08 Target: JETSON_ORIN_NX_TARGETS detected
2026-05-24 14:31:12 Downloading nvidia-jetpack-runtime
2026-05-24 14:31:18 Download complete (size 1.2GB)
2026-05-24 14:32:05 Setting up apt sources
2026-05-24 14:33:18 Running apt-get update
2026-05-24 14:33:20 apt-get update completed (took 2s)
2026-05-24 14:33:21 Running apt-get install nvidia-jetpack
Reading package lists... Done
Building dependency tree... Done
E: Unable to locate package nvidia-jetpack=6.1*
E: Couldn't find any package by glob 'nvidia-jetpack=6.1*'
2026-05-24 14:33:21 apt-get install failed with exit code 100
2026-05-24 14:33:21 Install aborted at stage: target_install (apt)
"""

MOCK_SUCCESS_LOG = """\
=== SDK Manager Install Log (retry after fix.sh) ===
target: JETSON_ORIN_NX_TARGETS
Host OS: ubuntu22.04
JetPack: 6.1
2026-05-24 14:45:00 SDK Manager 2.4.0 starting
2026-05-24 14:45:02 Reading manifest for Jetson 6.1
2026-05-24 14:45:08 Target: JETSON_ORIN_NX_TARGETS detected
2026-05-24 14:46:12 Downloading nvidia-jetpack-runtime
2026-05-24 14:46:18 Download complete (cached, 0s)
2026-05-24 14:47:05 Setting up apt sources (now includes r36.4 from fix.sh)
2026-05-24 14:47:18 Running apt-get update
2026-05-24 14:47:20 apt-get update completed
2026-05-24 14:47:21 Running apt-get install nvidia-jetpack
Reading package lists... Done
Building dependency tree... Done
The following packages will be installed:
  nvidia-jetpack nvidia-l4t-bsp nvidia-l4t-multimedia
Setting up nvidia-jetpack (6.1) ...
Setting up nvidia-l4t-bsp (6.1) ...
2026-05-24 14:50:00 SUCCESS: all components installed
2026-05-24 14:50:00 Install complete.
"""


def _write_mock_log(content: str, retry: bool = False) -> str:
    """Write mock log to ~/.sdk-advisor-mock/<SDKM-export-style-name>.log.

    Uses SDK Manager's official export filename convention so log_parser
    extracts target / JetPack / host_os / timestamp from the filename
    (the parser is filename-driven by design — see src/log_parser.py).

    Format:
      SDKM_logs_JetPack_<jp>_<host>_for_Jetson_<board>_<date>_<time>.log
    """
    log_dir = Path.home() / ".sdk-advisor-mock"
    log_dir.mkdir(exist_ok=True)
    date = time.strftime("%Y-%m-%d")
    # Retry log gets a slightly later time stamp so the two demo logs are
    # ordered correctly when listed.
    hms = time.strftime("%H-%M-%S")
    name = (
        f"SDKM_logs_JetPack_{_MOCK_JETPACK}_{_MOCK_HOST}_"
        f"for_Jetson_{_MOCK_TARGET_BOARD}_{date}_{hms}.log"
    )
    log_path = log_dir / name
    log_path.write_text(content, encoding="utf-8")
    return str(log_path)


def run_mock_install(retry: bool = False) -> tuple[int, str]:
    """Mock NvSDKManager invocation.

    First call: exit 100 + writes failure log + streams it to console.
    Retry call: exit 0 + writes success log + streams it to console.

    Returns (exit_code, log_path).
    """
    content = MOCK_SUCCESS_LOG if retry else MOCK_FAILURE_LOG
    # Stagger the timestamp on retry so the two log filenames differ.
    if retry:
        time.sleep(1)
    log_path = _write_mock_log(content, retry=retry)
    for line in content.splitlines():
        console.print(f"[dim]{line}[/dim]")
    rc = 0 if retry else 100
    return rc, log_path


async def run_full_mode(
    user_input: Optional[str] = None,
    mock_install: bool = True,
) -> None:
    """End-to-end orchestration: configure → install → troubleshoot → fix → retry.

    With --mock-install (the only mode currently supported): swaps NvSDKManager
    subprocess for canned mock returns. The first execute attempt fails with
    an apt error that troubleshoot recovers from; the retry succeeds.
    """
    if not mock_install:
        console.print(
            "[red]--full without --mock-install requires real hardware and is not "
            "yet implemented. Use --full --mock-install for the demo flow.[/red]"
        )
        sys.exit(2)

    console.print(Panel(
        "[bold]--full mode (mocked install)[/bold]\n"
        "Five phases: configure → install → troubleshoot → fix → retry.\n"
        "The install steps are mocked — see README for what that means.",
        title="NVIDIA SDK Advisor — end-to-end",
        border_style="cyan",
    ))

    # ─── PHASE 1: Configure ─────────────────────────────────────────────
    console.print()
    console.print(Panel("PHASE 1 / 5 — Configure  [REAL: agent + MCP + .ini generation]", border_style="cyan"))
    from src.agent import run_agent_single_turn

    if user_input is None:
        try:
            user_input = input("\nDescribe your hardware and use case: ").strip()
        except EOFError:
            console.print("[red]No input provided.[/red]")
            sys.exit(2)
    if not user_input:
        console.print("[red]Empty input.[/red]")
        sys.exit(2)

    console.print(f"\n[dim]→ agent: {user_input}[/dim]\n")
    response = await run_agent_single_turn(user_input)
    console.print(Panel(response, title="Configure result", border_style="green"))

    # Extract the agent's sdkmanager command + .ini code blocks from the chat
    # response and save them to output/ with filenames derived from the user's
    # query. (run_agent_single_turn returns text only — file saving is the
    # orchestrator's job in --full mode.)
    saved = _save_agent_artifacts(response, user_input)
    plan = saved["ini"]
    if not plan:
        console.print(
            "[yellow]Configure phase response did not include an .ini code block. "
            "Agent may not have called generate_response_file. Aborting --full chain.[/yellow]"
        )
        sys.exit(2)
    for kind, path in saved.items():
        if path:
            console.print(f"[green][OK][/green] {kind} saved: [bold]{path}[/bold]")
    console.print()

    # ─── PHASE 2: Install (mocked) ──────────────────────────────────────
    console.print(Panel("PHASE 2 / 5 — Install  [MOCKED: canned failure log, no NvSDKManager subprocess]", border_style="yellow"))
    console.print("[yellow]Note: install is mocked — no real subprocess to NvSDKManager.exe.[/yellow]\n")

    rc, failure_log = run_mock_install(retry=False)
    if rc == 0:
        console.print("\n[green]Install succeeded on first attempt (unexpected for the demo mock).[/green]")
        console.print("[dim]Skipping phases 3-5.[/dim]")
        return
    console.print(f"\n[red]Install exited with code {rc}. Log: {failure_log}[/red]\n")

    # ─── PHASE 3: Troubleshoot ──────────────────────────────────────────
    console.print(Panel("PHASE 3 / 5 — Troubleshoot  [REAL: agent + web_search + fix.sh generation]", border_style="cyan"))
    from src.troubleshoot import run_troubleshoot

    result = await run_troubleshoot(failure_log, auto_confirm=True, write_fix=True)

    outputs = result.get("outputs", {})
    fix_path = outputs.get("fix_sh")
    if not fix_path:
        console.print(
            "\n[yellow]Troubleshoot did not produce a fix.sh — agent may not have found "
            "an executable remediation. Aborting --full chain.[/yellow]"
        )
        sys.exit(0)
    console.print(f"\n[green][OK][/green] Fix script generated: [bold]{fix_path}[/bold]\n")

    # ─── PHASE 4: Apply fix (simulated) ─────────────────────────────────
    console.print(Panel("PHASE 4 / 5 — Apply fix  [SIMULATED: prints command, does NOT bash fix.sh]", border_style="yellow"))
    console.print(f"[dim]Would run: bash {fix_path}[/dim]")
    console.print(
        "[yellow]Note: fix.sh execution is simulated. A real --full mode would "
        "prompt the user to review fix.sh before running it under sudo.[/yellow]\n"
    )

    # ─── PHASE 5: Retry install (mocked) ────────────────────────────────
    console.print(Panel("PHASE 5 / 5 — Retry install  [MOCKED: canned success log]", border_style="yellow"))
    rc2, success_log = run_mock_install(retry=True)

    if rc2 == 0:
        console.print()
        console.print(Panel(
            f"[bold green][OK] End-to-end install complete (mocked).[/bold green]\n\n"
            f"Configure → install → troubleshoot → fix → retry: all five phases reached.\n"
            f"Final log: {success_log}",
            border_style="green",
        ))
    else:
        console.print()
        console.print(Panel(
            f"[red]Retry failed (code {rc2}).[/red]\n"
            f"A real --full mode would re-enter troubleshoot here.",
            border_style="red",
        ))

    console.print(
        "\n[dim]This run used --mock-install. Real-hardware --full mode is on the roadmap; "
        "see README → Troubleshoot evolution roadmap.[/dim]"
    )


def run_full_mode_sync(user_input: Optional[str] = None, mock_install: bool = True) -> None:
    """Sync wrapper for main.py dispatch."""
    try:
        asyncio.run(run_full_mode(user_input=user_input, mock_install=mock_install))
    except KeyboardInterrupt:
        console.print("\n[dim]interrupted[/dim]")
        sys.exit(0)
