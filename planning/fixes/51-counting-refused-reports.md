# 51 — Whether moving the summary last removed the refused reports

**Status: proposed.** Section D of [46](../archive/46-a-report-refused-five-times.md). 46 built
the rescue (A), moved `summary` to the schema's last field (B) and named the absence at the gate
(C). It did not measure whether B removed the failure it was found from.

**North star ([principle 2](../../PRINCIPLES.md)).** A step that did its work is recorded as having
done it. The rescue in A makes a refused report recoverable, but a person still waits through
refusals that B was meant to prevent.

## The measurement

Count `report_refusals` and `structured_output_retry_exhausted` across the run records of the next
ten runs, grouped by model and by whether a command led the step. Compare the counts with the
runs before B landed, starting with `remaining-plans-3`.

If refusals persist with the summary last, the next candidate is a shorter summary: a length the
schema states, with the long account going to `follow_up_work`.

## Acceptance

- A table of refusals over ten runs, by model and by command-led or not, is recorded here.
- If refusals persist, this document gains the shorter-summary change as a section, with its own
  acceptance.

## Touches

None until the measurement is in. Then `cairn/protocol.py` and `docs/step-protocol.md`.
