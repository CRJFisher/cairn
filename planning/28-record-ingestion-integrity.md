# 28 — Damaged evidence cannot become a clean run

The record pipeline is deliberately tolerant of partial engine state, but several tolerant
readers currently turn contradiction or malformed input into ordinary absence. A `noop` report
can override an engine-recorded failure, a missing top-level engine status can still yield
`green`, and a report renamed to another node is trusted under its filename.

**Serves** **Report** and every automated consumer of the run verdict. The invariant is
fail-closed evidence: incomplete observability may produce “unknown” or an integrity failure,
never a stronger outcome.

## A — Canonically validate step reports while collecting them

`read_reports` duplicates only part of `core.read_step_report`'s validation and indexes reports
by filename without checking their internal `step_id`.

- Share one validator for runtime gates and record extraction.
- Require the filename, internal step id, and expected run id to agree.
- Validate status and required field types against the frozen vocabulary.
- Do not silently discard malformed, truncated, wrong-run, or renamed reports. Preserve a
  bounded integrity diagnostic associated with the candidate node.
- A rejected report contributes no cost, summary, session, freshness, or outcome fact.

## B — Reconcile contradictions without raising outcomes

A work report saying `noop` is valid only when the engine says the marker gate skipped that
work. It cannot override `failed`, `running`, or completed execution. Define the compatibility
matrix between report statuses and engine statuses and make every contradiction explicit.

Required engine run fields, especially top-level status, must also be validated. Missing,
Boolean, string, or unknown status values produce an unavailable/integrity state that cannot
derive `green`.

## C — Reject duplicate identities before projection

Duplicate engine node names collapse in lookup maps and later collide in canonical-fact keys.
The detailed rows can then show both occurrences using the last one's outcome while the headline
uses the failed occurrence. Reject duplicate identities or assign stable occurrence keys before
any projection. Input order must not change the verdict or rendering.

## D — Validate stored records at their read boundary

`read_record` currently checks only `record_version`; `{"record_version": current}` is returned as
a `RunRecord` and fails later with a `KeyError`. Validate every required section, scalar type,
vocabulary, identity, and uniqueness rule before returning a stored record. Because records are
regenerable, the error names the rebuild command.

## E — Attribute triggers from trigger evidence

An absent actor currently means “Cairn” for every trigger, including scheduler, webhook, retry,
and catchup. Derive attribution from trigger kind plus actor and preserve distinct values for
Cairn, a named user, scheduler, webhook, retry scanner, and unknown.

## Acceptance

- No incompatible report/engine status pair can raise a failed or unknown execution to `no_op`
  or `verified`.
- Missing or malformed engine run status cannot yield a green verdict.
- Renamed, truncated, wrong-run, unknown-status, and missing-field reports contribute no facts
  and produce visible integrity diagnostics.
- Duplicate node identities behave identically in either order and never alias rendered facts.
- A current-version record with any required section missing is refused at `read_record`.
- Every trigger kind renders accurate provenance with and without an actor.

## Touches

`cairn/core.py`, `cairn/record/engine.py`, `cairn/record/extract.py`,
`cairn/record/model.py`, `cairn/record/store.py`, `cairn/record/facts.py`,
`cairn/report/compose.py`, `cairn/report/phrases.py`, run-model and report documentation,
malformed fixtures, and their tests.
