# `damaged`

A run the engine and Cairn's own gates recorded as entirely verified — both steps, every
node, every report — whose **evidence was then broken on purpose**. It is the one shape in
the corpus that is not a measurement of what the engine does, because none of these
documents can be recorded: the engine and the gates are built so that nothing produces
them. What it pins is the other half of the claim, which is what the record does when its
evidence is damaged.

## What was broken, and what each break costs

| The break                                                 | What the record refuses                                        |
| --------------------------------------------------------- | -------------------------------------------------------------- |
| the top-level `status` field is gone from `status.jsonl`  | the engine's own reading of the run, so it cannot be `green`   |
| `commit_beta` is recorded twice, the second one `failed`  | the second claim about that one identity                       |
| `work_alpha.json` says `noop` under a node that succeeded | the claim that the marker gate skipped work the engine ran     |
| `work_beta.json` is the account of `work_alpha`           | the whole document: it speaks for a node that did not write it |
| `commit_alpha.json` is cut in half                        | the whole document, as unreadable rather than as absent        |

Five refusals, and **not one of them raises anything**. `alpha` stays `verified` over its
`noop` report, because the engine says its work node ran and its marker gate recorded it.
`beta` stays `verified` with no account of itself at all: its report contributes no summary,
no session and no freshness, because it is another node's. The duplicated `commit_beta` is
read as the worst of its two occurrences, so the two orders of that pair extract to one
record.

**The run is not a clean success.** Every step verified and the verdict is `failed`, because
the engine's own status for the run is unreadable: without it there is no reading that says
whether the run even finished, and `green` over that is the stronger outcome fail-closed
evidence may never produce. The first screen of every rendering counts the refusals, and
each one is a row naming the node it was found under, what disagreed, and its frozen fault.

## What is here

- `status.jsonl` — the engine's own state file from a real Dagu 2.11.0 run of the workflow
  `green` is recorded from, with the two edits above and no others.
- `reports/` — that run's own step reports, with the three edits above and no others:
  commit_alpha.json, commit_beta.json, mark_alpha.json, mark_beta.json, work_alpha.json,
  work_beta.json.
- `recording.json` — the engine version, the run id, and the damage, field by field, so no
  reader has to guess which half of the fixture is a measurement.

Re-record with `python3 -m scripts.record_runs --shape damaged`.
