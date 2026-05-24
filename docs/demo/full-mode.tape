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
Set FontSize 13
Set Width 1280
Set Height 760
Set Padding 18
Set Theme "Dracula"
Set TypingSpeed 35ms
Set PlaybackSpeed 1.0

# Activate the project venv so `python` resolves to the right interpreter.
# Hidden from the recording with a Hide/Show block.
Hide
Type `source .venv/bin/activate`
Enter
Sleep 1200ms
Type `clear`
Enter
Sleep 400ms
Show

# Intro: show the command we're about to run.
Type@40ms `# NVIDIA SDK Advisor — end-to-end (configure → install → troubleshoot → fix → retry)`
Enter
Sleep 800ms
Type@40ms `python main.py --full --mock-install --query "I have an Orin NX 16GB and want to do edge LLM inference"`
Sleep 600ms
Enter

# Five phases run autonomously. Web_search + Claude calls dominate latency,
# so we wait long enough for the final success panel to render. The wait
# below is a hard ceiling; VHS exits as soon as the prompt returns.
Sleep 180s

# Settle frame so the success panel sticks at the end of the GIF.
Sleep 2s
