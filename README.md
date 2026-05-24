# NVIDIA SDK Advisor

A conversational agent that helps developers **discover, configure, install, and troubleshoot** NVIDIA SDKs, built on the same public data sources SDK Manager itself uses.

Generates output files (`.ini` response files) SDK Manager natively consumes, and optionally drives `NvSDKManager.exe` to completion via subprocess.

[![Smoke eval: 15/15](https://img.shields.io/badge/smoke%20eval-15%2F15-brightgreen)](#evaluation) [![Reasoning eval: 3.56/5](https://img.shields.io/badge/reasoning-3.56%2F5-yellow)](#evaluation) [![Troubleshoot eval: 4.70/5](https://img.shields.io/badge/troubleshoot-4.70%2F5-brightgreen)](#evaluation) [![Unit tests: 81 passing](https://img.shields.io/badge/tests-81%20passing-brightgreen)](#tests)

---

## What this does

| Capability | Current SDK Manager gap | This demo's mechanism |
|---|---|---|
| **Discover** | Flat list of NVIDIA-branded SDKs; user must already know which fits their use case | `search_3p_sample_repos` (vector search over 21 GitHub repo READMEs) + workload-to-product inference |
| **Configure** | Silent prune of invalid combinations; no resource preflight; no cross-product reasoning | 13 deterministic tools — `list_releases`, `validate_combo`, `estimate_resources`, `check_constraints` — over NVIDIA's own CDN manifests |
| **Install** | Wizard runs install; CLI takes flags. No conversational guided flow | Generates `.ini` matching NVIDIA's official template, optionally drives `NvSDKManager.exe --cli --response-file` as subprocess with streamed status + event classification |
| **Troubleshoot** | "Export logs" → user reads → user searches forum. No diagnostic surface | `parse_install_log` (20 regex patterns over 5 stages) → Claude synthesizes `fix.sh` + `diagnosis.md`, using native web search when grounding on forum threads helps |

---

## Hero scenarios

### Scenario 1: Configure + install (Orin NX → YOLO)

```
$ python main.py

╭───────────────── NVIDIA SDK Advisor ──────────────────╮
│ Detected Jetson Orin NX 16GB connected (USB 1-4).     │
│ What do you want to do with it?                       │
╰───────────────────────────────────────────────────────╯

> Orin NX, run YOLOv8 object detection at 30fps

  → Resolving hardware name           JETSON_ORIN_NX_TARGETS
  → Searching sample repos            jetson-inference DetectNet (top hit)
  → Listing releases                  JetPack 6.2.2, 6.2.1, 6.2, 6.1, 5.1.6
  → Estimating resources              target 22GB, host 35GB, RAM 1.7GB
  → Generating response file
  → Validating against template       ✓ structurally matches NVIDIA sample
  → Generating command

Plan:
  Product:      Jetson
  JetPack:      6.2.2
  Target:       JETSON_ORIN_NX_TARGETS
  Host OS:      ubuntu22.04
  Additional:   DeepStream 7.0

+ saved command: output/orin_nx_yolov8.command
+ saved ini:     output/orin_nx_yolov8.ini
```

Then either:

```powershell
python main.py --dry-run    # have SDK Manager parse the .ini to verify format
python main.py --execute    # actually install (requires 'yes' confirmation + sudo on Linux)
```

### Scenario 2: Troubleshoot (apt failure → fix.sh)

```
$ python main.py --troubleshoot ~/sdkm-export-logs.tar.gz

╭──────────────── Troubleshoot mode ────────────────╮
│ Parsing SDK Manager log…                          │
╰────────────────────────────────────────────────────╯

Failed stage:    apt
Error class:     apt-missing-package
Error signature: E: Unable to locate package nvidia-jetpack=6.1*
Target:          JETSON_ORIN_NX_TARGETS
Host OS:         ubuntu22.04
JetPack:         6.1
Last success:    apt-get update completed

→ Claude consults forum/docs via native WebSearch
  site:forums.developer.nvidia.com "nvidia-jetpack apt unable to locate"

→ synthesizing fix…

╭───────────────── Recommended fix ──────────────────╮
│ ## Diagnosis                                       │
│ The apt sources list lacks the NVIDIA L4T repo —   │
│ jetson-ota-public.asc key + repo URL never got    │
│ written during SDK Manager's install path…         │
│                                                    │
│ ## Recommended fix                                 │
│ ```bash                                            │
│ sudo apt-key adv --fetch-keys \                   │
│   https://repo.download.nvidia.com/jetson/jetson-ota-public.asc │
│ sudo bash -c 'echo "deb https://repo.download.nvidia.com/jetson/common r36.4 main" \ │
│   > /etc/apt/sources.list.d/nvidia-l4t-apt-source.list' │
│ sudo apt update                                   │
│ ```                                                │
╰────────────────────────────────────────────────────╯

✓ output/apt-missing-package_diagnosis.md
✓ output/apt-missing-package_fix.sh
```

---

## Architecture

```
                          User natural-language input
                                      │
                                      ▼
                        ┌──────────────────────────────┐
                        │  SDK Advisor Agent (Claude)  │
                        │  • multi-turn REPL           │
                        │  • mode classifier           │
                        │  • tool dispatch + synthesis │
                        └──┬───────────────────────┬───┘
                           │ MCP/stdio             │ MCP/stdio
             ┌─────────────▼───────┐    ┌──────────▼──────────────┐
             │  Server A:           │    │  Server B:              │
             │  nvidia-knowledge    │    │  nvidia-corpus-rag      │
             │  13 tools            │    │  2 tools                │
             │  (deterministic)     │    │  (NGC + GitHub RAG)     │
             └─────────────┬────────┘    └──────────┬──────────────┘
                           │                       │
    ┌──────────────────────▼────────────┐  ┌───────▼─────────────────────┐
    │ data/manifests/* (~25 JSON)       │  │ Tier 1: NGC catalog         │
    │  fetched from developer.download  │  │   (20 containers, JSONL)    │
    │  .nvidia.com — same source SDK    │  │ Tier 2: GitHub READMEs      │
    │  Manager itself reads             │  │   (21 repos, Chroma vector) │
    │                                    │  │                              │
    │ + data/resource_model.json        │  │ Tier 3 (forums + docs)       │
    │ + data/log_patterns.yaml (20)     │  │   delegated to Claude's      │
    │ + NvSDKManager.exe                │  │   native WebSearch with      │
    │   --list-connected (subprocess)   │  │   site: hints from prompt    │
    └────────────────────────────────────┘  └──────────────────────────────┘

                                      │ --execute (optional)
                                      ▼
                       ┌─────────────────────────────┐
                       │  NvSDKManager.exe           │
                       │  --cli --response-file *.ini│
                       └─────────────────────────────┘
```

**Two MCP servers, independently runnable.** Server A is deterministic facts from NVIDIA-signed manifests; Server B is semantic + structured retrieval. The agent dispatches via a tool-name → session routing table.

The Python REPL is just one consumer. Either server can be spawned standalone:

```powershell
python -m src.knowledge_server   # 13 tools, stdio MCP
python -m src.rag_server         # 2 tools, stdio MCP
```

Any MCP client — Claude Code, Cursor, a custom Node renderer, anything that speaks stdio MCP — can connect and use these tools without touching the rest of this repo. The agent loop is replaceable; the data layer is not. This separation is the MCP composability story.

---

## Setup

```powershell
git clone https://github.com/mingdongt/nvidia-sdk-advisor-v2.git nvidia-sdk-advisor
cd nvidia-sdk-advisor
python -m venv .venv
.venv\Scripts\Activate.ps1                  # Windows
# source .venv/bin/activate                 # Linux/Mac
pip install -r requirements.txt
Copy-Item .env.example .env                 # paste your ANTHROPIC_API_KEY here
python -m ingest.build_github_vectordb      # rebuild Chroma DB (~30s, one-time)
```

**Why the rebuild step:** the Chroma vector store is binary, fast-changing, and would bloat the repo via Git LFS. The committed JSONL corpus is the input; the index is local-rebuild.

### Tier 3 (forums + docs)

Earlier versions of this demo wrapped `forums.developer.nvidia.com` and `docs.nvidia.com` searches behind two dedicated MCP tools (`search_forum_threads`, `search_docs`) that called Brave Search under the hood. We removed them — they were ~2-line domain-filter shims and Claude's native web search handles the same task cleanly.

This means **no `BRAVE_API_KEY` setup required**. The two consumers of web search are wired differently:

- **`--troubleshoot` mode (SDK backend)**: the `web_search_20250305` server-side tool is attached **automatically**. No domain whitelist — Claude is good at preferring NVIDIA docs / official forums / Stack Exchange on its own; restricting to NVIDIA-only domains crowded out genuinely useful community fixes for apt / kernel / DNS errors that aren't NVIDIA-specific. The synthesis prompt makes at least one `web_search` call mandatory before recommending a fix, and ranks preferred source tiers (NVIDIA docs > NVIDIA forum > SO/AskUbuntu > GitHub issues). `max_uses=5` is the only constraint — a cost ceiling, not a trust filter. Cost: ~$0.01 per troubleshoot run. If `web_search` is unavailable (e.g. region-restricted), the agent falls back to training-knowledge synthesis with an explicit disclaimer.
- **`--troubleshoot` mode (CLI backend)**: Claude CLI's built-in WebSearch covers the same role; no extra config.
- **REPL / `--plan` mode**: web search is *not* auto-attached — the agent's primary tools are Server A's deterministic lookups + Server B's local RAG. The agent's SYSTEM_PROMPT mentions `site:forums.developer.nvidia.com` as a hint for the CLI backend; SDK backend uses only deterministic tools for planning.

### Optional: GitHub token

```powershell
# Raises GitHub API rate limit from 60→5000/hr (needed if you re-run scrape_github_samples)
GH_TOKEN=ghp_...
```

### Backend selection (use API key OR Claude Code subscription)

The agent supports three backends, switchable via env var:

```powershell
# Default: Anthropic Python SDK (Haiku 4.5 by default, configurable via ANTHROPIC_MODEL)
$env:ANTHROPIC_BACKEND="sdk"

# Subscription-based: spawn `claude` CLI as subprocess + our 2 MCP servers
# (avoids API quota when you already have a Claude Pro/Max subscription)
$env:ANTHROPIC_BACKEND="cli"

# Baseline (for the ablation study below): claude CLI with no tools attached
$env:ANTHROPIC_BACKEND="cli-no-tools"
```

The `cli` backend requires `claude` CLI installed and authenticated (`claude login`).

### Re-running corpus ingestion (optional)

```powershell
python -m ingest.fetch_manifests         # refresh NVIDIA CDN manifests
python -m ingest.fetch_ngc_catalog       # refresh NGC container catalog (20 containers)
python -m ingest.scrape_github_samples   # refresh GitHub READMEs (needs GH_TOKEN to cover all 28)
python -m ingest.build_github_vectordb   # rebuild Chroma index from latest READMEs
```

---

## Usage

```powershell
python main.py                           # default — conversational REPL, generates .ini + .command
python main.py --dry-run                 # invoke NvSDKManager --query against latest plan
python main.py --execute                 # actually install (confirmation + sudo prompt)
python main.py --troubleshoot <log>      # diagnose an SDK Manager log archive or .log file
python main.py --eval smoke              # Plan A eval (5 hand-crafted cases, exact match)
python main.py --eval reasoning          # Plan B eval (20 LLM-judged cases)
python main.py --eval troubleshoot       # Plan C eval (15 log-snippet LLM-judged cases)
```

### Execution mode safety

`--execute` requires explicit `yes` confirmation in the same session. On Linux, also prompts for sudo via `getpass`.

### Self-healing chain on failure

When `--execute` exits non-zero, the agent automatically finds the most recent SDK Manager log and offers to enter `--troubleshoot` on it. Two sources are searched, in priority order:

1. Exported tarballs (`sdkm-*log*.tar*`) in `~`, `~/Downloads`, or `cwd`
2. Raw session `.log` files in `~/.nvsdkm-logs/` (Linux/Mac) or `~/AppData/Local/NVIDIA Corporation/SDK Manager/logs/` (Windows)

**The user does NOT need to run `--export-logs` manually.** SDK Manager writes raw session logs during install; we read them directly. `--export-logs` packaging is a sharing convenience, not a prerequisite for our parser. The most-recent file (by mtime) across both sources wins.

`--troubleshoot` itself is read-only by default — it generates `fix.sh` and `diagnosis.md` but does NOT execute them. The user must review and run `bash fix.sh` themselves.

---

## Evaluation

Three tracks. All run with `python main.py --eval <track>`:

| Track | Method | Cases | Latest score | Target |
|---|---|---|---|---|
| Smoke | Exact field match (product / version / target / additional_sdks) | 5 hand-crafted | **15/15 (100%)** | ≥80% |
| Reasoning | LLM-as-judge, 4 axes, 3× median | 20 forum-mined | **3.56/5** | ≥3.5/5 |
| Troubleshoot | LLM-as-judge, 4 axes, 3× median | 15 log-snippet | **4.70/5** | ≥3.5/5 |

### Per-axis breakdown

**Reasoning** (4 axes × 1-5):
- Factual correctness: 3.20
- Reasoning quality: 3.35
- Constraints respected: 4.95
- INI validity: 2.75

**Troubleshoot** (4 axes × 1-5):
- Error correctly identified: 5.00 (perfect — regex patterns cover all 15 cases)
- Fix matches expert reference: 4.00
- Fix is actionable: 4.93
- Safety (sudo warnings): 4.93

Both reasoning and troubleshoot were evaluated with Tier 3 deferred to the underlying Claude's training knowledge (no live web search invoked during eval, to keep scores reproducible). Production runs with `ANTHROPIC_BACKEND=cli` get WebSearch grounding for free.

### Ablation: does the RAG layer actually help, or does Claude already know this?

Three-way smoke-eval comparison (same 5 hand-crafted cases, same scorer):

| Configuration | Backend | Tools | Smoke score | Δ vs baseline |
|---|---|---|---|---|
| **A** | Anthropic SDK + **Haiku 4.5** | + Server A (13) + Server B (4) | **15/15 (100%)** | +53.3 pp |
| **B** | Claude CLI + **Opus 4.7** | + Server A (13) + Server B (4) | **15/15 (100%)** | +53.3 pp |
| **C (baseline)** | Claude CLI + **Opus 4.7** | _none — model alone with format prompt_ | **7/15 (46.7%)** | — |

**Reading the table:** Opus 4.7 alone, even when explicitly told to produce `sdkmanager` commands in the right format, scores 46.7% on factual NVIDIA SDK questions. The misses are all hallucinations the model couldn't ground:

- `--product jetpack` instead of `Jetson` (product/version confusion)
- `--product JETSON_ORIN_NX_TARGETS` (target ID written into the product field)
- `JETSON_XAVIER_TARGETS` instead of `JETSON_AGX_XAVIER_TARGETS` (invented variant)
- Original Jetson Nano (4GB) paired with JetPack 5.0 — but Nano only supports up to 4.6.4

With the RAG + deterministic tool layer, both Haiku 4.5 and Opus 4.7 score perfectly. The tools convert the model's knowledge into **executable, factually-grounded** artifacts. Haiku + our layer **matches** Opus + our layer at this scoring axis.

The takeaway: the tool layer — not the underlying model — is where the accuracy comes from.

Try it yourself:
```powershell
$env:ANTHROPIC_BACKEND="cli-no-tools"; python main.py --eval smoke   # Opus alone
$env:ANTHROPIC_BACKEND="cli";          python main.py --eval smoke   # Opus + our tools
$env:ANTHROPIC_BACKEND="sdk";          python main.py --eval smoke   # Haiku + our tools (default)
```

Raw baseline responses (showing the hallucinations) archived at `data/eval_runs/opus_baseline_responses.txt`.

### How Haiku and Opus differ in tool usage (both still score 100%)

Running the smoke eval with `tests/list_smoke_tools.py` (SDK + Haiku) and `tests/list_smoke_tools_cli.py` (CLI + Opus 4.7) traces which MCP tools each model invoked per case:

| Case | Haiku 4.5 path | Opus 4.7 path |
|---|---|---|
| 1: Orin Nano + CUDA + JP 6.x | detect → lookup → list_releases → gen_ini → gen_cmd | **ToolSearch** → detect → lookup → list_releases → gen_cmd → gen_ini |
| 2: AGX Orin + DeepStream 7.0 | detect → lookup → list_releases → **validate_combo × 2** → gen_ini → gen_cmd | detect → lookup → list_releases → gen_ini → gen_cmd  _(no validate_combo)_ |
| 3: Orin NX + latest JP | detect → lookup → list_releases → gen_ini → gen_cmd | _(identical)_ |
| 4: AGX Xavier + latest JP | detect → lookup → list_releases → gen_ini → gen_cmd | detect → lookup → list_releases → **lookup × 2** → gen_ini → gen_cmd |
| 5: Nano + object detection sample | detect → lookup → **search_3p_sample_repos** → list_releases → gen_ini → gen_cmd | _(identical)_ |

Aggregate (5 cases each):

| Tool | Haiku calls | Opus calls |
|---|---:|---:|
| `detect_connected_hardware` | 5 | 5 |
| `lookup_target_id` | 5 | **6** _(self-verifies once)_ |
| `list_releases` | 5 | 5 |
| `generate_response_file` | 5 | 5 |
| `generate_command` | 5 | 5 |
| `validate_combo` | **2** | **0** _(internalizes the era-pairing rule)_ |
| `search_3p_sample_repos` | 1 | 1 |

**Two behavioral signals worth noting:**

1. **Opus skips `validate_combo` (case 2)** — it reads the JetPack ↔ addon-SDK era table in the SYSTEM_PROMPT and inlines the check rather than dispatching the tool. Haiku takes the tool path literally; Opus internalizes the rule. **Both still get the right answer.** This says something about tool-design: a tool the bigger model routinely skips without losing accuracy is *probably* doing the work the smaller model can't — and removing it would degrade the smaller model. Keep the tool.

2. **Opus double-checks `lookup_target_id` (case 4)** — it dispatches the lookup tool a second time on the same input. Haiku doesn't. This isn't a smarter behavior, just a more conservative one; the demo's correctness doesn't depend on it, but it shows up in the trace.

These are surface-level differences in HOW the two models walk the agent graph. The OUTCOME on every case is identical (15/15 each). The earlier takeaway — *the tool layer, not the underlying model, is where the accuracy comes from* — holds: change the model, behavior shifts slightly, output stays correct.

---

## Tests

```powershell
pytest                                   # all 81 unit tests
pytest tests/test_response_file.py -v    # response file format alignment with NVIDIA template
pytest tests/test_log_parser.py -v       # 20-pattern log parser against 5 fixture logs
```

---

## Project structure

```
nvidia-sdk-advisor/
├── main.py                          # CLI entry, mode dispatch
├── src/
│   ├── models.py                    # InstallConfig, LogDiagnosis dataclasses
│   ├── manifests.py                 # KnowledgeBase facade over CDN manifests
│   ├── sdkm_probe.py                # NvSDKManager.exe --list-connected wrapper
│   ├── resource_estimator.py        # estimate_resources, check_constraints
│   ├── response_file.py             # 3-section INI generator + validator
│   ├── command_gen.py               # sdkmanager --cli command builder
│   ├── log_parser.py                # SDK Manager log → LogDiagnosis (20 regex patterns)
│   ├── vector_search.py             # Chroma + sentence-transformers wrapper
│   ├── knowledge_server.py          # Server A — 13 tools (FastMCP)
│   ├── rag_server.py                # Server B — 4 tools (FastMCP)
│   ├── agent.py                     # Anthropic agent + dual MCP client + tool loop
│   ├── repl.py                      # prompt_toolkit conversational shell
│   ├── troubleshoot.py              # --troubleshoot orchestrator
│   └── execution.py                 # --plan / --dry-run / --execute dispatch
├── ingest/
│   ├── fetch_manifests.py           # NVIDIA CDN manifests
│   ├── fetch_ngc_catalog.py         # NGC container metadata (Tier 1, 20 entries)
│   ├── scrape_github_samples.py     # GitHub README scraper (Tier 2 input)
│   └── build_github_vectordb.py     # Chroma index builder (Tier 2)
├── data/
│   ├── manifests/                   # NVIDIA CDN snapshots (committed)
│   ├── corpus/
│   │   ├── ngc/containers.jsonl     # Tier 1 (20 records)
│   │   └── github/readmes.jsonl     # Tier 2 input (21 READMEs)
│   ├── resource_model.json          # curated disk/RAM table
│   ├── log_patterns.yaml            # 20 troubleshoot regex patterns
│   ├── response_templates/          # NVIDIA's official .ini samples
│   ├── ngc_seed_list.txt            # curated NGC slugs (20, all verified)
│   └── github_seed_list.txt         # curated repos (28)
├── tests/
│   ├── eval_cases/
│   │   ├── smoke.jsonl              # 5 hand-crafted golden cases
│   │   ├── reasoning.jsonl          # 20 forum-mined LLM-judged cases
│   │   └── troubleshoot.jsonl       # 15 log-snippet LLM-judged cases
│   ├── fixtures/                    # 5 sample SDK Manager logs
│   ├── test_*.py                    # 81 unit tests across modules
│   ├── run_smoke_eval.py
│   ├── run_reasoning_eval.py
│   └── run_troubleshoot_eval.py
└── output/                          # generated .ini, .command, fix.sh, diagnosis.md
```

---

## How to extend

**Add a new NVIDIA product**: append its slug to `data/ngc_seed_list.txt`, run `python -m ingest.fetch_ngc_catalog`.

**Add a new sample repo to RAG**: append to `data/github_seed_list.txt`, run `python -m ingest.scrape_github_samples && python -m ingest.build_github_vectordb`.

**Add a new log error pattern**: append to `data/log_patterns.yaml` with `{regex, stage, error_class, search_terms}`. Test: add a sample log to `tests/fixtures/` and a test case to `tests/eval_cases/troubleshoot.jsonl`.

**Use only Server B from a different agent**: `python -m src.rag_server` exposes the 4 RAG tools via stdio MCP — any MCP client can connect and use them in isolation.

---

## Implementation history

Plan series (in commit order, tagged in git):

| Tag | Scope | Commits |
|---|---|---|
| **v2.0.0-a** | Foundation: Server A skeleton + 11 deterministic tools + REPL + `--plan` | A.1–A.15 |
| _delta_ | `validate_combo` (12th Server A tool) | 1 commit |
| **v2.0.0-b** | RAG layer (Server B: NGC + GitHub) + `--dry-run` + `--execute` modes + reasoning eval | B.1–B.13 |
| **v2.0.0-c** | `parse_install_log` (13th Server A tool) + `--troubleshoot` mode + troubleshoot eval | C.1–C.10 |

---

## License & attribution

NVIDIA SDK Manager, NGC, JetPack, Jetson, Isaac, DeepStream, etc. are trademarks of NVIDIA Corporation. All NVIDIA data is fetched from public endpoints; this repo redistributes only what is necessary for offline reproducibility (CDN manifest snapshots, GitHub README scraped via API).
