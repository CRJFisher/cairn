# Running a plan

| Contract       | Value                                                                                              |
| -------------- | -------------------------------------------------------------------------------------------------- |
| Capability     | `run`                                                                                              |
| Entered when   | the dispatch table selected **run**                                                                |
| Preconditions  | a generated definition exists for the plan; the repository came from the request                   |
| Bound on entry | `capability` · `repository` · `workflow` · `plan_document` · `model` · `occasion_reading`          |
| Owns           | the occasion reading, the engine trigger, and the address the run is watched at                    |
| Defers to      | [../SKILL.md](../SKILL.md) · [../docs/triggers.md](../docs/triggers.md) · [reading.md](reading.md) |
| Triggers       | a run                                                                                              |

## The procedure

1. **If there is no definition here yet, author one first.** Running a plan that has never
   been compiled for this repository is Author followed by Run, and it is the one time you
   cross into another capability's document: follow [authoring.md](authoring.md), then come
   back and start at step 2. The start refuses in those words if you reach it first.

2. **Settle the repository.** It came from the request. If the request named none, ask; if a
   definition exists and disagrees with its own encoded repository, the start refuses and
   names both — the two answers are to run against the encoded repository or to re-author
   for the named one, and there is no third.

3. **Settle the occasion.** `--trigger fresh` for an ordinary run, `--trigger recovery
--recovering <run-id>` to continue a run, `--trigger pinned --occasion <value>` to
   continue one by hand. A recovery reads the occasion out of that run's record and never
   invents one, and a signal that contradicts the trigger is refused rather than dropped. A
   recovery goes only through the plan that run was a run of, generated from the graph it
   ran: a different `--plan`, or a plan re-authored since, is refused before anything is
   written — start a fresh run of the re-authored plan instead. A request to run a past
   execution again is a fresh run of the plan that execution ran, named in its record.

4. **Check what would run, if the person has not seen it.**
   `python3 -m cairn explain workflow --plan <slug> --repository <path>` says what the
   definition does and whether it is still the file Cairn wrote, and starts nothing.

5. **Start it.** The request is the go-ahead; nothing is put to the person first.
   `python3 -m cairn run start --plan <slug> --repository <path> --trigger <shape>`. The run
   id is minted for you; pass `--run-id` only to choose one. The branch comes from the
   definition, which already declares one; pass `--parent-branch <name>` only where the
   request asked for a different one. It refuses a definition that does not pass the
   execution gate, an engine that is not the pinned version, a shell the engine cannot start
   a run from, and a working tree with uncommitted work or an unresolved merge in it — each
   before anything is written.

6. **Hand over the address.** The command prints four lines — the run id, the branch, where
   the run can be watched, and the command that reads its record — and it prints them
   **before it invokes the engine**, so a start killed under a caller's own timeout has
   still told you the name of the run. Where the plan has run here before it also states the
   occasion reading it took and what the other reading would have meant; say that too. It
   then returns as soon as the engine has taken the run on, not when the run ends: a long
   plan is a blocked terminal otherwise, and an agent harness kills its own tool call long
   before that. The run keeps going without it; the engine is launched in its own session
   and its output goes to `runs/<run-id>/engine.log`.

   `--wait` blocks for the whole run and adds a line with the engine's exit status. It is
   for a caller that has no timeout of its own, and its terminal stays silent while it
   blocks — the engine's own words go to the log, not to the screen. Do not pass it from a
   harness.

   **There is a third answer**, and it is the detached one. If the engine has neither
   registered the run nor exited within thirty seconds, the command says so and exits zero,
   leaving the engine running (under `--wait` it keeps blocking instead, and asks again
   when the engine finally exits): it may be a moment away, and killing a run on a timer is
   the one destructive thing this command could do. Read `runs/<run-id>/engine.log` for what
   the engine said, and `cairn report --run <run-id>` a minute later for whether it took the
   run on. **Do not start the run again** until the report says it did not.

7. **When it ends, report it.** [reading.md](reading.md). The command's own exit status says
   only that the run was started: a run that dropped a branch exits zero at the engine level,
   and the verdict is derived by walking every node, which is `cairn report`'s to give.

## Recovery is re-running

There is no separate resume mode and no repair command (I4). A step already done re-runs as
a cheap no-op because its marker is committed alongside the work it describes, so recovering
a run is an ordinary start carrying the occasion it continues. Never `dagu retry`.

## Refusals

**Before anything is written**, so leaving the repository as it was: a malformed run id, an
engine that is not the pinned version, **a shell the engine cannot start a run from**, an
engine that cannot say where it keeps its run history, **a working tree with uncommitted work
in it, or a merge, rebase or cherry-pick left unresolved** — the refusal names the paths, and
a person settles them — and a definition that did not pass the execution gate. Clear the
cause and start again.

### The shell has to be allowed to bind a unix socket

Every run opens one — `/tmp/@dagu__<home>_<dag>_<hash>.sock` — before any step runs, so a
shell that may not `bind` cannot start a run at all. This is the ordinary case when Cairn is
driven through a coding-agent harness, because such harnesses sandbox their shell by default
and the person may not know a socket is involved. It has **two spellings**:

- the immediate refusal — `failed to start the unix socket server: listen unix …: bind:
operation not permitted`;
- **silence** — the start sits with no status data and no log until something kills it.

Neither is visible at authoring time: `dagu validate` and `dagu dry` never bind, so a
workflow authors cleanly in an environment that cannot run it. `run start` therefore
rehearses a one-step run in a scratch engine home before it launches anything, and refuses
with the engine's own words. What clears it is issuing the start from a shell with the
sandbox lifted for that one command.

**After it, inside the run**, because they are the run's first act rather than the start's:

- **the repository's run lock is held** — the refusal names the holder and its age. One
  repository, one run (I6). Wait for it or run against a different repository.
- **the working tree dirtied itself between the start and the run's first act** — the
  start read it clean; the run's own refusal is the backstop, not the interface.
- **the repository parameter was varied away from the authored one** — re-author instead.

Those three fail a run that really started, so recovering it is a start with `--trigger
recovery`. None of them is a thing to retry in a loop.

**A start that died still left a name.** The run id is printed before the engine is invoked,
so `cairn report --run <run-id>` and `run start --trigger recovery --recovering <run-id>`
both have something to quote even though the terminal is gone.

**An engine that exited without taking the run on** leaves no run: what the engine said is
in `runs/<run-id>/engine.log`, and the cause is cleared before starting again.

**Nothing here stops a run.** The engine is started in its own session precisely so a
closing terminal and a harness's process-tree kill cannot reach it — which also means Ctrl-C
cannot. A run holds the repository's lock until it ends; to end one
early, stop it at the engine's own view, then `python3 -m cairn supervise reconcile` so the
killed run is given a terminal status.

## Where the engine's view is better

The graph drawn live, each step's state as it changes, the logs, the timings and the node a
failure halted at. It reads a finished run identically to a live one and it survives the
server restarting. Link to it; do not narrate it.

What it will never answer is **divergence** and the **verdict**. Those are Cairn's and they
live in the run record. Say which is which.
