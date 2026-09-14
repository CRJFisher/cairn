# 26 — A reviewed plan is the plan that gets published

The authoring conversation can report questions it has no way to close, workflow generation
does not re-check the source pins shown during review, and every plan is directed to one
`graph.json`. A graph can therefore be reviewed, become stale, retain unresolved interpretation,
and still replace an executable workflow.

**Serves** **Author**, **Edit**, and recovery entry into **Run**. The invariant is that the
published workflow is derived from the reviewed source and from no unresolved reading.

## A — Give every question a durable answer

The schema defines six question kinds, but `plan answer` resolves only `missing_verify`.
The others are warnings, and generation blocks only an unasserted step. In particular,
`plan_gated`, `ambiguous_dependency`, `unresolved_reference`, and `non_convergent_task` can
remain open while publication proceeds.

- Define typed resolution records for every question kind: accepted reading, edited reading, or
  explicit decline/waiver where the contract permits one.
- Make the answer update the graph fact the question concerns, not merely delete the question.
- Refuse publication while any question lacks a valid recorded resolution.
- Keep the assertion rule: no suggested command is adopted without the author's answer.
- Make `plan propose --json` distinguish “a valid list containing work” from command failure;
  this is the open item carried from [19](19-start-friction.md).

## B — Re-check source provenance at publication

Source-aware validation exists, but `workflow author` calls validation without `source_root`.
A plan document can change after the parse report was confirmed and the stale graph will still
generate. Source paths can also be absolute, contain `..`, or escape through a symlink while
still being accepted as pins beneath the stated root.

- Require a canonical source root at the workflow-authoring boundary.
- Resolve every pinned source beneath that root and reject absolute, traversal, and symlink
  escapes.
- Recompute every digest and evidence quotation in the same invocation that publishes the
  workflow.
- Bind the source-root identity and graph digest into the publication receipt.
- A stale source refuses before an existing workflow or stamp is replaced.

## C — Preserve one editable graph per plan

The authoring procedure currently sends every plan to `<repository>/.git/cairn/graph.json`,
while workflows, offers, and records are plan-scoped. Authoring a second plan overwrites the
first plan's reviewed graph.

Store graphs under a plan-scoped namespace such as
`<git-common-dir>/cairn/graphs/<plan>.json`. Migration must identify an existing singleton graph
by its own plan slug, refuse ambiguity, and never silently assign it to the plan being authored.

## D — Make the schema discriminator and scalar contract explicit

An omitted `cairn_graph_version` is defaulted to the current version, silently upgrading an
unknown document. Require the version at input boundaries. Any legacy migration is an explicit
reader with an explicit source version, never normalisation into “current”.

Apply the finite numeric rules from [25](25-execution-admission-and-paid-bounds.md) to plan
budgets, timeouts, and retries before normalisation.

## E — Bind recovery to the run being recovered

Recovery reads the named run's occasion but prices and executes the separately supplied plan.
It does not require the record's plan to match. Derive the plan from the recovered record or
refuse any mismatch before minting an offer. The repository, plan, workflow digest, and occasion
must describe one lineage.

## Acceptance

- Every question kind has a tested answer path, and no unresolved question reaches publication.
- Changing, moving outside the root, or escaping a pinned source after review causes authoring to
  refuse without replacing the existing workflow.
- Two plans retain independently addressable reviewed graphs and can be re-authored in either
  order.
- An unversioned graph is refused or passed through a named legacy migration; it is never treated
  silently as the current schema.
- Recovering run A through plan B refuses before an offer exists.

## Touches

`cairn/plan/schema.py`, `cairn/plan/validate.py`, `cairn/plan/assertions.py`,
`cairn/plan/cli.py`, `cairn/workflow/cli.py`, `cairn/workflow/stamp.py`,
`cairn/skill/cli.py`, `cairn/skill/resolve.py`, authoring and plan-contract documentation,
fixture graphs, and their tests.
