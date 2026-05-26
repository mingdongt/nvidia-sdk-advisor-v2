"""Eval engine — load cases, run agent via AgentShell, score, write JSONL.

Post-M1 scope:
  - Runs the agent through src/agent_shell.AgentShell (not run_agent_single_turn)
    so per-turn token usage, tool call sequence, latency, and finish reason
    are captured into the RunRecord.
  - Applies A2 (compliance) + A3 (efficiency) scorers immediately after each
    case finishes.
  - Multi-arm dispatch via --arm:
      main          : AgentShell with default model (ANTHROPIC_MODEL)
      no-tools      : run_agent_single_turn with ANTHROPIC_BACKEND=cli-no-tools
                      (Opus alone baseline; no telemetry from this path)
      opus / haiku  : AgentShell with model override
      <anything else>: passes through as the arm tag for grouping

Usage:
    python -m eval.engine.runner eval/cases/L1
    python -m eval.engine.runner eval/cases/L1/smoke.jsonl --samples 3 --tag baseline
    python -m eval.engine.runner eval/cases/L1 --arm no-tools
    python -m eval.engine.runner eval/cases/L1 --arm opus --model claude-opus-4-7
"""
from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from dotenv import load_dotenv

load_dotenv()

# These imports are intentionally below load_dotenv so .env vars (especially
# ANTHROPIC_API_KEY) are visible when agent modules read them at import time.
from src.agent_shell import AgentShell, BudgetExceededError  # noqa: E402
from src.agent import run_agent_single_turn  # noqa: E402
from eval.engine.schemas import CaseSpec, RunRecord, ToolCallRecord  # noqa: E402
from eval.scorers.a1_correctness import score_correctness  # noqa: E402
from eval.scorers.a2_compliance import score_compliance  # noqa: E402
from eval.scorers.a3_efficiency import score_efficiency  # noqa: E402
from eval.scorers.a4_robustness import score_robustness  # noqa: E402


def _git_sha() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True,
        )
        return out.stdout.strip() or None
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def load_cases(paths: Iterable[Path]) -> list[CaseSpec]:
    cases: list[CaseSpec] = []
    for p in paths:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            cases.append(CaseSpec.model_validate_json(line))
    return cases


def expand_case_paths(paths: Iterable[Path]) -> list[Path]:
    out: list[Path] = []
    for path in paths:
        if path.is_dir():
            out.extend(sorted(path.rglob("*.jsonl")))
        else:
            out.append(path)
    return out


async def _run_via_shell(
    case_input: str, model: str
) -> tuple[str, list, int | None, int | None, int | None, int | None, str | None]:
    """Run a single case through AgentShell, return (text, tool_calls,
    tokens_in, tokens_out, cache_read, turns, error)."""
    try:
        async with AgentShell(mode="single_turn", model=model) as shell:
            result = await shell.turn(case_input)
        return (
            result.text,
            result.tool_calls,
            result.input_tokens,
            result.output_tokens,
            shell.budget.used_cache_read,
            result.turns_used,
            None,
        )
    except BudgetExceededError as e:
        return ("", [], None, None, None, None, f"BudgetExceededError: {e}")
    except Exception as e:
        return ("", [], None, None, None, None, f"{type(e).__name__}: {e}")


async def _run_via_wrapper(case_input: str) -> tuple[str, str | None]:
    """Run via run_agent_single_turn (used for arms that don't expose
    telemetry — cli, cli-no-tools). Returns (text, error)."""
    try:
        text = await run_agent_single_turn(case_input)
        return text, None
    except Exception as e:
        return "", f"{type(e).__name__}: {e}"


def _arm_to_backend(arm: str) -> str | None:
    """Map arm name to ANTHROPIC_BACKEND env var. Returns None for arms that
    route through AgentShell directly (no env override)."""
    if arm == "no-tools":
        return "cli-no-tools"
    if arm == "cli":
        return "cli"
    return None  # main / opus / haiku / etc. all use shell


async def run_case(
    case: CaseSpec,
    arm: str,
    sample_index: int,
    run_id: str,
    model: str,
    git_sha: str | None,
) -> RunRecord:
    started = datetime.now(timezone.utc)
    t0 = time.perf_counter()

    # Arm dispatch
    backend = _arm_to_backend(arm)
    if backend is not None:
        # cli / cli-no-tools: no telemetry available
        prev_backend = os.environ.get("ANTHROPIC_BACKEND")
        os.environ["ANTHROPIC_BACKEND"] = backend
        try:
            output_text, error = await _run_via_wrapper(case.input)
        finally:
            if prev_backend is None:
                os.environ.pop("ANTHROPIC_BACKEND", None)
            else:
                os.environ["ANTHROPIC_BACKEND"] = prev_backend
        tool_calls_raw: list = []
        tokens_in = tokens_out = cache_read = turns = None
    else:
        # Default: route through AgentShell directly to capture telemetry
        (output_text, tool_calls_raw, tokens_in, tokens_out, cache_read,
         turns, error) = await _run_via_shell(case.input, model)

    elapsed = time.perf_counter() - t0
    ended = datetime.now(timezone.utc)

    tool_sequence = [tc.name for tc in tool_calls_raw]
    tool_call_records = [
        ToolCallRecord(
            name=tc.name,
            input=tc.args,
            output_text=(tc.result_text[:500] + "...") if len(tc.result_text) > 500 else tc.result_text,
            latency_s=round(tc.latency_ms / 1000, 3),
        )
        for tc in tool_calls_raw
    ]

    # Scoring: A1 correctness + A2 compliance + A3 efficiency apply to all
    # tracks. A4 robustness applies only to L3 adversarial cases — for
    # L1/L2 the agent IS supposed to produce a command, so checking "did
    # it refuse?" would be inverted.
    a1 = score_correctness(output_text, case.expected) if case.layer != "L3" else None
    a2 = score_compliance(tool_sequence, case)
    a3 = score_efficiency(
        tool_sequence=tool_sequence,
        input_tokens=tokens_in,
        output_tokens=tokens_out,
        cache_read_tokens=cache_read,
        turns=turns,
        latency_s=elapsed,
        model=model,
    )
    a4 = score_robustness(output_text, case) if case.layer == "L3" else None

    scores: dict = {"a2_compliance": a2, "a3_efficiency": a3}
    if a1 is not None:
        scores["a1_correctness"] = a1
    if a4 is not None:
        scores["a4_robustness"] = a4

    return RunRecord(
        run_id=run_id,
        git_sha=git_sha,
        prompt_version=os.getenv("PROMPT_VERSION", "1.0.0"),
        model=model,
        arm=arm,
        sample_index=sample_index,
        case_id=case.case_id,
        case_layer=case.layer,
        case_track=case.track,
        case_input=case.input,
        started_at=started.isoformat(),
        ended_at=ended.isoformat(),
        latency_s=round(elapsed, 3),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        turns=turns,
        tool_sequence=tool_sequence,
        tool_calls=tool_call_records,
        output_text=output_text,
        scores=scores,
        error=error,
    )


