"""Every word the run record uses, frozen before anything reads them.

Four independent designs of this model spelled the same states four different ways, one
enum value present in three of them and absent from the fourth. "One model feeds every
surface" holds only if the vocabulary is closed first, so this module is a contract rather
than a program: it imports nothing of Cairn's but the exclusion causes it quotes, and
`docs/run-model.md` states the same values in the same order, which a test asserts.

Each vocabulary is a tuple, and where an order carries meaning the tuple *is* the order.
Two structures — an enumeration and a separate ranking — drift.
"""

from __future__ import annotations

RECORD_VERSION = 2

# --- the run verdict -----------------------------------------------------------------
#
# Highest concern first, and the first value a run qualifies for wins. A run with
# exclusions is never spelled the same as a clean success: that is I5, and it is why
# `green_with_exclusions` is a word of its own rather than a flag on `green`.
VERDICT_FAILED = "failed"
VERDICT_BLOCKED = "blocked"
VERDICT_RUNNING = "running"
VERDICT_GREEN_WITH_EXCLUSIONS = "green_with_exclusions"
VERDICT_ALL_NO_OP = "all_no_op"
VERDICT_GREEN = "green"
VERDICT_PRECEDENCE: tuple[str, ...] = (
    VERDICT_FAILED,
    VERDICT_BLOCKED,
    VERDICT_RUNNING,
    VERDICT_GREEN_WITH_EXCLUSIONS,
    VERDICT_ALL_NO_OP,
    VERDICT_GREEN,
)

# --- the step outcome ----------------------------------------------------------------
#
# `pending` is a sibling that has not started; `not_reached` is downstream of a halt and
# never will. Collapsing them would report a run still in flight and a run that gave up as
# the same thing.
OUTCOME_VERIFIED = "verified"
OUTCOME_FAILED = "failed"
OUTCOME_EXCLUDED = "excluded"
OUTCOME_NO_OP = "no_op"
OUTCOME_NOT_REACHED = "not_reached"
OUTCOME_RUNNING = "running"
OUTCOME_PENDING = "pending"
STEP_OUTCOMES: tuple[str, ...] = (
    OUTCOME_VERIFIED,
    OUTCOME_FAILED,
    OUTCOME_EXCLUDED,
    OUTCOME_NO_OP,
    OUTCOME_NOT_REACHED,
    OUTCOME_RUNNING,
    OUTCOME_PENDING,
)

# Orthogonal to the outcome, and carried beside it rather than folded in. A block rides an
# exclusion, a divergence rides an exclusion or a failure, and an unverified step is
# otherwise a plain verified one — folding them in would multiply seven outcomes by eight
# combinations, which is exactly how one state acquires four spellings.
OVERLAY_BLOCKED = "blocked"
OVERLAY_DIVERGENCE = "divergence"
OVERLAY_UNVERIFIED = "unverified"
OVERLAYS: tuple[str, ...] = (OVERLAY_BLOCKED, OVERLAY_DIVERGENCE, OVERLAY_UNVERIFIED)

# --- what needs a person's attention -------------------------------------------------
#
# Highest concern first. Naming the order here is what makes every renderer conform to one
# definition rather than inventing a subset, and it is deliberately not the verdict's
# order: a block outranks a failure for a reader, because a person can act on it now.
ATTENTION_BLOCKED = "blocked"
ATTENTION_FAILURE = "failure"
ATTENTION_EXCLUDED = "excluded"
ATTENTION_BUDGET = "budget"
ATTENTION_HOUSEKEEPING_FAILURE = "housekeeping_failure"
ATTENTION_DIVERGENCE = "divergence"
ATTENTION_FOLLOW_UP = "follow_up"
ATTENTION_ORDER: tuple[str, ...] = (
    ATTENTION_BLOCKED,
    ATTENTION_FAILURE,
    ATTENTION_EXCLUDED,
    ATTENTION_BUDGET,
    ATTENTION_HOUSEKEEPING_FAILURE,
    ATTENTION_DIVERGENCE,
    ATTENTION_FOLLOW_UP,
)

