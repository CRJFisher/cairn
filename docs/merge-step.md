# The merge step

A wave's branches land on the parent branch one at a time, in an order the dependencies
bound and the evidence chooses. A merge that meets no conflict is a command. A merge that
meets one is a question about intent, and that question — and only that question — goes to
a coding agent. What the agent says afterwards is not what decides: the merge is proven
against git, or the run stops with the conflict preserved for a person to settle.

## Why this step uses judgement

Everything else in the git layer is deterministic. Merging is not: a conflict between two
steps' edits is a question about what each was for, and the plan is the only place that
intent is written down. What no command can catch is a resolution that is clean but
semantically wrong.

So the deterministic layer around the agent exists to bound the damage to exactly that
case. `git merge` is attempted first and a clean landing never reaches an agent at all. A
conflicted one is handed over with the conflicted files named, and whatever comes back is
checked against the repository before the next slot is allowed to run.

## The rules

- **One at a time.** Slots are chained, never fanned out. That orders the landings and
  serialises the parent branch's writes in a single mechanism.
- **The dependency order is a bound, not a script.** A branch never lands before what it
  depends on. Within that bound the slot chooses on the evidence in front of it.
- **Resolve or halt — never abort.** An unresolvable conflict is left in the tree exactly
  as git left it, and the step reports failure naming the conflicted files. The preserved
  state is what a person settles before re-running. An abort does not converge: the branch
  would still be unmerged, so the next run re-attempts the same merge and stops again with
  nothing kept for anyone to look at.
- **The merge's success is proven, not reported.** Every landing asserts that the branch is
  an ancestor of the parent, that no merge is still in progress, that the tree is clean,
  and that no file the merge changed carries a conflict marker.
- **Excluded branches are named.** A branch with no verified work is not merged, and its
  cause travels into the record rather than disappearing into a merge that reports
  "already up to date".

## Where each rule lives

| The rule                                                  | Where it is                                          |
| --------------------------------------------------------- | ---------------------------------------------------- |
| the prompt, the ordering, the proof, the candidate survey | `cairn/merge.py`                                     |
| how many slots, their bound, and which agent resolves     | `cairn/topology.py`                                  |
| the body each node runs                                   | `cairn/emitters.py`                                  |
| the numbers                                               | `cairn/plan/schema.py`                               |
| the exclusion causes                                      | `cairn/verify.py` ([verify-gate.md](verify-gate.md)) |
| the wave's census, before any slot moves a tip            | `cairn/wave.py` ([workflow.md](workflow.md))         |

## The nodes a wave's landing becomes

The topology derives these nodes and the emitters give them bodies;
[workflow.md](workflow.md) assembles them into a file.

```yaml
- name: merge_w2_1 # the slot: it chooses, lands, and proves what it landed
  depends: [join_w2]
  run: python3 -m cairn merge land --slot 1 --provider claude
    --branch step/keymap_reader --branch step/theme_reader
  working_dir: ${CAIRN_REPOSITORY}
  timeout_sec: 3600
  retry_policy: { limit: 0, interval_sec: 1 }

- name: verify_merge_w2_1 # the proof, in a process of its own
  depends: [merge_w2_1]
  run: python3 -m cairn merge verify --merge merge_w2_1
    --branch step/keymap_reader --branch step/theme_reader
  working_dir: ${CAIRN_REPOSITORY}
  timeout_sec: 600
  retry_policy: { limit: 0, interval_sec: 1 }
```

**Every slot carries the whole candidate list.** Which branch a slot lands is decided at run
time, because the evidence it decides on — a read-only merge between committed tips — does
not exist when the workflow is written. A slot with nothing left to land is a no-op, which
is how a wave with an excluded branch still reaches its prune.

**No node in the chain carries `continue_on`, in either spelling.** A halt has to stop the
slots behind it and the prune after them. A flag here would let the next slot write over a
conflicted index.

**A slot is bounded like the session it may have to pay for**, plus the git work on either
side of it. At a support step's bound the engine would kill a resolution mid-merge, leaving
behind exactly the unsettled tree the halt path exists to produce only deliberately; at the
session's own bound the mutex wait and the merge in front of it would come out of the
session's budget. Its proof is priced as the support step it always is, because it runs git
reads and never a session.

**The resolver is the plan's own default agent.** A plan whose steps are all commands still
gets one, because a conflict is a question about intent whatever produced it.

