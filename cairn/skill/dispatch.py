"""Which capability a request reaches, and where it is asked back instead.

One table, keyed on a verb class and a subject shape, and a resolution that consults it only
when exactly one of each is present. Everything else asks. That is the whole of doc 15's
"where none applies, or more than one does, ask — never resolve to the more likely reading",
and it is a property of the shape of this module rather than of the care taken writing it:

- **Disjoint by construction.** The rules are a mapping, so a pair keys at most one entry.
  There is no rule list to scan, no first-match, and no score to break a tie with.
- **Total by assertion.** Every one of the 48 cells is present, so a missing pair cannot
  become an implicit ask, which would be a default by another name.
- **Every ask's readings come from the same table**, computed rather than restated, so the
  ask list cannot drift from the rules it is the complement of.

`SKILL.md` renders this table in prose and a model applies it. What a model supplies is the
step no table can — reading an utterance into a verb class and an object shape. What it is
spared is holding 48 cells in its head consistently, and a test proves the cells it is
holding are these.

This module deliberately imports nothing that can start, lock or write anything, and a test
asserts it: a classification that cannot reach an authorisation cannot mint one, however
wrong it is about a sentence.
"""

from __future__ import annotations

from typing import NamedTuple

from cairn.skill.vocabulary import (
    ASK_FAMILIES,
    CAPABILITY_AUTHOR,
    CAPABILITY_EDIT,
    CAPABILITY_EXPLAIN,
    CAPABILITY_ORDER,
    CAPABILITY_REPORT,
    CAPABILITY_RUN,
    CAPABILITY_SCHEDULE,
    FAMILY_HARMLESS_CHOICE,
    FAMILY_NOTHING_APPLIES,
    FAMILY_OBJECT_UNCLEAR,
    FAMILY_VERB_UNCLEAR,
    HEADLINE_COST,
    HEADLINE_DAEMON_COST,
    SHAPE_PLAN_DOCUMENT,
    SHAPE_PLAN_GRAPH,
    SHAPE_RUN,
    SHAPE_STEP,
    SHAPE_VERDICT_WORD,
    SHAPE_WORKFLOW,
    SUBJECT_SHAPES,
    VERB_ARRANGING,
    VERB_AUTHORING,
    VERB_CLASSES,
    VERB_EXECUTING,
    VERB_INTERROGATING,
    VERB_MUTATING,
    VERB_RECOUNTING,
    VERB_RECOVERING,
    VERB_WATCHING,
    WRITES_NOTHING,
)

ASK_AUTHORING_A_RUN = "authoring_a_run"
ASK_AUTHORING_A_STEP = "authoring_a_step"
ASK_MUTATING_A_RUN = "mutating_a_run"
ASK_EXECUTING_A_PAST_RUN = "executing_a_past_run"
ASK_EXECUTING_ONE_STEP = "executing_one_step"
ASK_RECOVERING_WITHOUT_A_RUN = "recovering_without_a_run"
ASK_RECOVERING_ONE_STEP = "recovering_one_step"
ASK_WATCHING_A_PLAN = "watching_a_plan"
ASK_WATCHING_A_WORKFLOW = "watching_a_workflow"
ASK_SCHEDULING_A_RUN = "scheduling_a_run"
ASK_SCHEDULING_A_STEP = "scheduling_a_step"
ASK_VERB_ON_A_FROZEN_WORD = "verb_on_a_frozen_word"

ASK_NO_VERB = "no_verb"
ASK_MANY_VERBS = "many_verbs"
ASK_NO_SUBJECT = "no_subject"
ASK_MANY_SUBJECTS = "many_subjects"
ASK_NOTHING_RECOGNISED = "nothing_recognised"

# The twelve above are cells of the table; the five below are the shapes an invocation can
# have that the table cannot be consulted for at all. Together they are the explicit ask
# list doc 15 asks for, and a test requires every one of them to be reachable and to be
# stated in `SKILL.md`.
ASK_REASONS: tuple[str, ...] = (
    ASK_AUTHORING_A_RUN,
    ASK_AUTHORING_A_STEP,
    ASK_MUTATING_A_RUN,
    ASK_EXECUTING_A_PAST_RUN,
    ASK_EXECUTING_ONE_STEP,
    ASK_RECOVERING_WITHOUT_A_RUN,
    ASK_RECOVERING_ONE_STEP,
    ASK_WATCHING_A_PLAN,
    ASK_WATCHING_A_WORKFLOW,
    ASK_SCHEDULING_A_RUN,
    ASK_SCHEDULING_A_STEP,
    ASK_VERB_ON_A_FROZEN_WORD,
    ASK_NO_VERB,
    ASK_MANY_VERBS,
    ASK_NO_SUBJECT,
    ASK_MANY_SUBJECTS,
    ASK_NOTHING_RECOGNISED,
)


