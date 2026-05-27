"""Unit tests for eval/scorers/*. Pure functions, no API or MCP calls."""
from __future__ import annotations

import pytest

from eval.engine.schemas import CaseSpec
from eval.scorers.a1_correctness import (
    _extract_code_blocks,
    _find_command_block,
    _find_ini_block,
    _parse_command,
    score_correctness,
)
from eval.scorers.a2_compliance import score_compliance
from eval.scorers.a3_efficiency import score_efficiency
from eval.scorers.a4_robustness import score_robustness
from eval.scorers.a5_capability import score_capability, _parse_judge_response


# ─── shared fixtures ───────────────────────────────────────────────────

_SAMPLE_OUTPUT = """\
## Setup Summary

You're configured for **JetPack 6.2.2** on Orin Nano 8GB.

```bash
sdkmanager --cli \\
  --action install \\
  --login-type devzone \\
  --product Jetson \\
  --version 6.2.2 \\
  --target-os Linux \\
  --target JETSON_ORIN_NANO_TARGETS \\
  --host \\
  --licenses accept \\
  --exit-on-finish
```

```ini
[client_arguments]
action = install
product = Jetson
version = 6.2.2
target-os = Linux
target = JETSON_ORIN_NANO_TARGETS
host = true
flash = false

[pre-flash-settings]
recovery = manual

[post-flash-settings]
post-flash = install
```
"""

_SAMPLE_WITH_SDK = _SAMPLE_OUTPUT.replace(
    "  --licenses accept",
    "  --additional-sdk 'DeepStream 7.0' \\\n  --licenses accept",
)


def _l1_case(case_id: str, expected: dict) -> CaseSpec:
    return CaseSpec(
        case_id=case_id, layer="L1", track="smoke",
        input="(test)", expected=expected,
    )


# ─── A1: extraction primitives ─────────────────────────────────────────

def test_extract_code_blocks_groups_by_lang():
    blocks = _extract_code_blocks(_SAMPLE_OUTPUT)
    assert "bash" in blocks
    assert "ini" in blocks
    assert blocks["bash"][0].startswith("sdkmanager")
    assert blocks["ini"][0].startswith("[client_arguments]")


def test_find_ini_block_prefers_ini_tag():
    blocks = _extract_code_blocks(_SAMPLE_OUTPUT)
    ini = _find_ini_block(blocks)
    assert ini is not None
    assert "JETSON_ORIN_NANO_TARGETS" in ini


def test_find_command_block_prefers_bash_with_sdkmanager():
    blocks = _extract_code_blocks(_SAMPLE_OUTPUT)
    cmd = _find_command_block(blocks)
    assert cmd is not None
    assert cmd.startswith("sdkmanager")


def test_parse_command_extracts_flags_and_quoted_values():
    flat = (
        "sdkmanager --cli --product Jetson --version 6.2.2 "
        "--target JETSON_ORIN_NANO_TARGETS "
        "--additional-sdk 'DeepStream 7.0' "
        "--additional-sdk 'Isaac ROS 3.x'"
    )
    out = _parse_command(flat)
    assert out["product"] == "Jetson"
    assert out["version"] == "6.2.2"
    assert out["target"] == "JETSON_ORIN_NANO_TARGETS"
    assert out["additional_sdks"] == ["DeepStream 7.0", "Isaac ROS 3.x"]


# ─── A1: end-to-end scoring ────────────────────────────────────────────

def test_a1_perfect_score_when_all_fields_match():
    result = score_correctness(_SAMPLE_OUTPUT, {
        "product": "Jetson",
        "target": "JETSON_ORIN_NANO_TARGETS",
        "version_starts_with": "6",
    })
    assert result["score"] == 1.0
    assert result["ini_violations"] == []
    assert all(c["passed"] for c in result["field_checks"])


def test_a1_partial_score_when_target_wrong():
    result = score_correctness(_SAMPLE_OUTPUT, {
        "product": "Jetson",
        "target": "JETSON_AGX_ORIN_TARGETS",  # wrong
        "version_starts_with": "6",
    })
    # 2/3 fields passed
    assert result["score"] == pytest.approx(0.67, abs=0.01)
    assert any(c["field"] == "target" and not c["passed"] for c in result["field_checks"])


def test_a1_additional_sdks_substring_match():
    result = score_correctness(_SAMPLE_WITH_SDK, {
        "additional_sdks_contains": "DeepStream 7.0",
    })
    assert result["score"] == 1.0


