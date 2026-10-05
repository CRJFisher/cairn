# 22 — A step the engine kills at its bound is recorded as one that never ran

Found live, on the same task-381 dogfood run [19](19-start-friction.md) and [21](21-commit-scope.md) came from. `work_task_381_14` ran for exactly its 9,000 s bound. In that time it landed four task-scoped commits, ticked all five acceptance criteria in its task document with measurements taken on the landed code, and then started a final run of the whole test suite — one it had already run six minutes earlier, and one the step's own assertion runs again after the session ends. The engine's step timeout fired with that suite still running, about ten minutes short of the session's structured report. The assertion then ran over the four commits and passed.

The run record's account of the step: `cause: not_reached`, "the step left no report of this run, so it never ran", "the step contributed no verified work". The same record's engine node for the same step: `status: failed`, `error: "step timed out after 2h30m0.041s (timeout: 2h30m0s): context deadline exceeded"`.

**Serves** the capability surface of **Run** and **Report**. No invariant moves: the record stays the only source of how a run went — which is exactly why it cannot hold two accounts of one step that contradict each other — and a verdict stays something a declared assertion proved.

## What was measured

Against Dagu 2.11.0 and the installed agent CLI, on run `20260828T142443Z-b03b41c1`:

- The engine logged `Step execution timed out` at 06:01:14.920 and `Step started` for the step's assertion at 06:01:14.929 — nine milliseconds later. It did not wait for the step's process to unwind.
- No `work_task_381_14.json` exists in the run's reports directory, and no `.work_task_381_14.json.*.tmp` beside it: the wrapper never reached its report write, not even the atomic-rename half of it.
- The session's transcript ends at the launch of a `Bash` call bounded at 595 s; the step's bound expired 587 s later. 136 `Bash` calls, 77 minutes inside them; the eight full-corpus benchmark arms the criteria demand took about fifty of those.
- The assertion — `tsc --noEmit && vitest run` over `packages/core` — passed in 2m20s over the committed tree.
- No marker was written, no orphaned `claude` or `cairn agent run` process survived, and the run lock was released.
- The task documents name no bound for the step; the 9,000 s came from the original derivation, not from any sentence a validator could check.

## A — The gate reads an absent report as a step that never ran

**Today.** `judge` in `cairn/verify.py` returns `not_reached` when the work node's report is absent, on the premise its own docstring states: _"A cascade-skipped step evaluates no precondition and runs no body, while its assertion still executes... The absent report is what tells the two apart."_ `run_verify_gate` maps the reader's `missing_report` fault to the same cause. The premise is false for a step the engine killed: it ran, it left no report, and its assertion also ran. The record's vocabulary then says "never ran" and "no verified work" over a step with four commits and a passing assertion.

The fact that distinguishes the two cases is already in the record. `record/extract.py` copies the engine's node into `nodes[].error`, where the timeout is spelled out in the engine's own words, and nothing reads it when the step's cause is derived.

**The change.** A cause of its own, `timed_out`, derived at record time from the engine node — `status: failed`, no report, an error the engine spells as a timeout — carrying the bound, the elapsed time, and the assertion's result as a divergence in the shape [19 §D](19-start-friction.md) established: _the engine killed the step at its 9,000 s bound; its assertion passed over the work it left._ `not_reached` is reserved for a node the engine itself recorded as skipped. The report's next action names the bound and says the assertion passed, so a person reading it knows the work is in the tree before deciding whether to re-run.

**What must not change.** An absent report still never opens the gate. A marker an unreported session could earn would survive that session's own failure, which is the thing the gate exists to prevent.

**Touches.** `cairn/verify.py` (`judge`, `run_verify_gate`), `cairn/record/extract.py`, `cairn/record/vocabulary.py`, `cairn/report.py`, `docs/run-model.md`, `docs/verify-gate.md`, `tests/test_verify_gate.py`, `tests/test_run_record.py`.

## B — The session has no bound of its own, so the engine's kill is the only one, and nothing survives it

**Today.** `emit_agent` in `cairn/emitters.py` writes the session's model and tool denials into its body and no timeout: the only bound on an agent step is the engine's `timeout_sec`. `cairn wait` is already emitted the other way — `--timeout <bound>` inside the body and `timeout_sec = bound + WAIT_REPORT_GRACE` on the engine step — so its own deadline fires first, inside the process, with headroom to say what happened.

The cancel path exists and is correct: `cancel_on_termination` turns a `SIGTERM` into `Cancelled`, `_dispatch` turns that into a `cancelled` report, and the write runs under `survive_termination`; `stop_child` gives the provider half a second before killing it. It was never reached. Whether the engine sent `SIGTERM` and then killed the group at its `max_clean_up_time_sec: 5`, or killed the group outright, is not established by the measurements above, and the change does not depend on which.

**The change.** An agent step carries its own bound the way a wait does: the wrapper's own deadline, the hang guard, and an engine `timeout_sec` of `HANG_GUARD + AGENT_REPORT_GRACE`. At the deadline the wrapper stops the provider, then does the one thing [19 §D](19-start-friction.md) built the machinery for: resumes the session once, under the remaining headroom, to ask for its report — a session that has committed its work and ticked its criteria answers that in a turn. Either way the wrapper writes a report before the engine's bound: `done` with the resumed session's account, or `timed_out` with the session id and turns. What changes is who does the killing, and whether a record survives it.

