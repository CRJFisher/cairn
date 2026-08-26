# 20 — Live visibility and follow-on work

Two capabilities Cairn does not yet have, designed here against measured engine facts. **Watch**: a
person asks where a running session is — which turn it is on, what it last did, to which file — and
gets an answer, instead of a raw JSONL log and a status integer. **Continue**: work an agent found
but did not do becomes the next run, and the sequence closes itself when a run surfaces nothing new.

**Serves** the capability surface of **Read** and **Run**. No invariant moves: the run's record
stays the only source of how it went and is rebuilt fresh on every read ([12](12-run-record.md)), a
verdict stays something a declared assertion proved ([08](08-verify-gate.md)), consent stays a
stated price and a qualifying yes spent on exactly one run ([15](15-the-skill.md)), and the emitted
graph stays static, digest-stamped, and free of logic ([11](11-emitter-and-preflight.md)).

## What the engine holds, measured against the pin

Both designs lean on engine facts, so the facts come first. Instrument: the pinned binary itself —
`dagu schema dag` on 2.11.0 — and the engine's published REST surface.

- **A step's outputs publish only at completion.** Failed, aborted, and still-running steps publish
  nothing, and the outputs endpoint serves settled runs only. "Update the node's outputs to say
  where it is" is not a channel the engine has; the wish dies here and neither design below uses
  outputs.
- **What is live is the log and the node status.** The engine streams each node's stdout and stderr
  to per-node files as they happen, and — where a server runs — renders them live and serves
  per-node status mid-run (`GET /api/v1/dag-runs/{name}/{runId}`, `…/steps/{step}/log?tail=N`).
  The view already links there ([13](13-triggers-and-schedules.md)); Cairn deliberately manages no
  server, and `cairn/record/engine.py` names the REST `statusLabel` as the road not taken.
- **The dynamic primitives exist in the pinned version.** The 2.11.0 step vocabulary carries
  `repeat_policy` (`while`/`until` on a condition, iteration `limit`, backoff), `parallel` (child
  runs of a `dag.run` action fanned over a runtime-produced item list), and inline `foreach`
  bodies. Feasibility is not the question anywhere below; fit is.
- **`harness.run` now ships a claude provider with `output_schema` validation and approval
  push-back.** The standing decision against it holds — exit-code translation must live in
  `cairn agent run` ([02](02-agent-step-spike.md)) — but the overlap with Cairn's step protocol is
  growing, and the decision deserves a re-measure at the next engine version change, not before.

## A — Watch: where a session is, read from the transcript Cairn already writes

**Today.** `run_claude` tees Claude's entire stream-json event stream, line by line, to the step's
stdout (`cairn/providers.py`), and the engine captures it to the node's `.out` file. The record
carries that path as the node's `transcript` and its stderr sibling (`cairn/record/extract.py`) —
and nothing, anywhere, reads either. The record pipeline already reads live runs correctly: a
running node yields `OUTCOME_RUNNING`, the run verdict `running`, next action `wait`, with a
recorded `fixtures/runs/mid-run/` corpus behind it; liveness comes from the process, never the
status field (`cairn/liveness.py`). So `cairn report` on a live run already says _that_ a node is
running and since when. It cannot say _where the session is inside the node_ — yet every fact
needed to say it sits in a file Cairn itself wrote, parsed once at capture time and never again.

**The change.**

- **A `where` line per running node.** For each node the record holds at `OUTCOME_RUNNING`, the
  extraction tails the node's transcript and derives one line from the last events: turn count,
  the last tool invocation and its object, cost so far — the stream already carries all three.
  The line is a fact like any other, rendered by the report in all three formats, and it degrades
  honestly: an unreadable or absent transcript yields no line, never a guess.
- **A legible line per turn in the engine's own view.** The tee loop in `run_claude` parses every
  event as it forwards it; beside the JSONL it writes one human-readable line per turn to stderr,
  which the engine captures to the node's `.err` file and its view renders live. The watch-it-live
  answer stays a link — the link just leads somewhere a person can read.
- **The transcript stays the receipt.** The `.out` file keeps the full, unabridged event stream;
  the breadcrumb is an addition on the other channel, not a replacement.

**What must not change.** The report still rebuilds the record fresh on every invocation and holds
no daemon and no poller; liveness is still decided by the process check; no engine server is
required for any of it, and Cairn still starts none. The REST road stays open and untaken.

