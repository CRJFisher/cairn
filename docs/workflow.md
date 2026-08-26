# Generating a workflow, and refusing to run a malformed one

A workflow is generated from a plan graph and never hand-written. Two components do it: the
**emitter** reads the plan, derives the topology, and emits every node through the kind
table; the **preflight** then refuses to run anything Cairn would not stake the run on.

The preflight is a component rather than a step inside the emitter because the engine's own
validation has holes, and every one of them reports success. Measured against Dagu 2.11.0,
`dagu validate` exits 0 on a file carrying a dependency cycle, an unresolved `${...}`,
`mark_success`, a step with no timeout, and a step with no working directory. Cairn already
holds a validated graph ([plan-contract.md](plan-contract.md)); the preflight's job is to
carry that guarantee across a translation the engine will not check.

## The file is JSON

The emitted definition is JSON in a `.yaml` file. JSON is a subset of YAML and the engine
loads it, so this is a spelling rather than a format change.

Two properties follow, and both are the reason. **The preflight re-reads exactly what the
engine will read** — there is no YAML parser in the standard library, so a hand-written
block-YAML writer would need a hand-written reader to check its own output, and the check
would then measure the reader rather than the file. And **quoting stops being a rule and
becomes a property**: an unquoted `false` is rejected at load, and a JSON string can never
render as a bare `false` whatever it holds.

The cost is that a person reading the file in the engine's own view reads JSON. The file is
generated and never hand-maintained, and an edit to it is a divergence rather than a
workflow, so that cost is accepted.

## Where a parameter may stand

A caller may vary three values, each declared as a `params:` entry and each an editable field
in the engine's own start dialog: `CAIRN_REPOSITORY`, `CAIRN_PARENT_BRANCH`, and
`CAIRN_OCCASION`.

**A parameter reference stands in `working_dir:` and nowhere else.** Measured against Dagu
2.11.0, the same reference behaves differently in every position it can occupy:

| Position              | Value holding a space      | Value holding `$(…)`          |
| --------------------- | -------------------------- | ----------------------------- |
| `working_dir:`        | one path, correctly        | inert — it is never a shell   |
| `run:`, bare          | **split into three words** | inert                         |
| `run:`, double-quoted | one word, correctly        | **the substitution executes** |
| `run:`, single-quoted | **not substituted at all** | inert                         |

Single quotes are what `shlex.quote` produces, so a reference routed through the emitters'
own joiner arrives as its own literal text. A bare token loses any path containing a space.
A double-quoted one runs whatever the value holds — and a parameter is editable at trigger
time, so that is a command-injection surface rather than a corner case.

So every other per-target value reaches a step **through the environment**, because the
engine exports each declared parameter into every step, precondition and lifecycle handler.
Nothing Cairn emits into a body needs quoting it did not already have, and the preflight
refuses a parameter reference in any body or condition.

**A step's exit status is the one reference that stands elsewhere**, as
`${<id>.exit_code}` in the precondition of the marker a gate protects. The engine resolves
that form itself, before any shell and regardless of quoting ([verify-gate.md](verify-gate.md)),
so it is neither split nor executed — and it is the only other `${` a generated file carries.

Two consequences reach the command line. `cairn worktree setup` and `cairn worktree prune`
take `--plan` and `--step` and derive the worktree path from the repository they already
stand in, because a path names one target. `cairn merge land`, `cairn merge verify` and
`cairn wave join` read the branch they land into from `CAIRN_PARENT_BRANCH`.

## What every file states explicitly

Omission is inheritance, not neutrality: the engine writes `~/.config/dagu/base.yaml` on its
first invocation and every DAG on that machine inherits it.

