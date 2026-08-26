# The step protocol

Every step checks whether it already ran, does the work only if it did not, and leaves a
marker committed alongside that work. This is why re-running is the whole recovery story:
Cairn has no resume mode, no repair command, and no state to reconcile by hand.

## The marker

**`.steps/<id>.done`, in the step's working directory** — one file per step, so parallel
steps in separate worktrees never contend. It names the run that **did the work**, which is what
lets a later run say which earlier one completed a step it no-ops. A no-op rewrites the
marker and keeps that name rather than claiming the work
([run-model.md](run-model.md)). It is JSON:

```json
{
  "step_id": "config_schema",
  "run_id": "20260809T215838Z-a6beaee2",
  "scope": "once",
  "key": "once",
  "summary": "sentinel field added to the config schema"
}
```

**Verification writes it, never the agent.** `cairn marker write` runs after the step's end
state has been asserted, gated on that assertion ([verify-gate.md](verify-gate.md)), and no
other subcommand writes a marker. A marker an agent could write would survive its own failed
verification, so a step excluded on one run would find its own marker on the next, no-op, be
excluded again, and never recover.

Its `summary` is the step's own account of what it did, read from that step's report and
flattened to the one line the shape promises, elided past 200 characters. Only the step that
did the work can say what it did; and because the marker reaches git and outlives every
report beside it, an account of no fixed length cannot travel there unbounded.

Two obligations fall on the steps around it, and neither is this document's to implement.
The marker root is the writing process's own working directory, so the verify step must
carry the same `working_dir` as the step it marks, or the marker lands outside the worktree
whose commit is meant to carry it. And the marker reaches git only because the commit step
stages it with the work — one commit carrying both is that step's obligation, and until it
exists the same-commit property is a requirement rather than a fact.

**The marker is the completion authority.** With a fresh marker present, a step is a no-op
and changes nothing, whatever the tree looks like. A mismatch between marker and tree can
be someone's later deliberate decision, so it is surfaced as data rather than repaired.

**Marker visibility follows the merge.** A marker committed on a step's branch reaches the
trunk only when that branch merges, so a step whose branch was excluded has no marker on
the trunk and the next run re-attempts it — with no exclusion list to maintain.

## Freshness: when "already done" expires

The marker records the **key** its work was done under, and the gate compares keys rather
than testing existence.

| Scope                               | The key is                                    | For                                                  |
| ----------------------------------- | --------------------------------------------- | ---------------------------------------------------- |
| `once`                              | the constant `once`; any recorded key matches | the default: every code step                         |
| `run`                               | the run occasion                              | work redone each real run, cheap under recovery      |
| `hourly` `daily` `weekly` `monthly` | the period the occasion falls in              | a standing job with a natural cadence                |
| `inputs`                            | a SHA-256 over the paths in `reads`           | derived work that is stale only when its inputs move |

`once` accepts whatever is recorded, including a key recorded under a scope the step
formerly declared: a step its author has since declared done-once is done. It is
byte-identical in effect to a plain existence check, so a plan that declares no scope pays
nothing for scopes.

A period key buckets the occasion's own moment, in UTC — `2019-04-12T21`, `2019-04-12`,
`2019-W15`, `2019-04`. Taking the moment from the occasion rather than the clock is what
stops a run that crosses midnight bucketing its own steps into two days; taking it in UTC
means a daily plan's boundary is 00:00Z rather than local midnight.

`reads` names files or directories; a directory is hashed file by file, and every path is
judged after resolution, so a symlink cannot key a step on anything outside its own
worktree. A declared path that is not there hashes as `absent`, so the step redoes its work
the moment the file appears, and a declaration that hashes no file at all is refused —
digesting nothing yields a constant, which would no-op for ever.

`.steps` and `.git` are not inputs to anything. Naming either as a read is refused. A walk
passes over them instead — along with anything else it sweeps up that the key has no
business covering, such as a symlink leading out of the worktree — so a whole-tree
declaration stays stable rather than keying itself on its own first marker, and one stray
entry cannot make a directory permanently unkeyable. `.git` is passed over at any depth,
because a repository can be nested; `.steps` only at the root, where Cairn's own is.