**Touches.** `cairn/providers.py` (the breadcrumb beside the tee), `cairn/record/extract.py` (the
transcript tail for running nodes), `cairn/record/facts.py`, `cairn/report/compose.py` and
`phrases.py`, `capabilities/reading.md`, `fixtures/runs/mid-run/`, `tests/test_run_record.py`,
`tests/test_report.py`.

## B — Continue: follow-on work becomes the next run

**Today.** Every step report is required to carry `follow_up_work: string[]` — _"list work you
found but did not do"_ (`cairn/protocol.py`) — and it survives the whole pipeline: report file,
`StepRecord`, an attention line in the rendered report. There it dies. Nothing turns a follow-up
into a step, a node, or a run. And the graph layer is built so nothing can: the root key set is a
closed frozenset, a step body must be one quoted invocation, `action:`/`with:` are refused by
preflight, the build path runs at authoring time only, and the body digest turns any runtime
rewrite into recorded divergence. The dynamism the engine offers is not merely unused; it is
structurally excluded, and that exclusion is load-bearing for every promise the preflight makes.

**The shape rejected: a loop node.** A single agent step under `repeat_policy: until`, drawing its
prompt from a queue of follow-ups until the queue is empty, is mechanically available on the pinned
engine. It is rejected because it spends exactly what this document's other half buys:

- **Visibility.** N tasks smeared into one node is one log, one report line, one timeline entry.
- **Verification.** A step with no assertion and no recorded answer is refused at emission
  ([08](08-verify-gate.md)); a task invented mid-run has no authored assertion, so a loop node
  makes runtime-discovered work the one category that ships unverified.
- **Convergence.** Markers are per step. A loop node holds one, so a crash mid-loop cannot no-op
  its finished follow-ups on the re-run that is Cairn's only recovery procedure.

**The change: the loop lives at the run layer, where every invariant already holds.**

- **Harvest.** A command reads a settled run's record and collects its follow-up work, per step,
  with each item's provenance (the step that surfaced it, its summary line). The record is the
  only source read; a run still `running` refuses the harvest.
- **Draft.** The harvest becomes an ordinary plan document — the same contract, the same parse,
  the same assertion conversation for steps that arrive without a checkable end state, which
  runtime-discovered work always does. A person confirms the draft exactly as they confirm any
  plan; nothing runs on an agent's say-so alone.
- **Run.** The follow-on plan is offered and started as any plan is: its own slug, its own offer,
  its own yes, its own record. The one-repository-one-run lock serialises it behind anything still
  landing; the marker protocol makes each round cheap where rounds overlap.
- **Close.** The sequence ends when a run's harvest is empty. A round bound exists and is stated
  in the offer of any scheduled form, because a sequence that cannot say when it stops is a price
  a person cannot accept ([13](13-triggers-and-schedules.md)).

**Held in reserve, named so it is not rediscovered:** for work that must fan out _within_ a run —
discovered items a later join in the same graph consumes — the engine's `parallel` + `dag.run` is
the fit: one child run per item, each individually inspectable. Adopting it is a generator version
bump that reopens the closed root keys, the one-invocation body rule, and output capture. Nothing
measured yet needs it; it waits for a plan that cannot settle before its follow-ups must execute.

**What must not change.** No step is emitted without an assertion or a recorded human answer; an
agent's self-report can still lower an outcome and never raise it — `follow_up_work` gains no
authority by being read; every run still costs one offer and one yes; the emitted graph stays
static per run and its digests stay honest; recovery is still re-running the plan, never
`dagu retry`.

**Touches.** `cairn/record/` (the harvest read), a `cairn plan draft --from-run <id>` entry in
`cairn/plan/cli.py`, `cairn/plan/` (drafting), the skill's rule table and a capability page
(`capabilities/continuing.md`), `docs/step-protocol.md` (_what follow-up work is for_),
`docs/plan-contract.md` (_where a drafted plan comes from_), `tests/test_plan_contract.py`,
`tests/test_the_skill.py`.

## Acceptance

- `cairn report` on a running run prints, for each running node, one line naming the session's
  turn count, its last action and that action's object, and cost so far — derived from the node's
  transcript, with no engine server running.
- The engine's live view of a work node shows one human-readable line per turn on the stderr
  channel, while the stdout transcript remains the complete event stream.
- One command turns a settled run's follow-up work into a draft plan document; every drafted step
  passes the same validation and assertion conversation as a hand-written one, and a run still
  running refuses the harvest by naming its state.
- A follow-on run is an ordinary run — own offer, own record, same namespaces — and a harvest
  that returns nothing ends the sequence.
- The generated workflow files are byte-identical before and after both features land: nothing
  here touches the emitted graph.
