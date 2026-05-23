"""NVIDIA SDK Advisor — CLI entry point.

Default mode is --plan (REPL, generate files only). Other modes coming in later plans.
"""
import argparse
import sys

from dotenv import load_dotenv
from rich.console import Console

load_dotenv()
console = Console()


def main() -> None:
    parser = argparse.ArgumentParser(description="NVIDIA SDK Advisor — conversational SDK config agent")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--plan", action="store_true", help="Plan only — generate files, do not execute (default)")
    mode.add_argument("--dry-run", action="store_true", help="Invoke SDK Manager in dry-run mode (Plan C)")
    mode.add_argument("--execute", action="store_true", help="Actually install via SDK Manager (Plan C)")
    parser.add_argument("--eval", nargs="?", const="smoke", choices=["smoke", "reasoning"],
                        help="Run an eval suite. Default: smoke")
    args = parser.parse_args()

    from src import execution

    if args.eval:
        if args.eval == "reasoning":
            from tests.run_reasoning_eval import main as run_eval
        else:
            from tests.run_smoke_eval import main as run_eval
        run_eval()
        return

    try:
        if args.dry_run:
            execution.run_dry_run_mode()
        elif args.execute:
            execution.run_execute_mode()
        else:
            execution.run_plan_mode()
    except NotImplementedError as e:
        console.print(f"[red]Not yet implemented:[/red] {e}")
        sys.exit(2)
    except KeyboardInterrupt:
        console.print("\n[dim]interrupted[/dim]")
        sys.exit(0)


if __name__ == "__main__":
    main()
