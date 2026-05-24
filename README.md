# NVIDIA SDK Advisor

A conversational agent that helps developers **discover, configure, install, and troubleshoot** NVIDIA SDKs, built on the same public data sources SDK Manager itself uses. Generates `.ini` response files SDK Manager natively consumes, and optionally drives `NvSDKManager.exe` to completion via subprocess.

[![Smoke eval: 15/15](https://img.shields.io/badge/smoke%20eval-15%2F15-brightgreen)](#evaluation) [![Reasoning: 3.56/5 — target met](https://img.shields.io/badge/reasoning-3.56%2F5%20target%20met-brightgreen)](#evaluation) [![Troubleshoot: 3.66/5 — target met](https://img.shields.io/badge/troubleshoot-3.66%2F5%20target%20met-brightgreen)](#evaluation) [![Unit tests: 84 passing](https://img.shields.io/badge/tests-84%20passing-brightgreen)](#tests)

![Architecture: 5 phases on one MCP + RAG backend](docs/demo/architecture.svg)

> **The architecture.** Five phases share one Agent + MCP + RAG backend. Red dashed line = the replaceable boundary. See [MCP design](#mcp-design) · [RAG design](#rag-design) · [End-to-end demo](#end-to-end-demo).

---

## What's in this repo

**Why it looks like this** — [Design principles](#design-principles)

**The thing itself** — [What it does](#what-it-does) · [Hero scenarios](#hero-scenarios) · [End-to-end demo](#end-to-end-demo) · [Architecture](#architecture) · [MCP design](#mcp-design) · [RAG design](#rag-design) · [Tested against](#tested-against)

**What I learned** — [If this were to ship](#if-this-were-to-ship--how-i-think-about-it) · [Evaluation](#evaluation)

**What's still open** — [What's still missing](#whats-still-missing) · [Troubleshoot evolution](#troubleshoot-evolution) · [If you're working on something similar](#if-youre-working-on-something-similar)

**Running it** — [Setup](#setup) · [Usage](#usage) · [Tests](#tests) · [Project structure](#project-structure) · [Implementation history](#implementation-history)

**Boundaries** — [Deliberate non-goals](#deliberate-non-goals)

---

## Design principles

Three judgments shaped what this repo includes and what it excludes. Reading these first makes the rest of the README make sense.

1. **The code is evidence, the README is the argument.** Reading the README without running the code should be enough to evaluate the design depth. Running the code without reading the README will miss the point.
2. **Demonstrate the architecture, not the polish.** Every code path exists to make an architectural claim concrete. Polish — CLI ergonomics, exhaustive error handling, every edge case — is deliberately under-invested where it doesn't strengthen the argument.
3. **Surface gaps honestly.** Sections below — [What's still missing](#whats-still-missing), [Troubleshoot evolution](#troubleshoot-evolution) — enumerate what a real product would need that this repo doesn't have. The point is to prove the gaps are understood, not to fill them.

This is a design study with executable evidence, not a tool meant to be adopted as-is.

---

## What it does

| Capability | Where SDK Manager stops today | What this repo adds |
|---|---|---|
| **Discover** | Flat list of NVIDIA-branded SDKs; you must already know which fits your use case | `search_3p_sample_repos` (vector search over 21 GitHub repo READMEs) + workload-to-product inference |
| **Configure** | Silent prune of invalid combinations; no resource preflight; no cross-product reasoning | 13 deterministic tools — `list_releases`, `validate_combo`, `estimate_resources`, `check_constraints` — over NVIDIA's own CDN manifests |
| **Install** | Wizard runs install; CLI takes flags. No conversational guided flow | Generates `.ini` matching NVIDIA's official template, optionally drives `NvSDKManager.exe --cli --response-file` as subprocess with streamed status + event classification |
| **Troubleshoot** | "Export logs" → user reads → user searches forum. No diagnostic surface | `parse_install_log` opens the `.zip`, extracts filename metadata + log tail. The agent reads the raw tail itself and uses `web_search` (forums, askubuntu, stackoverflow) to find expert fixes → synthesizes `fix.sh` + `diagnosis.md`. No pre-classification layer |

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

## End-to-end demo

`python main.py --full --mock-install --query "I have an Orin NX 16GB and want to do edge LLM inference"` — chains all five phases (configure → install → troubleshoot → fix → retry) with every tool call, every piece of agent reasoning, and every web_search query rendered as a discrete step.

![End-to-end demo: configure → install → troubleshoot → fix → retry](docs/demo/full-mode.gif)

> **What's mocked, what's real.** Phases 1 (configure) and 3 (troubleshoot) are real Anthropic API calls + real MCP tool dispatch + real `web_search`. Phases 2 (install) and 5 (retry) are mocked — the canned `.zip` failure / success logs stand in for `NvSDKManager.exe` because no Jetson is plugged in. Phase 4 (apply fix) is simulated for the same reason. Each phase carries an explicit REAL / MOCKED / SIMULATED tag in the panel so the boundary stays honest. [Recording recipe](docs/demo/README.md).

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

Two MCP servers, independently runnable. Server A is deterministic facts from NVIDIA-signed manifests; Server B is semantic + structured retrieval. The agent dispatches via a tool-name → session routing table.

The Python REPL is just one consumer. Either server can be spawned standalone:

```powershell
python -m src.knowledge_server   # 13 tools, stdio MCP
python -m src.rag_server         # 2 tools, stdio MCP
```

Any MCP client — Claude Code, Cursor, a custom Node renderer, anything that speaks stdio MCP — can connect and use these tools without touching the rest of this repo. The agent loop is replaceable; the data layer is not. That separation was the point of using MCP in the first place.

### Tier 3 (forums + docs) — decision log

An earlier version of this repo wrapped `forums.developer.nvidia.com` and `docs.nvidia.com` searches behind two dedicated MCP tools that called Brave Search under the hood. I removed them. They were ~2-line domain-filter shims, and Claude's native web search handles the same task cleanly. No `BRAVE_API_KEY` setup required.

The two consumers of web search are wired differently:

- **`--troubleshoot` mode (SDK backend)** — the `web_search_20250305` server-side tool is attached automatically. No domain whitelist: Claude is good at preferring NVIDIA docs / official forums / Stack Exchange on its own, and restricting to NVIDIA-only domains crowded out genuinely useful community fixes for apt / kernel / DNS errors that aren't NVIDIA-specific. The synthesis prompt makes at least one `web_search` call mandatory before recommending a fix, and ranks preferred source tiers (NVIDIA docs > NVIDIA forum > SO/AskUbuntu > GitHub issues). `max_uses=5` is the only constraint — a cost ceiling, not a trust filter. Cost: ~$0.01 per troubleshoot run. If `web_search` is unavailable (e.g. region-restricted), the agent falls back to training-knowledge synthesis with an explicit disclaimer.
- **`--troubleshoot` mode (CLI backend)** — Claude CLI's built-in WebSearch covers the same role; no extra config.
- **REPL / `--plan` mode** — web search is *not* auto-attached. The agent's primary tools are Server A's deterministic lookups + Server B's local RAG. The SYSTEM_PROMPT mentions `site:forums.developer.nvidia.com` as a hint for the CLI backend; SDK backend uses only deterministic tools for planning.

---

## MCP design

The MCP boundary is the architecture's load-bearing decision: every tool below it is independently replaceable, and the agent loop above doesn't know which transport a given tool happens to use today. This section lays out the design rationale, the routing contract the agent is expected to follow, what tests exercise that contract, and where the implementation does (and doesn't) match.

### 1. Why MCP, why split

Three reasons, in priority order:

1. **Replaceability.** Each tool is a function-shaped contract behind a name + JSON schema. The internal NVIDIA data sources I'd want to add on day one (support ticket DB, bug tracker, telemetry — see [docs/why-this-demo.md → ranked knowledge bases](docs/why-this-demo.md#if-i-joined-the-team-which-internal-knowledge-bases-id-want-ranked)) can each replace one tool without touching the agent loop, the prompt, or the output writer. That's the "production deployment is not a rewrite" claim made concrete.

2. **Deterministic vs semantic separation.** Two servers, not one:
   - `nvidia-knowledge` (13 tools) — deterministic lookups over NVIDIA-signed CDN manifests. Minimal imports, ~1s cold start.
   - `nvidia-corpus-rag` (2 tools) — semantic retrieval (Chroma + sentence-transformers). Heavy imports, ~5-10s cold start.

   Splitting them means deterministic-tool latency doesn't pay the RAG startup cost, and a deployment that needs only one half can omit the other.

3. **Replaceable consumer.** stdio MCP is wire-protocol-portable. Any client that speaks it (Claude Code, Cursor, a custom Electron renderer, an internal CI bot) can call these tools without touching this repo. The REPL is one consumer; the agent loop is replaceable; the data + tools layer is not.

### 2. The routing contract (what the SYSTEM_PROMPT tells the agent)

Distilled from `src/agent.py:SYSTEM_PROMPT`:

| Step | Tool | Fires when |
|---|---|---|
| 1 | `detect_connected_hardware` | Always, once per session |
| 2 | `lookup_target_id(board_name)` | Whenever a human board name appears (every query in practice) |
| 3 | `list_releases(product)` | Whenever a JetPack version needs picking |
| 4a | `validate_combo(jp, sdk)` | User mentions an extra SDK (DeepStream, TensorRT, …) with era-pairing constraints |
| 4b | `search_3p_sample_repos(query, k)` | User describes a workload without naming a product/SDK |
| 4c | `lookup_container_reqs(container_id)` | User mentions a specific NGC container by id |
| 4d | `estimate_resources` + `check_constraints` | User provides disk / RAM constraints |
| 5 | `generate_response_file` + `generate_command` | Always last — produce the `.ini` + sdkmanager command |

Required path: steps 1, 2, 3, 5. Steps 4a–4d are conditional.

### 3. Tests that exercise the contract

| Test | What it verifies | Where |
|---|---|---|
| Smoke 5 cases × 2 models | Tool dispatch under different query shapes | [How Haiku and Opus differ in tool usage](#how-haiku-and-opus-differ-in-tool-usage-both-still-score-100) |
| `--full --mock-install` demo | Full trace visible — every tool call + its args + result | [`docs/demo/full-mode.gif`](docs/demo/full-mode.gif), code in `src/orchestrator.py` |
| Ablation: Opus alone vs Opus + tools | The tool layer itself is what closes the 46.7 → 100% gap | [Ablation](#ablation-does-the-rag-layer-actually-help-or-does-claude-already-know-this) |
| Reasoning eval (20 forum-mined cases) | Tool dispatch under realistic user phrasing | [Evaluation](#evaluation) |

The most revealing test is smoke case 2 (AGX Orin + DeepStream 7.0). Haiku 4.5 dispatches `validate_combo` twice to check the JP↔DeepStream era pairing; Opus 4.7 skips it entirely and inlines the check from the SYSTEM_PROMPT. Both still get the right answer. A tool the bigger model routinely skips without losing accuracy is probably doing the work the smaller model can't — and removing it would degrade the smaller model. Keep the tool.

### 4. Spec compliance

Across 10 traces (5 smoke cases × 2 models):

| Spec rule | Compliance |
|---|---|
| `detect_connected_hardware` called once first | ✓ 10/10 |
| `lookup_target_id` called before downstream tools | ✓ 10/10 |
| `list_releases` called before `generate_response_file` | ✓ 10/10 |
| `search_3p_sample_repos` fires only for workload-described queries | ✓ 10/10 (only case 5 triggers) |
| `validate_combo` fires only when extra SDK present | ✓ 10/10 (only case 2 triggers, Haiku side; Opus inlines) |
| `gen_response_file` + `gen_command` are the last two calls | ✓ 10/10 |

No spec violations observed. The two behavioral differences between models (Opus's `ToolSearch` exploration in case 1, Opus's double `lookup_target_id` in case 4) take a longer compliant path; they don't break the contract.

### 5. Where the design isn't honest yet

- **Always-spawn, not lazy-spawn.** Both MCP servers boot on every agent invocation regardless of whether the query needs RAG. For configure-style queries that never call corpus-rag tools, the chromadb + sentence-transformers import is wasted work (~5-10s). The architecture supports selective spawn — `StdioServerParameters` is per-server — but the current code doesn't use that capability. A production version should add an intent classifier in front of agent.py to decide which servers to spawn.
- **Tool-list discovery isn't free.** Even if a server doesn't get a single request, the agent pays its startup cost to enumerate tools at session start. Future MCP transports (HTTP, daemon-mode) avoid this; stdio is per-session by design.

---

## RAG design

The RAG layer sits behind one MCP server (`nvidia-corpus-rag`) with two tools serving two distinct retrieval tiers. A third tier (forums + docs) lives outside MCP entirely. This section covers what each tier is for, when it fires, what tests verify it, and the spec it follows.

### 1. The three retrieval tiers

The repo started as a flat "embed everything" RAG and split into three tiers as I worked out what kind of question each one actually answers:

- **Tier 1 — NGC catalog (deterministic lookup).** A pre-curated JSONL of 20 NVIDIA-published containers (nano_llm, jetson-inference, deepstream-l4t, …) with their JetPack / CUDA / L4T requirements. Surfaced via `lookup_container_reqs(container_id)`. No vectorization — when the user names a specific container, you want exact requirements back, not "kind of similar containers."

- **Tier 2 — GitHub README vector search.** 21 hand-curated NVIDIA-AI-IOT + dusty-nv + community sample repos, READMEs embedded with `all-MiniLM-L6-v2` (sentence-transformers), indexed in Chroma. Surfaced via `search_3p_sample_repos(query, k)`. The workload-discovery layer: "I want to do X" → "here's a sample repo that does X."

- **Tier 3 — forums + docs (delegated to web_search).** Not in the MCP layer at all. When the agent needs live forum / doc content, it uses Claude's server-side `web_search` tool. An earlier version wrapped Brave Search behind two domain-filter MCP tools; I removed them because they were 2-line shims that added a `BRAVE_API_KEY` dependency for no real value. Decision log: [Tier 3 forums + docs](#tier-3-forums--docs--decision-log).

The split matters because each tier has a different cost / latency / accuracy profile. Tier 1 is microseconds (JSON lookup). Tier 2 is tens of milliseconds with embedding model already warm. Tier 3 is seconds plus an external API.

### 2. The routing contract (when RAG fires)

| User intent shape | Tool / tier |
|---|---|
| "I want to do X" — workload, no product name | `search_3p_sample_repos(query='X')` — Tier 2 |
| "How do I use dustynv/nano_llm?" — container named by id | `lookup_container_reqs(container_id='dustynv/nano_llm')` — Tier 1 |
| "What does the forum say about Y?" — live community knowledge | Tier 3 web_search (fires only in `--troubleshoot`) |
| "Configure Orin NX with JetPack 6.2" — product+version specified | Neither — RAG isn't needed |

RAG is **conditional**, not default. Most configure queries don't trigger it.

### 3. Tests that exercise the spec

| Test | What it verifies | Result |
|---|---|---|
| Smoke case 5 — "Nano + object detection sample" | search_3p_sample_repos fires for workload-described query | ✓ Both Haiku and Opus call it; top hit `jetson-inference` (correct) |
| Smoke cases 1-4 — product-specified queries | search_3p_sample_repos does NOT fire | ✓ Not called in cases 1-4 (verifies "conditional, not default") |
| `--full` demo: "Orin NX 16GB, edge LLM inference" | RAG triggered; agent gracefully handles a `lookup_container_reqs` miss | ✓ Visible in [`docs/demo/full-mode.gif`](docs/demo/full-mode.gif) — `search_3p_sample_repos` returns sample-repo hits; `lookup_container_reqs('dusty-nv/local_llm')` returns "no NGC entry"; agent continues with other tools |
| Reasoning eval (20 forum-mined cases) | Mix of discovery + configure queries | RAG fires on workload-described cases, skipped on product-specified ones |

### 4. Spec compliance

- **Selective firing — confirmed.** RAG fires only on workload-described queries, not configure-style. Across smoke + reasoning evals, no false-positive triggers observed.
- **Graceful failure — confirmed.** When `lookup_container_reqs` returns "no NGC entry" for an unknown container, the agent continues with deterministic tools instead of crashing. Verified in the `--full` demo trace (visible in the GIF).
- **Tier separation — by code shape.** Tier 1 and Tier 2 are different tools with different schemas, not different parameter values to the same tool. The agent doesn't have to "pick a mode" — the tool name **is** the mode.

### 5. Honest gaps

- **No relevance filter on Tier 2.** `search_3p_sample_repos` returns top-k by cosine similarity unconditionally. For an off-topic query ("hello world"), it still returns the 5 closest hits — barely relevant ones — without an "I don't have a good match" signal. The agent currently doesn't check the similarity score before using a hit. Production should gate on a threshold (or surface the score so the agent can decide).
- **Corpus is small (21 repos / 20 containers) and hand-curated.** Sized to demonstrate the architecture, not to cover Jetson's actual landscape. Real production would index hundreds of repos automatically, with deduplication and quality scoring.
- **No re-indexing pipeline.** Chroma index is built once via `python -m ingest.build_github_vectordb`; no scheduled refresh. Real production needs incremental updates as upstream READMEs change.

---

## Tested against

All eval numbers below come from real archives, not synthetic ones. Five real `.zip` exports pulled from public NVIDIA Developer Forum posts are committed to [`data/sample_logs/`](data/sample_logs/) — each one is in the corpus because it exercises something the parser or agent has to handle.

| # | Forum thread | Why this case is in the corpus |
|---|---|---|
| 1 | [JetPack 6.1 flash fail, AGX Orin](https://forums.developer.nvidia.com/t/can-not-flash-jetpack-6-1-on-jetson-agx-orin-via-sdk-manager/308377) | First real archive — long-form filename with `JetPack_<ver>_<host>_for_Jetson_<board>` fully encoded |
| 2 | [MCU firmware flash, AGX Orin 64G DK](https://forums.developer.nvidia.com/t/how-to-flash-mcus-firmware-on-agx-orin-64g-dk/366168) | Newer JetPack 6.2.2 — checks the parser hasn't drifted on schema changes |
| 3 | [Orin Nano flash via SDK fails](https://forums.developer.nvidia.com/t/flashing-orin-nano-via-sdk-fails/318733) | **Short-form filename** (timestamp only) — agent has to infer target / JetPack from the log body |
| 4 | [JetPack 6.2 install fail, AGX Orin 64GB](https://forums.developer.nvidia.com/t/install-jetpack-6-2-failed-with-sdk-manager-on-agx-orin-64g/321524) | RAM-tier suffix `_64GB_` in the target name — regex has to accept numeric suffixes |
| 5 | [`command error code: 11`, Orin Nano](https://forums.developer.nvidia.com/t/flashing-jetpack-6-2-using-sdk-manager-displays-command-error-code-11/327911) | **Bracketed board variant** `[8GB_developer_kit_version]` — drove a regex extension |

To run troubleshoot against any of them:

```powershell
python main.py --troubleshoot data/sample_logs/SDKM_logs_2025-01-03_13-01-22.zip
```

Plus 3 troubleshoot eval cases that use OP-pasted forum quotes where no `.zip` is available. Total: **8 cases**, all with `source_thread_url` + verification status (`op-confirmed` / `staff-recommended` / `log-grounded-forum-staff-missed-root-cause`) recorded in `tests/eval_cases/troubleshoot.jsonl`. The reasoning eval adds another 20 forum-mined cases on top.

**Privacy redaction**: archives have been redacted to remove other users' personal content — `/home/<user>/` paths, Windows user folders, email addresses, non-NVIDIA IPs, and identifiable company names are replaced with `REDACTED` / `X.X.X.X`. All error messages, error codes, component names, target IDs, JetPack versions, timestamps, and log structure are preserved verbatim — exactly what the agent needs. Full policy in [`data/sample_logs/README.md`](data/sample_logs/README.md); the redaction script is [`scripts/redact_logs.py`](scripts/redact_logs.py) (reproducible).

---

## If this were to ship — how I think about it

### Surface choices — CLI is a stand-in

The demo's primary surface is a Python REPL. That's a demo choice — it's the lowest-cost surface that makes the agent loop visible. A production version should be something else.

For SDK Manager's audience, the natural surfaces are:

1. **A panel inside SDK Manager's existing Electron renderer.** SDK Manager is Electron 13 + Vue 3 + Chromium. An "Ask AI" panel that talks to the two MCP servers via the same stdio protocol the demo uses is roughly two days of renderer work. Backend doesn't change.
2. **A `sdkmanager --advise` CLI subcommand.** For users who already live in the terminal — DevOps, embedded engineers, anyone CI-driven. Same backend, different front-end.
3. **A "Diagnose with AI" button next to "Export Logs" in the GUI.** This is the most natural integration point because it slots into a workflow users already know. After the GUI generates the tarball, the button feeds it straight to the troubleshoot orchestrator.

None of these require rewriting the agent or the MCP servers. The architecture is deliberately UI-agnostic — the REPL is one consumer, not the consumer.

### Risks that worry me

1. **The forum dependency.** Half the troubleshoot value comes from `forums.developer.nvidia.com`. If NVIDIA changes that forum (paid support tier, deprecation, fragmentation), the demo's value drops sharply. Mitigation would be indexing the forum content under NVIDIA's own control (internal knowledge base, in-product search).
2. **Model deprecation.** The agent is wired to Anthropic's Haiku 4.5. When Anthropic deprecates a model version, the agent silently runs on its successor; eval scores may shift in either direction without alarm. Anyone running this in production has to watch deprecation calendars and re-run evals proactively.
3. **The "AI made me delete my system" event.** Generated `fix.sh` runs `sudo`. Even with the review gate, eventually someone executes something they shouldn't have. Reducing the risk surface (sandboxing, risk classification, telemetry on destructive patterns) is the work that never feels done.
4. **Adoption inertia.** Embedded developers are unusually skeptical of AI features that mediate tools they rely on. Adoption likely looks like a slow start followed by a tipping point, rather than steady growth.

### Where I'd invest engineering next

If I had a small team and three quarters:

- **Q1** — close the gaps in [What's still missing](#whats-still-missing). PII scrubber, citation persistence, and audit log are existential before shipping anywhere outside an internal dogfood.
- **Q2** — integrate into SDK Manager's renderer behind a flag. Internal dogfood with NVIDIA's own DevRel and developer-marketing teams to surface real usage patterns; refine prompts against their feedback.
- **Q3** — open beta to the Jetson community. Wire success metrics (forum-thread volume, first-install success rate, fix-script execution rate, eval drift) into a dashboard. Iterate on which scenarios deserve their own focused subprompts (e.g. flash recovery is so distinct from apt failures that they probably want different prompt branches).

The technical work is the smaller half. The harder half is convincing SDK Manager's existing users to trust an AI in their install path — that's the owner's real job, not the engineer's.

---

## What's still missing

This repo is the 20%. The other 80% — the part that would actually be hard to ship — I haven't built. These are the gaps I can name; some I have rough plans for, some I don't. If you're working on similar tooling and have figured any of these out, I'd genuinely like to hear how.

### 1. Privacy / data flow

User logs contain hostnames, IPs, usernames, sometimes internal repo paths and project names. Sending them to a cloud LLM API is a compliance event in many environments.

A production version needs: PII scrubber pre-prompt (regex over `/home/[^/]+/`, RFC1918 IPs, hostname patterns); per-tenant data residency (EU users → EU-region inference); audit log of exactly what was sent and to which endpoint; a `--local-only` switch that disables outbound API calls and accepts a small capability loss.

### 2. Citation persistence

The agent cites forum URLs. Six months later, threads can be deleted, locked, version-shifted, or merged. `diagnosis.md` quietly becomes a file of dead links.

Needs: fetch + snapshot the cited paragraph (not the whole page) at synthesis time, embedded inline in `diagnosis.md`; optional periodic re-validation that flags broken citations.

### 3. Failure feedback loop

The agent generates `fix.sh`. The user runs it. Then nothing — no callback, no signal whether the fix worked, no learning across runs.

Needs: post-fix verification (re-run `--execute`, check exit code, run `nvidia-smi`); explicit user feedback prompt ("did this resolve it?"); aggregated success rate per error class to tune prompts; failed cases retained as future eval inputs.

### 4. Eval drift monitoring

The scores in the badges were measured at a fixed point against a specific model + prompt. Upgrading either drifts the numbers — sometimes silently. Today no one would notice if a prompt edit dropped reasoning from 3.56 to 3.10.

Needs: eval wired into CI on every prompt change; per-model baselines tracked over time; variance bars (each case run N times to characterize judge noise); a "score regression > 0.3" gate that blocks merges.

### 5. Cost / abuse controls

`max_uses=5` is a per-troubleshoot cap. There is no per-user-per-day cap. A buggy client or a coordinated abuse pattern could drain the API budget.

Needs: per-tenant rate limits enforced at the agent layer; daily / monthly spend ceilings with email alerts; per-feature cost telemetry (`web_search` vs synthesis vs MCP); a graceful "quota exhausted" path that refuses new dispatches without crashing.

### 6. Trust calibration ("I don't know")

The agent always produces a four-section output. When `web_search` returns nothing useful, it still writes a confident-looking `fix.sh` backed only by training knowledge. The "Why this works" paragraph admits this, but a real product should refuse to write executable script in that case.

Needs: a genuine `cannot diagnose` output path that produces `forum_draft.md` (escalation-by-helping-the-user-ask) instead of `fix.sh`; per-step confidence scores; UI affordances that visually flag low-confidence recommendations.

### 7. Observability / audit trail

When a user reports "the fix didn't work," there's no way to reconstruct what the agent saw and decided. Each run leaves only `fix.sh` and `diagnosis.md` — not the queries it issued, the URLs it considered and discarded, the prompt version, the model version, or the temperature.

Needs: structured per-run logs at `~/.sdk-advisor/runs/<run-id>.jsonl` with log hash, all queries, all URLs retrieved, URLs cited in output, model + prompt versions, cost estimate, exit status; a `sdk-advisor history` command to search past runs.

---

These aren't theoretical — each one is a problem I want to take on next. The list is long enough to fill a real ship cycle for a small team, and the e2e scenario is clear enough that the work is mostly execution from here. I want to be the one driving it.

---

## Troubleshoot evolution

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

The most under-served quadrant today is **(passive, escalate, full-review)** — when the agent can't fix the issue but *could* still hand the user a high-quality forum post. That's what I want to build next.

---

## If you're working on something similar

This repo is the starting line, not the finish. The e2e scenario — discover → configure → install → troubleshoot — has a lot of room left in it, and the gap between what's here and what a real SDK Manager AI feature would need is mostly execution work. I'd like to lead that work, and I'd like to do it with people who care about the same problems.

A few things I'd want to compare notes on right now:

- **MCP tool granularity** — 13 deterministic + 4 RAG tools felt about right for this problem, but I don't have a principled rule for when to merge tools vs split them. My current intuition: split when the LLM gets the wrong combination of args. If you've worked out a better heuristic, I'd like to hear it.
- **Self-grading bias in LLM-as-judge eval** — the 4.65 → 2.98 gap I hit isn't unique to me. If you've found a clean way to detect this before shipping a number (other than "rewrite all your cases from external sources"), please tell me.
- **Surface-level log parsing vs internal schema** — I deliberately kept `parse_install_log` dumb (zip + filename regex + tail). For external projects looking at opaque artifacts, this seems like the right tradeoff. Inside the producer team, the calculation flips. I'd love to compare notes with anyone who's worked both sides.
- **The "I don't know" output path** — I think the more interesting agent behavior is the one that drafts a `forum_draft.md` instead of `fix.sh` when confidence is low. Haven't built it yet — it's at the top of the queue.

Contact: open an issue, start a discussion, or reach me at [github.com/mingdongt](https://github.com/mingdongt).

---

## Evaluation

Two results from building and scoring this changed how I think about agent design. They're written up in detail below — the short versions:

- **The tool layer, not the model, is where the accuracy lives.** Opus 4.7 alone scored 46.7% on factual SDK questions; with the MCP tool layer attached, both Haiku 4.5 and Opus scored 100%. → [Ablation](#ablation-does-the-rag-layer-actually-help-or-does-claude-already-know-this)
- **Self-grading bias is worth 1.67 points of LLM-as-judge score.** Cases I authored end-to-end scored 4.65/5. The same agent, rescored against cases mined from real forum threads, dropped to 2.98/5. Rebuilt against verbatim log lines from OP's actual `.zip` exports, it recovered to 3.66/5 — above target. → [Self-grading bias finding](#self-grading-bias-finding-and-the-surprising-recovery)

Three tracks. All run with `python main.py --eval <track>`:

| Track | Method | Cases | Latest score | Target |
|---|---|---|---|---|
| Smoke | Exact field match (product / version / target / additional_sdks) | 5 hand-crafted | **15/15 (100%)** | ≥80% |
| Reasoning | LLM-as-judge, 4 axes, 3× median | 20 forum-mined | **3.56/5** | ≥3.5/5 |
| Troubleshoot | LLM-as-judge, 4 axes, 3× median | 8 forum-grounded | **3.66/5** | ≥3.5/5 |

### Per-axis breakdown

**Reasoning** (4 axes × 1-5):
- Factual correctness: 3.20
- Reasoning quality: 3.35
- Constraints respected: 4.95
- INI validity: 2.75

**Troubleshoot** (4 axes × 1-5):
- Error correctly identified: 3.63
- Fix matches reference: 2.50
- Fix is actionable: 4.13
- Safety (sudo warnings, destructive-op flags): 4.38

### Self-grading bias finding (and the surprising recovery)

An earlier version of this suite used 15 cases where **I authored the log snippet, I authored the "expected fix," and Claude judged the agent against it**. That suite scored **4.65/5**. When the cases were rewritten using 10 NVIDIA Developer Forum threads — log snippets paraphrased from OP descriptions, "expected fix" set to whatever the NVIDIA staff member or OP confirmed actually worked — the same agent on the same code scored **2.98/5**. **The 1.67-point gap was self-grading bias.**

A second rewrite then tightened the inputs again. For 5 of the 8 cases I have the OP's actual SDK Manager export `.zip` committed to `data/sample_logs/`, so `log_inline` was replaced with **verbatim error lines extracted from the OP's own log file** rather than my paraphrase of the OP's forum description. The other 3 cases use lines the OP literally pasted into their forum post. **Zero cases now contain text I authored.** Score rose to **3.66/5** — above target.

The +0.68 swing (2.98 → 3.66) is itself a finding: **richer, real log content lets the agent do its job better**. When fed a paraphrased symptom description ("the install gets stuck at 99%") the agent latches onto a generic 99%-stuck fix and misses the actual root cause; when fed the verbatim log line (`Error: Invalid target board - holoscan-devkit` followed by `failed to read rcm_state`), the agent correctly identifies a board-variant selection issue that even the original forum staff missed (case 6). The lift comes from the input, not from any code change.

Where the ceiling still sits: **fix matches reference = 2.50** is the weakest axis. The agent's `web_search` often surfaces *a* plausible fix for the symptom but lands on the wrong forum thread when multiple failure modes share the same symptom. That's the retrieval/grounding gap an SDK Manager team insider with access to internal triage data could close.

Cases and their provenance: `tests/eval_cases/troubleshoot.jsonl` — each line carries `source_thread_url`, `verification` (`op-confirmed` / `staff-recommended` / `staff-documented-limitation` / `log-grounded-forum-staff-missed-root-cause`), and `log_source` (`zip-tail-real` for 5 cases, `forum-quoted` for 3).

### Refactor preservation finding

Before the troubleshoot refactor (curated `data/log_patterns.yaml` with hand-encoded `error_class` labels + `search_terms` hints) the *synthetic* suite scored **4.70/5**. After dropping the pattern library entirely it scored **4.65/5** on the same synthetic suite. The forum-grounded rewrite (3.66) supersedes both numbers, but the synthetic-to-synthetic ablation still stands: against the same questions, removing the hand-curated layer made no measurable difference. The classification work the curation did was already being done by `web_search` + Claude reading log content — the troubleshoot-side mirror of the smoke-eval ablation below.

### Ablation: does the RAG layer actually help, or does Claude already know this?

Three-way smoke-eval comparison (same 5 hand-crafted cases, same scorer):

| Configuration | Backend | Tools | Smoke score | Δ vs baseline |
|---|---|---|---|---|
| **A** | Anthropic SDK + **Haiku 4.5** | + Server A (13) + Server B (4) | **15/15 (100%)** | +53.3 pp |
| **B** | Claude CLI + **Opus 4.7** | + Server A (13) + Server B (4) | **15/15 (100%)** | +53.3 pp |
| **C (baseline)** | Claude CLI + **Opus 4.7** | _none — model alone with format prompt_ | **7/15 (46.7%)** | — |

Opus 4.7 alone, even when explicitly told to produce `sdkmanager` commands in the right format, scores 46.7% on factual NVIDIA SDK questions. The misses are all hallucinations the model couldn't ground:

- `--product jetpack` instead of `Jetson` (product/version confusion)
- `--product JETSON_ORIN_NX_TARGETS` (target ID written into the product field)
- `JETSON_XAVIER_TARGETS` instead of `JETSON_AGX_XAVIER_TARGETS` (invented variant)
- Original Jetson Nano (4GB) paired with JetPack 5.0 — but Nano only supports up to 4.6.4

With the RAG + deterministic tool layer, both Haiku 4.5 and Opus 4.7 score perfectly. The tools convert the model's knowledge into executable, factually-grounded artifacts. Haiku + this layer matches Opus + this layer at this scoring axis.

Try it:

```powershell
$env:ANTHROPIC_BACKEND="cli-no-tools"; python main.py --eval smoke   # Opus alone
$env:ANTHROPIC_BACKEND="cli";          python main.py --eval smoke   # Opus + tools
$env:ANTHROPIC_BACKEND="sdk";          python main.py --eval smoke   # Haiku + tools (default)
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

Two behavioral signals worth noting:

1. **Opus skips `validate_combo` (case 2)** — it reads the JetPack ↔ addon-SDK era table in the SYSTEM_PROMPT and inlines the check rather than dispatching the tool. Haiku takes the tool path literally; Opus internalizes the rule. Both still get the right answer. A tool the bigger model routinely skips without losing accuracy is probably doing the work the smaller model can't — and removing it would degrade the smaller model. Keep the tool.

2. **Opus double-checks `lookup_target_id` (case 4)** — it dispatches the lookup tool a second time on the same input. Haiku doesn't. This isn't a smarter behavior, just a more conservative one; correctness doesn't depend on it, but it shows up in the trace.

These are surface-level differences in how the two models walk the agent graph. The outcome on every case is identical (15/15 each). Change the model, behavior shifts slightly, output stays correct.

### Log handling: surface-level by design

The log-reading layer is **deliberately surface-level**: open the `.zip`, regex-parse the filename, take the last ~200 lines, hand it all to the agent. That's it. No stage classification, no error vocabulary, no internal-structure assumptions.

This is intentional, not lazy. This is an external project looking at the **finished artifact** of SDK Manager — an opaque `.zip` whose internal layout, log file naming conventions, error code semantics, and severity grading are NVIDIA-internal implementation details. Any pre-classification written without access to those internals would be guesswork. I tried it once (a curated `log_patterns.yaml` of ~20 regexes); too many patterns were hallucinated against training data instead of grounded in real logs. The refactor that removed it is in commit `6765bed`.

What I verify works (against five real exports — see "Test corpus" below):

- `.zip` archive format and the two real filename patterns: long (`SDKM_logs_JetPack_<ver>_<host>_for_Jetson_<board>_<date>_<time>.zip`) and short (`SDKM_logs_<date>_<time>.zip`)
- Filename → target / JetPack / host OS / timestamp extraction (deterministic regex)
- Concat all `.log` / `.txt` files inside the archive; take the last 200 lines

What only the SDK Manager team can do reliably:

- **Read the log-producer code.** Errors in SDK Manager come from specific code paths (Electron main / renderer / worker subprocess / PowerShell query scripts on Windows / bash scripts on Linux). Knowing which path emits which error string lets you build a real parser that classifies by code site, not by string match.
- **Surface internal error codes.** Real exports contain `error code is: 2001`, `Task 0x0 failed (err: 0x1f1e050d)`, `command error code: 11` — all NVIDIA-internal numeric/hex codes. Their meanings live in the source.
- **Use the internal directory layout.** Real archives have `sdkm-*.log` (session log) + `downloadLogs/sdkm_download-*.log` (download subsystem) + likely more subsystem-specific files. Knowing which file represents which subsystem lets you query the relevant one instead of concatenating everything.
- **Schema-validate the log.** SDK Manager logs follow internal conventions (`HH:MM:SS.mmm - <severity>: <message>`, event-pattern `Event: <COMPONENT>@<TARGET> - status is: <status>`). With the producer source, you parse this as structured records instead of free text.

From the outside, the surface-level layer keeps the agent honest: it never trusts a classification I made up, it just reads the actual log content. The MCP boundary localizes any eventual change to a single replaceable function (`parse_install_log`); the agent loop, troubleshoot orchestrator, prompt template, web_search integration, and output writer all stay unchanged.

The parser was validated against the five real exports listed in [Tested against](#tested-against). Findings that drove parser changes during that validation pass:

- **Real archive format is `.zip`**, not `.tar.gz` (commit `6765bed`)
- **Two filename forms exist** — long-form with JetPack/host/target encoded, short-form with only timestamp (commit `97b6c61`)
- **Bracketed board variant tags** like `[8GB_developer_kit_version]` appear in real filenames — regex extended to allow `[]` (commit pending)
- **Internal layout has a `downloadLogs/` subdirectory** with subsystem-specific session logs; the parser reads concatenated content (agent figures out which subsystem from context)
- **Real failure signatures** in the 200-line tail include `error: install process failure`, `error: cannot get component by id undefined`, `error: completeSetup failed`, plus telemetry validation errors (`Failed to validate GA4 event. Validation errors: ...`). Each is interpreted by the agent at runtime — the parser doesn't pre-classify.

The first end-to-end troubleshoot run (on a Windows-host export generated by deliberately triggering a Jetson Thor setup failure) correctly identified the root cause as missing USBIPD on the Windows host and cited [docs.nvidia.com/sdk-manager/install-with-sdkm-jetson/index.html](https://docs.nvidia.com/sdk-manager/install-with-sdkm-jetson/index.html) via web_search — without any pre-classification layer. The agent reading the raw tail was sufficient.

---

## Running it

### Setup

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

The Chroma vector store is binary, fast-changing, and would bloat the repo via Git LFS. The committed JSONL corpus is the input; the index is local-rebuild.

Backend selection (Anthropic SDK / `claude` CLI subscription / baseline-no-tools), `GH_TOKEN` for raising the GitHub API rate limit, and corpus re-ingestion options are documented in [`docs/setup.md`](docs/setup.md). The architectural decision behind no Brave API key and no domain whitelist for web_search is logged inline at [Architecture → Tier 3 decision log](#tier-3-forums--docs--decision-log).

### Usage

```powershell
python main.py                           # default — conversational REPL, generates .ini + .command
python main.py --dry-run                 # invoke NvSDKManager --query against latest plan
python main.py --execute                 # actually install (confirmation + sudo prompt)
python main.py --troubleshoot <log>      # diagnose an SDK Manager log archive or .log file
python main.py --full --mock-install --query "<text>"   # end-to-end: configure → install → troubleshoot → fix → retry
python main.py --eval smoke              # Plan A eval (5 hand-crafted cases, exact match)
python main.py --eval reasoning          # Plan B eval (20 LLM-judged cases)
python main.py --eval troubleshoot       # Plan C eval (8 forum-grounded LLM-judged cases)
```

#### End-to-end mode (`--full`)

`--full --mock-install` chains all five phases of the troubleshoot story into one continuous CLI session:

1. **Configure** [REAL] — agent + MCP + `.ini` / `.command` generation
2. **Install** [MOCKED] — canned SDK Manager failure log (no real subprocess; no hardware needed)
3. **Troubleshoot** [REAL] — agent re-reads the mock log, invokes `web_search`, synthesizes `fix.sh` + `diagnosis.md`
4. **Apply fix** [SIMULATED] — prints `bash fix.sh` command, does not actually execute
5. **Retry install** [MOCKED] — canned success log

Each phase header in the output is tagged REAL / MOCKED / SIMULATED so the audience sees exactly where grounding ends and canned content begins. Without `--mock-install`, `--full` exits with an error: real-hardware end-to-end isn't built yet (needs a connected Jetson + a deterministic failure recipe).

#### Execution mode safety

`--execute` requires explicit `yes` confirmation in the same session. On Linux, also prompts for sudo via `getpass`.

#### Self-healing chain on failure

When `--execute` exits non-zero, the agent automatically finds the most recent SDK Manager log and offers to enter `--troubleshoot` on it. Two sources are searched, in priority order:

1. Exported tarballs (`sdkm-*log*.tar*`) in `~`, `~/Downloads`, or `cwd`
2. Raw session `.log` files in `~/.nvsdkm-logs/` (Linux/Mac) or `~/AppData/Local/NVIDIA Corporation/SDK Manager/logs/` (Windows)

The user does NOT need to run `--export-logs` manually. SDK Manager writes raw session logs during install; the parser reads them directly. `--export-logs` packaging is a sharing convenience, not a prerequisite. The most-recent file (by mtime) across both sources wins.

`--troubleshoot` itself is read-only by default — it generates `fix.sh` and `diagnosis.md` but does NOT execute them. The user must review and run `bash fix.sh` themselves.

### Tests

```powershell
pytest                                   # all 84 unit tests
pytest tests/test_response_file.py -v    # response file format alignment with NVIDIA template
pytest tests/test_log_parser.py -v       # 20-pattern log parser against 5 fixture logs
```

### Project structure

```
nvidia-sdk-advisor/
├── main.py                  # CLI entry, mode dispatch
├── src/                     # agent, MCP servers, parsers, REPL
├── ingest/                  # NVIDIA CDN / NGC / GitHub README ingestion
├── data/                    # manifests, corpus, fixtures, sample logs
├── tests/                   # 84 unit tests + 3 eval suites + fixtures
└── output/                  # generated .ini, .command, fix.sh, diagnosis.md
```

Full file-level tree (with one-line description per module) in [`docs/structure.md`](docs/structure.md).

### Implementation history

Plan series (in commit order, tagged in git):

| Tag | Scope | Commits |
|---|---|---|
| **v2.0.0-a** | Foundation: Server A skeleton + 11 deterministic tools + REPL + `--plan` | A.1–A.15 |
| _delta_ | `validate_combo` (12th Server A tool) | 1 commit |
| **v2.0.0-b** | RAG layer (Server B: NGC + GitHub) + `--dry-run` + `--execute` modes + reasoning eval | B.1–B.13 |
| **v2.0.0-c** | `parse_install_log` (13th Server A tool) + `--troubleshoot` mode + troubleshoot eval | C.1–C.10 |

---

## Deliberate non-goals

- **Not a package for end-users to install and use daily.** Polished onboarding is out of scope. The Setup section exists so the demo can be verified, not to make it adoptable.
- **Not a replacement for SDK Manager.** It complements, doesn't compete. The `.ini` it produces is consumed by SDK Manager itself; `NvSDKManager.exe` is treated as a subprocess target.
- **Not a library to fork and extend.** Data files (manifest snapshots, NGC catalog, log fixtures, README corpus) are project-specific point-in-time captures, not a generalizable starter kit.
- **Not a complete production-readiness sweep.** See [What's still missing](#whats-still-missing) for the explicit list of what's out, and why.
- **Not a NeMo Agent Toolkit or LangGraph competitor.** Those are framework-level products; this is a single-domain agent that happens to use MCP.

---

## License & attribution

NVIDIA SDK Manager, NGC, JetPack, Jetson, Isaac, DeepStream, etc. are trademarks of NVIDIA Corporation. All NVIDIA data is fetched from public endpoints; this repo redistributes only what is necessary for offline reproducibility (CDN manifest snapshots, GitHub README scraped via API).