DISPATCH_RULES: dict[tuple[str, str], str] = {
    (VERB_AUTHORING, SHAPE_PLAN_DOCUMENT): CAPABILITY_AUTHOR,
    (VERB_AUTHORING, SHAPE_PLAN_GRAPH): CAPABILITY_AUTHOR,
    # Re-authoring is how a workflow changes under I1, and the generator states what it is
    # replacing and refuses to merge, so this is Author rather than an ask.
    (VERB_AUTHORING, SHAPE_WORKFLOW): CAPABILITY_AUTHOR,
    (VERB_AUTHORING, SHAPE_RUN): ASK_AUTHORING_A_RUN,
    (VERB_AUTHORING, SHAPE_STEP): ASK_AUTHORING_A_STEP,
    (VERB_AUTHORING, SHAPE_VERDICT_WORD): ASK_VERB_ON_A_FROZEN_WORD,
    (VERB_MUTATING, SHAPE_PLAN_DOCUMENT): CAPABILITY_EDIT,
    (VERB_MUTATING, SHAPE_PLAN_GRAPH): CAPABILITY_EDIT,
    (VERB_MUTATING, SHAPE_WORKFLOW): CAPABILITY_EDIT,
    (VERB_MUTATING, SHAPE_RUN): ASK_MUTATING_A_RUN,
    (VERB_MUTATING, SHAPE_STEP): CAPABILITY_EDIT,
    (VERB_MUTATING, SHAPE_VERDICT_WORD): ASK_VERB_ON_A_FROZEN_WORD,
    (VERB_EXECUTING, SHAPE_PLAN_DOCUMENT): CAPABILITY_RUN,
    (VERB_EXECUTING, SHAPE_PLAN_GRAPH): CAPABILITY_RUN,
    (VERB_EXECUTING, SHAPE_WORKFLOW): CAPABILITY_RUN,
    (VERB_EXECUTING, SHAPE_RUN): ASK_EXECUTING_A_PAST_RUN,
    (VERB_EXECUTING, SHAPE_STEP): ASK_EXECUTING_ONE_STEP,
    (VERB_EXECUTING, SHAPE_VERDICT_WORD): ASK_VERB_ON_A_FROZEN_WORD,
    (VERB_RECOVERING, SHAPE_PLAN_DOCUMENT): ASK_RECOVERING_WITHOUT_A_RUN,
    (VERB_RECOVERING, SHAPE_PLAN_GRAPH): ASK_RECOVERING_WITHOUT_A_RUN,
    (VERB_RECOVERING, SHAPE_WORKFLOW): ASK_RECOVERING_WITHOUT_A_RUN,
    (VERB_RECOVERING, SHAPE_RUN): CAPABILITY_RUN,
    (VERB_RECOVERING, SHAPE_STEP): ASK_RECOVERING_ONE_STEP,
    (VERB_RECOVERING, SHAPE_VERDICT_WORD): ASK_VERB_ON_A_FROZEN_WORD,
    (VERB_WATCHING, SHAPE_PLAN_DOCUMENT): ASK_WATCHING_A_PLAN,
    (VERB_WATCHING, SHAPE_PLAN_GRAPH): ASK_WATCHING_A_PLAN,
    (VERB_WATCHING, SHAPE_WORKFLOW): ASK_WATCHING_A_WORKFLOW,
    (VERB_WATCHING, SHAPE_RUN): CAPABILITY_REPORT,
    (VERB_WATCHING, SHAPE_STEP): CAPABILITY_REPORT,
    (VERB_WATCHING, SHAPE_VERDICT_WORD): ASK_VERB_ON_A_FROZEN_WORD,
    (VERB_RECOUNTING, SHAPE_PLAN_DOCUMENT): CAPABILITY_REPORT,
    (VERB_RECOUNTING, SHAPE_PLAN_GRAPH): CAPABILITY_REPORT,
    (VERB_RECOUNTING, SHAPE_WORKFLOW): CAPABILITY_REPORT,
    (VERB_RECOUNTING, SHAPE_RUN): CAPABILITY_REPORT,
    (VERB_RECOUNTING, SHAPE_STEP): CAPABILITY_REPORT,
    (VERB_RECOUNTING, SHAPE_VERDICT_WORD): CAPABILITY_EXPLAIN,
    (VERB_ARRANGING, SHAPE_PLAN_DOCUMENT): CAPABILITY_SCHEDULE,
    (VERB_ARRANGING, SHAPE_PLAN_GRAPH): CAPABILITY_SCHEDULE,
    (VERB_ARRANGING, SHAPE_WORKFLOW): CAPABILITY_SCHEDULE,
    (VERB_ARRANGING, SHAPE_RUN): ASK_SCHEDULING_A_RUN,
    (VERB_ARRANGING, SHAPE_STEP): ASK_SCHEDULING_A_STEP,
    (VERB_ARRANGING, SHAPE_VERDICT_WORD): ASK_VERB_ON_A_FROZEN_WORD,
    (VERB_INTERROGATING, SHAPE_PLAN_DOCUMENT): CAPABILITY_EXPLAIN,
    (VERB_INTERROGATING, SHAPE_PLAN_GRAPH): CAPABILITY_EXPLAIN,
    (VERB_INTERROGATING, SHAPE_WORKFLOW): CAPABILITY_EXPLAIN,
    (VERB_INTERROGATING, SHAPE_RUN): CAPABILITY_EXPLAIN,
    (VERB_INTERROGATING, SHAPE_STEP): CAPABILITY_EXPLAIN,
    (VERB_INTERROGATING, SHAPE_VERDICT_WORD): CAPABILITY_EXPLAIN,
}


