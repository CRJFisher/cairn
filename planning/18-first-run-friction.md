# 18 — First-run friction: the defaults a first run dies on

The first person to drive Cairn without having built it hit two walls, and neither is a bug in
anything the invariants promise. Both are **defaults that make the person do work the tool could
have offered to do** — one before a run can be described at all, one before a workflow can be
generated. This document owns those two and is the place the rest of the small ones land as they
surface.

**Serves** the capability surface of **Run** and **Author**. No invariant moves: consent stays a
stated price and a qualifying yes ([15](15-the-skill.md)), and a verdict stays something a
declared assertion proved ([08](08-verify-gate.md)).

## What a person hit, in the order they hit it

1. **Cairn asked which repository to run against**, from a conversation already sitting in one.
   The answer was obvious to the person and unavailable to the tool by rule.
2. **Every step came back with `verify: null`**, so generation refused, so there was nothing to
   run. The worksheet that resolves it offered a candidate for almost none of them.

Both read as the tool declining to participate. The second is the more expensive: it is the point
where a plan that parsed correctly still produces nothing executable.

## A — The target repository: infer it, and say so in the offer

**Today.** `SKILL.md` binds every capability to a repository that came from the request, and
`resolve_repository` has no parameter for a working directory — _"not as a rule but as an absence,
so a caller cannot supply one"_. An unnamed repository is a question, for Report and Explain as
much as for Run.

**Why the absence is there, and stays there in part.** Three things derive from that path and all
three fail quietly when it is wrong: the run lock (I6), the worktrees parent `<repo>-worktrees`
that a mis-spelling sends every isolated step to while the wave lands nothing and reports success,
and the definition's own encoded repository, whose runs directory does not move with the
parameter. None of that is softened here.

**Why the strict form is nonetheless too wide.** Two of the four capabilities it binds — Report
and Explain — take no lock, spend nothing, and write nothing. `SKILL.md` justifies them by
symmetry, _"Report needs one to find a run just as Run needs one to start one"_, and symmetry is
not a hazard: a wrong guess there prints an empty run list. And for Run the extra turn buys
nothing that the offer does not already buy, because no run starts without a stated price and a
yes that names the action.

**The change: inference is surfaced, never silent.**

- Resolve a **default** in this order: the git root containing the plan document, then the
  session's own working directory. Both are candidates, neither is an answer.
- **Run and Schedule** put the resolved repository _in the offer_, beside the price, in the same
  sentence that mints the token. The yes that accepts the price accepts the repository. There is
  no path to a run against a repository the person was not shown.
- **Report and Explain** default silently to the same resolution and **name what they read** in
  the answer's first line, so a report over the wrong repository is legible as one.
- The session's directory is threaded from the skill layer. It is **never** `os.getcwd()` — the
  capability documents run `python3 -m cairn` from the skill's own directory, so the process CWD
  is Cairn's checkout and not the person's project. That confusion is what makes a naive default
  worse than none.

**What must not change.** The repository is still never inferred _from the workflow_; the
mismatch between a named repository and a definition's encoded one is still a question with two
answers and no third; the absolute-path refusal and the worktrees-spelling refusal still run
before anything is offered.

**Touches.** `cairn/skill/resolve.py` (a stated-or-defaulted resolution returning its provenance),
`SKILL.md`'s _target repository_ section, the four capability documents, the `repository` family
in `fixtures/invocations/cases.json`, `tests/test_the_skill.py`.

## B — Every step arrives unasserted, and generation is a wall

**Today, exactly.** The derivation sets `verify` to `null` wherever the document gives no command
and raises a `missing_verify` question — by rule, and the rule says _never synthesise a command_.
`cairn plan propose` then offers a candidate under **one** extraction rule: a backticked
path-like token in the acceptance line or the task becomes `test -e <path>`. Everything else
prints _"Nothing in those words names a checkable artefact."_ At emission, `_refuse_unasserted`
turns the leftovers into a hard error.

