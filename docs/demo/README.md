# Demo recordings

`full-mode.tape` — VHS script that records an end-to-end run of `--full --mock-install`,
covering all five phases (configure → install → troubleshoot → fix → retry).

## Recording recipe (WSL / Linux / macOS)

VHS depends on [ttyd](https://github.com/tsl0922/ttyd), and the ttyd + pwsh
combination hangs on native Windows. Recordings should run from WSL (or a
Linux/macOS host with the repo checked out):

```bash
# 1. Install VHS + ttyd + ffmpeg (one-time)
#    Ubuntu:    apt install ttyd ffmpeg && curl -fsSL https://vhs.charm.sh/install.sh | bash
#    macOS:     brew install vhs ttyd ffmpeg

# 2. From the project root, with the venv created (python -m venv .venv)
#    and deps installed (pip install -r requirements.txt), make sure
#    ANTHROPIC_API_KEY is exported.
export ANTHROPIC_API_KEY=...

# 3. Record
vhs docs/demo/full-mode.tape
```

Output: `docs/demo/full-mode.gif` (referenced by the project README hero block).

## Recording notes

- The tape runs the demo non-interactively via `--query`. The five phases
  take ~1–3 minutes wall-clock depending on Claude API + web_search latency.
  `Sleep 180s` in the tape is a ceiling, not a fixed length — VHS captures only
  until the prompt returns.
- The tape sources `./.venv/bin/activate` (POSIX layout). Adjust if the venv
  lives elsewhere.
- Theme is `Dracula` for high contrast against rich panels. Width 1280 is wide
  enough that the rich `Panel` borders don't wrap mid-line.

## What if VHS isn't available?

Alternative recorders that consume the same flow:

- **asciinema + agg**: `asciinema rec full-mode.cast` then `agg full-mode.cast full-mode.gif`.
- **Windows Game Bar** (Win+G): native screen recorder, .mp4 output (convert to .gif with ffmpeg).
- **Manual screenshots** at each phase boundary — slower but always works.

The point of committing the `.tape` file is that the recording is reproducible:
anyone with VHS installed can regenerate the GIF after a code change without
having to retype the command or guess the right window dimensions.

## Why no GIF yet?

The repo is developed on native Windows; VHS + ttyd hangs there, so the
`.tape` recipe is committed but the rendered `.gif` is not yet generated.
Next render is planned from WSL Ubuntu — see the open GitHub issue / commit
log for status.
