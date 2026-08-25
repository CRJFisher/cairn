# `red`

A step fails and everything behind it is recorded at status **3**, `aborted`, with
`error: "upstream failed"`. That is how the engine spells a cascade, and it is what tells a
step that will never run from one that has not started yet.

## What is here

- `status.jsonl` — the engine's own state file, copied verbatim from a real Dagu
  2.11.0 run.
- `reports/` — this run's own step reports: work_alpha.json.
- `recording.json` — the engine version, the run id, and any field set by hand.

Re-record with `python3 -m scripts.record_runs --shape red`.
