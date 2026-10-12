# 25 — Execute only the workflow that was admitted

Cairn gates a generated workflow while authoring it, then allows the published file to be
edited before it is started or installed. The execution boundary checks the engine and
repository but does not re-check the exact definition that will run.

**Serves** **Run** and **Schedule**. The invariant is that execution uses exactly the bytes
that were checked.

## A — Gate the exact bytes at every admission boundary

`workflow author` and `workflow check` run structural preflight and the engine gate.
`run start` and `schedule install` do not. A hand edit can therefore add active retries,
remove a timeout, alter routing, or otherwise violate an invariant while retaining a path to
execution.

- `run start` re-runs structural preflight, provenance verification, and engine validation
  over the selected file, and launches a snapshot of exactly the bytes that passed.
- `schedule install` validates the exact published target. A scheduled definition is either an
  immutable admitted artifact or is revalidated before every firing; a symlink to mutable,
  unchecked bytes is not enough.
- A definition changed after Cairn wrote it remains explainable and inspectable, but it cannot
  run under Cairn's guarantees until it passes the same gate again.

## Acceptance

- Mutating an authored workflow causes start and schedule installation to refuse before
  execution unless the changed bytes pass the complete gate and are newly admitted.
- The bytes executed are the bytes that passed the gate.

## Touches

`cairn/workflow/preflight.py`, `cairn/workflow/cli.py`, `cairn/workflow/gate.py`,
`cairn/skill/cli.py`, `cairn/schedule.py`, `cairn/schedule_cli.py`, the workflow and trigger
contracts, generated fixtures, and their tests.
