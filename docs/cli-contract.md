# Internal CLI contract

`python3 -m cairn` is an implementation surface for generated workflows, not a user
interface. It is the only entry point, and the skill invokes it: a person asks for what they
want and never learns one of these lines ([../SKILL.md](../SKILL.md)). Runtime commands — `exec`, `wait`,
`agent`, `marker write`, `lock`, `worktree`, `commit`, `merge`, `wave` — dispatch through a
dictionary and run inside a step. Eleven commands take no part in that dispatch: `cairn plan …`
runs at derivation time
against a graph on disk, `cairn workflow …` generates and checks an engine definition
([workflow.md](workflow.md)), `cairn occasion new` mints an occasion a caller means to pin,
`cairn marker absent` ([step-protocol.md](step-protocol.md)) and `cairn verify gate`
([verify-gate.md](verify-gate.md)) run as preconditions, before their step starts,
`cairn supervise …` repairs a run that is over, `cairn record …` reads one
([run-model.md](run-model.md)), `cairn report …` renders it ([report.md](report.md)), and
`cairn schedule …` installs a recurring trigger and starts the daemon it costs
([triggers.md](triggers.md)), `cairn run …` offers a run and starts exactly the one an offer
authorised, and `cairn explain …` answers what a workflow would do, what a frozen word means
and why a step was excluded. None of the eleven takes a runtime identity, and only the verify
gate leaves a report — under the name of the step it gated, on the one path where no step
will run to write one.

Every runtime subcommand self-identifies from Dagu's environment. `DAG_RUN_ID`,
`DAG_RUN_STEP_NAME`, `DAG_RUN_WORK_DIR` and `CAIRN_RUNS_DIR` are required; missing identity
is a loud failure. There is no fallback for the last of them: a step that cannot say where
its account goes must fail rather than write one somewhere nothing will look.
The step's own stdout and stderr paths are not, because nothing reads them. A lifecycle
handler is given the same identity as a step, under the step name `onExit`, so the run's
release resolves a report path like any other subcommand.

A subcommand reads a per-target value from the environment rather than its argv, because a
generated workflow declares each as a parameter and the engine exports every parameter into
the step's environment. `CAIRN_PARENT_BRANCH` is the branch a merge lands into, a worktree is
based on, and a prune deletes against; a missing one is `invalid_arguments`
([workflow.md](workflow.md)).

**The working directory is the process's own**, because that is where the engine puts a
step it was given a `working_dir` for. `DAG_RUN_WORK_DIR` names something else entirely — a
scratch directory under the run's data, where `git rev-parse` reports no repository — so it
is required as proof the step was engine-launched and never used as a path. Every emitted
step carries `working_dir`, including verify steps: omit it and the step runs in that
scratch directory, and a verify command in the wrong worktree asserts the wrong thing.

The report's location, its fields, and the meaning of each status are
[step-protocol.md](step-protocol.md)'s, and are stated there only. What this contract adds
is the routing: generic facts stay at top level while model, session, notional cost, turns,
permission decisions, scope and freshness key stay under `detail`; exit zero means
done/no-op except when `needs_user_decision` deliberately blocks routing with
`user_decision_required`; a terminal failure is nonzero, and `cause` explains it.

The cause vocabulary is closed. Doc 05 issues `command_failed`, `wait_timeout`,
`cancelled`, `provider_failed`, `provider_protocol`, `provider_unavailable`,
`reported_failure`, `user_decision_required`, `rate_limited`, `budget_exhausted`,
`turn_limit`, `process_launch_failed`, `invalid_command`, `invalid_wait`,
`invalid_arguments`, `invalid_report`, `missing_runtime_identity`, and `internal_error`.
Doc 06 adds `invalid_marker`, `invalid_occasion`, `invalid_reads`, `invalid_scope`,
`invalid_step_id`, `marker_ignored`, and `missing_report`. Docs 07 and
09 add `git_failed`, `not_a_repository`, `git_mutex_timeout`, `merge_in_progress`,
`repository_busy`, `repository_dirty`, `lock_not_held`, `base_retry_enabled`,
`base_config_unreadable`, `worktree_dirty`, `worktree_foreign`, and `worktree_unusable`.
Doc 10 adds `merge_conflict`, `merge_not_landed`, `conflict_markers_committed`,
`merge_indeterminate`, `merge_unowned_conflict`, `merge_wrong_branch`, and
`merge_environment_redirected`. Doc 13 adds `engine_paths_unreadable`, which `lock
acquire` raises when the engine cannot say where it keeps its run history.
`base_catchup_enabled` is **not** in that vocabulary: it is raised only by `cairn schedule`,
which leaves no report, so like the three below it is an exit diagnosis rather than a
cause.
`run_record_unreadable`, `engine_status_unmapped` and `invalid_run_id` are not in that
vocabulary: the first two are raised only by `cairn supervise` and `cairn record`, which
leave no report, so they are exit diagnoses rather than causes. `invalid_run_id` is raised
where runtime identity itself is being resolved, which is before there is anywhere to write.

