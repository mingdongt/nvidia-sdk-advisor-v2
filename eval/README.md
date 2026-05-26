# Eval engine

A multi-axis eval system for the SDK Advisor agent. Replaces the three ad-hoc
runners in `tests/run_*_eval.py`.

> Design rationale: see `docs/eval-design.md` (sibling document to `docs/mcp-design.md`).
> This README is the operating guide.

## Structure

```
eval/
  cases/
    L1/  golden path  — must pass 100%, blocks release if any fail
    L2/  hard cases   — capability eval, threshold-based
    L3/  adversarial  — negative cases, must reject correctly
  scorers/
    a1_correctness.py     INI schema + dry-run; deterministic
    a2_compliance.py      tool dispatch order vs SYSTEM_PROMPT contract
    a3_efficiency.py      tokens / latency / tool count
    a4_robustness.py      reject-behavior check on L3
    a5_capability.py      LLM-as-judge for free-text quality
  engine/
    schemas.py            CaseSpec + RunRecord (pydantic)
    runner.py             load cases → run agent → write JSONL
  runs/                   one JSONL per run; append-only history
  dashboard/              slice by arm × layer × axis × git_sha
```

## Run

```bash
# All L1 cases, 1 sample each (main arm)
python -m eval.engine.runner eval/cases/L1

# One file, 3 samples per case
python -m eval.engine.runner eval/cases/L1/smoke.jsonl --samples 3

# Multi-arm comparison (when M5 ships):
ANTHROPIC_BACKEND=cli-no-tools python -m eval.engine.runner eval/cases/L1 --arm no-tools
ANTHROPIC_MODEL=claude-opus-4-7 python -m eval.engine.runner eval/cases/L1 --arm opus

# Dry run (plan without invoking the agent)
python -m eval.engine.runner eval/cases/L1 --dry-run
```

Each run writes one JSONL file to `eval/runs/<timestamp>[_<tag>].jsonl`. Every
line is one `RunRecord` (see `engine/schemas.py`).

## Milestone status

| Milestone | What | Status |
|---|---|---|
| M0 | Scaffolding + JSONL schema + case migration | ✅ this commit |
| M1 | A2 compliance + A3 telemetry hooks in `src/agent.py` | pending |
| M2 | A1 deterministic scorer (INI schema, `sdkmanager --dry-run`) | pending |
| M3 | L3 adversarial cases + A4 scorer | pending |
| M4 | L2 hard cases (100-200 multi-hop) | pending |
| M5 | Multi-arm runner (main / no-tools / opus / etc.) | pending |
| M6 | Dashboard + human calibration (Cohen's kappa) | pending |
| M7 | `docs/eval-design.md` + README rewrite | pending |

## Case schema

One JSONL line per case:

```json
{
  "case_id": "L1-smoke-orin-nano-cuda-jp6",
  "layer": "L1",
  "track": "smoke",
  "input": "Orin Nano 8GB, basic CUDA work, JetPack 6.x",
  "expected": {"product": "Jetson", "target": "JETSON_ORIN_NANO_TARGETS", "version_starts_with": "6"},
  "metadata": {"source": "tests/eval_cases/smoke.jsonl#1"}
}
```

`expected` shape is scorer-specific. `metadata` is free-form (source, category, notes).

## Run record schema

See `engine/schemas.py`. One JSONL row per `(case, arm, sample_index)` tuple.
M0 records fill only the run/case/timing fields; M1+ adds telemetry; M2+ adds scores.
