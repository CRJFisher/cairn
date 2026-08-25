# Task 04 — Global settings wiring

Wire the generic hook into user-level settings so it applies across all repos.

## Scope

Add to `~/.claude/settings.json`:

- `SessionStart` → run `hydrate_worktree.sh`.
- `PostToolUse` with matcher `EnterWorktree` → run `hydrate_worktree.sh`.

Reference the hook by its `~/.claude/hooks/` path. No timeout tuning needed — the
hook only prints, so it is fast (unlike the old auto-running version, which
needed `timeout: 600`).

## Constraints

- This edits **global** settings; take it after Tasks 02–03 are in place and
  confirmed. Harder to reverse than the repo-local steps.
- Use the update-config skill or a careful, reviewed edit; do not clobber
  existing hook entries.

## Acceptance

- The hook fires on session start and on `EnterWorktree` in every repo, and is a
  silent no-op in repos without a `worktree.config.json`.

## Depends on

Tasks 02, 03.
