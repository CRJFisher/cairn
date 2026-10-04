# 39 — A failed assertion is classified before it halts, and may be remediated once

Found live, on run `20261004T101104Z-5d0131b9` (`remaining-plans-1`). `work_implement_24` finished
`done` after 26 minutes with sections A–C of [24](24-recovery-economics.md) in the tree. Its
assertion, the whole suite (`python3 -m unittest discover -s tests -t .`), was then terminated by
SIGTERM after 7m53s. The mark gate refused `implement_24`, and the ten steps behind it, across
plans 24, 32, 27 and 28, were never reached.

**Serves** [principle 1](../PRINCIPLES.md) (a pre-planned tree runs hands-free, so one broken
assertion should not cost the rest of the tree without a reason a person can read),
[principle 2](../PRINCIPLES.md) (the report says what actually happened) and
[principle 3](../PRINCIPLES.md) (a failing assertion is an expected event with a defined next move).
No invariant moves: a verdict stays something a declared assertion proved, run unchanged.

## What was measured

- The SIGTERM came from the suite itself. Dagu starts a bare assertion as `sh -c`, which execs
  Python, so the test runner leads its own process group, exactly as a Cairn step does.
  `stop_orphans` used "this process leads its group" as its test for "this process is a step".
  `ExecAndWait.test_until_times_out_with_cause` calls `run_wait_until` in-process, reaches
  `stop_orphans`, and `killpg`s the runner. Reproduced: run as a group leader, that one test exits
  143; run under a shell, it passes.
- Dagu passes the signal on as `exit_code = -1` (Go's `ExitCode()` for a signalled process). The
  mark gate recorded "the assertion exited -1", and gave the verify report the duration of the
  assertion's _precondition_, 0.03s.
- Four `ExecAndWait` tests ran `cairn exec` with the real checkout as the step's working directory.
  Inside a live run, that repository's run lock belongs to the run, so the test step was refused
  `lock_not_held`. The work step reported this and could not fix it.
- The assertion's bound is `SUPPORT_TIMEOUT`, a fixed 600s that a plan cannot change. This
  repository's suite takes longer than that, so the assertion would have failed at its bound even
  without the kill.

## A — Fixed in this change

- `stop_orphans` acts only inside `cancel_on_termination`, which `main` enters once Dagu's identity
  has resolved. A test suite, or any other process that imports Cairn, can no longer signal its
  own group (`cairn/core.py`, `TheGroupSweepBelongsToAStep`).
- The four `ExecAndWait` subprocess tests stand in a temporary root and import Cairn via
  `PYTHONPATH`, like their siblings in `test_step_protocol.py` (`tests/test_step_kinds.py`).
- The assertion's account names a signal kill as one, "ended by a signal after 473s, before it
  exited", and is timed from the moment its gate released it (`RELEASED_AT_KEY`,
  `SIGNALLED_EXIT` in `cairn/assertions.py`).

## B — The assertion's bound belongs to the plan

**Status: done.** A step declares `verify_timeout` beside `verify` (default 600s), validated like
every bound, warned as `derived_timeout` when it differs from the default, emitted as the
assertion's `timeout_sec`, and counted in the run's maximum. The assertion's gate is handed the
bound (`--bound`), so an assertion the engine killed at it is reported as "stopped at its 600 s
bound … the plan's verify_timeout is smaller than the assertion needs". Generator version 7.

## C — Classify before deciding anything

**Status: done.** An assertion a signal ended (exit `-1`, the engine's kill at its bound included)
is the exclusion cause `assertion_interrupted`, never `verify_failed`. It is never filed as a
proof another gate shares, because filed, its failure would dominate and close every later gate
quoting the command. A run halted at one gets the next action `fix_assertion` rather than
`rerun`: change what stopped it first, because running it as it stands meets the same end.

| Reading                         | Evidence                        | Cause                   | Next action              |
| ------------------------------- | ------------------------------- | ----------------------- | ------------------------ |
| the work is wrong               | a real nonzero exit             | `verify_failed`         | remedy (D), else `rerun` |
| the assertion outgrew its bound | exit `-1`, run time ≥ its bound | `assertion_interrupted` | `fix_assertion`          |
| something signalled it          | exit `-1` before its bound      | `assertion_interrupted` | `fix_assertion`          |
| Cairn could not tell            | no readable account             | `gate_indeterminate`    | `rerun`                  |

## D — An optional remedy, for a real nonzero exit only

**Status: done.** A plan declares `remediate: true` on an agent step with an assertion. The step
becomes `work → verify → remedy → recheck → mark → commit`:

- `remedy_<id>` is `cairn agent run … --remedy-of <id> --assertion <cmd>`: the same body, bound
  and price as the work, so offers and preflight count it as the paid session it is. Its gate,
  `cairn verify remedy`, opens it only over an assertion that ran (or shared a proof) and exited
  nonzero, behind a work report of `done` or `noop`. It declines a pass, an assertion that never
  ran, an interrupted one, a step that reported failure or is waiting on a person, and writes the
  decline as the node's own `noop` report. It fails closed.
- The session **resumes the work session** named in the work report (`--resume`), with a fresh
  session as the fallback. It is told the assertion command and its exit, the step's own summary
  and the original task, and to fix the work and never the assertion.
- `recheck_<id>` is the assertion again, verbatim, gated by `verify needed --after-remedy`: it runs
  only after a remedy reported `done`. The mark gate reads its exit (`--recheck-exit`) only then,
  so a second asking with no session between can never pass a flaky command.
- The record's step carries `remedy` (status, what it said, its cost, the first exit, the session it
  resumed). `assertion_exit` is the recheck's, the remedy's cost counts in the run's budget, and the
  report shows all of it. Record version 4.

## Still open

- **An edited assertion.** A remedy that changes the files the assertion runs proves less than the
  authored assertion did. The prompt forbids it; nothing yet detects it. The commit step knows
  which paths moved, so an attention item naming test files the remedy touched is the next step.
- **The failing output in the prompt.** The remedy runs the command itself rather than being handed
  the engine's log tail, which avoids [29](29-operator-and-report-ergonomics.md)'s noisy tail but
  costs one more run of the assertion inside the session.
- **Asking first.** Whether a declared remedy should be offered to a person, as a
  [35](35-human-in-the-loop-steps.md) approval gate on `remedy_<id>`.
- **The suite this incident ran.** This repository's own whole-suite assertion takes about 844s;
  the plan that runs it needs `verify_timeout` stated, and the suite itself mutates the repository
  it stands in, which [27](27-cross-plan-git-and-lock-isolation.md) owns.
