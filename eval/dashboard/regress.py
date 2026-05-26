"""Cross-run regression detector — compare two JSONL eval runs, fail on
axis deltas worse than configured tolerance.

Pair this with M5's arm dispatch and a CI hook to enforce a release gate:
the `baseline` JSONL is the last-known-good (committed under
`eval/runs/baseline/<arm>.jsonl` or similar); the `candidate` JSONL is
what the current PR produced. Exit code is 1 if any axis on any
(case_id, arm, layer) drops by more than the threshold; 0 otherwise.

Usage:
    python -m eval.dashboard.regress baseline.jsonl candidate.jsonl
    python -m eval.dashboard.regress baseline.jsonl candidate.jsonl --threshold 0.1
    python -m eval.dashboard.regress baseline.jsonl candidate.jsonl --axes a1 a2 a4

Considerations:
  - L3 cases compare A4 (robustness) — a 0.5 drop is severe (means an
    adversarial case that used to be rejected now produces a command).
  - L1 cases compare A1 (correctness) and A2 (compliance).
  - Default threshold is 0.05 — generous enough that single-sample
    judge noise doesn't trip the gate, tight enough to catch real
    regressions on the deterministic axes.

What this does NOT check:
  - Cost regressions (A3) — those deserve a separate budget-aware tool.
  - New cases that appear in candidate but not baseline (treated as N/A).
  - Cases that appear in baseline but not candidate (treated as DROPPED;
    counts as a regression by default).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path


# Axes that produce a comparable numeric "score" in record.scores.<axis>.score
DEFAULT_AXES = ("a1_correctness", "a2_compliance", "a4_robustness")
DEFAULT_THRESHOLD = 0.05


def _load(path: Path) -> list[dict]:
    if not path.exists():
        print(f"error: {path} not found", file=sys.stderr)
        sys.exit(2)
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        out.append(json.loads(line))
    return out


def _record_key(r: dict) -> tuple[str, str]:
    """(case_id, arm) uniquely identifies a record for cross-run comparison.

    Sample index is intentionally NOT in the key — we average across samples
    before comparing, because per-sample variance is noise.
    """
    return (r.get("case_id", "?"), r.get("arm", "main"))


def _axis_score(r: dict, axis: str) -> float | None:
    s = (r.get("scores") or {}).get(axis)
    if not isinstance(s, dict):
        return None
    return s.get("score")


def _group_and_average(records: list[dict], axes: tuple[str, ...]) -> dict[tuple, dict[str, float]]:
    """Group records by (case_id, arm), average each axis across samples."""
    buckets: dict[tuple, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for r in records:
        key = _record_key(r)
        for axis in axes:
            v = _axis_score(r, axis)
            if v is not None:
                buckets[key][axis].append(v)
    return {
        key: {axis: sum(vals) / len(vals) for axis, vals in axes_vals.items()}
        for key, axes_vals in buckets.items()
    }


def compare(
    baseline: list[dict],
    candidate: list[dict],
    axes: tuple[str, ...] = DEFAULT_AXES,
    threshold: float = DEFAULT_THRESHOLD,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Returns (regressions, improvements, dropped).

    Each entry: {case_id, arm, axis, baseline_score, candidate_score, delta}
    Dropped: cases in baseline but absent from candidate, surfaced as
             {case_id, arm, status: "dropped"}.
    """
    base_avg = _group_and_average(baseline, axes)
    cand_avg = _group_and_average(candidate, axes)

    regressions: list[dict] = []
    improvements: list[dict] = []
    dropped: list[dict] = []

    for key, base_scores in base_avg.items():
        case_id, arm = key
        if key not in cand_avg:
            dropped.append({"case_id": case_id, "arm": arm, "status": "dropped"})
            continue
        for axis, base_v in base_scores.items():
            cand_v = cand_avg[key].get(axis)
            if cand_v is None:
                # axis present in baseline but not in candidate — skip
                continue
            delta = round(cand_v - base_v, 4)
            if delta < -threshold:
                regressions.append({
                    "case_id": case_id, "arm": arm, "axis": axis,
                    "baseline": round(base_v, 3), "candidate": round(cand_v, 3),
                    "delta": delta,
                })
            elif delta > threshold:
                improvements.append({
                    "case_id": case_id, "arm": arm, "axis": axis,
                    "baseline": round(base_v, 3), "candidate": round(cand_v, 3),
                    "delta": delta,
                })

    return regressions, improvements, dropped


def _print_table(rows: list[dict], header: str) -> None:
    if not rows:
        return
    print(f"\n{header}:")
    cols = [("case_id", 38), ("arm", 12), ("axis", 16), ("baseline", 10), ("candidate", 10), ("delta", 8)]
    print("  ".join(f"{n:<{w}}" for n, w in cols))
    print("  ".join("-" * w for _, w in cols))
    for r in rows:
        print("  ".join(f"{str(r.get(n, '')):<{w}}" for n, w in cols))


def main() -> None:
    p = argparse.ArgumentParser(description="Compare two eval-run JSONL files; fail on regressions.")
    p.add_argument("baseline", type=Path, help="Known-good JSONL (e.g. last release).")
    p.add_argument("candidate", type=Path, help="New JSONL to evaluate against baseline.")
    p.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                   help=f"Max allowed score drop per (case, arm, axis). Default: {DEFAULT_THRESHOLD}")
    p.add_argument("--axes", nargs="*", default=list(DEFAULT_AXES),
                   help=f"Axes to compare. Default: {' '.join(DEFAULT_AXES)}")
    p.add_argument("--allow-dropped", action="store_true",
                   help="Don't fail when baseline cases are missing from candidate.")
    p.add_argument("--quiet", action="store_true",
                   help="Suppress improvements table; show only regressions/dropped.")
    args = p.parse_args()

    base = _load(args.baseline)
    cand = _load(args.candidate)
    regressions, improvements, dropped = compare(
        base, cand, axes=tuple(args.axes), threshold=args.threshold,
    )

    print(f"Baseline: {args.baseline} ({len(base)} records)")
    print(f"Candidate: {args.candidate} ({len(cand)} records)")
    print(f"Threshold: -{args.threshold} (axis score drop)")

    _print_table(regressions, "REGRESSIONS")
    if not args.quiet:
        _print_table(improvements, "Improvements")
    if dropped:
        _print_table(dropped, "Dropped cases" if args.allow_dropped else "DROPPED CASES (treated as regression)")

    # Exit code logic
    n_regressions = len(regressions)
    n_dropped = 0 if args.allow_dropped else len(dropped)
    total_failures = n_regressions + n_dropped

    print()
    print(f"Summary: {n_regressions} regression(s), {len(improvements)} improvement(s), "
          f"{len(dropped)} dropped case(s).")

    if total_failures:
        print("FAIL: regression gate tripped.")
        sys.exit(1)
    print("PASS: no regressions above threshold.")
    sys.exit(0)


if __name__ == "__main__":
    main()
