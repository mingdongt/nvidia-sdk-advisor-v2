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
import sys
import time
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel

console = Console()


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


def _write_mock_log(content: str, label: str) -> str:
    """Write mock log to ~/.sdk-advisor-mock/<label>-<timestamp>.log."""
    log_dir = Path.home() / ".sdk-advisor-mock"
    log_dir.mkdir(exist_ok=True)
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    log_path = log_dir / f"{label}-{timestamp}.log"
    log_path.write_text(content, encoding="utf-8")
    return str(log_path)


def run_mock_install(retry: bool = False) -> tuple[int, str]:
    """Mock NvSDKManager invocation.

    First call: exit 100 + writes failure log + streams it to console.
    Retry call: exit 0 + writes success log + streams it to console.

    Returns (exit_code, log_path).
    """
    content = MOCK_SUCCESS_LOG if retry else MOCK_FAILURE_LOG
    label = "mock-retry" if retry else "mock-install"
    log_path = _write_mock_log(content, label)
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

    # The agent should have generated .ini + .command files in output/.
    # Locate the latest one for the install step.
    from src.execution import _latest_plan_ini
    plan = _latest_plan_ini()
    if not plan:
        console.print(
            "[yellow]Configure phase did not produce a plan file in output/. "
            "The agent may have stopped before generation. Aborting --full chain.[/yellow]"
        )
        sys.exit(2)
    console.print(f"\n[green][OK][/green] Plan generated: [bold]{plan}[/bold]\n")

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
