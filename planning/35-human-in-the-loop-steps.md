# 35 — Human in the loop: a run asks a person, and carries on

**North star ([principle 3](../PRINCIPLES.md)).** A person is asked for a decision or a missing
fact at the moment the work needs it, answers, and the work carries on with that answer — without
re-running the plan by hand. Software is complex: no plan anticipates every decision, so asking is
an expected part of a run, not a failure.

**Status: idea, not a plan.** Seeing what sessions are doing is a separate capability,
[36](36-keeping-tabs.md); one surface for both is [37](37-one-plane-for-watching-and-answering.md).

## Two kinds of question

- **Known in advance** — the plan author knows a decision is coming ("pick the schema after the
  spike"). Declared in the plan, so it fits the static graph: Dagu's human task or approval gate
  (below).
- **Surfaced mid-session** — the agent hits something the plan did not foresee. This is the common
  case in complex work, and the one that causes today's friction. A static graph cannot declare
  it, so it needs a different mechanism. Candidates:
  - **Park and resume.** The session ends with a `needs_input` outcome and a written question; its
    branch parks, independent branches continue; the answer seeds a follow-on run
    ([34](34-follow-on-runs.md)). Fits every current invariant; loses the session's context.
  - **Block in place.** The session gets an `ask_human` tool (a small MCP server) that writes the
    question to a store and blocks until an answer appears. Keeps the full context and is the most
    natural for the agent; costs a held session, a held lock, and needs a timeout.
  - Likely both: block for a bounded time, then park.

Where the answer lands decides question 3 below, and is shared with [37](37-one-plane-for-watching-and-answering.md).

## What the engine offers (Dagu, docs.dagu.sh; unverified against the pinned 2.11.0)

- **Human task** — `action: human.task` with `prompt`, a flat JSON-Schema `form` (string, integer,
  number, boolean; `enum`, `pattern`, bounds, `required`; no nesting), and optional `artifacts` to
  review. The run enters `waiting`; a person completes it in the web UI, with
  `dagu human-task complete --run-id … --step … --input k=v`, or by REST. Each form field becomes a
  typed step output, read as `${steps.<id>.outputs.<field>}`. Completion is idempotent.
- **Approval gate** — an `approval:` block on any ordinary step: the step runs, then pauses for
  approve / reject (aborts the DAG) / push back (re-run from `rewind_to` with feedback in the
  environment). Parallel gates are independent. Notifications by web UI, REST, email, lifecycle
  handler.
- **State passing** — per-step named outputs (`$DAGU_OUTPUT_FILE`), `params`, `env`, `context.*`,
  files and stdin. Outputs are for small values (`max_output_size`, default 1 MB), survive retries
  within a run, and do not cross runs. There is no single shared state document with
  Step-Functions-style path transforms; data is per-step and named.
- **Limits** — human tasks are root-DAG only, and cannot combine with `run`, `approval`,
  `foreach`, retry or timeout.

## Where it meets Cairn

The graph layer is closed on purpose ([34](34-follow-on-runs.md), [11](11-emitter-and-preflight.md)):
`action:` and `with:` are refused by preflight, the root key set is a closed frozenset, and a step
body is one quoted invocation. Both engine mechanisms sit outside that today, so adopting either is
a generator version bump that reopens part of the closed surface — the same cost 34 names for
`parallel` + `dag.run`. The approval gate is the narrower reopening (one added key on a step Cairn
already emits); the human task adds a new step shape.

Invariants any version must keep:

- **A human answer is data, never a verdict.** A verdict stays something a declared assertion
  proved. An answer can feed a step; it cannot stand in for the assertion that step must carry.
- **The plan document stays the source of truth.** The decision point and the shape of its answer
  are authored in the plan, validated at parse, and digest-stamped into the emitted graph.
- **The graph stays static.** A form is declared up front; nothing is generated mid-run.
- **Recovery is re-running the plan, never `dagu retry`** ([34](34-follow-on-runs.md)). A waiting
  run is a new kind of resting state that this rule has to account for.

## Open questions

1. **Gate or task?** Is the need "review what a step produced" (approval gate) or "ask something
   no step produced" (human task)? Probably both appear in practice; which first?
2. **The run lock.** One-repository-one-run means a waiting run holds the repository. Is a run that
   may wait for days acceptable, or does waiting need to release the lock and be resumed as a new
   run?
3. **Markers and resume.** Markers are per step; does a completed human task count as a finished
   step for the re-run recovery path, and where is the answer recorded so a re-run does not ask
   again?
4. **Timeout.** Human tasks cannot carry one. A stalled wait needs a bound
   ([13](13-triggers-and-schedules.md) holds that bounds are stated up front); is that an external
   reaper, or a reason to prefer the approval gate?
5. **Relation to `user_decision_required`.** This idea should replace stop-and-rerun for that
   outcome (no backwards compatibility); confirm nothing else depends on it.
6. **Who answers.** Web UI, CLI, REST are all available; an agent can complete a task too. Cairn
   must decide whether an agent may answer a human task, since that would defeat its purpose.
7. **Passing state.** Is per-step typed output enough, or does any plan need a shared document
   across steps? Output capture is itself part of what a bump would reopen.

## Next, if this is pursued

Verify the above against the pinned engine version with a throwaway workflow (does `waiting`
survive a restart, does completion resume on a worker, what does the record show), then answer
questions 1–3 before drafting a real plan. Separately, spike `ask_human` as an MCP tool under
`claude -p`: does a blocking tool call survive tens of minutes, and what does the transcript show?
