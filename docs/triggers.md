# Triggering a run, and the surface that watches one

A run is a thing you can watch: the topology drawn, each step's state as it changes, its
logs, its cost, and where a failure halted the graph. A finished run is a thing you can read
afterwards, from the same place. Most of that is the engine's, and Cairn links to it rather
than rebuilding it.

**Where a person goes first is the skill's conversation; the view is the link it hands
them.** The commands below are what that skill invokes, and the link is the `view_url` a
run's record carries. (This is a different conversation from the authoring one that writes a
plan graph, which is [plan-derivation.md](plan-derivation.md)'s.) The view answers _what is
happening_ better than any prose can, and it reads a finished run identically to a live one.
It will never answer _what did this cost_ or _has this workflow been edited since Cairn
wrote it_ — there is no cost field anywhere in the engine's model, and the engine records
nothing about an edit at all. Those two are Cairn's, and they live in the run record, which
[report.md](report.md) renders.

## The division of labour

| Concern                            | Owner     | Why                                                                           |
| ---------------------------------- | --------- | ----------------------------------------------------------------------------- |
| The graph, live step state, logs   | Engine    | Drawn already, pushed over server-sent events, in place and without a reload  |
| Timings and the halt point         | Engine    | Per-step start and duration, a Gantt, and the failed node drawn red           |
| Cold reads                         | Engine    | A finished run renders identically and survives the server restarting         |
| Manual, cron and external triggers | Engine    | Cairn supplies the safety and the parameter handling around them              |
| **Cost**                           | **Cairn** | No field exists in the engine's model; it comes from Cairn's own step records |
| **Divergence**                     | **Cairn** | The engine records nothing about an edit, and its audit log is licensed       |
| The verdict                        | Cairn     | A run that dropped a branch reports a clean success at the engine level       |
| The rendered report                | Cairn     | [report.md](report.md): one model, three renderings, one fixed answer order   |

One asymmetry worth knowing: the view labels a precondition-skipped step `Precondition
unmet`, while the state file spells a chain halt and a branch exclusion with the same code.
The reason is rendered, not recorded, so Cairn's report still derives that distinction by
graph position ([run-model.md](run-model.md)).

## Opening the view

The engine serves it, and Cairn neither starts nor manages the process:

```text
dagu server                                  # binds 127.0.0.1:8080 unless told otherwise
http://127.0.0.1:8080/dag-runs/<name>/<run-id>
```

`<name>` is the workflow's filename, which is the link's name once a schedule is installed.
A run's record carries the whole address as `view_url`; on a machine that binds the server
elsewhere, set `CAIRN_VIEW_BASE` for the reader that builds the record.

**Claim the server's admin account when you start one.** It is safe in the sense that
matters here — it holds no run state, so killing it loses nothing — but its authentication
begins unclaimed, and the first local process to call its setup endpoint becomes
administrator. That is a packaging concern rather than this component's, and until it is
closed, starting a server is a decision to make deliberately.

## There are two daemons, and only one is dangerous

**The view's is safe to the run state.** `dagu server` binds loopback, holds no run state,
and reads the same files the CLI writes. Kill it and start another and nothing is lost. A
manual trigger from the view needs nothing else running.

**The scheduler's is not.** While it is up its retry scanner re-executes every failed run
recorded on the machine in the previous 24 hours — including runs outside the directory it
watches, three attempts each. For a tool whose failed runs are paid agent sessions against
git repositories, that is unacceptable by default.

**An external trigger costs the scheduler too**, which is the non-obvious one. A webhook does
not execute a run; it _enqueues_ one, and only the scheduler drains the queue. So a webhook
is not a cheaper alternative to a schedule — it is the same escalation through a different
door.

