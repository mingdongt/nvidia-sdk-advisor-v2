# Eval Framework Design Manual

> Sister document to [`mcp-design.md`](./mcp-design.md) and [`agent-design.md`](./agent-design.md). README gives the conclusions and the scoreboards; this document gives the design decisions behind how those scores get computed.

## Who this is for

- Engineers extending the eval engine or writing new scorers
- Reviewers evaluating whether the scoreboard is trustworthy
- The author, six months from now, asking "wait, why did we split it this way"

## What this is NOT

- Not a list of the cases — case JSONLs in `eval/cases/L1/`, `L2/`, `L3/` are self-describing
- Not a critique of the legacy `tests/run_*_eval.py` runners — that's covered briefly in §1
- Not a generic eval-design tutorial — this is specific to a CLI agent with structured outputs

## Table of contents

- [Ch 1. Why redesign — what the legacy eval got wrong](#ch-1-why-redesign--what-the-legacy-eval-got-wrong)
- [Ch 2. The axes — five independent quality dimensions](#ch-2-the-axes--five-independent-quality-dimensions)
- [Ch 3. The layers — L1 / L2 / L3 case difficulty tiers](#ch-3-the-layers--l1--l2--l3-case-difficulty-tiers)
- [Ch 4. The arms — same case set, multiple configurations](#ch-4-the-arms--same-case-set-multiple-configurations)
- [Ch 5. Why deterministic scorers beat LLM judges where possible](#ch-5-why-deterministic-scorers-beat-llm-judges-where-possible)
- [Ch 6. JSONL as source of truth](#ch-6-jsonl-as-source-of-truth)
- [Ch 7. Where the design isn't honest yet](#ch-7-where-the-design-isnt-honest-yet)
- [Appendix](#appendix)

---

## Ch 1. Why redesign — what the legacy eval got wrong

The legacy setup was three ad-hoc runners in `tests/`:

| Track | What it did | What it actually measured |
|---|---|---|
| `run_smoke_eval.py` | regex on final reply for `--product`, `--target`, `--version` | "did the regex extract the expected tokens?" — not whether the agent did the right thing |
| `run_reasoning_eval.py` | LLM-as-judge (Haiku) scoring Haiku's own output | self-grading bias (Zheng et al. 2023, MT-Bench) |
| `run_troubleshoot_eval.py` | LLM-as-judge against forum-mined references | same self-grading + reference set itself noisy |

Three specific failure modes:

**1. Scenario-split, not axis-split.** A score change couldn't be attributed to "model is worse", "prompt is broken", "tool dispatch order regressed", or "agent capitulated to a malformed input" — all of those land in the same scoreboard cell.

**2. LLM judge is same-family.** Haiku judging Haiku is documented in the literature to bias roughly +5–10pp vs. independent eval. README's `3.56/5` and `3.66/5` are systematically generous.

**3. Agent runs 1×, judge runs 3×.** The 3× median was meant to reduce variance — but it captured judge variance, not agent variance. The same buggy agent reply got the same buggy judge consensus across the three runs.

Plus the obvious surface problems: smoke = 5 cases (Wilson 95% CI on 5/5 is ~50%–100%), no negative cases, no baseline for reasoning/troubleshoot beyond smoke's MCP-vs-no-tools ablation.

The redesign keeps the existing 3 case sets as a starting point but splits them along **axes (what we measure)** × **layers (case difficulty)** × **arms (architectural comparison)** so each score change is attributable.

---

## Ch 2. The axes — five independent quality dimensions

```
       ┌────────────────────────────────────────────────────────────┐
       │                  AGENT OUTPUT                              │
       │       (final text + tool dispatch trace + telemetry)       │
       └─────────────┬──────────────────────────────────────────────┘
                     │
   ┌─────────────────┼─────────────────┬─────────────┬─────────────┐
   ▼                 ▼                 ▼             ▼             ▼
[A1 Correctness] [A2 Compliance] [A3 Efficiency] [A4 Robustness] [A5 Capability]
INI + command    tool dispatch   token/latency   reject on bad   free-text
schema valid?    order vs        cost            input           quality
fields match     SYSTEM_PROMPT
expected?        contract?
DETERMINISTIC    DETERMINISTIC   REPORTING       DETERMINISTIC   LLM JUDGE
```

Each axis answers exactly one question:

### A1 — Correctness (deterministic)

**Question.** Did the agent produce the right artifact?

**Implementation** ([`eval/scorers/a1_correctness.py`](../eval/scorers/a1_correctness.py)).
Parse the final assistant text. Find the fenced `bash` block starting with
`sdkmanager`, parse its flags with `shlex` (handles quoted multi-word
values like `'DeepStream 7.0'`, and distinguishes boolean flags from
key-value flags by peeking at the next token). Find the fenced `ini`
block, parse with `configparser`, verify the three required sections are
present (`[client_arguments]`, `[pre-flash-settings]`, `[post-flash-settings]`).
Extract `product` / `version` / `target` / `additional_sdks`; compare
against `case.expected`. **Command flags are authoritative** when they
disagree with the INI — the command is what the user would actually run.

**Score**: `passed_field_checks / total_field_checks`. INI schema
violations are reported separately (don't directly affect the score) so
"correct config in a malformed file" is distinguishable from "wrong
config in a clean file".

### A2 — Compliance (deterministic)

**Question.** Did the agent follow the routing contract?

**Implementation** ([`eval/scorers/a2_compliance.py`](../eval/scorers/a2_compliance.py)).
Eight rules encode the `SYSTEM_PROMPT` dispatch contract (detect first, lookup before generate, mandatory tools present, validate_combo when extra SDK declared, etc.). Each rule is a pure predicate over the tool dispatch sequence + the case spec. Inapplicable rules don't penalize: L3 adversarial cases legitimately have no tool calls when the agent rejects.

**Score**: `passed_rules / applicable_rules`.

### A3 — Efficiency (reporting, not scoring)

**Question.** How expensive was this case?

**Implementation** ([`eval/scorers/a3_efficiency.py`](../eval/scorers/a3_efficiency.py)).
Tool count, input/output tokens, cache read tokens, turn count, wall latency, estimated USD using `TokenBudget.estimated_cost_usd(model)` (shared with the agent shell). Cost estimator covers Haiku 4.5 / Sonnet 4.6 / Opus 4.7; unknown models return 0.0 rather than fabricate a price.

**No pass/fail.** Efficiency is a reporting axis — downstream (regression detection, dashboard) decides what's "too expensive". `None` values propagate cleanly when telemetry isn't available (e.g. `ANTHROPIC_BACKEND=cli-no-tools`).

### A4 — Robustness (deterministic)

**Question.** Did the agent refuse to do an unsafe thing?

**Implementation** ([`eval/scorers/a4_robustness.py`](../eval/scorers/a4_robustness.py)).
Applies ONLY to L3 adversarial cases. The hard property: no fenced
`bash`/`sh`/`shell` block whose first non-blank line is `sdkmanager`.
Soft signal: presence of "rejection keywords" (clarification requests,
not-supported language, scope refusal) — surfaced in the result dict but
does NOT affect the score, because the agent might legitimately pivot to
*"What NVIDIA hardware are you working with?"* with no explicit rejection
phrase but still safe behavior.

**Score**: 1.0 if no command emitted; 0.0 if command emitted.

### A5 — Capability (LLM judge, planned)

**Question.** On free-text reasoning quality, where's the ceiling?

**Implementation** (not yet built). Will use Opus-as-judge for Haiku-as-agent
sessions to remove the same-family bias from §Ch 1. Calibration plan:
quarterly N=30 human evaluation, Cohen's kappa target ≥ 0.6.

---

## Ch 3. The layers — L1 / L2 / L3 case difficulty tiers

```
┌─────────────────────────────────────────────────────────────────┐
│ L1 — golden path        (5 cases today, target: 30–50)          │
│   common configs, must pass 100%, blocks release if any fail    │
│   axes applied: A1, A2, A3                                      │
├─────────────────────────────────────────────────────────────────┤
│ L2 — hard cases         (28 legacy cases today, target: 100–200)│
│   multi-hop / ambiguous / cross-product, threshold ≥ 70%        │
│   axes applied: A1, A2, A3, A5 (when shipped)                   │
│   AUTHORING DEFERRED — see eval/cases/L2/README.md              │
├─────────────────────────────────────────────────────────────────┤
│ L3 — adversarial        (30 cases today)                        │
│   impossible combos / prompt injection / unsupported hardware / │
│   ambiguous / nonsense / out-of-scope; agent must NOT generate  │
│   an sdkmanager command. Must pass 100%.                        │
│   axes applied: A2, A3, A4 (A1 doesn't apply — no expected      │
│   command to compare against)                                   │
└─────────────────────────────────────────────────────────────────┘
```

**Why three layers and not one big mixed set.** Different layers have
different acceptance bars and different value:

- L1 is **regression guard** — every PR runs it, any fail blocks merge
- L2 is **capability proof** — measures where the agent's reasoning ceiling actually sits, and is the right test bed for prompt revisions
- L3 is **safety guard** — proves the agent doesn't quietly comply with bad inputs

A single mixed scoreboard with arithmetic mean would let an L2 improvement mask an L3 regression. Separating them keeps each axis of quality independently visible.

**Why not 100% threshold on L2.** L2 cases are deliberately at or near the model's limit. A 100% threshold would either mean cases are too easy (no signal) or the model is overfit to them. 70% is a starting point; the right threshold is whatever stays above the no-tools arm by a meaningful margin (see Ch 4).

---

## Ch 4. The arms — same case set, multiple configurations

Every full eval run can be sliced by **arm**. Same case set, different agent configuration. The arm dimension is what makes architecture claims (rather than just product claims) provable.

| Arm | What it isolates | How it's wired |
|---|---|---|
| `main` | the production config — AgentShell + default model + MCP | runner default |
| `no-tools` | model alone, no MCP — proves the tool layer's contribution | `ANTHROPIC_BACKEND=cli-no-tools` |
| `cli` | Claude CLI with MCP attached (subscription path) | `ANTHROPIC_BACKEND=cli` |
| `opus` | swap to Opus for the agent (or for the judge) | `--model claude-opus-4-7` |
| `<prev-commit>` | rerun with checkout of prior git_sha — regression detection | manual checkout + tag |

The runner writes the `arm` field into every RunRecord. `eval/dashboard/summarize.py` slices by `(arm, layer, track)`. Cross-arm comparison is just a SQL-style group-by:

```bash
# Same cases, two arms, compared side by side
python -m eval.engine.runner eval/cases/L1 --tag baseline-main --arm main
python -m eval.engine.runner eval/cases/L1 --tag baseline-no-tools --arm no-tools
python -m eval.dashboard.summarize eval/runs/*baseline*
```

**The L1 ablation already provable today.** The README §Tool-layer ablation claim ("46.7% → 100%") was a one-off comparison. With arm-aware eval, that same claim becomes a routine release-gate check rather than a hand-curated study.

---

## Ch 5. Why deterministic scorers beat LLM judges where possible

A1, A2, and A4 are deterministic on purpose. A3 is reporting, not scoring. Only A5 uses an LLM judge — and only because there's no other way to score free-text reasoning quality.

The principle: **if the output has structure, score on the structure**.

| Output property | Right scorer |
|---|---|
| INI file validity | `configparser`, deterministic |
| Command syntax | `shlex`, deterministic |
| Tool dispatch order | trace inspection, deterministic |
| Reject behavior | substring check on fenced code blocks, deterministic |
| Diagnosis prose quality | LLM judge (last resort) |

This inverts the legacy ratio. Legacy was ~70% LLM judge (reasoning + troubleshoot tracks, four 1–5 axes each); new design is ~80% deterministic. Cost goes down (no judge calls for A1/A2/A4), variance goes down (deterministic is bit-exact), and the bias question disappears for the majority of cases.

LLM judges still belong somewhere — when the output is *unstructured prose evaluated against an unstructured reference* (which is the case for the troubleshoot track's diagnosis content), there's no schema to score against. For those cases, A5 uses Opus-as-judge for Haiku-as-agent runs to remove the same-family bias.

---

## Ch 6. JSONL as source of truth

Each eval run writes one JSONL file to `eval/runs/<timestamp>[_<tag>].jsonl`. Each line is one [`RunRecord`](../eval/engine/schemas.py) — one (case × arm × sample) tuple.

The schema (Pydantic, see `eval/engine/schemas.py`):

```
run_id          : unique per run
git_sha         : repo state at run time
prompt_version  : SYSTEM_PROMPT version
model           : Anthropic model id
arm             : main / no-tools / opus / cli / ...
sample_index    : 0..N for multi-sample runs
case_id         : matches the CaseSpec
case_layer      : L1 / L2 / L3
case_track      : smoke / reasoning / troubleshoot / adversarial
started_at      : ISO 8601 UTC
ended_at        : ISO 8601 UTC
latency_s       : end-to-end wall clock
tokens_in       : input tokens (None for arms without telemetry)
tokens_out      : output tokens (None for arms without telemetry)
turns           : agent loop iterations
tool_sequence   : list of tool names in dispatch order
tool_calls      : list of {name, input, output_text, latency_s}
output_text     : final assistant text
scores          : {axis_name: {...}} — populated by scorers
error           : exception type + message, or None
```

**JSONL is line-streamable.** Long-running eval doesn't lose partial progress on a crash; each completed case flushes immediately. Downstream tooling (dashboards, regression checks, ad-hoc grep) only needs to know how to read one JSON object per line.

**Append-only convention.** Runs aren't rewritten or amended. Each new run is a new file. Old runs are reference data for trend analysis. The dashboard summarizes; it doesn't mutate.

---

## Ch 7. Where the design isn't honest yet

In the spirit of [`mcp-design.md` Ch 7](./mcp-design.md#ch-7-where-the-design-isnt-honest-yet) and [`agent-design.md` Ch 8](./agent-design.md#ch-8-where-the-design-isnt-honest-yet), here are the eval framework's known gaps.

### 1. L2 hard cases — only 28 legacy cases, target is 100–200

**Symptom.** `eval/cases/L2/` holds two migrated files (reasoning, troubleshoot) from the legacy `tests/eval_cases/`. They were designed for the legacy scoring path, not for the multi-axis split. Stats produced from L2 today are reusable but not authoritative.

**Production fix.** Author 100–200 cases per the design template in `eval/cases/L2/README.md`. Source from NVIDIA Developer Forum queries, NGC catalog descriptions, and GitHub issues on `dusty-nv/jetson-inference` etc. This is not LLM-generable — see `eval/cases/L2/README.md` for why.

**Why not fixed yet.** Bandwidth — case authoring is a discrete 1–2 week task and the multi-axis framework is more valuable to ship first as scaffolding.

### 2. A5 LLM judge not implemented

**Symptom.** Free-text quality (the troubleshoot diagnosis content, reasoning track replies) has no automated scorer in the new framework. Legacy tracks did this with Haiku-as-judge but that's biased.

**Production fix.** Implement `eval/scorers/a5_capability.py` using Opus-as-judge (with a configurable judge-model arg so cross-provider judges are possible later). Calibration plan: human N=30 quarterly, Cohen's kappa ≥ 0.6 vs. judge.

**Why not fixed yet.** A5 is the most expensive axis to build (writing a good judge prompt is its own discipline) and lowest-priority because A1+A2 already cover the deterministic part of correctness, which is most of the value.

### 3. No release gate wired into CI

**Symptom.** L1 fail-any-case-blocks-release is policy, not enforced. There's no CI hook that runs `python -m eval.engine.runner eval/cases/L1` on every PR.

**Production fix.** GitHub Actions workflow on `pull_request` that runs L1 and L3, fails the build on any L1 case fail or any A4 < 1.0. Cost: ~$0.10 per PR on Haiku.

**Why not fixed yet.** This repo is a portfolio piece, not a team CI target. The gate is documented; implementing it is one YAML file once a team wants it.

### 4. Cross-run regression detection is manual

**Symptom.** `summarize.py` shows one or more runs at a point in time. There's no "compare run X to run Y, fail if any axis dropped by Δ" check.

**Production fix.** A `eval/dashboard/regress.py` that takes two JSONL paths and outputs a delta table + exit code. Combined with #3 this becomes the PR-gate enforcement.

**Why not fixed yet.** Same bandwidth tradeoff as #3 — useful when a team adopts it; over-engineering for solo use.

### 5. Per-tool cost attribution is not in the scoreboard

**Symptom.** A3 reports total input/output tokens per case, but not which tool's `tool_result` blew up the context. The shell has `tool_call_history` with per-tool latency, but token attribution per tool requires diffing message lengths between turns — not done today.

**Production fix.** Pre/post-turn message-length sampling inside `AgentShell.turn()`, surface in `TurnResult.tool_calls[i].input_tokens_added` etc.

**Why not fixed yet.** Useful for prompt-cost optimization in production; not yet justified at portfolio scale.

---

## Ch 8. First production run — 2026-05-27

This chapter doesn't document design — it documents what the framework caught the first time it ran end-to-end against L1 with two arms. Listed here so the design rationale in Ch 1–7 is anchored to a real artifact, not just intent. JSONLs committed under `eval/runs/2026-05-26T22-28-49_20260527-dual-arm.jsonl` (main) and `2026-05-26T22-31-29_20260527-dual-arm.jsonl` (no-tools).

### Setup

Same 5 L1 smoke cases run under two arms, one sample each. ~$0.12 total, ~6 minutes wall-clock.

| Arm | Backend | Model | Tools |
|---|---|---|---|
| `main` | AgentShell + Anthropic SDK | Haiku 4.5 | MCP knowledge + MCP corpus-rag |
| `no-tools` | Claude CLI subprocess | Opus 4.7 | none |

### Aggregate scoreboard

```
arm        L1.smoke   A1     A2     tok_in     tok_out    total$
---------- --------   ----   ----   --------   --------   --------
main       5/5        1.00   0.94   91.1k      4.8k       $0.1152
no-tools   5/5        0.80   0.56   n/a        n/a        n/a
```

(No-tools cost shows `n/a` only because the Claude CLI subprocess goes through the user's subscription, not the metered SDK path. A3 telemetry coverage limitation noted in Ch 4 / Appendix A2.)

### What the framework caught that legacy eval would have missed

**1. A2 compliance violations on the main arm — 2 of 5 cases.** The legacy `tests/run_smoke_eval.py` regex scorer would have given these cases the same `15/15` it always did, because the final `.ini` and command outputs are correct. The new A2 axis surfaces trace-level issues that A1 alone can't see:

  - `L1-smoke-orin-nano-cuda-jp6` — agent **skipped `detect_connected_hardware` entirely**. Went straight to `lookup_target_id`. Violates SYSTEM_PROMPT routing step 1 (`detect_hardware_present` rule failed).
  - `L1-smoke-orin-nx-latest-jp6` — agent called `detect_connected_hardware` AFTER `lookup_target_id`, not first. Violates the "must be first" clause (`detect_hardware_first` rule failed).

Both cases produced correct outputs, but the routing contract is being violated on 40% of L1 smoke cases. **This is exactly the kind of silent drift that A1-only or regex-on-text eval cannot see** — and the most direct evidence that the axis split was worth doing.

**2. No-tools hallucinations — concrete and named.** A1 dropped from 1.00 (main) to 0.80 (no-tools). The 0.20 gap is two specific failures:

  - `L1-smoke-agx-orin-deepstream7` — A1 = 0.33. Opus alone wrote `--product JETSON_LINUX` (target_id encoded as product — a category confusion) and dropped the `--additional-sdk 'DeepStream 7.0'` flag entirely.
  - `L1-smoke-jetson-nano-orig-yolo-jp4` — A1 = 0.67. Opus chose JetPack 6.1 for original Jetson Nano (4GB), which physically tops out at JetPack 4.6.4 — a real impossible-combo hallucination.

These match the README §Tool-layer ablation finding pattern: deterministic tool-grounding closes specific hallucination classes the model alone cannot avoid. The new framework reproduces that finding as a routine cross-arm check rather than a one-off README study.

### What the framework made visible by absence

  - **A2 on no-tools is artificially 0.56**, not because the agent behaved badly but because there are no MCP tools to dispatch — every "must fire X" rule trivially fails. Useful per-arm floor, misleading for cross-arm comparison. A future revision could mark A2 as `n/a` for tool-less arms (similar to how A3 is `n/a` when telemetry isn't exposed).
  - **A3 telemetry doesn't reach the no-tools arm.** Claude CLI subprocess doesn't surface `response.usage`, so input/output tokens are `None` and estimated cost is `n/a`. Cost-attributing the no-tools baseline would require either the Anthropic SDK path (different accounting) or instrumenting the CLI subprocess (out of scope today).
  - **A5 (LLM-as-judge capability) is still unimplemented**, so free-text reasoning quality from L2 reasoning cases doesn't have a structure to score against. Ch 7.2 tracks this as the next-priority gap.

### Cost & velocity

Main arm cost $0.1152 total — $0.023 per case average — at ~30s wall-clock per case. Linear extrapolation:

| Layer | Planned size | Cost/arm (Haiku) | Cost/arm (Opus) |
|---|---|---|---|
| L1 | 30-50 | $0.70 - $1.15 | $7 - $12 |
| L2 | 100-200 | $2.30 - $4.60 | $25 - $50 |
| L3 | 30-50 | $0.70 - $1.15 | $7 - $12 |

A multi-arm full sweep (main + no-tools + opus + cli) on full L1 + L2 + L3 ≈ $35-75 per release-gate run on current case counts; closer to $100-150 after L2 reaches its 100-200 target. **Eval cost is itself an ops cost worth budgeting**, not a fixed-overhead assumption.

### Chapter takeaway

> The framework's value isn't in producing higher scores — the main arm scored the same `5/5` on A1 that the legacy smoke eval reported as `15/15`. The value is in **catching what the legacy eval couldn't see** (the two A2 violations above) and **enabling cross-arm deltas as routine checks** (main vs no-tools as a release-gate signal rather than a one-off study). Writing the design doc first surfaced the gaps; running it once surfaced two specific contract violations in production code that nobody had noticed. That alignment problem — measured behavior vs design intent — is what eval is for.

---

## Appendix

### A1. Layer × Axis applicability matrix

|         | A1 Correctness | A2 Compliance | A3 Efficiency | A4 Robustness | A5 Capability |
|---------|:--:|:--:|:--:|:--:|:--:|
| **L1**  | ✓ | ✓ | ✓ | — | — |
| **L2**  | ✓ | ✓ | ✓ | — | ✓ (planned) |
| **L3**  | — | ✓ | ✓ | ✓ | — |

A1 doesn't apply to L3 (no expected command to compare against). A4 doesn't apply to L1/L2 (the agent IS supposed to produce a command).

### A2. Arm × Telemetry availability

|              | A1 | A2 | A3 tokens | A3 cost |
|--------------|:--:|:--:|:--:|:--:|
| `main`       | ✓  | ✓  | ✓ | ✓ |
| `opus`       | ✓  | ✓  | ✓ | ✓ |
| `cli`        | ✓ (text-based) | — (no trace exposed) | — | — |
| `cli-no-tools` | ✓ (text-based) | — (no MCP, no trace) | — | — |

CLI-backed arms can still be A1-scored from the final text but have no MCP tool trace, so A2/A3 are N/A.

### A3. Glossary

| Term | Meaning |
|---|---|
| **Axis** | An independent quality dimension. Five today: A1–A5. |
| **Layer** | A case-difficulty tier. Three today: L1 / L2 / L3. |
| **Arm** | One agent configuration in a comparison. `main` is default. |
| **Sample** | One `(case, arm, sample_index)` tuple. Multi-sample runs (`--samples 3`) capture agent-side variance, which the legacy 3×-judge approach did NOT capture. |
| **Compliance rule** | A pure predicate over `(tool_sequence, case)`. Encodes one line of the SYSTEM_PROMPT routing contract. |
| **Deterministic scorer** | Pure function `(output, case) → score`. No model in the loop. A1, A2, A4. |
| **Reporting axis** | An axis that emits numbers without pass/fail. A3. |
| **Judge bias** | Systematic score inflation when the judge model is the same family as the agent model. The motivation for Opus-as-judge on Haiku-as-agent runs (A5). |

### A4. External references

- [Zheng et al. 2023, Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena](https://arxiv.org/abs/2306.05685) — documents the same-family bias
- [Anthropic Tool Use docs](https://docs.anthropic.com/en/docs/build-with-claude/tool-use) — the API surface every arm consumes
- [`mcp-design.md`](./mcp-design.md) — the tool layer whose effect this eval is measuring
- [`agent-design.md`](./agent-design.md) — the agent shell whose telemetry this eval consumes

---

**End of manual.** Five axes, three layers, N arms, one JSONL append-only log, deterministic-where-possible scoring — that's the whole eval framework design.
