# 55 — What a run does about engine processes it finds already running

**Status: built.** Found live: the agent driving a run concluded that any running engine
process had to be stopped before the run could start, and acted on it.

**North star ([principle 3](../../PRINCIPLES.md)).** The person is in the loop for decisions
that are theirs. An engine process Cairn did not start belongs to the person, or to their other
projects, and stopping it is never Cairn's decision.

## What happened

Before starting a run, the driving agent treated an engine process that was already running as
something Cairn manages and has to clear. It stopped the process before it started the run.
Cairn needs nothing of the kind: `dagu start` runs a workflow with no server and no scheduler.

## Root cause

The contract is already settled in pieces. [triggers.md](../../docs/triggers.md) and
[running.md](../../capabilities/running.md) both say that a server Cairn did not start is never
stopped, restarted or reconfigured. Run's flow, however, checks only the view, with
`view status`. It says nothing about a scheduler already up. Meanwhile
[SKILL.md](../../SKILL.md)'s first rule warns loudly that a scheduler's retry scanner re-executes
failed runs. An agent that sees a scheduler running has a hazard named and no instruction, so
it improvises.

The hazard does not reach a Cairn run at all. Every definition Cairn emits declares a DAG-level
retry limit of zero. Measured against Dagu 2.18.2, that overrides an armed base configuration:
the failed run stores no retry limit and is never indexed as a retry candidate. What was missing
was the sentence saying so. Several documents said the opposite, that Cairn's own runs were
exposed, and they are corrected with it.

## What changes

### A — Run says what it finds running and stops none

`run start` reads the scheduler's liveness the way `schedule status` does, and only reads it.
Where a scheduler is alive it prints a `scheduler` line naming the process, saying it cannot
replay this run and is left as it is. Where none is found alive it says nothing.

`run start` also refuses a base configuration that arms DAG-level retry, before anything is
written, through the same function the run's first act refuses it through. That refusal names
the file, the limit, and `supervise base-config --disable`.

The proposal asked one question on an unsafe machine with three answers: close the hazard,
start anyway, or not run. Building it showed that "start anyway" was never available, because
the run's own first act refuses that machine. The question keeps the two real answers, and it
is asked about the refusal rather than about the scheduler. Whether Cairn started the scheduler
does not change any outcome, so the line does not depend on it.

The same measurement shows that the base-retry gate itself guards no Cairn run. Whether to
keep it is carried to [56](../ideas/56-the-base-retry-gate-guards-no-cairn-run.md).

### B — The rule is stated where the agent reads it

`SKILL.md`'s first rule gains the sentence: _Cairn never stops, restarts or kills an engine
process it did not start, and a run needs none to be running._ `running.md` step 5 states it
after the view check, step 7 says to repeat the `scheduler` line, and the refusals list puts
the one question for `base_retry_enabled`. `scheduling.md` puts the same question before
`--disable`, and says a scheduler already running is never stopped to make room.

## Acceptance

- `run start` starts and stops no engine process. A run started beside a running server and
  scheduler completes, and both are still running with their original process ids afterwards.
- On a machine whose base configuration arms retry, `run start` refuses before anything is
  written and names the failing property; Run asks one question: disable it, or not run.
- `SKILL.md` and `capabilities/running.md` state the rule and the outcomes.

## Touches

`SKILL.md`, `capabilities/running.md`, `cairn/schedule.py` (`beside_a_run`),
`cairn/skill/cli.py` (`run start`), `cairn/baseconfig.py` (the refusal's wording),
`capabilities/scheduling.md`, `docs/triggers.md`, `docs/supervision.md`, `docs/workflow.md`
(the measurement), `docs/cli-contract.md`, `README.md`, `tests/`.
