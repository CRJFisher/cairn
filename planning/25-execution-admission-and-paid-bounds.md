# 25 — Execute only the workflow and spend that were admitted

Cairn gates a generated workflow while authoring it, then allows the published file to be
edited before it is offered, started, or installed. The execution boundary checks the engine
and repository but does not re-check the exact definition that will run. At the same time,
several numeric checks establish only that a field exists, not that it is a usable bound, and
merge-conflict resolution is the one paid session omitted from both pricing and an internal
deadline.

**Serves** **Run** and **Schedule**. The invariant is that execution uses exactly the bytes and
costs that were checked, disclosed, and accepted.

## A — Gate the exact bytes at every admission boundary

`workflow author` and `workflow check` run structural preflight and the engine gate.
`run offer`, `run start`, and `schedule install` do not. A hand edit can therefore add active
retries, remove a timeout, alter routing, or otherwise violate an invariant while retaining a
path to execution.

- `run offer` re-runs structural preflight, provenance verification, and engine validation over
  the selected file before minting an offer.
- The offer records the digest of those bytes. `run start` verifies the same digest before
  spending the acceptance and again before launch if the file can move between those acts.
- `schedule install` validates the exact published target. A scheduled definition is either an
  immutable admitted artifact or is revalidated before every firing; a symlink to mutable,
  unchecked bytes is not enough.
- A definition changed after Cairn wrote it remains explainable and inspectable, but it cannot
  run under Cairn's guarantees until it passes the same gate again.

## B — Bounds are finite semantic values, not merely present fields

The current validators admit non-finite budgets, Boolean/zero/negative workflow timeouts, and
malformed or active retry policies. Python's JSON writer also emits `NaN` and `Infinity` by
default, even though they are not standard JSON.

Define one shared scalar-validation layer:

- budgets and timeouts are positive and finite;
- retry limits are nonnegative integers and intervals are positive integers;
- Boolean values never count as integers;
- DAG-level retry is exactly disabled;
- paid and repository-mutating nodes use their role's exact retry policy;
- JSON persistence uses `allow_nan=False` as a final backstop.

Preflight remains total on hostile input. Its cycle walk becomes iterative so a valid 1,500-node
workflow receives a verdict rather than `RecursionError`.

## C — Price and bound merge-resolution sessions

An emitted `merge land` body currently omits the model and dollar ceiling its parser accepts,
`merge.py` opens the provider without an internal deadline, and consent counts only ordinary
`agent run` bodies. A conflict can therefore open an undisclosed paid session on provider
defaults until the engine's outer timeout kills it.

- Define the merge resolver's model, ceiling, and work timeout beside the other role bounds.
- Emit all three into every merge body.
- Include merge sessions in the offer's count, models, ceiling, and longest-bound disclosure.
- Stop the resolver inside its wrapper, leaving report grace before the engine timeout, using
  the agent-step deadline protocol from [22](22-timed-out-step.md).
- Record timeout, cost, model, and session identity whether resolution succeeds or stops.

## D — Daemon consent has the same enforceable boundary as run consent

`--accept-daemon` is currently a bare Boolean. It can be passed by the same caller that was
supposed to show the daemon's machine-wide retry and persistent-process cost. Replace it with a
persisted daemon offer and acceptance:

- showing the current cost is the only act that mints an offer;
- acceptance postdates and identifies that offer;
- installation and process start are separate scopes where they incur separate acts;
- every accepted escalation is auditable and single-use;
- no run offer authorises a daemon and no daemon acceptance authorises a run.

## Acceptance

- Mutating an authored workflow causes offer, start, and schedule installation to refuse before
  execution unless the changed bytes pass the complete gate and are newly admitted.
- The bytes executed have the digest the offer priced.
- `NaN`, infinity, Boolean/zero/negative timeouts, malformed retries, and active paid retries are
  refused; persistence cannot emit non-standard numeric JSON.
- A 1,500-node chain and cycle both receive deterministic preflight results without recursion.
- Every merge-resolution session has a disclosed model, ceiling, and internal timeout, and a
  timeout still leaves a durable report.
- A daemon cannot be installed or started from a bare acceptance flag with no prior offer.

## Touches

`cairn/workflow/preflight.py`, `cairn/workflow/cli.py`, `cairn/workflow/gate.py`,
`cairn/skill/consent.py`, `cairn/skill/cli.py`, `cairn/schedule.py`,
`cairn/schedule_cli.py`, `cairn/emitters.py`, `cairn/merge.py`, `cairn/core.py`,
the workflow and trigger contracts, generated fixtures, and their tests.
