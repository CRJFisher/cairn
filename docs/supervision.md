# Supervision

Everything that keeps a run's writes, its bounds and its recovery under control.

Process supervision itself is the engine's, in full. It makes every step a process-group
leader and pairs it with a watcher that kills the group on EOF; `kill -9` on the
orchestrator, on a step, and on the whole tree each leave zero surviving processes,
grandchildren included. Cairn builds no process groups and no kill path. What it owns is
what the engine leaves behind.

## Two locks, two lifetimes

The difference in lifetime is the whole design.

**The git write mutex** serialises the git writes of one moment. It is an advisory file
lock on `<git-common-dir>/cairn/git-write.lock`, so the kernel drops it the instant its
holder dies — which is what a mutex wants. It is taken _inside_ the subcommand that writes,
never around it, so serialisation holds however the step was invoked; the engine's own
`flock` guards only its own worktree add and remove, and nothing an agent does.

Agent subprocesses are deliberately outside it. An agent writes its own worktree's index
and its own branch's ref, neither of which another step touches, and agents are where the
wall-clock is.

**The run lock** outlives every process that touches it. One step takes it, a different step
gives it back, and a crash between them leaves it held with nobody running. So it cannot be
a file lock. It is a git ref, `refs/cairn/run-lock`, pointing at a blob holding the holder's
record, and every transition is a compare-and-swap through `git update-ref --stdin`:
`create` fails if the ref exists, and `update`/`delete` fail unless the ref still holds the
value the caller read. Two racing acquisitions resolve to one winner because exactly one
swap can name the object both of them read.

The lock is keyed on the repository's shared admin directory. Two worktrees of one
repository are one contender, two plans against one repository contend, and two
repositories never do — which is the case the engine's own per-DAG-name serialisation would
let through.

## Reclaim asks whether the run is alive, and falls back to the clock

A lock may be taken from its holder on either of two proofs, and the window is what answers
when neither is available.

**The holding run is provably gone.** The lock records where the engine keeps that run's
status file, and that record's `pid` is the _orchestrator_ — the one process spanning the
whole run. If it is gone, the repository is free at once, with no window to wait out.

**The window has passed**, meaning `acquired_at + run_timeout × 1.25`. The window is never
configured on its own: it is the run's own maximum duration ([topology.md](topology.md))
scaled by one factor, so a lock only comes free from a run that has outlived every bound its
plan gave it. A second, absolute grace would be a number to keep in step with the first,
which is how two numbers drift apart.

**A provably live run keeps its repository** however far past its estimate it has run. The
estimate bounds what a plan may declare, not what a running plan is permitted to finish, and
taking the repository from a run still writing to it is the worse error of the two.

A refusal names the holder and states the reason the decision actually turned on — still
running, or free in so many seconds — so it never sends someone back at a named minute to
the identical refusal. Recovery needs no operator procedure: only time the plan itself
declared, or the death of the run that declared it.

The liveness question is asked of the run's own record and never of the lock's. The lock is
taken by one short-lived step and returned by another, so the process that _recorded_ it has
already exited by the time the run's second step starts; a run that reclaimed on that death
would take the lock off every live run on the machine. The recorded step process is kept for
the refusal to name, and for nothing else.

A lock whose payload cannot be read is still a lock — the ref is there and every
compare-and-swap must name it — but it names no holder, so it is reclaimable immediately,
which is the one exception to the window. The alternative is a repository nobody can run
against and no way back that is not an operator procedure. "Cannot be read" means a payload
git produced that does not carry every field a refusal would name; a payload git could not
produce at all is a failure to read the lock, not grounds for taking it.

A run may always retake its own lock, and doing so **renews** the lease rather than
inheriting it. `dagu retry` reuses the run identifier, so refusing there would make the
documented recovery impossible for the very run it recovers — but returning the old record
unchanged would leave an already-expired window expired, and a third run would take the
repository out from under the retry.

## A step halts if the repository stopped being its own

Every step that spends or writes — `agent`, `exec`, `commit`, `worktree` — reads the lock
before it starts and halts if the repository is held by a different run. A run whose lock
was reclaimed while it queued would otherwise discover it at its next commit, an hour of
paid agent time later, with a second run already writing to the same repository.

Only a lock held by somebody else counts. An absent lock does not: these subcommands are the
step vocabulary and stand on their own, and a working directory that is no repository at all
has nothing to lose. Refusal follows proof, never silence — the same discipline the reclaim
decision uses.

It is a check at the head of a step and deliberately not a heartbeat. A run renewing its
lease as it worked would make the reclaim window meaningless as a bound on how long a
crashed run holds a repository, which is the only job that window has.