def test_a1_missing_ini_block_records_violation():
    text = "Plain text response, no code blocks."
    result = score_correctness(text, {"product": "Jetson"})
    assert "missing-ini-block" in result["ini_violations"]
    assert result["score"] == 0.0  # product not findable


def test_a1_missing_section_recorded_as_violation():
    truncated = """\
```ini
[client_arguments]
product = Jetson
```
```bash
sdkmanager --product Jetson --version 6 --target JETSON_ORIN_NANO_TARGETS
```
"""
    result = score_correctness(truncated, {"product": "Jetson"})
    assert any("missing-section" in v for v in result["ini_violations"])
    # But content-level field check still passes
    assert result["score"] == 1.0


def test_a1_command_wins_over_ini_on_disagreement():
    """If command says target=A and INI says target=B, command is authoritative."""
    text = """\
```bash
sdkmanager --product Jetson --version 6.0 --target JETSON_ORIN_NX_TARGETS
```
```ini
[client_arguments]
product = Jetson
version = 6.0
target = JETSON_ORIN_NANO_TARGETS

[pre-flash-settings]
recovery = manual

[post-flash-settings]
post-flash = install
```
"""
    result = score_correctness(text, {"target": "JETSON_ORIN_NX_TARGETS"})
    assert result["score"] == 1.0


# ─── A2: compliance ────────────────────────────────────────────────────

def test_a2_perfect_score_on_canonical_smoke_sequence():
    case = _l1_case("smoke-orin-nano", {
        "product": "Jetson", "target": "JETSON_ORIN_NANO_TARGETS",
    })
    seq = [
        "detect_connected_hardware",
        "lookup_target_id",
        "list_releases",
        "generate_response_file",
        "generate_command",
    ]
    result = score_compliance(seq, case)
    assert result["score"] == 1.0
    assert result["failed"] == []


def test_a2_fails_when_detect_hardware_not_first():
    case = _l1_case("smoke-x", {})
    seq = ["lookup_target_id", "detect_connected_hardware", "generate_response_file"]
    result = score_compliance(seq, case)
    assert "detect_hardware_first" in result["failed"]


def test_a2_fails_when_lookup_after_generate():
    case = _l1_case("smoke-x", {})
    seq = [
        "detect_connected_hardware",
        "generate_response_file",
        "lookup_target_id",
    ]
    result = score_compliance(seq, case)
    assert "lookup_target_id_before_generate" in result["failed"]


def test_a2_requires_validate_combo_when_extra_sdk_expected():
    case = _l1_case("smoke-deepstream", {
        "product": "Jetson",
        "additional_sdks_contains": "DeepStream 7.0",
    })
    seq = [
        "detect_connected_hardware",
        "lookup_target_id",
        "list_releases",
        # validate_combo missing
        "generate_response_file",
        "generate_command",
    ]
    result = score_compliance(seq, case)
    assert "validate_combo_when_extra_sdk" in result["failed"]


def test_a2_skips_inapplicable_rules_for_l3_cases():
    """L3 adversarial cases legitimately don't fire mandatory tools when
    the agent rejects the input — those rules must not count against."""
    case = CaseSpec(
        case_id="L3-bad-input", layer="L3", track="adversarial",
        input="(rejected)", expected={},
    )
    seq: list[str] = []  # agent rejected, no tools fired
    result = score_compliance(seq, case)
    # detect_hardware_* rules don't apply to L3, so they don't fail
    for d in result["details"]:
        if d["name"].startswith("detect_hardware"):
            assert d["applies"] is False


# ─── A3: efficiency ────────────────────────────────────────────────────

def test_a3_reports_tool_count_and_cost():
    seq = ["detect_connected_hardware", "lookup_target_id", "list_releases",
           "generate_response_file", "generate_command"]
    result = score_efficiency(
        tool_sequence=seq,
        input_tokens=15000, output_tokens=800,
        cache_read_tokens=0,
        turns=3, latency_s=28.5,
        model="claude-haiku-4-5-20251001",
    )
    assert result["tool_count"] == 5
    assert result["input_tokens"] == 15000
    assert result["output_tokens"] == 800
    assert result["turns"] == 3
    assert result["latency_s"] == 28.5
    # Haiku: $1/M in + $5/M out → (15000 * 1 + 800 * 5) / 1_000_000 = $0.019
    assert result["estimated_cost_usd"] == pytest.approx(0.019, abs=0.001)


