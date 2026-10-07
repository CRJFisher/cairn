"""Which repository a run targets, and which occasion it keys on.

Both are run-level decisions that come from the invocation, and both have a wrong answer
that goes quietly unnoticed. They live together because they are the two questions
[trigger.py] must have settled before it can compose an engine invocation, and because
neither may be defaulted.

**The repository is resolved from three candidates and the answer says which one it was.**
A repository named in the request, the one holding the request's subjects, the one the
session is in — strongest first. Three things hang off the path and all three fail quietly
when it is wrong: the run lock, the `<repo>-worktrees` parent, and the definition's encoded
repository. What protects them is getting the path right, which agreeing candidates do more
reliably than a person retyping it, so a question is owed only where the candidates that
were found disagree or where none was found at all. The repository is never inferred from
the workflow: a definition encodes the one it was authored for, and a mismatch is a question
rather than an override ([docs/triggers.md]).

The session's directory arrives as a parameter and is never read from the process. The
capability documents run `python3 -m cairn` from the skill's own directory, so the process
working directory is Cairn's checkout rather than the person's repository — evidence of
nothing, and the trap that makes `os.getcwd()` worse than silence.

**The occasion defaults to a new one.** Continuing an old one is the direction that can act
on stale work, so it requires a positive signal: a recovery of a named run, or an occasion
supplied verbatim.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from functools import cache
from pathlib import Path
from typing import Literal, NamedTuple

from cairn.core import CairnError
from cairn.gitio import (
    common_directory,
    main_working_tree,
    refuse_unusable_repository,
    same_repository,
)
from cairn.marker import occasion_moment
from cairn.record.model import RunRecord
from cairn.skill.vocabulary import (
    CONSEQUENCE_BY_READING,
    OCCASION_CONTINUE,
    OCCASION_NEW,
    PROVENANCE_SESSION,
    PROVENANCE_STATED,
    PROVENANCE_SUBJECTS,
    READING_BY_TRIGGER,
    TRIGGER_PINNED,
    TRIGGER_RECOVERY,
    TRIGGER_SCHEDULED,
)
from cairn.topology import WORKTREES_SUFFIX, worktrees_parent
from cairn.workflow.schema import REPOSITORY_PARAM, declared_parameter, read

REPOSITORY_SUBJECTS_LOST = "subjects_lost"
REPOSITORY_SUBJECTS_SPLIT = "subjects_split"
REPOSITORY_SUBJECTS_DISAGREE = "subjects_disagree"
REPOSITORY_NOWHERE = "nowhere"
REPOSITORY_CAIRN_ITSELF = "cairn_itself"
REPOSITORY_MISMATCH = "mismatch"

# Every doubt worth a turn of the conversation, and nothing else is one. Enumerated so the
# corpus can be held to covering each: a question nobody can reach is a rule that does not
# exist, and a doubt with no question is a wrong repository nobody was asked about.
REPOSITORY_QUESTIONS: tuple[str, ...] = (
    REPOSITORY_SUBJECTS_LOST,
    REPOSITORY_SUBJECTS_SPLIT,
    REPOSITORY_SUBJECTS_DISAGREE,
    REPOSITORY_NOWHERE,
    REPOSITORY_CAIRN_ITSELF,
    REPOSITORY_MISMATCH,
)


class Resolved(NamedTuple):
    kind: Literal["resolved"]
    repository: Path
    provenance: str
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


def refuse_unstartable_spelling(stated: str) -> Path:
    """The repository a person typed, refused where the spelling itself lands nothing."""
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
    return Path(stated).resolve()


def holding_repository(path: Path) -> Path | None:
    """The repository `path` belongs to, or nothing where none can be found.

    The main working tree rather than the enclosing one, because a path inside a worktree
    belongs to the repository that worktree was added from — taking the worktree itself
    would nest `<repo>-worktrees` inside a tree Cairn created and key the lock on a
    directory the repository's other steps never see.
    """
    directory = path if path.is_dir() else path.parent
    try:
        return main_working_tree(directory)
    except CairnError:
        return None


def subject_repositories(
    subjects: Sequence[Path],
) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    """The repositories the request's subjects live in, and the subjects living in none."""
    held: list[Path] = []
    lost: list[Path] = []
    for subject in subjects:
        holder = holding_repository(subject)
        if holder is None:
            lost.append(subject)
        elif not any(same_repository(holder, seen) for seen in held):
            held.append(holder)
    return tuple(held), tuple(lost)


@cache
def cairn_checkout() -> Path | None:
    """The repository Cairn's own source is checked out in, where it is a checkout at all.

    Cached because it cannot change under a process, and asked at all because a session
    sitting here is the one candidate that is never evidence: every capability document runs
    `python3 -m cairn` from this directory.
    """
    return holding_repository(Path(__file__).resolve().parent)


