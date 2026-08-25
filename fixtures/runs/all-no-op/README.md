# `all-no-op`

A recovery run in which the **real** marker gate found every marker fresh, so
every work node is skipped and each left a no-op report naming the run that did the work.
The repository, the markers and the gate are all real. The engine spells this run exactly as
it spells a clean green, which is why the verdict cannot be read off it.

## What is here

- `status.jsonl` — the engine's own state file, copied verbatim from a real Dagu
  2.11.0 run.
- `reports/` — this run's own step reports: commit_alpha.json, commit_beta.json, work_alpha.json, work_beta.json.
- `recording.json` — the engine version, the run id, and any field set by hand.

Re-record with `python3 -m scripts.record_runs --shape all-no-op`.
