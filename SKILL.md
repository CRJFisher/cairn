---
name: cairn
description: Runs a markdown plan document as verified, concurrent coding-agent work against a git repository — author a workflow from a plan, edit it, run it, schedule it, report on a past run, or explain what one would do.
disable-model-invocation: true
---

# Cairn

A person writes a plan document. Cairn compiles it into a graph of coding-agent sessions,
runs them concurrently against a real git repository, and merges only what a declared
assertion proved. The result can be trusted without having watched it run.

**This file carries the rules that bind every capability** — dispatch, the occasion,
the target repository — and no capability's steps. Keep it in mind; the steps are in the one
document named against a capability below, read when it is selected and not before.

## The six capabilities

**Author** turns a plan document into an executable workflow. **Edit** changes what one does,
by changing its plan and authoring again. **Run** executes one and reports the result.
**Report** says what happened in a past run, triggering nothing. **Schedule** arranges a
cadence or an external trigger. **Explain** answers what a workflow would do, what a verdict
means, and why a step was excluded.

## Two rules above all others

**A scheduler reaches runs Cairn never wrote.** A cron schedule and an external webhook both
need a running scheduler, whose retry scanner re-executes every failed run on this machine
from the last day — including runs Cairn never wrote. Scheduling installs what was asked for
and says so; it asks nothing. Wanting to _watch_ a run is not scheduling.

**A report triggers nothing.** Asking what happened is always safe, takes no lock, and
starts no execution.

## Reading a request

**Say the reading before acting on it.** The first reply after `/cairn` names the capability
entered and what the subject was read as, so a person can see the skill was followed.

Read the request into **one verb class** and **one subject shape**, then take the cell. Where
you cannot land on exactly one of each, ask — the shapes that leave you unable are listed
below, and each is a question rather than a best guess. Two narrow exceptions: where two
candidate _subject shapes_ land on the same cell the ambiguity is not one, so take the cell —
shapes only, never verb classes, which are two requests even where their cells agree; and a
verb inside a quotation is the quoted text's, never the request's.

**Verb classes**, by what the request asks for rather than by its words alone. A word listed
nowhere below still lands in the class whose thing it asks for; one asking for nothing any of
them names — tidy up, see to, deal with — is no verb Cairn holds, and the request is
`no_verb`. There is no nearest class.

- `authoring` — make a workflow exist: compile, generate, turn into a workflow, set up.
- `mutating` — change what one would do: change, edit, add, drop, rename, set.
- `executing` — start a fresh run: run, execute, start, kick off, do it, do it again.
- `recovering` — continue a run that stopped: recover, resume, pick up run X. Where a run is
  named, steps named with it are that run's and the subject is the run; where none is, a step
  is a `step` and takes its own cell.
- `watching` — see something as it happens: watch, keep an eye on, where can I see it.
- `recounting` — ask what happened: how did it go, did it work, the report.
- `arranging` — ask for a cadence: schedule, nightly, cron, webhook. A cadence makes it this
  class even when the verb is "run".
- `interrogating` — ask about state or meaning, changing nothing, including in the past
  tense: what would this do, what does this word mean, why was this excluded, check it.

**Subject shapes.** `plan_document` a markdown plan, a folder of task documents, **or the
request itself where it names work and the order that work runs in**; `plan_graph` a derived
graph.json; `workflow` a plan slug or generated definition; `run` a run id or a reference to
a past execution; `step` a step id or the plan's own name for one; `verdict_word` a request
_about a word as a word_, never a word that merely occurs in a sentence about something else.

**A plan stated in the request is a `plan_document`**, whatever words it uses — "run these
four together, then these two, then the close-out" names the work, the waves and the order,
and that is a plan. It is how a plan usually arrives; a document on disk is the special case.
Authoring writes the request's own words down as the plan document first, unparaphrased, and
nothing skips the parse report.

**Qualifiers** modify how a capability proceeds and are never what a request is about: a
`repository` path, a `cadence`, and a `model`. Two objects of one shape are one subject — two
runs of one plan is Run, twice.

**The `model` qualifier.** A model stated in the request is a sentence in the plan document,
and the derivation sets each agent session's model from it — a plan-wide sentence the merge
resolver's too. The parse report shows every session's model beside the words it came from,
which answers "can I choose the model?" without leaving this file. A model the provider does
not serve is a question, never a guess.

**A condition the person put on going ahead is theirs to waive.** "All steps on sonnet 5.5.
If the model type isn't configurable, stop and tell me" is one: where it cannot be honoured
exactly, say so and stop before authoring. A near miss is never decided to meet it on their
behalf.

