"""A5 — Capability scorer (LLM-as-judge).

For free-text reasoning quality where there's no structure to score on,
fall back to an LLM judge. Specifically used for L2 reasoning-track cases
that have an `expert_reply` in `case.expected` — a reference answer the
agent's output should be measured against.

## Why Sonnet-as-judge (and not Haiku-as-judge or Opus-as-judge)

The legacy `tests/run_reasoning_eval.py` used Haiku to judge Haiku's own
output, which has documented same-family bias (Zheng et al. 2023,
MT-Bench: ~5-10pp inflated scores when judge and judged share a model
family). This scorer defaults to `claude-sonnet-4-6` as judge so a
Haiku-agent run gets evaluated by a different model tier.

**The intended judge was originally `claude-opus-4-7`** — strictly the
strongest cross-tier signal — but the Tier 1 Anthropic quota on Opus
(low TPM) makes a 60-call judge sweep (20 cases × 3 samples) unreachable
even with 4-attempt exponential backoff: every call returned
`rate_limit_after_4_retries` on the first try. Sonnet 4.6 fits the same
"different tier than the agent" criterion (Haiku 4.5 vs Sonnet 4.6 are
distinct training rooftops in the Anthropic 4.x lineup) with a quota
that actually permits the run. The cross-tier bias question is less
studied than cross-vendor (e.g. GPT-4 judging Claude); future work
should add a cross-vendor judge option.

When `--arm opus` runs the agent itself with Opus, the cross-tier check
collapses — a future iteration should let `--a5-judge-model` pin to a
different vendor entirely for that arm.

## Median over samples (variance dampening)

Each judge call returns a 4-axis 1-5 rating. We take `samples` calls
(default 3) and use the per-axis median to dampen judge noise. The
agent's reply is NOT re-sampled — A5 measures judge variance against
ONE agent reply, not agent variance. Agent variance is the eval
engine's `--samples N` concern, not the scorer's.
"""
from __future__ import annotations

import json
import os
import re
import statistics
import time
from typing import Optional

import anthropic

from eval.engine.schemas import CaseSpec


JUDGE_PROMPT = """You are evaluating an agent's reply against a reference expert reply.

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


DEFAULT_JUDGE_MODEL = "claude-sonnet-4-6"
DEFAULT_SAMPLES = 3
_ZERO_AXES = {"factual": 0, "reasoning": 0, "constraints": 0, "ini_validity": 0}


def _parse_judge_response(text: str) -> dict | None:
    """Pull the first JSON object out of the judge's reply.

    The judge is instructed to return only JSON, but models occasionally
    wrap with prose. Regex-grab the first `{...}` block and try to parse.
    Returns None on any failure (caller treats as a 0-score sample).
    """
    m = re.search(r"\{[^}]+\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def _judge_once(
    client: anthropic.Anthropic,
    judge_model: str,
    user_input: str,
    expert_reply: str,
    agent_reply: str,
    max_attempts: int = 4,
) -> tuple[dict, Optional[str]]:
    """One judge call with exponential backoff on rate limit.

    Returns (axes_dict, error). On success, error is None. On failure,
    axes_dict is the all-zeros sentinel and error describes what went
    wrong (rate_limit, parse_error, api_error). The error string is
    bubbled into the per-sample record so debugging can spot WHY a
    zero appeared, vs the previous silent-swallow behavior.

    Opus has tighter RPM/TPM limits than Haiku (~20k TPM); chained A5
    calls easily trip them without backoff. The 15 * (attempt + 1)
    schedule matches src/agent_shell.py:_call_with_retry.
    """
    prompt = JUDGE_PROMPT.format(
        user_input=user_input,
        expert_reply=expert_reply,
        agent_reply=agent_reply,
    )
    for attempt in range(max_attempts):
        try:
            resp = client.messages.create(
                model=judge_model,
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.RateLimitError:
            if attempt == max_attempts - 1:
                return dict(_ZERO_AXES), f"rate_limit_after_{max_attempts}_retries"
            time.sleep(15 * (attempt + 1))
            continue
        except Exception as e:
            return dict(_ZERO_AXES), f"{type(e).__name__}: {str(e)[:160]}"

        text = next((b.text for b in resp.content if hasattr(b, "text")), "")
        parsed = _parse_judge_response(text)
        if not parsed:
            return dict(_ZERO_AXES), f"unparseable: {text[:120]}"
        # Coerce missing keys to 0 so median math doesn't crash
        return {axis: int(parsed.get(axis, 0)) for axis in _ZERO_AXES}, None

    return dict(_ZERO_AXES), "exhausted_retries"


def score_capability(
    output_text: str,
    case: CaseSpec,
    judge_model: str = DEFAULT_JUDGE_MODEL,
    samples: int = DEFAULT_SAMPLES,
    client: Optional[anthropic.Anthropic] = None,
) -> dict:
    """LLM-as-judge scorer for L2 reasoning-track cases.

    Applies only to cases that carry an `expert_reply` in `case.expected`.
    Skips otherwise — returns score=None, axes filled with None, samples=[].

    Returns:
        {
            "score": float in [0.0, 1.0] (avg of 4 axes / 5), or None if skipped,
            "axes": {factual, reasoning, constraints, ini_validity} — medians,
            "samples": list[dict] — per-sample raw 4-axis ratings,
            "judge_model": str,
            "samples_taken": int,
            "skipped_reason": str | None,
        }
    """
    expert_reply = (case.expected or {}).get("expert_reply")
    if not expert_reply:
        return {
            "score": None, "axes": None, "samples": [],
            "judge_model": judge_model, "samples_taken": 0,
            "skipped_reason": "case has no expert_reply in expected",
        }

    if client is None:
        if not os.getenv("ANTHROPIC_API_KEY"):
            return {
                "score": None, "axes": None, "samples": [],
                "judge_model": judge_model, "samples_taken": 0,
                "skipped_reason": "ANTHROPIC_API_KEY not set",
            }
        client = anthropic.Anthropic()

    raw_samples: list[dict] = []
    errors: list[str] = []
    for _ in range(samples):
        axes_dict, err = _judge_once(
            client=client, judge_model=judge_model,
            user_input=case.input, expert_reply=expert_reply,
            agent_reply=output_text,
        )
        # Embed error context into the sample dict so JSONL retains the
        # full debug trail rather than silently dropping failed calls.
        sample_entry = dict(axes_dict)
        if err:
            sample_entry["_error"] = err
            errors.append(err)
        raw_samples.append(sample_entry)

    # Per-axis median across samples (axes-only; _error key skipped)
    axes = {
        axis: statistics.median([s.get(axis, 0) for s in raw_samples])
        for axis in _ZERO_AXES
    }
    # Overall score: avg of 4 axes, normalized to [0, 1]
    avg_5 = sum(axes.values()) / len(axes)
    score = round(avg_5 / 5.0, 3)

    # If EVERY sample errored, score is meaningless — surface that.
    all_failed = len(errors) == len(raw_samples)
    return {
        "score": None if all_failed else score,
        "axes": None if all_failed else {k: float(v) for k, v in axes.items()},
        "samples": raw_samples,
        "judge_model": judge_model,
        "samples_taken": len(raw_samples),
        "skipped_reason": (
            f"all {len(errors)} judge calls failed; first error: {errors[0]}"
            if all_failed else None
        ),
    }
