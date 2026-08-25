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
<shape>`. It prints the price — the paid sessions with their dollar ceilings, models and
   timeouts, the working tree, the worktrees beside the repository, the run lock, the
   commits and the merge — the occasion reading it is taking and what the other reading
   would have cost, and one offer id. **Print what it printed.** Do not summarise the cost
   and do not compose your own.

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

7. **Hand over the address.** The command prints where the run can be watched. That is the
   engine's own view and it is the better place to watch a graph — see below.

8. **When it ends, report it.** [reading.md](reading.md). The command's own exit status says
   only that the run was started: a run that dropped a branch exits zero at the engine level,
   and the verdict is derived by walking every node, which is `cairn report`'s to give.

## Recovery is re-running

There is no separate resume mode and no repair command (I4). A step already done re-runs as
a cheap no-op because its marker is committed alongside the work it describes, so recovering
a run is an ordinary start carrying the occasion it continues. Never `dagu retry`.

## Refusals, and which of them cost the acceptance

**Before the offer is spent**, and so leaving the yes standing: a malformed run id, an engine
that is not the pinned version, a reply that acknowledges or declines rather than accepts, an
id naming no offer, an offer already spent, a damaged offer, and a definition that changed
since it was priced. Clear the cause and answer once — the same acceptance is still good.

**After it, inside the run**, because they are the run's first act rather than the start's:

- **the repository's run lock is held** — the refusal names the holder and its age. One
  repository, one run (I6). Wait for it or run against a different repository.
- **the working tree is dirty, or a merge is unresolved** — a person settles it.
- **the repository parameter was varied away from the authored one** — re-author instead.

Those three fail a run that really started, so they consume the offer and recovering needs a
fresh one. None of them is a thing to retry in a loop.

## Where the engine's view is better

The graph drawn live, each step's state as it changes, the logs, the timings and the node a
failure halted at. It reads a finished run identically to a live one and it survives the
server restarting. Link to it; do not narrate it.

What it will never answer is **cost** — there is no such field anywhere in the engine's model
— **divergence**, and the **verdict**. Those are Cairn's and they live in the run record.
Say which is which.