**The measurement behind that rule.** Over the corpus, zero of the two real plans' eight steps
named a checkable end state; three candidates were offered across those eight and **not one
survived contact with its author** — one named a file a later step writes, one named that same
file for a step whose end state is a line in `settings.json`, one asserted the presence of a file
the step exists to absorb. Doc 08 draws the correct conclusion for a candidate that is _silently
adopted_, and the wrong one for a candidate that is _shown_.

**The reconciliation.** What the rule protects is the report: a command Cairn invented, that
passes trivially, reading as verified. That harm requires the invention to _become the assertion
without a human_. It does not follow that Cairn may not **suggest**. So:

- **Widen the candidate surface, keep the decision where it is.** `candidate()` returns a ranked
  list of alternatives rather than one string or nothing, each carrying the words or the signal it
  came from. Sources worth drawing on: an artefact named in the acceptance line whether or not it
  is backticked; a test or build invocation the repository already has; the presence of a named
  symbol or line in a named file; the absence of a thing the step exists to remove — the inverse
  case that produced one of the three wrong offers, and the one a shape-aware rule can get right.
- **A step's own command is never its assertion.** A `command` step that ran is not a `command`
  step that worked.
- **An offer that cannot fail is refused at the offer**, not only when answered. `cannot_fail`
  already exists and currently guards the answer alone.
- **Where nothing fits, ask rather than print.** Say plainly that no candidate was drawn, name the
  shapes that would fit this step's end state, and ask the person before the graph is finalised.
  This is a conversation turn in the authoring capability, not a worksheet a person may skip:
  authoring does not proceed to generation with an unanswered step.
- **Record the provenance on every answer.** Keep the accepted / edited / authored / declined
  tally, and add which candidate rule produced the offer, so the question _does the wider
  inference carry its weight_ stays answerable rather than becoming a matter of taste.

**What must not change.** The answer is the author's. A command that cannot fail is refused. An
unverified step is honest only when someone declined a proposal and gave a reason. A step nobody
was asked about still never reaches the engine — the emitter's refusal stays exactly as it is,
because it is the last place the question can be put to a person.

**Touches.** `cairn/plan/assertions.py` (candidate ranking and provenance), `cairn/plan/cli.py`
(`propose` and `answer` shapes), `docs/plan-derivation.md`'s never-synthesise clause — restated as
never _adopt_ — `docs/verify-gate.md`'s conversation section, `capabilities/authoring.md` step 4,
`tests/test_verify_gate.py`.

## The measurement this owes

Re-run doc 08's own tally over the same corpus after the change: candidates offered per step,
accepted versus edited, and how many steps still draw nothing. **The success condition is offers
surviving contact, not more offers.** If accepted stays at zero, the wider inference is noise and
the mandatory ask is the whole of the feature — which is a fine result, recorded rather than
argued.

## The bucket

Small first-contact defects, appended as they surface, with how they were found.

| #   | Symptom                                                              | Where it lives                                        | State |
| --- | -------------------------------------------------------------------- | ----------------------------------------------------- | ----- |
| A   | The repository is asked for from inside the repository               | `cairn/skill/resolve.py`, `SKILL.md`                  | open  |
| B   | Every step is `verify: null`, so generation refuses and nothing runs | `cairn/plan/assertions.py`, `docs/plan-derivation.md` | open  |

The walls a person hits _after_ a workflow exists — a slug the engine's name limit refuses, a
`run start` that blocks for the whole run, a socket a sandboxed shell cannot bind — are
[19](19-start-friction.md)'s.

## Acceptance

- A first run in a git repository, over a plan in that repository, reaches a priced offer naming
  that repository without the person having typed a path.
- A report in that repository finds its runs and names the repository it read.
- A named repository that disagrees with a definition's encoded one is still a question.
- A plan whose steps name no commands reaches a generated workflow through one authoring
  conversation, with every assertion either the author's own words or a proposal they accepted or
  edited — and none adopted unshown.
- No step reaches the engine unasserted, and no offered command can pass trivially.
- The corpus tally is re-recorded in [08](08-verify-gate.md) beside the original three numbers.
