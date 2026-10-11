# 42 — A step held before its session started did nothing, and the record says so

**North star ([principle 2](../PRINCIPLES.md), [principle 4](../PRINCIPLES.md)).** A person
reading a run that stopped at the allowance should see which step was stopped, that it did
nothing, and what the allowance read when it stopped. Today a step the allowance stopped before
it started looks like a step that did work and then diverged from its assertion, and the run
spends minutes proving work that does not exist.

**Status: done.** Sections A and B are both built. Found in run
`20261010T143556Z-93a9629c` of `remaining-plans-3`.

**Serves** Run and Report. No invariant moves: the verify gate still fails closed, and a marker
is still only ever written over an assertion that ran over a session's work.

## What happened

`review_29` was admitted at 2026-10-11T00:23:35Z against a fresh probe reading: the weekly
allowance at 98%, past the 95% hold threshold, reopening 2026-10-13T05:00Z. That is beyond the
6-hour single-hold ceiling, so the step ended `quota_held` four seconds after it began. No
session was opened, nothing in the tree changed, and `dirty_before` was empty. Ending it was
correct. What came after it was not:

- **The assertion ran anyway.** `verify_review_29` ran the full suite (`python3 -m unittest
discover -s tests -t .`) for 506 seconds over the tree `implement_29` had left. Its exit
  proves nothing about `review_29`, which did nothing.
- **The mark recorded a divergence over work that does not exist.** `mark_review_29` filed
  `divergence: {asserted: true, reported: "held"}` with the summary _the step was held at the
  subscription's allowance before it reported, over an assertion that passed_. The report then
  lists `review_29` under **Divergence**: _its assertion passed over the work it left_. It left
  none.
- **The admission snapshot shows windows the decision did not rest on.** `review_29`'s
  `headroom.reading` lists a `five_hour` window `rejected` at 99%, read at 2026-10-10T19:27Z,
  five hours before admission and past its own reset. `implement_16`'s reading in the same run
  printed a `five_hour` rejection read the previous morning. Admission ignored both because they
  were older than `READING_TTL`. The report shows them as though the step had been admitted
  against them.

The same divergence appeared in run `20261009T224737Z-151a689d`, for `implement_16`. That step
_had_ run a session and left work, so the overlay was true there. What separates the two cases
is whether a session was ever opened.

## What changes

### A — A held step that opened no session has no work to assert

The work report already says which case it is: `headroom.admission == "held"` and no
`session_id`. Every downstream reader keys on that one fact, and none re-derives it.

- **`cairn verify needed` closes** over a work report with cause `quota_held` and no session,
  so the assertion is skipped and its node reads `skipped`. Nothing was done, so there is
  nothing to prove. The closing is the precondition's own decision, recorded in its report as
  `decision: "nothing_to_assert"`.
- **The verify gate closes `quota_held` with no divergence.** A divergence pairs a session's
  account with an assertion over that session's work, and here there is neither.
- **The record reads it as excluded `quota_held`, held before it started.** The summary says
  so: _held before it started — the weekly allowance at 98%, reopening Tue 13 Oct 06:00 BST;
  nothing was done_.

A step held _after_ its session met a limit keeps today's behaviour. Its session did work, the
assertion runs over it, and the divergence overlay is the truthful account of a step that
stopped without reporting.

### B — The admission snapshot is what admission read

`_snapshot` writes only the windows `fresh_windows` returned, each with its age, and adds the
names of any windows it set aside as stale. The report prints the stale ones, if at all, as
_set aside — older than 10 min_, never as a reading.

## Acceptance

- A step held at admission ends `quota_held` with no assertion run, no `verify_*` duration
  beyond its precondition, and no `divergence` overlay. The report says _held before it
  started_ and _nothing was done_.
- A step held after its session met a limit, and stopped there, is unchanged: the assertion
  runs and the divergence overlay stands.
- A recorded step's `headroom.reading` holds only windows read within `READING_TTL` of its
  admission, and the report never prints a stale window as a reading.
- A recorded fixture of each case (held before a session, held after one) is in the corpus,
  and the verdict and attention list for each are asserted.

## Touches

`cairn/verify.py` (`verify needed`, the gate's divergence), `cairn/assertions.py`,
`cairn/headroom.py` (`_snapshot`), `cairn/record/extract.py`, `cairn/report/phrases.py`,
`docs/verify-gate.md`, `docs/supervision.md` § Working within the subscription,
`scripts/record_runs.py` and two new fixtures under `fixtures/runs/`, `tests/test_headroom.py`,
`tests/test_verify_gate.py`, `tests/test_run_record.py`.
