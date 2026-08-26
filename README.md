# Cairn

Cairn turns a plan document a human wrote into a graph of verified, concurrent work.

Cairn generates workflows for **Dagu**, an external DAG engine; a `dagu` binary on PATH is
what runs them. "The engine" throughout these documents means Dagu. Cairn's own code is
Python 3 with no third-party imports.

**Type `/cairn`, then say what you want.** Cairn is a skill and it opens only when it is
asked for by name: author a workflow from a plan, change one, run it, put it on a schedule,
read what a past run did, or ask what a workflow would do, what a verdict means, or why a
step was excluded. Six capabilities, all reachable by asking, and none of them needing a
second surface — though where the engine's own view is the better answer, the skill links to
it and says what it will never tell you.

Nothing else opens it. A sentence that describes the work without naming Cairn — "run my
plan", "schedule this nightly" — reaches whatever else is installed, and Cairn stays silent
rather than competing for it. That is the price of a tool that spends money and commits to a
repository: it is entered on purpose, and naming it is itself the first consent.

A run is never a default. It happens on an unambiguous instruction or an accepted offer, the
offer states what the run costs before you answer it, and one acceptance authorises exactly
one execution. [SKILL.md](SKILL.md) is where those rules live.

**What is under it:** derive a plan graph from a plan's documents, validate it, and
read the parse report; answer the authoring conversation for a step whose plan named no
assertion; run a step under the committed-marker protocol so a resumed run skips work that
is already done, with the verify gate deciding what gets recorded; converge worktrees,
commit, land a wave's branches on the parent branch one at a time, and take and give back
the repository's run lock; generate a whole workflow file for a plan, refusing to write
one the engine would run into a silent failure; and read back what one run did with
`python3 -m cairn record build --run <run-id>`, as a presentation-free record whose verdict
is derived by walking every node — so a run the engine calls a clean success over a dropped
step reads as exactly that; give a plan a cron schedule and install it where a scheduler
watches, with `cairn schedule start` refusing on a machine whose retry or catchup policy
would re-execute paid work; read any run at the engine's own view, from the address its
record carries; and render that record for a terminal, a repository or a browser with
`python3 -m cairn report --run <run-id> --repository <path>`, which answers in order whether
it worked, what to do next, what needs attention, what each step did, what shape the run was,
and what it cost. Every run leaves that record whether anyone watched it or not. And offer a
run, priced from
the definition that would execute, then start exactly that one run against exactly the
authorisation the offer minted.

What is built is the plan contract, the execution core, the step protocol, the verify gate
and its authoring conversation, the branch topology, the merge step, the locks and repairs
that keep a run's writes under control, the generator and its preflight, the run model every
rendered surface reads, the triggers and schedules that start one, the three renderings, and
the skill that drives all of it.

- [SKILL.md](SKILL.md) — what `/cairn` opens: the six capabilities, the table a request is
  read against, the ask list, the consent rule, and where each procedure lives.
- `capabilities/` — four documents holding six capabilities' procedures, read when one is
  selected: authoring (with editing), running, scheduling, and reading (with explaining).
  Each states its entry preconditions and what it is bound to on entry. **Adding a seventh**
  touches: `cairn/skill/vocabulary.py` (the constant, its rank in `CAPABILITY_ORDER` — which
  must stay between the consent-gated prefix and the reads-only suffix — and
  `DOCUMENT_BY_CAPABILITY`), `cairn/skill/dispatch.py` (the cells that reach it — a new verb class is a new
  row of `DISPATCH_RULES`, and a capability reached only by an ask needs a reason,
  a question and a family too),
  `SKILL.md` (the summary and the table), a document here, and `fixtures/invocations/`.
- [docs/plan-contract.md](docs/plan-contract.md) — the step-graph schema, the identifier
  and slug rules, every validator check, and the readings the derivation declares.
- [docs/plan-derivation.md](docs/plan-derivation.md) — the two-pass procedure an agent
  follows to derive a graph from a plan's documents.
- [docs/step-kinds.md](docs/step-kinds.md) — the frozen kind vocabulary, and which document
  owns each thing not yet built.
- [docs/cli-contract.md](docs/cli-contract.md) — what a generated workflow can rely on from
  every subcommand: identity, reports, exit codes, and cancellation.