QUESTION_BY_ASK: dict[str, str] = {
    ASK_AUTHORING_A_RUN: (
        "A run is a past execution, so there is nothing to author from it. Do you mean the "
        "plan it executed?"
    ),
    ASK_AUTHORING_A_STEP: (
        "Cairn authors a whole plan and never one step. Which plan do you mean?"
    ),
    ASK_MUTATING_A_RUN: (
        "A past run cannot be changed. Do you want to read it, or to change the plan it ran?"
    ),
    ASK_EXECUTING_A_PAST_RUN: (
        f"Do you want that plan run again, or to read what that run did? Running it is the "
        f"one that costs: {HEADLINE_COST}. And if you mean to continue that run rather "
        "than start a fresh one, say so: a recovery keeps its occasion and a fresh run "
        "mints a new one."
    ),
    ASK_EXECUTING_ONE_STEP: (
        "Cairn runs a whole plan; a step runs only as part of one, and every step already "
        "done is a cheap no-op. Which plan do you mean?"
    ),
    ASK_RECOVERING_WITHOUT_A_RUN: (
        "A recovery continues a particular run, so it needs one named. Which run should be "
        "continued — or do you want a fresh run instead?"
    ),
    ASK_RECOVERING_ONE_STEP: (
        "Recovery is re-running the whole plan, which no-ops every step already done. Which "
        "run should be continued?"
    ),
    ASK_WATCHING_A_PLAN: (
        f"Do you want this plan started so there is something to watch, or to read the "
        f"last run of it? Starting it is the one that costs: {HEADLINE_COST}."
    ),
    ASK_WATCHING_A_WORKFLOW: (
        f"Do you want this workflow started so there is something to watch, or to read the "
        f"last run of it? Starting it is the one that costs: {HEADLINE_COST}."
    ),
    ASK_SCHEDULING_A_RUN: (
        "A past run cannot be scheduled. Which plan should run on a schedule?"
    ),
    ASK_SCHEDULING_A_STEP: (
        "Cairn schedules a whole plan and never one step. Which plan do you mean?"
    ),
    ASK_VERB_ON_A_FROZEN_WORD: (
        "That is a word from Cairn's own vocabulary, and the only thing I can do with a "
        "word is say what it means. Did you mean to ask that, or is it the name of a plan "
        "or a run I did not recognise?"
    ),
    ASK_NO_VERB: (
        "I can see what you are naming but not what you want done with it. Which of these "
        "do you mean?"
    ),
    ASK_MANY_VERBS: (
        "That reads as more than one request, and they are not the same kind of thing. "
        "Which do you want first?"
    ),
    ASK_NO_SUBJECT: "Which one do you mean?",
    ASK_MANY_SUBJECTS: (
        "That names more than one thing to act on, and each would be a separate piece of "
        "work. Which do you want first?"
    ),
    ASK_NOTHING_RECOGNISED: (
        "I did not recognise a plan, a workflow, a run or a Cairn word in that. What would "
        "you like me to do, and to what?"
    ),
}