## The run occasion

The occasion is the identity of one real occasion of running a plan. Every firing is a new
one; a recovery of a failed run continues the one it is recovering. An operator who wants to
pin a value — to continue an occasion under a fresh run identity — mints one for themselves:

```text
python3 -m cairn occasion new     # 20260809T215838Z-a6beaee2
```

**The run mints its own at its first act, and records it under its own identity**, so every
gate and every marker write in that run reads one value. The generator declares
`CAIRN_OCCASION` as a parameter of the emitted workflow and leaves it empty; a caller who
means to continue an earlier occasion supplies it, and the engine exports it into every
step's environment ([triggers.md](triggers.md)).

Minting in the run rather than at authoring is what makes a recurring plan work at all. A
cron firing has no override point, so an occasion fixed when the file was written would be
reused by every firing — and because a `run`-scoped key _is_ that value, every scoped step
from the second firing onward would find a fresh marker, skip, and the run would report a
clean success having done nothing.

The value lives beside the run's reports, keyed by run identity, which is what makes `dagu
retry` continue the occasion it is recovering rather than mint a second one. Both readers go
through one resolver, because the gate fails open and the marker write fails closed: a
disagreement between them would either redo the work on every run and then exclude it for
ever, or record a marker under a key no later gate will read.

The moment is part of the value rather than read from the clock at each gate, which is what
makes a period key stable across a long run.

## The step report

Every step of every kind writes one report, atomically, to
`<runs-root>/<run-id>/reports/<step-id>.json`, where `<runs-root>` is `CAIRN_RUNS_DIR` —
declared by the generated workflow and required, so a step that cannot say where its account
goes fails loudly rather than writing one somewhere nothing will look
([run-model.md](run-model.md)).

| Field                 | Meaning                                                           |
| --------------------- | ----------------------------------------------------------------- |
| `step_id`             | the engine's own name for the step that wrote it                  |
| `run_id`              | the run it belongs to, so a report cannot speak for a later one   |
| `status`              | exactly `done`, `noop`, or `failed`                               |
| `duration`            | seconds the subcommand ran                                        |
| `working_directory`   | where it ran, resolved                                            |
| `summary`             | one human-readable line                                           |
| `follow_up_work`      | a list, required, never null                                      |
| `needs_user_decision` | a boolean, required                                               |
| `cause`               | an enum value from the closed vocabulary, or null                 |
| `detail`              | everything kind-specific: model, session, cost, turns, scope, key |

**`failed` means the step could not reach its end state on this attempt** — a crash, an
unrecoverable error, a bounded resource exhausted, or a timeout. It never means "not
finished yet": there is no such status, because a step that is still running has not
written a report at all.

**Self-report can only lower an outcome, never raise it.** A reported failure vetoes the
step. A reported `needs_user_decision` surfaces as blocked — a distinct non-green state,
not a failure. A reported success green-lights nothing: verification owns the green path.

**A missing report is itself an outcome.** A step killed mid-flight produces nothing at
all, and its cause comes from the engine's own record.

An agent's half of the report is schema-constrained structured output, never prose to be
parsed:

```json
{
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "status": { "type": "string", "enum": ["done", "noop", "failed"] },
    "summary": { "type": "string" },
    "follow_up_work": { "type": "array", "items": { "type": "string" } },
    "needs_user_decision": { "type": "boolean" }
  },
  "required": ["status", "summary", "follow_up_work", "needs_user_decision"]
}
```

## The preamble

Every agent step's prompt is this text followed by the step's task. Measured against a
resumed step: without it a fresh session never inspected the tree, rewrote six files that
were already correct, and cost 152% of doing the work from scratch; with it the same resume
cost 83% and finished in a third of the time. That 69-percentage-point difference is what
makes running every step as a fresh session affordable, so the preamble is mandatory rather
than advisory.

```text
Before you change anything, work out how much of this task's end state already holds.

