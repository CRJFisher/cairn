"""The wave's census, taken once, at the only moment it can be taken.

A merge slot decides what to land from evidence that does not exist until run time, and
landing a branch makes it an ancestor of the parent — which is exactly what a branch that
never carried work looks like. So after the first slot runs, nothing can tell an excluded
step from a landed one ([merge-step.md]). The join stands before any landing and depends on
every commit in its wave, so it is the one node that sees the whole wave intact.

It answers one question and takes no action: which branches arrived with work, and for each
that did not, the cause the gate itself recorded. The exclusion vocabulary is frozen
elsewhere ([verify-gate.md]) and is quoted rather than re-spelled, because a second word for
one event would put two answers in the run record.
"""

from __future__ import annotations

from collections.abc import Sequence

from cairn.core import EXIT_OK, CommandResult, RuntimeContext
from cairn.merge import EXCLUDED, MERGEABLE, NOTHING_TO_MERGE, survey


def run_join(
    wave: int, branches: Sequence[str], into: str, context: RuntimeContext
) -> CommandResult:
    """Record what this wave produced, and never refuse.

    A wave whose every branch was excluded still reaches its merge slots, which each report
    a no-op, and still reaches its prune. Refusing here would abort the slots behind it and
    strand the branches that did verify — the same failure `continue_on: {failure: true}`
    exists to prevent one node earlier.
    """
    candidates = survey(
        context.working_directory,
        list(branches),
        into,
        context.report_path.parent,
        context.run_id,
    )
    arrived = [c.branch for c in candidates if c.disposition == MERGEABLE]
    # Only a branch the gate declined is an exclusion. A branch already contained in the
    # parent carries no cause — it landed on an earlier run, or its step had nothing to
    # commit — and recording it here would put an invented cause in the one census that
    # cannot be taken again ([merge-step.md]).
    excluded = {
        c.branch: {"cause": c.cause, "summary": c.summary}
        for c in candidates
        if c.disposition == EXCLUDED
    }
    settled = [c.branch for c in candidates if c.disposition == NOTHING_TO_MERGE]
    summary = (
        f"wave {wave}: {len(arrived)} of {len(candidates)} branches carry work to land"
    )
    return CommandResult(
        EXIT_OK,
        "done" if arrived else "noop",
        summary,
        [],
        False,
        None,
        {
            "wave": wave,
            "into": into,
            "arrived": arrived,
            "excluded": excluded,
            "settled": settled,
        },
    )


__all__ = ["run_join"]
