# `agent`

**One real agent step**, run against a real provider through `cairn agent run`. It is
the only shape in the corpus that can carry a step's receipts — its session
identity, its turn count, its transcript and a resume command that names a session which
genuinely existed. Every other shape is a command step, which can never populate them, so
without this one the corpus would prove those fields only in their absent form.

It also carries a real commit and a real diffstat, because its step actually changed a file
in a real repository and `cairn commit` recorded what it changed.

The agent was asked to bring the directory to a state where `note.txt` holds one word, and
told nothing about how — so its own account of itself is the model's, not a script's.

## What is here

- `status.jsonl` — the engine's own state file, written by a real Dagu 2.18.2 run, with
  one declared normalisation: the recording machine's interpreter path reads as `python3`.
- `reports/` — this run's own step reports: commit_alpha.json, mark_alpha.json, work_alpha.json.
- `recording.json` — the engine version, the run id, and any field set by hand.

## What is not a measurement

The report carries the recording machine's own account state — how much of each window it had
used, and when that window resets. The shape is kept so the fixture still looks like a report a
real provider writes; the provider's `rate_limits` values are zeroed and the reset moments Cairn
reads are nulled, because they are personal to whoever recorded it. The record states a nulled
reset as not known. Everything else here — the session identity, the turn count, the agent's
own account of what it did — is exactly what the run produced.

Re-record with `python3 -m scripts.record_runs --shape agent`. It opens one real session and
spends the subscription's allowance, so a plain `python3 -m scripts.record_runs` leaves it
alone. The step names `sonnet`: under `--permission-mode auto` a `haiku` session asks before
writing a file, and under `-p` nobody can answer, so the step reports that it was not allowed
to do the work.
