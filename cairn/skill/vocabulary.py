"""The words the skill is allowed to use, and nothing else.

This module imports nothing of Cairn's and does nothing. It exists so that the rules a
person's request is read against are a value a test can enumerate rather than prose a
reviewer has to agree with — the same brief `cairn/record/vocabulary.py` holds for the run
record, for the same reason: a plausible default is indistinguishable from a decision.

The surface that *executes* these rules is `SKILL.md`, which a model reads. Nothing here is
imported at dispatch time and nothing here classifies English. What a model cannot be asked
to do reliably is keep a rule set disjoint and total in its head across eight verb classes
and six object shapes, and that is exactly what this module makes checkable.
"""

from __future__ import annotations

CAPABILITY_RUN = "run"
CAPABILITY_SCHEDULE = "schedule"
CAPABILITY_EDIT = "edit"
CAPABILITY_AUTHOR = "author"
CAPABILITY_REPORT = "report"
CAPABILITY_EXPLAIN = "explain"

# Ordered by how far dispatching here *wrongly* reaches, furthest first. This is
# deliberately not a precedence: no rule may resolve on it, because doc 15's whole claim is
# that an ambiguous request is asked back rather than settled on the more likely reading.
# What the order is for is that the subset below is a contiguous slice of it, so a
# capability added at the wrong rank breaks a test rather than quietly changing what an
# ambiguous question puts forward.
CAPABILITY_ORDER: tuple[str, ...] = (
    CAPABILITY_RUN,  # mutates a repository, takes the lock, commits
    CAPABILITY_SCHEDULE,  # arms a daemon whose retry scanner reaches runs Cairn never wrote
    CAPABILITY_EDIT,  # replaces a definition that exists, wholesale, never merged
    CAPABILITY_AUTHOR,  # writes a definition that did not exist
    CAPABILITY_REPORT,  # reads
    CAPABILITY_EXPLAIN,  # reads, and needs no run to exist
)

# Where every reading of a request is one of these, the question would take a turn and the
# answer changes nothing, so the dispatcher answers. This is the one place a reading is
# resolved rather than asked, and it is safe precisely because the set is closed.
WRITES_NOTHING: tuple[str, ...] = (CAPABILITY_REPORT, CAPABILITY_EXPLAIN)


VERB_AUTHORING = "authoring"
VERB_MUTATING = "mutating"
VERB_EXECUTING = "executing"
VERB_RECOVERING = "recovering"
VERB_WATCHING = "watching"
VERB_RECOUNTING = "recounting"
VERB_ARRANGING = "arranging"
VERB_INTERROGATING = "interrogating"

VERB_CLASSES: tuple[str, ...] = (
    VERB_AUTHORING,
    VERB_MUTATING,
    VERB_EXECUTING,
    VERB_RECOVERING,
    VERB_WATCHING,
    VERB_RECOUNTING,
    VERB_ARRANGING,
    VERB_INTERROGATING,
)

# `recovering` is a class of its own rather than `executing` with a modifier, because the
# occasion turns on it and nothing else does: a recovery continues the occasion it is
# recovering and everything else mints a new one ([resolve.py], [docs/triggers.md]). Folded
# into `executing`, that decision would have to be inferred from the object's tense, which
# is the guess doc 15 forbids and which yields either redone work or a stale answer.


SHAPE_PLAN_DOCUMENT = "plan_document"  # a markdown plan, or a folder of task documents
SHAPE_PLAN_GRAPH = "plan_graph"  # a derived graph.json
SHAPE_WORKFLOW = "workflow"  # a plan slug, or a generated definition's path
SHAPE_RUN = "run"  # a run id, or a reference to a past execution
SHAPE_STEP = "step"  # a step id, or the plan's own name for one
SHAPE_VERDICT_WORD = "verdict_word"  # a member of one of Cairn's frozen vocabularies

