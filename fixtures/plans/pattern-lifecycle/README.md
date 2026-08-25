# pattern-lifecycle

A plan for **three loosely-coupled, user-invocable skills** that generalise a chain first
run by hand against the `ariadne` repo (three sessions: review → convert-to-tasks →
enforce/encourage). The chain abstracts to the lifecycle of a **pattern** — a convention
you want a codebase to hold to:

```
discover ─► audit ─► remediate ─► institutionalise
           review   plan-backlog   enforce-patterns
         patterns
```

The unifying noun is the pattern; the three verbs are genuinely different jobs with
different inputs, outputs, and moments-you-want-them, so they are **three skills, not one**.

## Why three, not one

A single `review-patterns` skill covering all three would need a generic name to describe
its contents — the exact failure mode the ariadne review itself hunted (a module that must
resort to a generic name needs splitting). Each of the three below carries an accurate
single name and is independently useful:

- You often want **just a review** (an IA audit) with no intent to task it out.
- You often want **just task decomposition** of an existing analysis doc — nothing to do
  with patterns.
- You sometimes want **just enforcement** of a pattern you already know you want, with no
  prior review.

And `plan-backlog` has **two callers** — the review emits remediation tasks, the
enforce step emits enforcement tasks (in ariadne: sub-tasks `362.1–.8` vs `362.9–.15`).
A shared consumer with two callers is a DRY unit, not a phase inlined twice.

This is a **runtime skill-stack** in the sense of
[../skill-composition/runtime-composition.md](../skill-composition/runtime-composition.md):
each skill is invocable alone; the outer chain is assembled by the user (or by one skill
handing off to the next), not fixed at authoring time.

## The three units

| Unit               | Doc                                        | One-line                                                                               |
| ------------------ | ------------------------------------------ | -------------------------------------------------------------------------------------- |
| `review-patterns`  | [review-patterns.md](review-patterns.md)   | Discover/confirm patterns, then run a tiered micro→macro fan-out audit; emit findings. |
| `plan-backlog`     | [plan-backlog.md](plan-backlog.md)         | Decompose a findings/analysis doc into a dependency-ordered backlog epic.              |
| `enforce-patterns` | [enforce-patterns.md](enforce-patterns.md) | Map each pattern to the cheapest reliable mechanism on two axes; emit the config.      |

## What already exists (and its fate)

- **`enforce-pattern` command** (`commands/enforce-pattern.md`) — a generic
  mechanism-picker with a solid decision matrix and a Claude-Code-docs refresh step.
  `enforce-patterns` **supersedes and absorbs it**: it inherits the matrix and the docs
  refresh, and adds the enforce/encourage philosophy and the user-level/repo-level axis
  that the command lacks. The command is removed when the skill lands.
- **`plan-and-spinoff`** — part of the retired `spinoff` plugin, no longer used. Nothing
  to preserve; `plan-backlog` is a clean re-build, not a port.
- **`deep-research`** — a web-oriented fan-out-and-verify harness. `review-patterns`
  shares its _shape_ (fan-out → synthesise) but not its substrate (codebase, not web).
  Kinship noted; no dependency.

## Composition and boundaries

Coupling is by **handoff, not import** (cross-skill relative import is unsupported in
Claude Code; a skill that must call another does so by absolute path — avoided here where
possible):

- `review-patterns` ends by naming its findings artifact and suggesting the next two steps.
- `plan-backlog` and `enforce-patterns` each accept a findings doc path as input and stand
  alone; neither requires the review to have run inside the same session.
- Nothing shared is promoted to an owned interface. Per the
  [skill-composition](../skill-composition/README.md) lesson — _an interface earns its
  existence only when multiple peer implementers exist_ — the fan-out methodology and the
  pattern-quality heuristics live as a **reference doc inside `review-patterns`**, copied
  (not shared by protocol) if a second consumer ever needs them.

## Portability — the user-level vs repo-level axis

The hard call (raised in planning): enforcement can live at **user-level** (your rich
`file-rules/` + `rule-injector.cjs` + Stop-hook surface) or **repo-level** (in-repo
`.claude/`, CLAUDE.md, plain linter/CI — assumes nothing). Public/foreign repos cannot
assume your surface exists.

Resolution: only `enforce-patterns` carries this axis, and it **detects and routes** rather
than assumes — rich artifacts when your surface is present and the repo is private on your
machine; degraded repo-level artifacts otherwise. `review-patterns` and `plan-backlog` are
fully portable and hold no dependency on your customisation surface (`plan-backlog` degrades
only on task _sink_: backlog MCP if present, plain markdown if not).

## Build order

1. **`review-patterns`** first — it is the crown jewel and it defines the **findings
   artifact contract** that the other two consume. Its output shape must be pinned before
   the consumers are built.
2. **`plan-backlog`** — generic, unblocks emitting tasks from any analysis, lowest coupling.
3. **`enforce-patterns`** — most existing material to port (the command), so lowest risk;
   built last so it can target the settled findings contract.

## Open questions (decide before/at build)

- **Names are provisional.** `plan-backlog` vs `decompose-tasks` vs `taskify`;
  `enforce-patterns` (plural) vs reusing `enforce-pattern`. The naming-sensitivity of this
  whole effort argues for settling these deliberately.
- **`enforce-patterns`: skill or keep as command?** The suite-of-skills decision says skill
  (bundled references via progressive disclosure, composes in the stack). Confirm you want
  to convert rather than extend the command in place.
- **Findings artifact contract** — one doc, or the ariadne two-doc split (analysis +
  program)? The consumers only need a stable _shape_; pinned in
  [review-patterns.md](review-patterns.md) once decided.
- **Does `review-patterns` auto-chain** into `plan-backlog`/`enforce-patterns`, or only
  suggest? Default: suggest (loose coupling); auto-chain is opt-in via argument.
- **Model tiering** (sonnet fan-out → opus synthesis → fable final judgement) — bake the
  tier assignment into the skill, or leave to the invoking session? Baking it makes the
  skill reproducible; leaving it flexible respects cost budgets.

## Status

Active plan. No unit is on a build worklist yet — this folder fixes the decomposition,
contracts, and boundaries so the build can start from `review-patterns` when chosen.
