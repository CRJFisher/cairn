# 30 — A plan stated in the request reaches a capability

`/cairn` was invoked with a plan written into the request itself: which tasks run together,
in what order, and on which model. The skill loaded, and the agent then had nowhere to go. No
row of the dispatch table held the request, so it never entered a capability document. It
searched the checkout instead, grepping the README, `docs/` and the Python source for the word
"model". To the person this looked like the skill had not run at all. It had run, but it had
no route for the most ordinary shape a run request takes.

**Serves** **Author** and **Run**: a person can hand Cairn a plan in a sentence and get it
run, and can choose the model that does the work without knowing where that setting lives. No
invariant moves. The plan document stays the source of truth (I1), and a step's model is still
set only where the plan's own words state it ([plan-derivation](../docs/plan-derivation.md)).
Cost is out of scope here because [32](32-no-cost.md) removes it.

## What a person hit

The request, in the ariadne repository, quoted whole:

> Run TASK-376.24, TASK-376.21, TASK-376.22 and TASK-398 together. Then run TASK-376.27 and
> TASK-397 together. By then TASK-376.21 has landed, and these two touch different files.
> Finally, do the TASK-376 close-out measurement and tick the remaining criteria.
> All steps should use sonnet 5.5. If the model type isn't configurable, stop and tell me

What the session did, in order:

1. It loaded `SKILL.md` and never stated a reading: no verb class, no subject shape, no cell.
2. It spent five tool calls searching `README.md`, `capabilities/`, `docs/` and then
   `cairn/plan/schema.py` for "model", to find out whether a step's model can be set.
   `SKILL.md` says nothing about it, and the docs that do say are ones it is told to follow
   "only when sent".
3. It found `MERGE_MODEL = AGENT_MODEL` and decided that a merge resolver running on the
   `sonnet` alias "should be 5.5 today". It counted that as meeting an instruction that said to
   stop if the model could not be set.
4. It invented a procedure no document holds: "I write your three stages as a plan". Then it
   asked which repository to use. That question is [31](31-the-repository-the-session-is-in.md)'s.

Steps 2 and 4 are one gap seen from two sides, and step 3 is a separate fault.

## A — A plan stated in the request is a plan document the skill writes down

**Today.** The subject shapes allow `plan_document` only as "a markdown plan or folder of task
documents". Authoring's precondition is "a plan document, folder or graph exists on disk".
Derivation quotes every edge, command and bound verbatim from the documents, and
`--source-root` re-checks each quotation. A request is none of these things. It names tasks
scattered among hundreds of others in one backlog, adds two that belong to no epic, and states
the dependencies itself ("By then TASK-376.21 has landed"). Strictly it is a `no_subject`
question, and nothing tells the agent how to answer one. So the agent improvised.

**Why it matters more than one session.** This is how a plan usually arrives: a few sentences
that sequence task documents the repository already has. An epic document that happens to fit
is the special case.

**The change.**

- `SKILL.md` names the shape. A request that names work and the order it runs in is a
  `plan_document`, whatever words it uses. The dispatch table then holds it: `executing` over
  `plan_document` is **run**, and running a plan with no definition is authoring first, which
  `running.md` already says.
- `authoring.md` gains a step zero for this shape. The agent writes the request's own words
  into a plan document, verbatim and unparaphrased, beside a link to each task document it
  names. Derivation then quotes from that document like any other. The person confirms the
  parse report as usual, and that confirmation is what makes the transcription theirs.
- The document lives outside the working tree, for the reason the graph does
  (`<git-common-dir>/cairn/plans/<slug>.md`): a run refuses to start over a dirty tree.
- **Open, and settled before building:** whether `--source-root` admits quoting from the task
  documents the request document links to, or only from the request document. A task's
  `verify` command usually lives in its own document, so the answer decides whether the
  assertion conversation can propose anything.
- The first reply after `/cairn` names the capability it entered and what it read the subject
  as. A person can then see the skill was followed, and see when it was not.

**What must not change.** The request's words are never paraphrased into the document.
Dependencies are still read from the words and never defaulted to sequential. A plan stated
in the request gets no shortcut past the parse report.

**Touches.** `SKILL.md` (_Subject shapes_), `capabilities/authoring.md` (preconditions and
step zero), `docs/plan-derivation.md` (the request document as a source), `cairn/plan/home.py`, `fixtures/invocations/cases.json` (a `canonical` case built from the request
above), `tests/test_the_skill.py`.

## B — The model is a qualifier `SKILL.md` names

**Today.** `SKILL.md` lists two qualifiers, a `repository` and a `cadence`. A step's
`model` is spelled out only in
[plan-derivation](../docs/plan-derivation.md) and [plan-contract](../docs/plan-contract.md):
"pin it to the opus model" becomes a model of `opus`. An agent holding a request that names a
model, or makes the model a condition of going ahead, has nothing in the file it was handed.
So it reads the source.

**The change.**

- `SKILL.md` adds `model` as a third qualifier. It is one paragraph. A model stated in the
  request is a sentence in the plan document. Derivation sets each agent step's model from
  that sentence, and the parse report shows every agent session with its model. That is
  enough to answer "can I set the model?" without leaving the file.
