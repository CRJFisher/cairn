# Reading a run, and explaining what it means

| Contract       | Value                                                                                      |
| -------------- | ------------------------------------------------------------------------------------------ |
| Capability     | `report`, `explain`                                                                        |
| Entered when   | the dispatch table selected **report** or **explain**                                      |
| Preconditions  | for a report, a run to read; for an explanation, a workflow, a run or one of Cairn's words |
| Bound on entry | `capability` · `repository` · `run` · `step` · `workflow` · `verdict_word`                 |
| Owns           | the verdict, the cost, the six questions, and what a frozen word means                     |
| Defers to      | [../docs/report.md](../docs/report.md) · [../docs/run-model.md](../docs/run-model.md)      |
| Triggers       | nothing                                                                                    |

**Nothing here starts, locks or writes anything.** Both capabilities read, and both work with
the engine stopped. That is why they share one document: neither procedure fills a screen on
its own, and neither has a consent rule because neither costs anything. Their entry
preconditions do differ, and the row above says how.

## Reporting

1. **Find the run if it was not named.** `ls <repository>/.git/cairn/runs` lists every run
   that repository has had. Every run leaves a record whether anyone was watching or not.

2. **Render it.** `python3 -m cairn report --run <id> --repository <path> [--format
terminal|markdown|html]`. Terminal is the default; markdown is the durable artifact for a
   repository or a pull request; HTML is self-contained and draws the graph.

3. **Answer in the order it answers.** Did it work, what to do next, what needs attention,
   what each step did, what shape the run was, what the receipts are. The order is the design
   — nobody's first question is the topology — so do not reorder it and do not lead with the
   step table.

4. **Never restate the verdict in your own words.** A run with exclusions is
   `green_with_exclusions` and it is not a clean success; the engine calls that same run
   `Succeeded` with exit 0. The report's exit status is the run's verdict, not the command's
   health.

5. **Say who started it.** A run with no recorded actor was started by Cairn; one with an
   actor was started by that person at the engine's view. An absent actor is never rendered
   as unknown, so Report accounts for runs the skill did not start.

If the honest answer is "this needs to be run", say so and stop. Offering a run is
[running.md](running.md)'s, and it needs its own turn.

## Explaining

Three questions, three sources, and the source is what makes each answer trustworthy.

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

What it will never answer: the **cost**, which has no field anywhere in the engine's model;
the **divergence** between a workflow and the plan that generated it; and the **verdict**,
because a run that dropped a branch reports a clean success at the engine level. Those three
are why this capability exists beside the view rather than instead of it.
