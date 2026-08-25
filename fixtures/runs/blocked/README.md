# `blocked`

A human decision is owed. Every node succeeded and the engine reports a clean
success, so the block exists only in the step's own report — which is the whole reason the
reports are the primary source.

## What is here

- `status.jsonl` — the engine's own state file, copied verbatim from a real Dagu
  2.11.0 run.
- `reports/` — this run's own step reports: commit_alpha.json, mark_alpha.json, work_alpha.json.
- `recording.json` — the engine version, the run id, and any field set by hand.

Re-record with `python3 -m scripts.record_runs --shape blocked`.

## What is not a measurement

These fields were set after the run rather than produced by it — `work_alpha`: needs_user_decision, summary — because no free provider can produce an agent that asks for a decision. Every other byte here is what the engine and Cairn actually wrote.