def resolve_repository(
    stated: str | None,
    workflow: Path | None = None,
    *,
    subjects: Sequence[Path] = (),
    session: Path | None = None,
) -> Resolution:
    """Which repository this request is about, and which candidate answered.

    Strongest candidate first, and a question only where the ones that were found disagree
    or where none was found. Retyping a path the request and the session already agree on
    protects nothing; the disagreements below are where a person knows something Cairn
    cannot derive.
    """
    target = refuse_unstartable_spelling(stated) if stated is not None else None
    held, lost = subject_repositories(subjects)
    if lost:
        named = ", ".join(str(subject) for subject in lost)
        return Unresolved(
            kind="unresolved",
            outcome=REPOSITORY_SUBJECTS_LOST,
            question=(
                f"No git repository holds {named}, so what this request is about cannot say "
                "which repository it is about. Which repository should this run against?"
            ),
        )
    if len(held) > 1:
        named = ", ".join(str(holder) for holder in held)
        return Unresolved(
            kind="unresolved",
            outcome=REPOSITORY_SUBJECTS_SPLIT,
            question=(
                f"What this request is about is spread across {len(held)} repositories: "
                f"{named}. One run targets one repository — a branch is landed in it and "
                "every isolated step is a worktree of it. Which of them did you mean?"
            ),
        )
    subjects_root = held[0] if held else None

    if target is not None:
        if subjects_root is not None and not same_repository(subjects_root, target):
            return _disagreement(subjects_root, target, PROVENANCE_STATED)
        provenance = PROVENANCE_STATED
    elif subjects_root is not None:
        session_root = holding_repository(session) if session is not None else None
        if session_root is not None and not same_repository(session_root, subjects_root):
            return _disagreement(subjects_root, session_root, PROVENANCE_SESSION)
        target = subjects_root
        provenance = PROVENANCE_SUBJECTS
    else:
        session_root = holding_repository(session) if session is not None else None
        if session_root is None:
            return Unresolved(
                kind="unresolved",
                outcome=REPOSITORY_NOWHERE,
                question=(
                    "Nothing says which repository this is about: none was named, what it "
                    "is about names none, and "
                    + (
                        f"{session} is not inside a git repository"
                        if session is not None
                        else "this conversation is not in one either"
                    )
                    + ". Which repository should this run against?"
                ),
            )
        own = cairn_checkout()
        if own is not None and same_repository(own, session_root):
            return Unresolved(
                kind="unresolved",
                outcome=REPOSITORY_CAIRN_ITSELF,
                question=(
                    f"The only repository on offer is {session_root}, which is Cairn's own "
                    "checkout, and what you have asked about is not one of Cairn's plans. "
                    "Running here would branch, commit and land in Cairn itself. Which "
                    "repository should this run against?"
                ),
            )
        target = session_root
        provenance = PROVENANCE_SESSION

    refuse_unusable_repository(target)
    if workflow is None:
        return Resolved(
            kind="resolved", repository=target, provenance=provenance, encoded=None
        )

    encoded = encoded_repository(workflow)
    if encoded is None:
        return Resolved(
            kind="resolved", repository=target, provenance=provenance, encoded=None
        )
    if _same_repository(encoded, target):
        return Resolved(
            kind="resolved", repository=target, provenance=provenance, encoded=encoded
        )

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


# What each candidate is, in words, for the line every surface opens with. Total over
# PROVENANCES, asserted. One place phrases it so that five surfaces cannot describe one
# resolution five ways.
SENTENCE_BY_PROVENANCE: dict[str, str] = {
    PROVENANCE_STATED: "named in the request",
    PROVENANCE_SUBJECTS: "the repository holding what this is about",
    PROVENANCE_SESSION: "the repository this conversation is in",
}


def repository_line(resolved: Resolved) -> str:
    """The resolution, said back: which repository, and which candidate answered."""
    return (
        f"repository  {resolved.repository} — {resolved.provenance}, "
        f"{SENTENCE_BY_PROVENANCE[resolved.provenance]}"
    )


