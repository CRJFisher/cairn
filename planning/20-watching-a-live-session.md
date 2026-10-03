# 20 — Watch: where a running session is, and how long it has been quiet

A person asks where a running session is — which turn it is on, what it last did, to which file,
and how long ago — and gets an answer, instead of a raw JSONL log and a status integer.

Turning follow-up work into the next run is a separate capability with its own risks, and is
[34](34-follow-on-runs.md). Watch ships without waiting on it.

**Serves** the capability surface of **Report** and **Run**. No invariant moves: the run's record
stays the only source of how it went and is rebuilt fresh on every read ([12](12-run-record.md)), a
verdict stays something a declared assertion proved ([08](08-verify-gate.md)), and the emitted
graph stays static, digest-stamped, and free of logic ([11](11-emitter-and-preflight.md)).

## What the engine holds, measured against the pin

The design leans on engine facts, so the facts come first. Instrument: the pinned binary itself —
`dagu schema dag` on 2.11.0 — and the engine's published REST surface.

- **A step's outputs publish only at completion.** Failed, aborted, and still-running steps publish
  nothing, and the outputs endpoint serves settled runs only. "Update the node's outputs to say
  where it is" is not a channel the engine has, and nothing below uses outputs.
- **What is live is the log and the node status.** The engine streams each node's stdout and stderr
  to per-node files as they happen, and — where a server runs — serves per-node status mid-run
  (`GET /api/v1/dag-runs/{name}/{runId}`, `…/steps/{step}/log?tail=N`). The view already links
  there ([13](13-triggers-and-schedules.md)), and `cairn/record/engine.py` names the REST
  `statusLabel` as the road not taken. **Whether the view renders a node's `.err` file live, as
  it does `.out`, is not yet measured**, and section A's breadcrumb depends on it — see the gate
  below.
- **`handler_on` carries `wait`.** Beside `init`, `success`, `failure`, `abort` and `exit`, the
  2.11.0 lifecycle hooks include `wait`, which fires when the DAG enters the waiting status
  because a step needs human input — an approval gate. `cairn/workflow/build.py` already emits an
  `exit` handler (the run's release), so a hook is not a new kind of graph content.
- **`otel` exports traces to an OTLP endpoint.** The DAG-level `otel` block sends spans for the run
  and its steps to a gRPC or HTTP collector.
- **`harness.run` ships a claude provider with `output_schema` validation and approval push-back.**
  The standing decision against it holds — exit-code translation lives in `cairn agent run`
  ([02](02-agent-step-spike.md)) — and it is re-measured at the next engine version change.

## Decided against, with the reason

- **A `handler_on.wait` notification.** It fires only when a step waits on a person. Cairn emits
  no approval step, so in every workflow Cairn generates the hook never fires: a session that has
  stalled is a running node, not a waiting DAG. Adopting it would also change every emitted
  definition for no observable effect. It is reconsidered if Cairn ever emits an approval gate,
  as its own decision with its own fixture change.
- **OpenTelemetry tracing.** It needs a collector listening at an endpoint, which is a server
  Cairn would have to run or require, and Watch requires none. Spans also arrive per step, not per
  turn inside a session, so they could not say where a session is even with a collector present.
- **The REST status surface.** It needs a running engine server; the transcript Cairn already
  writes carries more than the endpoint does.

## A — Where a session is, read from the transcript Cairn already writes

**Today.** `run_claude` tees Claude's entire stream-json event stream, line by line, to the step's
stdout (`cairn/providers.py`), and the engine captures it to the node's `.out` file. Claude's own
process runs with `stderr=None`, so it inherits the wrapper's stderr and anything Claude writes
there lands in the node's `.err` file. The record carries the `.out` path as the node's
`transcript` and its stderr sibling (`cairn/record/extract.py`) — and nothing reads either. The
record pipeline already reads live runs correctly: a running node yields `OUTCOME_RUNNING`, the
run verdict `running`, next action `wait`, with a recorded `fixtures/runs/mid-run/` corpus behind
it; liveness comes from the process, never the status field (`cairn/liveness.py`). So
`cairn report` on a live run says _that_ a node is running and since when. It cannot say _where
the session is inside the node_, or whether it has gone quiet.

**The change.**

- **A `where` line per running node.** For each node the record holds at `OUTCOME_RUNNING`, the
  extraction tails the node's transcript and derives one line from the last events: turn count,
  the last tool invocation and its object, and **the age of the last event** — measured from the
  transcript file's last write against the moment the report is built. A session that has been
  silent for ten minutes reads as silent for ten minutes, not as healthy. The line is a fact like
  any other, rendered by the report in all three formats, and it degrades honestly: an unreadable
  or absent transcript yields no line, never a guess, and the age is stated even where the last
  event cannot be parsed.
- **A legible line per turn on the stderr channel.** The tee loop in `run_claude` parses every
  event as it forwards it; beside the JSONL it writes one human-readable line per turn to the
  wrapper's own stderr. That is new output from Cairn's process, sharing the `.err` stream with
  whatever Claude writes to its inherited stderr, so each breadcrumb line carries a fixed prefix
  that tells it apart. It is built only after the gate below passes.
- **The transcript stays the receipt.** The `.out` file keeps the full, unabridged event stream;
  the breadcrumb is an addition on the other channel, not a replacement.

**The gate before the breadcrumb.** On Dagu 2.11.0, run a step that writes a line to stderr every
few seconds for a minute, open the node in the engine's view while it runs, and record whether
the `.err` lines appear live, only at completion, or not at all. The result is written into this
document's engine facts. If the view does not render `.err` live, the breadcrumb is not built and
the `where` line in `cairn report` is the whole of Watch.

**What must not change.** The report still rebuilds the record fresh on every invocation and holds
no daemon and no poller; liveness is still decided by the process check; no engine server is
required for the `where` line; the emitted workflow definitions are unchanged.

**Touches.** `cairn/providers.py` (the breadcrumb beside the tee), `cairn/record/extract.py` (the
transcript tail and last-event age for running nodes), `cairn/record/facts.py`,
`cairn/report/compose.py` and `phrases.py`, `capabilities/reading.md`, `fixtures/runs/mid-run/`,
`tests/test_run_record.py`, `tests/test_report.py`.

## Acceptance

- The `.err` live-rendering measurement on 2.11.0 is recorded in this document before the
  breadcrumb is written, and the breadcrumb exists only if it passed.
- `cairn report` on a running run prints, for each running node, one line naming the session's
  turn count, its last action and that action's object, and the age of its last event — derived
  from the node's transcript, with no engine server running.
- A running node whose transcript has not been written for ten minutes reports that age; a node
  whose transcript is unreadable reports no derived line and still reports the age where the file
  exists.
- Where the gate passed, the engine's live view of a work node shows one prefixed, human-readable
  line per turn on the stderr channel, while the stdout transcript remains the complete event
  stream.
- The generated workflow files are byte-identical before and after this lands: nothing here
  touches the emitted graph.
