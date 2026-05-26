"""Phase 1 unit tests for AgentShell primitives.

Focus is on the new dataclasses (AgentState, TokenBudget, ToolCallTrace,
TurnResult, BudgetExceededError) and shell construction. The agent loop
itself is exercised by an opt-in live smoke test gated on
RUN_LIVE_AGENT_TEST=1 (requires ANTHROPIC_API_KEY, spawns real MCP servers,
costs real tokens — ~$0.005 on Haiku).
"""
from __future__ import annotations

import asyncio
import os

import pytest
from dotenv import load_dotenv

load_dotenv()

from src.agent_shell import (
    AgentShell,
    AgentState,
    BudgetExceededError,
    MessageHistory,
    TokenBudget,
    ToolCallTrace,
    TurnResult,
)


# ─── AgentState ─────────────────────────────────────────────────────────

def test_agent_state_defaults_safe():
    s = AgentState()
    assert s.product is None
    assert s.version is None
    assert s.target is None
    assert s.target_os == "Linux"
    assert s.additional_sdks == []
    assert s.hardware_detected is False
    assert s.detected_devices == []
    assert s.last_ini_path is None
    assert s.last_install_log is None
    assert s.last_install_exit_code is None
    assert s.attempt_number == 0


def test_agent_state_mutations_independent():
    """Each AgentState instance must get its own mutable defaults — no
    shared list/dict bug from a forgotten field(default_factory=...)."""
    a = AgentState()
    b = AgentState()
    a.additional_sdks.append("DeepStream 7.0")
    a.detected_devices.append({"name": "Orin NX"})
    assert b.additional_sdks == []
    assert b.detected_devices == []


# ─── TokenBudget ────────────────────────────────────────────────────────

class _FakeUsage:
    """Mimics anthropic.types.Usage for budget unit tests."""

    def __init__(self, input_tokens=0, output_tokens=0, cache_read_input_tokens=0):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_input_tokens = cache_read_input_tokens


def test_token_budget_records_usage():
    b = TokenBudget(max_input_tokens=1000, max_output_tokens=500)
    b.add_usage(_FakeUsage(input_tokens=100, output_tokens=50))
    assert b.used_input == 100
    assert b.used_output == 50
    assert not b.is_exhausted()


def test_token_budget_accumulates_across_calls():
    b = TokenBudget(max_input_tokens=1000)
    b.add_usage(_FakeUsage(input_tokens=300))
    b.add_usage(_FakeUsage(input_tokens=400))
    assert b.used_input == 700
    assert not b.is_exhausted()
    b.add_usage(_FakeUsage(input_tokens=400))
    assert b.used_input == 1100
    assert b.is_exhausted()


def test_token_budget_cache_read_does_not_count_against_input_cap():
    """cache_read_input_tokens is server-discounted; tracking it helps
    cost attribution but it should not tip the input cap on its own."""
    b = TokenBudget(max_input_tokens=1000)
    b.add_usage(_FakeUsage(input_tokens=200, cache_read_input_tokens=5000))
    assert b.used_input == 200
    assert b.used_cache_read == 5000
    assert not b.is_exhausted()


def test_token_budget_handles_none_usage_fields():
    """Older SDK versions / models may return None for unset usage fields.
    add_usage should treat them as 0, not crash with TypeError."""

    class _PartialUsage:
        input_tokens = 100
        output_tokens = 50
        cache_read_input_tokens = None

    b = TokenBudget()
    b.add_usage(_PartialUsage())
    assert b.used_input == 100
    assert b.used_output == 50
    assert b.used_cache_read == 0


def test_token_budget_handles_missing_usage_fields():
    """A usage-like object missing fields altogether (e.g. tests mocking
    bare-bones usage) should not crash."""

    class _BareUsage:
        pass

    b = TokenBudget()
    b.add_usage(_BareUsage())
    assert b.used_input == 0
    assert b.used_output == 0


def test_token_budget_raise_if_exhausted_input():
    b = TokenBudget(max_input_tokens=100, max_output_tokens=10_000)
    b.add_usage(_FakeUsage(input_tokens=150))
    with pytest.raises(BudgetExceededError) as excinfo:
        b.raise_if_exhausted()
    assert excinfo.value.kind == "input"
    assert excinfo.value.used == 150
    assert excinfo.value.cap == 100


def test_token_budget_raise_if_exhausted_output():
    b = TokenBudget(max_input_tokens=10_000, max_output_tokens=100)
    b.add_usage(_FakeUsage(output_tokens=120))
    with pytest.raises(BudgetExceededError) as excinfo:
        b.raise_if_exhausted()
    assert excinfo.value.kind == "output"