def _disagreement(subjects_root: Path, other: Path, provenance: str) -> Unresolved:
    """The question owed when the subjects' repository and another candidate differ.

    The subjects' repository is named first and named as what holds the work, because that
    is the evidence the person cannot see Cairn weighing.
    """
    whence = {
        PROVENANCE_STATED: "you named",
        PROVENANCE_SESSION: "this conversation is in",
    }[provenance]
    return Unresolved(
        kind="unresolved",
        outcome=REPOSITORY_SUBJECTS_DISAGREE,
        question=(
            f"What this request is about lives in {subjects_root}, and {other} is the "
            f"repository {whence}. A run branches, commits and lands in one repository, and "
            "its records are filed under the one it was started against. Which did you "
            f"mean — {subjects_root}, or {other}?"
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


def refuse_foreign_recovery(
    record: RunRecord, *, run_id: str, plan: str, graph_sha256: str | None
) -> None:
    """Refuse a recovery whose run and whose workflow are not one lineage.

    A recovery continues the named run's occasion but executes the workflow it is started
    through. Through another plan's workflow, or through this plan's after it was
    re-authored from different content, the markers that occasion left would be read by
    steps that are not the steps that wrote them — so the run's own record must name the
    plan and the graph being started, and a record that cannot say is refused as one that
    disagrees.
    """
    recorded_plan = record["plan"]
    if recorded_plan != plan:
        raise CairnError(
            "invalid_arguments",
            f"run {run_id} is a run of plan {recorded_plan!r}, not {plan!r}; recovering it "
            "through another plan's workflow would continue one occasion with another "
            f"plan's steps. Recover it with --plan {recorded_plan}"
            if recorded_plan
            else f"run {run_id} recorded no plan, so it cannot be established that "
            f"{plan!r} is the plan it ran; there is nothing to recover it through",
        )
    recorded_graph = record["graph_sha256"]
    if recorded_graph is None:
        raise CairnError(
            "invalid_arguments",
            f"run {run_id} recorded no graph digest, so it cannot be established that the "
            f"workflow now published for {plan!r} holds the steps that run had; start a "
            "fresh run instead",
        )
    if recorded_graph != graph_sha256:
        raise CairnError(
            "invalid_arguments",
            f"run {run_id} ran plan {plan!r} as graph "
            f"{recorded_graph[:12]}, and the workflow now published for "
            f"it was generated from graph {(graph_sha256 or 'unrecorded')[:12]}. A recovery "
            "continues the run it names with the steps that run had; start a fresh run of "
            "the re-authored plan instead",
        )


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
    """Whether this trigger mints an occasion or continues one, and what that means.

    A new occasion redoes every scoped step; a continued one may act on work whose answer
    has moved. Both are the operator's to decide rather than Cairn's to infer, so where the
    invocation does not settle it the decision is disclosed with both consequences.
    """
    if signal.pinned is not None and signal.trigger != TRIGGER_PINNED:
        raise CairnError(
            "invalid_occasion",
            f"an occasion was given with a {signal.trigger!r} trigger, which does not "
            "continue one. Dropping it would silently redo every run-scoped step; "
            "pass --trigger pinned to continue that occasion",
        )
    if signal.named_run is not None and signal.trigger != TRIGGER_RECOVERY:
        raise CairnError(
            "invalid_occasion",
            f"a run was named with a {signal.trigger!r} trigger, which recovers nothing. "
            "Pass --trigger recovery to continue that run",
        )
    reading = READING_BY_TRIGGER[signal.trigger]
    forgone = CONSEQUENCE_BY_READING[
        OCCASION_CONTINUE if reading == OCCASION_NEW else OCCASION_NEW
    ]
    taken = CONSEQUENCE_BY_READING[reading]

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
            # Minting here would present as a recovery while silently redoing every scoped
            # step, which is the wrong answer the operator would not see.
            raise CairnError(
                "invalid_occasion",
                f"run {signal.named_run} recorded no occasion, so there is nothing to "
                "continue. Starting a fresh run instead redoes every run-scoped step; say "
                "so explicitly if that is what you want",
            )
        occasion_moment(recorded)
        return OccasionDecision(reading, recorded, True, taken, forgone)

    # A first run of a plan has no other reading available, so stating one would be noise.
    # Naming a workflow that has run before is the one genuinely silent choice, and there
    # Cairn takes a new occasion and says so.
    return OccasionDecision(reading, None, signal.prior_runs > 0, taken, forgone)


__all__ = [
    "REPOSITORY_CAIRN_ITSELF",
    "REPOSITORY_MISMATCH",
    "REPOSITORY_NOWHERE",
    "REPOSITORY_QUESTIONS",
    "REPOSITORY_SUBJECTS_DISAGREE",
    "REPOSITORY_SUBJECTS_LOST",
    "REPOSITORY_SUBJECTS_SPLIT",
    "SENTENCE_BY_PROVENANCE",
    "OccasionDecision",
    "OccasionSignal",
    "Resolution",
    "Resolved",
    "Unresolved",
    "cairn_checkout",
    "decide_occasion",
    "encoded_repository",
    "holding_repository",
    "refuse_foreign_recovery",
    "refuse_unstartable_spelling",
    "repository_line",
    "resolve_repository",
    "subject_repositories",
]