- [docs/step-protocol.md](docs/step-protocol.md) — the committed marker and its freshness
  scopes, the run occasion, the step report, the agent preamble, and how the no-op is
  lowered onto a precondition.
- [docs/verify-gate.md](docs/verify-gate.md) — the three nodes a step becomes, how failure
  routes by position, what the gate reads, the frozen exclusion causes, and the
  missing-assertion conversation.
- [docs/topology.md](docs/topology.md) — waves, branches and worktrees, the node-name
  contract, the four convergence cases, and the run's duration arithmetic.
- [docs/supervision.md](docs/supervision.md) — the git write mutex, the repository run lock,
  reconciling a killed run, and the bounds on every emitted step.
- [docs/merge-step.md](docs/merge-step.md) — landing a wave one branch at a time, what the
  prediction may decide, how a merge is proven, and the halt that a person settles.
- [docs/run-model.md](docs/run-model.md) — start here for a run's record: the frozen run
  verdict, step outcome, attention
  and exit-code vocabularies, the record's whole shape, the engine-status mapping and its
  version pin, where a run's records live, and the untrusted-text policy.
- [docs/report.md](docs/report.md) — the six questions the report answers and why that order,
  what makes it structural rather than conventional, the three renderings and each sink's
  escape, how an exclusion is made unmissable, and the drift oracle all three are checked
  against.
- [docs/workflow.md](docs/workflow.md) — the emitted file's whole shape, where a parameter
  may stand, what the preflight refuses, the mandatory gate and what it still misses,
  how a hand-edited workflow is detected, and the recorded shapes in `fixtures/workflows/`
  with what moves the generator's version.
- [docs/triggers.md](docs/triggers.md) — where a person goes to watch a run and what the
  view will never answer, the four trigger paths and which of them costs a daemon, what a
  caller may vary and what is refused, where a run's occasion comes from, installing a
  schedule safely, and the human gate.
- `cairn/plan/` — the schema, the validator, the parse report, the id rules, and the
  authoring conversation.
- `cairn/` — runtime identity and reports, command and wait execution, the marker protocol
  and its freshness keys, the verify gate, the agent preamble and report schema, the
  provider dictionary, the emitter table, the topology, the merge step, the locks, the
  supervisor, where the engine keeps its own files, the parameters a caller may vary, and
  the scheduler with its command line.
- `cairn/report/` — the frozen section spine, the phrasebook, the composition that is the one
  reader of the record, the per-sink escapes and the scribe every fact passes through, the
  graph layout, and the three renderings.
- `cairn/skill/` — `vocabulary.py` the frozen words, `dispatch.py` the rule table and its
  ask list, `resolve.py` the repository and the occasion, `consent.py` the offer ledger,
  `trigger.py` the one path that starts a run, `explain.py` three answers, `surface.py` the
  measurement below, `cli.py` the two commands. A run is authorised in one direction:
  `cairn run offer` prices the definition and mints one token, `cairn run start` spends it
  exactly once, and `trigger.start` is the only thing here that can invoke the engine.
- `fixtures/plans/` — sixteen plans with their golden graphs and expected verdicts, two of
  them carrying the answers their authoring conversation received.
- `fixtures/workflows/` — the whole emitted file for six of those plans, one per topology
  shape, compared byte for byte by the suite.
- `fixtures/runs/` — eight runs recorded from a real engine: green, red, blocked,
  green-with-exclusions, all-no-op, mid-run, crashed, and one real paid agent step. Three of
  them are the same clean success as far as the engine is concerned and none of them extracts
  the same way.
- `scripts/record_runs.py` — re-records that corpus against the installed engine.
- `scripts/measure_fanout.py` — what the fan-out buys and what the mutex costs, as numbers.
- `fixtures/invocations/` — every phrasing the skill is held to, what each must resolve to,
  and what the corpus does and does not prove.
- `scripts/regenerate_workflows.py` — rewrites the golden workflows, and refuses to when
  the emitted shape moved under a generator version that already described another.
- `scripts/measure_surface.py` — the context cost below, and `--check` refuses when this
  file no longer carries it.