**What must not change.** The hang guard is the bound. A session that would run past it is still stopped there; the grace is for the report, not for the work.

**Touches.** `cairn/emitters.py` (`emit_agent`), `cairn/__main__.py` (`_agent`, the `agent run` parser), `cairn/providers.py` (`run_provider` under a deadline; the resume), `cairn/core.py`, `docs/step-kinds.md`, `tests/test_step_kinds.py`, `tests/test_providers.py`.

## C — Recovery re-runs work that landed and was asserted

**Today.** Only the gate writes a marker, and `run_marker_write` reads the work node's report for the marker's one line (`cairn/marker.py`, `read_step_report(... node_name("work", step_id) ...)`). A step whose gate refused has no marker, so the next run re-runs it as a fresh session — the recovery story, _a step already done re-runs as a cheap no-op because its marker is committed alongside the work_, holds only where the gate wrote. There is no operator path to a marker, and `run_marker_write`'s docstring says why there must not be an unverified one.

**The change.** None here beyond A and B. With B the session leaves its own account and the gate opens on it; with A a person who still meets this case reads that the work is in the tree and the assertion passed, and the re-run is a session that finds its criteria ticked and reports in minutes. The repeat is stated in the report rather than removed by a marker nobody asserted.

## D — Merge resolution runs under the same guard

Emitted `merge land` nodes name their model and carry no bound of their own, and the merge
implementation opens its provider under the hang guard like every other session. A
conflict-resolution session is stopped inside its wrapper, leaves report grace before the
engine's outer merge timeout, and leaves the same durable timeout account as an ordinary
agent step.

## A plan sets no bound on a session

[plan-derivation.md](../docs/plan-derivation.md) derives no time limit for a step from a plan's
sentences. A session is stopped only by the hang guard, a constant in Cairn, so no bound
reaches the engine that nothing in a document could justify.

## Acceptance

- A step the engine kills at its bound is recorded with cause `timed_out`, the bound and the elapsed time; `not_reached` appears only on a node the engine itself skipped, and the engine node and the record never disagree about whether a step ran.
- **Such a step carries no assertion verdict and names no commits, and that is the shape rather than a gap in it.** The gate [24 B](24-recovery-economics.md) puts on every assertion declines it whenever the work node left no report of the run — which is how a killed step is defined — so nothing asserts over what the step left, and its commit node never runs. The record says the work is unproven rather than claiming it holds. A session the wrapper stops at its own bound is the other shape, and does leave a report: that is where a divergence over a stopped step comes from, and it is the common one now that an agent step owns its bound.
- An agent step's session is stopped by the hang guard inside the wrapper, before the engine's bound, and the step's report reaches the run directory carrying the session id and turns — as `done` where the resumed session reported, as `timed_out` where it did not.
- A merge-resolution session has the same model, internal deadline, and durable timeout
  report; no provider call relies only on an engine kill.
- The report's next action for a timed-out step with a passing assertion says the work is in the tree before it says re-run.
- Reproduction: an agent step whose session stops making progress. The run record names the timeout; the reports directory holds the step's report; the engine's own log shows the wrapper stopped the session before the engine did.

## Implementation Notes

**Status: done.** Sections A, B and D are built; C needed nothing beyond them.

A step stopped at a bound is recorded as one that ran and was stopped, with the bound and
the elapsed time beside it, and an agent session is stopped by Cairn rather than by the
engine, so a report survives the stop.

- **A.** The run record reads the engine node's own error — `step timed out after <elapsed>
(timeout: <bound>): context deadline exceeded`, parsed in `cairn/record/engine.py` — and
  `classify_step` gives a failed work node that left no report the cause `timed_out`,
  carrying `timeout_seconds` and `elapsed_seconds` as derived fields. It never overrides a
  cause the gate established on evidence of its own, and never touches a step that reported.
  `not_reached` is left to a node the engine itself skipped. An absent report still never
  opens the gate.
- **B.** `emit_agent` gives an agent step a `timeout_sec` of `HANG_GUARD +
AGENT_REPORT_GRACE` and states no bound in the body, because the bound is Cairn's own
  constant rather than anything a plan says. `run_claude` stops the provider at the hang
  guard and resumes the session once, under what is left of the grace less
  `AGENT_RESUME_MARGIN`, to ask for the account it owes. A session that answers is recorded
  `done` with `timed_out`, the bound, the elapsed time and its turns; one that stays silent
  is `timed_out` with its session id. Either way the report reaches the run directory before
  the engine's bound.
- **D.** A merge resolver names its model, opens its session through the same hang guard,
  and leaves the same `AGENT_REPORT_GRACE` before `MERGE_TIMEOUT`; a resolution stopped at
  the guard records its session id and `resolution.timed_out` in the merge's own detail.
- **The shape of a killed step.** The gate [24 B](24-recovery-economics.md) puts on every
  assertion declines it whenever the work node left no report, so an engine-killed step
  carries no assertion verdict and no commits, and the record says its work is unproven. A
  session the wrapper stops leaves a report, its assertion runs, and the divergence between
  the two accounts is what the record carries. Where that divergence says the assertion
  passed, the report says the work is in the tree before it offers the re-run.
- **Reproduction.** `fixtures/runs/timed-out` is a recorded two-step chain whose first step
  the engine killed at a 2 s bound, with the second never reached behind it.
