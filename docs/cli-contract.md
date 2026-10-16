# Internal CLI contract

`python3 -m cairn` is an implementation surface for generated workflows, not a user
interface. It is the only entry point, and the skill invokes it: a person asks for what they
want and never learns one of these lines ([../SKILL.md](../SKILL.md)). Runtime commands — `exec`, `wait`,
`agent`, `marker write`, `lock`, `worktree`, `commit`, `merge`, `wave` — dispatch through a
dictionary and run inside a step. Thirteen commands take no part in that dispatch: `cairn plan …`
runs at derivation time
against a graph on disk, `cairn workflow …` generates and checks an engine definition
([workflow.md](workflow.md)), `cairn occasion new` mints an occasion a caller means to pin,
`cairn marker absent` ([step-protocol.md](step-protocol.md)) and `cairn verify gate`
([verify-gate.md](verify-gate.md)) run as preconditions, before their step starts,
`cairn supervise …` repairs a run that is over, `cairn record …` reads one
([run-model.md](run-model.md)), `cairn report …` renders it ([report.md](report.md)), and
`cairn schedule …` installs a recurring trigger and starts the daemon it needs
([triggers.md](triggers.md)), `cairn run start` starts a run and `cairn run resume-at` waits
out the allowance a held run stopped at and starts its recovery, `cairn explain …` answers
what a workflow would do, what a frozen word means
and why a step was excluded, `cairn view status` says whether the address a run is watched at
is being served and `cairn view start` serves it — one detached `dagu server`, never the
scheduler, never on a port something else already answers on, and never left with an
unclaimed administrator account ([triggers.md](triggers.md)), and `cairn hook stop` answers the one hook a step's own session
runs under — reading the harness's end-of-turn payload on stdin and exiting `2` to hold the
turn open while a background shell the session started is still running, or `0` to let it
end ([step-protocol.md](step-protocol.md)). It is routed ahead of every other command
because it runs as a grandchild of a step and inherits the step's environment: one that
resolved a runtime identity would overwrite the very report the verify gate reads. It
**fails open** on any fault — the inverse of the verify gate, because it holds an agent
session and nothing in Cairn may depend on it having run. None of the thirteen takes a runtime
identity, and only the verify gate leaves a report — under the name of the step it gated, on the one path where no step
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
is the routing: generic facts stay at top level while model, session, turns,
permission decisions, scope and freshness key stay under `detail`; exit zero means
done/no-op except when `needs_user_decision` deliberately blocks routing with
`user_decision_required`; a terminal failure is nonzero, and `cause` explains it.

The cause vocabulary is closed. Doc 05 issues `command_failed`, `wait_timeout`,
`timed_out`, `cancelled`, `provider_failed`, `provider_protocol`, `provider_unavailable`,
`reported_failure`, `user_decision_required`, `quota_held`, `turn_limit`,
`process_launch_failed`, `invalid_command`, `invalid_wait`, `invalid_arguments`,
`invalid_report`, `missing_runtime_identity`, and `internal_error`. A session that meets the
subscription's limit is held and resumed rather than reported, so the limit reaches a report
only as `quota_held`, where the step could not wait it out. A session that loses the model
provider is held and resumed the same way, and reaches a report only as
`provider_unreachable`, where the provider did not answer again within the time the step may
wait ([supervision.md](supervision.md)).
Doc 06 adds `invalid_marker`, `invalid_occasion`, `invalid_reads`, `invalid_scope`,
`invalid_step_id`, `marker_ignored`, and `missing_report`. Docs 07 and
09 add `git_failed`, `not_a_repository`, `git_mutex_timeout`, `merge_in_progress`,
`repository_busy`, `repository_dirty`, `lock_not_held`, `base_retry_enabled`,
`base_config_unreadable`, `worktree_dirty`, `worktree_foreign`, and `worktree_unusable`.
Doc 27 adds `branch_unowned`, raised where a branch cannot be proved to belong to this
plan's step and parent, and `excluded_path_changed`, raised where a step altered a path its
commit is not allowed to stage. Doc 53 adds `work_left_uncommitted`, raised where a commit in
a step's own worktree would leave a dirty path beside the step's marker.
Doc 10 adds `merge_conflict`, `merge_not_landed`, `conflict_markers_committed`,
`merge_indeterminate`, `merge_unowned_conflict`, `merge_wrong_branch`,
`merge_environment_redirected`, `merge_foreign_branch`, raised where a merge candidate is not
a `step/<plan>/<step>` branch, and `merge_unjudged_work`, raised where a candidate carries
work no readable gate report of the run judged. Doc 13 adds `engine_paths_unreadable`, which
`lock acquire` raises when the engine cannot say where it keeps its run history.
`base_catchup_enabled` is **not** in that vocabulary: it is raised only by `cairn schedule`,
which leaves no report, so like the four below it is an exit diagnosis rather than a
cause.
`run_record_unreadable`, `engine_status_unmapped`, `run_still_running` and `invalid_run_id`
are not in that vocabulary either: the first three are raised only by `cairn supervise`,
`cairn record` and `cairn plan draft`, which leave no report, so they are exit diagnoses
rather than causes. `invalid_run_id` is raised where runtime identity itself is being
resolved, which is before there is anywhere to write.

