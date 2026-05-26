"""Unit tests for eval/dashboard/regress.py.

Pure function tests on `compare()` — no JSONL files, no subprocess, no API.
"""
from __future__ import annotations

from eval.dashboard.regress import compare


def _record(case_id: str, arm: str, axis: str, score: float, sample: int = 0) -> dict:
    """Build a minimal RunRecord-shaped dict for testing."""
    return {
        "case_id": case_id,
        "arm": arm,
        "sample_index": sample,
        "scores": {axis: {"score": score}},
    }


def test_no_change_zero_regressions():
    base = [_record("L1-a", "main", "a1_correctness", 1.0)]
    cand = [_record("L1-a", "main", "a1_correctness", 1.0)]
    regs, imps, dropped = compare(base, cand, axes=("a1_correctness",))
    assert regs == []
    assert imps == []
    assert dropped == []


def test_detects_score_drop_above_threshold():
    base = [_record("L1-a", "main", "a1_correctness", 1.0)]
    cand = [_record("L1-a", "main", "a1_correctness", 0.5)]
    regs, _, _ = compare(base, cand, axes=("a1_correctness",), threshold=0.05)
    assert len(regs) == 1
    assert regs[0]["case_id"] == "L1-a"
    assert regs[0]["delta"] == -0.5


def test_ignores_drop_within_threshold():
    """A 0.03 drop with threshold 0.05 should NOT trip the gate."""
    base = [_record("L1-a", "main", "a2_compliance", 1.0)]
    cand = [_record("L1-a", "main", "a2_compliance", 0.97)]
    regs, _, _ = compare(base, cand, axes=("a2_compliance",), threshold=0.05)
    assert regs == []


def test_detects_improvement_above_threshold():
    base = [_record("L1-a", "main", "a1_correctness", 0.6)]
    cand = [_record("L1-a", "main", "a1_correctness", 0.9)]
    _, imps, _ = compare(base, cand, axes=("a1_correctness",), threshold=0.05)
    assert len(imps) == 1
    assert imps[0]["delta"] == 0.3


def test_dropped_case_surfaced():
    """A case present in baseline but absent from candidate counts as dropped."""
    base = [
        _record("L1-a", "main", "a1_correctness", 1.0),
        _record("L1-b", "main", "a1_correctness", 1.0),
    ]
    cand = [_record("L1-a", "main", "a1_correctness", 1.0)]
    _, _, dropped = compare(base, cand, axes=("a1_correctness",))
    assert len(dropped) == 1
    assert dropped[0]["case_id"] == "L1-b"


def test_arm_dimension_distinguishes_records():
    """Same case_id but different arm = different comparison bucket."""
    base = [
        _record("L1-a", "main", "a1_correctness", 1.0),
        _record("L1-a", "no-tools", "a1_correctness", 0.5),
    ]
    cand = [
        _record("L1-a", "main", "a1_correctness", 1.0),
        _record("L1-a", "no-tools", "a1_correctness", 0.2),  # regressed
    ]
    regs, _, _ = compare(base, cand, axes=("a1_correctness",), threshold=0.05)
    assert len(regs) == 1
    assert regs[0]["arm"] == "no-tools"


def test_averages_across_samples():
    """Multi-sample baseline should average before comparing."""
    base = [
        _record("L1-a", "main", "a1_correctness", 1.0, sample=0),
        _record("L1-a", "main", "a1_correctness", 0.5, sample=1),
    ]  # avg 0.75
    cand = [_record("L1-a", "main", "a1_correctness", 0.75, sample=0)]
    regs, imps, _ = compare(base, cand, axes=("a1_correctness",), threshold=0.05)
    assert regs == []
    assert imps == []


def test_a4_robustness_axis_picked_up():
    """A4 (the L3 safety axis) must be a comparable axis, not just A1/A2."""
    base = [_record("L3-x", "main", "a4_robustness", 1.0)]
    cand = [_record("L3-x", "main", "a4_robustness", 0.0)]  # severe regression
    regs, _, _ = compare(base, cand, axes=("a4_robustness",), threshold=0.05)
    assert len(regs) == 1
    assert regs[0]["delta"] == -1.0


def test_missing_axis_in_candidate_skipped():
    """If baseline has A1 but candidate has no A1 (only A2), don't crash."""
    base = [_record("L1-a", "main", "a1_correctness", 1.0)]
    cand = [_record("L1-a", "main", "a2_compliance", 1.0)]
    regs, _, _ = compare(base, cand, axes=("a1_correctness",))
    # Case key (L1-a, main) IS present in both, but axis a1_correctness is
    # absent from candidate → that single axis is skipped, no regression.
    assert regs == []


def test_multiple_axes_multiple_regressions():
    base = [
        _record("L1-a", "main", "a1_correctness", 1.0),
        # second axis in same record by overwriting scores manually
    ]
    base[0]["scores"]["a2_compliance"] = {"score": 1.0}
    cand = [_record("L1-a", "main", "a1_correctness", 0.5)]
    cand[0]["scores"]["a2_compliance"] = {"score": 0.4}
    regs, _, _ = compare(base, cand, axes=("a1_correctness", "a2_compliance"), threshold=0.05)
    assert len(regs) == 2
    assert {r["axis"] for r in regs} == {"a1_correctness", "a2_compliance"}
