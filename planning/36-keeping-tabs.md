# 36 — Keeping tabs: a live, plain-language summary of every session

**North star ([principle 2](../PRINCIPLES.md)).** A person glances at one page and knows, for each
running session, what it is doing, what it has done, and whether it looks stuck — in sentences,
not JSONL, and current to within a minute or so.

**Status: idea, not a plan.** Extends [20](20-watching-a-live-session.md), whose `where` line is a
mechanical fact (turn, last tool, age). This adds a _meaning_ layer on top. Answering questions is
[35](35-human-in-the-loop-steps.md).

## The idea

A summariser (`claude -p`, a small model) periodically reads a session's recent events and writes
a short status to a store; a web page renders the store.

The first iteration reads the agent's own task list instead of summarising with a model — see
[40](40-task-list-as-the-tab-source.md) for which events and stores expose it.

## Design choices to settle

- **Trigger: session hooks or Cairn's wrapper?** Claude Code hooks (`PostToolUse`, `Stop`) would
  work, but must be installed into the target repo's session and the summariser's own session must
  not fire them (recursion). Cairn's `run_claude` already sees every event as it tees the stream —
  it can launch the summariser itself with no hook config at all. Recommendation: the wrapper.
- **Cadence.** Per tool call is too costly and noisy. Debounce: on turn end, at most every N
  seconds, plus once at session end (which doubles as a closing summary for the report).
- **Store.** One small JSON file per node beside the run's record (status line, summary, updated
  at, last-event age). No server needed to write it; the record stays the source of truth and the
  summary is commentary, never a verdict.
- **Page.** Needs something to serve it: a tiny local static server, or a page that polls the
  files. Conflicts with 20's "no daemon" stance — decide whether the page is opt-in.
- **Honesty.** The summary is a model's reading; the page shows its age and the mechanical `where`
  facts beside it, so a stale or wrong summary is visible as such.

## Open questions

1. Does summarising one session reliably fit a small model on a tail of the transcript, or does it
   need a rolling summary (previous summary + new events)?
2. Where does the page live — Cairn-served, or a static file under the run directory?
3. Is the Dagu view still linked for step logs, or does this page replace the need to open it?
