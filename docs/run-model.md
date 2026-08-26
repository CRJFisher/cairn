# The run model

One presentation-free model of what happened in one run, extracted from the engine's state
and Cairn's own step reports, durable enough to survive the orchestrator dying and readable
with nothing running. Every rendered surface reads this model and nothing else.

**No reader computes a fact this model does not carry, and no reader decides a verdict.**
Where two renderings disagree, both are compared against the canonical-facts projection
rather than against each other.

The vocabulary below is frozen. Every value appears here and in `cairn/record/vocabulary.py`
and nowhere else; a test asserts the two agree and that no third spelling exists.

## The run verdict

Six values, highest concern first. The order **is** the precedence: a run is described by
the most consequential thing true of it, and the first value it qualifies for wins.

| Rank | Verdict                 | Reached when                                                                                                          |
| ---- | ----------------------- | --------------------------------------------------------------------------------------------------------------------- |
| 1    | `failed`                | any step is `failed` or `not_reached`, or any non-housekeeping infrastructure node is                                 |
| 2    | `blocked`               | no failure, and any step carries the `blocked` overlay                                                                |
| 3    | `running`               | no failure and no block, and any step is `running` or `pending`, or the engine's run reading is `running` or `queued` |
| 4    | `green_with_exclusions` | nothing outstanding, and any step is `excluded` **or any wave's census declined a branch**                            |
| 5    | `all_no_op`             | every step is `no_op`                                                                                                 |
| 6    | `green`                 | everything else                                                                                                       |

**A run with exclusions is never spelled the same as a clean success.** That is I5, and it
is why `green_with_exclusions` is a value of its own rather than a flag on `green`.

**The verdict is derived by walking every node, and the engine's run status is never read as
one.** A run whose exclusions are all `skipped`, with no failed node anywhere, reports plain
`Succeeded` with exit 0 — measured, and kept as the `green-with-exclusions` fixture. Cairn's
own routing pattern is designed so that a real exclusion always leaves a `failed` node
behind ([verify-gate.md](verify-gate.md)); this walk is the check that it did.

**A wave's census is read into the verdict, not only into the git facts.** A branch the join
declined is work the run dropped, and it is the one exclusion no step outcome can speak for:
the join reads a step's gate _report_ while this walk reads the gate's _node_, so a step
whose report is damaged is declined by the join and verified by the walk. Left out, that run
reports a clean success over a branch that never landed — the exact shape I5 forbids. A
census exclusion therefore raises the verdict, raises an `excluded` attention item naming the
branch, and makes the next action `settle_merge`.

**A queued run is pending work, never an outcome.** Anything triggered externally arrives
queued and may sit there indefinitely if no scheduler is up, with every node at not-started.
It reads as `running`, its engine reading is kept verbatim, and its next action is to start
a scheduler.

## The step outcome

Seven values, and three overlays that ride beside them.

| Outcome       | Means                                                 |
| ------------- | ----------------------------------------------------- |
| `verified`    | the assertion passed and the marker was written       |
| `failed`      | the step could not reach its end state                |
| `excluded`    | the step ran and contributed no verified work         |
| `no_op`       | the marker was still fresh, so the step never started |
| `not_reached` | downstream of a halt, so it never ran and never will  |
| `running`     | in flight now                                         |
| `pending`     | a sibling that has not started yet                    |

**`pending` and `not_reached` are distinct on purpose.** One is a run still in flight and
the other is a run that gave up; collapsing them would report the two as the same thing.

A `no_op` carries the scope its key matched under and both keys, so a reader can tell a step
that is permanently done (`once`) from one that is merely fresh enough (`daily`). The
difference between correct caching and stale research is invisible otherwise.

| Overlay      | Rides on             | Means                                                       |
| ------------ | -------------------- | ----------------------------------------------------------- |
| `blocked`    | `excluded`           | a human has to decide something before the plan can proceed |
| `divergence` | `excluded`, `failed` | what the step said and what verification found do not agree |
| `unverified` | any                  | the plan declared this step has no checkable end state      |

