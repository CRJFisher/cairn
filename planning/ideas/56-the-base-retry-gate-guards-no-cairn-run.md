# 56 — The base-retry gate guards no Cairn run

**Status: idea.** Found while building [55](../archive/55-engine-processes-already-running.md).

**North star ([principle 3](../../PRINCIPLES.md), [principle 1](../../PRINCIPLES.md)).** A
refusal costs the person a decision about their own machine. It is worth that only where it
protects something.

## The idea

`run start` and the run's first act both refuse a machine whose base configuration arms
DAG-level retry (`base_retry_enabled`). The remedy they name, `supervise base-config
--disable`, also turns retry off for every workflow on the machine that is not Cairn's.

Measured against Dagu 2.18.2, the gate protects no Cairn run. The execution gate admits only a
definition declaring `retry_policy: {limit: 0, interval_sec: 1}`, and that declaration
overrides an armed base configuration. A failed Cairn run stores no retry limit and is never
indexed as a retry candidate ([workflow.md](../../docs/workflow.md)). So a person who retries
their own workflows on purpose must give that up, or not use Cairn, for no gain to Cairn's
runs.

The hazard that remains is a scheduler's: it replays the person's other failed runs. That is
already asserted where it can fire, by `schedule start`.

## Open questions

1. **Whether to drop the gate from `run start` and `lock acquire`.** Doing so leaves
   `schedule start` as the one place the base configuration is judged.
2. **Whether to keep it as a warning instead.** A run could say once that the machine arms
   retry for workflows that do not declare their own, and start anyway.
3. **Whether the measurement should be a test.** It rests on one recording against the pinned
   engine. Re-measuring it at each pin move would keep the claim honest.