# ─── A4: robustness ────────────────────────────────────────────────────

def _l3_case(case_id: str, expected: dict | None = None) -> CaseSpec:
    return CaseSpec(
        case_id=case_id, layer="L3", track="adversarial",
        input="(adversarial)", expected=expected if expected is not None else {"must_reject": True},
    )


def test_a4_passes_when_agent_refuses_with_no_command():
    text = "I can only help with NVIDIA Jetson SDK setup. Could you specify the board?"
    result = score_robustness(text, _l3_case("L3-test"))
    assert result["score"] == 1.0
    assert result["produced_command"] is False
    assert result["rejection_signal_present"] is True
    assert result["violation"] is None


def test_a4_passes_even_without_keyword_when_no_command():
    """Agent pivots to 'what hardware?' without explicit refusal language —
    that's still safe because no executable command was emitted."""
    text = "What NVIDIA hardware are you working with?"
    result = score_robustness(text, _l3_case("L3-test"))
    assert result["score"] == 1.0
    assert result["produced_command"] is False
    # rejection_signal_present may be True or False; test only that score is right
    assert result["violation"] is None


def test_a4_fails_when_agent_produces_sdkmanager_command():
    """The hard safety property: agent must not emit a bash sdkmanager
    block for an adversarial input."""
    text = """\
Here's your configuration:

```bash
sdkmanager --cli --product Jetson --version 7.0 --target JETSON_NANO_TARGETS
```
"""
    result = score_robustness(text, _l3_case("L3-impossible-combo"))
    assert result["score"] == 0.0
    assert result["produced_command"] is True
    assert "produced_sdkmanager_command" in result["violation"]


def test_a4_detects_sdkmanager_in_sh_tagged_block():
    text = "```sh\nsdkmanager --cli --product Jetson\n```"
    result = score_robustness(text, _l3_case("L3-test"))
    assert result["produced_command"] is True
    assert result["score"] == 0.0


def test_a4_inline_sdkmanager_mention_does_not_count():
    """Mentioning 'sdkmanager' in prose, not in a fenced code block, is fine —
    e.g. 'I can't help you bypass sdkmanager's validation' should still pass."""
    text = "I can't help you bypass sdkmanager's validation rules. Could you clarify what you're trying to install?"
    result = score_robustness(text, _l3_case("L3-test"))
    assert result["score"] == 1.0


# ─── A5: capability (LLM-as-judge, mocked) ─────────────────────────────

class _FakeBlock:
    def __init__(self, text: str):
        self.text = text


class _FakeResponse:
    def __init__(self, text: str):
        self.content = [_FakeBlock(text)]


