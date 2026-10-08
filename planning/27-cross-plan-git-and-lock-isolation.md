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

## Close-out

Done. All four sections hold, and [21](21-commit-scope.md)'s section B is built here with C.

**A run writes only through identities it can prove are its own.** A step's branch is
`step/<plan>/<step>`, derived inside `cairn worktree setup` from the same `--plan` and
`--step` the worktree path comes from and refused on the same two grammars, so two plans
sharing a step id share no branch and cannot see, verify, merge or prune each other's. The
prune composes every path and branch it touches from the plan it was given, so its reach is
bounded by derivation and not by a check. Beside each branch is a durable owner record at
`refs/cairn/branch-owner/<plan>/<step>` naming the branch, plan, step and parent: an
existing branch is reused only when that record proves all three, and one with no readable
record is refused rather than adopted. A ref occupying a plan's whole namespace is named as
the ref that has to go instead of surfacing as git's `cannot lock ref`.

A bare `step/<id>` ref is classified and reported as the setup node's `legacy_branch`:
`migrated` only where its own worktree registration establishes this plan as its owner,
`owned_elsewhere` where that registration names another, `unattributable` where nothing
attributes it — including a registration that has simply disappeared — and `superseded`
where this plan already has its branch. Only `migrated` moves anything; the rest are left
where they are and named in follow-up work.

Runtime ownership fails closed. `require_run_lock` replaces the fail-open guard on every
subcommand that opens a session, runs a command or writes, and requires a present readable
record, this run's id, the lock object the acquisition pinned, and a repository the record
agrees is the one the step stands in — so deleting, corrupting or replacing run A's lock all
stop A's next guarded operation, whether or not a run B has since taken over. Acquisition
keeps its separate reclaim path, which is the only place an unreadable lock may be taken.

Waits are guarded for their whole duration: ownership is proved before either form and
before every attempt of an `until` predicate, so a wait that loses the repository stops
before launching the next predicate and leaves a report naming the lock it lost.

A marker cannot outrun committed state. Every work step's snapshot carries the content of
each dirty path and not just its name, and the commit proves every excluded path
byte-identical to that baseline. A step that altered one fails `excluded_path_changed` and
commits nothing — no marker, no partial output — and every refusal the commit reaches
withdraws the fresh marker from the working tree, restoring whatever `HEAD` held, because
the gate that decides whether the step runs again reads the tree.

Generator version 12 marks the shape: a setup body written by 11 passes a `--branch` this
binary does not accept, and the bare `step/<id>` it names is not a branch this binary will
work on.