## What a run's first step does

`cairn lock acquire` is the run's first act, before its first spend:

1. Assert DAG-level retry is disabled in the engine's base configuration.
2. Refuse a bare repository, which has no working tree, and a submodule, whose admin
   directory belongs to its superproject and would lock that too.
3. Halt on an unresolved merge, rebase, cherry-pick or conflicted index.
4. Halt on a repository that already has uncommitted work in it.
5. Take the git write mutex, which clears the git lock files a killed step left.
6. Acquire the run lock, or refuse naming the holder.

`cairn lock release` takes the mutex and gives the lock back. It is stated as a
postcondition — afterwards this run does not hold this repository — so a lock that was never
taken is a no-op rather than a failure. That is what lets it run as the workflow's **exit
handler**, which is the only place it can run on the failure path: a step whose dependency
failed is never dispatched, so a release wired into the graph would run only when the run
succeeded and a failed run would hold its repository for the whole reclaim window.

The owner check is what stays hard: a run that halted _because_ the repository was busy
reaches this same code, and must never release the lock of the run it lost to.

A run also refuses to start against a repository that already has uncommitted work in it. A
chain step commits in the repository itself and stages everything there, so anything the
user left behind would be swept into a commit the plan claims as a step's output.

## How git itself is invoked

One module runs every git command, so the conditions it runs under are settled once rather
than at each call site.

The environment is stripped of every variable that can point git somewhere other than the
directory it was given — `GIT_DIR`, `GIT_WORK_TREE`, `GIT_COMMON_DIR`, `GIT_INDEX_FILE`,
`GIT_OBJECT_DIRECTORY`, `GIT_ALTERNATE_OBJECT_DIRECTORIES`, `GIT_NAMESPACE`,
`GIT_CEILING_DIRECTORIES` and `GIT_DISCOVERY_ACROSS_FILESYSTEM`. An inherited `GIT_DIR`
overrides discovery outright, so one exported by a shell, a hook or a parent process would
send a plan's every commit to a repository nobody named.

Ref writes wait three seconds for a contended lock rather than git's own 100ms on a loose
ref and one second on `packed-refs`. An agent commits in its own worktree outside the write
mutex by design, so a collision is expected traffic; failing on it would end a paid step for
a condition that clears itself.

A path that is empty or relative is refused before git sees it. An unresolved engine
reference expands to the empty string and the step still runs, so git handed one would fall
back to whatever directory the step happened to start in.

Decisions come from exit status and porcelain output, so a reworded git changes almost
nothing. There is one deliberate exception: `git update-ref` says `cannot lock ref` both for
a refused compare-and-swap and for a `.lock` file another git happens to be holding, which
are opposite answers. The verdict is still read from the ref itself; the wording only
decides whether to wait and ask again. `LC_ALL=C` is set so that reading is not
locale-dependent.

## Stale git locks

A killed step can leave `index.lock`, `HEAD.lock`, `packed-refs.lock` or a ref lock behind.
Every mutex entry clears the ones older than five minutes — a different five minutes from
the mutex wait above — and leaves younger ones alone,
because an agent's own commit runs outside the mutex by design and may still hold one. The
clearing happens under the mutex, so two writers cannot both decide a file is stale and race
to unlink it. Cairn's own mutex file lives under `cairn/` inside the admin directory and is
never a candidate.

## After a crash

Three things are true, in this order:

1. The next run is **refused**, naming the holder and saying when the lock becomes
   reclaimable. Nothing is lost; the repository is simply busy.
2. The lock comes free on its own once `acquired_at + max_duration × 1.25` has passed — the
   window the killed plan itself declared. That duration is the **sum** of every step's
   bound rather than its critical path, so a wide plan's window is longer than its likely
   wall-clock by some margin: the price of never taking a lock from a run that is still
   writing. A run killed at the _step_ level pays none of it — the orchestrator survives
   and reaches its exit handler, so the lock comes back at once.
3. The engine's own record still says `running`, and stays that way. Repair it with
   `python3 -m cairn supervise reconcile`, which defaults to the engine's own run history.
   That location is **asked of the engine** rather than derived from where its configuration
   lives: the two are different directories on at least one platform Cairn runs on, and a
   reconcile pointed at the wrong one reports a machine with no runs on it
   ([enginehome.py](../cairn/enginehome.py)).
   Until then `dagu retry` refuses the run as already running.

## Reconciling a killed run