| Field                | Emitted as                    | What omission would mean                                                 |
| -------------------- | ----------------------------- | ------------------------------------------------------------------------ |
| `type`               | `graph`                       | the machine decides; `chain` silently serialises it                      |
| `max_active_steps`   | the node count                | **zero also means "unset"** — see below                                  |
| `retry_policy`       | `{limit: 0, interval_sec: 1}` | three replays of every paid agent session                                |
| `timeout_sec`        | on every step                 | there is no default; a step ran 35m uninterrupted                        |
| `working_dir`        | on every step                 | the step runs in a generated scratch directory                           |
| `env: PYTHONPATH`    | the package root              | the gate cannot import Cairn and every step skips                        |
| `catchup_window`     | the empty string              | every cron slot missed while the machine slept replays as a paid session |
| `overlap_policy`     | `skip`                        | the machine decides what a firing arriving mid-run costs                 |
| no top-level `name:` | —                             | the validator rejects the file a run would accept                        |

`schedule` is the one optional root key, written when `author --schedule` is given and
absent otherwise. Measured, the empty catchup window is the only spelling that turns
replay off — a zero duration is refused as "duration must be positive" — and the engine
ships this machine's own base configuration with `"6h"` in it ([triggers.md](triggers.md)).

**`max_active_steps: 0` does not mean unlimited.** Measured: with `base.yaml` carrying a cap
of ten, a file emitting zero ran ten steps at a time — exactly as a file omitting the field
did — while a file emitting a positive number ran that many. Zero is inheritance wearing the
shape of an override, so the emitter states a number no wave can exceed.

The release runs as the workflow's `handler_on.exit` rather than as a node, because the
engine never dispatches a step whose dependency failed and a failed run must still give its
repository back.

## The join

A wave's commits feed one `join`, which runs `cairn wave join`. It records which branches
arrived with work and, for each that did not, the cause the gate itself recorded.

It exists because that census can only be taken once. A slot's landing moves a branch tip,
and a branch that has landed is an ancestor of the parent exactly as a branch that never
carried work is — so after the first merge nothing can tell an excluded step from a landed
one ([merge-step.md](merge-step.md)). The join stands before any landing and depends on every
commit in its wave, so it is the one node that sees the whole wave intact.

It carries no `continue_on` in either spelling, and it never refuses. An excluded branch
reaches it already, because that branch's commit stops the skip cascade in a fan-out. What
must not be absorbed is a genuine failure, which has to stop the slots rather than let them
land over a wave nobody could survey.

## What the preflight refuses

Every rule reads the document **re-parsed from the bytes on disk**, so a fault in
serialisation is inside the blast radius rather than behind it. A refusal is a hard stop
naming the offending step, never a warning a run proceeds past.

