"""Which repository a run targets, and which occasion it keys on.

Both are run-level decisions that come from the invocation, and both have a wrong answer
that costs money quietly. They live together because they are the two questions
[trigger.py] must have settled before it can compose an engine invocation, and because
neither may be defaulted.

**The repository is never inferred.** There is no parameter here for the session's working
directory — not as a rule but as an absence, so a caller cannot supply one. A definition
encodes the repository it was authored for, and a mismatch is a question rather than an
override ([docs/triggers.md]).

**The occasion defaults to a new one.** Continuing an old one is the direction that can act
on stale work, so it requires a positive signal: a recovery of a named run, or an occasion
supplied verbatim.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, NamedTuple

from cairn.core import CairnError
from cairn.gitio import common_directory, refuse_unusable_repository
from cairn.marker import occasion_moment
from cairn.record.model import RunRecord
from cairn.skill.vocabulary import (
    COST_BY_READING,
    OCCASION_CONTINUE,
    OCCASION_NEW,
    READING_BY_TRIGGER,
    TRIGGER_PINNED,
    TRIGGER_RECOVERY,
    TRIGGER_SCHEDULED,
)
from cairn.topology import WORKTREES_SUFFIX, worktrees_parent
from cairn.workflow.schema import REPOSITORY_PARAM, declared_parameter, read

REPOSITORY_ABSENT = "absent"
REPOSITORY_MISMATCH = "mismatch"


class Resolved(NamedTuple):
    kind: Literal["resolved"]
    repository: Path
    encoded: Path | None


class Unresolved(NamedTuple):
    kind: Literal["unresolved"]
    outcome: str
    question: str


Resolution = Resolved | Unresolved


def refuse_missing_definition(workflow: Path, plan: str, repository: str) -> None:
    """Refuse, in words, a plan this repository has no generated definition for.

    The ordinary shape of the mistake this catches is asking to run a plan against a
    repository it was never authored for, which is a sentence a person will say and not a
    fault — so it is answered with what to do rather than with a traceback.
    """
    if not workflow.exists():
        raise CairnError(
            "invalid_arguments",
            f"{repository} has no generated definition for {plan!r} ({workflow} is not "
            "there). A workflow is authored for one repository; author this plan for this "
            "repository before there is anything to run or to describe",
        )


def encoded_repository(workflow: Path) -> Path | None:
    """The repository a generated definition was authored for, read back from its params.

    An absence is returned as one. A definition with no such entry is not a definition that
    will run against anything the caller names — it is one Cairn did not write, or one whose
    parameters were removed, and reporting that as agreement would be the plausible default
    every refusal in this package exists to avoid.
    """
    try:
        document = read(workflow)
    except (OSError, ValueError) as unreadable:
        # An unparseable definition is a hand edit, not an absence. Reading it as "encodes
        # no repository" would agree with whatever the caller named, over a file nobody
        # reviewed.
        raise CairnError(
            "invalid_arguments",
            f"{workflow} is not the JSON document Cairn writes, so the repository it was "
            f"authored for cannot be established: {unreadable}. Re-author the plan",
        ) from unreadable
    declared = declared_parameter(document, REPOSITORY_PARAM)
    return Path(declared) if declared else None


def resolve_repository(stated: str | None, workflow: Path | None = None) -> Resolution:
    """The repository this run targets, from what was asked and nothing else."""
    if stated is None:
        return Unresolved(
            kind="unresolved",
            outcome=REPOSITORY_ABSENT,
            question=(
                "Which repository should this run against? Cairn takes the repository from "
                "what you ask for, never from the directory this conversation happens to "
                "be in, and never from the workflow."
            ),
        )
    if not os.path.isabs(stated):
        raise CairnError(
            "invalid_arguments",
            f"{stated!r} is not an absolute path. The engine resolves a relative value "
            "against a scratch directory rather than against the repository",
        )
    # The same two derivations `cairn lock acquire` compares, run here so a spelling that
    # would send every isolated step somewhere the setup never created is a question in the
    # conversation rather than a green run that landed nothing ([cairn/parameters.py]).
    spliced = Path(stated + WORKTREES_SUFFIX).resolve()
    derived = worktrees_parent(Path(stated)).resolve()
    if spliced != derived:
        raise CairnError(
            "invalid_arguments",
            f"{stated!r} is a spelling this run cannot be started with: every isolated "
            f"step would run under {spliced}, while `cairn worktree setup` creates "
            f"{derived}. The engine creates a missing working directory rather than "
            "failing, so the branch would carry no work and the wave would land nothing "
            f"while reporting success. Pass {str(Path(stated))!r}",
        )
    target = Path(stated).resolve()
    refuse_unusable_repository(target)

    if workflow is None:
        return Resolved(kind="resolved", repository=target, encoded=None)

    encoded = encoded_repository(workflow)
    if encoded is None:
        return Resolved(kind="resolved", repository=target, encoded=None)
    if _same_repository(encoded, target):
        return Resolved(kind="resolved", repository=target, encoded=encoded)

    return Unresolved(
        kind="unresolved",
        outcome=REPOSITORY_MISMATCH,
        question=(
            f"{workflow} was authored for {encoded} and you have asked for {target}. A "
            "generated definition is bound to the repository it was authored for: its runs "
            "directory is resolved at authoring time and does not move with the parameter, "
            "so a retargeted run would do its work in one repository and file every record "
            f"in the other. Do you want this run against {encoded}, or the plan re-authored "
            f"for {target}?"
        ),
    )


def _same_repository(encoded: Path, target: Path) -> bool:
    """Whether two paths name one repository, answerable when one of them is gone.

    Asked of git where both exist, because a symlink or a `..` can spell one repository two
    ways. Where the encoded one no longer exists — a repository renamed or moved, which is
    the ordinary way these diverge — git can say nothing, and the paths themselves are the
    only evidence left. Reporting the absence as agreement would run against the caller's
    repository under a definition authored for another.
    """
    try:
        return common_directory(encoded) == common_directory(target)
    except CairnError:
        return encoded.resolve() == target


class OccasionSignal(NamedTuple):
    trigger: str
    named_run: str | None = None
    pinned: str | None = None
    prior_runs: int = 0


class OccasionDecision(NamedTuple):
    reading: str
    occasion: str | None
    disclose: bool
    taken: str
    forgone: str


def decide_occasion(
    signal: OccasionSignal, record: RunRecord | None = None
) -> OccasionDecision:
    """Whether this trigger mints an occasion or continues one, and what that costs.

    A new occasion re-pays for every scoped step; a continued one may act on work whose
    answer has moved. Both are the operator's to decide rather than Cairn's to infer, so
    where the invocation does not settle it the decision is disclosed with both prices.
    """
    if signal.pinned is not None and signal.trigger != TRIGGER_PINNED:
        raise CairnError(
            "invalid_occasion",
            f"an occasion was given with a {signal.trigger!r} trigger, which does not "
            "continue one. Dropping it would silently re-pay for every run-scoped step; "
            "pass --trigger pinned to continue that occasion",
        )
    if signal.named_run is not None and signal.trigger != TRIGGER_RECOVERY:
        raise CairnError(
            "invalid_occasion",
            f"a run was named with a {signal.trigger!r} trigger, which recovers nothing. "
            "Pass --trigger recovery to continue that run",
        )
    reading = READING_BY_TRIGGER[signal.trigger]
    forgone = COST_BY_READING[
        OCCASION_CONTINUE if reading == OCCASION_NEW else OCCASION_NEW
    ]
    taken = COST_BY_READING[reading]

    if signal.trigger == TRIGGER_SCHEDULED:
        if signal.pinned is not None:
            raise CairnError(
                "invalid_occasion",
                "a scheduled trigger has no override point, so a pinned occasion would be "
                "reused by every firing and every scoped step from the second firing "
                "onward would find a fresh marker and skip",
            )
        return OccasionDecision(reading, None, False, taken, forgone)

    if signal.trigger == TRIGGER_PINNED:
        if signal.pinned is None:
            raise CairnError(
                "invalid_occasion", "a pinned trigger carries no occasion to pin to"
            )
        occasion_moment(signal.pinned)
        return OccasionDecision(reading, signal.pinned, True, taken, forgone)

    if signal.trigger == TRIGGER_RECOVERY:
        if signal.named_run is None:
            raise CairnError(
                "invalid_occasion",
                "a recovery continues one particular run, so it needs one named: pass "
                "--recovering <run-id>",
            )
        if record is None:
            raise CairnError(
                "invalid_occasion",
                f"recovering {signal.named_run} needs that run's record, and none was read",
            )
        recorded = record["lineage"]["occasion"]
        if not recorded:
            # Minting here would present as a recovery while silently re-paying for every
            # scoped step, which is the more expensive of the two wrong answers and the one
            # the operator would not see.
            raise CairnError(
                "invalid_occasion",
                f"run {signal.named_run} recorded no occasion, so there is nothing to "
                "continue. Starting a fresh run instead re-pays every run-scoped step; say "
                "so explicitly if that is what you want",
            )
        occasion_moment(recorded)
        return OccasionDecision(reading, recorded, True, taken, forgone)

    # A first run of a plan has no other reading available, so stating one would be noise.
    # Naming a workflow that has run before is the one genuinely silent choice, and there
    # Cairn takes a new occasion and says so.
    return OccasionDecision(reading, None, signal.prior_runs > 0, taken, forgone)


__all__ = [
    "REPOSITORY_ABSENT",
    "REPOSITORY_MISMATCH",
    "OccasionDecision",
    "OccasionSignal",
    "Resolution",
    "Resolved",
    "Unresolved",
    "decide_occasion",
    "encoded_repository",
    "resolve_repository",
]
