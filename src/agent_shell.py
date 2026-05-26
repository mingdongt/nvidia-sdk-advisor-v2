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

    Phase 1 deliberate non-features:
      - mode is stored but not yet acted on (mode-aware prompts = G2 = Phase 2)
      - messages list is unbounded (sliding window = G1 = Phase 2)
      - state fields are not auto-populated from tool_use (= Phase 2)
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
        self.system_prompt: str = SYSTEM_PROMPT  # mode-aware variant: Phase 2
        self.messages: list[dict] = []           # unbounded in Phase 1
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

        self.messages.append({"role": "user", "content": user_input})

        final_text = ""
        finish_reason: Literal["end_turn", "max_turns", "budget_exceeded"] = "end_turn"
        turn_index = 0

        for turn_index in range(MAX_TURNS):
            # G8: pre-flight budget check — raise BEFORE next API spend
            self.budget.raise_if_exhausted()

            response = await asyncio.to_thread(
                self._call_with_retry, list(self.messages)
            )

            # G9: capture usage every turn, before doing anything else
            if response.usage is not None:
                self.budget.add_usage(response.usage)

            if response.stop_reason == "end_turn":
                final_text = next(
                    (b.text for b in response.content if hasattr(b, "text")), ""
                )
                self.messages.append({"role": "assistant", "content": response.content})
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
                if on_step:
                    on_step(block.name, dict(block.input or {}), result_text)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_text,
                })

            self.messages.append({"role": "assistant", "content": response.content})
            self.messages.append({"role": "user", "content": tool_results})
        else:
            # for-else: loop completed without break → MAX_TURNS exhausted
            finish_reason = "max_turns"

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
