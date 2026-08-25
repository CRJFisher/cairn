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

   **Write it outside the working tree.** The repository's own admin directory —
   `<repository>/.git/cairn/graph.json` — is its home, beside the definition the generator
   writes. A run's first act refuses over a dirty tree, so a graph left beside the plan
   document stops the very run this authoring is for.

2. **Validate it.** `python3 -m cairn plan validate graph.json --source-root <plan-dir>`. A
   nonzero exit means the graph does not go forward. Fix the graph, never the validator.

3. **Show the parse report and wait.** `python3 -m cairn plan report graph.json` prints every
   step's task in full, every edge with the words behind it, everything left out with its
   cause, and every open question. The author's confirmation of that report is what makes the
   graph the plan's rather than the derivation's, so it is shown before anything is generated.

4. **Answer the assertions.** `python3 -m cairn plan propose graph.json` names every step
   whose end state nothing asserts, beside the command the derivation proposed for it — the
   reading declared on the graph's own `missing_verify` question, resting on the sentence it
   quotes ([../docs/plan-derivation.md](../docs/plan-derivation.md)). **The answers are the
   author's, never yours**: show the offer, and record with `python3 -m cairn plan answer
… --out graph.json`. A step with no command and no recorded answer never reaches the
   engine, and that refusal is correct.

5. **Generate.** `python3 -m cairn workflow author <repository>/.git/cairn/graph.json --repository <path>
[--parent-branch <name>] [--schedule '<cron>']`. It writes into the repository's own admin
   directory, gates the definition where it cannot be run from, and moves it into place only
   once it passes.

6. **Read back what it said it replaced.** Re-authoring always proceeds, and it says which of
   nine states it found: writing it, replacing it unmodified, modified since Cairn wrote it,
   generated from another plan, written by an older generator, the plan changed since. Carry
   that sentence to the person verbatim — a hand edit being overwritten is a thing they are
   owed rather than a detail.

7. **A preflight refusal is a hard stop.** It names the offending step and the rule. The fix
   is in the plan, not in the emitted file; every rule exists because the engine's own
   validation passes the same document.

## Then offer, or stop

Authoring starts nothing. When the workflow exists, say so and offer the run rather than
performing it — the offer, its price and what counts as accepting are
[../SKILL.md](../SKILL.md)'s consent rule, and the procedure is
[running.md](running.md)'s. Nothing here may start a run.

## Where the engine's view is better

Nowhere, for this capability. Its workflow editor is a legitimate place for a quick
experiment, and an edit made there is a divergence Cairn will report at the next authoring
rather than prevent.
