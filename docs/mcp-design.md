# MCP Layer Design Manual

> This is the deep dive that [README §MCP design](../README.md#mcp-design) points to. README gives the conclusions; this document gives the decision process.

## Who this is for

- Engineers extending or replacing this repo's MCP layer
- Reviewers evaluating the repo as a portfolio piece
- The author, six months from now, trying to recall "why did we do it this way"

## What this is NOT

- Not a generic MCP introduction — read the [MCP spec](https://modelcontextprotocol.io) or [FastMCP docs](https://gofastmcp.com) for that
- Not a RAG architecture manual — that gets its own document
- Not a per-API reference — use docstrings + IDE for that

## Table of contents

- [Ch 1. Why MCP, not something simpler](#ch-1-why-mcp-not-something-simpler)
- [Ch 2. Why split into two servers](#ch-2-why-split-into-two-servers)
- [Ch 3. Tool granularity](#ch-3-tool-granularity)
- [Ch 4. The 15 tools, one by one](#ch-4-the-15-tools-one-by-one)
- [Ch 5. Tool relationships](#ch-5-tool-relationships)
- [Ch 6. Routing contract & spec compliance](#ch-6-routing-contract--spec-compliance)
- [Ch 7. Where the design isn't honest yet](#ch-7-where-the-design-isnt-honest-yet)
- [Appendix](#appendix)

---

## Ch 1. Why MCP, not something simpler

### The problem

The LLM needs to call this repo's domain logic (manifest lookups, target-ID normalization, INI rendering, ...). Three paths exist:

```
┌──────────────────────────────┬──────────────────┬───────────────────────┐
│         Path                  │   Cost           │   Tool lives          │
├──────────────────────────────┼──────────────────┼───────────────────────┤
│ A. Bare tool use              │  lowest          │  agent process        │
│    (hand-coded JSON schema)   │                  │                       │
│                              │                  │                       │
│ B. In-process import          │  low             │  agent process        │
│    (call function directly)   │                  │                       │
│                              │                  │                       │
│ C. MCP server                 │  medium          │  separate subprocess  │
│    (stdio JSON-RPC)           │                  │  + protocol contract  │
└──────────────────────────────┴──────────────────┴───────────────────────┘
```

A is the native form of OpenAI / Anthropic function calling: you write tool definitions, pass them to `client.messages.create(tools=...)`.
B isn't really "tool use" — just importing functions into the agent loop and dispatching manually.
C runs tools in a separate process; the agent talks to them via stdio + JSON-RPC.

### The choice + three reasons

We chose C. Reasons in priority order:

**1. Replaceability**

Each tool is a contract of `name + JSON schema`. Today `list_releases` reads a local manifest; tomorrow it could hit an NVIDIA internal API, an internal support-ticket system, or another team's service. **The agent loop doesn't change. The prompt doesn't change.** Only the server's internal implementation does. This is what README §1 means by "production deployment is not a rewrite."

Path A can't do this — schemas are hard-coded in client code. Path B can't either — agent is directly coupled to the implementation.

**2. Portability (consumer-portable)**

stdio MCP is wire-protocol-portable. Claude Code / Cursor / a custom Electron renderer / a CI bot — any client that speaks stdio MCP can connect to these servers. With paths A or B, the tools only belong to this Python orchestrator; they're not reusable across IDEs or languages.

**3. Isolation (process isolation)**

`rag_server` pulls in chromadb + sentence-transformers, takes 5-10s to start, and can crash. Co-locating it with the agent means one crash kills the whole session. A separate process is cheap insurance.

### Key code evidence

[src/agent.py:99-103](../src/agent.py#L99-L103):

```python
def _build_tools(mcp_tools) -> list:
    return [
        {"name": t.name, "description": t.description, "input_schema": t.inputSchema}
        for t in mcp_tools
    ]
```

These 5 lines are the translation layer between MCP and Anthropic tool use — the tool descriptions from the MCP server go straight into `client.messages.create(tools=...)`. **The model is unaware MCP exists**; it just sees regular tool use.

In other words: MCP isn't for the model. It's for **tool publication and deployment**. From the model's side, paths A / B / C all look the same.

### When NOT to use MCP

Not every tool should be MCP-ified. This repo has its own counter-examples:

| Counter-example | Why MCP would be wrong |
|---|---|
| Brave Search wrapper (deleted) | 2-line domain-filter shim; adding `BRAVE_API_KEY` dependency buys no replaceability |
| `web_search_20250305` | Anthropic API's server-side tool; no need to wrap as MCP |
| `_build_install_config` ([knowledge_server.py:66](../src/knowledge_server.py#L66)) | Internal helper used only by a few tools in the same file; doesn't need a protocol boundary |

The rule: **at least one of replaceability / portability / isolation must apply**. If none do, use bare tool use (path A).

### Relation to README

README §1.1 is 3 paragraphs, ~half a page. This chapter expands each paragraph into its own section and adds the A/B/C path comparison plus the "when not to use MCP" counter-examples.

### Chapter takeaway

**MCP isn't for the model — it's for tool publication. It solves a boundary problem, not a performance problem.**

---

## Ch 2. Why split into two servers

### The problem

15 tools — one server or many? If many, on what axis?

### Candidate comparison

```
┌──────────────────────┬────────────┬────────────┬───────────────────────┐
│   Option              │  Cold start│  Flexibility│  Cross-server coupling│
├──────────────────────┼────────────┼────────────┼───────────────────────┤
│ 1 server (15 tools)   │  5-10s     │  low        │  none                 │
│                      │  (RAG drags│             │                       │
│                      │  everything│             │                       │
│                      │  down)     │             │                       │
│                      │            │             │                       │
│ 2 servers ★ chosen    │  1s + 5s   │  medium      │  Phase 4 only         │
│                      │  (parallel)│             │                       │
│                      │            │             │                       │
│ 3+ servers           │  1s × 3+   │  highest     │  routing gets complex │
│                      │  (fragment)│             │                       │
└──────────────────────┴────────────┴────────────┴───────────────────────┘
```

### Decision: split by dependency weight, not function domain

Multiple split axes are possible:

| Axis | What it would look like | Verdict |
|---|---|---|
| By dependency weight ★ | knowledge (light) / rag (heavy) | Cold start parallelizes; deploy independently |
| By function domain | configure / install / troubleshoot | troubleshoot only has 1 tool (`parse_install_log`); empty slice |
| By data source | manifest / NGC / GitHub | NGC + GitHub are both in rag; would force client to connect to two servers for the RAG path |
| By read/write | read-only / side-effects | Almost everything here is read-only; the one exception (`detect_connected_hardware`) is just probing |

The essence of splitting by dependency weight: **don't make the 1s-cold-start tools pay for the 5-10s-cold-start tools**.

### Consequence: cross-server boundary appears only in Phase 4

```
                              ┌─ knowledge_server (light) ─┐
                              │  Phase 0/1/2/3/5/6         │
                              │  13 tools                  │
                              │                            │
              ┌───────────────┤                            │
   agent ◄───►│ tool dispatch │                            │
              └───────────────┤                            │
                              │  ┌──── Phase 4 ───────────►│
                              │  │  workload discovery     │
                              │  │                         │
                              │  └─ rag_server (heavy) ◄───┘
                              │     2 tools
                              └─
```

Cross-server calls **happen only in Phase 4** — the RAG tool's output (e.g., "jetson-inference repo") feeds back into knowledge server tools (like `lookup_target_id`). The coupling surface is one phase.

This is the ruler for "was the split worth it": **if the two servers call each other constantly, you split on the wrong axis.**

### Where this isn't honest yet

`agent.py` spawns both servers on every invocation, even for config-only queries that don't need RAG. The chromadb + sentence-transformers startup cost (5-10s) is pure waste in those cases. The architecture supports lazy spawn (`StdioServerParameters` is per-server), but the current code doesn't use that capability.

A production version should put an intent classifier in front of agent.py to decide whether to spawn rag_server. Logged in README §"Where the design isn't honest yet".

### Relation to README

README §1.2 gives the two-server rationale in ~5 lines. This chapter adds the split-axis comparison + cross-server boundary diagram + the "right split vs wrong split" ruler.

### Chapter takeaway

**Splitting by dependency weight is right; if the resulting servers chatter, you split wrong.**

---

## Ch 3. Tool granularity

### The problem

How did we land on 15? Why not 5, why not 50?

### The principle

**Each tool = one observable agent decision.**

Not split by "domain noun" (Jetson / DRIVE / Holoscan). Not split by "manifest field" (target / version / sdk). Split by "what trade-off the agent needs to make."

### Counter-examples

| Counter-example | The problem |
|---|---|
| Merge everything into one `nvidia_advisor(query)` mega-tool | Agent loses visibility into intermediate steps; trace is opaque; the model does everything in one shot = black box |
| Split into 50+ tools (one per manifest field) | LLM context can't fit the tool list; decision tree too deep; model doesn't know where to start |
| Merge `validate_combo` into `generate_response_file` | When it fails, can't tell if the combo was wrong or the rendering was wrong; trace signal lost |
| Split `estimate_resources` into `estimate_disk` + `estimate_ram` | User's "do I have enough" is one decision; no reason to dispatch twice |

### Quantitative validation

[README §Tool-layer ablation](../README.md#tool-layer-ablation) — the Haiku vs Opus dispatch table — gives empirical data: **each query actually triggers 5-7 tools**.

```
┌──────────────────────────────┬─────────────────────┐
│  Tools per query              │  Interpretation     │
├──────────────────────────────┼─────────────────────┤
│  < 5                         │  Tools too coarse;  │
│                              │  agent misses steps │
│                              │                     │
│  5 - 7  ★ this repo's hit     │  Right granularity  │
│                              │                     │
│  > 10                        │  Tools too fine;    │
│                              │  agent doing busy-  │
│                              │  work orchestration │
└──────────────────────────────┴─────────────────────┘
```

The 5-7 range is empirical, not theoretical. But **violations usually mean the granularity is off**.

### Naming convention

| Field | Rule |
|---|---|
| Shape | `verb_noun` (`list_releases`, `validate_combo`, `generate_command`) |
| Verb | Exposes intent: list / get / lookup / detect / validate / estimate / check / generate / parse / search |
| Noun | Exposes object: products / releases / hardware / target_id / connected_hardware / combo / resources / constraints / response_file / command / install_log / sample_repos / container_reqs |
| Return type | All `str` (JSON-encoded). FastMCP exposes type hints to the model, but returning a JSON string (not a dict) keeps the serialization path consistent across MCP transports |

### Relation to README

README §3 gives the 5-7 hit data without explaining why that range is right. This chapter explains why it's not 5, not 50, not 100, and documents the naming convention.

### Chapter takeaway

**The granularity test is: is the trace auditable? If yes, the granularity is right.**

---

## Ch 4. The 15 tools, one by one

Each tool gets a section with a fixed template:

> **Signature** · **Returns** · **Why it exists** · **Fires when** · **Example trace** · **Common mistake**

Organized by Phase 0 → 6.

### Phase 0 — Detect (once at startup)

#### Tool 1: `detect_connected_hardware` · [Server A]

> Probe USB for connected Jetson boards.

- **Signature**: `detect_connected_hardware() -> str`
- **Returns**: `{"connected": ["JETSON_ORIN_NX_TARGETS", ...], "method": "NvSDKManager --list-connected"}`
- **Why**: The only tool that spawns a local subprocess. Isolating it means if `NvSDKManager.exe`'s CLI changes, only this one tool changes. SYSTEM_PROMPT forces it to fire once per session, first.
- **Fires when**: At session start, unconditionally.
- **Example**: User says "Configure for my Orin NX" → tool returns `["JETSON_ORIN_NX_TARGETS"]` (board is plugged in) → later phases use that target ID directly.
- **Pitfall**: If no board is connected, returns empty list (not error) — agent should continue to Phase 1.
- **Code**: [knowledge_server.py:60](../src/knowledge_server.py#L60)

### Phase 1 — Normalize (natural language → canonical ID)

#### Tool 2: `lookup_target_id` · [Server A]

> User says "Orin NX" → returns `JETSON_ORIN_NX_TARGETS`.

- **Signature**: `lookup_target_id(board_name: str) -> str`
- **Returns**: `{"target_id": "JETSON_ORIN_NX_TARGETS", "canonical_name": "..."}` or `{"error": "unknown board: ..."}`
- **Why**: [README §Tool-layer ablation](../README.md#tool-layer-ablation) shows Opus 4.7 without this tool will hallucinate `JETSON_XAVIER_TARGETS` (a non-existent variant). This tool is the **last mile of anti-hallucination** — compressing "sounds like some Jetson variant" into one of 7 real target IDs from the manifest.
- **Fires when**: User mentions any human-language board name ("Orin NX" / "Jetson Nano" / "Xavier AGX").
- **Example**: "AGX Xavier" → `JETSON_AGX_XAVIER_TARGETS` (not the model's hallucinated `JETSON_XAVIER_TARGETS`).
- **Pitfall**: When the user mistypes a model, returns error JSON rather than crashing — agent should ask for clarification.
- **Code**: [knowledge_server.py:53](../src/knowledge_server.py#L53)

### Phase 2 — Browse catalog

#### Tool 3: `list_products` · [Server A]

> What product families does NVIDIA currently publish?

- **Signature**: `list_products() -> str`
- **Returns**: `["Jetson", "DRIVE", "Holoscan", ...]`
- **Why**: Vague queries like "what SDKs does NVIDIA have" need a starting point. The model can't answer from training data (NVIDIA adds new product families often); must read the latest manifest.
- **Fires when**: User asks "what products are there" without specifying.
- **Code**: [knowledge_server.py:24](../src/knowledge_server.py#L24)

#### Tool 4: `list_releases` · [Server A]

> Which versions exist for a product (summary list).

- **Signature**: `list_releases(product: str) -> str`
- **Returns**: `[{"title": "JetPack 6.2.2", "releaseVersion": "6.2.2", "supportedHardware": [...]}]`
- **Why**: Version numbers are **time-sensitive data** — the model's training-time version list is long expired. Must hit this tool every time. The return value is deliberately stripped (title + version + hardware only); 90% of use cases only need those three fields.
- **Fires when**: User needs to pick a version ("latest JetPack" / "what versions support Orin NX").
- **Example**: `list_releases("Jetson")` → `[{"title": "JetPack 6.2.2", ...}, {"title": "JetPack 6.2.1", ...}, ...]`
- **Pitfall**: Model sometimes wants to skip and just say "the latest is 6.2". Forbidden — SYSTEM_PROMPT forces a re-query each new session.
- **Code**: [knowledge_server.py:30](../src/knowledge_server.py#L30)

#### Tool 5: `get_release` · [Server A]

> Full metadata for a single release.

- **Signature**: `get_release(product: str, version: str) -> str`
- **Returns**: Full JSON with components / sizes / dependencies
- **Why**: `list_releases` returns summary list; this returns single detail. **Two tools is cleaner than one tool with `detail=true` flag** — the LLM's decision tree is sharper.
- **Fires when**: After agent has picked a version and needs full data for resource estimation, combo validation, or INI rendering.
- **Code**: [knowledge_server.py:40](../src/knowledge_server.py#L40)

#### Tool 6: `list_hardware` · [Server A]

> What boards exist in a family.

- **Signature**: `list_hardware(family: str) -> str`
- **Returns**: `["Orin", "Xavier", "Nano", ...]`
- **Why**: User might not name a board explicitly — "I want to buy a Jetson to learn on". Discovery-style queries need a dedicated tool; can't be merged into `list_products`.
- **Fires when**: User asks "what boards are in this family".
- **Code**: [knowledge_server.py:47](../src/knowledge_server.py#L47)

### Phase 3 — Validate & budget

#### Tool 7: `validate_combo` · [Server A]

> Is this combo (JP + extra SDK + target) actually legal?

- **Signature**: `validate_combo(product, version, target, additional_sdks) -> str`
- **Returns**: `{"valid": true/false, "reason": "..."}`
- **Why**: NVIDIA SDKs have **strict generational pairing rules** (DeepStream 7.x pairs with JP 6.x; DeepStream 6.x with JP 5.x). The model can't get this right from memory — query the manifest's supported pairs. [README §Model variation](../README.md#model-variation) notes Haiku invoked this twice while Opus internalized the rule and skipped it. **It's a crutch for the smaller model — keep it.**
- **Fires when**: User adds an extra SDK (DeepStream / TensorRT / Holoscan SDK / etc.).
- **Example**: `validate_combo("Jetson", "6.2.2", "JETSON_ORIN_NX_TARGETS", ["DeepStream-7.0"])` → `{"valid": true}`
- **Pitfall**: Calling it without extra SDKs is harmless but wasteful — SYSTEM_PROMPT scopes it to "only when extra SDK present".
- **Code**: [knowledge_server.py:122](../src/knowledge_server.py#L122)

#### Tool 8: `estimate_resources` · [Server A]

> How much disk + RAM does this install need?

- **Signature**: `estimate_resources(product, version, target, target_os, host, flash, additional_sdks, login_type, action) -> str`
- **Returns**: `{"disk_gb": 35, "ram_gb": 1.7, "target_disk_gb": 22, ...}`
- **Why**: Resource math is deterministic; letting the LLM estimate is wrong (it doesn't know each component's size).
- **Fires when**: User asks "will this fit" or provides a hardware budget.
- **Pitfall**: An earlier version took `config_json: str` and made the LLM serialize a dict — frequent JSON errors. Switching to typed params lets FastMCP generate the schema; the LLM never hand-builds JSON. See [knowledge_server.py:78-86](../src/knowledge_server.py#L78-L86) for the postmortem.
- **Code**: [knowledge_server.py:95](../src/knowledge_server.py#L95)

#### Tool 9: `check_constraints` · [Server A]

> Given budget (disk + ram), does the estimate fit?

- **Signature**: `check_constraints(available_disk_gb, available_ram_gb, ...) -> str`
- **Returns**: `{"fits": true/false, "shortfall": {"disk_gb": 0, "ram_gb": 0}}`
- **Why**: Separate from `estimate_resources` because "how much is needed" and "does it fit" are **two decisions**. Splitting lets the agent reuse the estimate — compute once, check against multiple budgets.
- **Fires when**: User provides disk / RAM constraints.
- **Code**: [knowledge_server.py:108](../src/knowledge_server.py#L108)

### Phase 4 — Workload discovery (user didn't name a product)

#### Tool 10: `search_3p_sample_repos` · [Server B]

> Semantic search across 30 GitHub sample-repo READMEs.

- **Signature**: `search_3p_sample_repos(query: str, k: int = 5) -> str`
- **Returns**: `{"hits": [{"repo": "dusty-nv/jetson-inference", "score": 0.82, "chunk": "..."}, ...]}`
- **Why**: The bridge from discovery to configure. User says "I want to do X"; agent doesn't know what product maps to X. Vector search finds the README that does X, then the LLM follows the breadcrumb to a product. **This is the only tool in the project that loads an ML model (sentence-transformers)** — and the root reason for splitting into a second server (see Ch 2).
- **Fires when**: User describes a workload without naming a product/container ("I want to run YOLO at 30fps on edge").
- **Example**: query="YOLO object detection" → top hit `dusty-nv/jetson-inference/DetectNet`.
- **Pitfall**: No relevance threshold — off-topic queries still get top-k filler. Production would gate on score threshold.
- **Code**: [rag_server.py:86](../src/rag_server.py#L86)

#### Tool 11: `lookup_container_reqs` · [Server B]

> Given an NGC container name, look up its JP / CUDA / TensorRT requirements.

- **Signature**: `lookup_container_reqs(container_id: str) -> str`
- **Returns**: `{"name": "dustynv/nano_llm", "jetpack": "6.0+", "cuda": "12.2", ...}` or `{"error": "no NGC entry"}`
- **Why**: Separate from `search_3p_sample_repos` because **user intent differs**: fuzzy → semantic search, exact → structured query. This is what README means by "tool name **is** the mode" — the agent doesn't pick a mode, the tool name encodes it.
- **Fires when**: User already knows the container name (e.g. "dustynv/nano_llm").
- **Pitfall**: Supports suffix matching (`nano_llm` matches `dustynv/nano_llm`) — don't let the agent think it needs the full path.
- **Code**: [rag_server.py:49](../src/rag_server.py#L49)

### Phase 5 — Output artifacts

#### Tool 12: `generate_response_file` · [Server A]

> Render the `.ini` that NVIDIA SDK Manager consumes.

- **Signature**: `generate_response_file(product, version, target, ...) -> str`
- **Returns**: `{"content": "[application]\n...\n[product]\n...\n[targetos1]\n..."}`
- **Why**: The `.ini` format has a **strict official template** — section order and field names can't be off. Letting the LLM write it directly results in occasional missing fields. Encapsulating it: structured args in, template-conformant INI out. **The LLM decides; the tool formats.**
- **Fires when**: One of the two last steps (mandatory).
- **Code**: [knowledge_server.py:130](../src/knowledge_server.py#L130)

#### Tool 13: `validate_against_official_sample` · [Server A]

> Self-check: compare generated INI against the official sample, flag missing fields.

- **Signature**: `validate_against_official_sample(generated_ini: str, product: str) -> str`
- **Returns**: `{"valid": true, "missing": [], "extra": []}`
- **Why**: Even though `generate_response_file` is deterministic, template updates or code bugs could cause drift. Seeing a validation error, the LLM can decide whether to retry. **Separation of generate + validate is a generic engineering pattern.**
- **Fires when**: Optional self-check after `generate_response_file`.
- **Code**: [knowledge_server.py:143](../src/knowledge_server.py#L143)

#### Tool 14: `generate_command` · [Server A]

> Output the `sdkmanager --cli ...` command string.

- **Signature**: `generate_command(product, version, target, ...) -> str`
- **Returns**: `{"command": "sdkmanager --cli --product Jetson --target ... --version ..."}`
- **Why**: INI file and command-line are **two ways to invoke SDK Manager** (response file mode vs flag mode). User may want just one, or both. Separate tools = agent dispatches as needed.
- **Fires when**: One of the two last steps (mandatory).
- **Code**: [knowledge_server.py:149](../src/knowledge_server.py#L149)

### Phase 6 — Troubleshoot (exception branch)

#### Tool 15: `parse_install_log` · [Server A]

> SDK Manager `.zip` log → metadata + tail text.

- **Signature**: `parse_install_log(log_path_or_archive: str) -> str`
- **Returns**: `{"target": "...", "jetpack_version": "...", "host_os": "...", "timestamp": "...", "tail_text": "...(~200 lines)..."}`
- **Why**: Log archives are ZIPs containing multiple `.log` files; **the LLM can't unzip and concatenate tails on its own**. This tool does **structural extraction**: filename regex for metadata, last ~200 lines for content. **Does NOT classify errors** — just extracts content. Classification is left to the LLM + `web_search`. This is the key finding from [README §Pattern-library ablation](../README.md#pattern-library-ablation): the hand-curated classification layer added nothing; removing it didn't drop scores.
- **Fires when**: User runs `--troubleshoot` mode (exception branch, bypasses normal Phase 0-5).
- **Pitfall**: Accepts `.zip` / `.tar.gz` / single `.log` — don't let the agent assume a specific format.
- **Code**: [knowledge_server.py:162](../src/knowledge_server.py#L162)

### Chapter takeaway

**Presenting the 15 tools through a fixed template lets readers compare design patterns horizontally. The shared structure is the lesson — not any single tool.**

---

## Ch 5. Tool relationships

### Data dependency DAG (not a tree)

```
       USER QUERY
            │
            ▼
   ┌────────────────────┐         Phase 0 (1×, mandatory first)
   │detect_connected_   │
   │hardware  [K]       │
   └─────────┬──────────┘
             │ board names
             ▼
   ┌────────────────────┐         Phase 1 (mandatory)
   │lookup_target_id[K] │◄─────────────────────┐
   └─────────┬──────────┘                      │
             │ target_id                       │
             │                                 │
   ┌─────────┴──────┬──────────┬─────────┐    │
   ▼                ▼          ▼         ▼    │
 ┌─────┐        ┌──────┐  ┌─────────┐  ┌────┐ │
 │list_│ Ph 2  │get_  │  │validate_│  │esti│ │
 │rele │       │relea │  │combo[K] │  │mate│ │ Ph 3
 │ases │       │se[K] │  │         │  │_res│ │
 │ [K] │       │      │  │         │  │ourc│ │
 └──┬──┘       └──────┘  └─────────┘  │es[K│ │
    │                                  └──┬─┘ │
    │ version                             │   │
    │                                     ▼   │
    │                              ┌──────────┐
    │                              │check_    │ Ph 3
    │                              │constrain │
    │                              │ts  [K]   │
    │                              └──────────┘
    │
    │       ┌─── Phase 4 (workload, exclusive) ──┐
    │       │                                    │
    │   ┌───┴────┐   ┌─────────┐                 │
    │   │search_ │   │lookup_  │                 │
    │   │3p_     │   │container│                 │
    │   │sample_ │   │_reqs    │                 │
    │   │repos[R]│   │  [R]    │                 │
    │   └────┬───┘   └────┬────┘                 │
    │        │            │ container reqs       │
    │        │            └──feedback────────────┘
    │        │ sample repo → infer product
    │        └──────────────────────────┐
    │                                   │
    ▼  (product, version, target, sdks) ▼
   ┌────────────────────────────────────────┐
   │      Phase 5 (output, mandatory)       │
   │                                        │
   │  generate_response_file  [K]           │
   │       │                                │
   │       ▼ INI text                       │
   │  validate_against_official_sample [K]  │
   │       │                                │
   │       │ (warnings)                     │
   │       │                                │
   │  generate_command  [K]                 │
   │       │                                │
   │       ▼                                │
   │  final: .ini + sdkmanager cmd          │
   └────────────────────────────────────────┘

      ⊗ install fails ⊗
            │
            ▼
   ┌────────────────────┐         Phase 6 (exception)
   │parse_install_log[K]│
   └──────────┬─────────┘
              │ tail_text
              ▼
        web_search [external]
              │
              ▼
        fix.sh + diagnosis.md
```

### Hub nodes

Two nodes in the DAG have abnormally high out-degree — call them "hubs":

- **`lookup_target_id`**: outputs `target_id`, consumed by `validate_combo` / `estimate_resources` / `generate_response_file` / `generate_command` (4 downstream).
- **`list_releases`**: outputs `version`, consumed by `get_release` / `validate_combo` / `estimate_resources` / `generate_response_file` (4 downstream).

**Why hubs must be standalone tools**: if they weren't, 4-5 downstream tools would each redo the same parse (natural language → ID) or re-query (version list). Independent hubs = parse once, reuse everywhere.

### Triggering taxonomy

| Trigger type | Tools | Count |
|---|---|---|
| Mandatory (at least once per session) | detect_connected_hardware, lookup_target_id, list_releases, generate_response_file, generate_command | 5 |
| Conditional 4a (user adds extra SDK) | validate_combo | 1 |
| Conditional 4b (user describes workload) | search_3p_sample_repos | 1 |
| Conditional 4c (user names container) | lookup_container_reqs | 1 |
| Conditional 4d (user gives hardware budget) | estimate_resources, check_constraints | 2 |
| Exploratory | list_products, list_hardware, get_release | 3 |
| Optional self-check | validate_against_official_sample | 1 |
| Exception branch | parse_install_log | 1 |

### Exclusive branches

Only two:

- **Phase 4**: `lookup_container_reqs` (exact) vs `search_3p_sample_repos` (semantic) — picked by user intent, never both.
- **Phase 5**: `.ini` and command **don't depend on each other** but aren't mutually exclusive either — dispatch as needed.

### Relation to README

README §MCP design doesn't include the full DAG. This chapter adds the data flow + hub analysis + trigger table.

### Chapter takeaway

**The topology is a DAG, not a tree. The two hubs (`lookup_target_id` / `list_releases`) are the bottleneck — downstream dependencies converge on these two nodes.**

---

## Ch 6. Routing contract & spec compliance

### SYSTEM_PROMPT is the contract

agent.py's SYSTEM_PROMPT spells out the dispatch order (condensed):

```
1. detect_connected_hardware       ← always, once per session
2. lookup_target_id(board_name)    ← whenever a board name appears
3. list_releases(product)          ← whenever version needs picking
4a. validate_combo(jp, sdk)        ← if extra SDK with era-pairing
4b. search_3p_sample_repos(query)  ← if workload described, no product
4c. lookup_container_reqs(id)      ← if specific NGC container by id
4d. estimate_resources + check_constraints   ← if disk/RAM budget given
5. generate_response_file + generate_command ← always last
```

Required path: 1 → 2 → 3 → 5. 4a-d are conditional.

### Compliance verification (trace-based)

| Rule | How to verify | Current result |
|---|---|---|
| `detect_connected_hardware` fires once per session, first | Inspect trace positions | ✓ 10/10 smoke traces |
| `lookup_target_id` precedes downstream tools | Trace order check | ✓ 10/10 |
| `list_releases` precedes `generate_response_file` | Trace order check | ✓ 10/10 |
| `search_3p_sample_repos` fires only for workload queries | Check trace + query type | ✓ 10/10 (only case 5) |
| `validate_combo` fires only when extra SDK present | Check trace + extra-SDK list | ✓ 10/10 (only case 2 triggers Haiku; Opus inlines) |
| `generate_response_file` + `generate_command` are last two | Trace order check | ✓ 10/10 |
| Total tool count per query lies in 5-7 | Count trace length | ✓ 10/10 |

See [README §4 spec compliance](../README.md#mcp-design).

### Above-contract behavior

Beyond compliance, two **non-violating but interesting** behaviors show up in smoke traces:

**1. Opus 4.7 skips `validate_combo` (smoke case 2)**

For the DeepStream 7.0 + JetPack 6.2.2 + AGX Orin query, Haiku 4.5 invoked `validate_combo` twice to check era pairing; Opus 4.7 skipped it entirely — it read the era table from SYSTEM_PROMPT and internalized the rule. Both got the right answer.

**Lesson**: `validate_combo` is **a crutch for the smaller model**. Opus not needing it doesn't mean we should delete it — removing it would degrade Haiku.

**2. Opus 4.7 double-queries `lookup_target_id` (smoke case 4)**

For AGX Xavier, Opus dispatched lookup twice (same input, same output). Haiku didn't.

**Lesson**: Not a bug — Opus's more conservative self-verification. Doesn't affect correctness, but shows up in the trace.

### State machine view

Which tools are allowed in which state:

```
[INIT]
   │
   ▼  detect_connected_hardware
[POST-DETECT]
   │
   ▼  lookup_target_id     ←─ allowed: list_products, list_hardware
[NORMALIZED]
   │
   ▼  list_releases        ←─ allowed: get_release
[VERSION-PICKED]
   │
   ├─►  validate_combo     (if extra SDK)
   ├─►  search_3p_*        (if workload)
   ├─►  lookup_container_* (if container id)
   ├─►  estimate + check   (if budget)
   │
   ▼  generate_response_file
[INI-GENERATED]
   │
   ├─►  validate_against_*  (optional)
   │
   ▼  generate_command
[DONE]

[TROUBLESHOOT]   ← parse_install_log (exception branch, bypasses normal flow)
```

### Relation to README

README §4 gives conclusions; this chapter gives the **process** — how each rule becomes a verifiable trace check.

### Chapter takeaway

**SYSTEM_PROMPT is the contract; traces are the evidence. Alignment between them is compliance.**

---

## Ch 7. Where the design isn't honest yet

Honestly listing unsolved problems is a sign of design maturity.

### 1. Always-spawn, no lazy spawn

**Symptom**: `agent.py` spawns both MCP servers on every invocation, even for config-only queries that don't need RAG.

**Impact**: chromadb + sentence-transformers cold start (5-10s) is pure waste in those cases.

**Production fix**: Put an intent classifier in front of agent.py to decide whether to spawn rag_server. `StdioServerParameters` already supports per-server spawn — the current code just doesn't use it.

### 2. Tool-list discovery isn't cached

**Symptom**: Even when no tools have changed since startup, the agent calls `list_tools()` every session to fetch descriptions.

**Impact**: For stdio MCP, latency is ms-level — negligible. But future HTTP transport would pay round-trip cost for unchanged lists.

**Production fix**: Client-side schema cache; server-side versioned schemas (refetch only on version bump). Future MCP spec might add this.

### 3. No manifest-drift monitoring

**Symptom**: `data/manifests/` is a point-in-time snapshot. When NVIDIA ships a new JetPack, the manifest URL doesn't change but the contents do; tools silently drift.

**Impact**: User asks "latest version" and gets a stale answer; validation gives false positives/negatives.

**Production fix**: Scheduled re-ingestion + diff; smoke eval on every change; alert when drift exceeds threshold.

### 4. No per-tool latency / cost telemetry

**Symptom**: Current trace records dispatch order and args; not per-tool elapsed time, token usage, or failure rate.

**Impact**: No data on which tool slows down a session; no data on which tool the LLM misuses most; optimizations are blind.

**Production fix**: FastMCP middleware with timing decorator; write JSONL to `~/.sdk-advisor/runs/<run-id>.jsonl`; build a dashboard.

### Out of scope

README §"What's still missing" lists 7 broader product-level gaps (privacy, citation persistence, failure feedback loop, ...). This chapter only covers MCP-layer dishonesty; doesn't duplicate those.

### Chapter takeaway

**Each of the 4 is known and fixable but unfixed — their ROI in demo phase didn't justify the work. Each becomes mandatory at production.**

---

## Appendix

### A1. Quick-reference table

| # | Tool | Server | Phase | Required? | Data deps | Code |
|---|---|---|---|---|---|---|
| 1 | detect_connected_hardware | A | 0 | ✓ | — | [knowledge_server.py:60](../src/knowledge_server.py#L60) |
| 2 | lookup_target_id | A | 1 | ✓ | board name (user) | [knowledge_server.py:53](../src/knowledge_server.py#L53) |
| 3 | list_products | A | 2 | — | — | [knowledge_server.py:24](../src/knowledge_server.py#L24) |
| 4 | list_releases | A | 2 | ✓ | product | [knowledge_server.py:30](../src/knowledge_server.py#L30) |
| 5 | get_release | A | 2 | — | product, version | [knowledge_server.py:40](../src/knowledge_server.py#L40) |
| 6 | list_hardware | A | 2 | — | family | [knowledge_server.py:47](../src/knowledge_server.py#L47) |
| 7 | validate_combo | A | 3 | conditional | product, version, target, sdks | [knowledge_server.py:122](../src/knowledge_server.py#L122) |
| 8 | estimate_resources | A | 3 | conditional | product, version, target | [knowledge_server.py:95](../src/knowledge_server.py#L95) |
| 9 | check_constraints | A | 3 | conditional | budgets + estimate output | [knowledge_server.py:108](../src/knowledge_server.py#L108) |
| 10 | search_3p_sample_repos | B | 4 | conditional | query | [rag_server.py:86](../src/rag_server.py#L86) |
| 11 | lookup_container_reqs | B | 4 | conditional | container_id | [rag_server.py:49](../src/rag_server.py#L49) |
| 12 | generate_response_file | A | 5 | ✓ | full config | [knowledge_server.py:130](../src/knowledge_server.py#L130) |
| 13 | validate_against_official_sample | A | 5 | — | generated INI | [knowledge_server.py:143](../src/knowledge_server.py#L143) |
| 14 | generate_command | A | 5 | ✓ | full config | [knowledge_server.py:149](../src/knowledge_server.py#L149) |
| 15 | parse_install_log | A | 6 | exception | log archive path | [knowledge_server.py:162](../src/knowledge_server.py#L162) |

### A2. Glossary

| Term | Meaning |
|---|---|
| **Deterministic tool** | Same input → same output. 13 of 15 tools here. |
| **Semantic tool** | Involves vector search or LLM inference; results are probabilistic. Only `search_3p_sample_repos`. |
| **Hub node** | DAG node with abnormally high out-degree (here: `lookup_target_id`, `list_releases`). |
| **Routing contract** | The dispatch order rules SYSTEM_PROMPT gives the agent. |
| **Trace** | Ordered record of all tool_use blocks in one agent session. |
| **Spec compliance** | The degree to which a trace satisfies the routing contract. |
| **Always-spawn** | Client unconditionally spawns all servers at startup (vs lazy-spawn). Current implementation. |
| **Hub coupling** | Multiple tools share the same hub node as input. This repo eliminates redundant parsing by making hubs standalone tools. |

### A3. External references

- [Model Context Protocol Spec](https://modelcontextprotocol.io)
- [FastMCP documentation](https://gofastmcp.com)
- [Anthropic Tool Use docs](https://docs.anthropic.com/en/docs/build-with-claude/tool-use)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [README §MCP design](../README.md#mcp-design) — entry point to this manual

---

**End of manual.** One user query triggers 5-7 tools, the trace is auditable, two servers, mandatory path + 4 conditional branches — that's the whole MCP-layer design of this repo.
