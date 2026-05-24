"""List which tools each smoke-eval case invokes (CLI backend, Opus 4.7).

Run with:
  python -m tests.list_smoke_tools_cli

No env vars needed — script explicitly forces ANTHROPIC_MODEL=claude-opus-4-7
for the CLI subprocess. Plain stdout (no rich/unicode quirks).

Each case takes ~30-60s because claude CLI spawns the MCP servers fresh
per --print invocation (cold-start cost includes sentence-transformers load).
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Force Opus 4.7 for the CLI invocation, regardless of .env's ANTHROPIC_MODEL
os.environ["ANTHROPIC_MODEL"] = "claude-opus-4-7"

from tests.run_smoke_eval import _CASES, _extract_fields, _score
from src.agent import SYSTEM_PROMPT
from src.cli_backend import run_with_tools_traced


def main() -> None:
    cases = [json.loads(line) for line in _CASES.read_text(encoding="utf-8").splitlines() if line.strip()]

    print(f"backend=cli  model=claude-opus-4-7")
    print("=" * 90)

    all_results = []
    aggregate = Counter()

    for i, c in enumerate(cases, 1):
        print(f"\n[case {i}/{len(cases)} starting] {c['input'][:60]}...", flush=True)
        try:
            response, tools = run_with_tools_traced(c["input"], SYSTEM_PROMPT, timeout=600)
        except Exception as e:
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
