# Supervision

Everything that keeps a run's writes, its bounds and its recovery under control.

Process supervision itself is the engine's, in full. It makes every step a process-group
leader and pairs it with a watcher that kills the group on EOF; `kill -9` on the
orchestrator, on a step, and on the whole tree each leave zero surviving processes,
grandchildren included. Cairn builds no process groups inside a run and no kill path — the
one it does build is the detached engine's own session, so that a closing terminal and a
caller's process-tree kill cannot reach a run that was already under way
([triggers.md](triggers.md)). What it owns is
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

## A step writes only through ownership it can prove

Every step that opens a session or writes — `agent`, `exec`, `wait`, `commit`, `worktree`,
`merge`, `wave` — proves the repository is still its own before it starts, and halts if it
cannot. A run whose lock was reclaimed while it queued would otherwise discover it at its
next commit, an hour of agent time later, with a second run already writing to the same
repository.

**Ownership is positive, so inspection reporting no readable holder is not permission.** An
absent lock is the loss of the only thing that said this run may write here. Four things are
required, and each is one way a run can be writing to a repository that is no longer its
own:

- a present, readable run-lock record;
- a `run_id` that is this run's;
- the object the acquisition pinned, where this run's own `lock_acquire` report still says
  what that was — a lock replaced in place names the same run and proves nothing;
- a repository the record agrees is the one this step is standing in.

Deleting run A's lock, corrupting it, or replacing it therefore all stop A's next guarded
operation, whether or not a run B has since taken the repository.

That is the inverse of the acquisition's reading of the same three states, and the inverse
is the design. Acquisition is where an unreadable lock may be taken and a repository
recovered, because a repository nobody can run against is a state I4 forbids. A step already
running has no such claim to make: it either still holds what it took, or it stops.

A `wait` is checked before either of its forms **and before every attempt of an `until`
predicate**. The predicate is arbitrary shell, relaunched for as long as the plan's bound
allows while the run lock is held the whole time, so a wait that lost the repository stops
before launching the next predicate and leaves a report naming the lock it lost.

Otherwise it is a check at the head of a step and deliberately not a heartbeat. A run
renewing its lease as it worked would make the reclaim window meaningless as a bound on how
long a crashed run holds a repository, which is the only job that window has.

## What a run's first step does

`cairn lock acquire` is the run's first act, before its first step:

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

A run also refuses to start against a repository that already has uncommitted work in it —
before the run starts, and again here as the backstop for a tree that dirtied itself in
between ([../capabilities/running.md](../capabilities/running.md)). A chain step commits in
the repository itself, and its commit stages only what its own session dirtied: it
snapshots what is dirty before the session — every path **and the content of each** — and
stages the paths dirty afterwards that were not dirty before, plus its marker by path. A
path dirty both before and after is left alone and named in the step's record rather than
swept into a commit the plan claims as a step's output, which is why a person already
working in the same checkout when a step starts keeps their edits. The snapshot is taken
before the session, so this scopes what was already dirty and not what someone first touches
while the step runs: that is indistinguishable from the step's own work and still lands. The
commit names its paths, so nothing another session staged mid-step rides along either. A
tree git will not answer about is a refusal rather than a commit of the marker alone.

**A marker is published only over state the commit carries, and path membership cannot
establish that.** A path dirty before the step and changed by the step is classified as
somebody else's, so the commit would hold the marker and not the work the assertion passed
over — and discarding the residual edit would leave completion standing over work absent
from `HEAD`, which the next run skips rather than redoes. So every excluded path is proved
byte-identical to the content the baseline recorded. A step that altered one commits nothing
at all: not the marker, not its own partial output, and the refusal names the overlap.
Reverting such a path counts as altering it, and content that could not be read either time
proves nothing and counts as altered too. The marker is the exception and is not an
exclusion: the step takes its own marker by path whoever had it dirty.

The marker the mark node wrote is **withdrawn from the working tree on every refusal** the
commit reaches, because the gate that decides whether the step runs again reads the tree
rather than `HEAD`. What `HEAD` already holds is restored instead of deleted: an earlier
run's committed marker is not this commit's to withdraw.

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
mutex by design, so a collision is expected traffic; failing on it would end a session for
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
   wall-clock by some margin: the consequence of never taking a lock from a run that is still
   writing. A run killed at the _step_ level waits for none of it — the orchestrator survives
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

