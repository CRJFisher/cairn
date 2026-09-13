# `timed-out`

The engine killed `work_alpha` at its 2-second bound before any report was written. Its
node is a plain **`failed`** whose only account of the kill is its error —
`step timed out after 2.001s (timeout: 2s): context deadline exceeded` — and the node is a
raw `sleep`, not a `cairn exec`, because the wrapper would have caught the engine's signal
and left a `cancelled` report; the fault this shape pins is a step that left none.

Both of `alpha`'s gates then read that same absence. Its assertion's own gate declined the
assertion — a step that left no report has nothing to assert — so `verify_alpha` is
**skipped**, and the mark gate closed `not_reached` over the missing report. `work_beta`
was skipped behind the halt and its gates did the same.

The record reads the kill over the gate's word for `alpha` — the step is `timed_out`, with
the bound that fired and how long it had run — and the halt over the engine's `skipped` for
`beta`, which is `not_reached`. The subject of what to do next is `alpha`, never `beta`.

**A killed step carries no assertion verdict**, and that is the shape rather than a gap in
it: the gate that decides whether an assertion runs turns on the same absent report the
kill causes, so nothing ever asserts over what such a step left. A session the wrapper
stops at its own bound does leave a report, and that is where a divergence over a stopped
step comes from ([22 B]).

## What is here

- `status.jsonl` — the engine's own state file, copied verbatim from a real Dagu
  2.11.0 run.
- `reports/` — this run's own step reports: mark_alpha.json, mark_beta.json,
  verify_alpha.json, verify_beta.json.
- `recording.json` — the engine version, the run id, and any field set by hand.

Re-record with `python3 -m scripts.record_runs --shape timed-out`.
