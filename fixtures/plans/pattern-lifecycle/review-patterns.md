# review-patterns

The crown jewel of the suite and the source of the **findings artifact contract** the other
two units consume. It audits a codebase against a set of patterns using a tiered
micro→macro fan-out, and emits findings a human can read and a downstream skill can task
out.

Runtime location: `~/.claude/skills/review-patterns/`. User-invocable.

## reads → runs-as → emits

- **reads**: a **pattern spec** (the conventions to audit against) — either supplied by the
  user, or produced by the discover→confirm dialogue below; plus a target scope (repo,
  package, or path set).
- **runs-as**: a layered fan-out of sub-agents, model-tiered, from single-file reading up to
  whole-codebase synthesis.
- **emits**: findings doc(s) in `backlog/drafts/` (or a caller-named location), optionally
  rendered with `cdoc` diagrams. Every load-bearing claim cited to source.

## Two entry modes

The skill's front-end branches on whether it was handed a pattern spec.

**Mode A — spec provided.** The user names the patterns to audit (e.g. "module names must
accurately describe their contents"; "no stage imports from a later stage"). Go straight to
the fan-out.

**Mode B — discover then confirm.** No spec given. The skill runs a lightweight discovery
pass (sample the tree, the CLAUDE.md, the folder skeleton), proposes a **candidate pattern
set** with a one-line rationale each, and **holds for the user to confirm/edit** before any
expensive fan-out. This is the interactive gate — never burn a full fan-out on unconfirmed
patterns. The confirmed set becomes the spec and Mode A resumes.

## The fan-out methodology (reference: `references/fan-out.md`)

The reusable core, generalised from the ariadne run. Layered so each layer's context is
bounded and the judgement concentrates at the top:

1. **Micro (fan-out, cheap tier)** — one agent per file (or small cluster), reads the file
   and reports what it contains against the pattern spec. Wide, shallow, parallel.
2. **Meso (synthesis, mid tier)** — one agent per functional area aggregates the micro
   reports into area-level findings and begins the information-architecture picture.
3. **Macro (final synthesis, top tier)** — a small number of agents evaluate the full
   cross-section: which findings are systemic, how areas relate, what the highest-leverage
   fixes are, and which recommendations conflict (and the ruling).

**Model tiering** maps to the layers — a broad fleet at the micro layer, a workhorse tier
at meso, and a best-judgement tier for the macro synthesis. Whether the tier assignment is
baked into the skill or left to the caller is an open question (see README); default is to
bake a sensible mapping and let an argument override it.

**Verification discipline** — carried from ariadne: every load-bearing claim in the output
is verified against source before it ships. A finding the fan-out cannot cite is dropped or
demoted, not asserted.

## Pattern-quality heuristics (reference: `references/heuristics.md`)

Generic lenses the audit applies, independent of the specific pattern spec — these are the
transferable judgement the ariadne prompt encoded:

- **Name-accuracy test** — does each module/folder have a name that accurately describes all
  it contains? A name that must resort to being generic is a split signal, even if the split
  yields tiny leaf modules (that failure mode is preferred over abstract names).
- **Routing test** — could a reader, given only the file/folder names, route to the correct
  location to fix a given class of issue? If not, the structure under-expresses its function.
- **Extensibility probe** — how hard is it to add the next instance of a varying axis (e.g. a
  new language, a new backend)? Friction here names a missing seam.

These ship as a reference so the skill applies them by default and the user can extend the
lens set per invocation.

## Findings artifact contract (pinned here — consumers depend on it)

The output shape the downstream units read. Decide one-doc vs two-doc (README open
question); the _shape_ below is what `plan-backlog` and `enforce-patterns` require:

- A stable location the caller is told (default `backlog/drafts/<prefix>.*`).
- Findings grouped by **area** (the fixable unit), each with: what the pattern violation is,
  where (cited paths), severity/leverage, and a suggested direction.
- A closing **cross-area synthesis** — systemic themes, the highest-leverage fixes, and any
  rulings on conflicting recommendations.
- Each area's findings are self-contained enough that `plan-backlog` can turn one area into
  one sub-task without re-reading the whole codebase.

## Scope boundary — what it does NOT do

- Does **not** create tasks (that is `plan-backlog`).
- Does **not** design or write enforcement mechanisms (that is `enforce-patterns`).
- Does **not** apply fixes — it audits and reports only.
- Holds **no** dependency on the user's customisation surface; fully portable.

## Open questions

- One findings doc or the ariadne two-doc split (analysis + program)? Pin before consumers
  are built.
- `cdoc` rendering: always, or opt-in? Default opt-in (it is a separate user-triggered
  skill and adds cost).
- How does the discovery pass (Mode B) bound its cost on a large repo — sample budget, or
  depth cap? Draft a default in `references/fan-out.md`.