# Four of the five structural asks compute their readings from the table; `nothing_recognised`
# reaches no cell and has none. The twelve tabled ones state theirs, because a cell has no
# column to derive them from.
READINGS_BY_TABLED_ASK: dict[str, tuple[str, ...]] = {
    ASK_AUTHORING_A_RUN: (),
    ASK_AUTHORING_A_STEP: (),
    ASK_MUTATING_A_RUN: (),
    ASK_EXECUTING_A_PAST_RUN: (CAPABILITY_RUN, CAPABILITY_REPORT),
    ASK_EXECUTING_ONE_STEP: (),
    ASK_RECOVERING_WITHOUT_A_RUN: (),
    ASK_RECOVERING_ONE_STEP: (),
    ASK_WATCHING_A_PLAN: (CAPABILITY_RUN, CAPABILITY_REPORT),
    ASK_WATCHING_A_WORKFLOW: (CAPABILITY_RUN, CAPABILITY_REPORT),
    ASK_SCHEDULING_A_RUN: (),
    ASK_SCHEDULING_A_STEP: (),
    ASK_VERB_ON_A_FROZEN_WORD: (),
}

FAMILY_BY_TABLED_ASK: dict[str, str] = {
    ASK_AUTHORING_A_RUN: FAMILY_NOTHING_APPLIES,
    ASK_AUTHORING_A_STEP: FAMILY_NOTHING_APPLIES,
    ASK_MUTATING_A_RUN: FAMILY_NOTHING_APPLIES,
    ASK_EXECUTING_A_PAST_RUN: FAMILY_VERB_UNCLEAR,
    ASK_EXECUTING_ONE_STEP: FAMILY_NOTHING_APPLIES,
    ASK_RECOVERING_WITHOUT_A_RUN: FAMILY_NOTHING_APPLIES,
    ASK_RECOVERING_ONE_STEP: FAMILY_NOTHING_APPLIES,
    ASK_WATCHING_A_PLAN: FAMILY_VERB_UNCLEAR,
    ASK_WATCHING_A_WORKFLOW: FAMILY_VERB_UNCLEAR,
    ASK_SCHEDULING_A_RUN: FAMILY_NOTHING_APPLIES,
    ASK_SCHEDULING_A_STEP: FAMILY_NOTHING_APPLIES,
    ASK_VERB_ON_A_FROZEN_WORD: FAMILY_NOTHING_APPLIES,
}


class Invocation(NamedTuple):
    """One request, already read into verb classes and argument shapes.

    Reading an utterance into this is the model's, and no unit test can prove it was done
    right — `fixtures/invocations/` carries the phrasings so a later model-in-the-loop
    harness has an input, and `SKILL.md` carries the lexicon a reader applies.
    """

    verbs: frozenset[str]
    subjects: frozenset[str]
    qualifiers: frozenset[str] = frozenset()


class Selected(NamedTuple):
    capability: str
    rule: str


class Ask(NamedTuple):
    reason: str
    family: str
    question: str
    readings: tuple[str, ...]


class Asked(NamedTuple):
    ask: Ask
    rule: str


Decision = Selected | Asked


def _ordered(capabilities: set[str]) -> tuple[str, ...]:
    return tuple(name for name in CAPABILITY_ORDER if name in capabilities)


def _readings(pairs: set[tuple[str, str]]) -> tuple[str, ...]:
    return _ordered(
        {
            outcome
            for pair in pairs
            if (outcome := DISPATCH_RULES[pair]) in CAPABILITY_ORDER
        }
    )


def _asked(reason: str, family: str, readings: tuple[str, ...], rule: str) -> Asked:
    """One question, naming the kind of cost of every consent-gated branch it offers.

    Four of the five structural reasons are asked over readings computed from the table, so
    their text cannot state a cost the way a tabled question can. Appending it here is what
    makes task 5's "wherever it is made" reach the questions too: a person choosing between
    reading a run and starting one is being offered a way to spend money, and a bare "which
    of these do you mean?" does not say so.
    """
    question = QUESTION_BY_ASK[reason]
    for capability, headline in (
        (CAPABILITY_RUN, HEADLINE_COST),
        (CAPABILITY_SCHEDULE, HEADLINE_DAEMON_COST),
    ):
        if capability in readings and headline not in question:
            question = f"{question} {capability.capitalize()} is a costly one: {headline}."
    return Asked(
        ask=Ask(reason=reason, family=family, question=question, readings=readings),
        rule=rule,
    )


def _family(readings: tuple[str, ...], fallback: str) -> str:
    """Which kind of question this is, decided by what it is actually offering.

    A shape whose every cell is itself an ask offers no capability at all, so it is
    `nothing_applies` however the arity looked — otherwise a question would claim to be a
    choice between readings it does not have.
    """
    if not readings:
        return FAMILY_NOTHING_APPLIES
    if set(readings) <= set(WRITES_NOTHING):
        return FAMILY_HARMLESS_CHOICE
    return fallback


