"""The only module that turns a frozen word into a human sentence.

Every map here is total over the vocabulary it is keyed on, and a test asserts it: a value
the record can hold and this module cannot phrase raises rather than falling through to
something plausible. That is the same refusal `record/engine.py` makes about an unmapped
engine status, for the same reason — a plausible default is indistinguishable from a
measurement, and here it would be indistinguishable from a verdict.

Phrasing lives in one place so that three renderings cannot describe one run three ways. A
renderer spells no sentence of its own; it is handed these and decides only typography.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from cairn.record.facts import ABSENT, NONE
from cairn.record.vocabulary import (
    ATTENTION_BLOCKED,
    ATTENTION_BUDGET,
    ATTENTION_DIVERGENCE,
    ATTENTION_EXCLUDED,
    ATTENTION_FAILURE,
    ATTENTION_FOLLOW_UP,
    ATTENTION_HOUSEKEEPING_FAILURE,
    ATTENTION_ORDER,
    NEXT_ACTIONS,
    NEXT_DECIDE,
    NEXT_NOTHING,
    NEXT_RERUN,
    NEXT_SETTLE_MERGE,
    NEXT_START_SCHEDULER,
    NEXT_WAIT,
    OUTCOME_EXCLUDED,
    OUTCOME_FAILED,
    OUTCOME_NO_OP,
    OUTCOME_NOT_REACHED,
    OUTCOME_PENDING,
    OUTCOME_RUNNING,
    OUTCOME_VERIFIED,
    STEP_OUTCOMES,
    VERDICT_ALL_NO_OP,
    VERDICT_BLOCKED,
    VERDICT_FAILED,
    VERDICT_GREEN,
    VERDICT_GREEN_WITH_EXCLUSIONS,
    VERDICT_PRECEDENCE,
    VERDICT_RUNNING,
)
from cairn.report.spine import (
    RULE_ACTOR,
    RULE_ASSERTION,
    RULE_LINK,
    RULE_MONEY,
    RULE_VALUE,
    RULES,
    TONE_ALARM,
    TONE_CAUTION,
    TONE_PLAIN,
)
from cairn.verify import (
    EXCLUSION_CAUSES,
)
from cairn.verify import (
    GATE_INDETERMINATE as CAUSE_GATE_INDETERMINATE,
)
from cairn.verify import (
    NOT_REACHED as CAUSE_NOT_REACHED,
)
from cairn.verify import (
    ORCHESTRATOR_DIED as CAUSE_ORCHESTRATOR_DIED,
)
from cairn.verify import (
    PROVIDER_PROTOCOL as CAUSE_PROVIDER_PROTOCOL,
)
from cairn.verify import (
    REPORTED_FAILURE as CAUSE_REPORTED_FAILURE,
)
from cairn.verify import (
    RETRY_EXHAUSTED as CAUSE_RETRY_EXHAUSTED,
)
from cairn.verify import (
    TIMED_OUT as CAUSE_TIMED_OUT,
)
from cairn.verify import (
    USER_DECISION_REQUIRED as CAUSE_USER_DECISION_REQUIRED,
)
from cairn.verify import (
    VERIFY_FAILED as CAUSE_VERIFY_FAILED,
)

# What a report says instead of a number it does not have. Never `0`, never a blank: a run
# that recorded no cost and a run that cost nothing are different facts, and one of them is
# the crash.
NOT_RECORDED = "not recorded"
NOTHING_AT_ALL = "none"

HEADLINE_BY_VERDICT: dict[str, str] = {
    VERDICT_FAILED: "This run failed.",
    VERDICT_BLOCKED: "This run is blocked on a decision only a person can make.",
    VERDICT_RUNNING: "This run has not finished, so it has no outcome yet.",
    VERDICT_GREEN_WITH_EXCLUSIONS: "This run finished with exclusions. It is not a clean success.",
    VERDICT_ALL_NO_OP: "This run did nothing: every step was already complete.",
    VERDICT_GREEN: "This run worked.",
}

TONE_BY_VERDICT: dict[str, str] = {
    VERDICT_FAILED: TONE_ALARM,
    VERDICT_BLOCKED: TONE_ALARM,
    VERDICT_RUNNING: TONE_CAUTION,
    VERDICT_GREEN_WITH_EXCLUSIONS: TONE_ALARM,
    VERDICT_ALL_NO_OP: TONE_PLAIN,
    VERDICT_GREEN: TONE_PLAIN,
}

SENTENCE_BY_ACTION: dict[str, str] = {
    NEXT_DECIDE: "Decide the question this run is blocked on, then run it again.",
    NEXT_SETTLE_MERGE: "Settle the work that did not land, then run the plan again.",
    NEXT_RERUN: "Run it again once the failure is understood.",
    NEXT_START_SCHEDULER: "Start a scheduler: this run is queued and nothing is draining the queue.",
    NEXT_WAIT: "Wait: this run is still going.",
    NEXT_NOTHING: "Nothing.",
}

LABEL_BY_ATTENTION: dict[str, str] = {
    ATTENTION_BLOCKED: "Blocked on a decision",
    ATTENTION_FAILURE: "Failed",
    ATTENTION_EXCLUDED: "Excluded",
    ATTENTION_BUDGET: "Budget",
    ATTENTION_HOUSEKEEPING_FAILURE: "Housekeeping failed",
    ATTENTION_DIVERGENCE: "Divergence",
    ATTENTION_FOLLOW_UP: "Follow-up work",
}

LABEL_BY_OUTCOME: dict[str, str] = {
    OUTCOME_VERIFIED: "verified",
    OUTCOME_FAILED: "failed",
    OUTCOME_EXCLUDED: "excluded",
    OUTCOME_NO_OP: "skipped: already complete",
    OUTCOME_NOT_REACHED: "never ran",
    OUTCOME_RUNNING: "running",
    OUTCOME_PENDING: "not started",
}

# Why a step contributed no verified work, as a sentence rather than a token. The words are
# [docs/verify-gate.md]'s own, so the answer a person is given and the answer the document
# gives cannot differ — and this is the map that lets `cairn explain` quote the vocabulary
# instead of paraphrasing it.
SENTENCE_BY_CAUSE: dict[str, str] = {
    CAUSE_VERIFY_FAILED: (
        "the step's own assertion exited nonzero, so its end state was not there. Whatever "
        "the step said about itself, verification owns the green light"
    ),
    CAUSE_REPORTED_FAILURE: (
        "the step's own report vetoed it. A step's account of itself can lower its outcome "
        "and never raise it"
    ),
    CAUSE_PROVIDER_PROTOCOL: (
        "the step left no readable account of itself — most often a session that ended a "
        "turn without the structured report it is constrained to give. This is not a step "
        "that reported failure; it is a step whose account is missing, and its work may be "
        "sitting in the tree. The step's own summary says which of the two it was"
    ),
    CAUSE_USER_DECISION_REQUIRED: (
        "the step reached a decision it cannot make for itself, so it left the run rather "
        "than holding the repository's lock waiting for a person"
    ),
    CAUSE_NOT_REACHED: (
        "the step left no report of this run, so as far as anything durable shows it never "
        "ran — the engine's own node status is what separates never-reached from killed "
        "before it could write"
    ),
    CAUSE_GATE_INDETERMINATE: (
        "the gate could not establish what happened. It may have done all of its work; "
        "recording that as never having run would claim more than is known"
    ),
    CAUSE_TIMED_OUT: "the engine's bound killed the step before it finished",
    CAUSE_RETRY_EXHAUSTED: "the step hit its retry bound",
    CAUSE_ORCHESTRATOR_DIED: (
        "the run's own process was killed under the step, so nothing decided its fate at all"
    ),
}




def _absent(value: str) -> bool:
    return value == ABSENT


def value(shown: str) -> str:
    """One projected fact as a reader sees it, with an absence said rather than shown."""
    if _absent(shown):
        return NOT_RECORDED
    if shown == NONE:
        return NOTHING_AT_ALL
    return shown


def money(cost: str, notional: str) -> str:
    """A cost and whether it is money, in one string neither sink can separate.

    On a subscription login the figure is an API-equivalent price rather than money spent, so
    a rendering that printed the number alone would be inventing a payment. The qualifier
    travels inside the same string because that is the only way a sink cannot drop it.
    """
    if _absent(cost):
        return NOT_RECORDED
    if notional == "yes":
        return f"${cost} (an API-equivalent price, not money spent)"
    return f"${cost}"


def actor(name: str, started_by_cairn: str) -> str:
    """Who started the run — and an absent name is never rendered as unknown.

    The engine names the authenticated user only for a run started through its own view, so
    an absent actor means Cairn's own skill started it. `unknown` is a trigger kind the
    engine can record, and one word for those two facts is one word too few ([run-model.md]).
    """
    if started_by_cairn == "yes":
        return "Cairn"
    return value(name)


RULE_TEXT: dict[str, Callable[[tuple[str, ...]], str]] = {}


def assertion(passed: str) -> str:
    """What verification found, said rather than spelled as a bare yes or no.

    A divergence is two accounts side by side, and a reader who cannot tell what one of them
    means cannot weigh it against the other — which is the whole point of showing both.
    """
    if passed == "yes":
        return "the assertion passed"
    if passed == "no":
        return "the assertion did not pass"
    return value(passed)


def apply(rule: str, shown: tuple[str, ...]) -> str:
    """The one place a fact becomes the text a sink prints.

    Renderer and oracle call this same function, which is what makes "the rendering states
    what the projection holds" checkable: a sink that formatted a number itself disagrees
    with this, and a sink that never called it states nothing at all.
    """
    return RULE_TEXT[rule](shown)


# Every map that has to be total over a frozen vocabulary, including the rule table: a rule
# added to `RULES` and forgotten here would fall through to a `KeyError` at render time.
TOTAL_MAPS: tuple[tuple[Mapping[str, object], tuple[str, ...]], ...] = (
    (HEADLINE_BY_VERDICT, VERDICT_PRECEDENCE),
    (TONE_BY_VERDICT, VERDICT_PRECEDENCE),
    (SENTENCE_BY_ACTION, NEXT_ACTIONS),
    (LABEL_BY_ATTENTION, ATTENTION_ORDER),
    (LABEL_BY_OUTCOME, STEP_OUTCOMES),
    (SENTENCE_BY_CAUSE, EXCLUSION_CAUSES),
    (RULE_TEXT, RULES),
)

RULE_TEXT.update(
    {
        RULE_VALUE: lambda shown: value(shown[0]),
        RULE_MONEY: lambda shown: money(shown[0], shown[1]),
        RULE_ACTOR: lambda shown: actor(shown[0], shown[1]),
        # A link is still only text here; whether a sink may follow it is the sink's own
        # decision, and only one of them can follow anything at all.
        RULE_LINK: lambda shown: value(shown[0]),
        RULE_ASSERTION: lambda shown: assertion(shown[0]),
    }
)

__all__ = [
    "HEADLINE_BY_VERDICT",
    "LABEL_BY_ATTENTION",
    "LABEL_BY_OUTCOME",
    "NOTHING_AT_ALL",
    "NOT_RECORDED",
    "RULE_TEXT",
    "SENTENCE_BY_ACTION",
    "TONE_BY_VERDICT",
    "TOTAL_MAPS",
    "actor",
    "apply",
    "assertion",
    "money",
    "value",
]
