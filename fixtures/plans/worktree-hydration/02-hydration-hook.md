# Task 02 — Hydration hook (detection only)

Write the generic user-level hook that detects an unhydrated worktree and warns.

## Scope

`claude-config/hooks/hydrate_worktree.sh` (symlinked to `~/.claude/hooks`).
Fires on `SessionStart` and `PostToolUse:EnterWorktree`. Logic, in order, each
step a fast no-op that exits clean:

1. Resolve repo root via `git rev-parse --show-toplevel`. Outside a repo → exit.
2. `<root>/.claude/worktree.config.json` absent → exit. (Keeps the global hook
   harmless on every repo that has not opted in.)
3. `<root>/.git` is not a file (i.e. main checkout, not a linked worktree) →
   exit.
4. Sentinel (from config) present → already hydrated → exit.
5. Otherwise print a warning: this worktree is unhydrated, run `/hydrate-setup`.

## Constraints

- Detection only — the hook never installs, builds, or writes anything.
- POSIX `sh`, no repo-specific assumptions.
- Reads `hydration.sentinel` from the config (jq or a minimal parser).
- Fails open: any error prints guidance, never wedges the session.

## Acceptance

- No-op (silent, exit 0) in: non-repo dirs, repos without a config, the main
  checkout, an already-hydrated worktree.
- Prints the run-`/hydrate-setup` warning only in an unhydrated linked worktree
  that has a config.

## Depends on

Task 01 (sentinel field). Independent of the command (Task 03).
