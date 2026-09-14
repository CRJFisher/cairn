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
2. install or verify Dagu 2.11.0 and the agent provider;
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

This complements, rather than duplicates, [18](18-first-run-friction.md)'s repository inference
and [20](20-visibility-and-follow-on.md)'s live breadcrumbs and follow-on drafting.

## C — Scheduling status answers operational status

`schedule status` currently lists definitions, safety, and queues but never establishes scheduler
liveness; it can claim a queued run has “nothing draining” while a scheduler is alive.

- Distinguish running, stopped, stale identity, inaccessible, and unknown.
- Say “queued” without claiming no drainer unless liveness proves that conclusion.
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
