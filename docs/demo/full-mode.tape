# NVIDIA SDK Advisor — end-to-end demo (--full --mock-install).
#
# Generates docs/demo/full-mode.gif. Records the five-phase orchestration:
# configure → install (mocked) → troubleshoot → apply fix (simulated) → retry.
#
# Requires: vhs (https://github.com/charmbracelet/vhs)
#   winget install charmbracelet.vhs
#
# Run from the project root:
#   vhs docs/demo/full-mode.tape
#
# Env vars expected: ANTHROPIC_API_KEY (or ANTHROPIC_BACKEND=cli for subscription).

Output docs/demo/full-mode.gif

# Default shell is bash. Run this from WSL or Linux/macOS — VHS uses ttyd
# internally and the ttyd + pwsh combo is unstable on native Windows
# (process hangs). See docs/demo/README.md for the recording recipe.
Set FontSize 14
Set Width 1280
Set Height 760
Set Padding 18
Set Theme "Dracula"
Set TypingSpeed 22ms
Set PlaybackSpeed 1.0

# Activate the project venv so `python` resolves to the right interpreter.
# Hidden from the recording with a Hide/Show block.
Hide
# Use the project venv. On the WSL/Linux box we record from, the venv
# lives at ~/sdk-advisor-venv (the /mnt/c .venv is a Windows-side venv
# with Scripts/python.exe and doesn't have bin/python). Adjust the
# PATH export below if your venv lives elsewhere.
Type `export PATH=$HOME/sdk-advisor-venv/bin:$PATH`
Enter
Sleep 400ms
# Suppress the large ASCII-art startup banner that FastMCP prints when
# each MCP server boots — at 1280x760 it dominates the first ~15s of the
# recording. Banners are pretty for humans, but here they crowd out the
# actual demo content.
Type `export FASTMCP_SHOW_SERVER_BANNER=false`
Enter
Sleep 400ms
# Demo-only: hold the web_search trace on screen for ~3s before Phase 4 panel
# scrolls it up. Read by src/troubleshoot.py — no-op in normal CLI use.
Type `export SDK_ADVISOR_DEMO_PAUSE=3`
Enter
Sleep 400ms
# Demo-only: pause briefly after each tool call / reasoning line in Phase 1
# so the agent's step-by-step progression is readable instead of flashing
# by. Read by src/orchestrator.py — no-op in normal CLI use.
Type `export SDK_ADVISOR_STEP_PAUSE=0.6`
Enter
Sleep 400ms
Type `clear`
Enter
Sleep 400ms
Show

# Run the demo. No leading comment — the README captions the GIF, and the
# em-dash/arrow chars in earlier versions confused VHS's parser.
Type `python main.py --full --mock-install --query "I have an Orin NX 16GB and want to do edge LLM inference"`
Sleep 500ms
Enter

# Five phases run autonomously. Web_search + Claude calls dominate latency.
# Budget breakdown (approximate, ANTHROPIC_BACKEND=sdk + Haiku 4.5):
#   Phase 1 configure  ~25-40s  (agent + 5-8 MCP tool calls + .ini synthesis)
#   Phase 2 install    ~2s     (mock log stream)
#   Phase 3 troubleshoot ~30-45s (agent + 2-4 web_search calls + synthesis)
#   SDK_ADVISOR_DEMO_PAUSE  3s
#   Phase 4 + 5         ~5s
#   Final success panel ~2s
# Phase 1 ~25-40s + per-step pauses ~7s = ~35-50s
# Phase 2 ~2s
# Phase 3 ~30-45s + streamed troubleshoot trace pauses ~5s = ~35-50s
# Phase 4/5 + final Panel ~10s
# Total ~85-115s + buffer. 175s gives safe headroom.
Sleep 175s

# Settle frame so the success panel sticks at the end of the GIF.
Sleep 2s
