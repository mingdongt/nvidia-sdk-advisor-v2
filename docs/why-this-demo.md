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
- **Active feedback loop.** Log `--troubleshoot` runs that the user marks as "didn't fix it" → grow the log_patterns dictionary semi-automatically.

## In one line

*The same agent NVIDIA is building everywhere else, applied to the wizard their docs show users routinely bouncing off of.*
