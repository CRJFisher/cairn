# `agent`

**One real paid agent step**, run against a real provider through `cairn agent run`. It is
the only shape in the corpus that can carry a step's receipts — its cost, its session
identity, its turn count, its transcript and a resume command that names a session which
genuinely existed. Every other shape is a command step, which can never populate them, so
without this one the corpus would prove those fields only in their absent form.

It also carries a real commit and a real diffstat, because its step actually changed a file
in a real repository and `cairn commit` recorded what it changed.

The agent was asked to bring the directory to a state where `note.txt` holds one word, and
told nothing about how — so its own account of itself is the model's, not a script's.

## What is here

- `status.jsonl` — the engine's own state file, copied verbatim from a real Dagu
  2.11.0 run.
- `reports/` — this run's own step reports: commit_alpha.json, mark_alpha.json, work_alpha.json.
- `recording.json` — the engine version, the run id, and any field set by hand.

## What is not a measurement

The report's `rate_limits` carries the recording machine's own account state — how much of a
seven-day window it had used, and when that resets. The shape is kept so the fixture still
looks like a report a real provider writes; the values are replaced with zeroes, because
they are personal to whoever recorded it and nothing in the record reads them. `cairn record`
never looks at that field. Everything else here — the cost, the session identity, the turn
count, the agent's own account of what it did — is exactly what the run produced.

Re-recording this shape **spends money**, unlike every other shape in the corpus, so it is
not swept in by the bare command and refuses without being asked twice:

```text
CAIRN_PAID=1 python3 -m scripts.record_runs --shape agent --paid
```

That is the paid-suite discipline applied to the one paid thing that exists today: the
obvious command cannot spend a penny.