Why a step contributed no verified work is a **second, distinct vocabulary**: it answers a
question about a branch in a run rather than about one process's exit status, and it is
frozen in [verify-gate.md](verify-gate.md). The verify gate's report carries a value from
that set as its `cause`.

Exit status carries one further distinction. A rate-limited agent step leaves on **75**
rather than 1, so a report can say the run stopped because of a limit rather than because
the work was wrong. It drives no retry; see [supervision.md](supervision.md).

Once runtime identity resolves, a report is the one thing a subcommand always leaves.
An unclassified crash becomes `internal_error` rather than a traceback with no record;
argument skew between an emitted workflow and an upgraded binary becomes
`invalid_arguments` rather than a usage message the engine cannot route on; and an outcome
the writer itself cannot record degrades to `invalid_report` rather than to nothing.
Identity that never resolves is the single exception, because there is nowhere to write
to; it exits nonzero and says so on stderr.

Cancellation is a property of the command line, not of one subcommand. Every runtime
dispatch runs inside a scope that turns a step-directed `SIGTERM` into an unwind, so
whichever subcommand is running stops its own child, sweeps any descendant the child's
shell left behind, records `cancelled`, and exits nonzero. Children stay inside Dagu's step
process group, so an uncatchable kill still leaves the engine reaping the whole tree. The
report write itself is the one stretch that ignores a further stop signal: a step killed
while unwinding is exactly the case its report matters most for.

`exec` receives source-quoted executable text explicitly, never derives it from the prose
task, and runs it through `/bin/sh` — or the absolute shell `--shell` names, a relative one
being `invalid_command` — in the context working directory. It records the command and
preserves a normal child exit status, reporting a signalled child the way a shell does.

`wait` requires exactly one of `--until` and `--for` plus a positive, finite `--timeout`;
polling and fixed durations are bounded, and polling never sleeps past the bound. The bound
is Cairn's, and the emitted step's `timeout_sec` is set fifteen seconds above it, so a wait
that runs out reports `wait_timeout` instead of racing the engine's own kill for the same
instant. That grace counts in the run's declared maximum too.

The Claude provider invokes plain `claude -p --output-format stream-json --verbose
--json-schema … --session-id … --permission-mode auto`, sends the prompt on stdin, and
streams JSONL to the engine while retaining only the result data needed for the report. The
prompt is written while the stream is already being drained, because a task-sized prompt
and a session-sized reply each outgrow a pipe buffer and writing one before reading the
other would hang the step. Reading stops at the terminal result message rather than at
end-of-stream, because a provider's own children can hold the pipe open after it has
answered; a provider that then declines to exit is stopped and the fact recorded under
`detail`, never at the cost of the answer it already gave. It adds model and budget flags only when supplied. Plan tool
rules become repeated `--disallowedTools` flags only in that provider module. Cairn handles
no credentials and creates no process groups.

The whole prompt reaches the provider on stdin, but reaches Cairn on its own argv, so a
step's task text is visible to anything that can read the process table. The task is what
travels there: the state-check preamble every agent step is templated from is composed at
invocation, above the provider dictionary, so it stays out of every step's argv and a
provider added later inherits it without knowing it exists.

```text
python3 -m cairn occasion new
python3 -m cairn marker absent --step <id> --scope <scope> [--reads <path>]…
python3 -m cairn marker write  --step <id> --scope <scope> [--reads <path>]…
python3 -m cairn verify gate   --step <id> --position <chain|branch> [--verify-exit <status>]
python3 -m cairn plan propose  <graph> [--json]
python3 -m cairn plan answer   <graph> --step <id> (--command <text> | --decline --reason <text>)
                               [--out <path>]
python3 -m cairn workflow author <graph> --repository <path> [--parent-branch <name>]
                               [--python-path <dir>] [--out <path>] [--schedule <cron>]
python3 -m cairn workflow check  <workflow.yaml>
python3 -m cairn record build   --run <id> [--repository <path>] [--engine-records <path>]
python3 -m cairn record facts   --run <id> [--repository <path>] [--engine-records <path>]
python3 -m cairn report         --run <id> [--repository <path>]
                                [--format terminal|markdown|html] [--out <path>]
```

`marker absent` exits 0 when the step's work still has to happen, including on every error
it meets, and exits nonzero only when it has positively established a fresh marker.
`verify gate` is its inverse: it exits 0 only when it has positively established that the
step's end state was asserted and the step did not veto itself, and every fault closes it.
Both are preconditions rather than steps, so each writes a report only on the path where no
step will run to write one. Their opposite fail directions are argued in
[step-protocol.md](step-protocol.md) and [verify-gate.md](verify-gate.md).

`marker write` is a step and leaves a report like any other. It takes the marker's summary
from the verified step's own report rather than an argument, because only the step that did
the work can say what it did. Every scope but `once` and `inputs` keys on the run's occasion,
which the run mints at its first act and records under its own identity; the declared
`CAIRN_OCCASION` parameter is the override a recovery uses, not the source
([triggers.md](triggers.md)).

