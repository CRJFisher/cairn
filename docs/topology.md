# Topology

The topology turns a step graph into branches, worktrees and nodes. It is a pure function:
the same graph, repository root and parent branch always give the same topology, and
nothing in it reads a clock, a filesystem or git. This decides what the branches are;
[workflow.md](workflow.md) assembles them into a file.

**Every node emits.** The join runs the wave's one census — which branches arrived with work,
and why the others did not — because a slot's landing moves a branch tip and afterwards
nothing can tell an excluded step from a landed one ([workflow.md](workflow.md)).

## Waves

Steps are levelled by their dependencies. Each level is a wave, and its width decides its
shape.

A wave of one step is a **chain segment**: the step runs on the parent branch, in the
repository itself, as `work`, then `verify`, then `mark`, then `commit`. No worktree, no
join, no prune.

A wave of two or more steps is **isolated**: each step gets its own branch
`step/<plan>/<step>` and its own worktree, and runs `setup`, `work`, `verify`, `mark`,
`commit`. The wave's commits feed one `join`, the join feeds a chain of `merge` slots each
followed by its own proof ([merge-step.md](merge-step.md)), and the last proof feeds a
`prune`. The next wave starts from the prune.

A step that declares `remediate` runs `remedy` and `recheck` between its `verify` and its
`mark`, in either position: one resumed session over a failed assertion, then the same
assertion again ([verify-gate.md](verify-gate.md)).

A plan of one step is the degenerate chain. The run's first node is always `lock_acquire`.
The release is not a node at all: it runs as the workflow's exit handler, because a node
whose dependency failed is never dispatched and a failed run must still give the repository
back.

**A step's shape does not change with its position — only one flag on its `commit` does.**
A failing verify with no `continue_on` aborts the merge-join and nothing lands, so the
measured pattern is `continue_on` on the verify plus a precondition-gated consequence,
which is the `mark` node. A closed gate skips that node, and the skip cascades onward:
the `commit` carries the flag in an isolated wave, so the cascade stops at that branch and
the join still runs, and omits it in a chain, so the cascade carries on into everything
that depended on the work ([verify-gate.md](verify-gate.md)).

A step whose plan declared it unverified has nothing on disk to assert, so it gets no
`verify` node and its `mark` is gated on its own report alone.

## Where worktrees live, and what the branch is called

The worktree parent is `<repository>.cairn-worktrees/<plan-slug>/<step-id>`, derived from
the repository's own location and resolved at invocation. It sits **beside** the repository
so no commit step can sweep a worktree into a commit, and it is namespaced by plan so two
plans with the same step ids can never adopt each other's worktrees. No path contains a
home directory or an assumed workspace root.

The branch is `step/<plan-slug>/<step-id>`, the same two values in the same order, refused
on the same two grammars. A plan slug matches `^[a-z0-9][a-z0-9-]*$` and a step id is an
engine identifier, so neither admits a `/` and the pair composes exactly one ref and parses
back to exactly one pair. **Two plans sharing a step id therefore share no branch**, and
cannot see, verify, merge or prune each other's, because the only source of a branch name is
a generated workflow and every name in it carries the plan it was generated for.

Both derivations happen inside `cairn worktree setup`, from `--plan` and `--step`. Neither
the path nor the branch is written into the generated body: a path names one repository, and
a second spelling of the branch would be a second idea of which ref the step is answerable
for ([workflow.md](workflow.md)).

## A branch belongs to one plan and one step

Beside each branch is a durable owner record, a blob at
`refs/cairn/branch-owner/<plan>/<step>` holding the branch, the plan, the step and the
parent branch it was created from. A branch that does not exist is claimed outright. **A
branch that does exist is reused only when that record proves all three**: this plan, this
step, and this parent. The name proves the first two and nothing proves the third, and a
branch reused from a different parent would carry the wrong history into the run's merges
while every name and path still said it was right.

A branch with no readable record is refused, never adopted. Unreadable and absent are one
answer here on purpose: both mean nothing proves the ref is this plan's, and the only move a
second arm could make is the adoption this exists to prevent. The record is deleted with the
branch it describes and kept for a branch the prune retained, because that record is what
lets a later run of the plan prove the surviving branch is its own and pick the work up.

`refs/heads/step/<plan>` and `refs/heads/step/<plan>/<step>` cannot both exist — git stores
a ref as a file and a namespace as a directory — so a ref sitting on a plan's whole
namespace is refused by name. Left to git it surfaces as `cannot lock ref`, which reads as
contention rather than as the one ref that has to go. The worktree path has the same
collision in the same place: a file where the plan's directory belongs.

## A bare `step/<id>` ref is classified, never adopted

