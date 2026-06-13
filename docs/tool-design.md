# Tool design for an LLM caller

> [!NOTE]
> Design notes for the seven manifest tools in
> [`manifest_tools.py`](../libs/code/deepagents_code/manifest_tools.py). The
> running examples are concrete, but the principles are meant to be reusable
> whenever you add a tool to this agent.

A normal API is called by a deterministic program or a human. An **agent tool is
called by a probabilistic reasoner** — the model reads your tool's name and
description, decides whether to call it, fills in the arguments, reads the
result, and decides what to do next. The tool's docstring is not documentation
that sits on a shelf; it is the *prompt* the model routes on, and every parameter
you require is a place the model can guess wrong.

The seven manifest tools are deliberately small, read-only, and orthogonal.
Here is why, in principles.

---

## 1. The toolset is a flat menu; the model is the router

There is no orchestration graph. `MANIFEST_TOOLS` is a flat list
([`manifest_tools.py`](../libs/code/deepagents_code/manifest_tools.py)) that is
`tools.extend()`'d into one flat tool set at both entry points
([`main.py`](../libs/code/deepagents_code/main.py),
[`server_graph.py`](../libs/code/deepagents_code/server_graph.py)), gated only by
whether a `manifest.db` is present. There are no per-tool nodes, edges, or state
machine — the model chooses the next tool purely by reading names and
descriptions.

So **names and the first line of each docstring are the routing table.** Encode
the role and the next step directly into them: `find_releases` opens with *"Use
this first to ground…"*, `search_components` ends with *"then call
component_detail / footprint / build_plan,"* and the module docstring spells out
the canonical order. Treat the first sentence of a docstring as a routing cue,
not a summary. This is why the rest of this doc obsesses over docstrings — and
why "wire it in code" below means a composite **tool**, not a graph edge.

## 2. Every required argument is a guess point

Minimize what the model must *derive or carry* from a previous result. The risky
parameters aren't the ones the user states outright (`product=Jetson`) but the
ones threaded between calls.

