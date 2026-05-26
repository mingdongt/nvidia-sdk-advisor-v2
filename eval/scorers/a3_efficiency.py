"""A3 — Efficiency reporter.

Doesn't really "score" — efficiency is a reporting axis, not a pass/fail
axis. We report tool count, latency, input/output tokens, and an estimated
USD cost. Downstream (dashboard, regression tests) decides what's "too
expensive" relative to a baseline.

Pricing is shared with src/agent_shell.py's TokenBudget.estimated_cost_usd
to keep the cost model in one place.
"""
from __future__ import annotations

from src.agent_shell import TokenBudget


def score_efficiency(
    tool_sequence: list[str],
    input_tokens: int | None,
    output_tokens: int | None,
    cache_read_tokens: int | None,
    turns: int | None,
    latency_s: float,
    model: str,
) -> dict:
    """Build the efficiency block of a RunRecord.scores dict.

    Returns a dict with:
      - tool_count        : len(tool_sequence)
      - input_tokens      : echoed (or None if not captured)
      - output_tokens     : echoed
      - cache_read_tokens : echoed
      - turns             : echoed
      - latency_s         : echoed
      - estimated_cost_usd: per-call cost using TokenBudget pricing

    None-valued telemetry (e.g. ANTHROPIC_BACKEND=cli where usage isn't
    exposed) propagates through — we don't fabricate zero.
    """
    if input_tokens is not None or output_tokens is not None:
        budget = TokenBudget()
        budget.used_input = input_tokens or 0
        budget.used_output = output_tokens or 0
        budget.used_cache_read = cache_read_tokens or 0
        cost = round(budget.estimated_cost_usd(model), 6)
    else:
        cost = None

    return {
        "tool_count": len(tool_sequence),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "turns": turns,
        "latency_s": round(latency_s, 3),
        "estimated_cost_usd": cost,
    }