A ref named for a step and no plan is attributed by its own worktree registration: a
registration at `<repository>.cairn-worktrees/<plan>/<step>` is the plan that created the
ref saying so, in a place no later plan writes. `cairn worktree setup` reaches one of four
verdicts and reports it as the setup node's `legacy_branch`:

| Verdict           | What happens                                                        |
| ----------------- | ------------------------------------------------------------------- |
| `superseded`      | This plan already has its namespaced branch; the legacy ref is left |
| `unattributable`  | No registration attributes it; it is left where it is and named     |
| `owned_elsewhere` | Another plan owns it; it is that plan's to migrate                  |
| `migrated`        | This plan owns it: renamed onto `step/<plan>/<step>` and recorded   |

Only the last moves anything, and it carries a killed run's committed work forward. A
registration that has simply gone attributes nothing: a disappeared registration is the
absence of evidence, and adopting a branch on it is how one plan would take another's work.
The migration establishes the plan and never the parent — the parent recorded is the one
this run lands on, and whether the ref's tip may move onto it is the ancestry question the
convergence asks next.

## The node-name contract

Every node is named `<role>_<subject>`, and the role is the text before the first
underscore. The roles are closed:

| Role                                                       | Subject                | Example               |
| ---------------------------------------------------------- | ---------------------- | --------------------- |
| `setup` `work` `verify` `remedy` `recheck` `mark` `commit` | the step id            | `verify_theme_reader` |
| `join` `prune`                                             | `w<wave>`              | `prune_w3`            |
| `merge`                                                    | `w<wave>_<slot>`       | `merge_w3_2`          |
| `lock`                                                     | `acquire` or `release` | `lock_release`        |

Because the role is exactly the first token, a step whose own id begins with a role name
still round-trips: `work_work_config` is the `work` node of the step `work_config`. The run
model parses these names, so a rename moves both this table and the run model together.

A name is refused, at generation time, if it exceeds 40 bytes, contains a hyphen, or is one
of the engine's reserved ids (`env`, `params`, `args`, `stdout`, `stderr`, `output`,
`outputs`). Names are never truncated to fit: a truncated name stops round-tripping
silently, so an over-long step id is an error naming the arithmetic instead.

## What each node carries

The emitter reads a role-specific `detail` from every node, so the key set is as much a
contract as the name is.

| Role     | `detail` keys                                                                       |
| -------- | ----------------------------------------------------------------------------------- |
| `lock`   | `action`, `plan`                                                                    |
| `setup`  | `plan`, `branch`, `worktree`, `base`                                                |
| `work`   | `kind`                                                                              |
| `verify` | `command` for a step's assertion; `merge`, `candidates`, `into` for a merge's proof |
| `mark`   | `verified`, `position`                                                              |
| `commit` | `branch`, `position`                                                                |
| `join`   | `branches`                                                                          |
| `merge`  | `slot`, `candidates`, `into`, `provider`                                            |
| `prune`  | `plan`, `steps`, `worktrees`, `branches`, `parent`                                  |

A `verify` node names a step when it runs that step's own assertion and names none when it
proves a merge. Both answer "is what was claimed actually there"; only the first is a
command a plan's author wrote, which is why only the first is exempt from the quoting rule.

## The merge order is a bound

A wave's steps are independent by construction, so no dependency justifies an order among
them. The topology emits one merge **slot** per branch, chained so only one merge happens
at a time, and every slot carries the same candidate list. Which branch a slot lands is the
merge step's decision on the evidence in front of it
([merge-step.md](merge-step.md)) — evidence that does not exist here, because a read-only
merge compares committed tips and the topology touches no git at all. Across waves the
order is fixed, because the waves themselves are.

A merge slot is bounded as the agent step it can become rather than as the git work it
usually is, because a conflict is resolved by a session. Its proof is a support step.

## Converging a worktree

`cairn worktree setup` owns every case. The engine's `git.worktree.add` covers one and
fails one of them _green_, which is why none of it is delegated.

The decision is separated from the doing. `inspect` gathers facts, `classify` turns them
into exactly one of thirteen named states with no I/O in it at all, and the converger acts
on that state. The decision table is therefore a unit test rather than a workflow run, and
a shape nobody anticipated reaches `unclassified` and halts instead of falling into
whichever arm happened to be last.

Six states converge:

| State                | What Cairn does                        | Reported as          |
| -------------------- | -------------------------------------- | -------------------- |
| `healthy`            | Reuse it, uncommitted work and all     | `reused`             |
| `merged_behind`      | Fast-forward it onto the parent's head | `fast_forwarded`     |
| `wrong_branch`       | Check its own branch back out          | `switched_to_branch` |
| `stale_registration` | Prune the registration and recreate    | `created`            |
| `junk`               | Move the directory aside and recreate  | `recreated`          |
| `absent`             | Create it                              | `created`            |