Concrete seam:
[`resolve_deps`](../libs/code/deepagents_code/manifest_tools.py) returns its
closure under the key **`required`**, but `footprint` and `build_plan` accept the
same list under the name **`comp_ids`**. Nothing in code moves that value — the
model does, and each name-mismatched seam is a failure point. When a path is both
high-frequency and mechanical, collapse those hops into a composite "recipe" tool
that wires them in code (see [*When to add a composite*](#when-to-add-a-composite-tool)).

## 3. Separate ambient context from query parameters

`host_os`, `arch`, and `board` are **ambient** — they describe the user's machine
and don't change across a conversation. `query`, `release_id`, `comp_ids` are the
actual question. The manifest tools treat host facts as
["always-on filters"](../libs/code/deepagents_code/manifest_tools.py), but that
puts a burden on the caller: it must remember to pass them on *every* call, and
pass them *consistently* — the same `host_os` to `footprint` and to `build_plan`,
or a plan will size one machine and download for another.

## 4. Read-only knowledge and gated action are different axes

All seven tools are read-only: they describe what an install *would* do, never
download or flash. Because they have no side effects, they are not gated behind
human approval — the model can call, retry, and explore them cheaply. The real
install runs through the harness's approval-gated shell. Keep "planning"
cheap-and-free while "doing" stays costly-and-gated, and don't blur the line in a
new tool.

## 5. Progressive disclosure: broad-and-thin before deep-and-narrow

Pair a wide, thin lister with a narrow, deep detailer. `list_components` returns
one summary line per component (id, name, version, section, install side);
`component_detail` returns the full record — platforms, dependencies, license —
for a single id; `search_components` returns candidate cards the model then
drills into. The model surveys cheaply and pulls detail only where it commits,
which keeps the working context small.

## 6. Match altitude to intent, not to the schema

The seven tools sit at **schema altitude** — roughly one tool per query shape.
That is the right primitive layer. But users speak at **intent altitude** ("set
me up for object detection"). A healthy toolset has both: orthogonal primitives
for the long tail, and a few intent-level recipes for the frequent paths. The
recipes are a convenience layer on top — never a replacement (see below).

## 7. One fail-soft error contract

Every tool returns a JSON-serializable `dict` and degrades to `{"error": ...}`
rather than raising, so the caller always gets a predictable shape and can react
to failure in-band instead of an exception tearing through the turn. The *success*
shape is not perfectly uniform — some tools nest results under a key
(`{"releases": …}`, `{"matches": …}`), others return the record fields directly —
but the **error** shape is, so the model can always branch on a single `error`
key.

## 8. Shape outputs for the next decision, not for the database

Anti-example: [`build_plan`](../libs/code/deepagents_code/manifest_tools.py)
returns download URLs as **relative paths** (`./….deb`); the absolute URL needs
the release's `comp_repo_url` prepended, so the model can't hand the result to the
user as-is. A tool's return value is consumed by the model's *next* action, so
make it ready to act on: absolute URLs, totals already summed, a one-line human
summary alongside the structured fields. Every bit of post-processing you leave
in the output is reasoning the model has to redo.

## 9. Keep import cost off the startup path

Tool modules load when the agent boots, but their heavy or optional dependencies
shouldn't. Each manifest tool defers its `manifest_db` / `manifest_vector` import
into the function body
([`manifest_tools.py`](../libs/code/deepagents_code/manifest_tools.py)), so
registering the seven tools costs nothing until one is actually called — and the
optional vector backend can fail to import and fall back to substring search
without breaking startup.

## 10. Fewer, orthogonal tools

Every tool you add enlarges the menu the model must choose from and spends prompt
budget on its description. Keep primitives **orthogonal** (each does exactly one
thing) so they compose cleanly, and add recipes **sparingly** — ten composite
tools is just a different kind of mess. Apply YAGNI to the toolset itself.

## 11. Orchestration stability is not data correctness

Collapsing a mechanical path into a composite makes the *wiring* reliable — one
model decision instead of three, seams moved into tested code — but the composite
computes nothing itself. Put correctness one layer down, in the helpers. The
per-board row selection that stops `footprint` from double-counting lives in
`_pick_board_row` inside
[`manifest_db.py`](../libs/code/deepagents_code/manifest_db.py), so every tool —
and any future composite — inherits it for free, *including* its residual choice
(given no `board`, it deterministically over-estimates toward the larger variant
rather than duplicating). "The model chained the steps" and "the numbers are
right" are two separate guarantees; verify both.

---

## When to add a composite tool

A path earns a recipe only when it is **both**:

| Property | Why it matters |
| --- | --- |
| **High-frequency** | The ROI of a shortcut comes from how often the path is walked. |
| **Mechanical / deterministic** | Stability comes from removing model judgment. Packaging a *fuzzy* path (e.g. intent → component selection) doesn't make it stable — it just hides the ambiguity inside the tool. |

The clearest candidate here is the deterministic tail
`resolve_deps → footprint → build_plan`: nearly every session ends with "what do
I install, how big is it, what do I download," and those steps are pure SQL with
no judgment. High-frequency × mechanical = high ROI × genuinely more stable.

And the rule that keeps the toolset honest: **a recipe is a convenience layer
over the primitives, never a replacement.** The long tail enters from the middle
("does TensorRT depend on cuDNN?"), needs the atomic tools, and must keep working.
A composite that can't be expanded back into its primitives is a trap, not a
shortcut.

---

*See also: [`manifest_tools.py`](../libs/code/deepagents_code/manifest_tools.py)
(the tool layer), [`manifest_db.py`](../libs/code/deepagents_code/manifest_db.py)
(the deterministic SQLite store the tools read), and the project
[README](../README.md) for the user-facing tour of the seven tools.*
