"""Reasoning track eval: LLM-as-judge against forum-expert reference replies."""
import asyncio
import json
import os
import re
import statistics
import sys
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

load_dotenv()

# Suppress chromadb / sentence-transformers progress noise
os.environ.setdefault("TQDM_DISABLE", "1")
os.environ.setdefault("CHROMA_TELEMETRY_DISABLED", "1")

from src.agent import run_agent_single_turn  # noqa: E402

console = Console()
_CASES = Path(__file__).parent / "eval_cases" / "reasoning.jsonl"

_JUDGE_PROMPT = """You are evaluating an agent's reply against a reference expert reply.

User input: {user_input}

Reference expert reply: {expert_reply}

Agent's reply: {agent_reply}

Rate the agent's reply on FOUR axes (1-5 each):
1. Factual correctness vs reference (does it state the same hardware/version facts?)
2. Reasoning quality (does it identify the same trade-offs?)
3. Constraints respected (does it honor any user-stated budget/limit? If no constraints stated, rate 5 by default.)
4. INI validity (is the generated .ini self-consistent and parseable as a 3-section file? If no INI generated, rate 3 as neutral.)

Return ONLY a JSON object like {{"factual": 4, "reasoning": 3, "constraints": 5, "ini_validity": 4}}.
"""


def _judge(client: anthropic.Anthropic, user_input: str, expert_reply: str, agent_reply: str) -> dict:
    prompt = _JUDGE_PROMPT.format(
        user_input=user_input, expert_reply=expert_reply, agent_reply=agent_reply,
    )
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        console.print(f"[yellow]judge error: {e}[/yellow]")
        return {"factual": 0, "reasoning": 0, "constraints": 0, "ini_validity": 0}
    text = next((b.text for b in resp.content if hasattr(b, "text")), "")
    m = re.search(r"\{[^}]+\}", text, re.DOTALL)
    if not m:
        return {"factual": 0, "reasoning": 0, "constraints": 0, "ini_validity": 0}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"factual": 0, "reasoning": 0, "constraints": 0, "ini_validity": 0}


def main() -> None:
    cases = [json.loads(line) for line in _CASES.read_text(encoding="utf-8").splitlines() if line.strip()]
    client = anthropic.Anthropic()

    table = Table(title="Reasoning eval (LLM-as-judge, 3x median)")
    for c in ("#", "Input", "Factual", "Reasoning", "Constr", "INI", "Avg"):
        table.add_column(c)

    scores_all = {"factual": [], "reasoning": [], "constraints": [], "ini_validity": []}

    for i, c in enumerate(cases, 1):
        console.print(f"[dim]running case {i}/{len(cases)}...[/dim]")
        try:
            agent_reply = asyncio.run(run_agent_single_turn(c["input"]))
        except Exception as e:
            console.print(f"[red]agent error case {i}: {e}[/red]")
            agent_reply = f"(agent error: {e})"

        # 3 runs, median per axis
        per_run = []
        for _ in range(3):
            per_run.append(_judge(client, c["input"], c["expert_reply"], agent_reply))
        median_scores = {k: statistics.median([r[k] for r in per_run]) for k in scores_all}

        for k in median_scores:
            scores_all[k].append(median_scores[k])
        avg = sum(median_scores.values()) / 4
        table.add_row(
            str(i), c["input"][:38],
            f"{median_scores['factual']:.1f}",
            f"{median_scores['reasoning']:.1f}",
            f"{median_scores['constraints']:.1f}",
            f"{median_scores['ini_validity']:.1f}",
            f"{avg:.2f}",
        )

    console.print(table)
    overall_avg = sum(sum(v) / len(v) for v in scores_all.values()) / 4
    console.print(f"\n[bold]Overall average:[/bold] {overall_avg:.2f}/5 (target >= 3.5)")
    if overall_avg < 3.5:
        sys.exit(1)


if __name__ == "__main__":
    main()
