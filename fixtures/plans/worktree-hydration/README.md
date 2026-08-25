# Worktree Hydration

A general mechanism for hydrating fresh git worktrees — installing dependencies,
syncing generated code, running an initial build — so that tests, hooks, and
project checks work with no manual repair.

## Intention

A linked git worktree starts empty of build state: no `node_modules`, no
compiled native bindings, no `dist`. Until it is hydrated, every tool that
depends on that state fails. Today one repo (ariadne) solves this with a
bespoke `SessionStart` / `EnterWorktree` hook that hardcodes its marker
(`node_modules`) and its commands (`pnpm install && pnpm build`). This design
lifts that idea into a repo-agnostic mechanism: any repo declares its hydration
recipe in one small config file, a single user-level hook detects an unhydrated
worktree and warns, and one user-run command does the work.

**Contribution to the intention tree:** removes per-repo hook duplication and a
recurring class of "the worktree is broken" friction, replacing it with one
generic mechanism plus a small per-repo data file — the same generic-mechanism /
per-repo-data split used elsewhere in this config.

## Architecture

Three artifacts, split by responsibility:

| Artifact                 | Lives at                                                            | Role                                                                           |
| ------------------------ | ------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| `worktree.config.json`   | `<repo>/.claude/`                                                   | Per-repo data: the hydration recipe and worktree location.                     |
| Hydration hook           | User-level (`claude-config/hooks/`, symlinked to `~/.claude/hooks`) | Detection only. Warns when the current worktree is unhydrated.                 |
| `/hydrate-setup` command | User-level (`claude-config/commands/`)                              | User-invoked. Bootstraps the config if absent, then runs the hydration recipe. |

The only shared contract is the config schema. There is exactly one hook
consumer and one command consumer of it, so nothing is over-abstracted — the
schema is a small versioned contract owned by the command that writes it.

### `worktree.config.json`

```jsonc
{
  "hydration": {
    "commands": ["pnpm install", "pnpm build"], // ordered list, run in sequence
    "sentinel": ".claude/.worktree-hydrated", // written on full success
  },
  "worktrees": {
    "dir": ".claude/worktrees", // where new worktrees are stored
  },
}
```

- **`commands`** is a **list**, run in order, so a repo needing a series of steps
  (install → codegen → build) expresses them directly. The command runs each in
  turn and stops on the first failure.
- **`sentinel`** is the hydration success marker. It is written only after the
  final command succeeds — never after a partial run. This is the root-cause fix
  for the marker/command coupling problem: a natural marker like `node_modules`
  appears after `install` but before `build`, so a failed build would leave a
  half-hydrated tree that the next session mistakes for hydrated. The sentinel is
  ecosystem-agnostic and only ever means "the whole recipe succeeded."
- **`worktrees.dir`** records where the repo keeps its worktrees, the one piece
  of worktree-location data worth capturing centrally.

### Hydration hook (detection only)

Wired at user level into `SessionStart` and `PostToolUse:EnterWorktree`. On each
firing it:

1. Resolves the repo root from the hook's cwd. Outside a repo → no-op.
2. Reads `<root>/.claude/worktree.config.json`. Absent → no-op (this is what
   makes the global hook harmless on every repo that has not opted in).
3. Confirms this is a linked worktree — its `.git` is a _file_ (a gitdir
   pointer), not a directory. The main checkout → no-op.
4. Checks the sentinel. Present → already hydrated → no-op.
5. Otherwise prints a warning telling the user to run `/hydrate-setup`.

The hook never installs anything. Moving the work out of the hook removes the
sandbox and timeout complications the auto-running version carried: a hook that
only prints has no write-permission or long-runtime concerns.

Two triggers because `SessionStart` covers _launching into_ a worktree, and
`PostToolUse:EnterWorktree` covers _switching to_ one mid-session, which
`SessionStart` does not fire for.

### `/hydrate-setup` command (does the work)

User-invoked only. It:

1. Reads `<root>/.claude/worktree.config.json`. **If absent, bootstraps it:**
   detects the ecosystem (pnpm-lock / uv.lock / Cargo.toml / go.mod / Gemfile /
   requirements.txt / monorepo), proposes a command list and sentinel path,
   confirms with the user, and writes the config.
2. Runs `hydration.commands` in order, stopping on the first failure.
3. On full success, writes `hydration.sentinel`.

Because the command runs in the agent's Bash (sandboxed), a worktree entered
mid-session that is not yet a session working directory sits outside the sandbox
write-allowlist; the command disables the sandbox for the hydration step to
cover that case. The common `SessionStart`-into-a-worktree case needs no
exception — that worktree is already a working directory.

## Key design decisions

| #   | Decision                     | Choice                                   | Rationale                                                                                                                   |
| --- | ---------------------------- | ---------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| D1  | Where the generic hook lives | User-level, one copy                     | One implementation; auto-applies to any repo with a config; no-ops elsewhere.                                               |
| D2  | Config location              | `<repo>/.claude/worktree.config.json`    | Co-located with hooks; keeps repo root clean; clearly "Claude tooling."                                                     |
| D3  | Hydration marker             | Success sentinel, not a natural artifact | A natural marker green-lights a partially-hydrated tree if a later step fails. The sentinel means "whole recipe succeeded." |
| D4  | Hook behaviour               | Detect and warn; never hydrate           | Removes sandbox/timeout complexity from the hook; keeps heavy work explicit and user-initiated.                             |
| D5  | Where hydration runs         | The `/hydrate-setup` command             | User-initiated, runs in agent context, can bootstrap config and disable the sandbox for the mid-session case.               |
| D6  | Command scope                | Bootstrap-if-missing, then hydrate       | One command serves both first-time setup and per-worktree hydration.                                                        |
| D7  | Commands shape               | Ordered list                             | Supports multi-step recipes (install → codegen → build); stops on first failure.                                            |

## Build plan (v1)

Sequential tasks, one file each in this folder:

1. [Config schema](01-config-schema.md) — the `worktree.config.json` contract.
2. [Hydration hook](02-hydration-hook.md) — generic detection-and-warn hook.
3. [`/hydrate-setup` command](03-hydrate-setup-command.md) — bootstrap-if-missing,
   then run the recipe.
4. [Global wiring](04-global-wiring.md) — add the hook to `~/.claude/settings.json`.
5. [Ariadne migration](05-ariadne-migration.md) — write the config, delete the
   bespoke hook and script, remove the dangling settings entries.

Dependency order: 01 → (02, 03) → 04 → 05. Tasks 02 and 03 both depend only on
the schema and can proceed in parallel. Global settings (04) and the removal of
ariadne's working hook (05) are the harder-to-reverse steps, taken last after
the generic mechanism is in place and confirmed.

**Done already:** the hydration clause in the `build-and-review` skill (Step 1)
is removed.

## Deferred (v2)

- **Lockfile fingerprinting** — store a hash of the lockfile(s) in the sentinel;
  re-hydrate when it changes, handling "entered a worktree, then dependencies
  changed underneath." Adds complexity; not needed for v1.
- **`autoHydrate: "always"`** — opt-in fresh-clone hydration for the main
  checkout, for repos that want it. v1 targets worktrees only.
