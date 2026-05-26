"""Minimal eval dashboard — print a comparison table from one or more run JSONLs.

NOT a full dashboard (M6 was deliberately descoped to "minimal" — see
docs/eval-design.md). Just enough to slice the data along arm × layer ×
track and surface the key axis averages for a release-gate sanity check.

Usage:
    python -m eval.dashboard.summarize eval/runs/*.jsonl
    python -m eval.dashboard.summarize eval/runs/2026-05-26T13-47-59_m1-smoke.jsonl
    python -m eval.dashboard.summarize eval/runs/baseline.jsonl eval/runs/no-tools.jsonl

Output: one row per (arm × layer × track) group with sample count, error
count, per-axis averages, and total estimated cost.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any


def _load_records(paths: list[Path]) -> list[dict]:
    records: list[dict] = []
    for p in paths:
        if not p.exists():
            print(f"warn: {p} not found, skipping")
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"warn: {p}: bad JSONL line: {e}")
    return records


def _group_key(r: dict) -> tuple[str, str, str]:
    """(arm, layer, track) is the comparison axis."""
    return (
        r.get("arm", "main"),
        r.get("case_layer", "?"),
        r.get("case_track", "?"),
    )


def _axis_score(r: dict, axis: str) -> float | None:
    scores = r.get("scores") or {}
    s = scores.get(axis)
    if not isinstance(s, dict):
        return None
    return s.get("score")


def _axis_cost(r: dict) -> float | None:
    a3 = (r.get("scores") or {}).get("a3_efficiency") or {}
    return a3.get("estimated_cost_usd")


def _axis_tokens(r: dict) -> tuple[int | None, int | None]:
    a3 = (r.get("scores") or {}).get("a3_efficiency") or {}
    return a3.get("input_tokens"), a3.get("output_tokens")


def _fmt_avg(values: list[float | None]) -> str:
    clean = [v for v in values if v is not None]
    if not clean:
        return "—"
    return f"{mean(clean):.2f}"


def _fmt_sum(values: list[float | None]) -> str:
    clean = [v for v in values if v is not None]
    if not clean:
        return "—"
    return f"${sum(clean):.4f}"


def _fmt_int_sum(values: list[int | None]) -> str:
    clean = [v for v in values if v is not None]
    if not clean:
        return "—"
    total = sum(clean)
    if total >= 1_000_000:
        return f"{total/1_000_000:.2f}M"
    if total >= 1000:
        return f"{total/1000:.1f}k"
    return str(total)


def summarize(records: list[dict]) -> None:
    if not records:
        print("No records to summarize.")
        return

    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in records:
        groups[_group_key(r)].append(r)

    # Header
    cols = [
        ("arm",      10),
        ("layer",     5),
        ("track",    14),
        ("count",     5),
        ("err",       3),
        ("A1",        5),
        ("A2",        5),
        ("A4",        5),
        ("tok_in",    8),
        ("tok_out",   8),
        ("$total",   10),
    ]
    print("  ".join(f"{name:<{w}}" for name, w in cols))
    print("  ".join("-" * w for _, w in cols))

    for key in sorted(groups.keys()):
        arm, layer, track = key
        rs = groups[key]
        a1_scores = [_axis_score(r, "a1_correctness") for r in rs]
        a2_scores = [_axis_score(r, "a2_compliance") for r in rs]
        a4_scores = [_axis_score(r, "a4_robustness") for r in rs]
        tok_in = [_axis_tokens(r)[0] for r in rs]
        tok_out = [_axis_tokens(r)[1] for r in rs]
        costs = [_axis_cost(r) for r in rs]
        errors = sum(1 for r in rs if r.get("error"))

        row = [
            arm[:10],
            layer,
            track[:14],
            str(len(rs)),
            str(errors) if errors else "",
            _fmt_avg(a1_scores),
            _fmt_avg(a2_scores),
            _fmt_avg(a4_scores),
            _fmt_int_sum(tok_in),
            _fmt_int_sum(tok_out),
            _fmt_sum(costs),
        ]
        print("  ".join(f"{v:<{w}}" for v, (_, w) in zip(row, cols)))

    print()
    print(f"Loaded {len(records)} record(s) from {len(groups)} group(s).")


def main() -> None:
    p = argparse.ArgumentParser(description="Summarize one or more eval run JSONLs.")
    p.add_argument("paths", nargs="+", type=Path,
                   help="One or more JSONL files from eval/runs/.")
    args = p.parse_args()
    summarize(_load_records(args.paths))


if __name__ == "__main__":
    main()
