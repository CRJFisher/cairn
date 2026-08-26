# Running a plan

| Contract       | Value                                                                                              |
| -------------- | -------------------------------------------------------------------------------------------------- |
| Capability     | `run`                                                                                              |
| Entered when   | the dispatch table selected **run**                                                                |
| Preconditions  | a generated definition exists for the plan; the repository came from the request                   |
| Bound on entry | `capability` · `repository` · `workflow` · `occasion_reading` · `authorisation`                    |
| Owns           | the offer, the occasion reading, the engine trigger, and the address the run is watched at         |
| Defers to      | [../SKILL.md](../SKILL.md) · [../docs/triggers.md](../docs/triggers.md) · [reading.md](reading.md) |
| Triggers       | a paid run, on an unspent authorisation only                                                       |

## The procedure

1. **If there is no definition here yet, author one first.** Running a plan that has never
   been compiled for this repository is Author followed by Run, and it is the one time you
   cross into another capability's document: follow [authoring.md](authoring.md), then come
   back and start at step 2. The offer refuses in those words if you reach it first.

2. **Settle the repository.** It came from the request. If the request named none, ask; if a
   definition exists and disagrees with its own encoded repository, the offer refuses and
   names both — the two answers are to run against the encoded repository or to re-author
   for the named one, and there is no third.

3. **Settle the occasion.** `--trigger fresh` for an ordinary run, `--trigger recovery
--recovering <run-id>` to continue a run, `--trigger pinned --occasion <value>` to
   continue one by hand. A recovery reads the occasion out of that run's record and never
   invents one, and a signal that contradicts the trigger is refused rather than dropped.

4. **Check what would run, if the person has not seen it.**
   `python3 -m cairn explain workflow --plan <slug> --repository <path>` says what the
   definition does and whether it is still the file Cairn wrote, and starts nothing.

5. **Offer it.** `python3 -m cairn run offer --plan <slug> --repository <path> --trigger
<shape>`. It prints the price, the occasion reading it is taking and what the other reading
   would have cost, and one offer id. **Print what it printed.** Do not summarise the cost
   and do not compose your own — and do not expect a fixed list of facts, because the price
   is composed from the definition's own topology. Every run states its paid sessions with
   their ceilings, models and timeouts, the working tree, the run lock, the commits, and the
   unix socket the starting shell must be allowed to bind; the worktrees and the merge are
   stated only by a definition that has them, so a chain-shaped plan prices neither.

   The branch comes from the definition, which already declares one, and that is the branch
   priced. Pass `--parent-branch <name>` only where the request asked for a different one;
   then that branch is what is priced and what the run will use. There is no way to change it
   afterwards, which is the point.

6. **Wait for a qualifying yes** ([../SKILL.md](../SKILL.md)) — unless the request was
   itself an unambiguous run instruction, in which case it already is one and the offer and
   the start are the same turn. Either way:
   `python3 -m cairn run start --repository <path> --offer <offer-id> --reply '<their words,
verbatim>'`. The run id is minted for you; pass `--run-id` only to choose one. It refuses
   a bare acknowledgement, a reply that declines, an id that names no offer, an offer already
   spent, and a definition that changed since it was priced — and every one of those refusals
   happens before the offer is spent, so the acceptance still stands.

7. **Hand over the address.** The command prints four lines — the run id, the branch, where
   the run can be watched, and the command that reads its record — and it prints them
   **before it invokes the engine**, so a start killed under a caller's own timeout has
   still told you the name of the run your acceptance bought. It then returns as soon as the
   engine has taken the run on, not when the run ends: a plan bounded at forty-four hours is
   forty-four hours of a blocked terminal otherwise, and an agent harness kills its own tool
   call long before that. The run keeps going without it; the engine is launched in its own
   session and its output goes to `runs/<run-id>/engine.log`.

   `--wait` blocks for the whole run and adds a line with the engine's exit status. It is
   for a caller that has no timeout of its own, and its terminal stays silent while it
   blocks — the engine's own words go to the log, not to the screen. Do not pass it from a
   harness.

   **There is a third answer.** If the engine has neither registered the run nor exited
   within thirty seconds, the command says so and exits zero, leaving the engine running:
   it may be a moment away, and killing a run the offer has already paid for, on a timer,
   is the one destructive thing this command could do. Read `runs/<run-id>/engine.log` for
   what the engine said, and `cairn report --run <run-id>` a minute later for whether it
   took the run on. **Do not offer the run again** — the acceptance is spent either way.

8. **When it ends, report it.** [reading.md](reading.md). The command's own exit status says
   only that the run was started: a run that dropped a branch exits zero at the engine level,
   and the verdict is derived by walking every node, which is `cairn report`'s to give.

## Recovery is re-running

There is no separate resume mode and no repair command (I4). A step already done re-runs as
a cheap no-op because its marker is committed alongside the work it describes, so recovering
a run is an ordinary start carrying the occasion it continues. Never `dagu retry`.

## Refusals, and which of them cost the acceptance

**Before the offer is spent**, and so leaving the yes standing: a malformed run id, an engine
that is not the pinned version, **a shell the engine cannot start a run from**, a reply that
acknowledges or declines rather than accepts, an id naming no offer, an offer already spent,
a damaged offer, and a definition that changed since it was priced. Clear the cause and
answer once — the same acceptance is still good.

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
rehearses a one-step run in a scratch engine home before it spends the offer, and refuses
with the engine's own words. What clears it is issuing the start from a shell with the
sandbox lifted for that one command.

**After it, inside the run**, because they are the run's first act rather than the start's:

- **the repository's run lock is held** — the refusal names the holder and its age. One
  repository, one run (I6). Wait for it or run against a different repository.
- **the working tree is dirty, or a merge is unresolved** — a person settles it.
- **the repository parameter was varied away from the authored one** — re-author instead.

Those three fail a run that really started, so they consume the offer and recovering needs a
fresh one. None of them is a thing to retry in a loop.

**A start that died still left a name.** The offer is claimed before the engine is invoked,
which is correct — a start that really began must consume it — so a killed start is a spent
yes. What it is not any more is an anonymous one: the spent marker beside the offer, at
`<git-common-dir>/cairn/offers/<offer-id>.spent`, holds the run id and the engine command,
so `cairn report --run <run-id>` and `run offer --trigger recovery --recovering <run-id>`
both have something to quote even though the terminal is gone.

**An engine that exited without taking the run on** is neither of the two lists above: the
offer is spent and no run exists, so clearing the cause needs a fresh offer as well as a
fresh yes. What the engine said is in `runs/<run-id>/engine.log`.

**Nothing here stops a run.** The engine is started in its own session precisely so a
closing terminal and a harness's process-tree kill cannot reach it — which also means Ctrl-C
cannot. A run holds the repository's lock until it ends or the ceiling kills it; to end one
early, stop it at the engine's own view, then `python3 -m cairn supervise reconcile` so the
killed run is given a terminal status.

## Where the engine's view is better

The graph drawn live, each step's state as it changes, the logs, the timings and the node a
failure halted at. It reads a finished run identically to a live one and it survives the
server restarting. Link to it; do not narrate it.

What it will never answer is **cost** — there is no such field anywhere in the engine's model
— **divergence**, and the **verdict**. Those are Cairn's and they live in the run record.
Say which is which.
