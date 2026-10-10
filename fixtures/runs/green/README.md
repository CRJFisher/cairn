# `green`

Every step verified and nothing excluded. One compacted line, which is what an
attempt leaves when it closes — the other half of the claim that one extraction reads a live
run and a finished one.

## What is here

- `status.jsonl` — the engine's own state file, written by a real Dagu 2.18.2 run, with
  one declared normalisation: the recording machine's interpreter path reads as `python3`.
- `reports/` — this run's own step reports: commit_alpha.json, commit_beta.json, mark_alpha.json, mark_beta.json, work_alpha.json, work_beta.json.
- `recording.json` — the engine version, the run id, and any field set by hand.

Re-record with `python3 -m scripts.record_runs --shape green`.
