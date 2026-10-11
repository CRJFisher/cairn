# `held-before-session`

**A step the subscription's allowance stopped before it started.** `beta` was admitted
against a weekly window at 98%, past the 95% hold threshold, reopening three days later —
further ahead than a step may wait. It ended `quota_held` in a tenth of a second. No session
opened, nothing in the tree changed, and its report names no session.

Everything after it follows from that one fact. The assertion's own gate declined the
assertion for having nothing to assert, so `verify_beta` is a `noop` carrying
`decision: nothing_to_assert` and the node is skipped. The verify gate closed `quota_held`
with **no divergence**: a divergence weighs a session's account of itself against an
assertion over that session's work, and here there is neither. The record reads `beta` as
excluded, and its attention line is the step's own summary — _held before it started — the
weekly allowance at 98%, reopening …; nothing was done_.

Its pair is [`held-after-limit`](../held-after-limit/README.md), where a session did run.

## What is here

- `status.jsonl` — the engine's own state file, written by a real Dagu 2.18.2 run, with
  one declared normalisation: the recording machine's interpreter path reads as `python3`.
- `reports/` — this run's own step reports, written by real `python3 -m cairn` invocations.
- `recording.json` — the engine version, the run id, and what this shape was seeded with.

## What is not a measurement

Two inputs, both named in `recording.json` under `seeded`, because an allowance that is
spent cannot be recorded — it is somebody's subscription running out.

- **The shared reading.** `<runs root>/.headroom/reading.json` was written before the run,
  in the gauge's own format, with the weekly window at 98% and reopening in three days. The
  admission read off it, the hold, the gates and the record are all the real code.
- **A stub `claude` first on `PATH`,** which fails loudly if anything opens a session. It is
  never invoked, and the step's report naming no session is what says so.

Re-record with `python3 -m scripts.record_runs --shape held-before-session`. It opens no
session and spends none of the allowance.