# What a request can be *about*. Exactly one of these is what the table is keyed on.
SUBJECT_SHAPES: tuple[str, ...] = (
    SHAPE_PLAN_DOCUMENT,
    SHAPE_PLAN_GRAPH,
    SHAPE_WORKFLOW,
    SHAPE_RUN,
    SHAPE_STEP,
    SHAPE_VERDICT_WORD,
)

SHAPE_REPOSITORY = "repository"  # an explicit repository path
SHAPE_CADENCE = "cadence"  # "every night", a cron expression, a webhook
SHAPE_MODEL = "model"  # "all steps on sonnet 5.5", "pin it to the opus model"

# A qualifier modifies how a capability proceeds and can never be what a request is about.
# Keeping the two axes apart is what lets the table stay 48 cells instead of 384: a request
# names any combination of the three, and folding them into the subject axis would key the
# table on every one of those combinations.
QUALIFIER_SHAPES: tuple[str, ...] = (SHAPE_REPOSITORY, SHAPE_CADENCE, SHAPE_MODEL)

ARGUMENT_SHAPES: tuple[str, ...] = SUBJECT_SHAPES + QUALIFIER_SHAPES

# There is no `nothing` shape and no `no verb` class. Absence is an empty set, which is what
# makes "a bare workflow name with no verb" fall out of arity rather than out of a word
# invented to carry it.


FAMILY_NOTHING_APPLIES = "nothing_applies"  # the request names nothing Cairn does
FAMILY_VERB_UNCLEAR = "verb_unclear"  # several readings, at least one of them writing
FAMILY_OBJECT_UNCLEAR = "object_unclear"  # the capability is clear, the object is not
FAMILY_HARMLESS_CHOICE = "harmless_choice"  # every reading of it only reads

ASK_FAMILIES: tuple[str, ...] = (
    FAMILY_NOTHING_APPLIES,
    FAMILY_VERB_UNCLEAR,
    FAMILY_OBJECT_UNCLEAR,
    FAMILY_HARMLESS_CHOICE,
)


OCCASION_NEW = "new_occasion"
OCCASION_CONTINUE = "continue_occasion"
OCCASION_READINGS: tuple[str, ...] = (OCCASION_NEW, OCCASION_CONTINUE)

TRIGGER_FRESH = "fresh"  # a plan or a workflow named, and nothing about a past run
TRIGGER_RECOVERY = "recovery"  # a past run named, to be continued
TRIGGER_PINNED = "pinned"  # an occasion supplied verbatim
TRIGGER_SCHEDULED = "scheduled"  # a cron firing or a webhook; the skill composes none

TRIGGER_SHAPES: tuple[str, ...] = (
    TRIGGER_FRESH,
    TRIGGER_RECOVERY,
    TRIGGER_PINNED,
    TRIGGER_SCHEDULED,
)

# Total over TRIGGER_SHAPES, asserted. `scheduled` mints because a cron firing has no
# override point at all: an occasion fixed when the workflow was written would be reused by
# every firing, and every scoped step from the second firing onward would find a fresh
# marker and skip ([docs/triggers.md], measured over three firings).
READING_BY_TRIGGER: dict[str, str] = {
    TRIGGER_FRESH: OCCASION_NEW,
    TRIGGER_RECOVERY: OCCASION_CONTINUE,
    TRIGGER_PINNED: OCCASION_CONTINUE,
    TRIGGER_SCHEDULED: OCCASION_NEW,
}

# What each reading means for the work, so a disclosure states the consequence of the road
# not taken rather than only announcing the one taken. Total over OCCASION_READINGS,
# asserted.
CONSEQUENCE_BY_READING: dict[str, str] = {
    OCCASION_NEW: (
        "every run-scoped and period-scoped step runs again, because a new occasion is a "
        "new freshness key; once-scoped steps, which is the default and every code step, "
        "stay no-ops"
    ),
    OCCASION_CONTINUE: (
        "every run-scoped and period-scoped step that already ran under this occasion is "
        "skipped, so work whose answer has moved since is not redone"
    ),
}


