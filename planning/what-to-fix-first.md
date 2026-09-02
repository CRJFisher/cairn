# What to fix first — the order 21 through 24 are repaired in

[21](21-commit-scope.md), [22](22-timed-out-step.md), [23](23-reading-a-broken-run.md) and
[24](24-recovery-economics.md) all came out of one seventeen-step chain-shaped plan, run five
times against task-381. Between them they hold nine changes and three questions recorded rather
than decided. This is the order the nine are done in, what each one depends on, and the two
places where the design as written needs correcting before it is built.

**Serves** the capability surfaces of **Run** and **Report**. No invariant moves: nothing
sequenced here changes what a verdict is, who may write a marker, or what an offer prices.

## The four themes

The nine changes are four separable pieces of work, and the order within each is forced by
what one change teaches the next.

| Theme                                       | Items          | Surface    | What a person meets today                                                          |
| ------------------------------------------- | -------------- | ---------- | ---------------------------------------------------------------------------------- |
| The record states things that did not happen | 23 A, 22 A, 23 B | **Report** | A live run reported dead; a step with four commits reported as never run             |
| A commit claims work that is not the step's  | 21, 24 D        | **Run**    | Another session's uncommitted edit inside `cairn(task_X): …`, under a message about neither |
| A recovery re-pays for proofs already bought | 24 B, 24 A      | **Run**    | ~30 minutes per recovery proving one command against one tree, fourteen times        |
| A killed step leaves no account of itself    | 22 B            | **Run**    | The engine's bound fires, the wrapper never reaches its report write                 |

## The order

### 1 — Liveness is three values, not two ([23 A](23-reading-a-broken-run.md))

First, and on its own: it is the only fault here that tells a person to spend money undoing a
run that is working. `cairn report` from a sandboxed shell answers **failed**, every unfinished
step `orchestrator_died`, next action `rerun` — over a run that is mid-`work` and goes on to
verify and commit. The trigger is the ordinary usage mode, because Cairn is driven from inside a
coding-agent harness and that harness's shell is where `ps` is denied.

The consumers are already written for the repair. `cairn/record/extract.py:827` reads
`alive is False`, and carries `owner_alive: None` as a value of its own;
`cairn/supervise.py:169` maps `None` to `OWNER_UNKNOWN` and reconciles nothing. Both already do
the right thing with "unestablishable". The single place that collapses _I could not look_ into
_it is dead_ is `process_is_alive` in `cairn/liveness.py`, which returns `False` whenever
`process_start_time` returns `None` — including when `ps` failed for permission. Widening that
one return to `bool | None` propagates correctly to both callers without a new branch in either.

### 2 — The cause set, and what the headline names ([22 A](22-timed-out-step.md), [23 B](23-reading-a-broken-run.md))

One pass over the same derivation, rather than touching it twice.

**22 A** ends a record that contradicts itself: `not_reached`, "the step never ran", "contributed
no verified work", over a step that landed four commits, ticked five criteria and passed its
assertion — while the same record's engine node for the same step says `step timed out after
2h30m`. The evidence is already carried: `record/extract.py` copies the engine node into
`nodes[].error`, and `TIMED_OUT` already sits in the frozen cause list at `cairn/verify.py:47`
with nothing deriving it.

**23 B** then ranks over the completed set: the subject of "what to do next" is the first step,
in dependency order, whose gate closed for a cause of its own, and `settle_merge` is emitted
only where the topology holds a merge.

23 B does not strictly need 22 A — it works over `verify_failed` and `reported_failure` alone —
but 22 A completes the set it ranks over, and both live in `cairn/record/extract.py` and
`cairn/record/vocabulary.py`.

**Correction before building 23 B.** "First in dependency order" is not available where the
derivation stands: steps are sorted by step id at `cairn/record/extract.py:830`. The ordering
therefore rests on the engine's node array, which is dependency order for a chain and is not for
a fan-out. Decide which of the two the subject is chosen from, rather than discovering it on a
fan-out run.

### 3 — What a commit is allowed to claim ([21](21-commit-scope.md), [24 D](24-recovery-economics.md))

The highest severity per occurrence in the set, and the only one that writes to the artifact
that outlives the run. `git add --all` over the repository root at `cairn/worktrees.py:592` put
a second session's three-file change into a commit whose message describes none of it.

24 D belongs in the same change because 21 decides it. The preflight refuses only what
`lock_acquire` would refuse, and 21 changes what that refusal means — a per-step scope rather
than one whole-tree gate at the run's first act. Settle 21, and 24 D is the preflight reading
`git status` in the same breath as the socket and the engine.

### 4 — Recovery economics ([24 B](24-recovery-economics.md), then [24 A](24-recovery-economics.md))

**24 B** first: it is cheap and carries no soundness question. Thirteen full-suite executions
past the fault, whose results no gate can read, on a verdict already decided.

**24 A** second, and **with its key corrected**. As written, the shared result is keyed on the
assertion command's bytes alone, on the reasoning that the tree does not move within a run. That
holds on a pure recovery, where every step no-ops. It does not hold on a mixed run: a step doing
new work commits, and every later gate quoting the same command would read a proof taken before
the work it is supposed to assert — which is exactly what 24 A's own rule that sharing never
widens what passes forbids. The key is **(command bytes, HEAD and dirty-state digest)**. A pure
recovery still hits it fourteen times; any commit landing invalidates it.

### 5 — An agent step's own bound ([22 B](22-timed-out-step.md))

Last of the nine, and the largest by a distance: `cairn/providers.py` under a deadline, the
resume, `emit_agent`, the `agent run` parser, a grace constant, four documents and two test
modules. The template is already in the tree — `cairn wait` carries `--timeout` inside its body
and `timeout_sec = bound + WAIT_REPORT_GRACE` on the engine step (`cairn/emitters.py:84-91`).

It ranks last because 22 A already makes the record honest about a killed step. 22 B is what
makes the step's report exist at all, and its value is partly pre-paid by step 2.

## The one call worth reversing

21 sits ahead of the economics on severity: a commit that claims work it did not do outranks a
recovery that is slow. Where dogfood turnaround is the binding constraint instead, 3 and 4 swap
— 24 B alone takes ~30 minutes off every recovery for very little work, and 24 A and B are what
produced the loudest complaint in the record.

## Decided, not built

Three items are questions. Two of them gate work above.

- **May a verify retry once?** ([24 C](24-recovery-economics.md)) A plan-contract ruling. Left
  open, the gate's false-negative rate is the suite's flake rate times fourteen executions per
  recovery. It interacts with 24 A: once results are shared, one flaky execution closes every
  gate quoting that command, so 24 A raises the price of leaving this unanswered.
- **Does a non-default timeout carry its evidence, the way an edge does?**
  ([22](22-timed-out-step.md)) The 9,000 s bound came from the derivation, and no document
  states it.
- **Is an emitted definition a portable artifact or a per-machine build product?**
  ([23 C](23-reading-a-broken-run.md)) `cairn/workflow/build.py:156` resolves `PYTHONPATH` at
  authoring on purpose, and `docs/workflow.md:269` documents that as the design.
  [16](16-release.md) claims the opposite of the result — _"a generated workflow encoding the
  generating machine's absolute paths is author-shaped even when the source is not."_ The two
  documents disagree today, and a fresh machine is the release's own test.