| Path                   | What must be running | Can vary parameters                        | Carries the retry hazard |
| ---------------------- | -------------------- | ------------------------------------------ | ------------------------ |
| Skill-started run      | nothing              | the occasion and the branch, never the repository | no                |
| Manual trigger from UI | `dagu server`        | one editable field per param               | no                       |
| Cron schedule          | `dagu scheduler`     | **nothing at all**                         | **yes**                  |
| External webhook       | `dagu scheduler`     | **nothing** — the body arrives beside them | **yes**                  |

So a schedule or an external trigger is a deliberate, explained escalation from the one-shot
default, never a side effect of wanting a recurring plan. Wanting the _view_ is not that
escalation and is never priced as one.

## What a caller may vary, and what is refused

A generated workflow declares three parameters, and the engine exposes each as an editable
field at trigger time — so every value a step acts on arrives from outside anything Cairn
checked. What they let a caller vary is the occasion and the branch. **They do not let one
file serve many repositories**: the runs root is resolved at authoring time and emitted into
`env:`, where no trigger surface can move it, so a retargeted run would do its work in one
repository and write its occasion, every step report and its record into the other. A
definition is bound to the repository it was authored for, and retargeting it is
re-authoring it.

**They are judged at the run's first act rather than at any trigger surface.** Cairn owns
none of the four surfaces that can set them, and `cairn lock acquire` is the one node every
path passes through, before the first worktree and before the first paid session. A refusal
there is a failed node carrying its reason — which is what the view draws, what `dagu start`
exits on, and what the record keeps.

| Parameter             | Refused when                                                                                                                                                           |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `CAIRN_REPOSITORY`    | not absolute; or the emitter's splice and the runtime's own derivation of the worktrees root disagree; or it names a different repository than the step is standing in; or the declared runs root is not this repository's |
| `CAIRN_PARENT_BRANCH` | it begins with `-`, or git itself does not accept it as a branch name                                                                                                  |
| `CAIRN_OCCASION`      | present and not an occasion `cairn occasion new` would mint                                                                                                            |

The repository rule is not a rule about slashes. The emitter concatenates the parameter with
the worktrees suffix **as text**, because a parameter reference may stand in `working_dir:`
and nowhere else ([workflow.md](workflow.md)), while `cairn worktree setup` derives the same
directory through `Path`, which normalises. Measured, `/srv/product/` makes the two disagree:
the step's working directory becomes `/srv/product/.cairn-worktrees/…`, **inside the working
tree**, and the engine creates a missing working directory rather than failing. The step then
runs against nothing, its branch carries no work, and the wave's join, both merge slots, both
proofs and the prune all report success having landed nothing — while a generated directory
sits where the plan's own commit step stages everything. So the check compares the two
derivations rather than the spelling, and any spelling that would send work somewhere the
setup did not create is refused.

## Where a run's occasion comes from

An **occasion** is what a trigger mints and a recovery continues, and it is the freshness key
a `run`-scoped step reads; a period scope keys on the bucket its moment falls in
([step-protocol.md](step-protocol.md)).

**The run mints its own at its first act, and the declared parameter is the override.** A
cron firing has no override point at all, so an occasion fixed when the workflow was written
would be reused by every firing — and every `run`- and period-scoped step from the second
firing onward would find a fresh marker, skip, and the run would report a clean success
having done nothing. Measured over three firings of one file: the first did the work, the
second and third reported `succeeded` with the step skipped.

Three properties follow, and the third is the one to remember:

- A **scheduled firing** gets a new run identity and therefore a fresh occasion, so a
  recurring plan does work every time it fires.
- **`dagu retry` continues** the occasion it is recovering, because it reuses the run
  identity and the occasion is recorded under it.
- A **fresh re-run under a new identity mints a new occasion and re-pays** every `run`-scoped
  step. To continue an earlier one deliberately, pass it:
  `--params CAIRN_OCCASION=<the record's lineage.occasion>`.

## Installing a schedule, and the daemon it costs

```text
python3 -m cairn schedule install --plan <slug> --repository <path> --accept-daemon
python3 -m cairn schedule status
python3 -m cairn schedule start --accept-daemon
python3 -m cairn schedule remove --plan <slug> --repository <path>
```

