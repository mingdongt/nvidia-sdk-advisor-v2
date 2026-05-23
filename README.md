# NVIDIA SDK Advisor (v2 — Plan A: Foundation)

Conversational agent that helps developers pick the right NVIDIA SDK Manager configuration for their hardware and use case. Generates a `sdkmanager --cli` command and a `.ini` response file in NVIDIA's official template format.

Portfolio artifact for NVIDIA JR2017783 (Senior SWE Tech Lead — AI Developer Experiences, SDK Manager team, Shanghai).

> **Status:** This is **Plan A** of a 5-plan v2 series. Plan A ships the foundation: Knowledge MCP server + conversational REPL + `--plan` mode. Subsequent plans (B-E) add RAG corpus, execution modes, Electron GUI, and polish. See `docs/superpowers/specs/2026-05-23-nvidia-sdk-advisor-v2-design.md` for the complete design.

## What this does

```
$ python main.py

╭───────────────── NVIDIA SDK Advisor ──────────────────╮
│ Hi - what NVIDIA hardware are you working with?       │
╰───────────────────────────────────────────────────────╯

[describe hardware + use case] > Orin NX 8GB, want to run YOLOv8

  -> Resolving hardware name
  -> Listing releases
  -> Generating response file
  -> Validating against template
  -> Generating command

Plan:
  Product:      Jetson
  JetPack:      6.2.2
  Target:       JETSON_ORIN_NX_TARGETS
  Additional:   DeepStream 7.0

+ saved command: output/orin_nx_8gb__want_to_run_yolov8.command
+ saved ini:     output/orin_nx_8gb__want_to_run_yolov8.ini
```

## Architecture (Plan A)

```
User NL input
  |
  v
Conversational REPL (prompt_toolkit + rich)
  |
  v
Anthropic Agent (Claude + tool-use loop)
  |  MCP / stdio
  v
Server A: nvidia-knowledge (11 tools)
  - catalog: list_products, list_releases, get_release, list_hardware, lookup_target_id
  - probe:   detect_connected_hardware (subprocess -> NvSDKManager.exe)
  - plan:    estimate_resources, check_constraints
  - emit:    generate_command, generate_response_file, validate_against_official_sample
  |
  v
NVIDIA's own CDN manifests (data/manifests/, fetched once, committed)
```

The knowledge layer reads from the same CDN URLs SDK Manager itself uses
(`developer.download.nvidia.com/sdkmanager/sdkm-config/...`). Response files
match the format of NVIDIA's bundled
`responsefiles/Linux/sdkm_responsefile_sample_jetson.ini` — verified
structurally by `test_response_file_parity`.

## Setup

```powershell
git clone <repo>
cd nvidia-sdk-advisor
python -m venv .venv
.venv\Scripts\Activate.ps1     # Windows
# source .venv/bin/activate    # Linux/Mac
pip install -r requirements.txt
Copy-Item .env.example .env    # then paste your ANTHROPIC_API_KEY
```

Manifests are committed under `data/manifests/`. To refresh from NVIDIA's CDN
(re-runnable; takes ~30s):

```powershell
python -m ingest.fetch_manifests
```

## Usage

```powershell
# Conversational REPL, generates .ini + .command files (default mode)
python main.py

# Run smoke eval (5 golden cases via real Anthropic API)
python main.py --eval

# These modes are stubs in Plan A; implemented in Plan C:
python main.py --dry-run    # invoke SDK Manager in dry-run mode
python main.py --execute    # actually install via SDK Manager
```

## Tests

```powershell
pytest                                       # all unit tests
pytest tests/test_response_file.py -v        # response file format alignment
```

**Plan A test count:** 33 unit tests + 5 smoke eval cases.
**Smoke eval current:** 14/15 = 93.3% (target: ≥80%).

## What's not yet in Plan A

- RAG layer over forum threads / docs / GitHub samples / NGC catalog (Plan B)
- `--dry-run` and `--execute` modes — driving NvSDKManager.exe as subprocess (Plan C)
- Electron + Vue 3 GUI mirroring SDK Manager's stack (Plan D)
- Full forum-mined eval set + LLM-as-judge reasoning suite + dogfood polish (Plan E)

See `docs/superpowers/specs/2026-05-23-nvidia-sdk-advisor-v2-design.md` for the
complete design including all four JD verbs (discover / configure / install /
troubleshoot).

## Project layout