A run killed without a scheduler stays `Running` with no finish time forever. `dagu retry`
refuses it as already running, `dagu stop` reports success while changing nothing, and
clearing the socket and process file does not help. The block lives in the status record, so
that is where the repair goes.

`cairn supervise reconcile <path>` reads the last valid line of each `status.jsonl` — the
file is append-only during a run and compacted to one line when the attempt closes, so a
cold reader always scans to the end — and decides liveness from the recorded `pid` together
with `pidStartedAt`. The status field is never the evidence: after a crash it says running
forever. A recycled identifier cannot make a dead run look alive, because the start times
would not match.

When the owner is gone, a terminal snapshot is **appended**, carrying a finish time and an
error naming the reconciliation, with every still-running node marked failed too — so the
report never describes a dead run's steps as running.

## Refusing the engine's own retry scanner

A running `dagu scheduler` reconciles zombies for free, which is tempting, and it is the
wrong trade. The same process re-executes every failed run recorded on the machine in the
previous 24 hours, including runs from directories it does not watch. For Cairn a failed run
is a paid agent session that mutated a repository.

There is a second hazard in the same file and it has the same shape. The engine ships
`catchup_window: "6h"`, and a scheduler starting after downtime executes every cron slot
missed inside that window — up to a thousand of them, each a paid agent session for Cairn.
Every file Cairn emits states the empty window that turns replay off, so this reaches only
the DAGs Cairn did not write, which is exactly what the scanner reaches.

Both are asserted **at the moment a scheduler is started**, by
`python3 -m cairn schedule start`, which refuses on an armed machine and names every failed
run it would have re-executed ([triggers.md](triggers.md)). That is the only place either
hazard can fire, and a machine that was safe when a schedule was installed is not evidence
about the machine a month later.

So the disabling policy goes into the engine's machine-wide `base.yaml`, and is asserted
before any run rather than assumed. **An absent file is refused, not trusted**: the engine
writes `base.yaml` on the first invocation of any of its commands, with
`retry_policy: {limit: 3, interval_sec: 5}` active, so "not there yet" means "enabled from
the next command onward". `cairn supervise base-config --disable` writes it, editing rather
than replacing, and reads its own edit back before accepting it: a splice that produced a
duplicate key would leave a file the engine refuses to load at all, taking every unrelated
workflow on the machine with it. Anything the reader cannot account for exactly is refused
as unreadable rather than guessed at.

**Once per machine, before the first run:** `python3 -m cairn supervise base-config
--disable`. Without it every `lock acquire` refuses with `base_retry_enabled`, and every
such refusal names this command.

## Bounds on every emitted step

I7 forbids an unbounded step, and the engine supplies neither bound by default: its own step
timeout is none, and a step retries not at all while the _DAG_ around it retries three times.
Both are written on every emitted step, and a test fails if any step is emitted without them.

| Kind                     | Timeout                    | Retries |
| ------------------------ | -------------------------- | ------- |
| `agent.*`                | 3600s                      | 0       |
| `command`                | 600s                       | 0       |
| `command` (`wait_until`) | the wait's own bound + 15s | 0       |
| verify                   | 600s                       | 0       |
| Cairn's own subcommands  | 600s                       | 0       |

Two further bounds sit inside a support step's 600 seconds: a writer waits **300 seconds**
for the git write mutex and then reports `git_mutex_timeout` rather than being killed by the
engine with nothing recorded, and one git invocation is given **240 seconds** of its own.
The three are stated together in `cairn/plan/schema.py` because their sum is the whole of
the relation — separately they would drift until the report no longer fit.

"Never retry" is spelled `{limit: 0, interval_sec: 1}`, because `interval_sec` is required
whenever a retry policy is present.

**Nothing is retried**, and a plan that asks for retries gets exactly what it asked for.
A step that failed because the provider blinked and one that failed because the task is
wrong are indistinguishable from outside, and a second paid session would run against a
repository the first one already changed. So a failure stops the step, once, loudly.

A rate limit is the one distinguishable case — it arrives as a typed stream event carrying
`resetsAt`, and `cairn agent run` leaves on exit **75** rather than 1 — and it is still not
retried. The engine's retry policy is a static number in a file and cannot read `resetsAt`.
A fixed wait short enough to be worth making is far shorter than a real limit's reset, so
the retry would usually meet the same limit and pay a second session's tokens to discover
it. The moment is reported instead: `detail.resets_at` says when the plan is worth running
again, and the committed marker means the re-run skips every step that already landed.

Exit 75 survives as the distinction it always was — a report can say the run stopped
because of a limit rather than because the work was wrong — it simply no longer drives a
retry.