`--accept-daemon` is the escalation, and both verbs require it: `install` because a linked
definition is one a scheduler will fire, and `start` because it becomes that scheduler. Each
prints what is being agreed to before it does anything.

The cron expression goes into the file at authoring time, because a workflow is generated
and never hand-maintained:

```text
python3 -m cairn workflow author <graph.json> --repository <path> --schedule '0 3 * * *'
```

Whose 3am it is is the engine's answer, not Cairn's — it evaluates the expression against
the machine's own clock, while a period-scoped step buckets by UTC
([step-protocol.md](step-protocol.md)). A nightly plan whose steps are `daily`-scoped
therefore fires on local time and expires on UTC, which is worth knowing before choosing an
hour near midnight. The engine validates the expression, which is one of the few places its own
validator is not blind, so Cairn parses none of it.

`install` links the definition into the directory the scheduler watches. Cairn writes
workflows into the repository's own admin directory, which is **not** that directory, so a
file carrying a schedule that was never installed fires never and says nothing. A symlink
rather than a copy, so the workflow keeps one source of truth and a re-authoring is picked up
without a second install. The engine's name for the DAG is the link's filename, which is also
what the view's URL and any webhook endpoint are keyed on — so a name already taken by
another plan is refused rather than replaced.

**`start` becomes the scheduler.** It replaces itself with `dagu scheduler` and runs in
the foreground until killed, so keeping a nightly plan firing means keeping that process
alive — under `launchd`, `systemd`, or a terminal you leave open. Cairn supervises nothing
and offers no `stop`: the process is the daemon.

It asserts, at the moment of starting rather than at install, that the machine is safe
to run a scheduler on, and refuses otherwise **naming every failed run it would have
re-executed**. Two machine-wide properties, both living in the one file every DAG inherits:

- **DAG-level retry disabled.** The engine ships `retry_policy: {limit: 3}` active.
- **Catchup off.** The engine ships `catchup_window: "6h"`, and its own comment reads "all
  missed cron intervals within this window are executed (max 1000)". Every file Cairn emits
  states the empty window that turns it off — measured, `""` is accepted where `0s` is
  refused — so this is a hazard only for DAGs Cairn did not write, which is exactly what the
  scanner reaches.

Both are written by one command, because a person asked one question:

```text
python3 -m cairn supervise base-config --disable
```

`status` is the honest answer to a trigger that was accepted and does nothing: it names every
run sitting **queued** with no scheduler draining it.

## The external trigger

A webhook is created against a running server, which returns a bearer token **shown once**:

```text
POST /api/v1/dags/<name>/webhook
```

Cairn does not create it, and holds no credential by construction. What it records is where
the token went:

```text
python3 -m cairn schedule install --plan <slug> --repository <path> --accept-daemon \
    --webhook-token-sink '1password: cairn/webhooks'
```

The value is free text naming a place, and it lands in
`<repository>/.git/cairn/workflows/<plan>.triggers.json` beside the definition. Cairn never
stores the token, and `schedule remove` takes that record away with the link.

Two constraints are the design rather than choices. **A webhook cannot set parameters**: its
JSON body arrives beside them as `WEBHOOK_PAYLOAD` and `WEBHOOK_HEADERS` while the declared
defaults stand, so a webhook that names a target repository does it by reading that payload
inside a step. And **it enqueues rather than starts**, so it is dead without the scheduler.

## Who started a run

A run started at the view and a run started by Cairn are the same record but for one field:
the view records `triggerActor` as the authenticated username, and Cairn's own trigger
leaves it absent. That difference is free provenance and is kept rather than closed — an
absent actor means Cairn started it, and is never rendered as unknown
([run-model.md](run-model.md)). It rests on the spike that measured it rather than on a test
here: re-proving it needs an authenticated browser session against a running server.

