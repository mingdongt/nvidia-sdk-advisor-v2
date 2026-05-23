"""Execution mode dispatch.

Three modes:
- run_plan_mode():     default REPL, write .ini + .command files, exit
- run_dry_run_mode():  invoke NvSDKManager --query non-interactive against latest plan
- run_execute_mode():  real install (Plan B.11)
"""
from __future__ import annotations

import getpass
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

from rich.console import Console

console = Console()


def _locate_sdkmanager_binary() -> str | None:
    """Locate NvSDKManager.exe via PATH or default install dir."""
    for name in ("NvSDKManager.exe", "sdkmanager"):
        p = shutil.which(name)
        if p:
            return p
    default = r"C:\Program Files\NVIDIA Corporation\SDK Manager\NvSDKManager.exe"
    if os.path.exists(default):
        return default
    return None


_EVENT_PATTERNS = [
    (re.compile(r"(?i)error|failed|fatal"), "error"),
    (re.compile(r"(?i)downloading"), "downloading"),
    (re.compile(r"(?i)installing|setting up"), "installing"),
    (re.compile(r"(?i)flashing|recovery"), "flashing"),
    (re.compile(r"(?i)success|complete|done"), "success"),
]


def _classify_event(line: str) -> str:
    for pat, label in _EVENT_PATTERNS:
        if pat.search(line):
            return label
    return "info"


def _stream_subprocess(proc: subprocess.Popen, on_line: Optional[Callable[[str], None]] = None) -> int:
    """Stream subprocess stdout line-by-line. Returns exit code."""
    for raw in proc.stdout:
        line = raw.rstrip("\n")
        console.print(line)
        if on_line:
            on_line(line)
    return proc.wait()


def _latest_plan_ini() -> Path | None:
    output_dir = Path("output")
    if not output_dir.exists():
        return None
    inis = sorted(output_dir.glob("*.ini"), key=lambda p: p.stat().st_mtime, reverse=True)
    return inis[0] if inis else None


def run_dry_run_mode_for_file(plan_path: Path) -> int:
    """Invoke NvSDKManager.exe --query non-interactive --response-file <plan.ini>."""
    binary = _locate_sdkmanager_binary()
    if not binary:
        console.print("[red]NvSDKManager.exe not found. --dry-run requires SDK Manager installed.[/red]")
        return 2
    if not plan_path.exists():
        console.print(f"[red]Plan file does not exist: {plan_path}[/red]")
        return 3

    cmd = [
        binary, "--cli", "--query", "non-interactive",
        "--response-file", str(plan_path),
    ]
    console.print(f"[dim]→ {' '.join(cmd)}[/dim]")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    return _stream_subprocess(proc)


def run_dry_run_mode() -> None:
    """Locate the most-recently-generated plan .ini and dry-run it."""
    plan = _latest_plan_ini()
    if not plan:
        console.print("[red]No plan files in output/. Run --plan first to generate one.[/red]")
        sys.exit(2)
    console.print(f"[bold]Dry-run on {plan}[/bold]")
    rc = run_dry_run_mode_for_file(plan)
    sys.exit(rc)


def run_plan_mode() -> None:
    """Default mode: REPL writes .ini + .command files to output/ and exits."""
    import asyncio
    from src.repl import run_repl
    asyncio.run(run_repl())


def run_execute_mode() -> None:
    """Real install. Asks for confirmation + sudo. Streams subprocess output."""
    plan = _latest_plan_ini()
    if not plan:
        console.print("[red]No plan files in output/. Run --plan first.[/red]")
        sys.exit(2)

    console.print(f"[bold]About to execute install plan: {plan}[/bold]")
    console.print(f"[yellow]This will run NvSDKManager.exe and modify your system.[/yellow]")
    try:
        confirm = input("Type 'yes' to proceed: ")
    except EOFError:
        confirm = ""
    if confirm.strip().lower() != "yes":
        console.print("[dim]aborted[/dim]")
        sys.exit(0)

    binary = _locate_sdkmanager_binary()
    if not binary:
        console.print("[red]NvSDKManager.exe not found.[/red]")
        sys.exit(2)

    sudo = ""
    if sys.platform != "win32":
        sudo = getpass.getpass("Sudo password (will not be echoed): ")

    cmd = [binary, "--cli", "--response-file", str(plan), "--exit-on-finish", "--licenses", "accept"]
    if sudo:
        cmd.extend(["--sudo-password", sudo])

    # Print command without echoing sudo
    safe_cmd = " ".join(cmd[:6]) + (" ..." if sudo else "")
    console.print(f"[dim]→ {safe_cmd}[/dim]")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)

    counters = {"info": 0, "downloading": 0, "installing": 0, "flashing": 0, "error": 0, "success": 0}

    def on_line(line: str) -> None:
        event = _classify_event(line)
        counters[event] += 1

    rc = _stream_subprocess(proc, on_line=on_line)
    console.print(f"\n[bold]Event summary:[/bold] {counters}")
    if rc != 0:
        console.print(f"[red]NvSDKManager exited with code {rc}.[/red]")
        console.print("[dim]Tip: run `python main.py --troubleshoot <log_path>` once Plan C is shipped.[/dim]")
    sys.exit(rc)
