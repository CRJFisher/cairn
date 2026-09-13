# 23 — Reading a broken run: two answers the record gave that were wrong

Found live on the task-381 dogfood runs [21](21-commit-scope.md) and [22](22-timed-out-step.md) came from. Both faults are in the reading layer: the runs themselves behaved exactly as their gates decided, and the account a person was given of them did not.

**Serves** the capability surface of **Report**. No invariant moves: the record stays the only source of how a run went — these are two places where what it says is not what happened.

## A — A reader that cannot inspect processes reports a live run as dead

**What happened.** `cairn report --run …` was invoked mid-run from a coding-agent harness shell whose sandbox denies process inspection (`ps` itself fails with `operation not permitted`). The report's verdict: **failed**, with _"The process running this run is gone, so what it was doing when it died is not recorded"_, every unfinished step `not_reached` or excluded with `cause: orchestrator_died`, and next action `rerun` — over a run whose engine process was alive, mid-`work` step, and which went on to verify and commit that step's work. The same command from an unsandboxed shell, a minute later, said `running` with nothing needing attention.

**Today.** The liveness check reads a failure to find the orchestrator's process as the process being gone. "I could not look" and "I looked and it is not there" produce the same answer, and the answer carries the most alarming vocabulary the report has.

**The change.** Liveness returns three values, not two: alive, gone, and **unestablishable**. A probe that fails for a reason other than "no such process" — permission denied, above all, because a sandboxed reader is the ordinary case when Cairn is driven through a coding-agent harness — reports the run as running-as-far-as-the-record-shows, says in one line that the process could not be checked and why, and never writes `orchestrator_died`, which is reserved for a probe that succeeded and found nothing. The report's own note already distinguishes _"The engine's own record still says running, and this report does not"_ — that sentence is the tell: when the reader's probe failed and the engine says running, the engine is the better witness.

**What must not change.** A probe that succeeds and finds no process still says so plainly; the reconciler still gives a genuinely dead run a terminal status.

**Touches.** `cairn/liveness.py`, `cairn/report.py`, `docs/report.md`, `tests/`.

## B — After a cascade, the headline names a bystander and prescribes a merge the plan does not have

**What happened.** A recovery run's first assertion failed — `verify_task_381_2`, one flaky test — and its gate refused, so thirteen downstream work steps skipped and every downstream gate closed. The report's reading: _"Settle the work that did not land, then run the plan again. It concerns **task_381_10**."_ with `action: settle_merge`. Three things wrong in two lines: the step it names is a bystander eleven nodes downstream of the fault; the one step whose gate recorded the real cause (`verify_failed`, "the assertion exited 1 over a step reporting 'noop'") appears nowhere in the headline; and `settle_merge` prescribes settling a merge in a chain-shaped run that has no worktrees and no merges — there is nothing to settle and re-running is the whole remedy. Below the headline, fourteen near-identical "contributed no verified work" attention items bury the single line that differs.

**Today.** The next-action derivation picks a subject from the excluded set without ordering by position in the chain or by cause specificity, and maps exclusion to `settle_merge` regardless of whether the topology holds a merge.

**The change.** The subject of "what to do next" is the **first** step, in dependency order, whose gate closed for a cause of its own (`verify_failed`, `reported_failure`, `timed_out`) — never one closed as `not_reached`/`gate_indeterminate` downstream of it. The action names what that cause needs: a failed assertion says so, quotes the assertion's tail, and offers the re-run; `settle_merge` is emitted only for a run whose topology contains a merge node. Cascade exclusions collapse into one line — _"and N more steps were never reached behind it"_ — instead of N items that outnumber the fault.

**What must not change.** Every exclusion stays in the record in full; the collapse is presentation, and the per-step section still lists each one.

**Touches.** `cairn/record/extract.py` (next-action derivation), `cairn/report.py` (attention ordering and the cascade collapse), `docs/report.md`, `docs/run-model.md`, `tests/test_run_record.py`.

## The bucket

| #   | Symptom                                                                                                                                                  | Where it lives      | State                                                                                              |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------- | -------------------------------------------------------------------------------------------------- |
| A   | A sandboxed reader turns a live run into `orchestrator_died`                                                                                             | `cairn/liveness.py` | open                                                                                               |
| B   | The cascade's headline names a bystander, prescribes `settle_merge` on a merge-less chain                                                                | `cairn/record/`     | open                                                                                               |
| C   | The emitted definition bakes `PYTHONPATH` as this machine's absolute path, so it runs nowhere else — re-authoring on the target machine is the only path | `cairn/workflow/`   | decided: a definition is a per-machine build product, authored where it runs ([16](16-release.md)) |

## Acceptance

- `cairn report` over a live run, from a shell that may not inspect processes, says the run is running and that liveness could not be established — and never `orchestrator_died`.
- After a one-gate cascade, the report's next action names the step whose gate recorded the fault, quotes its cause, and counts the rest as unreached behind it; `settle_merge` appears only where the topology has a merge.
- Reproduction for A: run `cairn report` on a live run under a sandbox that denies `ps`-class syscalls. Reproduction for B: a chain of three steps whose middle assertion fails; the report must name the middle step.