| verb class \ subject | `plan_document`                | `plan_graph`                   | `workflow`                     | `run`                  | `step`                    | `verdict_word`              |
| -------------------- | ------------------------------ | ------------------------------ | ------------------------------ | ---------------------- | ------------------------- | --------------------------- |
| `authoring`          | **author**                     | **author**                     | **author**                     | ask `authoring_a_run`  | ask `authoring_a_step`    | ask `verb_on_a_frozen_word` |
| `mutating`           | **edit**                       | **edit**                       | **edit**                       | ask `mutating_a_run`   | **edit**                  | ask `verb_on_a_frozen_word` |
| `executing`          | **run**                        | **run**                        | **run**                        | **run**                | ask `executing_one_step`  | ask `verb_on_a_frozen_word` |
| `recovering`         | ask `recovering_without_a_run` | ask `recovering_without_a_run` | ask `recovering_without_a_run` | **run**                | ask `recovering_one_step` | ask `verb_on_a_frozen_word` |
| `watching`           | ask `watching_a_plan`          | ask `watching_a_plan`          | ask `watching_a_workflow`      | **report**             | **report**                | ask `verb_on_a_frozen_word` |
| `recounting`         | **report**                     | **report**                     | **report**                     | **report**             | **report**                | **explain**                 |
| `arranging`          | **schedule**                   | **schedule**                   | **schedule**                   | ask `scheduling_a_run` | ask `scheduling_a_step`   | ask `verb_on_a_frozen_word` |
| `interrogating`      | **explain**                    | **explain**                    | **explain**                    | **explain**            | **explain**               | **explain**                 |

**The ask list.** Five shapes never reach the table at all, and each is a question. The
_readings_ of a request are the capabilities its cells hold: a subject's readings are its
column, a verb's are its row.

- `many_verbs` — the request reads as two different kinds of thing at once. Ask, always:
  never one reading over the other, and never even when both only read. **A stated order
  does not resolve it**: "and then" names two pieces of work, and doing the first before
  going on to the second is the whole failure. Doing only the harmless half and stopping is
  the same failure: neither piece is performed — not even one that only reads — until the
  question is answered.
- `no_verb` — a subject named with nothing asked of it. Ask, unless its whole column
  collapses to a single reading that only reads, in which case answer.
- `many_subjects` — more than one _kind_ of thing to act on, each a separate piece of work.
- `no_subject` — a verb with nothing to apply it to. A pronoun takes the shape of the thing
  most recently acted on; where that was a past execution the shape is `run`, not `workflow`.
  A subject the sentence lacks is never supplied by there being only one candidate in the
  world.
- `nothing_recognised` — no plan, workflow, run or Cairn word in the request at all.

A run verb over a past execution — "do that one again" — is a fresh run of the plan that
execution ran; continuing the execution is `recovering`. A bare workflow name is `no_verb`,
whose column includes Run, so it is asked.

## A new occasion, or the one being recovered

An **occasion** is the freshness key a run-scoped step reads, and it is never a question: a
recovery continues the one it is recovering, everything else mints a new one, and a scheduled
firing always mints. What is open is whether to _say so_ — where the plan has run here before,
the start states the reading it took and what the other would have meant, because a new
occasion redoes every scoped step while a continued one may act on work whose answer has
moved. A plan's first run has no other reading available, so stating one there is noise.

## The target repository

It comes from the request, for every capability, always. It is never inferred from the
workflow and never defaulted to the directory this conversation is in — Report needs one to
find a run just as Run needs one to start one, and an unnamed repository is asked for rather
than assumed.

A generated definition is bound to the repository it was authored for. A plan with no
definition anywhere is authored in the repository named. A definition that exists for the
named workflow makes a differing repository the encoded-or-re-author question: ask whether
to run against the encoded one or re-author for the named one — authoring where none exists
never answers it unasked. Never reconcile the two.

## Where each procedure lives

| Read this                                                | For                        |
| -------------------------------------------------------- | -------------------------- |
| [capabilities/authoring.md](capabilities/authoring.md)   | **Author** and **Edit**    |
| [capabilities/running.md](capabilities/running.md)       | **Run**                    |
| [capabilities/scheduling.md](capabilities/scheduling.md) | **Schedule**               |
| [capabilities/reading.md](capabilities/reading.md)       | **Report** and **Explain** |

Each states its entry preconditions and what it is bound to on entry. Read one, not four —
the single exception is that running a plan with no definition yet is authoring first, and
that document says so where it arises. Every command in them is `python3 -m cairn …`, run
from the directory this file is in. They link into `docs/` for the contracts behind them;
follow those only when sent.

## The engine, and where it is the better answer

Cairn generates workflows for **Dagu**, an external DAG engine, and invokes it as a
subprocess. Its view draws a running graph — the topology, each step's state as it changes,
logs, timings, the halted node — better than a conversation can, and reads a finished run
identically to a live one. Run and Report link to it rather than describing what it drew.

Its command line is the one you already have for your other jobs, and the README says which
of its verbs are worth pointing at. Cairn refuses exactly one: never `dagu retry`, because
re-running a plan is the whole recovery story and a continued occasion is what makes it cheap.

Two things it cannot answer are Cairn's: **divergence**, a workflow no longer matching the
plan that generated it; and the **verdict**, because a run that dropped a branch reports a
clean success at the engine level.
Say which is which rather than leaving someone to find the gap. A run started at the view
records the username that started it and a run Cairn started records no actor, so Report
accounts for runs the skill did not start.
