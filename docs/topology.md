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

A wave of two or more steps is **isolated**: each step gets its own branch `step/<id>` and
its own worktree, and runs `setup`, `work`, `verify`, `mark`, `commit`. The wave's commits
feed one `join`, the join feeds a chain of `merge` slots each followed by its own proof
([merge-step.md](merge-step.md)), and the last proof feeds a `prune`. The next wave starts
from the prune.

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

## Where worktrees live

The worktree parent is `<repository>.cairn-worktrees/<plan-slug>/<step-id>`, derived from
the repository's own location and resolved at invocation. It sits **beside** the repository
so no commit step can sweep a worktree into a commit, and it is namespaced by plan so two
plans with the same step ids can never adopt each other's worktrees. No path contains a
home directory or an assumed workspace root.

## The node-name contract

Every node is named `<role>_<subject>`, and the role is the text before the first
underscore. The roles are closed:

| Role                                    | Subject                | Example               |
| --------------------------------------- | ---------------------- | --------------------- |
| `setup` `work` `verify` `mark` `commit` | the step id            | `verify_theme_reader` |
| `join` `prune`                          | `w<wave>`              | `prune_w3`            |
| `merge`                                 | `w<wave>_<slot>`       | `merge_w3_2`          |
| `lock`                                  | `acquire` or `release` | `lock_release`        |

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

| Role     | `detail` keys                     |
| -------- | --------------------------------- |
| `lock`   | `action`, `plan`                  |
| `setup`  | `plan`, `branch`, `worktree`, `base` |
| `work`   | `kind`                            |
| `verify` | `command` for a step's assertion; `merge`, `candidates`, `into` for a merge's proof |
| `mark`   | `verified`, `position`            |
| `commit` | `branch`, `position`              |
| `join`   | `branches`                        |
| `merge`  | `slot`, `candidates`, `into`, `provider` |
| `prune`  | `plan`, `steps`, `worktrees`, `branches`, `parent` |

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

A merge slot is priced as the agent step it can become rather than as the git work it
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
registration whose directory no longer exists does **not** hold its branch: branch names
carry no plan slug while worktree paths do, so a crashed run of another plan otherwise
halts every later plan naming that step, permanently.

**Convergence never costs work.** A worktree git can still read is repaired before any arm
that would move it, so a broken `.git` file costs nothing. A directory that has to go is
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

## The run's maximum duration

Every node carries a worst-case duration, and two numbers are derived from them because
they answer two different questions.

The **sum** is how long the run might still be _writing_, and it is what the run lock's
lease is derived from. The slowest chain would be tighter and wrong for that: it holds only
under unbounded concurrency, and the engine caps concurrent steps, so a wave wider than the
cap outruns its own chain and a lease derived from it would come free mid-run.

The **critical path** is how long the run plausibly _takes_, and it is what the ceiling is
judged against — the number a plan author can act on, because shortening the slowest chain
moves it. Gating admission on the sum instead would refuse a plan of sixteen ordinary agent
steps for being wide, which is a plan-size cap nobody asked for.

A step's own weight is `timeout × attempts + interval × retries`, because **the engine
applies `timeout_sec` to each attempt rather than to the step** — measured, not assumed. A
bound counted once would understate a plan by hours. A `wait_until` step counts the
fifteen-second grace its emitted bound carries, so the number stated and the number the
engine enforces are the same one.

A plan whose slowest chain exceeds the 48-hour ceiling is refused at generation time, with
the arithmetic named. A declared `cairn wait` counts in full, because it holds the run lock
for its whole duration — so a plan's waits are part of its maximum duration and therefore of
the lock's reclaim window ([supervision.md](supervision.md)).

## Measured

Five independent steps, five seconds of work each, real worktrees and the git write mutex
in place: **6.86s against 28.23s** run one step at a time — a ratio of **0.24** where 0.20
is ideal and the engine's own raw parallelism measured 0.33. The mutex adds about **40ms
per git write**, which is the serialisation itself and not overhead around it: two writes
per step against several seconds of work is under two percent, and against an agent step
measured in minutes it is not a cost the fan-out can feel.

Reproduce with `python3 -m scripts.measure_fanout --steps 5 --seconds 5` from this
package's root, with `dagu` on PATH — without it only the mutex half is measured.
