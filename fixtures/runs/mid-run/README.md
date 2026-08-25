# `mid-run`

Sampled while the engine was still appending, so the file is **five** snapshot
lines rather than the single compacted one a finished attempt leaves. One step is running and
a sibling has not started.

Read cold, its recording process is gone, so it reads as a crash. Read with a live process
named as its owner — the same bytes, the same status field — it reads as `running`. The test
reads it both ways, and the pair is the proof that the status field was never the evidence.

## What is here

- `status.jsonl` — the engine's own state file, copied verbatim from a real Dagu
  2.11.0 run.
- `reports/` — this run's own step reports: none.
- `recording.json` — the engine version, the run id, and any field set by hand.

Re-record with `python3 -m scripts.record_runs --shape mid-run`.
