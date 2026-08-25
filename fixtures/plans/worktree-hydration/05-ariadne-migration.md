# Task 05 — Ariadne migration

Move ariadne onto the generic mechanism and delete its bespoke hydration code.

## Scope

- **Add** `ariadne/.claude/worktree.config.json`:

  ```jsonc
  {
    "hydration": {
      "commands": ["pnpm install", "pnpm build"],
      "sentinel": ".claude/.worktree-hydrated",
    },
    "worktrees": { "dir": ".claude/worktrees" },
  }
  ```

- **Delete** `ariadne/.claude/hooks/hydrate_worktree.sh`.
- **Delete** `ariadne/scripts/hydrate-worktree.sh`.
- **Remove** the two hydration hook entries from `ariadne/.claude/settings.json`
  (the `SessionStart` entry and the `PostToolUse:EnterWorktree` entry that call
  `hydrate_worktree.sh`). Leave the other hooks untouched.

## Verify before deleting

Ariadne's `scripts/hydrate-worktree.sh` carries real nuance — the pnpm
`publicHoistPattern` / `allowBuilds` config in `pnpm-workspace.yaml` that lets a
single install compile the tree-sitter native bindings and keep `pnpm exec`
working. Confirm `["pnpm install", "pnpm build"]` reproduces that behaviour (it
should: the config lives in `pnpm-workspace.yaml`, which the worktree inherits
because `worktree.baseRef: head` branches from local HEAD). If any step in the
old script is load-bearing beyond install+build, fold it into the `commands`
list before deleting.

## Acceptance

- A fresh ariadne worktree, hydrated via `/hydrate-setup`, has working `pnpm
exec`, vitest, and commit hooks — parity with the old script.
- No bespoke hydration hook or script remains in ariadne; no dangling settings
  entries reference the deleted files.

## Depends on

Tasks 02, 03, 04. This is the last step — it removes a currently-working hook.