**The slot takes the git write mutex around its own `git merge` and releases it before the
agent runs.** The mutex's wait is five minutes and a session runs to an hour, so holding it
across one would turn every contender into a failure rather than a wait. Nothing else in
the run writes the parent branch while a slot holds it: the slots are chained, the join is
upstream, the prune is downstream, and the run lock excludes other runs.

## What a slot does, in order

1. Halt if a merge is already in progress, if anything in the environment would redirect
   git, or if the repository is not on the parent branch.
2. Survey every candidate: does the branch exist, and does it hold a commit the parent does
   not. A branch with nothing to land is asked _why_ — see below.
3. If nothing is left to land, report a no-op naming every exclusion.
4. Predict, then refuse a predicted conflict in a file no branch of the wave changes. This
   happens **before any agent is paid for**.
5. Land the lightest branch: `git merge --no-ff`, under the write mutex.
6. On a conflict only, hand the named files to the agent.
7. Prove what landed, whatever the agent said.

## What a slot may land, and why it may not

**A branch lands only where the gate recorded its step.** A step's own session can commit
inside its worktree, so a branch can carry commits over a gate that closed — and the commit
count alone cannot tell those from work the gate approved. Landing them would land exactly
the unverified work the gate refused to record.

A branch with nothing left to land needs no permission, because there is nothing to permit.
Ancestry cannot tell such a branch apart from an excluded one — **a branch whose step never
committed is already an ancestor of the parent** — so the gate's report is what separates
"the gate declined" from "an earlier slot, or an earlier run, already landed this".

| What is true of the branch                  | Disposition        | Cause                                                 |
| ------------------------------------------- | ------------------ | ----------------------------------------------------- |
| holds new commits, and the gate recorded it | `mergeable`        | —                                                     |
| holds new commits, and the gate did not     | `excluded`         | the gate's own value, quoted                          |
| the branch does not exist                   | `excluded`         | the gate's value for a step nothing durable shows ran |
| nothing to land, and the gate declined      | `excluded`         | the gate's own value, quoted                          |
| nothing to land, and no decline recorded    | `nothing_to_merge` | —                                                     |

**The merge mints no exclusion cause of its own.** That vocabulary is frozen in
[verify-gate.md](verify-gate.md), which is the one place its values are named, and a second
spelling for one fact would put two words in the record for one event. Where the gate left
nothing readable, the merge takes the value that gate would itself have used, rather than
inventing one.

## What the prediction can say, and what it may decide

`git merge-tree --write-tree` performs a three-way merge between two committed tips without
touching a ref, an index or a working tree. It is used for two things: to order the chain so
the branch that conflicts most with the rest of the wave lands last, and to refuse a
predicted conflict set covering files no step of the wave changes — which is a plan defect
rather than a merge problem.

Each remaining branch is predicted against every other **and against the parent**. The
pairwise predictions alone can only ever name a file the wave itself changed, because every
branch forks from the parent — so a path no step owns can appear only where the parent is one
of the operands, which is the whole of what "the parent moved for a reason outside the plan"
means. Both are recomputed per slot over the branches that remain.

What a step owns is **derived**, not declared: the plan contract has no field in which a step
claims the files it writes, so ownership is what its branch actually changed against the
point it forked from, counting both names of a rename. A `writes:` declaration on the step
schema would make this a statement rather than an inference, and that belongs to the plan
contract rather than here.

The prediction is **advice**, and the proof takes no argument derived from it.

| What git does                            | The prediction                             |
| ---------------------------------------- | ------------------------------------------ |
| exits 0                                  | `clean`                                    |
| exits 1 with a tree id on the first line | `conflicted`, in the files it names        |
| exits 1 with nothing on stdout           | `unavailable` — a ref it could not resolve |
| exits 128                                | `unavailable` — unrelated histories        |

**Measured against git 2.42.1, and it contradicts the documentation.** `git-merge-tree(1)`
says an error exits "something other than 0 or 1"; a ref that does not resolve exits **1**,
which is the same status a conflict exits. Reading the status alone would report a broken
ref as a conflict in every file. The tree id on stdout is the discriminator.

Two more measured constraints. An **empty conflicted-file list is not a clean merge** —
git's own note is that a merge can conflict without any individual file conflicting — so the
outcome is read from the status and the file list is only ever detail. And with `--stdin`
the exit status is 0 for conflicted merges too, so pairs are never batched.

`unavailable` is a third answer and never a verdict. A prediction git declined to make
orders the chain by name and refuses nothing.

## What "no leftover conflict markers" means