| Rule                           | What it prevents                                                     |
| ------------------------------ | -------------------------------------------------------------------- |
| `cycle`                        | the run never starts, and `dagu validate` exits 0 on the same file   |
| `unresolved_reference`         | the reference empties and the step runs on a corrupted argument      |
| `reference_out_of_position`    | quoting decides whether it substitutes, splits, or executes          |
| `with_block`                   | executor configuration is retyped by YAML behind Cairn's back        |
| `mark_success`                 | a failed step is rewritten as succeeded, on disk and in the API      |
| `continue_on_output`           | routing on stdout text, which for an agent step is self-report       |
| `assertion_absorbs_no_failure` | one branch's failed assertion aborts the merge join                  |
| `absorbs_a_failure`            | the next merge slot writes over a conflicted index                   |
| `reference_without_id`         | it resolves to nothing and the branch drops with no failed node      |
| `gate_without_skipped`         | a correct no-op cascades and the plan evaporates into a success      |
| `commit_without_skipped`       | an excluded branch's skip cascades and the wave lands nothing        |
| `marker_with_skipped`          | the commit runs anyway and lands exactly the unverified work         |
| `gate_unresolvable`            | every step skips into a clean success                                |
| `foreign_condition`            | the gate runs a command Cairn did not write, and `dagu dry` runs it  |
| `scope_without_occasion`       | the step re-pays and is excluded on every run, for ever              |
| `missing_timeout`              | there is no default; the step can hang for ever                      |
| `unbounded_session`            | a paid session opens whose price and model nobody stated             |
| `missing_working_dir`          | the step runs in a scratch directory, not the repository             |
| `wrong_graph_type`             | one deletes the dependency graph, the other serialises it            |
| `body_not_one_invocation`      | logic in a generated file is untestable                              |
| `top_level_name`               | the validator rejects the file while a run would accept it           |
| `node_name`                    | the run model cannot parse the name back into a role and a step      |
| `unexpected_id`                | a step exempts its own body from the one-invocation rule             |
| `unexpected_handler`           | a lifecycle body runs that no rule has looked at                     |
| `unbounded_retry`              | the machine's own configuration decides how often paid work repeats  |
| `undeclared_parameter`         | a caller can vary something the run cannot survive varying           |
| `inherited_concurrency`        | zero reads as unset, so the machine's cap decides the width          |
| `catchup_replay`               | a cron slot missed while the machine slept replays as a paid session |
| `inherited_overlap`            | the machine decides what a firing arriving mid-run costs             |
| `schedule_with_fixed_occasion` | every firing after the first no-ops into a clean success             |
| `foreign_root_key`             | the machine's own configuration decides a field no rule has read     |
| `not_a_document`               | there is nothing here a run could be built from                      |
| `engine_validate`              | the engine refuses to load the file                                  |
| `engine_dry`                   | the engine cannot build an execution plan from the file              |

Several of these are narrower than they first appear, and every distinction is read off the
file rather than guessed — because the file may not be one Cairn wrote.

A step's own assertion is the node named `verify_<step>` **whose `id` is that step's own
handle**, and it alone is exempt from the one-invocation rule and required to carry
`continue_on: {failure: true}`. Bound to the presence of an `id` alone, any step could exempt
its own body by declaring one. A merge's proof is also a `verify` node, and it is told apart
by name: no node of a merge chain carries `continue_on` in either spelling, because a flag
there lets the next slot write over a conflicted index.

A marker-gated step must carry `continue_on: {skipped: true}` and a verify-gated one must
not, because a flag there lets the commit land exactly the unverified work the gate refused
to record. A commit carries it exactly when a join waits on that commit, which is what makes
the wave's position readable from the file rather than guessed from the name. And a gate is
recognised by its argv rather than by text anywhere in the condition, so a plan that reads a
file called `verify gate.md` is not mistaken for one.

**Every precondition must be a gate Cairn emitted.** `dagu dry` executes preconditions for
real, so the mandatory gate runs whatever a file's conditions contain; a condition Cairn did
not write is refused before the engine is ever asked.

**The gate command is proven by running it**, under the environment the file declares and
from a directory holding no `cairn` package. A gate that cannot launch exits nonzero from
outside Cairn, and the engine reads any nonzero as "skip this step" — so a plan whose Cairn
does not resolve skips every step and reports a clean success with nothing done.

## The mandatory gate, and what it still misses

`dagu validate` and `dagu dry` both run, in that order, before any run may start, and the
engine version is checked first. Both run against a scratch data directory: `dagu dry` writes,
and an engine home the binary has never seen is created carrying `retry_policy: {limit: 3}`
**active** — arming precisely the scheduler hazard that re-executes every failed run on the
machine ([supervision.md](supervision.md)). The path handed over is absolute, because a
relative path the engine cannot find is silently re-resolved against its own `dags`
directory.

The gate is mandatory and it is **not** sufficient. Between them the two commands still pass
an unresolved substitution, a missing working directory, `mark_success`, a body that is not
one invocation, and a gate command that cannot launch. That is the whole of why the preflight
runs first and refuses on its own authority.

The pin is `2.11.0`, compared exactly. The workflow format carries no version field of its
own, so the pinned engine is the installed binary and nothing else, and a mismatch presents
as format drift rather than as a load error. A halt names both versions.

