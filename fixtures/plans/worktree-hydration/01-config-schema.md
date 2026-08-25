# Task 01 — Config schema

Define and document the `worktree.config.json` contract that the hook reads and
the command writes.

## Scope

- A short contract doc (`config-schema.md` alongside the command, or a heredoc
  reference in the command file) describing every field.
- The canonical shape:

```jsonc
{
  "hydration": {
    "commands": ["pnpm install", "pnpm build"], // ordered; stop on first failure
    "sentinel": ".claude/.worktree-hydrated", // written on full success only
  },
  "worktrees": {
    "dir": ".claude/worktrees", // where new worktrees are stored
  },
}
```

## Decisions to pin

- `commands` is a list, run in order, halting on first non-zero exit.
- `sentinel` path is repo-relative; written only after the last command succeeds.
- `worktrees.dir` is repo-relative; informational for v1 (not enforced).
- All paths repo-relative, resolved against the repo root.

## Acceptance

- One authoritative description of the schema exists, owned by the command.
- Ariadne's future config validates against it.

## Depends on

Nothing. This is the shared contract the other tasks build against.
