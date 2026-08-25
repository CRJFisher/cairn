# Task 03 — `/hydrate-setup` command

Write the user-invoked command that bootstraps the config if missing, then runs
the hydration recipe.

## Scope

`claude-config/commands/hydrate-setup.md`. Behaviour:

1. Read `<root>/.claude/worktree.config.json`.
2. **If absent, bootstrap it:**
   - Detect the ecosystem from lockfiles/manifests: pnpm-lock → pnpm, uv.lock →
     uv, Cargo.toml → cargo, go.mod → go, Gemfile → bundle, requirements.txt →
     pip; recognise monorepos.
   - Propose a `commands` list and a `sentinel` path.
   - Confirm with the user (allow edits).
   - Write the config.
3. Run `hydration.commands` in order, stopping on the first failure and
   reporting which command failed.
4. On full success, write `hydration.sentinel`.

## Constraints

- User-invoked only; not model-triggered.
- Mid-session worktree case: a worktree entered this session that is not yet a
  session working directory sits outside the sandbox write-allowlist — disable
  the sandbox for the hydration step so the install can write. The
  `SessionStart`-into-a-worktree case needs no exception.
- Idempotent: re-running on a hydrated worktree re-runs the (idempotent) recipe
  and re-stamps the sentinel.

## Acceptance

- On a fresh worktree with a config: recipe runs, sentinel appears, the hook
  goes quiet on next fire.
- On a fresh worktree with no config: bootstraps a correct config after
  confirmation, then hydrates.

## Depends on

Task 01 (schema). Pairs with Task 02 (the warning names this command).