A reader that may not inspect processes at all — a sandboxed harness shell, where `ps` is
refused — cannot decide liveness, and says so: the record is left alone as owner unknown
rather than repaired, because a terminal status written into a run that may still be going
is the more damaging direction. Run the reconcile from a shell that can look.

When the owner is gone, a terminal snapshot is **appended**, carrying a finish time and an
error naming the reconciliation, with every still-running node marked failed too — so the
report never describes a dead run's steps as running.

## Refusing the engine's own retry scanner

A running `dagu scheduler` reconciles zombies for free, which is tempting, and it is the
wrong trade. The same process re-executes every failed run recorded on the machine in the
previous 24 hours, including runs from directories it does not watch. For Cairn a failed run
is an agent session that mutated a repository.

There is a second hazard in the same file and it has the same shape. The engine ships
`catchup_window: "6h"`, and a scheduler starting after downtime executes every cron slot
missed inside that window — up to a thousand of them, each an agent session for Cairn.
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

## The hang guard on every emitted step

I7 forbids an unbounded step, and the engine supplies neither bound by default: its own step
timeout is none, and a step retries not at all while the _DAG_ around it retries three times.
Both are written on every emitted step, and a test fails if any step is emitted without them.

**One constant, `HANG_GUARD`, bounds every plan step.** It is Cairn's own: a plan cannot set
it, and no report states it as a limit on a step. Its only job is to kill a session or a
command that has stopped making progress, so it sits above any step that is still going. A
session the guard stops is reported as stopped by the hang guard.

| Kind                     | Timeout                                                    | Retries |
| ------------------------ | ---------------------------------------------------------- | ------- |
| `agent.*`                | `HANG_GUARD` + `QUOTA_WAIT` (6h) + 180s                    | 0       |
| `command`                | `HANG_GUARD`                                               | 0       |
| `command` (`wait_until`) | `HANG_GUARD` + 15s                                         | 0       |
| verify                   | the step's `verify_timeout` (600s unless the plan sets it) | 0       |
| Cairn's own subcommands  | 600s                                                       | 0       |

The wrapper enforces the guard on an agent session: the session is stopped there, resumed
once under the 180-second grace to give the account it owes, and its report is written
before the engine's kill — which lands the grace later and erases nothing a report could
have said. The grace is for the report, never the work. `QUOTA_WAIT` is the longest an agent
step may hold at the subscription's allowance (below); held time is never charged to the
guard, so the engine's bound is the work, the hold and the grace, in that order. A `wait`
keeps its own `--timeout`,
because how long to wait for a condition is what that step means; the emitter sets it to the
guard.

Two further bounds sit inside a support step's 600 seconds: a writer waits **300 seconds**
for the git write mutex and then reports `git_mutex_timeout` rather than being killed by the
engine with nothing recorded, and one git invocation is given **240 seconds** of its own.
The three are stated together in `cairn/plan/schema.py` because their sum is the whole of
the relation — separately they would drift until the report no longer fit.

"Never retry" is spelled `{limit: 0, interval_sec: 1}`, because `interval_sec` is required
whenever a retry policy is present.

**Nothing is retried**, and a plan that asks for retries gets exactly what it asked for.
A step that failed because the provider blinked and one that failed because the task is
wrong are indistinguishable from outside, and a second session would run against a
repository the first one already changed. So a failure stops the step, once, loudly.

A subscription limit is not a failure, and it is not retried either: the engine's retry
policy is a static number in a file and cannot read the moment a limit reopens. The step
holds instead, inside its own body, as the next section describes.

## Working within the subscription

A Claude subscription meters work in a 5-hour window and a weekly one. A queue of agent steps
is bounded by that allowance rather than by what the plan wants to run, so an agent step
**holds** where it would otherwise run into a closed window, and carries on when the window
reopens ([principle 4](../PRINCIPLES.md)). A limit is a pause in the run, never the end of it.

### One shared reading

Every step reads one **headroom reading**, kept at `<runs root>/.headroom/reading.json` and
replaced whole under a lock, so concurrent steps share one fact and none measures on its own
account. Each window carries how full it is (`used`, a 0–1 fraction, where a measurement
gave one), its `status` (`allowed`, `allowed_warning`, `rejected`), when it `resets_at`, the
`source` that measured it, and when. A window measured more than **10 minutes** ago is
unknown rather than trusted, and one whose reset has passed is void — except a rejection,
which is measured again rather than assumed over.

Three feeders write it, cheapest first:

1. **The stream.** Every session's `rate_limit_event` updates its window as it arrives, so a
   limit one step meets holds every other step at once. It is free, and the only feeder that
   sees `rejected`. The session's `system` message also records how the account is funded.
2. **The usage endpoint**, `GET https://api.anthropic.com/api/oauth/usage`, **off unless**
   `CAIRN_HEADROOM_USAGE_ENDPOINT=1` is set in the environment a workflow is generated from —
   the generator carries it into the workflow's `env:` block, because the engine hands a step
   a curated environment rather than the caller's. It reads Claude Code's own credential
   (the macOS keychain item `Claude Code-credentials`, else `~/.claude/.credentials.json`),
   never refreshes it, and treats an expired one as unknown. It is asked at most every 180
   seconds and backed off 3 → 6 → 12 → 15 minutes after a 429. It is the only feeder that
   gives both windows' percentages on demand, and it is undocumented, which is why a person
   turns it on.
3. **A probe**, when nothing fresh is known and the endpoint did not answer: one turn of
   `claude -p --model haiku` with no settings, no tools, no MCP and no session persistence,
   in a scratch directory, read for its `rate_limit_event` and discarded. Concurrent steps
   that find the reading stale cause one probe between them: the first takes a refresh lock
   and the rest re-read what it wrote.

### Admission

Before a step opens its session, the reading decides:

| Reading                                                | Decision                                         |
| ------------------------------------------------------ | ------------------------------------------------ |
| the session is funded by an API key                    | `inert` — no subscription window applies         |
| a window `rejected`                                    | `held` until that window's reset                 |
| a window's `used` at or past its hold threshold (0.95) | `held` until that window's reset                 |
| a window `allowed_warning` below the threshold         | `warned` — admitted, and the warning is recorded |
| nothing measured recently enough, by any feeder        | `unknown` — admitted, and why is recorded        |
| otherwise                                              | `admitted`                                       |

A per-model weekly window (`seven_day_opus`, `seven_day_sonnet`) holds only a step whose model
is of that family. Where several windows are closed, the hold lasts until the last of them
reopens.

**Unknown admits.** A guard that blocked on its own blindness would stall a queue for a fault
in the instrument, and the backstop below makes a wrong admission a short wait rather than a
lost step. This is the inverse of the verify gate on purpose: nothing durable depends on this
check having run.

### The hold, and measuring again

A hold sleeps until the reported reset plus one minute, then **measures again** and believes
only a reading taken after the reset. Cairn never computes a reset itself: a window that
reopens late, or a weekly window whose reset moves, is held on for 5, 10, then 20 minutes at a
time until a measurement says it is open. While it holds, the step announces the hold at
`runs/<run-id>/holds/<node>.json`, so the run reads as waiting for a window rather than
stalled, and takes the announcement back when it stops.

A step may hold for at most `QUOTA_WAIT`, **6 hours** — enough to wait out a whole 5-hour
window. A hold that would end later, typically a weekly window days away, does not sleep a
worker for days: the step ends **`quota_held`**, exit **75**, and its summary names the moment
and the window — _held until Thu 08 Oct 04:00 BST — the weekly allowance at 97%_. The run's
next action is then `await_allowance`, whose recovery command is the one to run after that
moment; the committed markers mean it skips every step that already landed.

### The backstop: a limit met mid-session

Admission lowers the odds of meeting a limit; it cannot remove them, because one session
spends an unknown share of a window. A session that ends on `blocking_limit`, or on an `api_error` that is HTTP 429 (the monthly spend limit), is held, then
**resumed by id** (`--resume`) once its window reopens, and asked to continue from where the
tree now stands. The window it met is written into the shared reading at once, so every other
step holds on it too. A resumed session may meet the limit again and is held and resumed
again, within the same 6-hour hold budget. A resume that cannot continue the session —
refused, or failing before it reports — ends the step `quota_held` with the session's id.
A merge slot's resolving session is admitted, held and resumed the same way, within the
same 6-hour hold budget, and its outcome feeds the same reading.

Held time is never charged as work: every session the step opens shares one hang guard,
counted as time inside a session, and each resume is given what is left of it.

### What the step's report carries

`detail.headroom` records the admission decision and its reason, the reading it rested on
with each window's age and source, every hold (window, from, until, why, and whether it came
before the session or after a limit), and every resume. `detail.resets_at` is the ISO-8601
UTC moment of the furthest `resetsAt` the session's stream reported. The run record and every
report state the same facts ([run-model.md](run-model.md)).
