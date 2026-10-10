# `crashed`

The orchestrator was killed with `SIGKILL` mid-run. The engine's own record
still says `running` with no finish time and will say so forever, which is exactly what the
extraction must not repeat. The recorded `pid` and `pidStartedAt` are real and belong to a
process that is gone, so a machine that later recycles that identifier still reads it as
dead — the start-time comparison is what makes that true.

## What is here

- `status.jsonl` — the engine's own state file, written by a real Dagu 2.18.2 run, with
  one declared normalisation: the recording machine's interpreter path reads as `python3`.
- `reports/` — this run's own step reports: none.
- `recording.json` — the engine version, the run id, and any field set by hand.

Re-record with `python3 -m scripts.record_runs --shape crashed`.
