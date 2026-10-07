# Putting a plan on a schedule or an external trigger

| Contract       | Value                                                                                               |
| -------------- | --------------------------------------------------------------------------------------------------- |
| Capability     | `schedule`                                                                                          |
| Entered when   | the dispatch table selected **schedule**                                                            |
| Preconditions  | a plan or workflow named; the repository resolved and its provenance known; a cadence was asked for |
| Bound on entry | `capability` · `repository` · `workflow` · `cadence`                                                |
| Owns           | the cron expression's place, the scheduler daemon, and the honest answer about queues               |
| Defers to      | [../docs/triggers.md](../docs/triggers.md) · [authoring.md](authoring.md)                           |
| Triggers       | a scheduler daemon, once the plan is installed and `start` is run                                   |

**A schedule needs a scheduler process.** A cron firing and an external webhook run on the
same thing: a scheduler, whose retry scanner re-executes every failed run recorded on this
machine in the previous 24 hours — including runs Cairn never wrote, three attempts each.
Scheduling installs what was asked for and says this plainly while it does; it is
information and asks nothing.

**Wanting the view is not scheduling.** `dagu server` holds no run state, binds loopback, and
needs no scheduler. Someone who says "I want to see the graph" is asking for
[reading.md](reading.md), not for this.

## The procedure

1. **The cron expression goes in at authoring time.** A workflow is generated and never
   hand-maintained, so re-author with `--schedule '<cron>'`
   ([authoring.md](authoring.md)). The engine validates the expression against the machine's
   own clock; Cairn parses none of it.

2. **Install it.** `python3 -m cairn schedule install --plan <slug> --repository <path>
--session <path>`. Its first line is the repository it resolved and where that came from — a
   repository named in the request or the one this conversation is in, a cadence over a plan
   slug naming no document to find one from — and that line is the first line of your reply
   too, because a schedule armed against
   the wrong repository fires on time for years. Where the candidates leave real doubt it asks
   rather than installing ([../SKILL.md](../SKILL.md)).
   It links an immutable admitted snapshot into the directory the scheduler watches, which
   is not where Cairn writes it — a file carrying a schedule that was never installed fires
   never and says nothing. A name already taken by another plan is refused rather than
   replaced. The command states what the scheduler does on this machine.

3. **Start the scheduler, or say plainly that nothing will fire.**
   `python3 -m cairn schedule start` **becomes** the scheduler and runs in the foreground
   until killed, so keeping a nightly plan firing means keeping that process alive under
   `launchd`, `systemd`, or a terminal left open. It asserts at that moment that the machine
   is safe to run a scheduler on and refuses otherwise, naming every failed run it would have
   re-executed. `python3 -m cairn supervise base-config --disable` is what makes it safe.

4. **Answer "is it actually going to fire?" honestly.** `python3 -m cairn schedule status`
   names every run sitting queued with nothing draining the queue. A trigger that was accepted
   and does nothing is the failure mode this exists for.

## An external trigger needs the same daemon

A webhook does not execute a run; it enqueues one, and only the scheduler drains the queue.
So it is not a lighter alternative to a schedule — it is the same daemon through a
different door. Cairn does not create the webhook and holds no credential: the engine shows
a bearer token once, and `--webhook-token-sink '<where it went>'` records the place, never
the value.

A webhook also cannot set parameters. Its JSON body arrives beside them while the declared
defaults stand, so a webhook that targets a particular repository does it by reading that
payload inside a step.

## Timezones, said once

The engine evaluates a cron expression against the machine's local clock, while a
period-scoped step buckets by UTC. A nightly plan whose steps are `daily`-scoped therefore
fires on local time and expires on UTC — worth saying out loud before an hour near midnight
is chosen.

## Where the engine's view is better

Seeing that a scheduled run fired, and what it did. Its own page for the workflow lists every
run of it. What it will not tell you is whether the scheduler is up, which is
`schedule status`'s answer.