`plan propose` and `plan answer` are the authoring conversation
([verify-gate.md](verify-gate.md)). Like the rest of `cairn plan …` they run at derivation
time, against a graph on disk, and leave no step report. `propose` writes nothing at all,
and exits nonzero while any step is still unanswered, so a derivation can tell an unfinished
conversation from a finished one. `answer` writes the answered graph to `--out` atomically,
or to stdout when none is given; the offer it judges the answer against is the one the
graph's own `missing_verify` question carries, so an accept, an edit and a command written
unaided are derived rather than declared, and no invocation can drop or misquote the offer.

`lock acquire` is the run's first act and does eight things before its first spend: assert
the engine's DAG-level retry is off, **judge every parameter a caller varied**
([triggers.md](triggers.md)), halt on a directory that is no repository Cairn can own, halt
on an unresolved merge, halt on a repository that already has uncommitted work in it,
**record the occasion this run keys on**, clear the git lock files a killed step left, and
take the repository's run lock or refuse naming the holder. `lock release` is owner-checked and is a no-op when this run holds nothing, so it
can run as the workflow's exit handler — and it also writes the run's own record there,
which is the only place a run nobody watched can leave one.

**`lock release` exits nonzero for exactly one reason: it could not give the lock back.**
Measured against Dagu 2.11.0, a lifecycle handler exiting nonzero records the whole run as
`failed` and makes `dagu start` exit 1 even when every step succeeded, and that node is
load-bearing infrastructure in the run record — so anything else it learns rides in its
report rather than in its status.
`worktree setup` converges every worktree state it can and halts on the rest, `worktree
prune` removes a wave's worktrees and its merged branches only — both taking `--plan` and
`--step` and deriving the worktree path from the repository they stand in, so no body names
one target — and `commit` distinguishes
nothing-to-commit from a staging failure by reading the index. Each holds the git write
mutex inside itself.

`wave join` records which of a wave's branches carry work to land, before any slot moves a
tip and makes an excluded branch indistinguishable from a landed one. `merge land` chooses
one of a wave's branches, lands it, and proves what it landed; `merge
verify` proves the same thing again in a process of its own, and takes no mutex because it
only reads ([merge-step.md](merge-step.md)). `merge land` is the only subcommand that lets a
git write happen outside the mutex: the mutex covers its own `git merge` and is released
before a resolving session, because the mutex's wait is shorter than a session and holding
it across one would turn every contender into a failure rather than a wait.

`--help` prints usage without resolving runtime identity: a person asking what the
subcommands are is not a step.

`cairn supervise reconcile` gives a killed run's record a terminal status; `cairn supervise
base-config` asserts or writes the engine's disabled DAG retry. Both are described in
[supervision.md](supervision.md).

`cairn schedule install|status|start|remove` owns the recurring trigger and the daemon it
costs, and `start` refuses on a machine whose retry or catchup policy would re-execute paid
work, naming every failed run it found ([triggers.md](triggers.md)).

`cairn workflow author` is the only thing that writes an engine definition, and `cairn
workflow check` reads one and writes nothing. Both run at authoring time and are described in
[workflow.md](workflow.md).

`cairn run offer` prices one execution of a definition and mints the one token `cairn run
start` accepts; `start` spends it exactly once and hands the engine the run. The offer lives
at `<git-common-dir>/cairn/offers/<offer-id>.json` beside the runs it authorises, and its
`.spent` sibling is claimed by an exclusive link, so a second acceptance of one offer is a
refusal rather than a second run. Every refusal — an id naming no offer, a damaged one, one
already spent, a definition that moved since it was priced, a reply with no words in it at
all, an engine that is not the pin — happens **before** the token is consumed, so a refused
start leaves the acceptance standing. Both verbs exit `1` on a refusal; `start` exits `0`
once the engine has been handed the run, because whether that run worked is the record's
answer and not this command's ([run-model.md](run-model.md)).

**`start` does not read `--reply` for meaning, and no refusal above is about what it said.**
The value is whatever the caller passed, so a caller that heard "no" and passed "yes, run it"
has minted an authorisation nobody gave, and nothing downstream can tell. What the reply is
for is the ledger: it is written into the `.spent` sibling, so what authorised a run is a
fact about the repository afterwards. The rule that binds the judgement is `SKILL.md`'s.

`cairn explain workflow|word|exclusion` answers what a definition would do, what one of
Cairn's frozen words means, and why a step contributed no verified work. It starts nothing,
takes no lock and writes nothing, and it exits on its own health rather than on any run's
verdict: it is answering a question, not reporting an outcome.

`cairn record` reads a run and `cairn report` renders one. Both exit with the **run's**
verdict rather than their own health, on the codes [run-model.md](run-model.md) freezes, so a
run with exclusions exits 3 whatever either of them printed. `cairn report --format` chooses
between the terminal, markdown and HTML renderings, which are [report.md](report.md)'s.

What doc 05 does not build, and who owns it, is the ownership table in
[step-kinds.md](step-kinds.md).
