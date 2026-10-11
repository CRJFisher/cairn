# `held-after-limit`

**A step whose session met the limit and could not be resumed in time.** `beta` was admitted
against an open 5-hour window, opened its session, and met the weekly limit mid-turn. That
window reopens three days later — further ahead than a step may wait — so the step ended
`quota_held` holding the id of a session that ran.

Because that session did work, the step is read the opposite way to its pair. The assertion
ran over what the session left and passed, and the verify gate recorded the **divergence**
between the two accounts: a step that stopped before reporting, and an assertion that found
its end state holding. The record carries that divergence in the attention list beside the
exclusion, which is the truthful account of a step stopped without reporting.

Its pair is [`held-before-session`](../held-before-session/README.md), where no session ever
opened. What separates the two is exactly that: whether a session was ever opened.

## What is here

- `status.jsonl` — the engine's own state file, written by a real Dagu 2.18.2 run, with
  one declared normalisation: the recording machine's interpreter path reads as `python3`.
- `reports/` — this run's own step reports, written by real `python3 -m cairn` invocations.
- `recording.json` — the engine version, the run id, and what this shape was seeded with.

## What is not a measurement

Two inputs, both named in `recording.json` under `seeded`, because a real account meeting a
real limit mid-session is not something a recording can arrange.

- **The shared reading.** `<runs root>/.headroom/reading.json` was written before the run,
  in the gauge's own format, with the 5-hour window at 10% so admission passes.
- **A stub `claude` first on `PATH`,** which speaks the provider's own stream protocol and
  nothing else: one `rate_limit_event` rejecting the weekly window, then a `result` ending
  `blocking_limit`. Cairn's reading of that stream, the window it writes into the shared
  reading, the hold, the gates and the record are all the real thing.

Re-record with `python3 -m scripts.record_runs --shape held-after-limit`. It reaches no real
provider and spends none of the allowance.