def _make_run_id(tag: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    return f"{ts}_{tag}" if tag else ts


def _summarize(records: list[RunRecord]) -> None:
    """Print a compact summary of the run after writing JSONL."""
    n = len(records)
    if n == 0:
        return
    errors = sum(1 for r in records if r.error)
    total_cost = sum(
        (r.scores.get("a3_efficiency", {}) or {}).get("estimated_cost_usd") or 0.0
        for r in records
    )
    def _avg(axis: str) -> float:
        scores = [
            (r.scores.get(axis, {}) or {}).get("score")
            for r in records
        ]
        scores = [s for s in scores if s is not None]
        return sum(scores) / len(scores) if scores else 0.0

    print()
    print(f"Records: {n}  errors: {errors}")
    a1_avg = _avg("a1_correctness")
    a4_avg = _avg("a4_robustness")
    if a1_avg > 0:
        print(f"A1 correctness avg:  {a1_avg:.2f}")
    print(f"A2 compliance avg:   {_avg('a2_compliance'):.2f}")
    if a4_avg > 0 or any(r.case_layer == "L3" for r in records):
        print(f"A4 robustness avg:   {a4_avg:.2f}")
    print(f"A3 total cost (USD): ${total_cost:.4f}")
    if errors:
        print()
        print("Errors:")
        for r in records:
            if r.error:
                print(f"  [{r.case_id} sample={r.sample_index}] {r.error}")


def main() -> None:
    p = argparse.ArgumentParser(description="Eval engine — run cases, write JSONL.")
    p.add_argument("paths", nargs="+", type=Path,
                   help="Case files (.jsonl) or directories of case files.")
    p.add_argument("--arm", default="main",
                   help="Arm tag (main / no-tools / opus / haiku / ...). "
                        "no-tools and cli skip the shell; others route through AgentShell.")
    p.add_argument("--model", default=None,
                   help="Override ANTHROPIC_MODEL for this run (used by --arm opus etc.).")
    p.add_argument("--samples", type=int, default=1, help="Samples per case.")
    p.add_argument("--tag", default="", help="Run-id tag suffix.")
    p.add_argument("--out-dir", type=Path, default=Path("eval/runs"))
    p.add_argument("--dry-run", action="store_true",
                   help="Print plan without invoking the agent.")
    args = p.parse_args()

    case_files = expand_case_paths(args.paths)
    cases = load_cases(case_files)
    if not cases:
        raise SystemExit("No cases loaded.")
    print(f"Loaded {len(cases)} cases from {len(case_files)} file(s).")

    model = args.model or os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
    git_sha = _git_sha()
    total = len(cases) * args.samples

    if args.dry_run:
        print(f"DRY RUN: would run {total} (case × sample) on arm={args.arm}, model={model}")
        for c in cases:
            print(f"  - {c.case_id} [{c.layer}/{c.track}]: {c.input[:60]}...")
        return

    run_id = _make_run_id(args.tag)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.out_dir / f"{run_id}.jsonl"

    records: list[RunRecord] = []
    done = 0
    with out_path.open("w", encoding="utf-8") as f:
        for case in cases:
            for sample in range(args.samples):
                done += 1
                print(f"[{done}/{total}] {case.case_id} (arm={args.arm}, sample={sample})...")
                record = asyncio.run(
                    run_case(case, args.arm, sample, run_id, model, git_sha)
                )
                f.write(record.model_dump_json() + "\n")
                f.flush()
                records.append(record)
                if record.error:
                    msg = record.error
                else:
                    parts = [f"latency {record.latency_s}s", f"tools {len(record.tool_sequence)}"]
                    a1 = record.scores.get("a1_correctness")
                    a2 = record.scores.get("a2_compliance")
                    a4 = record.scores.get("a4_robustness")
                    if a1 is not None:
                        parts.append(f"a1 {a1['score']:.2f}")
                    if a2 is not None:
                        parts.append(f"a2 {a2['score']:.2f}")
                    if a4 is not None:
                        parts.append(f"a4 {a4['score']:.2f}")
                    cost = (record.scores.get("a3_efficiency", {}) or {}).get("estimated_cost_usd")
                    if cost is not None:
                        parts.append(f"cost ${cost:.4f}")
                    msg = " · ".join(parts)
                print(f"  {msg}")

    print(f"\nWrote {done} records to {out_path}")
    _summarize(records)


if __name__ == "__main__":
    main()
