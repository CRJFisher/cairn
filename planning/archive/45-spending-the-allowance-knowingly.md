# 45 — Spending the allowance knowingly: what each step costs, what the queue will cost

**North star ([principle 4](../../PRINCIPLES.md), [principle 3](../../PRINCIPLES.md)).** A queue is
bounded by the allowance behind it. Cairn holds at a threshold, but it cannot say what a step
costs or whether the rest of the queue fits in what is left. So a person learns that the week
ran out from a failed run, rather than from a sentence before the run began, when they could
still have chosen a model, an order or a day.

**Status: A, B, C and D built; E open.** A step's record carries `consumption` and the report
shows it per step and for the run (`cairn/headroom.py`, `cairn/record/`, `cairn/report/`); the
forecast is `cairn/forecast.py`, stated by `run start` and `explain workflow`; admission paces
a window as well as thresholding it; and the endpoint's answer is recorded once by `cairn
supervise headroom --endpoint on|off`, with `run start` naming the windows it leaves blind.
E — a model fallback the plan states — stays the open question this plan carries, and is not
built. These would have prevented, or announced in advance, both stalls in `remaining-plans-3`.

**Serves** Run, Report and Explain.

## What happened

- **A fresh 5-hour window spent in 49 minutes by one step.** In run
  `20261009T224737Z-151a689d`, `implement_16` (Opus) was admitted at 03:11Z into a window that
  had reset at 03:10Z. Its session reached the `allowed_warning` threshold (90%) and then the
  limit at 04:00Z: 134 turns, 33.4 M cache-read tokens, an API-equivalent $23.80. Admission had
  read `allowed` with no figure (`used: null`), because the stream carries a utilization only
  from 90% up and the usage endpoint is off by default. Nothing could have predicted this, and
  the record kept no figure that would predict it next time.
- **A week spent in ten hours without a word before it ran out.** In run
  `20261010T143556Z-93a9629c` the weekly allowance read 77% at 14:51Z, when `implement_16`
  started. `implement_16`, `review_16` and `implement_29` then took it to 98% by 00:23Z.
  `implement_29` alone (Opus, 330 turns) took about 19 points of the week. Every admission in
  between was `warned`, which is recorded and shown to nobody. The queue still held two Opus
  implementations and three reviews, which plainly could not fit in 23 points.

## What changes

### A — The record says what each step cost

Every session already ends with the provider's own account: `total_cost_usd`, `modelUsage`
per model, and token counts. Each window's reading is taken at admission and, where the stream
or a probe gives one, again at the end. A step's record gains `consumption`:

- tokens by kind and model, and the API-equivalent cost, summed across every session the step
  opened (first, resumes, rescue);
- per window, the reading before and after, where both were measured. A delta is stated only
  between two measurements, never inferred.

The report shows consumption per step, and the run's total beside the allowance it read.

### B — A forecast before the run, and before each admission

Cairn estimates a step from what steps like it cost before: the same kind and model, in this
repository's own records. It uses the median, and states how many records the figure rests on.
The forecast is a sentence, never a refusal; deciding what to do with it is the person's
(principle 3).

- `run start` prints, after the occasion: _this queue's 7 agent steps have cost about 31% of
  the weekly allowance in past runs (from 12 records); 23% remains, reopening Tue 13 Oct 06:00
  BST_.
- `explain workflow` gives the same figure without starting anything.
- Where no records exist, it says _no past runs to estimate from_ and nothing else.

### C — Pace the week, not just the threshold

A step whose estimate exceeds what is left of a window **before its reset** is held at
admission instead of being started. A hold before a session strands nothing: no partial work
is left uncommitted, and no session has to be resumed days later. With
[44](44-a-held-run-resumes-itself.md), the run resumes when the window does. The 95%
threshold remains the floor for a step with no estimate.

### D — Make the instruments' blindness visible

Admission reads `used: null` for most of a window. The stream says nothing below 90%, and the
usage endpoint (`CAIRN_HEADROOM_USAGE_ENDPOINT`) is off unless the owner turns it on, because it
reads a credential Cairn did not create. Both stay as they are. What changes is that the owner's
choice is asked once and recorded (`cairn supervise headroom --endpoint on|off`), and `run
start` names the blindness: _the 5-hour allowance is measured only from 90% up; turn on the
usage endpoint for figures below that_.

### E — A model fallback the person states (a question, not a default)

[38](38-subscription-headroom.md) names three answers to a long hold: wait, switch to a
cheaper model, or stop. Only waiting is built. A plan sentence such as _if the weekly Opus
allowance is low, use sonnet_ would let the derivation record a fallback per step, and
admission would take it rather than hold. Per [SKILL.md](../../SKILL.md) a model is never chosen
on the person's behalf, so this is offered only where the plan says it. Whether to build it is
the open question this plan carries.

## Acceptance

- Every agent step's record carries `consumption` with tokens, cost and the window readings it
  was measured between. The report shows a per-step and per-run total.
- `run start` and `explain workflow` state a forecast against the current reading, naming the
  number of past records it rests on, or say that there are none.
- A step whose estimate exceeds a window's remainder before reset is held at admission, with
  that reason, and never started into the window.
- With the endpoint off, `run start` says which windows are blind below 90%.

## Touches

`cairn/providers.py` (the result's usage into the report), `cairn/headroom.py` (readings at
session end, pacing in `decide`), a new `cairn/forecast.py`, `cairn/record/extract.py`,
`cairn/record/vocabulary.py`, `cairn/report/`, `cairn/skill/trigger.py`,
`cairn/skill/explain.py`, `cairn/baseconfig.py` or a new `supervise headroom` verb,
`docs/supervision.md`, `docs/report.md`, `capabilities/running.md`,
`tests/test_headroom.py`, `tests/test_forecast.py`.