Overlays are a list beside the outcome rather than values of it. They are orthogonal — a
block rides an exclusion, a divergence rides either — and folding them in would multiply
seven outcomes by eight combinations, which is exactly how one state acquires four
spellings. They are always emitted in the order above.

Why a step contributed no verified work is a **separate** frozen vocabulary, and
[verify-gate.md](verify-gate.md) owns it. The record carries whichever of those causes the
gate recorded and mints none of its own — one event with two spellings would put two answers
in the record, so the causes are named there and quoted here.

## What needs a person's attention

Seven kinds, in one fixed order, highest concern first:

`blocked`, `failure`, `excluded`, `budget`, `housekeeping_failure`, `divergence`,
`follow_up`.

Naming the order here is what makes every renderer conform to one definition rather than
inventing a subset. It is deliberately **not** the verdict's order: a block outranks a
failure for a reader, because a person can act on it now.

An `excluded` item's subject is a step's id where a step's own gate declined it, and a
**branch name** where a wave's census did — the one exclusion no step outcome can speak for.
Where both saw the same event, it is listed once: the branch a step already accounts for is
the same thing seen twice, and two lines would make the run's own count disagree with itself.

Follow-up work is a per-run snapshot rather than a standing backlog. A no-op re-run re-emits
none, because no-op steps do not redo work — so a re-run showing none reads as correct
rather than as lost signal.

## The next action

A report that says a run failed and stops has answered one question of six. The next action
is derived from the record rather than composed as prose, and is one of:

| Action            | Reached when                                        | The command it carries                       |
| ----------------- | --------------------------------------------------- | -------------------------------------------- |
| `decide`          | a step is blocked on a human decision               | none — a person decides, and no command does |
| `settle_merge`    | a step or a census exclusion left work unlanded     | none                                         |
| `rerun`           | the run failed                                      | `dagu retry --run-id=<run> <plan>`           |
| `start_scheduler` | the run is queued and nothing is draining the queue | `cairn schedule start`                       |
| `wait`            | the run is still in flight                          | none                                         |
| `nothing`         | the run is green, or every step no-opped            | none                                         |

Never the engine's own scheduler command: starting one is where the retry hazard fires, and
Cairn's own is the one that disables it first ([triggers.md](triggers.md)).

**A command is carried only where it can be spelled completely.** Measured against Dagu
2.11.0, the retry takes the run as a required `--run-id` flag and the plan as its operand, so
a run whose plan is absent carries no command at all rather than one that exits
`required flag(s) "run-id" not set` when it is pasted. A run whose orchestrator was killed
carries none either: the engine still calls it running and refuses to retry it, and the
reconciliation that would unblock it takes a path this derivation does not hold.

## The exit-code contract

Frozen apart from the display verdict, so a severity judgement in a report can never
silently redefine what automation sees.

| Code | Meaning                                                           |
| ---- | ----------------------------------------------------------------- |
| `0`  | `green` and `all_no_op`                                           |
| `1`  | `failed`                                                          |
| `3`  | `green_with_exclusions`                                           |
| `4`  | `blocked`                                                         |
| `5`  | `running` — the run has not finished, so it has no outcome        |
| `6`  | no record could be produced; not a statement about the run at all |

`2` is left alone: argparse spends it on usage, and a caller reading 2 as a verdict would be
reading a typo. `green_with_exclusions` carries a code of its own because that is the
distinction automation most needs and the one the engine cannot make.

`python3 -m cairn record build` exits with the run's code. A nonzero exit there reports the
**run's** verdict, never the command's own health.

## Provenance

Three values: `recorded`, `derived`, `absent`.

Every section of the record carries a `provenance` map naming each field that is not plainly
recorded. `recorded` is the default and is not listed — a map that repeated the whole record
would double it for no reader.

**An empty string is an absence too.** The projection spells one `absent` rather than the
empty string it found, because a surface that printed nothing would otherwise agree with the
oracle quietly instead of disagreeing with it.

**The invariant: a field whose value is `null` is listed as `absent`.** A field the engine
did not record is marked absent rather than defaulted to something plausible, because a
plausible default is indistinguishable from a measurement.

