# 46 — A finished step whose every report was refused is asked once more, not failed

**North star ([principle 2](../PRINCIPLES.md), [principle 3](../PRINCIPLES.md)).** A step that
did its work should not be recorded as a failure because its account came out malformed. The
work is the person's; the report is how they learn about it.

**Status: A and B built; C and D open.** Found in run `20261011T154219Z-f8e59892` of
`remaining-plans-3`.

**Serves** Run and Report.

## What happened

`review_29` (Sonnet, led by `/code-review low --fix`) ran its review, found no correctness bugs
in the diff, checked that the tree was clean, and concluded that the end state held. It then
filed its report five times. Every time it closed the `summary` parameter with `</summary>`
instead of ending the parameter, so `follow_up_work` and `needs_user_decision` landed inside the
summary string. The provider refused each report and, after five, ended the session with
`terminal_reason: structured_output_retry_exhausted` and exit 1.

Cairn read that as `provider_protocol` and failed the step. The rescue that asks a session again
for its report did not run, because it fired only for a session that exited 0 with no report.
The run halted with `pause_after_29`, `implement_20` and `review_20` unreached, over a review
that had nothing to fix.

The failure mode is old. [step-protocol.md](../docs/step-protocol.md) already recorded seven
review sessions with `follow_up_work` written inside `summary`. In run
`20261009T224737Z-151a689d`, `review_33` filed four refused reports before one was accepted.

## What changes

### A — Exhausted retries are an absent report, and are rescued (built)

`ended_without_reporting` is true for a session with no accepted report that either exited 0 or
ended `structured_output_retry_exhausted`. The second case is resumed once, by session id, with
`REFILE_AFTER_REFUSALS`. That text names the shape that went wrong, the field order, and a
summary written as plain sentences with no markup. The rescue is recorded like the existing one
(`ended_without_reporting`, `resumed_for_report`). A session still refused after it fails
`provider_protocol`, as before.

### B — The summary is the last field (built)

`STEP_REPORT_SCHEMA` lists `summary` last, in `properties` and in `required`. It is the one
free-text field. A session that closes it wrongly then garbles only the summary, and the fields
the record keys on are already written.

### C — The gate says which absence it was (open)

The verify gate's sentence for a rescued-but-still-unreported step is _the step's session ended
without reporting_. For a session whose reports were all refused, it should read _every report
the step's session filed was refused_. The fact needed is already in `detail.subtype`.

### D — Measure whether B removed the failure (open)

Count `report_refusals` and `structured_output_retry_exhausted` across the next runs' records,
by model and by whether the step was led by a command. If refusals persist with the summary
last, the next candidate is a shorter summary: a length the schema states, with the long account
going to `follow_up_work`.

## Acceptance

- A session ended `structured_output_retry_exhausted` is resumed once with
  `REFILE_AFTER_REFUSALS`, and its new report is the step's account (built; tested in
  `tests/test_step_kinds.py`).
- A session still refused after that ask is recorded `provider_protocol`, with the rescue on
  record (built).
- The schema's last property and last required field is `summary`, and the document carries
  the schema and the ask verbatim (built; `tests/test_step_protocol.py`).
- The gate's sentence distinguishes a refused account from a silent one (C).
- A summary of refusals across ten runs is recorded here (D).

## Touches

`cairn/protocol.py`, `cairn/providers.py`, `docs/step-protocol.md`, `docs/cli-contract.md`,
`tests/test_step_kinds.py`, `tests/test_step_protocol.py`; for C, `cairn/verify.py` and
`cairn/report/phrases.py`.
