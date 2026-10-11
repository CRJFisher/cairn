# 43 — The allowance belongs to an account

**North star ([principle 4](../PRINCIPLES.md)).** Cairn's holds are statements about one
subscription's windows. When the credential behind Claude Code changes, those statements stop
describing the sessions Cairn is about to start. A person who switched to an account with
headroom should be told that the queue can go on, not that it has to wait for another
account's week to reset.

**Status: proposed.** Found between runs `20261010T143556Z-93a9629c` and its recovery.

**Serves** Run, Report and Schedule.

## What happened

The run ended `quota_held` on the weekly window of a personal subscription: 98%, reopening
2026-10-13T05:00Z. Its record's next action is `await_allowance`, and `cairn report` says
_It was held on seven_day, which reopens 2026-10-13T05:00:00Z_.

By the time the report was read, Claude Code was logged in to a different subscription:
`claude auth status` answered `subscriptionType: team` for another organisation. A probe through
it answered `five_hour: allowed` with no weekly warning. The report's instruction was true of an
account no session would use.

Nothing in Cairn can see this:

- **The shared reading has no owner.** `runs/.headroom/reading.json` holds windows by name only
  (`five_hour`, `seven_day`). After a switch, readings from both accounts merge into one file.
  `READING_TTL` drops the old account's figures after ten minutes. Inside those ten minutes, the
  old account's rejection holds the new account's steps, and `_merged` keeps the old account's
  higher figure for a window whose `resets_at` happens to match.
- **The record does not say which account funded a session.** A step's report carries
  `apiKeySource` (key or subscription), never which subscription. Two steps in one run, funded by
  two accounts, cannot be told apart.
- **`await_allowance` is a fixed sentence.** The report reads the moment out of the record and
  never asks whether that moment still applies.

## What changes

### A — Every reading and every session names its account

The account is read where admission already measures, from `claude auth status` (JSON:
`orgId`, `subscriptionType`, `authMethod`), cached beside the reading for `READING_TTL`. It is
never read from the credential store. The identity Cairn keeps is the organisation id and the
subscription type, never the email.

- `reading.json` becomes one reading per account, keyed by organisation id. Admission reads the
  current account's reading only, so a switch starts from _unknown_, and _unknown_ admits by
  design.
- Each session's report records `account: {org_id, subscription_type}`, and the record lists
  the accounts a run was funded by.

### B — `await_allowance` is re-read, never repeated

`cairn report` keeps the held step's account beside its moment. Where the account Claude Code
is logged in to now differs from it, the next action reads: _held on the weekly allowance of
another account (team → personal); the account in use now has not been measured — run the
recovery now, and it will be admitted or held on its own reading_. The recovery command is the
same command. Only the moment stops being a precondition.

## Acceptance

- After a credential switch, the first admission measures the new account and never holds on
  the old account's figures, including inside `READING_TTL`.
- A run whose steps were funded by two accounts records both, and each step names its own.
- `cairn report` on a `quota_held` run, read under a different account, says the hold was
  another account's and gives the recovery as runnable now.
- No email address and no token appears anywhere Cairn writes.

## Touches

`cairn/headroom.py` (the reading's key, admission), `cairn/providers.py` (the account reader,
the session's report detail), `cairn/record/extract.py` and `cairn/record/vocabulary.py`
(funding, the next action), `cairn/report/phrases.py`, `docs/supervision.md`,
`tests/test_headroom.py`, `tests/test_run_record.py`.