Why a step contributed no verified work is a **second, distinct vocabulary**: it answers a
question about a branch in a run rather than about one process's exit status, and it is
frozen in [verify-gate.md](verify-gate.md). The verify gate's report carries a value from
that set as its `cause`.

Exit status carries one further distinction. A step that stopped at the subscription's
allowance, `quota_held`, leaves on **75** rather than 1, so the engine's record alone says the
run stopped on the account rather than because the work was wrong. It drives no retry; see
[supervision.md](supervision.md).

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
is the step's own, and the emitted step's `timeout_sec` is set fifteen seconds above it, so a
wait that runs out reports `wait_timeout` instead of racing the engine's own kill for the
same instant. That grace counts in the run's declared maximum too.

The Claude provider invokes plain `claude -p --output-format stream-json --verbose
--json-schema … --session-id … --permission-mode auto --settings …`, sends the prompt on stdin, and
streams JSONL to the engine while retaining only the result data needed for the report. The
prompt is written while the stream is already being drained, because a task-sized prompt
and a session-sized reply each outgrow a pipe buffer and writing one before reading the
other would hang the step. Reading stops at the terminal result message rather than at
end-of-stream, because a provider's own children can hold the pipe open after it has
answered; a provider that then declines to exit is stopped and the fact recorded under
`detail`, never at the expense of the answer it already gave. It adds the model flag only
when supplied. Tool rules become repeated `--disallowedTools` flags only in that provider
module, and **Cairn's own denials lead**: a fixed set the plan adds to and cannot remove,
because those tools' whole contract is that something will re-invoke the session and under
`-p` nothing does. The `--settings` document arms one `Stop` hook per session and is composed
per invocation, so nothing is written to any settings file on the machine. A session that
ends a turn without its structured report, or whose every report the provider refused, is
resumed once, `--resume` in place of `--session-id` ([step-protocol.md](step-protocol.md)). Cairn handles
no credentials, and the only process group it creates is the detached engine's
([supervision.md](supervision.md)).

The whole prompt reaches the provider on stdin, but reaches Cairn on its own argv, so a
step's task text is visible to anything that can read the process table. The task is what
travels there: the state-check preamble every agent step is templated from is composed at
invocation, above the provider dictionary, so it stays out of every step's argv and a
provider added later inherits it without knowing it exists.

