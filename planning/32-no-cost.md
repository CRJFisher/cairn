# 32 — Cairn has no notion of cost

Cost goes out of Cairn completely: money, tokens and time spent. That covers the price of a
run, a step's dollar ceiling, its time limit as something a plan sets or an offer states, the
cost a provider reports, the cost a report shows, and the run offer and acceptance that exist
only to put a price in front of a yes. Cost was meant to protect the person. In practice it
sits in front of every run and every answer, and it is the thing a person most often has to
wade through to get work done.

This is a purge, not a softening. When it is finished, nothing a person can do, see or be
asked in Cairn mentions what anything cost or will cost. No field, flag, constant, refusal,
outcome or sentence in the code or docs carries one. No compatibility path is kept. Every
caller moves to the new shape in the same change.

**Serves** every capability. **Run** becomes "ask for a run and it starts". **Report** and
**Explain** stop answering cost questions. **Author** stops reading money and time bounds out
of a plan. **Schedule** stops putting a scheduler behind an offer.

## Decided

The person settled these three, and they are not reopened while this is built:

1. **Run consent goes entirely.** There is no offer, no offer id, no reply, no spent marker
   and no acceptance ledger, and no question is put before a run. A request to run is the
   go-ahead. This retires the first of `SKILL.md`'s three rules, and the half of the dispatch
   ask list that exists because "one reading spends money".
2. **A fixed hang guard stays, and it is not a cost.** One internal liveness deadline still
   kills a session that has stopped making progress. It is a constant in Cairn. A plan cannot
   set it, no offer or report states it as a cost, and it never appears as a per-step bound.
   It is what keeps the guarantee that no step runs unbounded (I7), and that is its only job.
3. **`paid/` is deleted**, along with everything that exists to serve it.

## What a person sees change

- "Run X" runs X. Nothing is quoted, priced or asked. The run id and the engine's view link
  come back at once, as they do today after a yes.
- The parse report has no budget, ceiling or timeout column. A plan sentence like "spend at
  most eight dollars" or "at most fifteen minutes" is not a bound. Derivation leaves it in the
  step's prose and sets nothing from it.
- A step's model stays. It decides who does the work, not what the work costs, and it is
  what [30 C](30-a-plan-stated-in-the-request.md) makes binding on the resolver.
- A report shows no cost, no "notional" flag and no abandoned-session cost. Asking "what did
  it cost" is no longer something Cairn answers, and it says so in one line, with no
  apology and nothing that hints the figure lives somewhere else.
- No step can end as "budget exhausted". A session the hang guard stops ends as "stopped by
  the hang guard". The report recovery from [22](22-timed-out-step.md) stays, but its resume
  no longer splits a remaining budget.
- Scheduling installs what was asked for. The fact that the scheduler's retry scanner
  re-executes failed runs on this machine is stated when scheduling, because it decides what
  will execute. It is information, not a priced offer, and it asks nothing.

## The inventory: everything that goes

**The skill and its rules.**

