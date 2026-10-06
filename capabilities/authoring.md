# Authoring a workflow, and changing one

| Contract       | Value                                                                                                 |
| -------------- | ----------------------------------------------------------------------------------------------------- |
| Capability     | `author`, `edit`                                                                                      |
| Entered when   | the dispatch table selected **author** or **edit**                                                    |
| Preconditions  | a plan document, folder or graph exists on disk; the target repository came from the request          |
| Bound on entry | `capability` · `repository` · `plan_document` · `plan_graph` · `workflow`                             |
| Owns           | the derivation, the assertion conversation, generation, and what a re-authoring replaces              |
| Defers to      | [../docs/plan-derivation.md](../docs/plan-derivation.md) · [../docs/workflow.md](../docs/workflow.md) |
| Triggers       | a written definition in the repository's own admin directory                                          |

**Edit is authoring.** There is no in-place edit of a generated definition. The plan document
is the source of truth (I1), so changing what a workflow does means changing the plan and
authoring again; the generator states what it is replacing and never merges. Editing the
`.yaml` by hand is a divergence its editor owns, and Cairn's job is to make that visible
rather than to prevent it.

## The procedure

1. **Derive the graph.** Follow [../docs/plan-derivation.md](../docs/plan-derivation.md) — two
   passes over every document, not the index alone. Do not restate its rules here; the ones
   that go wrong most often are that a dependency is never defaulted to sequential, and that
   a verify command is never synthesised.

   **Write it to the plan's own home.** `python3 -m cairn plan home <plan-slug> --repository
<path>` prints it: `<git-common-dir>/cairn/graphs/<plan>.json`, one file per plan, outside
   the working tree and beside the definition the generator writes. A run's first act refuses
   over a dirty tree, so a graph left beside the plan document stops the very run this
   authoring is for — and a graph shared between plans would let authoring one overwrite the
   answers already given for another. Below, `<graph>` is the path it printed.

   **Skills and commands are the derivation's to check.** A task that runs a skill is only
   runnable headless if the skill may be model-invoked; one that only a person may start,
   and a built-in command, is written with its slash command as the task's first line; and
   one that cannot run headless, or does nothing a step could be verified for, is a
   question rather than a task ([../docs/plan-derivation.md](../docs/plan-derivation.md)).
   The parse report in step 3 shows the first line, so say which skills you checked.

2. **Validate it.** `python3 -m cairn plan validate <graph> --source-root <plan-dir>`. A
   nonzero exit means the graph does not go forward. Fix the graph, never the validator.

3. **Show the parse report and wait.** `python3 -m cairn plan report <graph>` prints every
   step's task in full, every edge with the words behind it, everything left out with its
   cause, and every question with its answer or the fact that it has none. The author's
   confirmation of that report is what makes the graph the plan's rather than the
   derivation's, so it is shown before anything is generated.

4. **Answer every question.** `python3 -m cairn plan propose <graph>` names every step whose
   end state nothing asserts, beside the command the derivation proposed for it — the reading
   declared on the graph's own `missing_verify` question, resting on the sentence it quotes
   ([../docs/plan-derivation.md](../docs/plan-derivation.md)) — and every other open
   question, each with the whole `python3 -m cairn plan answer … --out <graph>` invocation
   that records each answer it admits. **The answers are the author's, never yours**: show
   the question and the proposal, and record exactly what they said. Its exit status says only
   that the listing was made; `--json` carries `complete`, which says whether anything is
   left to ask.

5. **Generate.** `python3 -m cairn workflow author <graph> --repository <path> --source-root
<plan-dir> [--parent-branch <name>] [--schedule '<cron>']`. It re-reads every document the
   graph pins beneath the plan's directory, rechecks every digest and quotation, and refuses
   any question without a recorded answer and any step nobody was asked to assert — all
   before anything on disk is touched, so a refusal leaves the published workflow as it was.
   It then writes into the repository's own admin directory, gates the definition where it
   cannot be run from, moves it into place only once it passes, and prints a receipt naming
   the graph and the directory it was checked against.

6. **Read back what it said it replaced.** Re-authoring always proceeds, and it says which of
   nine states it found: writing it, replacing it unmodified, modified since Cairn wrote it,
   generated from another plan, written by an older generator, the plan changed since. Carry
   that sentence to the person verbatim — a hand edit being overwritten is a thing they are
   owed rather than a detail.

7. **A preflight refusal is a hard stop.** It names the offending step and the rule. The fix
   is in the plan, not in the emitted file; every rule exists because the engine's own
   validation passes the same document.

## Then run, or stop

Authoring starts nothing. When the workflow exists, say so. Where the request also asked for
a run, the next step is the run, and the procedure is [running.md](running.md)'s; where it
did not, stop here — nothing here may start a run on its own.

## Where the engine's view is better

Nowhere, for this capability. Its workflow editor is a legitimate place for a quick
experiment, and an edit made there is a divergence Cairn will report at the next authoring
rather than prevent.