The working tree may already carry some or all of this work. An earlier attempt at this
same step may have been interrupted part-way, and its partial edits are still here. Read
the tree and establish what is already true.

Then do only what is missing. Bring the tree to the end state the task describes and
leave whatever already matches it untouched. Do not start over, do not repeat work that
is already correct, and do not assume you are looking at an empty tree.

Do not record your own completion anywhere. Completion is recorded by the verification
that follows you, never by you.

This session is one shot: the process ends when your turn ends, and nothing re-invokes
you for a background shell. Subagents and `Monitor` are yours to use — a background
subagent is waited for, and `Monitor` blocks — but anything you start with `Bash`'s
`run_in_background` dies unread when your turn ends. Wait for whatever you start, and
end only by reporting.

Report through the structured output you are constrained to. `status` is `done` when the
end state now holds, `noop` when it already held and you changed nothing, and `failed`
when you could not reach it. List work you found but did not do in `follow_up_work`. Set
`needs_user_decision` when a human has to decide something before the plan can safely
proceed; that blocks the step rather than failing it.

The task:
```

The preamble makes a resumed step cheap; it does not make a non-convergent task safe. A
task phrased as a blind append duplicates its own work whatever the prompt says. Whether a
task converges is a reading of the plan, so the derivation declares it — a
`non_convergent_task` question quoting the sentence it read
([plan-contract.md](plan-contract.md)) — and the author restates the task; no code at
emission or anywhere else re-reads the sentence.

## Lowering the gate onto the engine

The marker check is a **precondition**. A `condition:` with no `expected:` executes as a
command and gates the step on its exit status; a step whose precondition fails records
`skipped` and never starts, so no agent session begins and nothing is spent. Because the
condition is a real command against real repository state, it works across separate
`dagu start` invocations — which is exactly what "there is no separate resume mode"
requires.

```yaml
params:
  - CAIRN_REPOSITORY: /repo
  - CAIRN_PARENT_BRANCH: main
  - CAIRN_OCCASION: ""
env:
  # Without this the gate cannot import Cairn, and see the hazard below.
  - PYTHONPATH: /path/to/cairn
steps:
  - name: config_schema
    run: python3 -m cairn agent run --provider claude --prompt '…' --model sonnet --max-budget-usd 5.0
    working_dir: ${CAIRN_REPOSITORY}
    timeout_sec: 3600
    retry_policy: { limit: 0, interval_sec: 1 }
    preconditions:
      - condition: python3 -m cairn marker absent --step config_schema --scope once
    continue_on: { failure: true, skipped: true }
