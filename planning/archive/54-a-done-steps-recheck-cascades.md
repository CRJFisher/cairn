# 54 — A done step's re-assertion that cannot run halts every step after it

**Status: built.** Found live, on a recovery run of a chain plan.

**North star ([principle 2](../../PRINCIPLES.md), [principle 3](../../PRINCIPLES.md)).** A
recovery re-proves what is done and does what is not. Today a done step whose assertion cannot
even start reads as a step whose work failed, and the skip carries down the chain into steps
that never started.

## What happened

A recovery reached a step whose marker was fresh. Its work node no-oped, and its assertion
re-ran, which is the recovery guarantee ([verify-gate.md](../../docs/verify-gate.md)). The
assertion exited 127: its worktree was freshly created and had none of the project's installed
dependencies, so the test runner the assertion called did not exist. The gate closed
`verify_failed`. The marker and commit were skipped, and because the commit sits in a chain
position with no `continue_on`, the skip cascaded into every dependent step. Those steps had no
marker, never started, and were recorded `not_reached`.

## Root cause

Two faults combine:

1. **A worktree has only what git tracks.** `worktree setup` creates a checkout. Anything the
   project installs or generates outside git, such as dependency directories, is absent. An
   assertion written for the primary checkout cannot run there. The plan contract has no place
   to say how a worktree is made ready.
2. **"Could not run" and "ran and failed" share one verdict.** `judge` in `cairn/verify.py`
   reads every nonzero exit, other than a signal, as `verify_failed` with a divergence. Exit
   126 and 127 are the shell saying the command was never executed. That says nothing about
   the step's work, in the same way `assertion_interrupted` says nothing about it.

The cascade itself is correct. A chain dependent must not run over a predecessor whose state
is unproved ([emitters.py](../../cairn/emitters.py), `emit_commit`). Leaving the marker in place
while its re-assertion fails would weaken the gate, so the fix is not to keep the marker.

## What changes

### A — A plan states how a worktree is made ready

The plan gains an optional `prepare` command per plan, run by `worktree setup` after it creates
or reuses a worktree and before the work node. Its failure fails setup with its own cause and
the command's output, so the halt names the real problem.

### B — An assertion that never executed is its own cause

`judge` reads exit 126 and 127 as `assertion_unrunnable`, with no divergence and a summary that
quotes the assertion's last stderr line. The record and report show it as an environment fault
to settle, not as the step's work failing.

### C — A done step that no longer holds is said plainly

Where the step's report is a marker `noop` and its re-assertion fails, the summary says that a
step already recorded done no longer passes over the current tree. It names the commits since
the marker. The cause stays `verify_failed`, and the cascade stays.

## Acceptance

- A plan with `prepare` runs it in every worktree before work, and a failing `prepare` halts
  setup with its own cause.
- An assertion exiting 126 or 127 is recorded as `assertion_unrunnable`, with no divergence.
- A fresh-marker step whose re-assertion fails is reported as a done step that no longer holds,
  and its dependents are recorded as stopped behind it.
- `docs/plan-contract.md` and `docs/verify-gate.md` state all three.

## Touches

`cairn/plan`, `cairn/worktrees.py` (`setup_worktree`), `cairn/verify.py` (`judge`),
`docs/plan-contract.md`, `docs/verify-gate.md`, `docs/topology.md`, `tests/`.
