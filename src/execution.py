"""Execution mode dispatch.

Plan A implements --plan only. Plans B/C will fill in --dry-run / --execute.
"""
from rich.console import Console

console = Console()


def run_plan_mode() -> None:
    """Default mode: REPL writes .ini + .command files to output/ and exits."""
    import asyncio
    from src.repl import run_repl
    asyncio.run(run_repl())


def run_dry_run_mode() -> None:
    raise NotImplementedError("--dry-run is implemented in Plan C")


def run_execute_mode() -> None:
    raise NotImplementedError("--execute is implemented in Plan C")
