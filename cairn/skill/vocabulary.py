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

# Ordered by what dispatching here *wrongly* costs, worst first. This is deliberately not a
# precedence: no rule may resolve on it, because doc 15's whole claim is that an ambiguous
# request is asked back rather than settled on the more likely reading. What the order is
# for is that the two subsets below are contiguous slices of it, so a capability added at
# the wrong rank breaks a test rather than quietly acquiring or shedding a gate.
CAPABILITY_ORDER: tuple[str, ...] = (
    CAPABILITY_RUN,  # spends money, mutates a repository, takes the lock, commits
    CAPABILITY_SCHEDULE,  # arms a daemon whose retry scanner reaches runs Cairn never wrote
    CAPABILITY_EDIT,  # replaces a definition that exists, wholesale, never merged
    CAPABILITY_AUTHOR,  # writes a definition that did not exist
    CAPABILITY_REPORT,  # reads
    CAPABILITY_EXPLAIN,  # reads, and needs no run to exist
)

# Nothing may start without an authorisation ([consent.py]).
CONSENT_GATED: tuple[str, ...] = (CAPABILITY_RUN, CAPABILITY_SCHEDULE)

# Where every reading of a request is one of these, asking costs a turn and answering costs
# nothing, so the dispatcher answers. This is the one place a reading is resolved rather
# than asked, and it is safe precisely because the set is closed.
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
# is the guess doc 15 forbids and which costs either a re-payment or a stale answer.


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

# A qualifier modifies how a capability proceeds and can never be what a request is about.
# Keeping the two axes apart is what lets the table stay 48 cells instead of 192.
QUALIFIER_SHAPES: tuple[str, ...] = (SHAPE_REPOSITORY, SHAPE_CADENCE)

ARGUMENT_SHAPES: tuple[str, ...] = SUBJECT_SHAPES + QUALIFIER_SHAPES

# There is no `nothing` shape and no `no verb` class. Absence is an empty set, which is what
# makes "a bare workflow name with no verb" fall out of arity rather than out of a word
# invented to carry it.


FAMILY_NOTHING_APPLIES = "nothing_applies"  # the request names nothing Cairn does
FAMILY_VERB_UNCLEAR = "verb_unclear"  # several readings, at least one of them costly
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

# What each reading costs, so a disclosure states the price of the road not taken rather
# than only announcing the one taken. Total over OCCASION_READINGS, asserted.
COST_BY_READING: dict[str, str] = {
    OCCASION_NEW: (
        "every run-scoped and period-scoped step is paid for again, because a new occasion "
        "is a new freshness key; once-scoped steps, which is the default and every code "
        "step, stay cheap no-ops"
    ),
    OCCASION_CONTINUE: (
        "every run-scoped and period-scoped step that already ran under this occasion is "
        "skipped, so work whose answer has moved since is not redone"
    ),
}


COST_SPEND = "spend"
COST_CEILING = "ceiling"
COST_MODEL = "model"
COST_TIMEOUT = "timeout"
COST_MUTATES = "mutates"
COST_WORKTREES = "worktrees"
COST_LOCK = "lock"
COST_COMMITS = "commits"

# What a person is agreeing to. The money fact leads, asserted, because it is the one a
# person most needs before saying yes and the one no other surface states at all — and the
# three bounds follow it, because a ceiling, a model and a timeout are the facts that
# decide what "up to N sessions" can actually cost ([17.3]).
RUN_COST_FACTS: tuple[str, ...] = (
    COST_SPEND,
    COST_CEILING,
    COST_MODEL,
    COST_TIMEOUT,
    COST_MUTATES,
    COST_WORKTREES,
    COST_LOCK,
    COST_COMMITS,
)

# The headline a question names when one of its branches is a run. Doc 15 task 5 wants the
# price stated wherever a run is offered, and a question offering one is such a place — but
# there is no offer yet and possibly no definition, so what a question can state is the kind
# of cost and never a number. The number comes only from an offer, which reads the
# definition.
HEADLINE_COST = (
    "it spends money on agent sessions, takes the repository's run lock and commits"
)

# The same, for the other consent-gated capability. A schedule spends nothing by itself and
# arms something that can spend repeatedly, which is a different sentence.
HEADLINE_DAEMON_COST = (
    "it needs a scheduler running, whose retry scanner re-executes every failed run on this "
    "machine from the last day, including runs Cairn never wrote"
)

