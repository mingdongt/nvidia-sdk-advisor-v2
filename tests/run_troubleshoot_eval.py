"""Troubleshoot track eval: LLM-as-judge against expected fix summaries."""
import asyncio
import io
import json
import os
import statistics
import sys
import tempfile
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

load_dotenv()

# Force UTF-8 stdout/stderr on Windows (default code page is often GBK/cp936).
# Must happen before any Rich Console or print() calls that may render Unicode.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
os.environ["PYTHONUTF8"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"

# Suppress chromadb / sentence-transformers progress noise
os.environ.setdefault("TQDM_DISABLE", "1")
os.environ.setdefault("CHROMA_TELEMETRY_DISABLED", "1")

from src.troubleshoot import run_troubleshoot  # noqa: E402

console = Console()
_CASES = Path(__file__).parent / "eval_cases" / "troubleshoot.jsonl"


_JUDGE_PROMPT = """You are evaluating a troubleshoot agent's fix recommendation against an expert reference.

Log snippet (input):
{log}

Expected error class: {expected_error_class}
Expected fix summary (expert reference): {expected_fix}

Agent's recommendation:
{agent_output}

Rate the agent on FOUR axes (1-5 each):
1. Error correctly identified — did the agent classify the same failed stage / error class as the reference?
2. Fix matches expert reference — does the recommendation aim at the same root cause and proposed solution path?
3. Fix is actionable — are the proposed commands runnable (specific commands, not vague advice)?
4. Safety — for any destructive or sudo command, is there an explicit warning?

Return ONLY a JSON object: {{"identified": 4, "matches_expert": 3, "actionable": 5, "safety": 4}}.
"""


def _run_one_case(case: dict) -> dict:
    """Write log to temp file, run troubleshoot, return result dict."""
    with tempfile.NamedTemporaryFile(suffix=".log", delete=False, mode="w", encoding="utf-8") as f:
        f.write(case["log_inline"])
        log_path = f.name
    try:
        return asyncio.run(run_troubleshoot(log_path, auto_confirm=False, write_fix=False))
    except Exception as e:
        console.print(f"[red]troubleshoot error: {e}[/red]")
        return {"diagnosis": {}, "forum_threads": [], "fix_recommendation": f"(orchestrator error: {e})", "outputs": {}}
    finally:
        try:
            os.unlink(log_path)
        except OSError:
            pass


def _judge(client: anthropic.Anthropic, case: dict, agent_output: str) -> dict:
    import re
    prompt = _JUDGE_PROMPT.format(
        log=case["log_inline"], expected_error_class=case["expected_error_class"],
        expected_fix=case["expected_fix_summary"], agent_output=agent_output,
    )
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        console.print(f"[yellow]judge error: {e}[/yellow]")
        return {"identified": 0, "matches_expert": 0, "actionable": 0, "safety": 0}
    text = next((b.text for b in resp.content if hasattr(b, "text")), "")
    m = re.search(r"\{[^}]+\}", text, re.DOTALL)
    if not m:
        return {"identified": 0, "matches_expert": 0, "actionable": 0, "safety": 0}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"identified": 0, "matches_expert": 0, "actionable": 0, "safety": 0}


def main() -> None:
    cases = [json.loads(line) for line in _CASES.read_text(encoding="utf-8").splitlines() if line.strip()]
    client = anthropic.Anthropic()

    table = Table(title="Troubleshoot eval (LLM-as-judge, 3x median)")
    for col in ("#", "Error class", "Ident", "Matches", "Actionable", "Safety", "Avg"):
        table.add_column(col)

    scores_all = {"identified": [], "matches_expert": [], "actionable": [], "safety": []}

    for i, c in enumerate(cases, 1):
        console.print(f"[dim]running case {i}/{len(cases)} ({c['expected_error_class']})...[/dim]")
        result = _run_one_case(c)
        agent_output = result.get("fix_recommendation", "")

        per_run = []
        for _ in range(3):
            per_run.append(_judge(client, c, agent_output))
        median = {k: statistics.median([r[k] for r in per_run]) for k in scores_all}

        for k in median:
            scores_all[k].append(median[k])
        avg = sum(median.values()) / 4
        table.add_row(
            str(i), c["expected_error_class"][:24],
            f"{median['identified']:.1f}", f"{median['matches_expert']:.1f}",
            f"{median['actionable']:.1f}", f"{median['safety']:.1f}",
            f"{avg:.2f}",
        )

    console.print(table)
    overall = sum(sum(v) / len(v) for v in scores_all.values()) / 4
    console.print(f"\n[bold]Overall:[/bold] {overall:.2f}/5 (target >= 3.5)")
    if overall < 3.5:
        sys.exit(1)


if __name__ == "__main__":
    main()
