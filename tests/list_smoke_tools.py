"""List which tools each smoke-eval case invokes (SDK backend, on_step tracing).

Run with:
  $env:ANTHROPIC_BACKEND="sdk"
  $env:ANTHROPIC_MODEL="claude-haiku-4-5-20251001"   # or claude-sonnet-4-6 / claude-opus-4-7
  python -m tests.list_smoke_tools

Outputs:
  - per-case tool list (in invocation order)
  - per-case score
  - aggregate frequency table
  - all in plain ASCII (no rich/unicode quirks)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from tests.run_smoke_eval import _CASES, _extract_fields, _score
from src.agent import run_agent_single_turn


def main() -> None:
    cases = [json.loads(line) for line in _CASES.read_text(encoding="utf-8").splitlines() if line.strip()]

    backend = os.getenv("ANTHROPIC_BACKEND", "sdk")
    model = os.getenv("ANTHROPIC_MODEL", "(default)")
    print(f"backend={backend}  model={model}")
    print("=" * 90)

    all_results = []
    aggregate = Counter()

    for i, c in enumerate(cases, 1):
        tools: list[str] = []

        def on_step(tool_name: str, _args: dict, _result_text: str) -> None:
            tools.append(tool_name)

        try:
            response = asyncio.run(run_agent_single_turn(c["input"], on_step=on_step))
        except Exception as e:
            print(f"\nCase {i}: {c['input']}")
            print(f"  ERROR: {e}")
            continue

        actual = _extract_fields(response)
        score, total, _misses = _score(actual, c["expected"])
        all_results.append((i, c["input"], tools, score, total))
        aggregate.update(tools)

        print(f"\nCase {i}: {c['input']}")
        print(f"  score: {score}/{total}")
        print(f"  tools ({len(tools)}): {', '.join(tools) if tools else '(none)'}")

    print("\n" + "=" * 90)
    print("Aggregate tool call frequency (across all 5 cases):")
    for tool, count in sorted(aggregate.items(), key=lambda kv: -kv[1]):
        print(f"  {count:3d}  {tool}")

    if all_results:
        total_score = sum(s for _, _, _, s, _ in all_results)
        total_max = sum(t for _, _, _, _, t in all_results)
        print(f"\nOverall: {total_score}/{total_max} = {100*total_score/total_max:.1f}%")


if __name__ == "__main__":
    main()
