"""What a settled run found and did not do, read from its record and nothing else.

Every step report carries `follow_up_work` — the work the session found and left — and the
record keeps it per step beside the step that surfaced it ([model.py]). This reads that back
as a list a person can act on, with each item's provenance, and it is the whole of the
harvest: it starts nothing, decides nothing, and gives an agent's account no authority it did
not already have. What becomes of the items is a plan document somebody confirms
([cairn/plan/draft.py]).

**The record is the only source.** Reading the reports directly would mean a second extraction
with its own idea of which report speaks for a step, and the record already answers that
([extract.py]). It also means a harvest can only be taken of a run that has a record.

**A run still running refuses.** Its remaining steps have not reported yet, so its follow-up
work is a partial list that reads like a whole one — and a sequence that closes when a harvest
comes back empty would close on a run that had not finished speaking.
"""

from __future__ import annotations

from typing import NamedTuple

from cairn.core import CairnError
from cairn.record.model import RunRecord
from cairn.record.vocabulary import EXIT_UNFINISHED, VERDICT_RUNNING


class FollowUp(NamedTuple):
    """One piece of work a step found and did not do, and which step found it.

    `said` is that step's own summary line, carried beside the item because an item written
    for the person who read the step's report is often unreadable without it.
    """

    item: str
    step_id: str
    step_slug: str | None
    step_title: str | None
    said: str | None


class Harvest(NamedTuple):
    """One settled run's follow-up work, in the order its steps are recorded in."""

    run_id: str
    plan: str | None
    verdict: str
    items: tuple[FollowUp, ...]


def harvest(record: RunRecord) -> Harvest:
    """Collect this run's follow-up work, or refuse while the run is still in flight."""
    if record["verdict"] == VERDICT_RUNNING:
        raise CairnError(
            "run_still_running",
            f"run {record['run_id']} is {VERDICT_RUNNING}, and a harvest of a run still "
            "working is a partial list that reads like a whole one; take it once the run "
            "has settled",
            exit_code=EXIT_UNFINISHED,
        )
    return Harvest(
        run_id=record["run_id"],
        plan=record["plan"],
        verdict=record["verdict"],
        items=tuple(
            FollowUp(
                item=item,
                step_id=step["step_id"],
                step_slug=step["slug"],
                step_title=step["title"],
                said=step["said"],
            )
            for step in record["steps"]
            for item in step["follow_up_work"]
        ),
    )


__all__ = ["FollowUp", "Harvest", "harvest"]