Ancestry decides movement, never appearance. A merged branch moves with `merge --ff-only`
rather than `reset --hard`: the branch is a proven ancestor, so the move cannot drop a
commit, and git itself refuses when the move would overwrite a killed agent's edits — that
refusal is reported as `stale_head_preserved` with follow-up work naming it. A move that
fails for any _other_ reason halts, because it leaves the branch at exactly the stale head
this arm exists to clear. Cleanliness therefore decides what may be done and never what the
worktree _is_, which is what stops one stray build artefact leaving the branch behind.

Seven states halt:

| State                    | Cause               | Why                                              |
| ------------------------ | ------------------- | ------------------------------------------------ |
| `foreign`                | `worktree_foreign`  | It belongs to another repository                 |
| `locked`                 | `worktree_unusable` | Cairn never unlocks a worktree                   |
| `branch_elsewhere`       | `worktree_unusable` | The branch is live in another worktree           |
| `interrupted`            | `merge_in_progress` | An unfinished merge or rebase is left as it is   |
| `unreadable`             | `worktree_unusable` | Registered here and git will not answer about it |
| `repairable` after retry | `worktree_unusable` | `git worktree repair` did not recover it         |
| `unclassified`           | `worktree_unusable` | A shape Cairn does not recognise                 |

A worktree holding uncommitted work on a ref other than the one this step owns halts as
`worktree_dirty`, and the repository's own working tree is refused before any of this. A
registration whose directory no longer exists does **not** hold its branch: a run killed
after its worktrees root was moved or deleted leaves exactly that, and refusing on it would
halt every later run of the plan, permanently, over a directory the create arm prunes.

**Convergence never loses work.** A worktree git can still read is repaired before any arm
that would move it, so a broken `.git` file loses nothing. A directory that has to go is
renamed aside rather than deleted, because with the admin data gone nothing can say whether
what is inside was ever committed. The rename is refused unless the path sits inside a
`*.cairn-worktrees` root — checked component-wise, and against both the path as given and
the path with symlinks resolved, so that `/repo-backup` is not mistaken for something under
`/repo` and a worktrees root symlinked onto another volume still converges.

`cairn worktree prune` runs `git worktree remove` inside the write mutex. Uncommitted work
in a worktree is a killed agent's output, so a dirty worktree is kept and reported as
follow-up work rather than discarded — unless `--force` is passed explicitly, which nothing
Cairn emits does. A branch is deleted only when it is an ancestor of the parent branch the
topology named, so an unmerged branch is never deleted and a merged one is not retained just
because the repository sits elsewhere. When a removal refuses, why is read back off the
worktree itself rather than out of git's wording, so a directory that is simply gone is not
reported as work to rescue.

It takes `--plan` and `--step` and composes every path and every branch from them, so **the
cleanup is bounded to the plan being pruned by derivation rather than by a check**: there is
no argument through which a ref outside `step/<plan>/` could be named.

## The run's maximum duration

Every node carries a worst-case duration, and the run's maximum is their **sum**: how long
the run might still be _writing_. It is what the run lock's lease is derived from. The
slowest chain would be tighter and wrong for that: it holds only under unbounded concurrency,
and the engine caps concurrent steps, so a wave wider than the cap outruns its own chain and
a lease derived from it would come free mid-run.

A step's own weight is `bound × attempts + interval × retries`, where the bound is the hang
guard for every plan step, because **the engine applies
`timeout_sec` to each attempt rather than to the step** — measured, not assumed. A bound
counted once would understate a plan by hours. A `wait_until` step counts the fifteen-second
grace its emitted bound carries and an agent step the 180-second report grace, so the number
stated and the number the engine enforces are the same one.

No plan is refused for its length. A declared `cairn wait` counts in full, because it holds
the run lock for its whole duration — so a plan's waits are part of its maximum duration and
therefore of the lock's reclaim window ([supervision.md](supervision.md)).

## Measured

Five independent steps, five seconds of work each, real worktrees and the git write mutex
in place: **6.66s against 29.46s** run one step at a time — a ratio of **0.23** where 0.20
is ideal and the engine's own raw parallelism measured 0.33. The mutex adds about **44ms
per git write**, which is the serialisation itself and not overhead around it: two writes
per step against several seconds of work is under two percent, and against an agent step
measured in minutes it is not overhead the fan-out can feel.

Reproduce with `python3 -m scripts.measure_fanout --steps 5 --seconds 5` from this
package's root, with `dagu` on PATH — without it only the mutex half is measured.
