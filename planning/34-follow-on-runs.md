# 34 — Continue: follow-on work becomes the next run

Work an agent found but did not do becomes the next run, and the sequence closes itself when a run
surfaces nothing new.

Split from [20](20-watching-a-live-session.md), which owns seeing a live session. This is a
different capability with its own risks — it creates work rather than reading it — and it does not
block Watch.

**Serves** the capability surface of **Run** and **Author**. No invariant moves: a verdict stays
something a declared assertion proved ([08](08-verify-gate.md)), the plan document stays the source
of truth (I1), and the emitted graph stays static, digest-stamped, and free of logic
([11](11-emitter-and-preflight.md)).

## Today

Every step report is required to carry `follow_up_work: string[]` — _"list work you found but did
not do"_ (`cairn/protocol.py`) — and it survives the whole pipeline: report file, `StepRecord`, an
attention line in the rendered report. There it stops. Nothing turns a follow-up into a step, a
node, or a run. And the graph layer is built so nothing can: the root key set is a closed
frozenset, a step body must be one quoted invocation, `action:`/`with:` are refused by preflight,
the build path runs at authoring time only, and the body digest turns any runtime rewrite into
recorded divergence. The engine's dynamic primitives — `repeat_policy`, `parallel` over a
`dag.run` action, inline `foreach`, all present in 2.11.0 — are structurally excluded, and that
exclusion is load-bearing for every promise the preflight makes.

## The shape rejected: a loop node

A single agent step under `repeat_policy: until`, drawing its prompt from a queue of follow-ups
until the queue is empty, is mechanically available on the pinned engine. It is rejected because it
spends what every other part of Cairn buys:

- **Visibility.** N tasks smeared into one node is one log, one report line, one timeline entry.
- **Verification.** A step with no assertion and no recorded answer is refused at emission
  ([08](08-verify-gate.md)); a task invented mid-run has no authored assertion, so a loop node
  makes runtime-discovered work the one category that ships unverified.
- **Convergence.** Markers are per step. A loop node holds one, so a crash mid-loop cannot no-op
  its finished follow-ups on the re-run that is Cairn's only recovery procedure.

## The change: the loop lives at the run layer, where every invariant already holds

- **Harvest.** A command reads a settled run's record and collects its follow-up work, per step,
  with each item's provenance (the step that surfaced it, its summary line). The record is the
  only source read; a run still `running` refuses the harvest by naming its state.
- **Draft.** The harvest becomes an ordinary plan document — the same contract, the same parse,
  the same assertion conversation for steps that arrive without a checkable end state, which
  runtime-discovered work always does. A person confirms the parse report exactly as they confirm
  any plan's; nothing runs on an agent's say-so alone.
- **Run.** The follow-on plan is started as any plan is started: its own slug, its own record.
  The one-repository-one-run lock serialises it behind anything still landing; the marker
  protocol makes each round cheap where rounds overlap.
- **Close.** The sequence ends when a run's harvest is empty. Any scheduled form states its round
  bound when it is arranged, because a sequence that cannot say when it stops is one a person
  cannot reason about ([13](13-triggers-and-schedules.md)).

**Held in reserve, named so it is not rediscovered:** for work that must fan out _within_ a run —
discovered items a later join in the same graph consumes — the engine's `parallel` + `dag.run` is
the fit: one child run per item, each individually inspectable. Adopting it is a generator version
bump that reopens the closed root keys, the one-invocation body rule, and output capture. Nothing
measured yet needs it; it waits for a plan that cannot settle before its follow-ups must execute.

**What must not change.** No step is emitted without an assertion or a recorded human answer; an
agent's self-report can still lower an outcome and never raise it — `follow_up_work` gains no
authority by being read; every follow-on round is its own run with its own record; the emitted
graph stays static per run and its digests stay honest; recovery is still re-running the plan,
never `dagu retry`.

**Touches.** `cairn/record/` (the harvest read), a `cairn plan draft --from-run <id>` entry in
`cairn/plan/cli.py`, `cairn/plan/` (drafting), the skill's dispatch table and a capability page
(`capabilities/continuing.md`), `docs/step-protocol.md` (_what follow-up work is for_),
`docs/plan-contract.md` (_where a drafted plan comes from_), `tests/test_plan_contract.py`,
`tests/test_the_skill.py`.

## Acceptance

- One command turns a settled run's follow-up work into a draft plan document; every drafted step
  passes the same validation and assertion conversation as a hand-written one, and a run still
  running refuses the harvest by naming its state.
- A follow-on run is an ordinary run — own record, same namespaces — and a harvest that returns
  nothing ends the sequence.
- The generated workflow files are byte-identical before and after this lands: nothing here
  touches the emitted graph.