The scan reads the **merge commit**, not the working tree: an agent can tidy a file after
committing it, and what reaches history is what every later run and every later merge
carries. It is scoped to the paths the merge itself changed.

A file is flagged only when it holds **both** an opening and a closing marker, each with the
label git writes after it. The separator alone is not evidence: `=======` on its own line is
a Markdown setext underline, and a repository whose committed content legitimately discusses
merges would otherwise redden every run.

## The halt, and the two ways a person settles it

A slot that cannot resolve leaves `MERGE_HEAD`, the conflicted index and the marked files
exactly as git left them, and reports failure naming the files. Nothing is aborted, reset or
checked out.

**How a person finds out.** The slot's node fails in the engine, and because no node in the
chain carries `continue_on` the run itself fails with it. The slot's step report, under the
run's `reports/` directory, names the branch, the parent, every conflicted file, and what to
settle. Settling means completing the merge in the repository by hand and re-triggering the
workflow — not re-invoking `cairn merge land`, which takes its identity from the engine and
refuses outside a run.

The next run stops before spending anything: taking the run lock halts on an unresolved
merge, which is what forces the state to be settled rather than run over. Both completions
converge.

- **Resolve the conflict and commit.** The branch becomes an ancestor, so every slot before
  the halt is a no-op and nothing lands twice.
- **Complete the merge taking the parent's content.** The branch is still an ancestor, so
  the run converges — and that the branch contributed nothing is what the record shows, on
  this run and every future one.

Deleting the branch is not one of them. It would take the step's committed marker with it,
so the next run re-runs the step, rebuilds the branch and stops on the same conflict.

**A proof that fails after the merge landed is settled differently.** `repository_dirty` and
`conflict_markers_committed` are reached only once the merge commit is already on the parent
branch, so the branch is an ancestor and a re-run finds nothing left to land and never looks
again. The slot names that commit in its follow-up work, and settling it means rewriting or
reverting it before the next run.

## The prompt a resolution is given

Reproduced here verbatim; `cairn/merge.py` states it once and this is the only other place
it appears.

> A merge of the branch {branch} into {into} is in progress in this repository and has
> stopped on a conflict. These files are conflicted:
>
> &nbsp;&nbsp;{one conflicted path per line}
>
> Resolve them so that the intended change from both sides survives. Read the commits on
> each side — `git log --merge -p` shows them — and work out what each was for. The two
> sides are steps of one plan, so both intentions are meant to hold at once.
>
> Then: stage the resolved files and complete the merge with a commit. Leave no conflict
> marker anywhere, in any file, including any you edit by hand.
>
> If a resolution is not clearly correct — if you cannot tell what one side meant, or the
> two intentions genuinely contradict — stop and report failure, naming the files and what
> disagrees. Leave the merge exactly as you found it. Do not run `git merge --abort`, `git
reset` or `git checkout` to undo it: the half-finished merge is what a person needs to
> settle this, and discarding it only means the next run stops in the same place.
>
> Never resolve a conflict by taking one side wholesale to make the merge pass.

The prediction is deliberately **not** in it. Handing an agent a prediction invites it to
trust that over the tree in front of it, which is the one thing the proof exists to catch.

## What this hands forward

- **The run record** reads each slot's report for what landed, what was excluded and why,
  and whether a session was paid for. Every exclusion cause is a value from the gate's
  frozen set.
- **The preflight** refuses a merge-chain node carrying `continue_on`, and asserts that
  every slot's body is one quoted invocation.
- **The divergence rate** — how often a resolution is clean but semantically wrong — needs
  real sessions against real repositories, and the suite here runs none: a test that spends
  money and depends on what a model decided that day cannot gate a commit. The instrument is
  in place, because every landing records what it changed and what proved it. What is owed
  is a second suite that runs deliberately and measures it.

## Measured against git 2.42.1

| Fact                                                           | Consequence                                                   |
| -------------------------------------------------------------- | ------------------------------------------------------------- |
| a ref that does not resolve exits 1 with empty stdout          | the exit status alone cannot tell a prediction from a failure |
| an empty conflicted-file list is not a clean merge             | the outcome is read from the status, never from the list      |
| `--stdin` exits 0 for conflicted merges                        | pairs are predicted one invocation at a time                  |
| a branch with no commits is already an ancestor                | "has anything to land" is counted, not asked of ancestry      |
| `--no-ff` still reports "already up to date" for an ancestor   | a no-op is a no-op however it is spelled                      |
| a re-attempt over an unresolved merge is refused by git itself | the preserved halt is what stops a run running over it        |