# Which document holds each capability's procedure. Here rather than only in `SKILL.md`'s
# prose and a test constant, because it is the answer to "what do I change to add one" and
# it is the mapping a totality assertion can be keyed on. Four documents for six
# capabilities: Edit is authoring under I1, and Report and Explain both only read.
DOCUMENT_BY_CAPABILITY: dict[str, str] = {
    CAPABILITY_AUTHOR: "authoring.md",
    CAPABILITY_EDIT: "authoring.md",
    CAPABILITY_RUN: "running.md",
    CAPABILITY_SCHEDULE: "scheduling.md",
    CAPABILITY_REPORT: "reading.md",
    CAPABILITY_EXPLAIN: "reading.md",
}


BINDING_CAPABILITY = "capability"
BINDING_REPOSITORY = "repository"
BINDING_WORKFLOW = "workflow"
BINDING_PLAN_DOCUMENT = "plan_document"
BINDING_PLAN_GRAPH = "plan_graph"
BINDING_RUN = "run"
BINDING_STEP = "step"
BINDING_VERDICT_WORD = "verdict_word"
BINDING_CADENCE = "cadence"
BINDING_MODEL = "model"
BINDING_OCCASION_READING = "occasion_reading"

# What a capability document may read and may not re-decide. A document that re-decides one
# is a second decision point, and a second decision point is how a run starts against the
# wrong repository or starts twice.
BINDINGS: tuple[str, ...] = (
    BINDING_CAPABILITY,
    BINDING_REPOSITORY,
    BINDING_WORKFLOW,
    BINDING_PLAN_DOCUMENT,
    BINDING_PLAN_GRAPH,
    BINDING_RUN,
    BINDING_STEP,
    BINDING_VERDICT_WORD,
    BINDING_CADENCE,
    BINDING_MODEL,
    BINDING_OCCASION_READING,
)


__all__ = [
    "ARGUMENT_SHAPES",
    "ASK_FAMILIES",
    "BINDINGS",
    "CAPABILITY_AUTHOR",
    "CAPABILITY_EDIT",
    "CAPABILITY_EXPLAIN",
    "CAPABILITY_ORDER",
    "CAPABILITY_REPORT",
    "CAPABILITY_RUN",
    "CAPABILITY_SCHEDULE",
    "CONSEQUENCE_BY_READING",
    "DOCUMENT_BY_CAPABILITY",
    "FAMILY_HARMLESS_CHOICE",
    "FAMILY_NOTHING_APPLIES",
    "FAMILY_OBJECT_UNCLEAR",
    "FAMILY_VERB_UNCLEAR",
    "OCCASION_CONTINUE",
    "OCCASION_NEW",
    "OCCASION_READINGS",
    "QUALIFIER_SHAPES",
    "READING_BY_TRIGGER",
    "SHAPE_CADENCE",
    "SHAPE_MODEL",
    "SHAPE_PLAN_DOCUMENT",
    "SHAPE_PLAN_GRAPH",
    "SHAPE_REPOSITORY",
    "SHAPE_RUN",
    "SHAPE_STEP",
    "SHAPE_VERDICT_WORD",
    "SHAPE_WORKFLOW",
    "SUBJECT_SHAPES",
    "TRIGGER_FRESH",
    "TRIGGER_PINNED",
    "TRIGGER_RECOVERY",
    "TRIGGER_SCHEDULED",
    "TRIGGER_SHAPES",
    "VERB_ARRANGING",
    "VERB_AUTHORING",
    "VERB_CLASSES",
    "VERB_EXECUTING",
    "VERB_INTERROGATING",
    "VERB_MUTATING",
    "VERB_RECOUNTING",
    "VERB_RECOVERING",
    "VERB_WATCHING",
    "WRITES_NOTHING",
]
