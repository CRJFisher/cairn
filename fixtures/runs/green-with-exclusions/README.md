# `green-with-exclusions`

**I5's regression fixture.** The engine reports run status `4`,
`Succeeded`, with CLI exit 0, over a step whose marker was skipped — because every node
either succeeded or skipped and none failed. Cairn's own emitted pattern cannot produce this
shape: its assertion carries `continue_on: {failure: true}`, so a real exclusion always
leaves a `failed` node and the engine reports `PartiallySucceeded` instead. The workflow here
is therefore hand-written against the node-name grammar, and that is the point — the
extraction is the check that Cairn's pattern still leaves the failed node behind, and a
corpus that could only express the safe shape could not perform it.

## What is here

- `status.jsonl` — the engine's own state file, copied verbatim from a real Dagu
  2.11.0 run.
- `reports/` — this run's own step reports: commit_alpha.json, join_w1.json, mark_alpha.json, work_alpha.json, work_beta.json.
- `recording.json` — the engine version, the run id, and any field set by hand.

Re-record with `python3 -m scripts.record_runs --shape green-with-exclusions`.
