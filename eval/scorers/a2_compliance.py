"""A2 — Contract compliance scorer.

Checks the tool dispatch order against SYSTEM_PROMPT's routing rules. Pure
trace inspection: no LLM in the loop, so this is deterministic and cheap.

The rules below encode the prompt's routing contract from
src/prompts/1.0.0/system-prompt.md. Each rule is a small pass/fail predicate
on the tool_sequence (a list of tool names in dispatch order). The score is
`num_passed / num_applicable`, rounded to 2 dp.

A case where the agent errored out (empty tool_sequence) is scored 0.0 with
all rules marked failed — we don't penalize missing tools as N/A because the
contract requires at minimum a few mandatory tools to fire.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from eval.engine.schemas import CaseSpec


@dataclass
class Rule:
    """One compliance check."""

    name: str
    description: str
    check: Callable[[list[str], CaseSpec], bool]
    applies_when: Callable[[CaseSpec], bool] = field(default=lambda _: True)


def _idx(seq: list[str], name: str) -> int:
    """Return the first index of `name` in seq, or len(seq) if absent."""
    return seq.index(name) if name in seq else len(seq)


# Mandatory path: detect_connected_hardware → lookup_target_id → list_releases
#                 → ... → generate_response_file + generate_command
RULES: list[Rule] = [
    Rule(
        name="detect_hardware_present",
        description=(
            "detect_connected_hardware must fire at least once per session "
            "(per SYSTEM_PROMPT line 9)."
        ),
        check=lambda seq, _c: "detect_connected_hardware" in seq,
        # L3 adversarial cases may not require this if the agent rejects
        # before any tool dispatch — exempt them by track.
        applies_when=lambda c: c.layer != "L3",
    ),
    Rule(
        name="detect_hardware_first",
        description=(
            "When present, detect_connected_hardware must be the FIRST tool "
            "call (SYSTEM_PROMPT 'Call ... once' instruction)."
        ),
        check=lambda seq, _c: (
            "detect_connected_hardware" not in seq
            or seq[0] == "detect_connected_hardware"
        ),
        applies_when=lambda c: c.layer != "L3",
    ),
    Rule(
        name="detect_hardware_once",
        description="detect_connected_hardware should fire at most once.",
        check=lambda seq, _c: seq.count("detect_connected_hardware") <= 1,
        applies_when=lambda c: c.layer != "L3",
    ),
    Rule(
        name="lookup_target_id_before_generate",
        description=(
            "lookup_target_id must precede generate_response_file and "
            "generate_command — the target_id is an input to both."
        ),
        check=lambda seq, _c: (
            "lookup_target_id" not in seq
            or "generate_response_file" not in seq
            or _idx(seq, "lookup_target_id") < _idx(seq, "generate_response_file")
        ),
        applies_when=lambda c: c.track in ("smoke", "reasoning"),
    ),
    Rule(
        name="list_releases_before_generate",
        description="list_releases must precede generate_response_file.",
        check=lambda seq, _c: (
            "list_releases" not in seq
            or "generate_response_file" not in seq
            or _idx(seq, "list_releases") < _idx(seq, "generate_response_file")
        ),
        applies_when=lambda c: c.track in ("smoke", "reasoning"),
    ),
    Rule(
        name="generate_response_file_present",
        description=(
            "generate_response_file is mandatory output for configure-style "
            "queries (SYSTEM_PROMPT step 5)."
        ),
        check=lambda seq, _c: "generate_response_file" in seq,
        applies_when=lambda c: c.track in ("smoke", "reasoning"),
    ),
    Rule(
        name="generate_command_present",
        description=(
            "generate_command is mandatory output alongside the .ini "
            "(SYSTEM_PROMPT step 5)."
        ),
        check=lambda seq, _c: "generate_command" in seq,
        applies_when=lambda c: c.track in ("smoke", "reasoning"),
    ),
    Rule(
        name="validate_combo_when_extra_sdk",
        description=(
            "validate_combo must fire when the case carries an extra SDK "
            "(DeepStream / Isaac ROS / etc.)."
        ),
        check=lambda seq, _c: "validate_combo" in seq,
        applies_when=lambda c: bool(
            c.expected.get("additional_sdks_contains")
            or c.expected.get("additional_sdks")
        ),
    ),
]


def score_compliance(
    tool_sequence: list[str], case: CaseSpec
) -> dict:
    """Apply every applicable rule; return {score, passed, failed, applicable}.

    Result shape:
        {
            "score": float (0.0-1.0, num_passed / num_applicable),
            "passed": list[str] — rule names that passed,
            "failed": list[str] — rule names that failed,
            "applicable": list[str] — rule names that applied,
            "details": list[dict] — per-rule {name, applies, passed, description},
        }
    """
    details: list[dict] = []
    passed: list[str] = []
    failed: list[str] = []
    applicable: list[str] = []

    for rule in RULES:
        applies = rule.applies_when(case)
        rule_passed = rule.check(tool_sequence, case) if applies else None
        details.append({
            "name": rule.name,
            "description": rule.description,
            "applies": applies,
            "passed": rule_passed,
        })
        if not applies:
            continue
        applicable.append(rule.name)
        if rule_passed:
            passed.append(rule.name)
        else:
            failed.append(rule.name)

    score = round(len(passed) / len(applicable), 2) if applicable else 0.0
    return {
        "score": score,
        "passed": passed,
        "failed": failed,
        "applicable": applicable,
        "details": details,
    }