# --- the exit-code contract ----------------------------------------------------------
#
# Frozen apart from the display verdict, so a severity judgement in a report can never
# silently redefine what automation sees. `green_with_exclusions` carries a code of its own
# because that is the distinction automation most needs and the one the engine cannot make.
# 2 is left alone: argparse spends it on usage, and a caller reading 2 as a verdict would
# be reading a typo.
EXIT_GREEN = 0
EXIT_FAILED = 1
EXIT_EXCLUSIONS = 3
EXIT_BLOCKED = 4
EXIT_UNFINISHED = 5
EXIT_NO_RECORD = 6
VERDICT_EXIT_CODES: dict[str, int] = {
    VERDICT_GREEN: EXIT_GREEN,
    VERDICT_ALL_NO_OP: EXIT_GREEN,
    VERDICT_GREEN_WITH_EXCLUSIONS: EXIT_EXCLUSIONS,
    VERDICT_FAILED: EXIT_FAILED,
    VERDICT_BLOCKED: EXIT_BLOCKED,
    VERDICT_RUNNING: EXIT_UNFINISHED,
}

# --- where a field's authority sits --------------------------------------------------
#
# A field the source did not record is marked absent rather than defaulted to something
# plausible, because a plausible default is indistinguishable from a measurement.
PROVENANCE_RECORDED = "recorded"
PROVENANCE_DERIVED = "derived"
PROVENANCE_ABSENT = "absent"
PROVENANCES: tuple[str, ...] = (
    PROVENANCE_RECORDED,
    PROVENANCE_DERIVED,
    PROVENANCE_ABSENT,
)

# --- the shape of the graph ----------------------------------------------------------
EDGE_STEP = "step"
EDGE_DEPENDENCY = "dependency"
EDGE_WAVE = "wave"
EDGE_RUN = "run"
EDGE_KINDS: tuple[str, ...] = (EDGE_STEP, EDGE_DEPENDENCY, EDGE_WAVE, EDGE_RUN)

# --- what to do now ------------------------------------------------------------------
#
# Derived from the record rather than composed as prose, so a report that answers "it
# failed" and stops has not answered the reader's second question.
NEXT_DECIDE = "decide"
NEXT_SETTLE_MERGE = "settle_merge"
NEXT_RERUN = "rerun"
NEXT_START_SCHEDULER = "start_scheduler"
NEXT_WAIT = "wait"
NEXT_NOTHING = "nothing"
NEXT_ACTIONS: tuple[str, ...] = (
    NEXT_DECIDE,
    NEXT_SETTLE_MERGE,
    NEXT_RERUN,
    NEXT_START_SCHEDULER,
    NEXT_WAIT,
    NEXT_NOTHING,
)

__all__ = [
    "ATTENTION_BLOCKED",
    "ATTENTION_BUDGET",
    "ATTENTION_DIVERGENCE",
    "ATTENTION_EXCLUDED",
    "ATTENTION_FAILURE",
    "ATTENTION_FOLLOW_UP",
    "ATTENTION_HOUSEKEEPING_FAILURE",
    "ATTENTION_ORDER",
    "EDGE_DEPENDENCY",
    "EDGE_KINDS",
    "EDGE_RUN",
    "EDGE_STEP",
    "EDGE_WAVE",
    "EXIT_BLOCKED",
    "EXIT_EXCLUSIONS",
    "EXIT_FAILED",
    "EXIT_GREEN",
    "EXIT_NO_RECORD",
    "EXIT_UNFINISHED",
    "NEXT_ACTIONS",
    "NEXT_DECIDE",
    "NEXT_NOTHING",
    "NEXT_RERUN",
    "NEXT_SETTLE_MERGE",
    "NEXT_START_SCHEDULER",
    "NEXT_WAIT",
    "OUTCOME_EXCLUDED",
    "OUTCOME_FAILED",
    "OUTCOME_NOT_REACHED",
    "OUTCOME_NO_OP",
    "OUTCOME_PENDING",
    "OUTCOME_RUNNING",
    "OUTCOME_VERIFIED",
    "OVERLAYS",
    "OVERLAY_BLOCKED",
    "OVERLAY_DIVERGENCE",
    "OVERLAY_UNVERIFIED",
    "PROVENANCES",
    "PROVENANCE_ABSENT",
    "PROVENANCE_DERIVED",
    "PROVENANCE_RECORDED",
    "RECORD_VERSION",
    "STEP_OUTCOMES",
    "VERDICT_ALL_NO_OP",
    "VERDICT_BLOCKED",
    "VERDICT_EXIT_CODES",
    "VERDICT_FAILED",
    "VERDICT_GREEN",
    "VERDICT_GREEN_WITH_EXCLUSIONS",
    "VERDICT_PRECEDENCE",
    "VERDICT_RUNNING",
]
