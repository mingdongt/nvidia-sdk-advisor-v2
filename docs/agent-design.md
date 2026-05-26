# Agent Shell Design Manual

> This is the deep dive that [README §Architecture](../README.md#architecture) points to. README gives the conclusions; this document gives the decision process for the *agent shell* — the loop that sits above the MCP tool layer documented in [mcp-design.md](./mcp-design.md).

## Who this is for

- Engineers extending or replacing this repo's agent loop
- Reviewers evaluating the repo as a portfolio piece
- The author, six months from now, trying to recall "why did we do it this way"

## What this is NOT

- Not a generic agent / tool-use introduction — read [Anthropic Tool Use docs](https://docs.anthropic.com/en/docs/build-with-claude/tool-use) for that
- Not a duplicate of [mcp-design.md](./mcp-design.md) — that document explains the tool layer; this one explains the agent shell that sits above it. The two are sister docs and cross-reference each other
- Not a per-function reference — use docstrings + IDE for that

## Table of contents

- Ch 1. Why single-agent + tool-use loop, not multi-agent / plan-execute *(TBD)*
- Ch 2. Three entry points, one agent shell *(TBD)*
- Ch 3. State management: messages list, not typed state *(TBD)*
- Ch 4. Context budget: turns, tokens, summarization *(TBD)*
- Ch 5. Routing contract: prompt-as-contract vs hard-coded FSM *(TBD)*
- Ch 6. Backend pluggability: ablation by env var *(TBD)*
- Ch 7. Prompt versioning *(TBD)*
- [Ch 8. Where the design isn't honest yet](#ch-8-where-the-design-isnt-honest-yet)
- Appendix *(TBD)*

> **Drafting note.** This document is being written Ch 8 first — the self-critique is the highest-value section, so it ships first. Chapters 1-7 are scaffolded but not yet drafted.

---

## Ch 8. Where the design isn't honest yet

Honestly listing unsolved problems is a sign of design maturity. [mcp-design.md Ch 7](./mcp-design.md#ch-7-where-the-design-isnt-honest-yet) lists four MCP-layer dishonesties. This chapter lists **nine** agent-shell dishonesties, in three tiers: architectural (G1-G3), implementation (G4-G5, G8), and hygiene (G6-G7, G9).

Each gap follows a fixed template: **Symptom · Impact · Production fix · Why not fixed in demo phase**.

### Architectural gaps

#### G1 — REPL context grows unbounded, no pruning or summarization

**Symptom.** [repl.py:162](../src/repl.py#L162) appends each user input to `messages`; [repl.py:195-196](../src/repl.py#L195-L196) appends the full `response.content` (including all tool_use blocks) plus a tool_result block every turn. There is no sliding window, no summarization, no turn-boundary reset.

**Impact.** Anthropic billing charges `input_tokens × every API call`. If a user runs the REPL for 30 turns, the 30th turn re-sends all 29 prior turns' tool calls + tool results as input. The message tail grows by roughly 1.5-3k tokens per turn (manifest dumps, log tails, generated INI text), so by turn 30 the per-turn input cost is roughly **30× the first turn**. A long REPL session is a denial-of-wallet vector against the operator. The current code has no defense.

**Production fix.** Two layers:

(a) A sliding window over message history, dropping turns older than N. The pruning is not just `messages[-N:]` — `tool_result` blocks must remain paired with their originating `tool_use` blocks, or Anthropic returns 400. Roughly 80-120 LOC of careful pairing logic.

(b) When a phase concludes (configure done, troubleshoot done), summarize the closed phase into a single assistant message — *"Configured Jetson Orin NX with JetPack 6.2.2 + DeepStream 7.0; INI saved to output/foo.ini"* — and replace the underlying turn history. Tier (b) requires phase-boundary signaling from the orchestrator and is the bigger architectural change.

**Why not fixed in demo phase.** Demo sessions are 1-5 turns. The bloat is real but the wallet damage is theoretical until someone leaves a REPL open. Surfacing the problem here is higher-ROI than implementing a fix that no demo trace will exercise. The fix becomes mandatory the moment a real user runs an extended troubleshoot session.

---

#### G2 — One SYSTEM_PROMPT, three incompatible entry-point lifecycles

**Symptom.** Three entry points consume the same `prompts/1.0.0/system-prompt.md`:

| Entry point | Lifecycle | Messages behavior |
|---|---|---|
| `run_agent_single_turn` ([agent.py:54](../src/agent.py#L54)) | stateless | reset every call |
| `run_repl` ([repl.py:112](../src/repl.py#L112)) | stateful | accumulate across user inputs |
| `run_troubleshoot` ([troubleshoot.py](../src/troubleshoot.py)) | mode-switched | independent loop, own prompt |

But the shared prompt at [system-prompt.md:9](../src/prompts/1.0.0/system-prompt.md#L9) reads:
> *Call detect_connected_hardware once (**if not already done in this conversation**).*

The clause **"if not already done in this conversation"** is meaningless in single-turn mode — `messages` is always fresh, so the model has no prior tool call to detect.

**Impact.** Two leaks. (a) `single_turn` calls `detect_connected_hardware` on every invocation, wasting one subprocess probe per query (~50-200ms). (b) The prompt asserts a multi-turn conversation contract that two of the three entry points cannot honor. Future prompt revisions that lean further on conversational state will silently regress single-turn behavior without test signal.

**Production fix.** One of two paths:

(a) Make the prompt mode-aware — `prompts/1.0.0/system-prompt-{mode}.md` with mode-specific routing sections, picked at agent boot. One new file + one if/else in agent.py.

(b) Unify the three entry points behind a single `AgentShell` class with explicit `mode` flag injected as message preamble. Path (b) is the right long-term choice but invasive — every call site changes.

**Why not fixed in demo phase.** The leak is silent in score terms (`detect_connected_hardware` is idempotent, so calling it twice doesn't break correctness), so no eval case catches it. The cost surfaces only on the Anthropic invoice diff post-hoc — invisible until per-tool telemetry (cross-ref [mcp-design.md Ch 7.4](./mcp-design.md#4-no-per-tool-latency--cost-telemetry)) lands.

---

#### G3 — Multi-phase orchestration has no typed state object; phases communicate via filesystem and hardcoded constants

**Symptom.** [orchestrator.py:341](../src/orchestrator.py#L341) runs the configure phase as `run_agent_single_turn(user_input, ...)`. [orchestrator.py:381](../src/orchestrator.py#L381) runs troubleshoot as `run_troubleshoot(failure_log, ...)`. These two calls share **no Python object** — the agent that wrote the `.ini` does not pass `product` / `version` / `target` / `additional_sdks` to the agent that diagnoses the failure.

State flows out-of-band: configure writes `output/<stem>.ini`, troubleshoot reads the log file path and infers metadata from the log filename via [log_parser.py](../src/log_parser.py)'s `_FILENAME_RE`. In `--mock-install` mode the metadata is baked in as module-level constants:

```python
# orchestrator.py:146
_MOCK_TARGET_BOARD = "Orin_NX_16GB"
_MOCK_JETPACK = "6.1"
_MOCK_HOST = "Linux"
```

**Impact.** In `--mock-install` mode the chain runs end-to-end because the constants paper over the missing state passing. **In real-hardware mode the chain is broken** — troubleshoot would have no way to confirm "the user is on Orin NX 16GB" without re-prompting or re-detecting; retry phase would not know which `.ini` to feed back to NvSDKManager. The code itself admits this at [orchestrator.py:309-314](../src/orchestrator.py#L309-L314):

```python
if not mock_install:
    console.print("[red]--full without --mock-install requires real hardware and is not "
                  "yet implemented. Use --full --mock-install for the demo flow.[/red]")
    sys.exit(2)
```

The early exit IS the honest disclosure that the state-passing is missing.

**Production fix.** A typed `AgentState` dataclass that lives at the orchestrator level and is passed by reference into each phase. Minimal fields:

```python
@dataclass
class AgentState:
    product: str
    version: str
    target: str
    target_os: str
    additional_sdks: list[str]
    last_ini_path: Path | None
    last_install_log: Path | None
    last_install_exit_code: int | None
    attempt_number: int
```

Each phase reads from and writes to this object. **The state IS the contract between phases.** Today the messages list pretends to be that contract but cannot survive the phase boundary because `run_agent_single_turn` is stateless and `run_troubleshoot` is a separate agent loop with its own context.

**Why not fixed in demo phase.** The mock fills the gap. Honest path is to flag this loudly here, not to ship a typed state object that no test exercises and that real-hardware mode (its only consumer) doesn't exist yet. The right time to introduce `AgentState` is the same week as real-hardware `--full` — these are coupled requirements, and shipping one without the other is premature abstraction.

---

### Implementation gaps

#### G4 — REPL's opening probe duplicates a tool call

**Symptom.** [repl.py:141](../src/repl.py#L141) invokes `_opening_probe` ([repl.py:96-109](../src/repl.py#L96-L109)), which calls `detect_connected_hardware` directly against the MCP session and formats the result as an opening line shown to the user. Then [repl.py:160](../src/repl.py#L160) embeds that opening *text* (not the tool result) into the user's first message: `f"{opening}\n\nUser response: {user_input}"`. The agent never sees the structured `tool_result` block — only natural-language paraphrase. Meanwhile [system-prompt.md:9](../src/prompts/1.0.0/system-prompt.md#L9) instructs the agent to call `detect_connected_hardware` itself. **Result: the tool fires twice every REPL session** — once from the host code, once from the agent.

**Impact.** One wasted subprocess probe per session (~50-200ms) plus one wasted Anthropic round-trip for a tool call whose answer was already known. Worse, the host probe's result is *discarded* — only the natural-language opening survives, so any structured fields the second probe surfaces (USB ports, device fingerprints, multi-device disambiguation) had to be re-extracted by the model from a sentence the host already had in JSON form.

**Production fix.** Two paths, both small:

(a) Inject the host-side probe result as a synthetic `tool_use` + `tool_result` pair in the initial `messages` list — the agent sees a finished tool call, satisfies the prompt's "call once" rule without re-invoking, and gets the structured data verbatim.

(b) Remove the host-side probe entirely and let the agent's first turn make the call. Loses the immediate opening line ("Detected Jetson Orin NX...") that's nice in REPL UX, but removes the duplication.

(a) is the better fix — keep the UX, kill the duplication. Roughly 20 LOC.

**Why not fixed in demo phase.** The duplication doesn't affect correctness and the latency is negligible vs. the rest of a turn (~50-200ms vs. 5-30s). It's a tax on every REPL session but invisible in score-based eval. Surfacing it here is higher-ROI than the 20-line fix.

---

#### G5 — Phase 4 design doc and SYSTEM_PROMPT disagree on whether the two RAG tools are exclusive or sequential

**Symptom.** [mcp-design.md Ch 5](./mcp-design.md#exclusive-branches) describes Phase 4 as having only **exclusive branches**:
> *Phase 4: `lookup_container_reqs` (exact) vs `search_3p_sample_repos` (semantic) — picked by user intent, never both.*

But [system-prompt.md:31-32](../src/prompts/1.0.0/system-prompt.md#L31-L32) reads:
> *User describes a workload without naming a product → `search_3p_sample_repos` **FIRST** to find the matching NVIDIA sample*

The word "FIRST" implies there's a second call after — i.e., the two tools are **sequential**, not exclusive. The design doc says one thing, the prompt says another.

**Impact.** Two readers will form two different mental models. A reviewer cross-checking mcp-design.md against the prompt will find the contradiction in under sixty seconds and treat it as evidence that the docs are aspirational rather than authoritative. The agent's actual behavior is governed by the prompt, not the doc, so the doc is the one that's wrong — but only because no one re-aligned the doc after the prompt was revised.

**Production fix.** Reconcile to **sequential** (the prompt's view, which matches real usage): after `search_3p_sample_repos` returns a top-hit repo, looking up its container requirements via `lookup_container_reqs` is a legitimate next step, not a violation. Update [mcp-design.md Ch 5](./mcp-design.md#exclusive-branches) to drop the "never both" claim and rename the section from "Exclusive branches" to "Branching points." Five-minute edit.

**Why not fixed in demo phase.** This is a documentation drift problem, not a code problem. The cost of fixing is trivial; the cost of *catching* it (a structured doc/prompt consistency check) is the harder long-term play. Listed here so the next consistency review can fold it in.

---

#### G8 — `MAX_TURNS` caps turn count, not token budget

**Symptom.** [agent.py:30](../src/agent.py#L30) defines `MAX_TURNS = 50`. The loop at [agent.py:105](../src/agent.py#L105) terminates after 50 iterations or `stop_reason == "end_turn"`, whichever comes first. There is no cap on cumulative input tokens, output tokens, or wall-clock time. The comment at [agent.py:28-30](../src/agent.py#L28-L30) explicitly notes *"Successful runs typically use 6-12 turns; 50 is wide margin"* — the cap is designed to catch error-loops, not to control cost.

**Impact.** If a tool keeps returning malformed JSON or an `{"error": "..."}` payload the model can't recover from, the loop runs 50 turns. Each turn: one `client.messages.create()` (input includes all prior turns) + one tool call. A 50-turn stuck loop on a session whose `tool_result` blocks average 2k tokens each burns roughly **30-50k input tokens × 50 turns ≈ 1.5-2.5M cumulative input tokens** — \$4-7 on Haiku, \$30-45 on Opus, per stuck query. **Turn count is the wrong unit of budget.**

**Production fix.** Track cumulative token spend across turns and add a hard cap before the next `messages.create`:

```python
total_input_tokens += response.usage.input_tokens
if total_input_tokens > MAX_INPUT_TOKENS:  # e.g. 200_000
    raise BudgetExceededError(...)
```

Combined with `MAX_TURNS`, this gives belt-and-suspenders: turn cap catches infinite loops, token cap catches expensive loops. Roughly 5 LOC.

**Why not fixed in demo phase.** Demo runs are short enough that 50 turns is a theoretical risk. The fix is cheap and should arrive in the same patch as **G9** (per-turn usage capture) — they share the same infrastructure.

---

### Hygiene gaps

#### G6 — No cross-session persistence; REPL exit drops all state

**Symptom.** [repl.py:139](../src/repl.py#L139) initializes `messages: list[dict] = []` at the start of `run_repl()`. When the user exits the REPL (`Ctrl-C`, `exit`, `quit`), the process terminates and `messages` is garbage-collected with it. The next `python main.py` invocation starts from `messages = []` again. There is no `~/.sdk-advisor/sessions/` directory, no SQLite, no JSONL append.

**Impact.** A user who spends 20 minutes configuring a complex Jetson setup, closes the terminal, and reopens it tomorrow gets zero continuity — the agent has no memory of the prior session. Common questions like *"resume where we left off"* or *"what was that DeepStream config I picked yesterday"* require the user to re-narrate everything.

**Production fix.** This one is genuinely a **design choice, not a bug** — but it's not documented as such, and that's the real gap. Two options:

(a) Document the choice explicitly in README: *"Each REPL session is independent. To resume work, re-prompt with prior config or re-run with `--query`. Cross-session persistence is intentionally out of scope for the portfolio demo."*

(b) Add minimal session persistence: JSONL-serialize `messages` to `~/.sdk-advisor/sessions/<timestamp>.jsonl` on exit, list past sessions with `python main.py --resume`. Roughly 60 LOC.

**For the portfolio, do (a), not (b).** The gap is in the docs, not the code.

**Why not fixed in demo phase.** (a) is in fact the *right* answer — session statelessness is a defensible choice for a CLI agent. The honest disclosure is "we chose stateless, here's why" rather than "we forgot to implement persistence." This entry exists to make the choice deliberate rather than accidental.

---

#### G7 — Dead tool labels in the REPL trace formatter

**Symptom.** [repl.py:29-45](../src/repl.py#L29-L45) defines `_STEP_LABELS`, a dict mapping tool names to user-facing labels for the live trace output. Two entries are dead:

```python
"search_forum_threads": "Searching forum threads",
"search_docs": "Searching NVIDIA docs",
```

But [system-prompt.md:34-38](../src/prompts/1.0.0/system-prompt.md#L34-L38) explicitly states these tools were removed and replaced by Anthropic's server-side `web_search_20250305`:
> *We removed the dedicated MCP wrappers for those — they were thin domain-filter shims and your built-in WebSearch handles the same task more cleanly.*

The labels remain even though the tools they label can never fire.

**Impact.** Zero functional impact. But code reviewers will spot the dead entries and lower their confidence that the rest of the file is up-to-date. Cosmetic, but cosmetic-with-signal.

**Production fix.** Delete the two entries. One-line cleanup.

**Why not fixed in demo phase.** Listed here for completeness — the entire point of a "where it isn't honest yet" chapter is to catch even the small drift items, because the *practice* of listing them is the discipline. Fixing this one is approximately 30 seconds of work; it would be in the next commit if this document weren't pinning a specific point-in-time snapshot for portfolio review.

---

#### G9 — `response.usage` is discarded; per-turn cost is invisible from inside the application

**Symptom.** [agent.py:44-47](../src/agent.py#L44-L47) calls `client.messages.create(...)` and returns the response object intact, but the calling loop at [agent.py:105-132](../src/agent.py#L105-L132) only inspects `response.stop_reason` and `response.content`. The `response.usage` field (Anthropic's per-call `input_tokens` / `output_tokens` / `cache_read_input_tokens`) is never read. There is no cumulative tracker across turns, no JSONL emission, no log line.

**Impact.** Three blindnesses:

(a) Per-query cost is unknown until the Anthropic invoice arrives.
(b) Per-turn cost growth in REPL (the G1 problem) is unobservable from inside the application — only externally.
(c) Token usage cannot be sliced by tool (whose `tool_result` blew up the context?), by phase (configure vs. troubleshoot), or by case (which eval case is expensive?).

This is the same gap that [mcp-design.md Ch 7.4](./mcp-design.md#4-no-per-tool-latency--cost-telemetry) flags from the tool-layer angle. From the agent-shell angle it's even cheaper to fix — the data is already in the response object.

**Production fix.** Three lines per turn:

```python
total_input_tokens += response.usage.input_tokens
total_output_tokens += response.usage.output_tokens
# emit JSONL: {"turn": i, "tool_calls": [...], "tokens": {...}, "latency_ms": ...}
```

Combined with **G8** (token budget cap) and the eval framework's JSONL emission, this becomes the foundation for the entire telemetry layer. **One small addition unlocks the whole `docs/eval-design.md` story.**

**Why not fixed in demo phase.** This is the most asymmetric gap in the list — fix cost is ~5 LOC, value is "everything downstream of telemetry." It should be the first gap closed when moving from demo to production. The reason it's listed here rather than fixed is precisely to make that asymmetry visible: *every other gap in this chapter is easier to reason about once G9 is closed.*

---

### Chapter takeaway

Eight of these nine gaps were not on the README or in mcp-design.md's Ch 7 — they surfaced from a structured code walkthrough done specifically to write this chapter. **The act of writing self-critique surfaced gaps that the original design process did not.** That alignment problem — design intent vs. shipped behavior — is itself the meta-lesson of this chapter, and the reason every agent project of non-trivial size should have a document like this one.

---
