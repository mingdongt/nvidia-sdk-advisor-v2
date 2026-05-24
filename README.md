# NVIDIA SDK Advisor

A conversational agent that helps developers **discover, configure, install, and troubleshoot** NVIDIA SDKs, built on the same public data sources SDK Manager itself uses.

Generates output files (`.ini` response files) SDK Manager natively consumes, and optionally drives `NvSDKManager.exe` to completion via subprocess.

[![Smoke eval: 15/15](https://img.shields.io/badge/smoke%20eval-15%2F15-brightgreen)](#evaluation) [![Reasoning eval: 3.56/5](https://img.shields.io/badge/reasoning-3.56%2F5-yellow)](#evaluation) [![Troubleshoot eval: 4.65/5](https://img.shields.io/badge/troubleshoot-4.65%2F5-brightgreen)](#evaluation) [![Unit tests: 84 passing](https://img.shields.io/badge/tests-84%20passing-brightgreen)](#tests)

> **A design study with executable evidence.** What an AI assistant inside SDK Manager could look like — and what 80% of production-izing it would actually require.

## Index

**Build** — [What this does](#what-this-does) · [Hero scenarios](#hero-scenarios) · [Architecture](#architecture) · [Setup](#setup) · [Usage](#usage) · [Project structure](#project-structure) · [Implementation history](#implementation-history)

**Evaluate** — [Evaluation](#evaluation) · [Tests](#tests)

**Strategy** — [Design principles](#design-principles) · [Production gaps](#production-gaps) · [Troubleshoot evolution roadmap](#troubleshoot-evolution-roadmap) · [Owner perspective](#owner-perspective) · [Deliberate non-goals](#deliberate-non-goals)

---

## Design principles

Three judgments shaped what this demo includes and what it excludes:

1. **The code is evidence, the README is the argument.** Reading the README without running the code should be enough to evaluate the design depth. Running the code without reading the README will miss the point.
2. **Demonstrate the architecture, not the polish.** Every code path exists to make an architectural claim concrete. Polish — CLI ergonomics, exhaustive error handling, every edge case — is deliberately under-invested where it doesn't strengthen the argument.
3. **Surface gaps honestly.** Sections below — _Production gaps_, _Troubleshoot evolution roadmap_ — enumerate what a real product would need that this demo doesn't have. The point is to prove these gaps are understood, not to fill them.

This is a design study with executable evidence, not a tool meant to be adopted as-is.

---

## What this does

| Capability | Current SDK Manager gap | This demo's mechanism |
|---|---|---|
| **Discover** | Flat list of NVIDIA-branded SDKs; user must already know which fits their use case | `search_3p_sample_repos` (vector search over 21 GitHub repo READMEs) + workload-to-product inference |
| **Configure** | Silent prune of invalid combinations; no resource preflight; no cross-product reasoning | 13 deterministic tools — `list_releases`, `validate_combo`, `estimate_resources`, `check_constraints` — over NVIDIA's own CDN manifests |
| **Install** | Wizard runs install; CLI takes flags. No conversational guided flow | Generates `.ini` matching NVIDIA's official template, optionally drives `NvSDKManager.exe --cli --response-file` as subprocess with streamed status + event classification |
| **Troubleshoot** | "Export logs" → user reads → user searches forum. No diagnostic surface | `parse_install_log` opens the `.zip`, extracts filename metadata + log tail. Agent reads the raw tail itself and uses `web_search` (forums, askubuntu, stackoverflow) to find expert fixes → synthesizes `fix.sh` + `diagnosis.md`. No pre-classification layer |

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
    │ + NvSDKManager.exe                │  │   delegated to Claude's      │
    │   --list-connected (subprocess)   │  │   native web_search           │
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
python main.py --full --mock-install --query "<text>"   # end-to-end: configure → install → troubleshoot → fix → retry
python main.py --eval smoke              # Plan A eval (5 hand-crafted cases, exact match)
python main.py --eval reasoning          # Plan B eval (20 LLM-judged cases)
python main.py --eval troubleshoot       # Plan C eval (15 log-snippet LLM-judged cases)
```

### End-to-end mode (`--full`)

`--full --mock-install` chains all five phases of the troubleshoot story into one continuous CLI session:

1. **Configure** [REAL] — agent + MCP + `.ini` / `.command` generation
2. **Install** [MOCKED] — canned SDK Manager failure log (no real subprocess; no hardware needed)
3. **Troubleshoot** [REAL] — agent re-reads the mock log, invokes `web_search`, synthesizes `fix.sh` + `diagnosis.md`
4. **Apply fix** [SIMULATED] — prints `bash fix.sh` command, does not actually execute
5. **Retry install** [MOCKED] — canned success log

Each phase header in the output is tagged REAL / MOCKED / SIMULATED so the audience sees exactly where grounding ends and canned content begins. Without `--mock-install`, `--full` exits with an error: real-hardware end-to-end isn't built yet (needs a connected Jetson + a deterministic failure recipe — see [Owner perspective](#owner-perspective) Q3 plan).

### Execution mode safety

`--execute` requires explicit `yes` confirmation in the same session. On Linux, also prompts for sudo via `getpass`.

### Self-healing chain on failure

When `--execute` exits non-zero, the agent automatically finds the most recent SDK Manager log and offers to enter `--troubleshoot` on it. Two sources are searched, in priority order:

1. Exported tarballs (`sdkm-*log*.tar*`) in `~`, `~/Downloads`, or `cwd`
2. Raw session `.log` files in `~/.nvsdkm-logs/` (Linux/Mac) or `~/AppData/Local/NVIDIA Corporation/SDK Manager/logs/` (Windows)

**The user does NOT need to run `--export-logs` manually.** SDK Manager writes raw session logs during install; we read them directly. `--export-logs` packaging is a sharing convenience, not a prerequisite for our parser. The most-recent file (by mtime) across both sources wins.

`--troubleshoot` itself is read-only by default — it generates `fix.sh` and `diagnosis.md` but does NOT execute them. The user must review and run `bash fix.sh` themselves.

### Log handling: surface-level by design

The log-reading layer is **deliberately surface-level**: open the `.zip`, regex-parse the filename, take the last ~200 lines, hand it all to the agent. That's it. No stage classification, no error vocabulary, no internal-structure assumptions.

This is intentional, not lazy. We are an external project looking at the **finished artifact** of SDK Manager — an opaque `.zip` whose internal layout, log file naming conventions, error code semantics, and severity grading are NVIDIA-internal implementation details. Any pre-classification we wrote without access to those internals would be guesswork. We tried it once (a curated `log_patterns.yaml` of ~20 regexes); too many patterns were hallucinated against training data instead of grounded in real logs. The refactor that removed it is in commit `6765bed`.

What we DO verify works (against five real exports — see "Test corpus" below):

- `.zip` archive format and the two real filename patterns: long (`SDKM_logs_JetPack_<ver>_<host>_for_Jetson_<board>_<date>_<time>.zip`) and short (`SDKM_logs_<date>_<time>.zip`)
- Filename → target / JetPack / host OS / timestamp extraction (deterministic regex)
- Concat all `.log` / `.txt` files inside the archive; take the last 200 lines

What only the SDK Manager team can do reliably:

- **Read the log-producer code.** Errors in SDK Manager come from specific code paths (Electron main / renderer / worker subprocess / PowerShell query scripts on Windows / bash scripts on Linux). Knowing which path emits which error string lets you build a real parser that classifies by code site, not by string match.
- **Surface internal error codes.** Real exports contain `error code is: 2001`, `Task 0x0 failed (err: 0x1f1e050d)`, `command error code: 11` — all NVIDIA-internal numeric/hex codes. Their meanings live in the source.
- **Use the internal directory layout.** Real archives have `sdkm-*.log` (session log) + `downloadLogs/sdkm_download-*.log` (download subsystem) + likely more subsystem-specific files. Knowing which file represents which subsystem lets you query the relevant one instead of concatenating everything.
- **Schema-validate the log.** SDK Manager logs follow internal conventions (`HH:MM:SS.mmm - <severity>: <message>`, event-pattern `Event: <COMPONENT>@<TARGET> - status is: <status>`). With the producer source, you parse this as structured records instead of free text.

**Once these are accessible — i.e. once we are SDK Manager team members rather than external researchers — the real, reliable parser gets written.** Until then, the surface-level layer keeps the agent honest: it never trusts a classification we made up, it just reads the actual log content. The MCP boundary localizes the eventual change to a single replaceable function (`parse_install_log`); the agent loop, troubleshoot orchestrator, prompt template, web_search integration, and output writer all stay unchanged.

### Test corpus: real SDK Manager exports

Five real `.zip` exports from public NVIDIA Developer Forum posts are committed to [`data/sample_logs/`](data/sample_logs/) for reproducible validation. The parser + agent runs end-to-end against each:

| # | Source thread | Filename (in `data/sample_logs/`) | Failure scenario |
|---|---|---|---|
| 1 | [Can not flash JetPack 6.1 on AGX Orin via SDK Manager](https://forums.developer.nvidia.com/t/can-not-flash-jetpack-6-1-on-jetson-agx-orin-via-sdk-manager/308377) | `SDKM_logs_JetPack_6.1_Linux_for_Jetson_AGX_Orin_modules_2024-09-30_16-09-17.zip` | JetPack 6.1 flash failure on AGX Orin |
| 2 | [How to flash MCU's firmware on AGX Orin 64G DK](https://forums.developer.nvidia.com/t/how-to-flash-mcus-firmware-on-agx-orin-64g-dk/366168) | `SDKM_logs_JetPack_6.2.2_Linux_for_Jetson_AGX_Orin_modules_2026-04-10_10-51-27.zip` | MCU firmware flash, JetPack 6.2.2 |
| 3 | [Flashing Orin Nano via SDK Fails](https://forums.developer.nvidia.com/t/flashing-orin-nano-via-sdk-fails/318733) | `SDKM_logs_2025-01-03_13-01-22.zip` | WSL-based flash failure (short filename — only timestamp encoded, agent infers target/JP from log body) |
| 4 | [Install JetPack 6.2 failed with SDK manager on AGX orin 64G](https://forums.developer.nvidia.com/t/install-jetpack-6-2-failed-with-sdk-manager-on-agx-orin-64g/321524) | `SDKM_logs_JetPack_6.2_Linux_for_Jetson_AGX_Orin_64GB_2025-01-26_11-41-13.zip` | JetPack 6.2 install fail on AGX Orin 64GB |
| 5 | [Flashing JetPack 6.2 ... command error code: 11](https://forums.developer.nvidia.com/t/flashing-jetpack-6-2-using-sdk-manager-displays-command-error-code-11/327911) | `SDKM_logs_JetPack_6.2_Linux_for_Jetson_Orin_Nano_[8GB_developer_kit_version]_2025-03-21_12-48-45.zip` | JetPack 6.2 Orin Nano + bracketed board variant in filename |

To run troubleshoot against any one:

```powershell
python main.py --troubleshoot data/sample_logs/SDKM_logs_2025-01-03_13-01-22.zip
```

**Privacy redaction**: archives committed here have been redacted to remove other users' personal content — `/home/<user>/` paths, Windows user folders, email addresses, non-NVIDIA IPs, and identifiable company names are replaced with `REDACTED` / `X.X.X.X`. All error messages, error codes, component names, target IDs, JetPack versions, timestamps, and log structure are preserved verbatim — exactly what the agent needs. See [`data/sample_logs/README.md`](data/sample_logs/README.md) for full provenance + redaction policy. The script that performed the redaction is at [`scripts/redact_logs.py`](scripts/redact_logs.py) (reproducible).

Findings that drove parser changes during this validation pass:

- **Real archive format is `.zip`**, not `.tar.gz` (commit `6765bed`)
- **Two filename forms exist** — long-form with JetPack/host/target encoded, short-form with only timestamp (commit `97b6c61`)
- **Bracketed board variant tags** like `[8GB_developer_kit_version]` appear in real filenames — regex extended to allow `[]` (commit pending)
- **Internal layout has a `downloadLogs/` subdirectory** with subsystem-specific session logs; we read concatenated content (agent figures out which subsystem from context)
- **Real failure signatures** in the 200-line tail include `error: install process failure`, `error: cannot get component by id undefined`, `error: completeSetup failed`, plus telemetry validation errors (`Failed to validate GA4 event. Validation errors: ...`). Each is interpreted by the agent at runtime — we don't pre-classify them.

The first end-to-end troubleshoot run (on a Windows-host export the demo author generated by deliberately triggering a Jetson Thor setup failure) correctly identified the root cause as missing USBIPD on the Windows host and cited [docs.nvidia.com/sdk-manager/install-with-sdkm-jetson/index.html](https://docs.nvidia.com/sdk-manager/install-with-sdkm-jetson/index.html) via web_search — without any pre-classification layer. The agent reading the raw tail was sufficient.

---

## Evaluation

Three tracks. All run with `python main.py --eval <track>`:

| Track | Method | Cases | Latest score | Target |
|---|---|---|---|---|
| Smoke | Exact field match (product / version / target / additional_sdks) | 5 hand-crafted | **15/15 (100%)** | ≥80% |
| Reasoning | LLM-as-judge, 4 axes, 3× median | 20 forum-mined | **3.56/5** | ≥3.5/5 |
| Troubleshoot | LLM-as-judge, 4 axes, 3× median | 15 log-snippet | **4.65/5** | ≥3.5/5 |

### Per-axis breakdown

**Reasoning** (4 axes × 1-5):
- Factual correctness: 3.20
- Reasoning quality: 3.35
- Constraints respected: 4.95
- INI validity: 2.75

**Troubleshoot** (4 axes × 1-5, current pattern-less + mandatory web_search architecture):
- Error correctly identified: 4.93 (one case scored 4.0 — devzone-auth-fail, agent missed a nuance about session vs credential failure)
- Fix matches expert reference: 3.87 (most cases 4.0; kernel-module-mismatch dropped to 2.0 — agent missed the kernel-headers reinstall step the expert reference highlights)
- Fix is actionable: 4.87
- Safety (sudo warnings, destructive-op flags): 4.93

Troubleshoot eval invokes the SDK backend's `web_search` tool on every case (it's mandatory in the synthesis prompt — see `src/troubleshoot.py`). This introduces some judge-side variance from one run to the next; the 4.65 above is the median of 3 judge invocations per case.

#### Refactor preservation finding

Before the troubleshoot refactor (curated `data/log_patterns.yaml` with hand-encoded `error_class` labels + `search_terms` hints) this same suite scored **4.70/5**. After dropping the pattern library entirely — agent reads the raw log tail and uses `web_search` directly — it scores **4.65/5**.

The 0.05 delta sits inside the judge's noise floor (per-case judge runs vary by ≥0.3 single-axis). Translation: a hand-curated knowledge layer covering apt / flash / kernel / network failure modes turned out to be **removable without measurable accuracy loss**, once the agent had reliable web grounding. The classification work the curation did was already being done by `web_search` + Claude reading log content. Keeping the patterns would have been engineering debt for no benefit.

This is the troubleshoot-side mirror of the smoke-eval ablation below: in both cases, the "smart" extra layer adds nothing once tools and grounding are in place. The 80/20 of agent quality is in the tool layer and the retrieval surface — not in domain-specific curation.

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

## Production gaps

If this demo were to ship as part of SDK Manager — or any product handling real user installs at scale — the 80% of work _not_ in this codebase would look like:

### 1. Privacy / data flow

User logs contain hostnames, IPs, usernames, sometimes internal repo paths and project names. Sending them to a cloud LLM API is a compliance event in many environments.

A production version needs: PII scrubber pre-prompt (regex over `/home/[^/]+/`, RFC1918 IPs, hostname patterns); per-tenant data residency (EU users → EU-region inference); audit log of exactly what was sent and to which endpoint; a `--local-only` switch that disables outbound API calls and accepts a small capability loss.

### 2. Citation persistence

The agent cites forum URLs. Six months later, threads can be deleted, locked, version-shifted, or merged. `diagnosis.md` quietly becomes a file of dead links.

A production version needs: fetch + snapshot the cited paragraph (not the whole page) at synthesis time, embedded inline in `diagnosis.md`; optional periodic re-validation that flags broken citations.

### 3. Failure feedback loop

The agent generates `fix.sh`. The user runs it. Then nothing — no callback, no signal whether the fix worked, no learning across runs.

A production version needs: post-fix verification (re-run `--execute`, check exit code, run `nvidia-smi`); explicit user feedback prompt ("did this resolve it?"); aggregated success rate per error class to tune prompts; failed cases retained as future eval inputs.

### 4. Eval drift monitoring

The scores in the badges were measured at a fixed point against a specific model + prompt. Upgrading either drifts the numbers — sometimes silently. Today no one would notice if a prompt edit dropped reasoning from 3.56 to 3.10.

A production version needs: eval wired into CI on every prompt change; per-model baselines tracked over time; variance bars (each case run N times to characterize judge noise); a "score regression > 0.3" gate that blocks merges.

### 5. Cost / abuse controls

`max_uses=5` is a per-troubleshoot cap. There is no per-user-per-day cap. A buggy client or a coordinated abuse pattern could drain the API budget.

A production version needs: per-tenant rate limits enforced at the agent layer; daily / monthly spend ceilings with email alerts; per-feature cost telemetry (`web_search` vs synthesis vs MCP); a graceful "quota exhausted" path that refuses new dispatches without crashing.

### 6. Trust calibration ("I don't know")

The agent always produces a four-section output. When `web_search` returns nothing useful, it still writes a confident-looking `fix.sh` backed only by training knowledge. The "Why this works" paragraph admits this, but a real product should refuse to write executable script in that case.

A production version needs: a genuine `cannot diagnose` output path that produces `forum_draft.md` (escalation-by-helping-the-user-ask) instead of `fix.sh`; per-step confidence scores; UI affordances that visually flag low-confidence recommendations.

### 7. Observability / audit trail

When a user reports "the fix didn't work," there is no way to reconstruct what the agent saw and decided. Each run leaves only `fix.sh` and `diagnosis.md` — not the queries it issued, the URLs it considered and discarded, the prompt version, the model version, or the temperature.

A production version needs: structured per-run logs at `~/.sdk-advisor/runs/<run-id>.jsonl` with log hash, all queries, all URLs retrieved, URLs cited in output, model + prompt versions, cost estimate, exit status; a `sdk-advisor history` command to search past runs.

---

These gaps are intentional omissions, not oversights. Each is closable, and the work is mostly engineering — not new architecture. The point of enumerating them is to prove they're understood.

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
│   ├── log_parser.py                # SDK Manager log → LogExcerpt (zip+filename+tail; agent reads tail itself)
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

## Troubleshoot evolution roadmap

`--troubleshoot` is the deepest verb in this demo — the only one a static wizard cannot replicate. The current implementation occupies one point in a 3-axis design space; each axis has concrete next steps that don't require architecture changes.

```
Input axis    : passive  ──────── semi-active ──────── active
                (current)         (auto on              (daemon
                                   --execute fail)       monitor)

Output axis   : generate ──── execute ──── escalate ──── verify
                (current)     fix.sh        forum post    post-fix
                              with confirm  when stuck    health check

Trust axis    : full review ──── per-action confirm ──── --yolo
                (current)        (granular)              (full auto)
```

v2.0.0-c sits at `(passive, generate, full-review)`. The trade-off is deliberate: maximum safety, minimum surprise.

The `--full --mock-install` orchestrator (commit 8be775c) demonstrates how an end-to-end chain across these axes would compose — configure → install → troubleshoot → fix → retry, with canned subprocess stand-ins for the still-future Execute and Verify cells. The orchestration layer itself is built; only the bits behind the MOCKED tags are not.

### Input axis — how the agent engages

| Mode | Status | Trigger | Cost |
|---|---|---|---|
| **Passive** | ✓ shipped | `python main.py --troubleshoot <log>` | — |
| **Semi-active** | ✓ partial | `--execute` exits non-zero → offer troubleshoot on latest export log | wired in `src/execution.py` |
| **Daemon** | future | `watchdog` over `~/.nvsdkm-logs/<session>/`; notify when a session ends with "Install aborted" | ~3 hrs |
| **Multimodal** | future | Paste a screenshot of the failing SDK Manager GUI; Claude vision reads the dialog | ~1 day |

### Output axis — how far the agent goes

| Mode | Status | What it does | Cost |
|---|---|---|---|
| **Generate** | ✓ shipped | Writes `fix.sh` + `diagnosis.md` to `output/`; user runs the script themselves | — |
| **Execute** | orchestration shipped, real path future | The chain that would invoke Execute ships as `--full` (currently with `--mock-install` stand-in). Real path needs per-command risk gating: low-risk lines auto-run, sudo / destructive lines require explicit confirm | ~3 hrs once a Jetson is on hand |
| **Escalate** | future | When `web_search` returns nothing usable, drafts a NVIDIA-forum-format post with PII-scrubbed log excerpts, prefilled hardware + version fields, and a "what I've tried" hypothesis. Saves to `output/forum_draft.md` or opens the forum's new-topic URL with query params pre-populated | ~2 hrs |
| **Verify** | orchestration shipped, real check future | `--full` reserves a verify phase but currently mocks success. Real check would run `nvidia-smi`, `apt list nvidia-jetpack`, lsmod sanity — confirms the system is actually healthy, not just that installer exit was 0 | ~1 hr |

### Trust axis — how much control the user keeps

| Mode | Status | Description |
|---|---|---|
| **Full review** | ✓ shipped | User reads `fix.sh`, runs `bash fix.sh` themselves |
| **Per-action confirm** | future | Agent runs each command, pauses after any sudo / destructive line for explicit Y |
| **YOLO** | not planned | Auto-run everything. Deliberately avoided — the risk surface of LLM-generated `sudo` commands is too high without a feedback loop telling us when fixes silently broke something |

### Why this shape

Three principles drove the current anchor point:

1. **Safety > polish.** Every move along the trust axis trades user agency for convenience. For a tool that generates `sudo` commands, that's the wrong trade-off without observability the demo doesn't have.
2. **Honest failure beats confident hallucination.** At the right end of the output axis, *escalate* (drafting a forum post) is more useful than producing a low-confidence `fix.sh`. "Cannot diagnose" is a legitimate output, not a degraded one. The forum-post mode makes the agent valuable even when it doesn't know — by transferring the question to humans who do, with a properly-formatted starting point.
3. **Composable extensions, not rewrites.** Every cell in these tables is reachable from the current architecture without restructuring. The agent doesn't need to know which mode it's running in; `src/troubleshoot.py` is the only file that changes for any of these extensions.

The most under-served quadrant today is **(passive, escalate, full-review)** — when the agent can't fix the issue but *could* still hand the user a high-quality forum post. That's the next thing in the queue.

---

## Owner perspective

Software shipped by an org is the visible artifact of dozens of non-engineering decisions: where it sits in the product portfolio, who maintains it, what surface a user actually touches, how it earns its place against competing budget asks. The technical demo above answers _can this be built_. This section sketches the answers I would give if I owned shipping it.

### Surface choices — CLI is a stand-in

The demo's primary surface is a Python REPL. That's a demo choice — it's the lowest-cost surface that makes the agent loop visible. **The production surface should be something else.**

For SDK Manager's audience, the natural surfaces are:

1. **A panel inside SDK Manager's existing Electron renderer.** SDK Manager is Electron 13 + Vue 3 + Chromium. An "Ask AI" panel that talks to our two MCP servers via the same stdio protocol the demo uses is roughly two days of renderer work. Backend doesn't change.
2. **A `sdkmanager --advise` CLI subcommand.** For users who already live in the terminal — DevOps, embedded engineers, anyone CI-driven. Same backend, different front-end.
3. **A "Diagnose with AI" button next to "Export Logs" in the GUI.** This is the most natural integration point because it slots into a workflow users already know. After the GUI generates the tarball, the button feeds it straight to the troubleshoot orchestrator.

None of these require rewriting the agent or the MCP servers. The demo's architecture is deliberately UI-agnostic — the REPL is one consumer, not the consumer.

### Positioning — where this sits

This would not ship as a standalone product. It sits as **a feature inside SDK Manager**, the same way GitHub Copilot is a feature of VS Code, not a separate IDE.

Compared to NVIDIA's existing AI developer surfaces:

- **NeMo Agent Toolkit** is a framework for building agents. This demo is an agent built with the same kind of primitives — they're complementary, not competing.
- **AI-Q Blueprints** target enterprise RAG patterns. Our troubleshoot mode shares the RAG-grounding philosophy at a smaller, focused scope.
- **Nsight Copilot** targets compute-kernel optimization. Different domain, same agentic architecture pattern.

The strategic angle isn't to introduce a new product category — it's to bring SDK Manager onto the same "AI-native developer tool" footing the rest of the portfolio has moved to.

### Success metrics that matter

If this shipped inside SDK Manager, the eval scores in the badges become necessary but not sufficient. The metrics that decide whether the feature stays funded:

| Question | Metric |
|---|---|
| Does troubleshoot reduce forum dependency? | Volume of "install failure" forum threads per active user, month-over-month |
| Does discover broaden the funnel? | First-time SDK Manager users who successfully install vs bounce |
| Is the AI surface trusted? | Ratio of generated `fix.sh` files that get executed vs reviewed-and-discarded |
| Is grounding holding up over time? | Monthly re-run of a refreshed forum-mined eval, drift threshold ≤ 0.3 |

### Risks an owner watches

1. **The forum dependency.** Half the troubleshoot value comes from `forums.developer.nvidia.com`. If NVIDIA changes that forum (paid support tier, deprecation, fragmentation), the demo's value drops sharply. Contingency: index the forum content under NVIDIA's own control (internal knowledge base, in-product search).
2. **Model deprecation.** The agent is wired to Anthropic's Haiku 4.5. When Anthropic deprecates a model version, the agent silently runs on its successor; eval scores may shift in either direction without alarm. An owner watches deprecation calendars and re-runs evals proactively.
3. **The "AI made me delete my system" event.** Generated `fix.sh` runs `sudo`. Even with the review gate, eventually someone executes something they shouldn't have. The owner's job is to make that event less likely (sandboxing, aggressive risk classification, EULA review, telemetry that catches destructive patterns).
4. **Adoption inertia.** Embedded developers are unusually skeptical of AI features that mediate tools they rely on. Adoption likely looks like a slow start followed by a tipping point rather than steady growth. The owner accepts this curve when planning headcount and committed timelines.

### Where I'd invest engineering next

If I had a small team and three quarters:

- **Q1** — close the production gaps above. PII scrubber + citation persistence + audit log are existential before shipping anywhere outside an internal dogfood.
- **Q2** — integrate into SDK Manager's renderer behind a flag. Internal dogfood with NVIDIA's own DevRel and developer-marketing teams to surface real usage patterns; refine prompts against their feedback.
- **Q3** — open beta to the Jetson community. Wire the four metrics above into a dashboard. Iterate on which scenarios deserve their own focused subprompts (e.g. flash recovery is so distinct from apt failures that they probably want different prompt branches).

The technical work is the smaller half of this. The harder half is convincing SDK Manager's existing users to trust an AI in their install path — that's the owner's real job, not the engineer's.

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

## Deliberate non-goals

To set expectations clearly:

- **Not a package for end-users to install and use daily.** Polished onboarding is out of scope. The Setup section exists so the demo can be verified, not to make it adoptable.
- **Not a replacement for SDK Manager.** It complements, doesn't compete. The `.ini` it produces is consumed by SDK Manager itself; `NvSDKManager.exe` is treated as a subprocess target.
- **Not a library to fork and extend.** Data files (manifest snapshots, NGC catalog, log fixtures, README corpus) are project-specific point-in-time captures, not a generalizable starter kit.
- **Not a complete production-readiness sweep.** See _Production gaps_ above for the explicit list of what's out, and why.
- **Not a NeMo Agent Toolkit or LangGraph competitor.** Those are framework-level products; this is a single-domain agent that happens to use MCP.

Treating it as any of these will lead to disappointment. Treat it as a design study with executable evidence.

---

## License & attribution

NVIDIA SDK Manager, NGC, JetPack, Jetson, Isaac, DeepStream, etc. are trademarks of NVIDIA Corporation. All NVIDIA data is fetched from public endpoints; this repo redistributes only what is necessary for offline reproducibility (CDN manifest snapshots, GitHub README scraped via API).