## Provenance, and the divergence nothing else reports

The engine's editing surface rewrites a workflow in place and records nothing about having
done so — no version field, no checksum, no modification metadata, and the audit log is
licensed ([03]). So detection is Cairn's.

The stamp lives in two places. In the file's `labels` — measured to accept arbitrary keys and
to survive both engine checks — it carries the plan's identity and a hash of everything but
itself. In `<workflow>.stamp.json` beside it, it carries the emitted file's own hash.

Re-authoring reads both and says one of the following, then **proceeds**. It never merges: the
plan document is the source of truth, and a merge between a generated file and an edited one
would produce something neither was reviewed as.

| Observed                                | What re-authoring says                        |
| --------------------------------------- | --------------------------------------------- |
| no file, no stamp                       | writing it                                    |
| both agree                              | replacing it, unmodified since Cairn wrote it |
| the body hash differs                   | modified since Cairn wrote it, naming both    |
| the file no longer parses               | describable only by its hash                  |
| no Cairn labels at all                  | Cairn did not write it                        |
| a stamp naming a different plan         | generated from another plan                   |
| the file is gone but the stamp is not   | it was generated and is no longer there       |
| another generator's version in `labels` | it was written by generator N, and this is M  |
| the plan's own digest has moved         | the plan changed since this was generated     |

An edit outranks the generation, because someone having touched the file is the more urgent
thing to say — but the state of Cairn's own record does not, because the generation rides in
the file's labels. A workflow whose record is gone is still named for the shape that wrote it.

The last row is the one the workflow's own bytes cannot show, and the plan's digest is read
from the labels for exactly the reason the generation is. A hand edit to the _workflow_ moves
its body hash; an edit to the **plan** leaves the workflow untouched and silently stale, so
the digest the file records is compared against the plan being authored from and nothing else
would have said so. `check` never asks, because it has no plan in hand — and the absence of
one is never reported as agreement with it.

The two rows a state record alone could not see are the fourth and fifth: a workflow deleted
and re-created, or replaced wholesale, arrives carrying no labels, and nothing in Cairn's own
state could have told that from a file it had simply never seen. Carrying the stamp in the
file is what closes them — which answers the question doc 11 left open.

## The recorded shape, and what moves the generator's version

The stamp above only means something if the version in it moves when the shape does. What
holds it to that is a recorded copy of the whole emitted file.

`fixtures/workflows/<name>.yaml` is what the emitter writes for `fixtures/plans/<name>/`, one
per topology shape — `single-step`, `linear-chain`, `all-roots`, `fan-out`, `multi-wave` and
`mixed-kinds` — and each carries its plan's name back as its `cairn_plan` label. The suite
compares each one byte for byte against what the emitter writes.

Six shapes rather than five topologies, because `all-roots` is the only one whose first wave
is itself a fan: its worktrees hang off the run lock, where every other shape's hang off the
previous wave's commit.

This is what a property cannot do. Every rule above holds for any document that has it, so a
change anywhere else in the emitted file passes them all; a recorded file holds for exactly
the file the emitter writes, and a change to any of it has to be looked at by a person.

Every input is pinned, because a golden only one machine could reproduce records nothing:

| Input            | Pinned to                      | Without the pin                                   |
| ---------------- | ------------------------------ | ------------------------------------------------- |
| the plan graph   | six plans in `fixtures/plans/` | the input would live in test code, unvalidated    |
| the repository   | `/srv/work/product`            | every worktree path would name a developer's home |
| the branch       | `main`                         | the parameter would carry a local branch          |
| the occasion     | the empty string               | nothing: the run mints its own                    |
| the package root | `/opt/cairn`                   | `PYTHONPATH` would carry this checkout            |