```

**`continue_on: {skipped: true}` is mandatory on every gated step, and omitting it is
catastrophic rather than untidy.** A `skipped` status cascades to every dependent, so a
step that correctly no-opped would skip its own verify and commit steps, and from there the
merge join. Worse, a run whose nodes are all `skipped` with no failed node anywhere reports
plain `Succeeded` with exit 0 — a whole plan evaporating into a clean green result.
`emit_step` emits the flag with the gate, so the two cannot be separated.

**`failure` sits beside it for the opposite reason.** Without it a step that reports its own
failure aborts its assertion, and a step that reported failure over work that is actually
there could never be told from one that did nothing
([verify-gate.md](verify-gate.md)). The step still lands a failed node, so the run is still
not clean.

Two consequences follow from the engine's own semantics. Preconditions are evaluated **once
at step start**, not per retry, so the marker must be durable filesystem or git state —
which it is. And a **root-level** precondition aborts the whole run rather than skipping a
step, so marker checks are only ever emitted at step level.

The gate's exit status is a precondition's answer, not a step's outcome, so it reads
backwards from the obvious:

| Exit    | The engine     | Reached by                                  |
| ------- | -------------- | ------------------------------------------- |
| 0       | runs the step  | no marker, a stale key, or any error at all |
| nonzero | skips the step | a fresh marker, positively established      |

**Every error is exit 0**, because a gate that cannot tell whether the work is done must
let the work happen: the task is convergent and its end state is asserted either way,
whereas skipping unverified work leaves nothing downstream to catch it. The gate therefore
does not enumerate the faults it survives — an unclassified one escaping it would exit
nonzero and be read as a fresh marker. Every such decision names the step and its scope on
stderr.

**What the gate cannot defend against is its own absence.** Fail-open is a property of the
gate running; a condition command that never launches exits nonzero from outside Cairn, and
the engine reads that as a skip. Measured against Dagu 2.11.0, a step is given a curated
environment rather than the caller's — `PYTHONPATH` is not among what propagates — so a gate
invoked as a bare `python3 -m cairn` fails to import and
**every step of the plan skips into a clean `Succeeded` with exit 0**. Two things follow,
and both are the generator's: the emitted workflow carries Cairn's own resolution in its
`env:` block, and the preflight asserts the gate command resolves before the run starts.

**The gate writes a report only when it skips.** On that path no step will run to write one,
so the no-op is recorded as `status: noop` carrying the scope and both keys — which is how
the run record names every step that no-opped and under which scope. On the other path it
writes nothing, because a report there would outlive a step that was then killed and claim
an outcome for work that never happened.

## What resume means

Cairn has no resume command, because re-running is resume.

**Resuming a run** is re-running the plan. The state contract is the marker, in git,
committed with the work. Nothing else is consulted and no execution state has to survive
for correctness, which is what makes a dead orchestrator an observability problem rather
than a recovery problem.

**Resuming a session for its report** is the one exception, and it is not a re-run. A
session that ends a turn without producing its structured output has not failed — it may
have done every bit of the work and simply stopped short of saying so. Measured: a step
that did exactly this had made its edit, and the assertion that followed it passed; what
the missing report cost was $10.89 of proven work, the chain behind it, and a run record
claiming the step had "reported failure" when it had reported nothing at all.

So such a session is continued exactly once, with one message asking for the account it
owes and telling it to do no further work. It runs under **what is left** of the step's own
dollar ceiling, because the offer priced one ceiling for the step and a second pass carrying
a fresh one would double what the person agreed to; both passes are summed into the record's
cost and turn count. If it reports, the step is recorded as it should have been. If it does
not, the outcome is exactly the `provider_protocol` failure it already was, with the attempt
recorded beside it — the rescue can never make the outcome worse than not attempting it.

The discrimination is narrow and it is measured: a **correct** structured report is itself a
tool call, so a session that reported returns `stop_reason: "tool_use"` too. The stop reason
decides nothing on its own; the absent `structured_output` beside it is what says the session
never reported.

**Resuming a step killed mid-work** is the worktree's own contents. The marker is absent
because verification never ran, the partial edits are still there because the worktree is
reused, and the convergent task absorbs them without duplicating. This is the one place
uncommitted state is load-bearing, and it is why the derivation declares a task that will
not converge rather than letting it ride.

**Resuming an agent session is not an execution mechanism.** Each step runs as a fresh
session by design. The session id is captured and reported so a human can open a failed
step's transcript and continue by hand — a receipt, not a control flow. Chaining a prior
session into a re-run would carry that session's stale beliefs about a tree other steps
have since changed.

**The engine's own retry is not the resume story.** Engine-held completion state would be a
second completion authority competing with the marker, and two authorities drift. Because
completed steps no-op via the committed marker, a plain re-`start` is idempotent by
construction and `dagu retry` is an optimisation rather than a requirement.

Two things are never paid twice: a step whose work is verified never starts an agent session
again, and a step killed mid-flight never redoes work that reached the disk. What is paid
twice is the killed step's re-orientation, and the plan author's lever against it is step
size.

The commands this protocol adds, and which of them are steps, are in
[cli-contract.md](cli-contract.md) with the rest of the subcommand surface.
