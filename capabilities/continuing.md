# Continuing: the work a run found becomes the next plan

| Contract       | Value                                                                                                                                           |
| -------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| Capability     | `continue`                                                                                                                                      |
| Entered when   | the dispatch table selected **continue**                                                                                                        |
| Preconditions  | a run of this repository has settled and has a record; the repository resolved and its provenance known                                         |
| Bound on entry | `capability` · `repository` · `run`                                                                                                             |
| Owns           | the harvest of one run's follow-up work, the drafted plan document, and the sentence that closes a sequence                                     |
| Defers to      | [authoring.md](authoring.md) · [running.md](running.md) · [scheduling.md](scheduling.md) · [../docs/plan-contract.md](../docs/plan-contract.md) |
| Triggers       | a plan document in the repository's own admin directory                                                                                         |

Every step report carries the work that session found and did not do, and the run's record
keeps it beside the step that surfaced it. This is what becomes of it: one settled run's
follow-up work, written down as an ordinary plan document that a person then authors and runs
like any other. **Nothing here runs anything, and nothing here is a step.** A drafted step is
asserted by the same conversation a hand-written one is, and the sequence of rounds ends when
a run's harvest comes back empty.

## The procedure

1. **Settle the repository, and say which one it is.** `python3 -m cairn explain repository
--session <the directory this conversation is in>` answers it and says which candidate did
   — a repository named in the request, the one holding its subjects, or the session's own —
   and asks instead where they leave real doubt ([../SKILL.md](../SKILL.md)). Repeat that line
   first in your reply: the run's record and the drafted document both live inside it.

2. **Name the run.** The harvest is one run's, so the request has to name one. Where it names
   a plan instead of a run, `python3 -m cairn record latest --plan <slug> --repository <path>`
   says which run a person means by "the last one" — put that run to them before harvesting
   it, because an older run's follow-ups are a different list.

3. **Draft the plan.** `python3 -m cairn plan draft --from-run <run-id> --repository <path>`
   reads that run's record, collects every step's follow-up work with the step that surfaced
   it, and writes the plan document to `<git-common-dir>/cairn/plans/<plan>-after-<run>.md`,
   printing the path. It transcribes and interprets nothing: each item becomes one step's
   heading with the item's own words beneath it.

   It refuses two states by naming them. A run **still running** has steps that have not
   reported, so its follow-up work is a partial list that reads like a whole one; wait for
   the run to settle. A run with **no record** cannot be harvested at all —
   `python3 -m cairn record build --run <run-id> --repository <path>` makes one where the
   run exists.

4. **Where it says the harvest is empty, the sequence is over.** `left no follow-up work: the
sequence is closed` is the whole answer: nothing was written, and there is no next round.
   Say so and stop. Never invent work to keep a sequence going.

5. **Show the person what was harvested, and whose words each item is.** Read the drafted
   document back to them with the step that surfaced each item. The items are an agent's
   account of what it found, which carries no authority over what runs — this is the turn
   where a person drops what they do not want before any of it becomes a step.

6. **Then author it like any other plan.** The draft is a plan document and nothing more, so
   it goes through [authoring.md](authoring.md) from its first step: the derivation, the
   validator, the parse report, and an assertion for every step. Runtime-discovered work
   arrives with no checkable end state, so **expect a `missing_verify` question on every
   step** — each answer is the person's, and generation refuses while one is open.

7. **Then run it, or stop.** A follow-on plan is an ordinary plan: its own slug, its own
   workflow, its own record, and the repository's one-run lock serialising it behind anything
   still landing. Where the request asked for the run too, the procedure is
   [running.md](running.md)'s; where it did not, stop at the authored workflow.

## A round bound belongs to any schedule

A follow-on plan can leave follow-up work of its own, so rounds can continue indefinitely.
Where the request asks for the sequence to be arranged rather than run once, say how many
rounds it is to run for before anything is installed — a sequence that cannot say when it
stops is one nobody can reason about — and the cadence itself is
[scheduling.md](scheduling.md)'s.

## What this capability never does

- **Never turn a follow-up into a step of the run that reported it.** The work was discovered
  mid-run, no authored assertion exists for it, and a step with no assertion is refused at
  emission. It becomes a step of the next plan, where a person asserts it.
- **Never write an assertion, a dependency or a restated task into the draft.** The document
  carries the items and their provenance; every reading of them is the author's.
- **Never harvest a run to decide anything about it.** A report's follow-up work lowers no
  outcome and raises none ([../docs/step-protocol.md](../docs/step-protocol.md)).