Because that package root is a fiction, a recorded file is judged by the preflight's rules and
never by the gate rehearsal, which runs `python3 -m cairn` under the `PYTHONPATH` the file
declares. Both engine checks still pass on one, which the suite holds to for `multi-wave` exactly
as committed — and that is the silent failure this whole component exists for: a gate that cannot launch
exits nonzero, the engine reads nonzero as a skip, and a run whose every step skipped reports
a clean success.

Regenerate them from this directory:

```text
python3 -m scripts.regenerate_workflows
```

**The command refuses when the emitted file moved under a generator version that already
described another one**, and writes nothing at all in that case — not even the shapes that did
not move. This is the reader `GENERATOR_VERSION` claims to have: it rides in every emitted
file's `cairn_generator` label so that re-authoring can tell a person their workflow was
written by a shape Cairn no longer emits (the last row of the table above), and that claim
holds only if the version moves whenever the file does. Regenerating is exactly the moment
nobody is thinking about the version, because the suite goes green either way once the bytes
are rewritten; a refusal is the only thing that puts the constant back in front of a person.

The evidence is the file itself. The recorded document is re-serialised and compared to the
rebuilt one, so a new label and a reordering count as the shape moving alongside a changed
command; and everything the emitter was handed — `cairn_graph_sha256`, and the three pins the
file carries back in its `params` and `env` — says whether the input moved too. **Output
moved while input did not** is the generator having moved and nothing else. A plan edited in
the corpus moves the graph digest, a re-pin moves a pin, and both regenerate freely.

Two things it does not do. A person who rewrites or deletes the recorded bytes by hand is
choosing to, and this raises the cost of skipping the decision rather than making it
impossible. And the shapes in the corpus are the whole of its claim: a construct no plan there
holds — a declined assertion, a step with retries, a plan whose repository path holds a space
— is covered by the rules and the properties, not by a recorded file.

## Where the files live

| Thing                | Path                                                   |
| -------------------- | ------------------------------------------------------ |
| the definition       | `<git-common-dir>/cairn/workflows/<plan-slug>.yaml`    |
| the provenance stamp | the same name plus `.stamp.json`                       |
| the authoring copy   | a file under a dotted sibling **directory**, carrying the published name so the gate judges the DAG that will run, replaced into place only if it gates |
| the gate's scratch   | a temporary directory, removed afterwards              |

Nothing generated is written into the repository's working tree. The admin directory is
where git's own files live, so no commit step can stage a generated file and no worktree
removal can take it away.

A definition is **always** written to a file and handed to the engine by path. It is tens of
kilobytes and it is never re-emitted inline through a conversation, where it could not be
reproduced faithfully. It is gated where it cannot be run from and moved into place only
once it passes, so a refused definition never reaches the path a run would start from.

The last row is the one the workflow's own bytes cannot show. A hand edit to the _workflow_
moves its body hash; an edit to the **plan** leaves the workflow untouched and silently
stale, so the digest the stamp records is compared against the plan being authored from and
nothing else would have said so. `check` never asks, because it has no plan in hand — and
the absence of one is never reported as agreement with it.

Every emitted workflow declares `CAIRN_RUNS_DIR` beside `PYTHONPATH`, resolved at authoring
time, and a step composes its own report path from that root and the run id the engine gave
it ([run-model.md](run-model.md)). Measured against Dagu 2.11.0, an `env:` entry reaches a
step, a precondition **and** the lifecycle handler, which is what lets the run's release
write the one report a failed run always has to leave.

## The command line

```text
python3 -m cairn workflow author <graph.json> --repository <path>
                                 [--parent-branch <name>] [--python-path <dir>]
                                 [--out <path>] [--schedule '<cron>']
python3 -m cairn workflow check  <workflow.yaml>
```

`author` is the only thing in Cairn that writes an engine definition; `check` reads one and
writes nothing. Both run at authoring time, take no runtime identity and leave no step
report, alongside `cairn plan`, `cairn occasion` and `cairn supervise`
([cli-contract.md](cli-contract.md)).
