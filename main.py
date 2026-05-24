"""NVIDIA SDK Advisor — CLI entry point.

Modes: --plan (default), --dry-run, --execute, --troubleshoot <log_path>, --full.
"""
import argparse
import asyncio
import sys

from dotenv import load_dotenv
from rich.console import Console

load_dotenv()
console = Console()


def main() -> None:
    parser = argparse.ArgumentParser(description="NVIDIA SDK Advisor — conversational SDK config agent")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--plan", action="store_true", help="Plan only — generate files, do not execute (default)")
    mode.add_argument("--dry-run", action="store_true", help="Invoke SDK Manager --query to verify .ini format")
    mode.add_argument("--execute", action="store_true", help="Actually install via SDK Manager")
    mode.add_argument("--troubleshoot", type=str, metavar="LOG_PATH",
                      help="Diagnose an SDK Manager log archive or .log file")
    mode.add_argument("--full", action="store_true",
                      help="End-to-end: configure → install → troubleshoot → fix → retry. Requires --mock-install today.")
    parser.add_argument("--mock-install", action="store_true",
                        help="Use a mocked NvSDKManager subprocess (canned failure + retry). Required for --full today.")
    parser.add_argument("--query", type=str, metavar="USER_INPUT",
                        help="Pre-supply the natural-language input for --full mode (skips the prompt).")
    parser.add_argument("--eval", nargs="?", const="smoke",
                        choices=["smoke", "reasoning", "troubleshoot"],
                        help="Run an eval suite. Default: smoke")
    args = parser.parse_args()

    from src import execution

    if args.eval:
        if args.eval == "reasoning":
            from tests.run_reasoning_eval import main as run_eval
        elif args.eval == "troubleshoot":
            from tests.run_troubleshoot_eval import main as run_eval
        else:
            from tests.run_smoke_eval import main as run_eval
        run_eval()
        return

    try:
        if args.troubleshoot:
            from src.troubleshoot import run_troubleshoot
            asyncio.run(run_troubleshoot(args.troubleshoot))
        elif args.full:
            from src.orchestrator import run_full_mode_sync
            run_full_mode_sync(user_input=args.query, mock_install=args.mock_install)
        elif args.dry_run:
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