def test_token_budget_raise_if_exhausted_silent_when_under():
    b = TokenBudget(max_input_tokens=1000, max_output_tokens=500)
    b.add_usage(_FakeUsage(input_tokens=500, output_tokens=250))
    b.raise_if_exhausted()  # should not raise


def test_token_budget_estimated_cost_haiku():
    b = TokenBudget()
    b.used_input = 1_000_000
    b.used_output = 1_000_000
    # $1/M in + $5/M out
    assert b.estimated_cost_usd("claude-haiku-4-5-20251001") == pytest.approx(6.00)


def test_token_budget_estimated_cost_opus():
    b = TokenBudget()
    b.used_input = 100_000
    b.used_output = 10_000
    # $15/M * 0.1M + $75/M * 0.01M = $1.50 + $0.75 = $2.25
    assert b.estimated_cost_usd("claude-opus-4-7") == pytest.approx(2.25)


def test_token_budget_estimated_cost_sonnet():
    b = TokenBudget()
    b.used_input = 1_000_000
    b.used_output = 1_000_000
    # $3/M in + $15/M out
    assert b.estimated_cost_usd("claude-sonnet-4-6") == pytest.approx(18.00)


def test_token_budget_estimated_cost_unknown_model_returns_zero():
    b = TokenBudget()
    b.used_input = 100_000
    b.used_output = 10_000
    assert b.estimated_cost_usd("gpt-4") == 0.0


# ─── BudgetExceededError ────────────────────────────────────────────────

def test_budget_exceeded_error_carries_attribution():
    e = BudgetExceededError("input", 250_000, 200_000)
    assert e.kind == "input"
    assert e.used == 250_000
    assert e.cap == 200_000
    assert "input" in str(e)
    assert "250000" in str(e)


# ─── TurnResult ─────────────────────────────────────────────────────────

def test_turn_result_str_returns_text():
    """str(result) returns the final assistant text, so callers that only
    want the reply can do `text = str(await shell.turn(...))`."""
    r = TurnResult(text="hello world", turns_used=3)
    assert str(r) == "hello world"


def test_turn_result_defaults():
    r = TurnResult(text="ok")
    assert r.tool_calls == []
    assert r.turns_used == 0
    assert r.finish_reason == "end_turn"
    assert r.input_tokens == 0
    assert r.output_tokens == 0


# ─── ToolCallTrace ──────────────────────────────────────────────────────

def test_tool_call_trace_holds_fields():
    t = ToolCallTrace(
        name="lookup_target_id",
        args={"board_name": "Orin NX"},
        result_text='{"target_id": "JETSON_ORIN_NX_TARGETS"}',
        latency_ms=42.0,
        turn_index=0,
    )
    assert t.name == "lookup_target_id"
    assert t.args == {"board_name": "Orin NX"}
    assert t.latency_ms == 42.0
    assert t.turn_index == 0


# ─── MessageHistory ─────────────────────────────────────────────────────

def _user_turn(text: str) -> dict:
    return {"role": "user", "content": text}


def _assistant_text(text: str) -> dict:
    return {"role": "assistant", "content": [type("B", (), {"type": "text", "text": text})()]}


def _assistant_tool_use(tool_id: str, name: str) -> dict:
    block = type("B", (), {"type": "tool_use", "id": tool_id, "name": name, "input": {}})()
    return {"role": "assistant", "content": [block]}


def _user_tool_results(*tool_ids: str) -> dict:
    return {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tid, "content": "{}"} for tid in tool_ids
    ]}


def test_message_history_unbounded_never_prunes():
    h = MessageHistory(strategy="unbounded")
    for i in range(50):
        h.append(_user_turn(f"q{i}"))
    dropped = h.prune()
    assert dropped == 0
    assert len(h) == 50


def test_message_history_sliding_under_cap_no_op():
    h = MessageHistory(strategy="sliding", max_user_turns=5)
    # 3 user turns + interleaved assistant/tool_result content
    for i in range(3):
        h.append(_user_turn(f"q{i}"))
        h.append(_assistant_text(f"a{i}"))
    assert h.prune() == 0
    assert len(h) == 6


def test_message_history_sliding_drops_oldest_user_turns():
    h = MessageHistory(strategy="sliding", max_user_turns=2)
    # 4 user-initiated turns
    for i in range(4):
        h.append(_user_turn(f"q{i}"))
        h.append(_assistant_text(f"a{i}"))
    # Should drop the first 2 user turns (and their assistant replies)
    dropped = h.prune()
    assert dropped == 4
    assert len(h) == 4
    assert h.messages[0]["role"] == "user"
    assert h.messages[0]["content"] == "q2"  # the third user input now first


