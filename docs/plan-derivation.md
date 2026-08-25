# Deriving a step graph from a plan document

The procedure an agent follows to turn a Markdown plan into a graph conforming to
[the plan contract](plan-contract.md). Two passes over the document, then the deterministic
half takes over.

A step's `kind`, and what each kind becomes in the engine, is [step-kinds.md](step-kinds.md).

Input: a path to a plan document, or to a folder of numbered task documents whose index is
`README.md`, `WORKLIST.md`, `PLAN.md`, or `index.md`.
Output: one `graph.json`, a parse report, and a list of questions for the author.

## Pass one — read every document and write the steps

**Read the whole plan, not its index.** A folder of numbered task documents is one plan,
and its index names the steps while the task documents carry the dependencies, the
acceptance criteria, and the actions. Deriving from the index alone produces a graph that
looks complete and is missing everything the index summarised — and each document read is
pinned in `plan.sources`, so what was read is a matter of record rather than of memory.

Within a document, read it whole before writing anything. A plan states its dependencies in
one place and its steps in another, and a linked-reference parser over any single section
derives a graph the document does not describe.

For each unit of work the plan names:

- **`slug`** is the plan's own name for it, verbatim — the numbered document's name where
  there is one, including its ordinal.
- **`title`** is the document's own heading or the index's own link text for it. Where the
  plan gives neither, the title is the slug; a prettified invention is not a title.
- **`task`** is the change the document asked for — not a re-imagining of it that serves
  the same goal. When the document says "delete the bespoke hook and script", the task
  says that, and not "consolidate hook handling".
- **`verify`** is the command the document gives for asserting the step's end state. If it
  gives none, the value is `null` and a `missing_verify` question is raised, which the
  authoring conversation later answers into the step's `assertion`. **Never
  synthesise a command into `verify`.** A synthesised check passes trivially and destroys
  the one guarantee the run makes. What you do instead is **propose**: where the document
  states the end state in prose, read it and declare the command you would offer as the
  question's `proposed`, with the sentence it rests on quoted verbatim as the question's
  `evidence` — the validator rechecks the quote, and a proposal quoting nothing or resting
  on words no document holds is refused. Read the end state the words actually state: a
  document saying a file _holds_ a word asks for its content, not its existence. Where the
  words state nothing a command can assert, propose nothing — a declared absence is the
  honest offer, and the author writes or declines unaided.
- **`kind`** is `command` when the document gives a command to run and nothing to decide —
  including a step whose whole content is waiting for something else to finish — and
  `agent.<provider>` otherwise.
- **`command`** is executable text the document itself contains, quoted verbatim and kept
  separate from the prose `task`. `--source-root` rechecks it against the documents, so
  anything paraphrased, completed, or corrected on the way in is refused. A plain command
  uses `command_type: exec`; polling until it succeeds uses `command_type: wait_until`.
- **`scope`** is `once` unless the document says the work has a cadence ("weekly",
  "each run", "whenever the inputs move"). A plan that never mentions freshness behaves
  exactly as it would if scopes did not exist.
- **`tools`** is a deny list, set only where the document gives a concrete deny pattern.
  A positive allow list is not inverted into denies. A step the document says nothing
  about keeps `null`.
- **`timeout`**, **`max_budget_usd`** and **`model`** are the step's bounds, and the
  document is their only lever: set each exactly where the document states it — "at most
  fifteen minutes" is a timeout of 900, "spend at most eight dollars on it" is a ceiling
  of 8.0, "pin it to the opus model" is a model of `opus` — and leave it out everywhere
  else, so the kind's default applies. Raising a workflow's timeouts, ceilings or models
  is therefore an edit to the plan document followed by re-authoring, not an edit to the
  generated file.

### State every task as an end state

Write each task as _bring X to a state where Y_. That phrasing is re-runnable by
construction: a step killed mid-work resumes into its own half-finished worktree, so a task
phrased as an action duplicates its work on the way back.

Whether a task converges is your reading to declare — no code re-reads the sentence. Where
the document's own action cannot be restated as an end state without changing what it asks
for — "add the hook to `~/.claude/settings.json`" — keep the document's words and raise a
`non_convergent_task` question naming the duplication and proposing the end state, with the
sentence you read quoted verbatim as its `evidence`. The author restates it, not the
derivation, and a declaration quoting nothing is refused by the validator.

### Leave out what the document defers

A step the document defers, gates on a future signal, or records as already done is not in
the graph. It goes in `omissions` with a reason (`deferred`, `gated`, `already_done`,
`out_of_scope`) and the words that put it there, quoted. An omission is never silent: a
plan whose "Deferred (v2)" section quietly became four steps is a plan Cairn misread, and
so is a plan whose every row is ticked and whose graph is full of work.

A plan the document has not green-lit is not an omission but a question: raise
`plan_gated`, quoting the words that gate it, and let the author say whether the plan is
live.

## Pass two — recheck every edge against the documents' own words

Go back to the documents. For every dependency in the graph, find the words that justify it
and record them **verbatim**. With `--source-root` the validator checks each quotation
against the documents, so a paraphrase is caught and a fabrication cannot land.

- **`origin: "declared"`** — a document states the dependency. A task document's
  `## Depends on` section saying "Tasks 02, 03." is a declaration; so is "Dependency order:
  01 → (02, 03) → 04 → 05", and so is "depends on the bucket store".
- **`origin: "derived"`** — you inferred it, and the evidence quotes what you inferred it
  from. A step naming an artifact an earlier step produces, an ordering the document calls
  an implementation order, a section that says one thing settles the vocabulary for
  another: all derivable, all quotable.
- **An edge you cannot justify is not an edge**, whichever origin you would have given it.
  Drop it and raise an `unjustified_edge` question instead. The validator rejects any edge
  with empty evidence, so labelling a guess `declared` buys nothing.

**A dependency is never defaulted to sequential.** Document order is not dependency order.
Two steps listed one after another with nothing connecting them are two roots, and the
concurrency that falls out is the plan's own.

Where an edge is defensible but genuinely uncertain — the document gives an implementation
order but never says one step needs another's output — keep the edge with its evidence and
raise an `ambiguous_dependency` question. A spurious edge costs only concurrency; a missing
edge lets a step run against a half-built state and pass.

## Then hand over to the deterministic half

1. **Sanitise ids** from the slugs — `python3 -m cairn plan ids <slug>…` — and record every
   break it reports in `plan.id_collisions`.
2. **Derive the plan slug** and confirm it is free:
   `python3 -m cairn plan slug <path> --against <worktree-parent> <workflow-dir> <run-dir>`.
3. **Pin every document read** in `plan.sources`, each with the SHA-256 of its bytes.
4. **Validate**:
   `python3 -m cairn plan validate graph.json --source-root <plan-dir>`. A non-zero exit
   means the graph does not go forward. Fix the graph, not the validator.
5. **Report**: `python3 -m cairn plan report graph.json`, and show it to the author before
   anything is generated — every step's task in full, every edge with the words behind it,
   everything left out with its cause, and every question needing an answer.
6. **Answer the assertions**: `python3 -m cairn plan propose graph.json` shows every step
   whose end state nothing asserts, beside the words the document gives for it and the
   command you proposed for it in pass one, and prints the
   `python3 -m cairn plan answer …` invocation that records each answer. The answers are
   the author's, never yours. A step with no command and no recorded answer never reaches
   the engine ([verify-gate.md](verify-gate.md)), and no command is ever invented for one.

The author's confirmation of that report, and their answers, are what make the graph the
plan's and not the derivation's.
