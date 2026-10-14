# 55 — What a run does about engine processes it finds already running

**Status: proposed.** Found live: the agent driving a run concluded that any running engine
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

The hazard is real but narrow. A running scheduler re-executes a failed Cairn run only if the
machine's base configuration arms DAG-level retry. `schedule start` asserts that the machine is
safe, but only when Cairn is the one starting the scheduler.

## What changes

### A — Run reads every engine process and stops none

Run step 5 reads `schedule status` beside `view status`, and both only read. The answers have
these outcomes:

- **No scheduler**, or a scheduler Cairn started: say nothing.
- **A scheduler Cairn did not start, on a machine the safety assertion passes:** the run starts,
  and the reply says in one line that a scheduler is up and cannot replay this run.
- **A scheduler Cairn did not start, on a machine the assertion fails:** the run is not started
  until the person answers one question. They can close the hazard in the base configuration,
  start anyway, or not run. Cairn never stops the scheduler itself.

### B — The rule is stated where the agent reads it

`SKILL.md`'s two rules gain the sentence: _Cairn never stops, restarts or kills an engine
process it did not start, and a run needs none to be running._ `running.md` step 5 carries the
three outcomes above.

## Acceptance

- `run start` starts and stops no engine process. A run started beside a running server and
  scheduler completes, and both are still running with their original process ids afterwards.
- With a scheduler up on an unsafe machine, Run asks one question before starting and names
  the failing property.
- `SKILL.md` and `capabilities/running.md` state the rule and the three outcomes.

## Touches

`SKILL.md`, `capabilities/running.md`, `cairn/schedule.py` (expose the safety assertion as a
read-only check), `cairn/schedule_cli.py`, `tests/`.