Four things the engine never supplies, and where each field's authority sits instead:

- **The exit code as a number.** It survives only inside an error string — `exit status 7`.
  The extraction parses it and marks it `derived`, never `recorded`. A verify step's exit
  code, where the number itself matters, is recorded by Cairn ([verify-gate.md](verify-gate.md)).
- **Cost and session identity.** The engine holds none, so spend accounting rests entirely
  on Cairn's own step reports ([step-protocol.md](step-protocol.md)). This is not a gap to
  work around; it is the reason the reports are the primary source.
- **A per-step process id**, so a step cannot be traced to a process after the fact.
- **A retry count** on a node, so attempts are counted from the engine's separate attempt
  records rather than read off one.

## The record's shape

Declared in `cairn/record/model.py`. The run carries `record_version`, `run_id`, `plan`,
`graph_sha256`, `attempt_id`, `attempts`, `engine_version`, `engine_run_status`,
`engine_run_status_name`, `engine_contradicted`, `owner_alive`, `verdict`, `exit_code`,
`view_url`, `started_at`, `finished_at`, `trigger`, `lineage`, `steps`, `infrastructure`,
`nodes`, `edges`, `waves`, `attention`, `budget`, `git`, `next_action` and `provenance`.

`view_url` is where the engine's own view serves this run — the same address live and cold,
which is what makes a finished run readable from the surface that showed it running
([triggers.md](triggers.md)). It is composed from the engine's **own name for the
workflow**, which is the filename it was started from, rather than from the plan's slug: a
definition published under a second name is served under that one and nowhere else. It is
`derived` rather than recorded, and absent for a run whose state file names no workflow.

It is a field of the record rather than something a surface composes, for the same reason
`resume_command` is: a rendering may state no fact the model does not carry, so a link
composed by three surfaces would be three chances to compose it differently. Where the view
is served is `CAIRN_VIEW_BASE`, else the engine's own `DAGU_HOST` and `DAGU_PORT`, else the
measured default. A base with no scheme is refused rather than used — it would compose a
relative link, which looks like an answer and goes nowhere — and a wildcard bind address
becomes one a browser can actually reach.

Each step carries `step_id`, `outcome`, `overlays`, `cause`, `position`, `asked`, `said`,
`verified`, `divergence`, `freshness`, `completed_by_run`, `branch`, `commit`, `diffstat`,
`cost_usd`, `cost_is_notional`, `turns`, `session_id`, `model`, `transcript`, `stderr_log`,
`resume_command`, `follow_up_work`, `started_at`, `finished_at`, `exit_code`, `nodes` and
`provenance`.

`asked` is the command the engine recorded for the step, which for an agent step contains
the prompt. The plan's own task text does not survive into a run — the generator consumes
the graph and keeps only its digest — so the command is the only durable record of the ask.

`nodes` carries **every** node the engine recorded, including the lifecycle handler and
including one whose name the topology's grammar does not cover. A node dropped for being
unrecognisable is a node whose failure nothing can report.