def test_message_history_sliding_preserves_tool_use_pairs():
    """Critical invariant: after pruning, every tool_use block must still
    have its matching tool_result block in the next user message."""
    h = MessageHistory(strategy="sliding", max_user_turns=1)
    # Turn 1: user → assistant tool_use → user tool_result → assistant text
    h.append(_user_turn("first"))
    h.append(_assistant_tool_use("t1", "lookup_target_id"))
    h.append(_user_tool_results("t1"))
    h.append(_assistant_text("answer1"))
    # Turn 2: user → assistant text
    h.append(_user_turn("second"))
    h.append(_assistant_text("answer2"))

    dropped = h.prune()
    assert dropped > 0
    # The retained history should start at a user-turn boundary
    assert h.messages[0]["role"] == "user"
    assert isinstance(h.messages[0]["content"], str)
    # All tool_use blocks in the retained range must have matching tool_result
    tool_use_ids = {
        b.id for m in h.messages
        if m["role"] == "assistant"
        for b in m["content"]
        if hasattr(b, "type") and b.type == "tool_use"
    }
    tool_result_ids = {
        b["tool_use_id"] for m in h.messages
        if m["role"] == "user" and isinstance(m["content"], list)
        for b in m["content"]
        if b.get("type") == "tool_result"
    }
    assert tool_use_ids == tool_result_ids


def test_message_history_bool_and_len():
    """bool(history) and len(history) work for `if not shell.messages:` checks."""
    h = MessageHistory()
    assert not h
    assert len(h) == 0
    h.append(_user_turn("hi"))
    assert h
    assert len(h) == 1


def test_message_history_unknown_strategy_raises():
    h = MessageHistory(strategy="unbounded")
    h.strategy = "bogus"  # bypass type check
    with pytest.raises(ValueError, match="unknown.*strategy"):
        h.prune()


# ─── AgentShell construction (no MCP spawn) ─────────────────────────────

def test_shell_construction_defaults():
    shell = AgentShell()
    assert shell.mode == "single_turn"
    assert isinstance(shell.state, AgentState)
    assert isinstance(shell.budget, TokenBudget)
    assert isinstance(shell.history, MessageHistory)
    assert shell.messages == []  # backward-compat property alias
    assert shell.tool_call_history == []
    # MCP / client are None until __aenter__
    assert shell._client is None
    assert shell._k_session is None
    assert shell._tools is None


def test_shell_single_turn_mode_picks_unbounded_strategy():
    shell = AgentShell(mode="single_turn")
    assert shell.history.strategy == "unbounded"


def test_shell_repl_mode_picks_sliding_strategy():
    """REPL is the production-killer case from G1; sliding window must
    activate automatically when mode='repl' is selected."""
    shell = AgentShell(mode="repl")
    assert shell.history.strategy == "sliding"


def test_shell_messages_property_aliases_history():
    """External code uses shell.messages (e.g. `if not shell.messages:`);
    internal code uses shell.history.append. They must point at the same list."""
    shell = AgentShell()
    shell.history.append({"role": "user", "content": "test"})
    assert shell.messages == [{"role": "user", "content": "test"}]
    assert shell.messages is shell.history.messages


def test_shell_construction_with_explicit_state():
    s = AgentState(product="Jetson", version="6.2.2", target="JETSON_ORIN_NX_TARGETS")
    shell = AgentShell(mode="repl", state=s)
    assert shell.mode == "repl"
    assert shell.state.product == "Jetson"
    assert shell.state.version == "6.2.2"
    assert shell.state.target == "JETSON_ORIN_NX_TARGETS"


def test_shell_construction_with_explicit_budget():
    b = TokenBudget(max_input_tokens=50_000, max_output_tokens=10_000)
    shell = AgentShell(budget=b)
    assert shell.budget.max_input_tokens == 50_000
    assert shell.budget.max_output_tokens == 10_000


def test_shell_default_model_from_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-opus-4-7")
    shell = AgentShell()
    assert shell.model == "claude-opus-4-7"


def test_shell_explicit_model_wins_over_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-opus-4-7")
    shell = AgentShell(model="claude-haiku-4-5-20251001")
    assert shell.model == "claude-haiku-4-5-20251001"


def test_shell_turn_raises_if_not_entered():
    shell = AgentShell()
    with pytest.raises(RuntimeError, match="async context manager"):
        asyncio.run(shell.turn("hello"))


# ─── state auto-populate (Phase 3 / G3 partial fix) ─────────────────────

def test_shell_state_autopopulate_detect_hardware_with_devices():
    """detect_connected_hardware result with devices → state updated."""
    shell = AgentShell()
    assert shell.state.hardware_detected is False
    assert shell.state.detected_devices == []
    shell._update_state_from_tool_result(
        "detect_connected_hardware",
        '{"available": true, "devices": [{"name": "Orin NX", "port": "2-1"}]}',
    )
    assert shell.state.hardware_detected is True
    assert shell.state.detected_devices == [{"name": "Orin NX", "port": "2-1"}]