- `paid/` — a second suite that drives real coding-agent sessions against real repositories,
  run deliberately rather than on every change. It is unreachable from the command below,
  refuses without `--paid` and `CAIRN_PAID=1`, prices the whole selection before the first
  call, and leaves a committed record of what it cost and what it measured in
  `paid/measurements.jsonl`. Every part of it that is not a session is a pure function over
  recorded input and is proved for nothing by `tests/test_paid_suite.py`. See
  [paid/README.md](paid/README.md).

## What releasable means

Stated 2026-08-25, before the sweep it judges — a bar picked after the sweep is in is an
estimator's sin, and this one is dated so a reader can check.

A release cites one full sweep of the paid suite, taken with nothing in flight, and it
gates on end states rather than on rates:

- the four end-state cases reach: the resolution keeps both sides' intent, the divergence
  count is zero, the consent case starts nothing, and the skill case lands a verified
  branch on the person's own words;
- **breach reach is zero** — a misread that stops at a sentence is a quality trend; one
  that prices or starts a run is a gate;
- the sweep's exit code is 0, or 3 with every red line triaged to the model and named. An
  exit of 1 — the tool's own fault classification — is never releasable, whatever the
  rates.

The reading rate, ask compliance and authoring acceptance are published as trends with
their triage beside them and gate nothing: a threshold on a 75-sentence population is noise
wearing a bar's clothes, and the record separating the model's misses from the
instrument's is what makes publishing them honest. What turns a watched number into a stop
is a breach past a gate, and that is already a gate.

Every price in that record is **notional**: the suite's sessions run against a
subscription allowance, so each figure is an API-equivalent price rather than money that
moved. The releasing sweep's run id is the citation a release carries.

Run everything with `python3 -m unittest discover -s tests -t .` from this directory —
around 1,290 tests, roughly eight minutes. They kill real processes, wait on real locks and
drive a real Dagu 2.11.0; stray provider output during the run is expected. **The command
cannot spend a penny**, and the paid suite asserts that against the loader rather than
claiming it.

Two suites treat a missing `dagu` differently on purpose. `test_step_protocol.py` **fails**
without it, because it covers the one failure mode that otherwise reports success; set
`CAIRN_SKIP_ENGINE_TESTS=1` to record deliberately that a run did not check it.
`test_engine_supervision.py` skips, because its subject is what a crash leaves behind rather
than whether a green run was really green.

Cairn's own command line is internal: the skill, generated workflows and tests invoke it,
its arguments carry no promise to anyone else, and nothing a person needs to do requires
learning them — the commands named above are what is under the skill, not a surface. Its
`--help` says so too, and names `/cairn`, because going looking for a command line is how
somebody who did not build this arrives at the wrong door.

**The engine's command line is a different matter.** You already have it for your other
jobs, and these are the verbs worth pointing at a Cairn plan:

| Command              | For                                                       |
| -------------------- | --------------------------------------------------------- |
| `dagu server`        | the view: a running graph, live step state, logs, timings |
| `dagu status <plan>` | whether a run is going, without opening a browser         |
| `dagu history`       | every run of a plan, without opening a browser            |

One is refused rather than useful: **never `dagu retry`**. Re-running a plan is the whole
recovery story, a continued occasion is what makes it cheap, and a retry reuses the run
identity in a way Cairn's own recovery already handles.

## What it costs to have installed

Measured by `python3 -m scripts.measure_surface`. Tokens are an estimate at 4 characters each, not a tokenizer's count.

| Paid                          | What                                   | Characters | Lines | Tokens (est.) |
| ----------------------------- | -------------------------------------- | ---------: | ----: | ------------: |
| when Cairn is named           | the skill's description                |      `211` |   `1` |          `53` |
| when Cairn is named           | `SKILL.md`                             |    `13612` | `192` |        `3403` |
| when a capability is selected | `capabilities/running.md`, the largest |     `8298` | `128` |        `2075` |

**None of it is unavoidable.** Cairn declares `disable-model-invocation: true`, so its
description stays out of a session's context until someone types `/cairn` — a session that
never names it pays nothing for having it installed. The suite recomputes all three figures
on every run and fails if this table has drifted from them.

Python 3, standard library only. No code in this directory imports anything outside it. The
numbered documents these files cite — doc 05, doc 09 and so on — are the planning series in
`.planning/cairn/`.

## License

MIT — see [LICENSE](LICENSE). Cairn's development happens in the author's tooling workspace; this repository is its public home.