- Derivation settles how the person's words for a model become the value written into the
  emitted `--model`, and the parse report shows that value beside the sentence it came from.
  "sonnet 5.5" must not quietly become `claude-sonnet-5-5` or `sonnet` without the person
  seeing which. A name the provider does not serve is a question, never a guess.

**Touches.** `SKILL.md` (_Qualifiers_), `docs/plan-derivation.md` (model names),
`cairn/plan/report.py` (the bound beside its sentence), `fixtures/invocations/cases.json` (a
`model` qualifier case), `tests/test_the_skill.py`.

## C — A plan-wide model binds the merge resolver, and a stop condition is never reinterpreted

**Today.** [merge-step](../docs/merge-step.md) says "the resolver is the plan's own default
agent", but its model is the constant `MERGE_MODEL = AGENT_MODEL`, and no plan can set it. The
person asked for every step on Sonnet 5.5 and said to stop if that was impossible. The agent
judged the alias close enough and went on.

**The change.**

- A plan-wide model sentence sets the resolver's model as well. The resolver is the plan's
  default agent, so it takes the plan's default rather than the engine's. The parse report
  shows the resolver's model beside the steps' models, so the person sees it was asked for
  and is honoured.
- `SKILL.md` states that a condition the person put on going ahead is theirs to waive. Where a
  model cannot be honoured exactly, the skill says so and stops. It never decides on the
  person's behalf that a near miss meets the condition.

**Touches.** `cairn/plan/schema.py` (`MERGE_MODEL` derives from the plan's default),
`cairn/emitters.py` and `cairn/merge.py` (the resolver slot), `docs/merge-step.md`, `SKILL.md` (_Three
rules_ or _Reading a request_), `tests/test_workflow.py`.

## Acceptance

- The request above, run in the ariadne repository, enters **run** and then
  `capabilities/authoring.md` in its first turn. The session reads nothing in Cairn's checkout
  outside `SKILL.md`, the capability document and the `docs/` pages that document sends it to.
- The first reply names the capability entered and the subject it read.
- The request becomes a plan document holding its words verbatim. Its parse report shows two
  waves and a final step, with the "By then TASK-376.21 has landed" edge quoted, and every
  agent session, the resolver included, pinned to the model value the person confirmed.
- A request whose model cannot be honoured exactly stops and says so before any authoring.
- The new `canonical` and `model` fixture cases pass, and every existing dispatch case
  still passes.

## Close-out

Done. Every criterion above holds, and all three sections — A, B and C — are built.

A person can now hand Cairn a plan in a sentence. A request that names work and the order it
runs in is a `plan_document`, which `SKILL.md` says in the subject shapes themselves, so the
table holds it: `executing` over `plan_document` is **run**, and running a plan with no
definition is authoring first. Authoring's step zero writes the request's own words into
`<git-common-dir>/cairn/plans/<slug>.md` — printed by `python3 -m cairn plan home <slug>
--repository <path> --document`, outside the working tree a run refuses to start over — beside
a link to each task document the request names. From there it is a plan document like any
other: the dependencies are read from the request's own sentences and quoted verbatim, and the
person confirms the parse report before anything is generated. The first reply after `/cairn`
names the capability entered and what the subject was read as.

A person can also choose the model without knowing where the setting lives. `model` is a third
qualifier in `SKILL.md`, beside `repository` and `cadence`. A model the plan's words state is
`plan.default_model`, the sentence it was read from is `plan.model_evidence`, and
`--source-root` rechecks that quotation like an edge's — a model resting on words no document
holds is refused, and a model stated with nothing quoted for it is `unquoted_model`. Every
agent session takes that value, **the merge resolver included**: `MERGE_MODEL` is gone, the
topology carries the plan's model into each merge slot, and `cairn merge land` has no model of
its own to fall back on. The parse report's Models section shows the value beside the sentence,
names the resolver, and names any step that departs from the plan's; a step that departs is a
`derived_model` warning, as a non-default `verify_timeout` already was.

A condition a person puts on going ahead is theirs to waive. Where a model cannot be honoured
exactly, the skill says so and stops — before the plan document is written and before anything
is derived — rather than deciding on their behalf that a near miss meets the condition.

**The open question, settled.** `--source-root` admits the task documents the request document
links to, pinned in `plan.sources` like any other document read, with the repository that holds
them all as the source root. The alternative was forced: a step's `verify` must appear in a
pinned source or it is `invented_verify`, so quoting only the request document would leave
every step unasserted and the assertion conversation with nothing to propose — the opposite of
what a plan sequencing existing task documents is for.

**What proves it.** `fixtures/plans/stated-in-the-request/` is the request above, written down
whole, with the seven documents it names beside it. Its parse report shows 7 steps in 3 waves,
the "By then TASK-376.21 has landed" edge in the person's own words, and every session — the
resolver included — on the model value they confirmed. `fixtures/invocations/cases.json` holds
the request as a `canonical` Run case and a second case carrying the `model` qualifier, and
`SKILL.md` is now held to naming every qualifier it has.