- `SKILL.md`:
  - _What a run costs, and how a yes is taken_, deleted whole.
  - The rule "a run is never a default and never an inference … an offer whose price was
    stated".
  - "A scheduler is its own escalation", in its cost and offer form.
  - In _Reading a request_: every ask justified by cost ("one spends money", "a question
    offering a costly reading names the kind of cost", "never the costlier reading").
  - The occasion section's "what the other would have cost".
  - **cost** from _The engine, and where it is the better answer_.
- An ask that stays because the request is genuinely ambiguous stays without a cost reason.
  An ask that existed only because of cost goes, and its dispatch-table cell becomes the
  capability.
- `capabilities/running.md`: steps 5–6 (offer, ask, start on the answer), the preconditions
  and bindings `authorisation` and `occasion_reading`, and the closing lines about cost.
- `capabilities/scheduling.md`: the daemon offer and its yes.
- `capabilities/reading.md`: "the cost … never answered" and every cost question it routes.
- `capabilities/authoring.md`: "offer the run rather than performing it". After authoring, the
  next step is the run, when the request asked for one.

**Code.**

- `cairn/skill/consent.py`: deleted. That removes `Offer`, `Authorisation`, `Acceptance`,
  `make_offer`, `spend`, `session_bounds`, `longest_timeout`, `disclosure`, the `offers/`
  directory and the `.spent` markers.
- `cairn/skill/cli.py`: `run offer` goes, and `run start` loses `--offer` and `--reply`.
- `cairn/schedule_cli.py`: `schedule offer` goes, and `install` and `start` lose `--offer`
  and `--reply`.
- `cairn/skill/vocabulary.py`, `surface.py`, `dispatch.py` and `trigger.py`: every offer,
  price, acceptance and cost phrase, and the asks that existed for cost.
- `cairn/plan/schema.py`:
  - `max_budget_usd` and `timeout` go as step fields.
  - `AGENT_BUDGET_USD` goes, and so does `MERGE_BUDGET_USD`.
  - `AGENT_TIMEOUT`, `COMMAND_TIMEOUT` and `MERGE_WORK_TIMEOUT` collapse into the one hang
    guard constant.
  - The comments that justify bounds by price go.
- `cairn/plan/validate.py`, `cairn/plan/report.py` and `cairn/plan/assertions.py`: bound
  checks, the timeout warning and any cost wording.
- `cairn/bounds.py`: what is left once spend is gone. If only the hang guard uses it, it folds
  into that.
- `cairn/__main__.py`: `--max-budget-usd` and the per-step `--timeout` come off `agent run` and
  `merge land`. A `wait` keeps its `--timeout`, because how long to wait for a condition is
  what that step means, not a cost bound.
- `cairn/emitters.py`, `cairn/merge.py` and `cairn/topology.py`: no budget or per-step timeout
  in emitted bodies. The hang guard is applied in one place.
- `cairn/providers.py`:
  - `--max-budget-usd`.
  - Reading `total_cost_usd`, plus `cost_is_notional`, `abandoned_cost_usd`, `budget_exhausted`
    and `RESUME_DECLINED_BUDGET`.
  - The arithmetic that sums the cost of a resumed session.
- `cairn/record/model.py` and `cairn/record/extract.py`: `cost_usd`, `cost_is_notional` and the
  `Budget` record.
- `cairn/report/`: every cost rendering and phrase.
- `cairn/workflow/preflight.py` and `gate.py`: any refusal over a missing or invalid bound.

**The paid suite.** `paid/` is deleted whole, including `measurements.jsonl`, together with
`tests/test_paid_suite.py`. `scripts/record_runs.py` imports from `paid/`. Its agent-shape path
goes, and the script either drops that path or is deleted, whichever leaves no reference
behind. The README's paid-suite section and the release steps in
[16](16-release.md) that run it go too.

**Docs.** `docs/plan-contract.md` and `docs/plan-derivation.md` (the bound fields and the
derivation rule for them), `docs/step-kinds.md`, `docs/merge-step.md` (the resolver's
ceiling), `docs/cli-contract.md`, `docs/triggers.md`, `docs/run-model.md`,
`docs/supervision.md` (_Bounds on every emitted step_ becomes the hang guard), `docs/report.md`
and `README.md`.

**Fixtures and tests.**

- `fixtures/invocations/cases.json`: the whole `consent` family, 22 cases, goes. Every case
  whose expectation is an ask over cost becomes its capability, and every `why` that names
  cost is rewritten.
- `fixtures/workflows/*.yaml` and `fixtures/plans/*/graph.json`: regenerate through
  `scripts/regenerate_workflows.py`. No hand edits.
- The tests that assert any of the above change with it. That is chiefly
  `tests/test_the_skill.py`, `test_step_kinds.py`, `test_run_record.py`,
  `test_plan_contract.py`, `test_verify_gate.py`, `test_supervision.py`, `test_triggers.py`,
  `test_merge_step.py`, `test_topology.py`, `test_workflow.py` and
  `test_engine_supervision.py`. A test that existed only to pin a cost behaviour is deleted,
  not adapted.

**Planning.**

- [25](25-execution-admission-and-paid-bounds.md): B, C and D go. A (gate the exact bytes)
  stays, reworded so it no longer speaks of "costs that were accepted".
- [24](24-recovery-economics.md) D goes, because there is no acceptance left to lose. A–C
  stay as work about how long a person waits, written without money.
- The words "offer", "spent" and "acceptance" are reworded out of [19](19-start-friction.md),
  [22](22-timed-out-step.md) and [18](18-first-run-friction.md).
  [30](30-a-plan-stated-in-the-request.md) and [31](31-the-repository-the-session-is-in.md)
  were written against this decision and carry no cost.
- `what-to-fix-first.md` drops anything ordered by cost.

## What must not change

- A verdict is still something a declared assertion proved. A run that drops a branch is
  still not a success.
- The run lock, the worktrees, the dirty-tree refusal, recovery and the occasion all work as
  they do now. Only their cost framing goes.
- Every agent step still names its model, and the run record still says which model did each
  step's work.
- `dagu retry` is still refused, because of what it re-executes, not because of what it
  costs.

## Acceptance

- `grep -rniE 'cost|price|priced|budget|dollar|usd|spend|spent|ceiling|offer|accept(ed|ance)|authoris|consent|paid' SKILL.md README.md capabilities docs cairn fixtures tests scripts`
  finds no match that speaks of cost or consent. Each match left is a different sense of the
  word, such as git's "accept" in a merge, and each one is listed in this task's close-out
  note with the reason it stays.
- `paid/` does not exist, and nothing imports from it.
- "Run X", made in a repository whose definition exists, starts the run in the same turn. No
  question is put and no file is written under `offers/`.
- A plan that says "spend at most eight dollars" derives a step with no budget field, and the
  parse report shows none.
- A session that stops making progress is killed by the hang guard, and its report reads
  "stopped by the hang guard" without a figure.
- A report on any run shows no cost.
- The ordinary suite passes. `python3 -m cairn --help` and every capability document describe
  only what is left.
