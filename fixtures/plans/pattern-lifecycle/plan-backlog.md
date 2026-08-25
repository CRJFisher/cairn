# plan-backlog

The shared consumer. It turns a body of findings into a **dependency-ordered backlog epic**
— an epic task plus sub-tasks, sequenced into waves by their implicit dependencies and
natural implementation order.

It is the most **generic** of the three: nothing about it is pattern-specific. Any analysis
or findings doc is a valid input. It lives in this suite because the pattern chain is its
driving need, but its name and description should not tie it to patterns.

Runtime location: `~/.claude/skills/plan-backlog/`. User-invocable.

## reads → runs-as → emits

- **reads**: a findings/analysis doc (the [review-patterns](review-patterns.md) contract, or
  any structured analysis) — grouped into fixable areas.
- **runs-as**: decomposition + dependency analysis. One sub-task per area; infer the
  dependency edges between sub-tasks; compute an execution order and group into waves.
- **emits**: an epic + ordered sub-tasks to the task **sink** (see portability), each
  sub-task carrying its scope, its dependency edges, and its wave.

## Two callers

This is why it is its own unit, not a phase inlined twice:

- **After a review** — the remediation program (ariadne `362.1–.8`).
- **After enforce-design** — the enforcement/encouragement mechanisms as their own sub-tasks
  (ariadne `362.9–.15`), sequenced _against_ the remediation tasks (warn-only until the
  matching refactor lands — the no-grandfathering coupling).

It must therefore handle **appending** to an existing epic, not only creating one — the
enforce pass adds sub-tasks to the epic the review pass created.

## Dependency ordering (the reusable capability)

The transferable value beyond plain task creation:

- Infer edges from the findings — shared files, "X must move before Y imports it", barrels
  written once against final names, etc.
- Emit an explicit **dependency graph** and **waves** (independent starting points first;
  each later wave gated on the prior). The ariadne epic's `362.x ──► 362.y` graph and
  "Waves (1)…(2)…" block are the target shape.
- Respect the **no-shims constitution** in the task bodies it writes: land each unit whole
  (update all callers, no transitional aliases, `git mv` for renames, colocated tests move
  with their code).

## Portability — the task sink degrades

- **Backlog MCP present** (`backlog init` done) → create the epic + sub-tasks as real
  backlog tasks with the dependency metadata.
- **No MCP** → emit a plain-markdown epic doc (a task tree with the dependency graph and
  waves inline), so the skill is useful in any repo.

No dependency on the user's _enforcement_ surface — only on the task sink, which it detects.

## Scope boundary — what it does NOT do

- Does **not** audit or discover findings (that is `review-patterns`).
- Does **not** decide enforcement mechanisms (that is `enforce-patterns`) — but it **does**
  task out the mechanisms `enforce-patterns` designs, when called as its second caller.
- Does **not** execute the tasks it writes.

## Open questions

- Name: `plan-backlog` vs `decompose-tasks` vs `taskify`. Should reflect generality, not
  patterns.
- Does it own the dependency inference, or does it accept an already-computed graph from the
  caller when one exists (the review's macro synthesis may already imply the edges)? Default:
  own it, but consume a supplied graph if present.
- How much of the ariadne epic's prose density (per-sub-task rationale, effort/risk labels,
  small-item accounting table) is core vs over-fit? Draft a minimal task template and grow it
  only on observed need.