def _no_verb(readings: tuple[str, ...], rule: str) -> Decision:
    """An object named with nothing asked of it, resolved only where one harmless reading
    stands.

    Report and Explain both answer without starting, locking or writing, so where every
    verb class over this object collapses to a single one of them the question would cost a
    turn and buy nothing. Anything else — a costly reading among them, or several harmless
    ones to choose between — is asked, and doc 15's rule applies in full.
    """
    if len(readings) == 1 and readings[0] in WRITES_NOTHING:
        return Selected(capability=readings[0], rule=f"safe:{rule}")
    return _asked(ASK_NO_VERB, _family(readings, FAMILY_VERB_UNCLEAR), readings, rule)


def dispatch(invocation: Invocation) -> Decision:
    """The capability this request reaches, or the question that has to be asked first.

    A word outside the frozen vocabulary raises nothing and resolves nothing. The producer of
    these values is a model reading `SKILL.md`, so an unrecognised token is exactly the input
    this is for — but it is a token that was *read out of the request*, so dropping it and
    taking the cell the survivor keys would answer a request nobody made. It counts toward
    the arity instead: one recognised verb beside one unrecognised one is two readings and
    is asked, and a request made entirely of them lands on `nothing_recognised`.
    """
    verbs = invocation.verbs & frozenset(VERB_CLASSES)
    subjects = invocation.subjects & frozenset(SUBJECT_SHAPES)
    unrecognised = (invocation.verbs - verbs, invocation.subjects - subjects)

    if not any(unrecognised) and len(verbs) == 1 and len(subjects) == 1:
        pair = (next(iter(verbs)), next(iter(subjects)))
        outcome = DISPATCH_RULES[pair]
        rule = f"table:{pair[0]}/{pair[1]}"
        if outcome in CAPABILITY_ORDER:
            return Selected(capability=outcome, rule=rule)
        return _asked(outcome, FAMILY_BY_TABLED_ASK[outcome], READINGS_BY_TABLED_ASK[outcome], rule)

    if unrecognised[1] and subjects:
        readings = _readings({(verb, subject) for verb in verbs for subject in subjects})
        return _asked(
            ASK_MANY_SUBJECTS,
            _family(readings, FAMILY_OBJECT_UNCLEAR),
            readings,
            "structural:unrecognised-subject",
        )
    if unrecognised[0] and verbs:
        pairs = {
            (verb, subject)
            for verb in verbs
            for subject in (subjects or frozenset(SUBJECT_SHAPES))
        }
        return _asked(
            ASK_MANY_VERBS,
            _family(_readings(pairs), FAMILY_VERB_UNCLEAR),
            _readings(pairs),
            "structural:unrecognised-verb",
        )
    if not verbs and not subjects:
        return _asked(ASK_NOTHING_RECOGNISED, FAMILY_NOTHING_APPLIES, (), "structural:empty")

    if not subjects:
        pairs = {(verb, subject) for verb in verbs for subject in SUBJECT_SHAPES}
        readings = _readings(pairs)
        return _asked(
            ASK_NO_SUBJECT,
            _family(readings, FAMILY_OBJECT_UNCLEAR),
            readings,
            "structural:no-subject",
        )

    if not verbs:
        pairs = {(verb, subject) for verb in VERB_CLASSES for subject in subjects}
        return _no_verb(_readings(pairs), "structural:no-verb")

    pairs = {(verb, subject) for verb in verbs for subject in subjects}
    readings = _readings(pairs)
    if len(subjects) > 1:
        return _asked(
            ASK_MANY_SUBJECTS,
            _family(readings, FAMILY_OBJECT_UNCLEAR),
            readings,
            "structural:many-subjects",
        )
    # Never resolved, however harmless the readings look. Two verb classes in one sentence
    # are two requests, and which comes first is the person's to say — the safe collapse
    # below belongs to a request that named no verb at all, where there is no order to get
    # wrong.
    return _asked(
        ASK_MANY_VERBS,
        _family(readings, FAMILY_VERB_UNCLEAR),
        readings,
        "structural:many-verbs",
    )


__all__ = [
    "ASK_FAMILIES",
    "ASK_REASONS",
    "DISPATCH_RULES",
    "FAMILY_BY_TABLED_ASK",
    "QUESTION_BY_ASK",
    "READINGS_BY_TABLED_ASK",
    "Ask",
    "Asked",
    "Decision",
    "Invocation",
    "Selected",
    "dispatch",
]
