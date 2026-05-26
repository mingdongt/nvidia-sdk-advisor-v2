"""Unified agent shell with typed state, token budgeting, and structured results.

Phase 1 deliverable for the agent-design.md refactor. This module introduces:

  - AgentState        : typed dataclass for cross-phase / cross-turn state
  - TokenBudget       : cumulative cost cap (input/output token tracking)
  - BudgetExceededError : raised when the cap would be passed
  - ToolCallTrace     : one tool_use / tool_result pair with timing
  - TurnResult        : structured outcome of one shell.turn() call
  - AgentShell        : async-context-manager-owned agent loop

The existing entry points in src/agent.py, src/repl.py, and src/troubleshoot.py
are NOT yet migrated — they continue to work via the original code paths.
Phase 2 will migrate them on top of AgentShell.

See docs/agent-design.md (Ch 8, gaps G1/G2/G3/G4/G8/G9) for the motivation.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Optional

import anthropic
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


# Reuse the same MCP server paths as src/agent.py so behavior stays consistent
# while both code paths coexist during the Phase 1 / Phase 2 cutover.
_KNOWLEDGE_SERVER = Path(__file__).parent / "knowledge_server.py"
_RAG_SERVER = Path(__file__).parent / "rag_server.py"

_PROMPT_DIR = Path(__file__).parent / "prompts" / "1.0.0"
SYSTEM_PROMPT = (_PROMPT_DIR / "system-prompt.md").read_text(encoding="utf-8")

MAX_TURNS = 50

AgentMode = Literal["single_turn", "repl", "troubleshoot"]


# ───────────────────────────────────────────────────────────────────────
# Errors
# ───────────────────────────────────────────────────────────────────────

class BudgetExceededError(RuntimeError):
    """Raised mid-turn when cumulative token usage passes the configured cap.

    Carries which budget axis tripped (input vs. output), the offending used
    count, and the cap value. Callers can catch and inspect for retry logic.
    """

    def __init__(self, kind: Literal["input", "output"], used: int, cap: int):
        super().__init__(f"{kind} token budget exceeded: {used} > {cap}")
        self.kind = kind
        self.used = used
        self.cap = cap


# ───────────────────────────────────────────────────────────────────────
# State + Budget + Result + Trace
# ───────────────────────────────────────────────────────────────────────

@dataclass
class AgentState:
    """Typed state object passed across phases / turns.

    Phase 1: populated by external code (e.g. orchestrator) and read by
    future migrated entry points. Phase 2: turn() will start updating these
    fields automatically by inspecting tool_use blocks (e.g. detect a
    `lookup_target_id` call returning `JETSON_ORIN_NX_TARGETS` and write
    that into self.target).
    """

    # configure phase outputs
    product: Optional[str] = None
    version: Optional[str] = None
    target: Optional[str] = None
    target_os: str = "Linux"
    additional_sdks: list[str] = field(default_factory=list)

    # one-shot probes (will eliminate G4 once REPL is migrated in Phase 2)
    hardware_detected: bool = False
    detected_devices: list[dict] = field(default_factory=list)

    # install / troubleshoot phase outputs
    last_ini_path: Optional[Path] = None
    last_install_log: Optional[Path] = None
    last_install_exit_code: Optional[int] = None
    attempt_number: int = 0


@dataclass
class TokenBudget:
    """Cumulative token-spend cap across all turns in a shell's lifetime.

    Anthropic's `response.usage` reports `input_tokens`, `output_tokens`,
    and `cache_read_input_tokens` per call. We track all three but only
    enforce caps on input/output — cache reads are discounted server-side
    and shouldn't push the cap on their own.
    """

    max_input_tokens: int = 200_000
    max_output_tokens: int = 50_000
    used_input: int = 0
    used_output: int = 0
    used_cache_read: int = 0

    def add_usage(self, usage: Any) -> None:
        """Record one API call's usage. Does NOT raise — call is_exhausted()
        to check enforcement. Accepts None-valued fields gracefully (older
        SDK versions / models may not populate cache_read)."""
        self.used_input += getattr(usage, "input_tokens", 0) or 0
        self.used_output += getattr(usage, "output_tokens", 0) or 0
        self.used_cache_read += getattr(usage, "cache_read_input_tokens", 0) or 0

    def is_exhausted(self) -> bool:
        """True if either axis has hit or passed its cap."""
        return (
            self.used_input >= self.max_input_tokens
            or self.used_output >= self.max_output_tokens
        )

    def raise_if_exhausted(self) -> None:
        """Raise BudgetExceededError if either axis is exhausted."""
        if self.used_input >= self.max_input_tokens:
            raise BudgetExceededError("input", self.used_input, self.max_input_tokens)
        if self.used_output >= self.max_output_tokens:
            raise BudgetExceededError("output", self.used_output, self.max_output_tokens)

    def estimated_cost_usd(self, model: str) -> float:
        """Rough cost estimate using public Anthropic pricing (per 1M tokens,
        as of 2026-05). For dashboards / logs; not authoritative.

            haiku-4.5 : $1.00 in / $5.00 out
            sonnet-4.6: $3.00 in / $15.00 out
            opus-4.7  : $15.00 in / $75.00 out

        Returns 0.0 for unknown models (no provider attribution).
        """
        pricing = {
            "haiku": (1.00, 5.00),
            "sonnet": (3.00, 15.00),
            "opus": (15.00, 75.00),
        }
        m = model.lower()
        for tag, (in_rate, out_rate) in pricing.items():
            if tag in m:
                return (self.used_input * in_rate + self.used_output * out_rate) / 1_000_000
        return 0.0


@dataclass
class ToolCallTrace:
    """One tool_use + tool_result pair, with timing for telemetry."""

    name: str
    args: dict
    result_text: str
    latency_ms: float
    turn_index: int  # which turn (0-indexed) within the shell's lifetime


@dataclass
class TurnResult:
    """Structured outcome of a single AgentShell.turn() call.

    Backward-compatible with code that wants only the text: callers can
    treat str(result) as the final assistant text. New code should inspect
    tool_calls / input_tokens / output_tokens / finish_reason for richer
    behavior (eval, telemetry, debugging).
    """

    text: str
    tool_calls: list[ToolCallTrace] = field(default_factory=list)
    turns_used: int = 0
    finish_reason: Literal["end_turn", "max_turns", "budget_exceeded"] = "end_turn"
    input_tokens: int = 0   # incremental usage for this turn() call only
    output_tokens: int = 0  # ditto

    def __str__(self) -> str:
        return self.text


# ───────────────────────────────────────────────────────────────────────
# MessageHistory — pluggable retention strategy
# ───────────────────────────────────────────────────────────────────────

HistoryStrategy = Literal["unbounded", "sliding"]


@dataclass
class MessageHistory:
    """Message buffer with pluggable retention strategy.

    Anthropic billing charges input_tokens × every API call. A long REPL
    session that never prunes its message list re-sends all prior turns
    on every request — by turn 30 the per-turn input cost can be 30× the
    first turn. This class adds a sliding-window option that drops the
    oldest user-initiated turns when the buffer grows past a threshold,
    while preserving the tool_use / tool_result block pairing that
    Anthropic requires (a tool_use without its matching tool_result is
    a 400 error).

    Strategies (Phase 2d):
      - "unbounded": never prune; used for single_turn mode (1 turn per
        shell anyway) and for the troubleshoot path (which makes only
        one API call).
      - "sliding": keep the most recent max_user_turns user-initiated
        turns; drop the rest. Used for REPL mode.

    Future strategy (Phase 2e or later):
      - "phase_summarized": when a phase concludes (configure done,
        troubleshoot done), summarize the closed phase into a single
        assistant message and reset the underlying turn history.
    """

    strategy: HistoryStrategy = "unbounded"
    max_user_turns: int = 10  # only used by "sliding"
    messages: list[dict] = field(default_factory=list)

    def append(self, message: dict) -> None:
        self.messages.append(message)

    def __len__(self) -> int:
        return len(self.messages)

    def __bool__(self) -> bool:
        return bool(self.messages)

    def __iter__(self):
        return iter(self.messages)

    def prune(self) -> int:
        """Drop oldest user turns per the configured strategy.

        Returns the number of messages dropped (0 if no pruning occurred).
        """
        if self.strategy == "unbounded":
            return 0
        if self.strategy == "sliding":
            return self._prune_sliding()
        raise ValueError(f"unknown MessageHistory strategy: {self.strategy!r}")

    def _prune_sliding(self) -> int:
        """Sliding window: keep at most max_user_turns recent user-initiated turns.

        A "user-initiated turn" is identified by a role=user message whose
        content is a plain string (vs. a list of tool_result blocks, which
        belong to the assistant's prior tool_use). Pruning at a turn-start
        boundary guarantees that any tool_use blocks in the kept history
        retain their matching tool_result blocks in the next user message.
        """
        turn_starts = [
            i for i, m in enumerate(self.messages)
            if m["role"] == "user" and isinstance(m["content"], str)
        ]
        if len(turn_starts) <= self.max_user_turns:
            return 0
        cutoff = turn_starts[-self.max_user_turns]
        dropped = cutoff
        self.messages = self.messages[cutoff:]
        return dropped


# ───────────────────────────────────────────────────────────────────────
# AgentShell
# ───────────────────────────────────────────────────────────────────────

class AgentShell:
    """Single agent loop with typed state, token budgeting, and structured
    results. Owns its MCP sessions for the duration of the async context.

    Phase 1 usage:

        async with AgentShell(mode="single_turn") as shell:
            result = await shell.turn("Configure my Orin NX for CUDA 12")
            print(result.text)
            print(f"used {result.input_tokens}+{result.output_tokens} tokens")

    Multiple turn() calls on the same shell instance accumulate messages
    and budget — that is the REPL semantics. For independent single-turn
    queries, create a new shell per query (cheap apart from MCP spawn,
    which is Phase 2 territory to optimize).

    Phase 2 status (post-2d migration):
      - mode IS now acted on for MessageHistory strategy selection (REPL
        gets sliding window, single_turn gets unbounded). Mode-aware
        SYSTEM_PROMPT (G2) is still Phase 2e work.
      - history (G1): pruned after every turn() via the configured strategy.
      - state fields are still not auto-populated from tool_use blocks
        (= Phase 2e or later — requires per-tool result parsers).
    """

    def __init__(
        self,
        mode: AgentMode = "single_turn",
        state: Optional[AgentState] = None,
        budget: Optional[TokenBudget] = None,
        model: Optional[str] = None,
        knowledge_server_path: Path = _KNOWLEDGE_SERVER,
        rag_server_path: Path = _RAG_SERVER,
    ) -> None:
        self.mode: AgentMode = mode
        self.state: AgentState = state or AgentState()
        self.budget: TokenBudget = budget or TokenBudget()
        self.model: str = model or os.getenv(
            "ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"
        )
        self.system_prompt: str = SYSTEM_PROMPT  # mode-aware variant: Phase 2e
        self.history: MessageHistory = MessageHistory(
            strategy=self._strategy_for_mode(mode),
        )
        self.tool_call_history: list[ToolCallTrace] = []

        self._k_path = knowledge_server_path
        self._r_path = rag_server_path

        # Set inside __aenter__:
        self._client: Optional[anthropic.Anthropic] = None
        self._k_session: Optional[ClientSession] = None
        self._r_session: Optional[ClientSession] = None
        self._tools: Optional[list[dict]] = None
        self._session_map: Optional[dict[str, ClientSession]] = None
        self._stack: Optional[contextlib.AsyncExitStack] = None

    # ─── mode → strategy mapping ───────────────────────────────────────

    @staticmethod
    def _strategy_for_mode(mode: AgentMode) -> HistoryStrategy:
        """Pick a MessageHistory strategy that matches the mode's lifetime.

        - single_turn: one shell per query, history is short by construction;
                       unbounded is fine.
        - repl:        long-lived shell across user turns; without sliding
                       the input token cost grows linearly with turn count.
                       sliding caps that at max_user_turns recent turns.
        - troubleshoot: this mode is currently NOT routed through AgentShell
                       (single Anthropic call, server-side web_search; see
                       src/troubleshoot.py for the rationale). The mapping
                       exists for completeness.
        """
        return {
            "single_turn": "unbounded",
            "repl": "sliding",
            "troubleshoot": "unbounded",
        }[mode]

    @property
    def messages(self) -> list[dict]:
        """Backward-compat alias for self.history.messages.

        Read-only convention: external callers should use this for
        inspection (`if not shell.messages:` etc.). Internal shell code
        appends via `self.history.append(...)` so the intent — adding
        a message that participates in the pruning policy — is explicit.
        """
        return self.history.messages

    # ─── async context manager ─────────────────────────────────────────

    async def __aenter__(self) -> "AgentShell":
        self._stack = contextlib.AsyncExitStack()
        # MCP's stdio_client uses a small env whitelist; forward FASTMCP_*
        # control vars explicitly so server-side settings (e.g. silent banner
        # for demo recording) propagate.
        mcp_env = {
            k: v for k, v in os.environ.items() if k.startswith("FASTMCP_")
        } or None
        k_params = StdioServerParameters(
            command="python", args=[str(self._k_path)], env=mcp_env
        )
        r_params = StdioServerParameters(
            command="python", args=[str(self._r_path)], env=mcp_env
        )
        kr, kw = await self._stack.enter_async_context(stdio_client(k_params))
        rr, rw = await self._stack.enter_async_context(stdio_client(r_params))
        self._k_session = await self._stack.enter_async_context(ClientSession(kr, kw))
        self._r_session = await self._stack.enter_async_context(ClientSession(rr, rw))
        await self._k_session.initialize()
        await self._r_session.initialize()

        k_tools = (await self._k_session.list_tools()).tools
        r_tools = (await self._r_session.list_tools()).tools
        self._session_map = {}
        for t in k_tools:
            self._session_map[t.name] = self._k_session
        for t in r_tools:
            self._session_map[t.name] = self._r_session
        self._tools = [
            {"name": t.name, "description": t.description, "input_schema": t.inputSchema}
            for t in (list(k_tools) + list(r_tools))
        ]
        self._client = anthropic.Anthropic()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self._stack = None
        self._client = None
        self._k_session = None
        self._r_session = None
        self._tools = None
        self._session_map = None

    # ─── public API ────────────────────────────────────────────────────

    @property
    def knowledge_session(self) -> ClientSession:
        """Direct access to the knowledge MCP session.

        Use ONLY for one-off host-side tool calls outside the agent loop
        (e.g. REPL's opening probe that needs to call detect_connected_hardware
        before the first user turn). Inside the agent loop, the LLM dispatches
        tools via shell.turn() — do not use this property there.
        """
        if self._k_session is None:
            raise RuntimeError(
                "Shell must be entered as async context manager before accessing knowledge_session"
            )
        return self._k_session

    @property
    def rag_session(self) -> ClientSession:
        """Direct access to the RAG MCP session. See knowledge_session docstring."""
        if self._r_session is None:
            raise RuntimeError(
                "Shell must be entered as async context manager before accessing rag_session"
            )
        return self._r_session

    async def turn(
        self,
        user_input: str,
        on_step: Optional[Callable[[str, dict, str], None]] = None,
        on_thinking: Optional[Callable[[str], None]] = None,
    ) -> TurnResult:
        """Run one user message through the agent loop, return TurnResult.

        Multiple turn() calls on the same shell accumulate into shell.messages
        and shell.budget — long-running REPL semantics.

        Raises:
            RuntimeError: if shell was not entered as async context manager
            BudgetExceededError: if cumulative token usage passes the cap
        """
        if self._client is None or self._tools is None or self._session_map is None:
            raise RuntimeError(
                "AgentShell must be entered as async context manager before turn()"
            )

        # Track this turn()'s incremental contribution to lifetime budget
        start_input = self.budget.used_input
        start_output = self.budget.used_output
        history_start = len(self.tool_call_history)

        self.history.append({"role": "user", "content": user_input})

        final_text = ""
        finish_reason: Literal["end_turn", "max_turns", "budget_exceeded"] = "end_turn"
        turn_index = 0

        for turn_index in range(MAX_TURNS):
            # G8: pre-flight budget check — raise BEFORE next API spend
            self.budget.raise_if_exhausted()

            response = await asyncio.to_thread(
                self._call_with_retry, list(self.history.messages)
            )

            # G9: capture usage every turn, before doing anything else
            if response.usage is not None:
                self.budget.add_usage(response.usage)

            if response.stop_reason == "end_turn":
                final_text = next(
                    (b.text for b in response.content if hasattr(b, "text")), ""
                )
                self.history.append({"role": "assistant", "content": response.content})
                break

            # tool_use turn: dispatch each tool, collect results
            tool_results: list[dict] = []
            for block in response.content:
                if block.type == "text":
                    if on_thinking and getattr(block, "text", None):
                        on_thinking(block.text)
                    continue
                if block.type != "tool_use":
                    continue
                sess = self._session_map.get(block.name)
                t_start = time.perf_counter()
                if sess is None:
                    result_text = json.dumps({"error": f"unknown tool: {block.name}"})
                else:
                    result = await sess.call_tool(block.name, arguments=block.input)
                    result_text = result.content[0].text if result.content else "{}"
                latency_ms = (time.perf_counter() - t_start) * 1000

                trace = ToolCallTrace(
                    name=block.name,
                    args=dict(block.input or {}),
                    result_text=result_text,
                    latency_ms=latency_ms,
                    turn_index=turn_index,
                )
                self.tool_call_history.append(trace)
                # G3 partial fix: auto-populate shell.state from tool results
                # for the two tools where the mapping is direct. Other state
                # fields (product/version/additional_sdks) are derived by the
                # agent across multiple tool calls and stay extracted by A1
                # from final text.
                self._update_state_from_tool_result(block.name, result_text)
                if on_step:
                    on_step(block.name, dict(block.input or {}), result_text)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_text,
                })

            self.history.append({"role": "assistant", "content": response.content})
            self.history.append({"role": "user", "content": tool_results})
        else:
            # for-else: loop completed without break → MAX_TURNS exhausted
            finish_reason = "max_turns"

        # G1: prune oldest user turns per MessageHistory strategy. For
        # unbounded (single_turn) this is a no-op; for sliding (REPL) it
        # caps the input-token growth across long sessions.
        self.history.prune()

        incremental_input = self.budget.used_input - start_input
        incremental_output = self.budget.used_output - start_output
        turn_tool_calls = self.tool_call_history[history_start:]

        return TurnResult(
            text=final_text,
            tool_calls=turn_tool_calls,
            turns_used=turn_index + 1,
            finish_reason=finish_reason,
            input_tokens=incremental_input,
            output_tokens=incremental_output,
        )

    # ─── internals ─────────────────────────────────────────────────────

    def _update_state_from_tool_result(self, tool_name: str, result_text: str) -> None:
        """Auto-populate self.state fields from known tool results.

        Currently handles two tools whose result shape directly maps to a
        state field:

          - detect_connected_hardware: sets state.hardware_detected=True
            and state.detected_devices from result["devices"] (or
            result["connected"] if the server uses the older key).
          - lookup_target_id: sets state.target from result["target_id"]
            (skipped if result carries an "error" key — unknown board).

        Defensive by design — JSON parse failures, missing keys, or
        unknown tool names all skip silently. The state stays at whatever
        value it had before; never raises mid-turn.

        Not handled (and deliberately left to A1's text-based extraction):
          product / version / additional_sdks — the agent CHOOSES these
          across multiple tool calls based on LLM reasoning; no single
          tool result carries the final selection. The final command /
          INI is the authoritative source, and A1 already parses it with
          shlex + configparser.
        """
        try:
            data = json.loads(result_text)
        except (json.JSONDecodeError, ValueError):
            return
        if not isinstance(data, dict):
            return

        if tool_name == "detect_connected_hardware":
            # Probe ran successfully regardless of whether devices were found.
            self.state.hardware_detected = True
            devices = data.get("devices") or data.get("connected") or []
            if isinstance(devices, list):
                # Normalize: some shapes have dict entries, others bare strings
                self.state.detected_devices = [
                    d if isinstance(d, dict) else {"name": str(d)}
                    for d in devices
                ]
            return

        if tool_name == "lookup_target_id":
            if "error" in data:
                return
            target_id = data.get("target_id")
            if target_id and isinstance(target_id, str):
                self.state.target = target_id
            return

    def _call_with_retry(self, messages: list[dict], max_attempts: int = 4):
        """Anthropic call with exponential backoff on rate limit.

        Lives on the shell (not a free function) so tests can override it
        on a single instance without monkeypatching the module.
        """
        for attempt in range(max_attempts):
            try:
                return self._client.messages.create(
                    model=self.model,
                    max_tokens=4096,
                    system=self.system_prompt,
                    tools=self._tools,
                    messages=messages,
                )
            except anthropic.RateLimitError:
                if attempt == max_attempts - 1:
                    raise
                time.sleep(15 * (attempt + 1))
