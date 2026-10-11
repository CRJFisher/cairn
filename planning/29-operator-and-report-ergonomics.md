# 29 — The common journeys have one discoverable, truthful surface

The underlying contracts are extensive, but first use still begins without installation
instructions, “last run” requests have no resolver, scheduler status does not establish whether
a scheduler exists, and reports drop plan-facing identity or alter content labeled verbatim.
These are not one-off wording fixes: they are missing operator journeys.

**Serves** all six capabilities, especially **Report** and **Schedule**. Safety wording remains
precise, but a person should not need internal paths, engine ids, or implementation verbs to
complete an ordinary task.

## A — A clone reaches `/cairn`

Complete [16](16-release.md)'s acquisition work with a short README path:

1. supported platforms and Python 3.11;
2. install or verify the pinned Dagu (2.18.2, [41](41-dagu-2-18-upgrade.md)) and the agent provider;
3. install/register the Cairn skill;
4. verify discovery without spending or mutating;
5. author and explain a minimal plan before the first offer.

`python3 -m cairn --help` remains honest that the CLI is internal, but its first screen leads with
the six user tasks, `/cairn`, prerequisites, and one example before runtime plumbing.

## B — Find runs by the plan language a person has

Add a read-only, plan-filtered run listing and an explicit latest-run resolver. Each entry names
plan, run id, timestamp, trigger, and verdict; unreadable entries are visible rather than
discarded. “How did the last run of plan A go?” resolves deterministically even when runs from
several plans are interleaved.

Carry the original step title/slug into the run record and render it beside the stable engine id,
so sanitisation and collision suffixes do not make a report require a graph lookup.

This complements, rather than duplicates, [18](18-first-run-friction.md)'s repository inference,
[20](20-watching-a-live-session.md)'s live breadcrumbs, and [34](34-follow-on-runs.md)'s follow-on drafting.

## C — Scheduling status answers operational status

`schedule status` currently lists definitions, safety, and queues but never establishes scheduler
liveness; it can claim a queued run has “nothing draining” while a scheduler is alive.

- Distinguish running, stopped, stale identity, inaccessible, and unknown.
- Say “queued” without claiming no drainer unless liveness proves that conclusion.
- Engine surfaces on the pin that bear on this ([41](41-dagu-2-18-upgrade.md) O3, O6): `dagu ps
--json`, an API field naming a running run's local process, and a global scheduler pause
  (`/services/scheduler/pause`). Measured on 2.18.2, `dagu ps` listed a killed run as fresh, so
  none of them is proof of liveness on its own; Cairn's process check stays the authority. A
  paused scheduler is a distinct state that status reports rather than calling it dead.
- Show each installed plan's cron or external-only trigger and whether its link resolves.
- Provide supported launchd/systemd installation, status, and stop guidance rather than leaving a
  foreground `execvp` as the operational story.
- Make trigger installation transactional: a symlink collision or other refusal leaves no
  sidecar claiming publication or webhook-token disposition.

## D — Preserve actionable evidence in every report

- Assertion diagnostics carry bounded, labeled stdout and stderr tails; harmless stdout never
  hides the failure on stderr.
- A `Verbatim` block preserves the exact payload. Markdown uses collision-safe fenced blocks and
  terminal output does not mutate copied content.
- The HTML graph has an accessible name and a nonvisual node/edge/status equivalent.
- Terminal wrapping and alignment use display-cell width and the active terminal width, covering
  CJK, combining marks, and emoji.
- Correct the timed-out fixture prose in `docs/run-model.md` so current emitted behavior is not
  described as having an assertion result it does not carry.

## E — Use accurate launch language

`run start` prints `started` before the engine accepts the run and may then print that the engine
refused it. Print the durable identity as `run` or `launching`; reserve `started` for confirmed
registration. Automation and incident logs must never contain an affirmative start claim for a
run the engine declined.

## Acceptance

- A fresh supported machine reaches a discovered `/cairn` and a read-only explanation from the
  README alone.
- A plan's latest readable run is selected deterministically and every report shows plan-facing
  step identity.
- Schedule status distinguishes liveness states without claiming more than it measured, and a
  failed install leaves no active trigger sidecar.
- Multiline/tabbed/backtick-containing verbatim text round-trips exactly through every sink.
- Every HTML graph has an accessible name and textual equivalent; terminal lines respect display
  width.
- Engine refusal never follows a line claiming the run started.

## Touches

`README.md`, packaging/skill installation metadata, `cairn/__main__.py`,
`cairn/record/`, `cairn/report/`, `cairn/schedule.py`, `cairn/schedule_cli.py`,
`cairn/skill/cli.py`, capability documents, report/run-model/trigger documentation,
fixtures, and end-to-end journey tests.

## Close-out

Done. All five sections hold and every criterion above holds.

**A cold machine is told what it needs, and the path ends in an offer rather than a run.**
The README's install path is six steps: the floor (Python 3.11, git, the agent command line,
macOS or Linux and WSL on Windows), the pinned engine against its own checksums, the
checkout, the engine's machine-wide configuration, this checkout registered as the skill, and
five checks that spend nothing — the engine's version, the agent installation a session runs
as, the registered skill resolving to this checkout, the engine's config carrying Cairn's
values, and the worked example. Then a minimal plan this repository ships is authored into a
scratch repository and explained: the parse report, the definition written through the
engine's own `validate` and `dry`, and what running it would do, step by step, with nothing
started and nothing spent. That last line is the shape of every offer.