```text
python3 -m cairn occasion new
python3 -m cairn marker absent --step <id> --scope <scope> [--reads <path>]…
python3 -m cairn marker write  --step <id> --scope <scope> [--reads <path>]…
python3 -m cairn verify needed --step <id> --command-digest <sha256>
python3 -m cairn verify gate   --step <id> --position <chain|branch> [--verify-exit <status>]
python3 -m cairn plan home     <plan-slug> --repository <path>
python3 -m cairn plan draft    --from-run <id> [--repository <path>] [--reports <runs-root>]
                               [--out <path>]
python3 -m cairn plan propose  <graph> [--json]
python3 -m cairn plan answer   <graph> --kind <kind> [--step <id>] [--dep <id>]
                               (--command <text> | --accept | --edit <text> | --decline)
                               [--reason <text>] [--out <path>]
python3 -m cairn workflow author <graph> --repository <path> --source-root <plan-dir>
                               [--parent-branch <name>] [--package-root <dir>] [--out <path>]
                               [--schedule <cron>]
python3 -m cairn workflow check  <workflow.yaml>
python3 -m cairn record build   --run <id> [--repository <path>] [--engine-records <path>]
                                [--reports <runs-root>]
python3 -m cairn record facts   --run <id> [--repository <path>] [--engine-records <path>]
                                [--reports <runs-root>]
python3 -m cairn record list    [--plan <slug>] [--repository <path>] [--reports <runs-root>]
python3 -m cairn record latest  --plan <slug> [--repository <path>] [--reports <runs-root>]
python3 -m cairn report         --run <id> [--repository <path>]
                                [--format terminal|markdown|html] [--out <path>]
```

`marker absent` exits 0 when the step's work still has to happen, including on every error
it meets, and exits nonzero only when it has positively established a fresh marker.
`verify needed` sits on the assertion node and exits 0 when the assertion has anything to
assert, including on every error it meets; it exits nonzero only when the step's work node
left no report of this run — a step an upstream halt skipped — or when the same command
has already been proven in this run against exactly this tree, in which case it records
the proof it shares and the step whose execution backed it. `verify gate` is the inverse
of both: it exits 0 only when it has positively established that the step's end state was
asserted and the step did not veto itself, and every fault closes it. All three are
preconditions rather than steps. The marker gate and the verify gate write a report only on
the path where no step will run to write one; the assertion's gate writes its decision on
every path, under the assertion node's own name, because the verify gate trusts the
engine's exit-status reference only where that decision says the assertion ran. Their fail
directions are argued in [step-protocol.md](step-protocol.md) and
[verify-gate.md](verify-gate.md).

`marker write` is a step and leaves a report like any other. It takes the marker's summary
from the verified step's own report rather than an argument, because only the step that did
the work can say what it did. Every scope but `once` and `inputs` keys on the run's occasion,
which the run mints at its first act and records under its own identity; the declared
`CAIRN_OCCASION` parameter is the override a recovery uses, not the source
([triggers.md](triggers.md)).

`plan propose` and `plan answer` are the authoring conversation
([verify-gate.md](verify-gate.md), [plan-contract.md](plan-contract.md)). Like the rest of
`cairn plan …` they run at derivation time, against a graph on disk, and leave no step report.
`propose` writes nothing at all, and lists every step nobody has been asked to assert and every
other open question, each with the invocations that record its answers. It exits 0 whenever the
listing was made and 2 when the graph could not be read; `--json` prints `complete` beside the
two lists, so a caller tells a finished conversation from an unfinished one by what the listing
holds, never by its exit status. `answer` writes the answered graph to `--out` atomically, or to
stdout when none is given. A `missing_verify` answer is `--command` or `--decline`, judged
against the proposal the graph's own question carries, so an accept, an edit and a command written
unaided are derived rather than declared. Every other kind is `--accept`, `--edit` or
`--decline` as its kind admits; an accept adopts the question's own proposal, never an
argument, so no invocation can drop or misquote the proposal. `plan home` prints the plan's own
graph path, `<git-common-dir>/cairn/graphs/<plan>.json`, and refuses, naming the file, while
the shared `graph.json` of the old one-graph-per-repository layout remains; nothing migrates it.

`plan draft` is the one command under `plan` that acts on a run rather than a graph: it reads
one settled run's record and writes that run's follow-up work as a plan document beside the
graphs, `<git-common-dir>/cairn/plans/<plan>-after-<run>.md`, printing the path it drafted or
replaced ([plan-contract.md](plan-contract.md)). It exits `0` having written nothing where the
run left no follow-up work, saying that the sequence is closed, and it refuses on the code the
named run's own state carries rather than on its own health: `5` for a run still running,
whose steps have not all reported, and `6` for a run whose record is not there or cannot be
read.

