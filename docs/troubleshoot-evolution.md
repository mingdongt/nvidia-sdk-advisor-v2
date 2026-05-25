# Troubleshoot evolution

`--troubleshoot` is the deepest verb in the demo — the only one a static wizard cannot replicate. The current implementation occupies one point in a 3-axis design space; each axis has concrete next steps that don't require architecture changes.

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

The `--full --mock-install` orchestrator (commit `8be775c`) demonstrates how an end-to-end chain across these axes would compose — configure → install → troubleshoot → fix → retry, with canned subprocess stand-ins for the still-future Execute and Verify cells. The orchestration layer itself is built; only the bits behind the MOCKED tags are not.

## Input axis — how the agent engages

| Mode | Status | Trigger | Cost |
|---|---|---|---|
| **Passive** | ✓ shipped | `python main.py --troubleshoot <log>` | — |
| **Semi-active** | ✓ partial | `--execute` exits non-zero → offer troubleshoot on latest export log | wired in `src/execution.py` |
| **Daemon** | future | `watchdog` over `~/.nvsdkm-logs/<session>/`; notify when a session ends with "Install aborted" | ~3 hrs |
| **Multimodal** | future | Paste a screenshot of the failing SDK Manager GUI; Claude vision reads the dialog | ~1 day |

## Output axis — how far the agent goes

| Mode | Status | What it does | Cost |
|---|---|---|---|
| **Generate** | ✓ shipped | Writes `fix.sh` + `diagnosis.md` to `output/`; user runs the script themselves | — |
| **Execute** | orchestration shipped, real path future | The chain that would invoke Execute ships as `--full` (currently with `--mock-install` stand-in). Real path needs per-command risk gating: low-risk lines auto-run, sudo / destructive lines require explicit confirm | ~3 hrs once a Jetson is on hand |
| **Escalate** | future | When `web_search` returns nothing usable, drafts a NVIDIA-forum-format post with PII-scrubbed log excerpts, prefilled hardware + version fields, and a "what I've tried" hypothesis. Saves to `output/forum_draft.md` or opens the forum's new-topic URL with query params pre-populated | ~2 hrs |
| **Verify** | orchestration shipped, real check future | `--full` reserves a verify phase but currently mocks success. Real check would run `nvidia-smi`, `apt list nvidia-jetpack`, lsmod sanity — confirms the system is actually healthy, not just that installer exit was 0 | ~1 hr |

## Trust axis — how much control the user keeps

| Mode | Status | Description |
|---|---|---|
| **Full review** | ✓ shipped | User reads `fix.sh`, runs `bash fix.sh` themselves |
| **Per-action confirm** | future | Agent runs each command, pauses after any sudo / destructive line for explicit Y |
| **YOLO** | not planned | Auto-run everything. Deliberately avoided — the risk surface of LLM-generated `sudo` commands is too high without a feedback loop telling us when fixes silently broke something |

## Why this shape

Three principles drove the current anchor point:

1. **Safety > polish.** Every move along the trust axis trades user agency for convenience. For a tool that generates `sudo` commands, that's the wrong trade-off without observability the demo doesn't have.
2. **Honest failure beats confident hallucination.** At the right end of the output axis, *escalate* (drafting a forum post) is more useful than producing a low-confidence `fix.sh`. "Cannot diagnose" is a legitimate output, not a degraded one. The forum-post mode makes the agent valuable even when it doesn't know — by transferring the question to humans who do, with a properly-formatted starting point.
3. **Composable extensions, not rewrites.** Every cell in these tables is reachable from the current architecture without restructuring. The agent doesn't need to know which mode it's running in; `src/troubleshoot.py` is the only file that changes for any of these extensions.

The most under-served quadrant today is **(passive, escalate, full-review)** — when the agent can't fix the issue but *could* still hand the user a high-quality forum post. That's what I want to build next.
