# L2 — Hard cases (capability eval)

## Status

**Authoring deferred.** This directory currently holds two legacy case files
migrated from `tests/eval_cases/`:

- `reasoning.jsonl` — 20 cases used by the legacy `tests/run_reasoning_eval.py`
- `troubleshoot.jsonl` — 8 cases used by the legacy `tests/run_troubleshoot_eval.py`

Both are runnable via the new eval engine (they validate against the
`CaseSpec` schema), but they were designed before the multi-axis split. A
proper L2 case set requires domain-deep authoring — see *Design intent* below.

## Design intent

L2 is the **capability eval** layer. Where L1 tests "did we regress on the
common cases" and L3 tests "does the agent safely reject bad input", L2
tests the agent's **upper bound** on hard cases. Pass threshold is **≥ 70%**
(not 100% — these are deliberately difficult).

Target case count: **100–200** cases distributed across these categories:

| Category | What it tests | Example |
|---|---|---|
| **Multi-hop reasoning** | Agent must chain ≥3 deductions before generating output | *"My friend has Orin and wants to run the same model I'm running on AGX Xavier (DeepStream 6.3, JP 5.1.1) — what's the equivalent config?"* |
| **Cross-product** | Workload spans products (Jetson + DRIVE, Jetson + Holoscan) | *"I want to develop on Jetson, then deploy to DRIVE Orin for production"* |
| **Ambiguous → clarify** | Inputs that LOOK ambiguous but have a defensible answer with one clarifying assumption | *"Set me up for object detection at 30fps" — agent should ask board, suggest defaults if pushed* |
| **Workload-to-product inference** | User describes a problem; agent must infer product family | *"I have a 100W power budget and want real-time pose estimation on robots"* → Orin NX |
| **Resource-constrained** | User states budget; agent must size config to fit | *"I have 16 GB free on host and 12 GB on target — what JetPack version fits?"* |
| **Version-edge cases** | Specific older/newer versions where compatibility matters | *"AGX Xavier with JetPack 4.x for legacy app, but also need DS 5.x"* |
| **Multi-board fleets** | User has multiple boards, wants one config that works across them | *"I have 5 Orin NX 16GB and 3 Orin Nano 8GB; one .ini for all?"* |

## Why I'm deferring this

Authoring 100–200 high-quality L2 cases is **not** an LLM-augmentation task.
Each case needs:

1. A scenario plausible enough that a real Jetson developer might ask it
2. A defensible `expected` schema — what fields must appear in the answer
3. Verification that the case actually triggers the capability it claims
   (the multi-hop case must require multi-hop reasoning, not just lookup)
4. Distribution-honest difficulty — not all 200 should be solvable by
   pattern-matching

Generated-by-LLM cases tend to be **stereotyped** — all start with "I have
a Jetson Orin Nano and want to do X." Real users phrase queries in much
more varied (and more confusing) ways. Authoring requires NVIDIA-ecosystem
domain knowledge plus user-research empathy, neither of which scales
through generation.

**Plan**: this directory gets filled as a sequence of authoring sessions
post-portfolio-submission, mining real queries from:
- NVIDIA Developer Forum (`forums.developer.nvidia.com`)
- GitHub issues on `dusty-nv/jetson-inference`, `NVIDIA-AI-IOT/*`
- NGC catalog descriptions (the "what is this for" sections)

## What runs today

Until proper L2 authoring is done, you can run the two migrated files:

```bash
# Reasoning track (20 cases)
python -m eval.engine.runner eval/cases/L2/reasoning.jsonl --tag reasoning-baseline

# Troubleshoot track (8 cases) — note these use a different agent path
# (src/troubleshoot.py, not AgentShell), so A2/A3 telemetry will be empty.
python -m eval.engine.runner eval/cases/L2/troubleshoot.jsonl --tag troubleshoot-baseline
```

These will produce JSONL with A1 + A2 + A3 scores, but A4 won't apply
(those are L2, not L3) and A5 (LLM-as-judge) isn't implemented yet.

## What success looks like

A complete L2 set will:

- Hit ≥ 70% overall A1 + A2 average on the `main` arm
- Show **strictly lower** scores on the `no-tools` arm — proving the tool
  layer's value beyond what L1 already shows
- Show **smaller delta** between Haiku and Opus arms than current README
  numbers suggest, because Haiku has more cases that exercise its actual
  reasoning ceiling (not just its routing competence)
