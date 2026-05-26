"""SDK Advisor agent core — thin wrapper layer over AgentShell.

Post Phase 2b: the agent loop lives entirely in src/agent_shell.py. This
module is now just two things:

  - SYSTEM_PROMPT       : the versioned system prompt, exported because
                          cli_backend.run_with_tools and other CLI paths
                          need it as a literal string.
  - run_agent_single_turn: thin async wrapper that constructs an AgentShell,
                          runs one turn, returns the final assistant text.
                          Preserved for backward compatibility with eval
                          runners, tests, and orchestrator code.

New code that wants per-turn token usage, tool call traces, or finish
reason should import AgentShell directly:

    async with AgentShell() as shell:
        result = await shell.turn(user_input)
        # result.text, result.tool_calls, result.input_tokens, ...

See docs/agent-design.md for the refactor rationale.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Callable, Optional

# Versioned prompt assets live in prompts/<version>/ so the system prompt can
# be git-diffed and revised without touching code. Bump the version directory
# when introducing a non-backward-compatible prompt revision.
_PROMPT_DIR = Path(__file__).parent / "prompts" / "1.0.0"
SYSTEM_PROMPT = (_PROMPT_DIR / "system-prompt.md").read_text(encoding="utf-8")


async def run_agent_single_turn(
    user_input: str,
    on_step: Optional[Callable] = None,
    on_thinking: Optional[Callable[[str], None]] = None,
) -> str:
    """Single-prompt agent run. Backend dispatch via ANTHROPIC_BACKEND env var.

    - 'sdk' (default): AgentShell (Phase 2 migration target). Connects to both MCP servers.
    - 'cli':           claude CLI subprocess with MCP servers attached
    - 'cli-no-tools':  claude CLI subprocess, NO tools (baseline for 3-way comparison)

    Returns the final assistant text for backward compatibility. New callers
    that want token usage / tool traces / finish_reason should import
    AgentShell from src.agent_shell directly.
    """
    backend = os.getenv("ANTHROPIC_BACKEND", "sdk").lower()
    if backend == "cli":
        from src import cli_backend
        return await asyncio.to_thread(cli_backend.run_with_tools, user_input, SYSTEM_PROMPT)
    if backend == "cli-no-tools":
        from src import cli_backend
        return await asyncio.to_thread(cli_backend.run_no_tools, user_input)

    # Default: route through AgentShell. Single shell per call, MCP servers
    # spawn on enter and close on exit (same as the pre-migration behavior).
    #
    # Two additional failure modes vs. the original implementation:
    #   - BudgetExceededError raised mid-turn if cumulative input >200k or
    #     output >50k tokens. Older code had no cost cap. NEW guard, not a
    #     regression — it strictly tightens the failure contract.
    #   - finish_reason == "max_turns" (50-turn ceiling). The original code
    #     raised RuntimeError here; we re-raise below to preserve that
    #     contract for callers that depend on the exception.
    from src.agent_shell import AgentShell, MAX_TURNS
    async with AgentShell(mode="single_turn") as shell:
        result = await shell.turn(user_input, on_step=on_step, on_thinking=on_thinking)
    if result.finish_reason == "max_turns":
        raise RuntimeError(
            f"agent exceeded MAX_TURNS={MAX_TURNS} without stop_reason='end_turn' "
            f"— likely stuck in a tool error loop"
        )
    return result.text