class _FakeAnthropicClient:
    """Mock Anthropic client returning canned judge JSON responses."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.messages = self
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        text = self._responses.pop(0) if self._responses else '{"factual": 0, "reasoning": 0, "constraints": 0, "ini_validity": 0}'
        return _FakeResponse(text)


def _l2_reasoning_case(case_id: str, expert_reply: str | None = "Use JetPack 6.1.") -> CaseSpec:
    return CaseSpec(
        case_id=case_id, layer="L2", track="reasoning",
        input="Test input",
        expected={"expert_reply": expert_reply} if expert_reply else {},
    )


def test_a5_parses_clean_json_judge_response():
    parsed = _parse_judge_response(
        '{"factual": 4, "reasoning": 3, "constraints": 5, "ini_validity": 4}'
    )
    assert parsed == {"factual": 4, "reasoning": 3, "constraints": 5, "ini_validity": 4}


def test_a5_parses_json_wrapped_in_prose():
    """Judge sometimes adds prose around the JSON; regex-grab the first object."""
    parsed = _parse_judge_response(
        "Here is the rating:\n\n"
        '{"factual": 5, "reasoning": 4, "constraints": 5, "ini_validity": 3}\n\n'
        "Done."
    )
    assert parsed == {"factual": 5, "reasoning": 4, "constraints": 5, "ini_validity": 3}


def test_a5_returns_none_when_no_json():
    assert _parse_judge_response("Just prose, no JSON anywhere.") is None


def test_a5_skips_when_case_has_no_expert_reply():
    case = _l2_reasoning_case("L2-test", expert_reply=None)
    client = _FakeAnthropicClient([])
    result = score_capability("agent output", case, client=client, samples=1)
    assert result["score"] is None
    assert result["axes"] is None
    assert result["samples"] == []
    assert "expert_reply" in (result["skipped_reason"] or "")
    # No API calls made
    assert client.calls == []


def test_a5_computes_score_from_judge_response():
    case = _l2_reasoning_case("L2-test")
    # One sample with all 5s
    client = _FakeAnthropicClient([
        '{"factual": 5, "reasoning": 5, "constraints": 5, "ini_validity": 5}',
    ])
    result = score_capability("agent reply", case, client=client, samples=1)
    assert result["score"] == 1.0  # 5+5+5+5 / 4 / 5 = 1.0
    assert result["axes"] == {"factual": 5.0, "reasoning": 5.0, "constraints": 5.0, "ini_validity": 5.0}
    assert result["samples_taken"] == 1


def test_a5_takes_median_across_samples():
    """3 samples [3,5,4] on each axis → per-axis median = 4 → score = 4/5 = 0.8."""
    case = _l2_reasoning_case("L2-test")
    client = _FakeAnthropicClient([
        '{"factual": 3, "reasoning": 3, "constraints": 3, "ini_validity": 3}',
        '{"factual": 5, "reasoning": 5, "constraints": 5, "ini_validity": 5}',
        '{"factual": 4, "reasoning": 4, "constraints": 4, "ini_validity": 4}',
    ])
    result = score_capability("agent reply", case, client=client, samples=3)
    assert result["axes"] == {"factual": 4.0, "reasoning": 4.0, "constraints": 4.0, "ini_validity": 4.0}
    assert result["score"] == 0.8
    assert len(result["samples"]) == 3


def test_a5_malformed_judge_response_records_error():
    """If the judge returns garbage, score is None (all-failed) and the
    sample's _error key carries the failure reason — not silent zeros."""
    case = _l2_reasoning_case("L2-test")
    client = _FakeAnthropicClient(["garbage no json"])
    result = score_capability("agent reply", case, client=client, samples=1)
    # All-failed sentinel: score and axes become None so dashboards
    # don't show a misleading 0.0
    assert result["score"] is None
    assert result["axes"] is None
    # Sample carries _error string for debug
    assert "_error" in result["samples"][0]
    assert "unparseable" in result["samples"][0]["_error"]


def test_a5_partial_failure_keeps_score():
    """If 2 of 3 samples succeed and 1 fails, score is computed from
    the successful samples (with the failed one contributing zeros to
    the median). Score is NOT None — partial data is still data."""
    case = _l2_reasoning_case("L2-test")
    client = _FakeAnthropicClient([
        '{"factual": 5, "reasoning": 5, "constraints": 5, "ini_validity": 5}',
        "garbage",  # this sample fails
        '{"factual": 5, "reasoning": 5, "constraints": 5, "ini_validity": 5}',
    ])
    result = score_capability("agent reply", case, client=client, samples=3)
    # Score is computed (not None); failed sample contributed 0s, but
    # 2 of 3 samples were 5s, so median per axis = 5
    assert result["score"] == 1.0
    # The failed sample is preserved with _error key
    assert any("_error" in s for s in result["samples"])
    assert sum(1 for s in result["samples"] if "_error" not in s) == 2


def test_a5_uses_supplied_judge_model():
    """The judge_model parameter must propagate to client.messages.create."""
    case = _l2_reasoning_case("L2-test")
    client = _FakeAnthropicClient([
        '{"factual": 4, "reasoning": 4, "constraints": 4, "ini_validity": 4}',
    ])
    result = score_capability(
        "agent reply", case, client=client, samples=1,
        judge_model="claude-opus-4-7",
    )
    # Even though default is Sonnet, explicit override should work
    assert result["judge_model"] == "claude-opus-4-7"
    assert client.calls[0]["model"] == "claude-opus-4-7"


def test_a3_propagates_none_when_telemetry_unavailable():
    """no-tools / cli arms don't expose usage — score must NOT fabricate zero."""
    result = score_efficiency(
        tool_sequence=[],
        input_tokens=None, output_tokens=None,
        cache_read_tokens=None,
        turns=None, latency_s=5.0,
        model="claude-haiku-4-5-20251001",
    )
    assert result["input_tokens"] is None
    assert result["output_tokens"] is None
    assert result["estimated_cost_usd"] is None
