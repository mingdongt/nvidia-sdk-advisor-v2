# NVIDIA SDK Advisor

A conversational agent that helps developers **discover, configure, install, and troubleshoot** NVIDIA SDKs — the four AI capabilities named in NVIDIA JR2017783 (Senior SWE Tech Lead — AI Developer Experiences, SDK Manager team).

Built on the same NVIDIA public data sources SDK Manager itself uses, producing output files (`.ini` response files) SDK Manager natively consumes, and optionally driving `NvSDKManager.exe` to completion via subprocess.

[![Smoke eval: 15/15](https://img.shields.io/badge/smoke%20eval-15%2F15-brightgreen)](#evaluation) [![Reasoning eval: 3.56/5](https://img.shields.io/badge/reasoning-3.56%2F5-yellow)](#evaluation) [![Troubleshoot eval: 4.70/5](https://img.shields.io/badge/troubleshoot-4.70%2F5-brightgreen)](#evaluation) [![Unit tests: 81 passing](https://img.shields.io/badge/tests-81%20passing-brightgreen)](#tests)

---

## The four JD verbs, mapped to demo mechanisms

| JD verb | What SDK Manager wizard can't do | What this demo does |
|---|---|---|
| **Discover** | Flat list of NVIDIA-branded SDKs; user must already know which fits their use case | `search_3p_sample_repos` (vector search over 21 GitHub repo READMEs) + workload-to-product inference |
| **Configure** | Silent prune of invalid combinations; no resource preflight; no cross-product reasoning | 13 deterministic tools — `list_releases`, `validate_combo`, `estimate_resources`, `check_constraints` — over NVIDIA's own CDN manifests |
| **Install** | Wizard runs install; CLI takes flags. No conversational guided flow | Generates `.ini` matching NVIDIA's official template, optionally drives `NvSDKManager.exe --cli --response-file` as subprocess with streamed status + event classification |
| **Troubleshoot** | "Export logs" → user reads → user searches forum. No diagnostic surface | `parse_install_log` (20 regex patterns over 5 stages) → forum search via Brave → Claude synthesizes `fix.sh` + `diagnosis.md` |

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

→ search_forum_threads(mode=troubleshoot)
  found 4 thread(s)

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
             │  13 tools            │    │  4 tools                │
             │  (deterministic)     │    │  (3-tier hybrid)        │
             └─────────────┬────────┘    └──────────┬──────────────┘
                           │                       │
    ┌──────────────────────▼────────────┐  ┌───────▼─────────────────────┐
    │ data/manifests/* (~25 JSON)       │  │ Tier 1: NGC catalog         │
    │  fetched from developer.download  │  │   (20 containers, JSONL)    │
    │  .nvidia.com — same source SDK    │  │ Tier 2: GitHub READMEs      │
    │  Manager itself reads             │  │   (21 repos, Chroma vector) │
    │                                    │  │ Tier 3: Brave Search        │
    │ + data/resource_model.json        │  │   (forums + docs, fresh)    │
    │ + data/log_patterns.yaml (20)     │  │                              │
    │ + NvSDKManager.exe                │  │                              │
    │   --list-connected (subprocess)   │  │                              │
    └────────────────────────────────────┘  └──────────────────────────────┘

                                      │ --execute (optional)
                                      ▼
                       ┌─────────────────────────────┐
                       │  NvSDKManager.exe           │
                       │  --cli --response-file *.ini│
                       └─────────────────────────────┘
```

**Two MCP servers, independently runnable.** Server A is deterministic facts from NVIDIA-signed manifests; Server B is semantic + structured retrieval. The agent dispatches via a tool-name → session routing table.

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

### Optional keys (improve eval scores + lift Tier 3 to active)

```powershell
# Brave Search API (https://api.search.brave.com/) — free tier 2000 req/mo
# Required to activate Tier 3 (forum threads + docs)
BRAVE_API_KEY=BSA...

# GitHub token (no scopes needed) — raises rate limit from 60→5000/hr
# Needed if you re-run scrape_github_samples to expand the corpus
GH_TOKEN=ghp_...
```

Without `BRAVE_API_KEY`, Tier 3 returns empty hits and the agent works from Tier 1+2 only (still useful, just narrower).

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

`--execute` requires explicit `yes` confirmation in the same session. On Linux, also prompts for sudo via `getpass`. On non-zero exit, automatically offers to run `--troubleshoot` on the latest export log.

`--troubleshoot` is read-only by default — it generates `fix.sh` and `diagnosis.md` but does NOT execute them. The user must review and run `bash fix.sh` themselves.

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

Both reasoning and troubleshoot were evaluated **without** Brave Search (Tier 3 silent). With Brave key configured, factual and matches-expert axes are expected to lift further.

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
│   ├── brave_search.py              # Brave Search API client (Tier 3)
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
| **v2.0.0-b** | Hybrid 3-tier RAG (Server B) + `--dry-run` + `--execute` modes + reasoning eval | B.1–B.13 |
| **v2.0.0-c** | `parse_install_log` (13th Server A tool) + `--troubleshoot` mode + troubleshoot eval | C.1–C.10 |

Spec: `docs/superpowers/specs/2026-05-23-nvidia-sdk-advisor-v2-design.md`.
Plans: `docs/superpowers/plans/2026-05-23-nvidia-sdk-advisor-v2{a,b,c}-*.md`.

---

## Strategic positioning (for NVIDIA reviewers)

This demo addresses the unfilled white space on NVIDIA's "AI everywhere in developer tools" roadmap — the SDK Manager team's product gap. NeMo Agent Toolkit, AI-Q Blueprints, Nsight Copilot, Vera CPU all received agentic upgrades in the past 18 months; SDK Manager's developer-blog tag has zero posts since July 2023, and v2.4.0 release notes mention no AI features.

The pitch in one sentence: *the same agent NVIDIA is building everywhere else, applied to the wizard their docs show users routinely bouncing off of.*

The repo is built on the same data sources SDK Manager itself reads (the public CDN at `developer.download.nvidia.com/sdkmanager/sdkm-config/`), produces output matching NVIDIA's own `.ini` template format, and treats `NvSDKManager.exe` as a subprocess target rather than competing with it. It positions itself as *everything before the wizard fires* — it does not replace `sdkmanager`, it feeds it.

---

## License & attribution

Portfolio artifact. NVIDIA SDK Manager, NGC, JetPack, Jetson, Isaac, DeepStream, etc. are trademarks of NVIDIA Corporation. All NVIDIA data is fetched from public endpoints; this repo redistributes only what is necessary for offline reproducibility (CDN manifest snapshots, GitHub README scraped via API).
