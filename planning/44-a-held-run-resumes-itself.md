# 44 — A held run resumes itself when its window reopens

**North star ([principle 1](../PRINCIPLES.md), [principle 4](../PRINCIPLES.md)).** A person
queues work and walks away. When the queue stops at the allowance, it should start again when
the allowance reopens, not when the person next looks. [38](38-subscription-headroom.md) left
this unbuilt: _the scheduler's own resume at the reported moment is not built: a `quota_held`
run carries `await_allowance` and the recovery command, and a person or a schedule runs it._

**Status: proposed.** This would have prevented the stall in run
`20261009T224737Z-151a689d`.

**Serves** Run, Schedule and Report.

## What happened

- Run `20261009T224737Z-151a689d` ended at 04:08Z with `implement_16` `quota_held` until
  08:10Z. The window reopened at 08:10Z and nothing happened. A person started the recovery at
  14:35Z: **6 h 25 min of open window unused**, with six steps queued behind it.
- Run `20261010T143556Z-93a9629c` ended at 00:32Z with `review_29` held on the weekly window
  until 2026-10-13T05:00Z. It will sit for 52 hours unless a person returns, and nothing tells
  them that it stopped.

In both cases the record already held everything a resume needs: the moment, the window, and
the exact recovery command.

## What changes

### A — The run's release arms one resume

When `lock release` (the run's `onExit`) writes a record whose next action is
`await_allowance` with a known moment, it arms **one** resume for that run: a detached Cairn
process, `cairn run resume-at --run <id>`, started in its own session the way `run start`
starts the engine. It is not the engine's scheduler. [SKILL.md](../SKILL.md)'s first rule
stands: a scheduler's retry scanner re-executes runs Cairn never wrote, and this must not need
one running.

The waiter:

1. Writes `runs/<id>/resume.json`: the moment, the window, the account it was held on ([43]),
   the command, and its own pid. The view and `cairn report` read this file, so the run reads
   as _resuming at Tue 13 Oct 06:00 BST_ rather than failed and idle.
2. Sleeps until the moment plus `RESET_SETTLE`, then **measures** with the allowance's own
   probe. It never trusts the clock. A window still closed is backed off along
   `RECHECK_BACKOFF`, exactly as a hold inside a step is.
3. Starts `cairn run start --trigger recovery --recovering <id>` once a measurement admits,
   and records the new run id in `resume.json`.

### B — It refuses where a person is needed, and says so

The waiter starts nothing, and records why, when:

- the working tree is dirty (a held step's partial work, or a person's edits): _the resume
  needs a clean tree; settle it and run the recovery_;
- the repository's run lock is held by another run;
- the workflow was re-authored from a different graph since the run;
- the moment is further away than `RESUME_HORIZON` (seven days, one weekly window).

A refusal never retries in a loop. It is one attention item on the run.

### C — The person is told, twice

Principle 2: the run stopping, and the run resuming, are the two facts a person away from the
terminal needs. Cairn sends both through a command the owner configures,
`CAIRN_NOTIFY_COMMAND`, given the sentence on stdin. It is unset by default and never
required. Without it, the facts are in the view and the report only.

### D — It can be called off

`cairn run resume-at --run <id> --cancel` stops the waiter and removes `resume.json`. Starting
any run against the repository supersedes a pending resume, which then exits without acting.

## Acceptance

- A run that ends `quota_held` with a known moment within the horizon starts its own recovery
  after the window measures open, with no person involved, and the new run skips everything
  already landed.
- A window still closed at its reported moment is backed off and measured again, never entered.
- Every refusal in B leaves the run's report saying why the resume did not happen, and starts
  nothing.
- The view and `cairn report` show a pending resume, with its moment, while it waits.
- No engine scheduler is started or required, and `cairn schedule` is untouched.
- With `CAIRN_NOTIFY_COMMAND` set, the command is invoked once when the run stops and once
  when it resumes.

## Touches

`cairn/locks.py` (release arms the resume), a new `cairn/resume.py`, `cairn/skill/cli.py`
(`run resume-at`), `cairn/skill/trigger.py` (detached launch, shared with `run start`),
`cairn/record/extract.py` (`resume.json` in the record), `cairn/view.py`,
`cairn/report/phrases.py`, `capabilities/running.md`, `docs/supervision.md`,
`docs/triggers.md`, `tests/test_resume.py`.
