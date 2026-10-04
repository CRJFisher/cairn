# 38 — A failed assertion is classified before it halts, and may be remediated once

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

A step declares `timeout` for its work but cannot declare one for its assertion, and the one it
gets is the support bound sized for git housekeeping. Add `verify_timeout` beside `verify`, with
default `SUPPORT_TIMEOUT`, validated finite like every other bound and counted in the run's
admitted maximum ([25](25-execution-admission-and-paid-bounds.md)). An assertion that hits its
bound is reported as `timed_out` in [22](22-timed-out-step.md)'s vocabulary, never as an exit.

## C — Classify before deciding anything

A failed assertion means one of four things, and only one of them is about the work:

| Class            | Evidence                                      | Next move                                            |
| ---------------- | --------------------------------------------- | ---------------------------------------------------- |
| `asserted_false` | a real nonzero exit                           | the work is wrong: remediate (D) or halt             |
| `signalled`      | exit `-1`                                     | the assertion never finished; environment or harness |
| `timed_out`      | the engine's timeout error on the verify node | bound too small (B), or a hang                       |
| `indeterminate`  | the gate could not read an account            | Cairn's own fault                                    |

The record carries the class, and the report's next action follows it. "Run it again as a
recovery once the failure is understood" is the right sentence for `asserted_false`. For
`signalled` and `timed_out` a re-run reproduces the failure, so the action says what to change
first. The evidence tail is the end of the stream that failed, not stderr progress noise
([29](29-operator-and-report-ergonomics.md) owns bounding and labelling it).

## D — An optional remediation step, for `asserted_false` only

A plan may declare `remediate: 1` on a step. The emitter adds two nodes between the assertion and
the mark gate, both static and digest-stamped:

    work_<id> → verify_<id> → remediate_<id> → reverify_<id> → mark_<id>

- `remediate_<id>` runs only when `verify_<id>` was `asserted_false` (a precondition on the class,
  in the same way `verify needed` gates the assertion today). It is an agent step whose prompt
  holds the step's task, the work report's summary, the assertion command, its exit, the bounded
  failing tail, and the commit range the work step landed.
- **It resumes the work session rather than opening a fresh one** where the session id was
  recorded. The work session already knows what it changed and why, and resuming is already used
  to collect a missing report ([19 D](19-start-friction.md)). A fresh session is the fallback
  when there is no session to resume.
- `reverify_<id>` runs the **same assertion command, unchanged**, and the mark gate reads its exit.
  The verdict is still only something the declared assertion proved.
- The record keeps both proofs. The step is `verified` with `remediated: true` and the first
  failing tail is preserved, so a person reading the report sees that the work needed a second
  pass and what it changed.
- Bounded: one attempt per step by default, its cost admitted up front like any agent step
  ([25](25-execution-admission-and-paid-bounds.md)), and charged to the allowance
  ([principle 4](../PRINCIPLES.md)).

**What remediation must not do.** It never runs for `signalled`, `timed_out` or `indeterminate`.
Those are not failures of the work, and an agent sent to "fix" them edits tests until the harness
fault disappears. In this incident, the first failure would have gone to remediation labelled "exited -1". A
session would then have spent its budget on code that was correct. Changes to the files the
assertion command names, or to its test tree, are listed in the record as attention items, because
an edited assertion proves less than the authored one.

**Open.** Whether a declared remediation should also be offered to a person first, as a
[35](35-human-in-the-loop-steps.md) approval gate on `remediate_<id>`. That would be cheap and
keep principle 3 visible.

## Order

A first (done). Then C, because D is unsafe without it and because C alone would have made this
run's report say "killed by a signal" instead of "exited -1". Then B, then D.

**Touches.** `cairn/plan/schema.py`, `cairn/emitters.py`, `cairn/topology.py`, `cairn/verify.py`,
`cairn/assertions.py`, `cairn/record/*`, `cairn/report/*`, `docs/plan-contract.md`,
`docs/verify-gate.md`, tests for each.