## The human gate

The engine's approve, reject and push-back steps are first-class and unlicensed, so a plan
wanting sign-off between steps needs nothing from Cairn — and Cairn emits none of it. Two
things decide when it is right:

- It is a **manual** gate and never a substitute for the deterministic verify gate
  ([verify-gate.md](verify-gate.md)). A person saying the work looks right is not the same
  fact as a declared assertion passing, and only the second may record a marker.
- **A step waiting on a person holds the repository's run lock for as long as it waits**
  ([supervision.md](supervision.md)). That is why a decision a plan cannot make for itself
  exits the run instead ([step-protocol.md](step-protocol.md)): a person on holiday would
  otherwise hold a repository against every other plan indefinitely.

## Two triggers at once

A trigger arriving while the target repository's run lock is held is **refused with the
holder named** — never queued, never silently dropped. The refusal is a failed
`lock_acquire` node carrying the holder's identity and age, which the view draws, `dagu
start` exits on, and the record keeps.

The engine's own `overlap_policy` is a different question: it decides what happens when one
DAG's firing arrives while that same DAG is still running, and every emitted file states
`skip` rather than inheriting the machine's answer. **This is a knowing exception to "never
silently dropped".** A firing skipped that way never dispatches a node, so Cairn's lock is
never reached and nothing anywhere records that a firing was due — and the engine offers no
spelling at this pin that avoids it, because the alternatives queue paid work against a
scheduler nobody started or discard all but the most recent. The refusal a person can see is
Cairn's lock, and it covers the case that matters, which is two plans against one
repository.

## Hand-editing a generated workflow

Editing in the engine's canvas is a legitimate quick experiment and Cairn does not prevent
it. The engine rewrites the same file in place and records nothing about having done so — no
version field, no checksum, no modification metadata, and its audit log is licensed — so
detection is Cairn's provenance stamp alone. Re-authoring states plainly what it is replacing
and proceeds; it never merges ([workflow.md](workflow.md)).

## What a run leaves behind, watched or not

Every run's release writes its record to `runs/<run-id>/record.json` in Cairn's own state,
whether anyone was watching or not — the exit handler is the only body that runs however the
run ends, because the engine never dispatches a step whose dependency failed. The record
carries the run's verdict, its cost, and `view_url`, so a run is reachable from its identity
alone:

```text
ls <repository>/.git/cairn/runs                      # every run this repository has had
python3 -m cairn record build --run <run-id> --repository <path>
```

A run nobody watched is exactly the one whose identity nobody has, so the directory listing
is the entry point; the view's own page for the workflow is the other. Both reach the same
record ([run-model.md](run-model.md)).

**Nothing the release does may reach its exit status.** Measured against Dagu 2.11.0, a
`handler_on.exit` body exiting nonzero records the whole run as `failed` and makes `dagu
start` exit 1 even when every step succeeded — and because that node is load-bearing
infrastructure in Cairn's own record, Cairn's verdict flips with it. A failure to write the
record would be reported as the run having failed, on the one path nobody is watching. So
the release exits nonzero for exactly one reason, which is that it could not give the lock
back, and everything else it learns rides in its report.

The record written at that moment is honest but not final: the last state line already
carries the run's final status and every step node final, while the run's own finish time
and the handler's own node are not yet recorded. It is regenerable, and a later
`cairn record build` supersedes it.

**What is not built here.** The engine's notification channels are server-side objects
created through its REST API and bound to the engine's own lifecycle events; the only
in-file transport, `mail_on`, fires on the engine's verdict — which is precisely the verdict
Cairn does not trust, because a run that dropped a branch reports clean. Binding one to
Cairn's verdict needs a claimed admin account on a running server, and an unclaimed server is
an open administrator account any local process can take — which is release's to close, not
this component's. Until then an unattended run leaves a record a person can find, and
`cairn schedule status` is where they find the ones nothing is draining — but nothing is
pushed to them.
