# 49 — The allowance at a real limit: what the instruments read when a window closes

**Status: proposed.** The parts of [38](../archive/38-subscription-headroom.md) that need a real
limit hit or the owner's say-so on the credential: its spikes 1, 2, 3 and 5 and two of its open
questions. Everything else 38 named has since been built: holding and resuming at the allowance
(38, [44](../archive/44-a-held-run-resumes-itself.md)), readings per account
([43](../archive/43-the-allowance-belongs-to-an-account.md), which closes 38's third question),
and what each step costs ([45](../archive/45-spending-the-allowance-knowingly.md)).

**North star ([principle 4](../../PRINCIPLES.md)).** A queue holds at the limit and resumes when
the window reopens. Today that rests on the shape of a `rejected` event that has been read about
and scripted, but never recorded live.

## Spikes

1. **A real rejection.** Run until a limit is hit, on a throwaway account if need be. Record the
   `rejected` event, the result message's `terminal_reason` and `is_error`, and the exit status.
   This settles the rejected event's real shape and whether `blocking_limit` is the terminal
   reason.
2. **Resume after rejection.** Find out whether `--resume <session>` continues the refused turn
   once the window reopens. If it does not, a session the limit stops falls back to `quota_held`
   plus the marker path ([supervision.md](../../docs/supervision.md)).
3. **The usage endpoint, live.** With the owner's say-so, read the keychain token once, call the
   endpoint with its `User-Agent`, and compare its percentages with `/usage`. Record the schema,
   the token's lifetime, and whether the 429 appears on this account.
4. **What a step costs in percent.** Each step's record now carries `consumption` and the readings
   it was measured between (45 A). Read those across runs and find out whether a step's share of
   a window is predictable enough to compare with the headroom, instead of using a fixed
   threshold.

## Open questions

1. **Hold threshold per window.** 0.95 on the 5-hour window is a different bet from 0.95 on the
   weekly one, which reopens days away. Spike 4 informs it.
2. **Which model does the probe use?** The cheapest one. But a per-model weekly window (Opus,
   Sonnet) is visible only through that model's event or the endpoint.

## Acceptance

- A throwaway account driven to its limit under a plan of five agent steps. The run record shows
  a hold, a resume, five landed steps, and no exit 75 reaching the engine. The run's verdict is
  the same as an uninterrupted run's.
- `detail.resets_at` is asserted against a recorded `rejected` event, not a scripted one.
- Spikes 3 and 4 and both open questions have their readings or decisions recorded here.

## Touches

`cairn/headroom.py`, `cairn/providers.py`, `tests/test_headroom.py`, `docs/supervision.md`.