# Total over RUN_COST_FACTS, asserted. Every field is read out of the definition that is
# about to run, so a cost cannot be quoted for a workflow nobody has in hand.
COST_SENTENCES: dict[str, str] = {
    COST_SPEND: (
        "it starts up to {agent_steps} paid agent session(s), on the coding-agent "
        "installation you already authenticated — the cost lands on that allowance, and "
        "Cairn never sees it"
    ),
    COST_CEILING: (
        "every one of those sessions is stopped at the dollar ceiling its step writes — "
        "US$ {ceiling_usd} at most across all of them"
    ),
    COST_MODEL: (
        "each session is pinned to the model its step names ({models}), which is the "
        "model the run's record will name"
    ),
    COST_TIMEOUT: (
        "every step is killed at its own written timeout; the longest allows "
        "{longest_timeout_seconds}s"
    ),
    COST_MUTATES: "it changes the working tree of {repository} and moves branches in it",
    COST_WORKTREES: (
        "it creates worktrees beside the repository, under {worktrees_root}, one per "
        "isolated step"
    ),
    COST_LOCK: (
        "it takes {repository}'s run lock, so a second Cairn run against that repository "
        "is refused with this one named for as long as it holds"
    ),
    COST_COMMITS: (
        "it commits: each verified step's work and its marker land in one commit on the "
        "step's branch, and every verified branch is merged into {parent_branch}"
    ),
}


# Every way one offer can be answered, and each is a fact about a file on disk. **No list of
# accepting or refusing phrases stands beside them**, here or in `consent.py`: a reply arrives
# as `run start --reply "…"`, the session's own argument, so anything compared against it
# would sit downstream of the judgement it claimed to make and could fire only where a session
# misread the words and then quoted them faithfully. No list covers English, and one reasoned
# about as protection is worse than none. `SKILL.md` states the rule that binds the judgement,
# and the session is what keeps it.
ACCEPTED = "accepted"
REFUSED_NO_WORDS = "no_words"
REFUSED_NO_SUCH_OFFER = "no_such_offer"
REFUSED_OFFER_UNREADABLE = "offer_unreadable"
REFUSED_ALREADY_SPENT = "already_spent"
REFUSED_WORKFLOW_MOVED = "workflow_moved"

CONSENT_OUTCOMES: tuple[str, ...] = (
    ACCEPTED,
    REFUSED_NO_WORDS,
    REFUSED_NO_SUCH_OFFER,
    REFUSED_OFFER_UNREADABLE,
    REFUSED_ALREADY_SPENT,
    REFUSED_WORKFLOW_MOVED,
)


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
BINDING_OCCASION_READING = "occasion_reading"
BINDING_AUTHORISATION = "authorisation"

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
    BINDING_OCCASION_READING,
    BINDING_AUTHORISATION,
)


__all__ = [
    "ACCEPTED",
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
    "CONSENT_GATED",
    "CONSENT_OUTCOMES",
    "COST_BY_READING",
    "COST_CEILING",
    "COST_COMMITS",
    "COST_LOCK",
    "COST_MODEL",
    "COST_MUTATES",
    "COST_SENTENCES",
    "COST_SPEND",
    "COST_TIMEOUT",
    "COST_WORKTREES",
    "DOCUMENT_BY_CAPABILITY",
    "FAMILY_HARMLESS_CHOICE",
    "FAMILY_NOTHING_APPLIES",
    "FAMILY_OBJECT_UNCLEAR",
    "FAMILY_VERB_UNCLEAR",
    "HEADLINE_COST",
    "HEADLINE_DAEMON_COST",
    "OCCASION_CONTINUE",
    "OCCASION_NEW",
    "OCCASION_READINGS",
    "QUALIFIER_SHAPES",
    "READING_BY_TRIGGER",
    "REFUSED_ALREADY_SPENT",
    "REFUSED_NO_SUCH_OFFER",
    "REFUSED_NO_WORDS",
    "REFUSED_OFFER_UNREADABLE",
    "REFUSED_WORKFLOW_MOVED",
    "RUN_COST_FACTS",
    "SHAPE_CADENCE",
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
