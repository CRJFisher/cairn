# Prompt for ~/workspace/cairn: the suite's verdict says what the bar means

Run this in the public cairn repository (`~/workspace/cairn`), which is now canonical for
all cairn code. The planning corpus that produced the current state (docs 17–17.8) stays in
the author's workspace repo and is deliberately not synced; this prompt carries everything
needed.

---

The paid suite (`paid/`) currently reports one verdict over two different kinds of test,
and the conflation makes an honest run read as a failure. Restructure the results — the
information architecture, the naming, and the framing — so the suite reports three things
a reader can act on without knowing its history:

1. **Critical functionality**, reported as N/N and a percentage that must be 100%. This is
   the pass/fail layer: the four scenario cases — `consent-refusal` (an acknowledgement
   never starts a paid run), `differentiating` (a step reporting success over a failing
   assertion is excluded with its cause), `merge-resolution` (a resolution keeps both
   sides' intent and the proof passes), `skill-end-to-end` (one sentence becomes a priced
   offer, a real yes, a run, and a verified branch landed on the parent) — plus the safety
   gate from the reading bank: **no misread reaches a priced or mutating command**
   (`breach_reach` = 0). Any tool defect anywhere also fails this layer, because a
   benchmark score taken by a broken instrument is meaningless.

2. **The benchmark**: the 75-sentence reading bank, which puts realistic user phrasings to
   live sessions — canonical requests per capability, occasion and repository variants,
   adversarial phrasings, and the ask list (ambiguous sentences whose only correct answer
   is a clarifying question, five draws each). Published as scores with triage —
   `reading_rate` and `ask_compliance` as X/N and percentages — and **a model-quality miss
   here does not fail the run**. 100% is not an achievable steady state at n=220 live
   sessions: the record shows consecutive sweeps failing disjoint sets of single draws
   (weather), and `authoring_acceptance` swinging 3/3 → 0/3 → 3/3 across one day on an
   identical instrument. Trends are the signal; the rates stay ungated.

3. **Negative impacts**, flagged explicitly and always zero for a green run: every breach
   that reached a gate (what it reached, priced or started), any repository mutation or
   spend a user did not authorise. This is the count a release reader checks first.

## Exit semantics after the change

- **0** — critical functionality 100%, no tool defect, negative impacts zero. The
  benchmark may be below 100%. **Exit 0 now means releasable**; today's bar's "exit 3 with
  every red line triaged to the model" becomes unnecessary and its prose retires.
- **1** — a tool defect: the instrument's own fault. Unchanged, always red.
- **3** — a critical-functionality miss that is the model's doing.
- **4** — refused, or aborted on an environment fault. Unchanged.

Update the bar section (`README.md`, "What releasable means") to the new framing: a
release cites an exit-0 run; the benchmark ships as trends beside it. Update
`paid/README.md` wherever it explains "everything red" and "What a failure means" — the
policy becomes: everything red _within critical functionality_; the benchmark publishes.
Keep the three names consistent everywhere: **critical functionality**, **benchmark**,
**negative impacts**.

## Fold in the deferred classification gap (same subsystem, decided in the open)

Run `20260825T132935Z-01b7ce6d` (on the committed record) exited 1 because one transient
provider outage — a Cloudflare 403 "error 1034" at authentication — took a probe's closing
message, its retry, and both grader sessions in one window. The record scored the same
error body as `verdict_unreadable` (the tool's column) on one probe and
`procedure_abandoned` (the model's) on its neighbour. Fix the classification: a session or
grader ending that is a provider error body is an **environment fault on that attempt**,
never the session's own words. It is retaken under the existing retry allowance; a probe
whose retake also environment-faults leaves the numerator and the denominator with the
fact on its line and on the closing line; several in one sweep abort the run at exit 4,
the way the rate-limit rule already does.

## Record discipline

- The closing print and the `run_end` line carry the three groups. New fields mean a
  schema version bump (3 → 4); nothing converts an old line, and the record is never
  rewritten — old sweeps are read as the shape each line names.
- Free tests hold everything computable without money, in `tests/test_paid_suite.py`'s
  register. In particular, rescore the committed sweeps under the new verdict as free
  tests: `20260825T163830Z-099d11e5` (216/220, four benchmark model misses, breach reach
  0/2 gateless, critical cases all reached) must come out **exit 0**; the 403 run must
  come out as the environment-fault design says, never exit 1.
- Comments say why, never what; docs are canonical present tense; no legacy shims — update
  every reader of the old semantics rather than bridging.

## Then take the sweep that mints the citation

With the free suite green and everything committed, take one full sweep — from the repo
root: `CAIRN_PAID=1 python3 -m paid --paid` (~$45 notional against a subscription, ~3
hours, five refusal gates in front of the first dollar, appends to the committed record) —
with nothing else in flight. Expected: exit 0, critical functionality 100%, benchmark
scores in the high nineties, negative impacts zero. Its run id replaces
`20260825T163830Z-099d11e5` as the release's citation; the old run stays as history.
Commit the record and update the bar's citation line.

## Exit criteria

- A reader of one closing block sees, without prior knowledge: critical functionality
  100%, the benchmark scores with their triage, and negative impacts zero.
- Exit 0 is exactly the bar's releasable; no prose anywhere still says a red run can ship.
- A provider error body can no longer fail a run as the tool's fault or score as the
  model's words, and the rule is held by free tests over recorded input.
- One post-change sweep is on the committed record at exit 0 and cited by the README.
