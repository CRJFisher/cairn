# Reading a run, and explaining what it means

| Contract       | Value                                                                                      |
| -------------- | ------------------------------------------------------------------------------------------ |
| Capability     | `report`, `explain`                                                                        |
| Entered when   | the dispatch table selected **report** or **explain**                                      |
| Preconditions  | for a report, a run to read; for an explanation, a workflow, a run or one of Cairn's words |
| Bound on entry | `capability` · `repository` · `run` · `step` · `workflow` · `verdict_word`                 |
| Owns           | the verdict, the six questions, and what a frozen word means                               |
| Defers to      | [../docs/report.md](../docs/report.md) · [../docs/run-model.md](../docs/run-model.md)      |
| Triggers       | nothing                                                                                    |

**Nothing here starts, locks or writes anything.** Both capabilities read, and both work with
the engine stopped. That is why they share one document: neither procedure fills a screen on
its own. Their entry preconditions do differ, and the row above says how.

## Reporting

1. **Find the run if it was not named.** `ls <repository>/.git/cairn/runs` lists every run
   that repository has had. Every run leaves a record whether anyone was watching or not.

2. **Render it.** `python3 -m cairn report --run <id> --repository <path> --session <path>
[--format terminal|markdown|html]`. Terminal is the default; markdown is the durable artifact
   for a repository or a pull request; HTML is self-contained and draws the graph. The
   repository it read and where that came from are stated beside the rendering rather than in
   it, because the rendering's own first line is the verdict — name that repository in the
   first line of your reply, so a run nobody finds reads as the wrong repository rather than
   as a repository with no runs.

3. **Answer in the order it answers.** Did it work, what to do next, what needs attention,
   what each step did, what shape the run was, what the receipts are. The order is the design
   — nobody's first question is the topology — so do not reorder it and do not lead with the
   step table.

4. **Never restate the verdict in your own words.** A run with exclusions is
   `green_with_exclusions` and it is not a clean success; the engine calls that same run
   `Succeeded` with exit 0. The report's exit status is the run's verdict, not the command's
   health.

5. **Say who started it.** A run with an actor was started by that person at the engine's
   view; a run with none is attributed from its trigger — Cairn's own skill, the scheduler,
   a webhook, the retry scanner, the run above it, or nothing at all. Report the attribution
   the record carries, so a run the machine started on its own is not credited to Cairn.

6. **Say what the report could not read.** Where the record refused a piece of the run's own
   evidence, the first screen counts it and the attention section names each one. Repeat
   that rather than summarising past it: the facts under a refusal may be missing rather
   than absent, and nothing refused raised any outcome.

A question about what a run cost is not one Cairn answers; say that in one line and go on
with what the record does hold.

If the honest answer is "this needs to be run", say so and stop. Running is
[running.md](running.md)'s, and it needs its own turn.

## Explaining

Four questions, four sources, and the source is what makes each answer trustworthy.

- **Which repository is this about?** `python3 -m cairn explain repository --session <the
directory this conversation is in> [--subject <path>]…` — a repository named in the request,
  the one holding the request's subjects, or the session's own, whichever answers first. It
  prints the one it took and where that came from, and asks instead where the candidates
  leave real doubt ([../SKILL.md](../SKILL.md)). Every other capability's first command needs
  this answer, and so does the first line of your reply.
- **What would this workflow do?** `python3 -m cairn explain workflow --plan <slug>
--repository <path>` — read off the generated definition without running it, including
  whether the file is still the one Cairn wrote. It prints an account, not the definition: a
  workflow is tens of kilobytes and re-emitting one through a conversation is a copy nobody
  can reproduce faithfully.
- **What does this word mean?** `python3 -m cairn explain word <word>` — quoted from the
  frozen vocabulary. **Do not paraphrase a verdict, an outcome, an attention kind, a next
  action or an exclusion cause from memory.** One run described three ways is the failure the
  single phrasebook exists to prevent, and a fourth rendering in conversation is the one
  nobody diffs. A word the vocabularies do not hold is refused rather than guessed at.
- **Why was this step excluded?** `python3 -m cairn explain exclusion --run <id> --step <id>
--repository <path>` — the cause the record carries, what it means, the divergence if there
  was one, and what it means for the next run. A step that was not excluded is said not to
  have been, rather than explained away.

Explain is a capability, not a fallback. A request nothing else fits is an ask
([../SKILL.md](../SKILL.md)), never quietly answered here.

## Where the engine's view is better

Watching a run go, and reading its logs and per-step timings afterwards. It draws the same
graph live and zoomable, its link survives the run ending, and past eighty nodes Cairn's own
drawing defers to it outright. Every run record carries that address, so a run is reachable
from its identity alone.

What it will never answer: the **divergence** between a workflow and the plan that generated
it, and the **verdict**, because a run that dropped a branch reports a clean success at the
engine level. Those two are why this capability exists beside the view rather than instead
of it.
