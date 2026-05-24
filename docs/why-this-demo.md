# Why this demo

Context notes from building NVIDIA SDK Advisor. Not linked from the README — for anyone who wandered into `docs/` and wants the reasoning behind the project.

## The gap that motivated this

NVIDIA has shipped agentic upgrades to nearly every developer tool in the past 18 months: NeMo Agent Toolkit, AI-Q Blueprints, Nsight Copilot, Vera CPU. SDK Manager is the conspicuous exception — its developer-blog tag has zero posts since July 2023, and v2.4.0 release notes mention no AI features.

This is interesting because SDK Manager is where new developers **first** touch NVIDIA's platform. The wizard's install funnel is the company's onboarding surface, and the [forums.developer.nvidia.com](https://forums.developer.nvidia.com) Jetson tag is full of users bouncing off it.

The unfilled white space: SDK Manager's wizard cannot translate problem → product. Users have to already know which NVIDIA-branded SDK fits their use case. The forum is full of "I have X hardware and want to do Y — what do I install?" threads.

## What this demo is and isn't

**Is:** a CLI agent that fills the four AI capabilities the wizard lacks — discover, configure, install, troubleshoot — built on the same data sources SDK Manager itself reads (the public CDN at `developer.download.nvidia.com/sdkmanager/sdkm-config/`), producing output matching NVIDIA's own `.ini` template format, and treating `NvSDKManager.exe` as a subprocess target.

**Isn't:** a replacement for `sdkmanager`. The agent positions itself as *everything before the wizard fires* — it does not compete with `sdkmanager`, it feeds it. The `.ini` output is exactly the format `NvSDKManager.exe --cli --response-file` consumes.

## Why this architecture, not just prompt engineering

The ablation in the README (running smoke eval against Opus 4.7 with no tools, vs. Haiku 4.5 + our tool layer) shows raw model capability scores 46.7% on factual NVIDIA SDK questions — the misses are hallucinations like inventing `JETSON_XAVIER_TARGETS` (real ID is `JETSON_AGX_XAVIER_TARGETS`). With deterministic tools over real CDN manifests, both Haiku and Opus score 100%.

The model's job is reasoning and synthesis. The tools' job is grounding it in facts that exist. RAG over forum threads adds the "what experts actually recommend" layer on top.

## What I'd build next if this went further

- **Web UI on the same MCP backend.** SDK Manager itself is Electron + Vue 3 + Chromium 91. The same MCP servers (`nvidia-knowledge`, `nvidia-corpus-rag`) could plug into their existing renderer as a chat panel — no change to the agent or backend, just a new front-end view.
- **Live `compRepoURL` consumption.** Level-3 component manifests are auth-gated; the agent's resource estimates would become exact, not approximate, if NVIDIA exposed read-only access to a registered developer.
- **Cross-product reasoning.** Right now Jetson is deep, others shallow. Same data pipeline extends to DRIVE / Holoscan / DOCA without re-architecture.
- **Reliable log parser.** Today's `parse_install_log` is surface-level (open zip, take tail, hand to agent) because we don't have the SDK Manager log-producer source. Internal access unlocks a real structured parser — see below.

## If I joined the team: which internal knowledge bases I'd want, ranked

The biggest gap between this PoC and a production-grade NVIDIA SDK Advisor isn't model quality or architecture — it's **data access**. From outside, we get public CDN manifests, public forum threads, public docs, and standard Linux tool knowledge. That's roughly 70-80% of what's needed for a credible troubleshoot. The remaining 20-30% lives in NVIDIA-internal sources I'd request on day one:

| Priority | Source | Why it matters |
|---|---|---|
| 1 | **Support ticket database** | Enterprise customer tickets where an NVIDIA engineer marked a fix as "verified working". Upgrades agent's authority from "forum-claimed" to "NVIDIA-validated". |
| 2 | **SDK Manager source + log-producer code** | Errors come from specific code paths (Electron main / renderer / PowerShell query scripts / bash flash scripts). Insider access lets you classify errors by code site instead of regex-matching free text. Unlocks the real log parser. |
| 3 | **Bug tracker (Jira/Bugzilla)** | Known issues, workarounds, predicted fix versions. When a user hits a bug already filed, the agent can surface ETA and workaround instead of synthesizing from scratch. |
| 4 | **Telemetry / GA4 export** | Anonymized failure logs and aggregated metrics across millions of sessions. Tells you which errors are real distribution problems vs one-off oddities. Drives proactive notification when a regression is in flight. |
| 5 | **Internal error code dictionary** | Real logs contain `error code is: 2001`, `Task 0x0 failed (err: 0x1f1e050d)`, `command error code: 11` — all NVIDIA-internal numeric/hex codes. Their semantics live in the source. |
| 6 | **Cross-team SME knowledge** | Internal wikis from CUDA / TensorRT / DeepStream / Isaac / Container teams. Cross-product dependency failures (e.g., "DeepStream X on JetPack Y in Docker container Z") are hard to triage from public data alone. |
| 7 | **NDA partner forums** | DRIVE OEMs, medical imaging customers, enterprise robotics partners — their support discussions happen in NDA-only sections of developer.nvidia.com. |
| 8 | **Manufacturing quality data** | Board-batch-level known issues, USB controller compatibility matrices, firmware regressions. Lets the agent diagnose hardware-level issues that look like software bugs from the outside. |
| 9 | **Release notes drafts + RC test reports** | Pre-publication known-issue lists. Lets the agent answer "what will break when I upgrade to X.Y.Z" before X.Y.Z ships. |
| 10 | **CI/CD build logs** | SDK Manager's own continuous integration produces installation logs every commit. Internal version of our test corpus, dramatically larger and dramatically cleaner. |

The architectural takeaway: production deployment is not a rewrite. The MCP boundary localizes change. Specifically:

- Replace `parse_install_log` with a source-aware parser → upgrades source 2 + 5
- Add a new MCP tool `lookup_known_bug(error_signature)` against bug tracker → upgrades source 3
- Add `query_support_kb(error_signature)` against the ticket DB → upgrades source 1
- Server B's `search_forum_threads` already works against the public forum — point it at the internal Discourse cluster → upgrades source 7

The agent loop, prompt template, web_search integration, and output writer all stay untouched. *The part that needs insider knowledge is exactly the part the MCP boundary makes replaceable.*

## In one line

*The same agent NVIDIA is building everywhere else, applied to the wizard their docs show users routinely bouncing off of.*