`lock acquire` is the run's first act and does eight things before its first agent session: assert
the engine's DAG-level retry is off, **judge every parameter a caller varied**
([triggers.md](triggers.md)), halt on a directory that is no repository Cairn can own, halt
on an unresolved merge, halt on a repository that already has uncommitted work in it,
**record the occasion this run keys on**, clear the git lock files a killed step left, and
take the repository's run lock or refuse naming the holder. `lock release` is owner-checked and is a no-op when this run holds nothing, so it
can run as the workflow's exit handler — and it also writes the run's own record there,
which is the only place a run nobody watched can leave one.

**`lock release` exits nonzero for exactly one reason: it could not give the lock back.**
Measured against Dagu 2.18.2, a lifecycle handler exiting nonzero records the whole run as
`failed` and makes `dagu start` exit 1 even when every step succeeded, and that node is
load-bearing infrastructure in the run record — so anything else it learns rides in its
report rather than in its status.
`worktree setup` converges every worktree state it can and halts on the rest, `worktree
prune` removes a wave's worktrees and its merged branches only — both taking `--plan` and
`--step` and deriving the worktree path _and_ the branch `step/<plan>/<step>` from them
against the repository they stand in, so no body names one target and no body spells the
branch twice ([topology.md](topology.md)) — and `commit`, taking `--message` and `--step`,
stages what the step's own session dirtied and the step's marker by path, never the working
tree at large, and distinguishes nothing-to-commit from a staging failure by reading the
index. Every work step records in its report's `detail`, as `dirty_before`, what was already
dirty when its session started: a mapping of path to the content it then held. Outside a
step's own worktree, the commit stages the paths dirty now and not then, leaves the rest alone, and names them as
`left_uncommitted` and in its follow-up work. An excluded path whose content moved is an
`excluded_path_changed` refusal that commits nothing — the step cannot claim the path as its
own work nor prove it left it alone, and a marker over it would stand for work absent from
`HEAD`. A work report that carries no snapshot at all — a marker no-op's — stages the marker
alone; one whose snapshot is absent because git would not answer is a `git_failed` refusal,
and one that will not parse or whose snapshot is any shape but path to digest is an
`invalid_report` refusal, because a commit that cannot be scoped is the loss this scoping
exists to prevent. A present snapshot is never read as a missing one. In a step's own
worktree — the path derived for its plan and step, on its `step/<plan>/<step>` branch, which
the owner record names — the commit reads no snapshot: the branch tip is its baseline, every
dirty path is staged, and a path still dirty after staging is a `work_left_uncommitted`
refusal that commits nothing and withdraws the marker ([topology.md](topology.md)).
`worktree setup` reports, as `carried`, the uncommitted paths a worktree it reused or
fast-forwarded already held — an empty list for a clean or newly created one — with one
follow-up line when there are any, and as `carried_from_run` the run that left them where
that run's uncommitted marker says so. Every
refusal withdraws the step's fresh marker from the working tree, restoring whatever `HEAD`
already held. Each holds the git write mutex inside itself.

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
base-config` asserts or writes the engine's disabled DAG retry and catch-up; `cairn supervise
headroom` records the owner's answer to whether the subscription's usage endpoint may be
read, and says what the instruments can see without it. All three are described in
[supervision.md](supervision.md).

`cairn schedule install|status|start|remove` owns the recurring trigger and the daemon it
needs. `install` and `start` print what a scheduler does on this machine and ask nothing, and
`start` refuses on a machine whose retry or catchup policy would re-execute failed runs,
naming every one it found ([triggers.md](triggers.md)).

`cairn workflow author` is the only thing that writes an engine definition, and `cairn
workflow check` reads one and writes nothing. Both run at authoring time and are described in
[workflow.md](workflow.md).

`cairn run start` hands the engine one execution of a definition: a request to run is the
go-ahead, and nothing is quoted or asked first. It prints the durable identity as
`launching`, with the engine's view address beside it, launches the engine detached, and records the run id and the engine invocation in
the run's directory, so a start whose process died still left a name a recovery can quote.
Every refusal — a definition that no longer matches its plan, an engine that is not the
pin, a shell the engine cannot start a run from, and an engine that cannot say where it
keeps its run history — happens before anything starts. `start` exits `1` on a refusal and
`0` once the engine has been handed the run **and** where the engine has neither taken it on
nor exited within its bound, because in that case the run may still begin — whether a run
worked is the record's answer and not this command's ([run-model.md](run-model.md)).

Before the launch it states a `forecast`: what this queue's agent steps have cost each
subscription window in this repository's past runs, how many records the figure rests on, and
what is left of that window now. Where no record can speak for the queue it says there is
nothing to estimate from, and says nothing else. Where the usage endpoint is off it adds a
`blind` line naming the windows the stream puts a figure on only from 90% up. Both are
sentences a person weighs; neither refuses anything, and a forecast that could not be read
costs the sentence and never the run ([supervision.md](supervision.md)).

**`started` is printed only once the engine has registered the run**, and the line before the
launch says `launching`. The identity, the branch and both addresses are known before the
engine is invoked and are printed then, so a caller killed mid-launch has still been told the
name of the run — but an affirmative start claim is a line automation and an incident log keep
forever, and the engine may still decline the run. Where it declines, the output carries no
claim that the run started; where it has neither registered nor exited, the line is `waiting`.

`cairn run resume-at --run <id>` is the waiting half of a start, and the only other verb
under `run`. It is the body a held run's release detaches: it sleeps until the moment that
run recorded its window as reopening, measures the allowance, and then runs the recovery
command that run's own record carries — composing none of its own. It is the one command in
Cairn expected to run for hours. It exits `1` where it refuses, `0` otherwise, and it writes
what it is doing to `runs/<run-id>/resume.json` throughout, which is what the record reads.
`--cancel` stops a waiter and removes that file instead of waiting
([supervision.md](supervision.md)).

`cairn explain repository|workflow|word|exclusion` answers which repository a request is
about, what a definition would do, what one of Cairn's frozen words means, and why a step
contributed no verified work. `explain workflow` states the same `forecast` and `blind` lines
`run start` does, so the figure can be had without starting a run. It starts no run, takes no
lock and writes nothing, and it
exits on its own health rather than on any run's verdict: it is answering a question, not
reporting an outcome.

Every command that acts on a repository takes `--repository` and `--session`, and answers with
the one it resolved and which candidate said so. `cairn run start` and `cairn explain
workflow|exclusion|repository` also take `--subject`, repeatable, for the documents the
request is about; `cairn report` and `cairn schedule install` have no such subject and take
only the other two. `--session` is passed in rather than read from the process, because these
commands are run from the skill's own directory and so the process's own directory names
Cairn's checkout whatever repository the person is in.

`cairn record` reads a run and `cairn report` renders one. Both exit with the **run's**
verdict rather than their own health, on the codes [run-model.md](run-model.md) freezes, so a
run with exclusions exits 3 whatever either of them printed. `cairn report --format` chooses
between the terminal, markdown and HTML renderings, which are [report.md](report.md)'s.

`record list` and `record latest` are the two verbs that take a plan rather than a run id,
because "how did the last run of plan A go" is asked with the plan's name and nothing else.
`list` builds nothing: it reads the records already on disk and names each run's plan, id,
start moment, trigger and verdict, newest first. `latest` resolves one run — the newest
readable run of that plan, ties broken by run id — and exits on **that run's** verdict, so
the question can be asked by a script; it exits on the code no verdict owns where the plan
has no readable run. A run whose record cannot be read appears in the listing with the fault
and the rebuild command, and is never what `latest` returns ([triggers.md](triggers.md)).

What doc 05 does not build, and who owns it, is the ownership table in
[step-kinds.md](step-kinds.md).
