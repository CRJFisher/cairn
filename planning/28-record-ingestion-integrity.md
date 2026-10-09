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
- A rejected report contributes no summary, session, freshness, or outcome fact.

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

## Close-out

Done. All five sections hold and every criterion above holds.

**A record now accounts for what it could not read.** `integrity` is a section of the run
record: one entry per piece of this run's own evidence the record refused, naming the node
it was found under, one of nine frozen faults, and a bounded sentence saying what
disagreed. Each raises an attention item, placed above every failure and exclusion because
those lines were read off the same evidence, and the count is on the first screen of all
three renderings with the sentence that nothing refused raised anything.

One validator serves the runtime gates and the record. A report is the account of a node of
a run only where the filename, the internal `step_id` and the `run_id` agree, its status is
one of the frozen three, and every field a reader turns on is present and of its type. A
document that fails contributes no summary, no session, no freshness and no outcome, and
the step reads as having left no account.

A report status stands beside only the engine node statuses it can. `noop` stands beside a
`skipped` node and nothing else, and it is the only report status that can raise an
outcome; no status at all stands beside a node the engine never started or aborted, because
such a node evaluated no precondition and ran nothing. `verified` is now the marker gate's
word **and** the engine's together. Where the two contradict, the step keeps the outcome
its own node supports and the contradiction is recorded as it stands.

The engine's own run status is read once, and a missing, Boolean, string or unmapped one is
a refusal of that one field rather than the death of the reading: the rest of the walk
stands, the status reads as absent, and the verdict cannot be `green`, `all_no_op` or
`green_with_exclusions` — fail-closed, because without that field nothing says whether the
run even finished.

A node name the engine recorded twice is one identity with two claims about it. The record
keeps it once, reading the worst of what the occurrences claim by a total order over their
own content, so the two orders of one pair produce the same record, verdict and rendering,
and no projected key can ever hold two nodes' facts.

A stored record is validated whole at `read_record` against the model's own declarations —
every required section, scalar type, frozen word, the run id it was filed under, the exit
code its verdict carries, and the uniqueness of every step id and node name — and a field
the model does not declare is refused rather than ignored. Because the record is
regenerable, every refusal names `cairn record build`.

Attribution is derived from the trigger kind together with the actor. A named actor speaks
for itself; an absent one means Cairn's own skill for a manual start, the scheduler for a
firing or its catchup, the retry scanner for a retry, the run above for a sub-run, and
nothing at all for a kind the engine recorded as unknown. `started_by_cairn` is gone.

The corpus carries a tenth shape, `damaged`: a run the engine and the gates recorded as
entirely verified, broken afterwards in five declared ways. It is the one fixture whose
evidence is not a measurement, because nothing Cairn or the engine does produces those
documents — and it is where the renderer oracle checks that every refusal reaches every
rendering.