`python3 -m cairn --help` leads with the way back. It says the command line is internal,
names `/cairn`, lists the six tasks — read from the skill's own vocabulary, so a seventh
capability cannot be added without this screen naming it — states the prerequisites, gives
one example that costs nothing, and only then reaches the subcommands an emitted workflow
invokes.

**A run can be found by the plan language a person has.** `cairn record list` names every run
a repository holds — plan, run id, start moment, trigger, verdict — newest first and filtered
to one plan where one is named, and `cairn record latest --plan` resolves one run by a rule
stated once: the newest _readable_ run of that plan, ties broken by run id, exiting on that
run's own verdict so a script can ask too. A run whose record cannot be read is listed with
the fault and the command that rebuilds it, and is never what the resolver returns — the run
somebody is looking for is the one that failed badly enough to damage its own record. A
directory holding neither a record nor a lock report is not listed at all: the engine's
authoring rehearsal runs every precondition, and Cairn's gates leave reports under the run id
it minted for the dry pass.

**Every report names a step twice.** The engine's id, which its nodes, its branch and its
worktree are called, and beside it the name the plan document gave it — the title in a column
of the step account, the name it was written under in its receipts. Both are read from the
plan's own graph against the `graph_sha256` the run recorded at its first act, so a graph the
next authoring rewrote names nothing and both fields read as not recorded rather than naming
another plan's steps. `work_build_the_thing_2` no longer sends a reader to the graph.

**Scheduling status establishes what it claims.** Its first line is whether a scheduler
exists, in six words: `running`, `paused`, `stopped`, `stale_identity`, `inaccessible`,
`unknown`. Liveness comes from the engine's own registry of scheduler processes
(`<data>/service-registry/scheduler/*.json`, carrying each `pid` and the moment it started)
adjudicated by Cairn's own process check, because the registry outlives the process it names
— measured on 2.18.2, a scheduler killed outright leaves its entry in place, and `dagu ps`
has no `--json` at this pin and called a killed run fresh besides. A registry this shell may
not read is `inaccessible` and never an empty machine; a registry that is not there at all is
`unknown`, because a scheduler under another `DAGU_HOME` registers somewhere else. The
engine's global pause (`<data>/scheduler/paused.json`, set through its API) is a live process
deliberately firing nothing, reported as its own state and as a standing instruction wherever
it is set — never as a dead scheduler. **"Nothing is draining the queue" is now a
measurement**: it is said for `stopped` and `stale_identity` alone, a running scheduler means
a queued run is queued behind other work, and the three states that establish nothing say so
and why.

Status also names, per installed definition, what the link points at, whether it still
resolves — a pruned target is refused at load, which is a schedule that silently never fires
— and the cron expression it fires on or that only an external trigger will. And it names
this machine's own way of keeping a scheduler alive, because a foreground `execvp` dies with
its terminal: `cairn schedule service` prints a user-level `launchd` agent or `systemd --user`
unit composed from this interpreter, this Cairn and this engine's watched directory, with the
install, status and stop commands; `--write` writes it and still leaves loading it to the
person. The unit runs `cairn schedule start` and never `dagu scheduler`, so the hazard
assertion is not skipped at every boot.

**An install is one act or none.** The sidecar recording what was installed, and where a
webhook's bearer token went, is written only once the link holds; a refused name leaves none,
and a sidecar that cannot be written takes the link back out.

**A report keeps the evidence a person acts on.** A failed assertion's output is recorded per
channel — `assertion_stdout` and `assertion_stderr`, each bounded, each read on its own — and
rendered as two labelled blocks, so a type checker that chatters on standard output and names
the fault on standard error no longer has the fault hidden by the chatter. A `Verbatim` block
is the payload itself in all three sinks: markdown fences it in a block whose fence outruns
any backtick run inside it, the terminal hands over its own lines at column zero rather than
indenting what a reader will paste, and the HTML carries it as character data — multiline,
tabbed, backtick-bearing text round-trips exactly through every one of them. The drawn graph
carries an accessible name in both an `aria-label` and a `title`, with every layer's nodes,
their statuses and every dependency written out beside it as text, read off the same figure so
the three renderings still agree about every fact. Terminal lines are folded and padded in
**display cells** against the **active terminal's** width — CJK and fullwidth forms at two
cells, combining marks and joiners at none, emoji as the one glyph they render as — falling
back to the 88-column bound where there is no terminal, so a piped document is stable. A word
with nothing to fold at is left whole, because half a path is neither pasteable nor
searchable.

`docs/run-model.md` no longer describes the `timed-out` fixture as carrying an assertion
result: a step the engine killed left no report, its assertion's own gate declined, and the
step carries no assertion verdict and no divergence. The record version moved to 7.

**`started` is now a claim about registration.** `cairn run start` prints the durable
identity as `launching`, with the branch and both addresses, before the engine is invoked —
so a caller killed mid-launch still has the name of the run — and prints `started` only once
the engine has taken the run on. An engine refusal can no longer follow a line claiming the
run started.