`edges` carries every dependency the engine enforced, each with a kind: `step` (inside one
step's group), `dependency` (between two steps), `wave` (a join, a merge slot or a prune),
`run` (the run lock).

`infrastructure` is every node whose parsed subject is no step's id — the run lock, a
worktree setup, a wave's join, its merge slots, the proof of each, and its prune. Membership
is the node name's own answer, so there is no second list to keep in step. A failed
infrastructure node fails the run, except a `prune`: it runs after the landing and can only
leave litter behind, so it raises `housekeeping_failure` and nothing more.

## Run identity, the occasion, and lineage

A run has an identity — the engine's own `dagRunId`. An **occasion** is what a scheduled or
deliberate trigger mints and a recovery continues, and it is the key a `run`-scoped step
reads ([step-protocol.md](step-protocol.md)).

**The run mints its own occasion at its first act**, and the declared parameter is the
override rather than the source. A cron firing has no override point at all, so an occasion
fixed when the workflow was written would be reused by every firing — and because the
freshness key for `run` scope _is_ that value, every scoped step from the second firing
onward would find a fresh marker, skip, and the run would report a clean success having done
nothing ([triggers.md](triggers.md)). The value lives at `runs/<run-id>/occasion`, which is
keyed by run identity, so `dagu retry` continues the occasion it is recovering. The record
reads it from the lock's own report, falling back to the parameter for a run that was given
one.

**Lineage rides the marker.** `.steps/<id>.done` names the run that did the work, and the
marker is the only artifact that survives a run, travels through every merge and is per
step. When a later run's gate finds that marker fresh, its no-op report carries that run id,
and the record surfaces it as the step's `completed_by_run`. Without the link, a recovery
run renders as a screen of no-ops with no account of who did the work — technically correct
and useless.

**A no-op keeps the doer's name.** A recovery run's gate opens over a step that did nothing,
because the freshness key still matched, and it rewrites the marker. Stamping that run's
identity there would replace the only durable answer to "who did the work" with the identity
of a run that did none, and every recovery afterwards would inherit it. So only a run that
actually did the work claims it.

**Lineage is an observability contract only.** Completion authority stays with the marker in
git: freshness keys on scope and key alone, and a missing or corrupt lineage never changes
what a run does.

## The trigger

`kind` is one of `unknown`, `scheduler`, `manual`, `webhook`, `subdag`, `retry`, `catchup`.

`actor` names the authenticated user when the trigger came through the engine's own view,
and is absent otherwise — so a run a person started at the view and a run Cairn's own skill
started are the same record but for that one field. **An absent actor means Cairn started
it, and is never rendered as unknown**: `unknown` is a trigger kind the engine can record,
and one word for two facts is one word too few.

## The engine-status mapping

Measured against **Dagu 2.11.0**, which is the pin `cairn/workflow/schema.py` states and
this table imports rather than restating.

**Three vocabularies overlap numerically and mean different things at the same value**, so
the tables are indexed by which one is being read, never by the number alone. `5` is
`skipped` for a node, `queued` for a run and `retry` for a trigger.

| Value | As a **node** | As a **run**          | As a **trigger** |
| ----- | ------------- | --------------------- | ---------------- |
| 0     | `not_started` | —                     | `unknown`        |
| 1     | `running`     | `running`             | `scheduler`      |
| 2     | `failed`      | `failed`              | `manual`         |
| 3     | `aborted`     | —                     | `webhook`        |
| 4     | `succeeded`   | `succeeded`           | `subdag`         |
| 5     | `skipped`     | **`queued`**          | `retry`          |
| 6     | —             | `partially_succeeded` | `catchup`        |

**An unmapped value is a hard error at extraction, never a silent default.** It is raised in
`node_status_name`, `run_status_name` and `trigger_name` and nowhere else, so a version bump
surfaces in exactly three places. The error names the number, which vocabulary was being
read, and the pinned engine version.

Two things this table contributes beyond translation:

- **`aborted` is the cascade.** A node whose dependency failed is recorded at 3 with
  `error: "upstream failed"` and an empty start time. That is what tells a step which will
  never run from one that has not started yet, without walking the graph to find out.
- **A run status contributes nothing to the verdict.** It is carried for display and for the
  `engine_contradicted` flag, and read as a verdict nowhere.

This table is load-bearing beyond version drift, because **the record has no external
contract**. Unlike the engine's input format — a strict JSON Schema, numbered specs, a
conformance suite — its state file is an internal struct whose statuses serialise as bare
integers keyed to declaration order, with no schema, no spec, no conformance test, and live
legacy remaps proving it has already churned ([research-dagu.md]). Two mitigations belong
with it: the version pin above, and the note that the engine's REST API exposes a `status`
**and** a `statusLabel` for every node, so a Cairn willing to run a server has a named
alternative to integer archaeology.

## Reading a run that is live, finished, or killed

**One extraction reads all three, because it always scans.** Each line of the engine's state
file is a full snapshot rather than a diff, appended and fsynced during the run — which is
what makes it tailable for live progress. But the file is compacted to a single line when
the attempt closes, so a cold reader must scan start-to-EOF and take the last valid line,
never tail a fixed number of lines. A line a kill cut mid-character fails to parse and is
skipped like any other damage.

**After a crash the record lies.** A `kill -9`'d run stays `running` with no finish time
forever. So liveness is decided from the recorded process id together with the moment that
process started, and **never from the status field**: a bare existence check would call a
recycled identifier alive. A run whose status is `running` and whose owner is provably gone
reads as `failed`, with every step that was in flight carrying `orchestrator_died`.

What a crash costs is stated rather than discovered: the killed step's finish time, exit
code, cost, turns and session identity are all absent, each carrying `absent` in its
provenance map.

This has a consequence worth stating plainly: **liveness is a property of now, not of the
file.** A recorded mid-run snapshot read later reads as a crash, because the process that
was running it really is gone. The same bytes read while that process lives read as
`running`. The `mid-run` fixture is read both ways, and the pair is the proof that the
status field was never the evidence.

Reconciling the engine's own record is [supervision.md](supervision.md)'s and is a **write**;
this model only reads, so the two never race and a record does not need the repair to be
honest.

## A wave's exclusions

`cairn wave join` runs before any slot moves a branch tip and records three sets: the
branches that arrived with work, those the gate declined with its own frozen cause, and
those already contained in the parent. After the first landing that distinction is
unrecoverable — a landed branch and a branch that never carried work are both ancestors of
the parent — so **the census is the only honest source and the record never re-derives it
from git.** The extraction issues no git command at all.

A branch in `settled` carries no cause: it landed on an earlier run, or its step had nothing
to commit. Recording one for it would put an invented cause in the one census that cannot be
taken again.

## Untrusted text

Agent output and repository content are both untrusted input to a renderer. The policy is
**one normalisation pass** — string coercion, control-character stripping, a length cap —
and then **context-specific escaping at each final sink**, never the other way round.

A pre-escaped string is never stored or reused across contexts: an escape is a property of
where text is going rather than of what it is, and text escaped for one sink is wrong in the
next. So what this model stores is unescaped. `<script>` survives here as itself and is
escaped by whichever surface renders it.

The pass strips every Unicode `Cc` and `Cf` character, plus `U+2028` and `U+2029` — which
end a statement in a script context, split `str.splitlines`, and are invisible everywhere
else. A summary keeps neither newlines nor tabs and is capped at 200 characters, the length
the committed marker has always held; prose that is allowed its own shape keeps `\n` and
`\t` and is capped at 2000.

Numbers get the same treatment for the same reason. A cost that is not a finite, non-negative
number is absent rather than zero: `NaN` is a legal Python float and is not legal JSON, so
admitting one would cost every reader the whole record rather than one field.

## The canonical-facts projection

`canonical_facts(record)` returns every fact a rendering may state, as an ordered list of
`(key, value)` pairs with string values. The report's renderers consume it alongside the
model, and it is the drift oracle between them.

A pair list rather than a mapping, because the ordering is part of the contract and a JSON
object's key order is incidental in some readers. Values are strings because containment is
what an oracle test can assert. An absent value spells `absent` rather than an empty string,
so a renderer that printed a zero disagrees with the oracle instead of agreeing quietly.

Keys are `run.*`, `budget.*`, `git.*`, then `run.steps.<outcome>` — one count per step
outcome, always present and zero where none — then `step.<id>.*` in the record's own order,
then `attention.<n>.*` in the frozen attention order, then `infrastructure.<name>.*`, then
`wave.<n>.*` with a nested `wave.<n>.excluded.<branch>.*` per declined branch.

**The projection is total over what a surface may state**, which is what makes a count a
fact rather than a surface's arithmetic. Every number the report prints — how many steps
no-opped, how many were excluded, how many nodes the graph has, how many items need
attention — is a key here, so a rendering that prints one has been given it rather than
having worked it out.

## Reading a record

```text
python3 -m cairn record build --run <run-id> --repository <path>   # write it, print its path
python3 -m cairn record facts --run <run-id> --repository <path>   # print the projection
```

Both run outside any run, take no runtime identity and leave no step report, like
`cairn plan`, `cairn supervise` and `cairn workflow` ([cli-contract.md](cli-contract.md)).
Both find the engine's own state by the run id inside it rather than by its path, so
`--engine-records` is needed only to read a history that is not the engine's default.

**Both exit with the run's verdict**, never with their own health — a green run exits 0 and
a run with exclusions exits 3, whatever either of them printed.

## Where a run's records live

```text
<repository>/.git/cairn/
  workflows/<plan>.yaml            the generated definition
  offers/<offer-id>.json             one run offered, with the price it was offered at
  offers/<offer-id>.spent            that offer consumed, once
  runs/<run-id>/reports/<node>.json  every step's own account
  runs/<run-id>/occasion             the occasion every scoped step in this run keys on
  runs/<run-id>/record.json          this model
  runs/<run-id>/engine.log           what the engine said while taking the run on
```

`engine.log` is the **start command's** own output and not the run's step logs, which are the
engine's and which the view renders. It is the only account of a start the engine never
registered, and it is appended rather than truncated, because a run directory is per run and
a recovery against the same run id must not delete the evidence of the attempt it continues.

The runs root is resolved once at authoring time and travels to every step in the emitted
workflow's `env:` block as `CAIRN_RUNS_DIR`; a step composes its own path from that root and
the run id the engine gave it. Measured against Dagu 2.11.0, an `env:` entry reaches a step,
a precondition **and** the lifecycle handler, which is what lets the run's release write the
one report a failed run always has to leave.

Four properties, each a decision:

- **Outside every working tree**, so no commit step can sweep it up and no worktree removal
  can take it away. It shares the admin directory every worktree of one repository resolves
  to, so a step standing in a worktree and a step standing in the repository write to one
  place.
- **Keyed by run identity**, so a recovery run never writes over the record of the run it is
  recovering from. A run id is a caller's string and becomes a path segment, so one that is
  not a single plain segment is refused rather than resolved.
- **Per run rather than per attempt.** `dagu retry` reuses the run id and preserves the steps
  it skips, so a per-attempt directory would hide the earlier attempt's reports and the
  record would show finished work as never having happened.
- **Regenerable.** The record is derived from the engine's state and the run's own reports,
  both of which outlive it. It is replaced atomically — written to a temporary file in the
  same directory, fsynced, then renamed — so a killed live refresh leaves the previous whole
  record or none, never a plausible truncated one.

There is no fallback. A step that cannot say where its account goes fails loudly rather than
writing one somewhere nothing will look.

## The fixture corpus

`fixtures/runs/<shape>/` holds `status.jsonl` copied verbatim from a real Dagu 2.11.0 run,
that run's own step reports, and a `recording.json` naming the engine version, why the shape
exists, and any field set by hand. `scripts/record_runs.py` re-records them.

Every state file is a real engine run, because the corpus exists to prove things about the
engine and the fixture the exit criteria name is a claim only the engine can make. Every
shape but one is a command step and costs nothing to re-record; `agent` runs a real provider
and spends real money, which is why it is one step and not a plan.

| Shape                   | What it pins                                                                                                                                               |
| ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `green`                 | every step verified; one compacted line                                                                                                                    |
| `red`                   | a step fails and everything behind it is `not_reached` at status 3                                                                                         |
| `blocked`               | a human decision is owed; the engine still reports a clean success                                                                                         |
| `green-with-exclusions` | **the engine reports `Succeeded` with exit 0 over an excluded step, with no failed node anywhere.** I5's regression fixture                                |
| `all-no-op`             | the real marker gate skipped every step; each no-op names the run that did the work                                                                        |
| `mid-run`               | five snapshot lines, uncompacted; a step running and a sibling `pending`                                                                                   |
| `crashed`               | the orchestrator was killed; the engine's record still says `running` with no finish time                                                                  |
| `agent`                 | one real paid agent step, so a step's receipts — cost, turns, session identity, transcript, resume command — are carried populated rather than only absent |

`green`, `all-no-op` and `blocked` all carry engine run status `4`. That they extract to
`green`, `all_no_op` and `blocked` is what proves the verdict is not read off the engine.