def test_shell_state_autopopulate_detect_hardware_no_devices():
    """Probe ran but nothing connected → hardware_detected=True, devices=[]."""
    shell = AgentShell()
    shell._update_state_from_tool_result(
        "detect_connected_hardware",
        '{"available": false}',
    )
    assert shell.state.hardware_detected is True
    assert shell.state.detected_devices == []


def test_shell_state_autopopulate_detect_hardware_old_connected_key():
    """Server may use legacy 'connected' key — accept it."""
    shell = AgentShell()
    shell._update_state_from_tool_result(
        "detect_connected_hardware",
        '{"connected": ["JETSON_ORIN_NX_TARGETS", "JETSON_AGX_ORIN_TARGETS"]}',
    )
    assert shell.state.hardware_detected is True
    # Bare strings normalized into dicts with `name` key
    assert shell.state.detected_devices == [
        {"name": "JETSON_ORIN_NX_TARGETS"}, {"name": "JETSON_AGX_ORIN_TARGETS"},
    ]


def test_shell_state_autopopulate_lookup_target_id():
    """lookup_target_id success → state.target set."""
    shell = AgentShell()
    assert shell.state.target is None
    shell._update_state_from_tool_result(
        "lookup_target_id",
        '{"target_id": "JETSON_ORIN_NX_TARGETS", "canonical_name": "Jetson Orin NX"}',
    )
    assert shell.state.target == "JETSON_ORIN_NX_TARGETS"


def test_shell_state_autopopulate_lookup_target_id_error_skipped():
    """lookup_target_id error result → state.target stays None (no mis-write)."""
    shell = AgentShell()
    shell._update_state_from_tool_result(
        "lookup_target_id",
        '{"error": "unknown board: Frobnicator XL"}',
    )
    assert shell.state.target is None


def test_shell_state_autopopulate_unknown_tool_silently_skipped():
    """A tool name we don't handle should not raise or mutate state."""
    shell = AgentShell()
    shell._update_state_from_tool_result(
        "some_future_tool",
        '{"unrelated_field": "x"}',
    )
    assert shell.state.target is None
    assert shell.state.hardware_detected is False


def test_shell_state_autopopulate_malformed_json_skipped():
    """If the tool result isn't JSON, skip silently — never raises."""
    shell = AgentShell()
    shell._update_state_from_tool_result("lookup_target_id", "this is not JSON {{{")
    assert shell.state.target is None


def test_shell_state_autopopulate_non_dict_json_skipped():
    """A JSON array result should not crash the dict-key lookup."""
    shell = AgentShell()
    shell._update_state_from_tool_result("lookup_target_id", '["a", "b"]')
    assert shell.state.target is None


def test_shell_state_autopopulate_preserves_prior_target_on_error():
    """If state.target is already set and a later lookup returns error, keep it."""
    shell = AgentShell()
    shell._update_state_from_tool_result(
        "lookup_target_id",
        '{"target_id": "JETSON_ORIN_NX_TARGETS"}',
    )
    assert shell.state.target == "JETSON_ORIN_NX_TARGETS"
    # Later: agent asks about an unknown board, result has error
    shell._update_state_from_tool_result(
        "lookup_target_id",
        '{"error": "unknown board"}',
    )
    # Original target preserved
    assert shell.state.target == "JETSON_ORIN_NX_TARGETS"


# ─── Live integration test (gated) ──────────────────────────────────────

@pytest.mark.skipif(
    os.getenv("RUN_LIVE_AGENT_TEST") != "1",
    reason="Set RUN_LIVE_AGENT_TEST=1 to run (spawns MCP servers, hits Anthropic API).",
)
@pytest.mark.timeout(120)
def test_shell_live_smoke():
    """End-to-end: shell spawns MCP, runs a simple query, returns a TurnResult.
    Costs real Anthropic tokens (~$0.005 per run on Haiku)."""
    if not os.getenv("ANTHROPIC_API_KEY"):
        pytest.skip("needs ANTHROPIC_API_KEY")

    async def _run():
        async with AgentShell(mode="single_turn") as shell:
            return await shell.turn("What is the target_id for Jetson Orin Nano 8GB?")

    result = asyncio.run(_run())

    # Finish reason and token accounting populated
    assert result.finish_reason == "end_turn"
    assert result.input_tokens > 0
    assert result.output_tokens > 0
    assert result.turns_used >= 1

    # Functional correctness: the canonical target_id should appear in reply
    assert "JETSON_ORIN_NANO_TARGETS" in result.text

    # Tool calls were recorded with timing
    assert len(result.tool_calls) >= 1
    assert all(t.latency_ms >= 0 for t in result.tool_calls)
    assert any(t.name == "lookup_target_id" for t in result.tool_calls)
