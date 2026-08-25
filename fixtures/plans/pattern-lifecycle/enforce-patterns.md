# enforce-patterns

Institutionalises a pattern: maps it to the **cheapest reliable mechanism** that keeps the
codebase holding to it going forward. Supersedes and absorbs the existing
`commands/enforce-pattern.md`.

Runtime location: `~/.claude/skills/enforce-patterns/`. User-invocable.

## reads → runs-as → emits

- **reads**: a pattern to institutionalise (named directly, or drawn from a
  [review-patterns](review-patterns.md) findings doc), plus the target repo context.
- **runs-as**: a two-axis decision per pattern, preceded by a Claude-Code-docs refresh so
  recommendations track current capabilities.
- **emits**: complete, copy-paste-ready mechanism artifacts (rule files, hook configs +
  scripts, CLAUDE.md edits, settings), either applied or handed to `plan-backlog` as
  sub-tasks.

## Inherited from the `enforce-pattern` command

Ported forward, not rebuilt:

- The **docs-refresh** phase (fetch the current memory / hooks / settings / skills / agents
  / commands / mcp docs before recommending).
- The **mechanism decision matrix** (pattern type → primary mechanism + what to combine).
- The **implementation templates** for each mechanism (rule, hook, CLAUDE.md, settings,
  skill, subagent, mcp, command).

## Axis 1 — enforce vs encourage (the philosophy the command lacked)

The ariadne enforcement strategy's core split. Choose by whether the convention is
**mechanically decidable**:

- **ENFORCE (hooks)** — deterministic, mechanically-checkable conventions get a blocking
  hook (PreToolUse for path/content shape; Stop for whole-tree invariants). Hooks carry
  **zero standing tokens** and emit bounded messages only on violation.
- **ENCOURAGE (guidance)** — judgement-heavy conventions get terse **path-scoped** rule text
  (loads only when a matching file is touched) plus, where useful, one write-time
  `additionalContext` micro-injection.
- **Never wire a judgement call to a deny/block.** A false-positive block on a judgement
  pattern is worse than a miss.

**Context-cost is a first-class output constraint.** The mechanism set should be net-neutral
or negative on always-on tokens: prefer hooks (zero standing cost) and path-scoped rules
(load-on-touch); when adding guidance, look for always-on text to thin or path-scope in
exchange (the ariadne pass ran ~−200 lines/turn while adding coverage).

## Axis 2 — user-level vs repo-level (the portability call)

Detected, not assumed:

- **User-level (rich)** — when the target is a private repo on a machine with the user's
  customisation surface present (`file-rules/` + `rule-injector.cjs`, central Stop hooks).
  Emit path-scoped `file-rules/` entries and hook extensions that leverage that surface.
- **Repo-level (portable)** — when the repo is public/foreign or the surface is absent. Emit
  only in-repo artifacts that assume nothing: `.claude/` hooks committed to the repo, a
  CLAUDE.md block, or plain linter/CI config.
- The skill **checks** for the surface (does `~/.claude/file-rules/` and the injector exist?
  is the repo public?) and routes; when ambiguous, it asks.

## Sequencing coupling (when it feeds plan-backlog)

Per the no-grandfathering constitution, a new blocking hook must not block on today's real
violations. When `enforce-patterns` tasks its mechanisms out via `plan-backlog`, each
blocking hook ships **warn-only** (or lands with its offender migration) until the matching
remediation sub-task removes the existing violations. The emitted sub-tasks carry this
ordering as explicit dependency edges on the remediation wave.

## Scope boundary — what it does NOT do

- Does **not** audit for violations (that is `review-patterns`).
- Does **not** itself decompose into a task tree — it hands mechanisms to `plan-backlog` when
  tasking is wanted, or applies them directly when not.
- Does **not** invent new enforcement paradigms — it routes to the current Claude Code
  surface as refreshed in the docs phase.

## Open questions

- Skill vs keep-as-command (README open question). Skill is the working assumption.
- Name: `enforce-patterns` vs reusing `enforce-pattern`. Reusing the name eases muscle memory
  but the plural signals the multi-pattern, two-axis upgrade.
- Apply-directly vs always-task-out: default to _propose + confirm_, then let the user pick
  apply-now or task-out. Never silently write hooks.
- How to detect "repo is public" reliably (remote URL heuristic vs asking)? Draft in the
  skill; default to asking when unsure.
