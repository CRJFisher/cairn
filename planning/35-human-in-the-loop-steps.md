# 35 — Human in the loop: a step that waits for a person

**Status: rough idea, not a plan.** Nothing here is decided. It records what the engine offers,
where Cairn's invariants meet it, and the questions a real plan would have to answer.

Some steps genuinely need a person — a choice between options, a sign-off, a missing fact. Today a
run can only stop at such a step (`user_decision_required`, [08](08-verify-gate.md)) and a person
re-runs the plan afterwards. The idea: let a run **pause at a named point, take a typed answer from
a person, and carry that answer into later steps**, without ending the run.

**Serves** the capability surface of **Run** (a run can wait and resume) and **Author** (a plan can
declare a decision point and consume its answer). It would be the first runtime path by which a
human supplies data mid-run.

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
5. **Relation to `user_decision_required`.** Does this replace stop-and-rerun for that outcome, or
   sit beside it for decisions known in advance?
6. **Who answers.** Web UI, CLI, REST are all available; an agent can complete a task too. Cairn
   must decide whether an agent may answer a human task, since that would defeat its purpose.
7. **Passing state.** Is per-step typed output enough, or does any plan need a shared document
   across steps? Output capture is itself part of what a bump would reopen.

## Next, if this is pursued

Verify the above against the pinned engine version with a throwaway workflow (does `waiting`
survive a restart, does completion resume on a worker, what does the record show), then answer
questions 1–3 before drafting a real plan.
