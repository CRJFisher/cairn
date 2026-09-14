# 27 — A run proves ownership of every branch, path, and write

Worktree paths are plan-scoped but branch names are only `step/<id>`. A later plan can therefore
adopt a surviving branch from another plan with the same step id. Runtime ownership checks also
treat an absent or unreadable lock as safe, and wait predicates execute without the guard used by
agent and command steps.

**Serves** **Run**. The invariant is positive ownership: a run writes only through identities it
can prove belong to that plan, step, and run.

## A — Namespace branches by plan

Use a branch identity containing both plan and step, for example `step/<plan>/<step>`, with the
same grammar and collision checks as the corresponding worktree path. A branch that already
exists is reused only when durable metadata proves it belongs to this plan and expected parent.

Legacy `step/<id>` refs require explicit classification:

- migrate one only when its owning plan can be established;
- refuse ambiguous or unowned refs;
- never adopt a branch merely because its former worktree registration disappeared;
- keep cleanup bounded to refs owned by the plan being pruned.

## B — A marker cannot outrun committed state

[21](21-commit-scope.md) correctly excludes paths that were dirty before a step, but path-level
exclusion has a second failure mode: the step can modify that same path, verification can pass
over its final uncommitted content, and the wrapper can commit only the marker. Discarding the
residual edit then leaves a fresh marker over work absent from `HEAD`.

The commit protocol must refuse to publish a marker whenever asserted state may depend on an
excluded path. A chain step may refuse to start over overlapping dirty work, or use a
content-level baseline that proves the step did not alter the excluded content; path membership
alone is insufficient.

## C — Runtime ownership fails closed

Inspection may report that no readable holder exists. A running step may not interpret that as
permission. Every runtime operation after acquisition requires:

- a present, readable run-lock record;
- an object id and run id matching the current runtime identity;
- a repository/ref still matching the acquired state.

A deleted or malformed lock is loss of ownership. Acquisition retains a separate, explicit
repair/reclaim path; ordinary runtime work does not.

## D — Waits remain guarded for their whole duration

`exec` and agent steps check repository ownership, while `wait_until` repeatedly executes
arbitrary shell without doing so. Check before either wait form and before every predicate
attempt. If ownership changes during a long wait, stop before launching the next predicate and
leave a report naming the lost lock.

## Acceptance

- Two plans sharing a step id cannot see, verify, merge, or prune each other's branch.
- Legacy branches are migrated only with positive ownership evidence; ambiguity refuses.
- A pre-dirty file changed by a step cannot produce a commit containing a completion marker but
  not the asserted file.
- Deleting, corrupting, or replacing run A's lock causes A's next guarded operation to refuse,
  even if run B subsequently owns the repository.
- A wait predicate never executes after its run loses ownership.

## Touches

`cairn/topology.py`, `cairn/worktrees.py`, `cairn/locks.py`, `cairn/__main__.py`,
`cairn/commands.py`, `cairn/parameters.py`, supervision/topology/run-model documentation,
[21](21-commit-scope.md), generated workflow fixtures, and concurrency tests.