```
nvidia-sdk-advisor/
├── main.py                       # CLI entry, mode dispatch
├── src/
│   ├── models.py                 # InstallConfig dataclass
│   ├── manifests.py              # KnowledgeBase facade over CDN manifests
│   ├── sdkm_probe.py             # NvSDKManager.exe --list-connected wrapper
│   ├── resource_estimator.py     # estimate_resources, check_constraints
│   ├── response_file.py          # 3-section INI generator + validator
│   ├── command_gen.py            # sdkmanager --cli command builder
│   ├── knowledge_server.py       # MCP server wiring all 11 tools
│   ├── agent.py                  # Anthropic agent + MCP client + tool loop
│   ├── repl.py                   # prompt_toolkit conversational shell
│   └── execution.py              # mode dispatch (--plan / --dry-run / --execute)
├── ingest/
│   └── fetch_manifests.py        # CDN bootstrap
├── data/
│   ├── manifests/                # NVIDIA-signed JSON, committed
│   ├── resource_model.json       # curated disk/RAM sizes
│   └── response_templates/       # copies of NVIDIA's .ini samples
├── tests/
│   ├── test_*.py                 # unit tests (per module)
│   ├── eval_cases/smoke.jsonl    # 5 hand-crafted golden cases
│   └── run_smoke_eval.py         # end-to-end eval runner
└── output/                       # generated .ini + .command files
```

---

## Plan B additions: Hybrid RAG + execution modes

### Hybrid RAG architecture (3 tiers)

The agent now retrieves from three sources, each chosen for a different problem class:

| Tier | Source | Backed by | Use case |
|---|---|---|---|
| **1** | NGC catalog metadata | Local JSONL (~10 containers) | Container → JetPack/CUDA reqs |
| **2** | GitHub READMEs | Chroma vector store (~21 repos, 203 chunks) | Workload → which NVIDIA sample to use |
| **3** | Forum threads + docs | Brave Search API (free tier 2000 req/mo) | Best practices, troubleshooting, fresh content |

Tier 2 uses `sentence-transformers/all-MiniLM-L6-v2` for embeddings (90MB local model, no third-party API).

### Server B: nvidia-corpus-rag

Second MCP server alongside Server A. 4 tools:
- `lookup_container_reqs(container_id)` — Tier 1
- `search_3p_sample_repos(query, k)` — Tier 2
- `search_forum_threads(query, k, mode)` — Tier 3 (forums.developer.nvidia.com)
- `search_docs(query, k)` — Tier 3 (docs.nvidia.com)

Both servers connect via MCP stdio. The agent dispatches via tool→session routing table.

### Execution modes

```powershell
python main.py --plan                  # default — generate .ini and .command, exit
python main.py --dry-run               # invoke NvSDKManager.exe --query to verify .ini format
python main.py --execute               # actually install (requires confirmation + sudo on Linux)
```

`--execute` flow:
1. Locates the most recent `output/*.ini`
2. Asks user to type `yes` to confirm
3. Prompts for sudo password (Linux only; Windows skips)
4. Spawns `NvSDKManager.exe --cli --response-file <plan.ini>` with streamed stdout
5. Classifies output lines (downloading / installing / flashing / error / success) and prints summary

### Eval results

```powershell
python main.py --eval smoke            # Plan A smoke (5 hand-crafted cases) — 14/15 = 93.3%
python main.py --eval reasoning        # Plan B reasoning (20 LLM-judged) — 3.56/5 (target ≥3.5)
```

### Setup additions for Plan B

```powershell
# Optional: get a free Brave Search API key (2000 req/mo) for Tier 3
# https://api.search.brave.com/  →  Data for AI plan  →  Create Key
# Add to .env:
BRAVE_API_KEY=BSA...

# Optional: GitHub token for raising scrape rate limit from 60→5000/hr
# https://github.com/settings/tokens (no scopes needed)
$env:GH_TOKEN = "ghp_..."

# Then refresh corpus (one-time):
python -m ingest.fetch_ngc_catalog       # NGC catalog (Tier 1)
python -m ingest.scrape_github_samples   # GitHub READMEs (Tier 2 data)
python -m ingest.build_github_vectordb   # Build Chroma index (Tier 2)
```

The corpus + Chroma DB are committed via Git LFS; a fresh clone does not need to re-run the ingestion scripts.

### Plan B sequence (for reference)

Plan B series (in commit order):
- B.1: NGC catalog fetcher
- B.2: Server B skeleton + Tier 1 tool
- B.3: VectorStore (Chroma + sentence-transformers)
- B.4: GitHub READMEs scraper
- B.5: Vector index builder
- B.6: search_3p_sample_repos
- B.7: Brave Search client
- B.8: search_forum_threads + search_docs
- B.9: Multi-server agent + REPL wire-up
- B.10: --dry-run execution mode
- B.11: --execute execution mode
- B.12: Reasoning eval suite
