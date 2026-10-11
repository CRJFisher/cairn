"""Finding a run again: what a repository has run, and which run of a plan was the last.

Every other reader here starts from a run id. A person does not have one — they have the
plan's name and a question about the last time it ran, and until this existed the only way
to answer it was to list a directory of opaque identifiers and build records until one
looked right ([29 B]).

So there are two answers and they are different questions. A **listing** is every run this
repository holds, newest first, filtered by plan where one is named. A **latest resolver**
is one run, chosen by a rule stated once here rather than by whichever entry a reader's eye
lands on: the newest readable run of that plan by the moment it started, and where two
started in the same second, the later run id.

**Nothing unreadable is dropped.** A run whose record cannot be read is exactly the run
somebody is looking for, and a listing that silently omitted it would answer "that plan has
never run" about a run that failed badly enough to damage its own record. Each such entry is
listed with what is wrong with it and what to run — and it is never what the latest resolver
returns, because a record that cannot be read cannot say how the run went.

Read-only throughout: no record is built, nothing is written, and the engine is not asked
anything. A run's record is the file its own release left behind ([store.py]).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, NamedTuple, cast

from cairn.core import CairnError, read_standard_json
from cairn.layout import RUN_ID, reports_directory
from cairn.record.store import read_record
from cairn.text import LINE_LIMIT, flatten
from cairn.topology import node_name

# The node that records which plan a run is executing, read where the record itself cannot
# be. It is the run's first act, so it exists for every run that got as far as starting.
LOCK_NODE = node_name("lock", "acquire")

REBUILD = "`cairn record build --run {run}` rebuilds it"
NEVER_BUILT = "no record has been built for this run yet"


class RunEntry(NamedTuple):
    """One run as a listing names it, whether or not its record could be read.

    `fault` is what is wrong with this entry, and it is the field that decides what the rest
    of them mean: where it is set, the verdict and the moments are whatever could still be
    established — never a verdict inferred to fill the gap.
    """

    run_id: str
    plan: str | None
    started_at: str | None
    finished_at: str | None
    trigger: str | None
    verdict: str | None
    fault: str | None


def _plan_of_an_unreadable_run(runs_root: Path, run_id: str) -> str | None:
    """Which plan a run was, where its record cannot say.

    Read straight from the lock's own report rather than through the record pipeline: the
    point of reaching for it is that the pipeline already refused, and a plan-filtered
    listing that could not place such a run would hide it from the one question it answers.
    """
    path = reports_directory(runs_root, run_id) / f"{LOCK_NODE}.json"
    try:
        report: Any = read_standard_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(report, dict):
        return None
    detail: Any = cast(dict[str, Any], report).get("detail")
    if not isinstance(detail, dict):
        return None
    found: Any = cast(dict[str, Any], detail).get("plan")
    return flatten(found, limit=LINE_LIMIT) if isinstance(found, str) and found else None


def _entry(runs_root: Path, run_id: str) -> RunEntry | None:
    """One directory under the runs root as an entry, or nothing where it names no run.

    A directory with neither a record nor a lock report is not a run of a plan: the engine's
    own authoring rehearsal evaluates every precondition, and Cairn's gates write their
    reports under whatever run id it minted for the dry pass ([workflow/gate.py]). Listing
    those as runs with no record would bury every real one under them.
    """
    lock = reports_directory(runs_root, run_id) / f"{LOCK_NODE}.json"
    try:
        record = read_record(runs_root, run_id)
    except CairnError as refused:
        return RunEntry(
            run_id=run_id,
            plan=_plan_of_an_unreadable_run(runs_root, run_id),
            started_at=None,
            finished_at=None,
            trigger=None,
            verdict=None,
            fault=f"{refused.cause}: {flatten(refused, limit=LINE_LIMIT)}",
        )
    if record is None:
        if not lock.exists():
            return None
        return RunEntry(
            run_id=run_id,
            plan=_plan_of_an_unreadable_run(runs_root, run_id),
            started_at=None,
            finished_at=None,
            trigger=None,
            verdict=None,
            fault=f"{NEVER_BUILT}; {REBUILD.format(run=run_id)}",
        )
    return RunEntry(
        run_id=record["run_id"],
        plan=record["plan"],
        started_at=record["started_at"],
        finished_at=record["finished_at"],
        trigger=record["trigger"]["kind"],
        verdict=record["verdict"],
        fault=None,
    )


def _order(entry: RunEntry) -> tuple[str, str]:
    """Newest last, by the moment the run started and then by its own identity.

    A run whose record carries no start moment sorts by its id alone, which for a run Cairn
    named is still chronological — the identity is minted from the clock ([skill/cli.py]).
    """
    return (entry.started_at or "", entry.run_id)


def list_runs(runs_root: Path, *, plan: str | None = None) -> list[RunEntry]:
    """Every run this repository holds, newest first, of this plan where one is named.

    An entry whose plan could not be established at all is kept in a filtered listing. It
    may be the run that was asked for, and the alternative is answering a question about a
    plan by hiding the evidence that it ran.
    """
    if not runs_root.is_dir():
        return []
    entries = [
        entry
        for path in sorted(runs_root.iterdir())
        if path.is_dir() and RUN_ID.fullmatch(path.name) is not None
        if (entry := _entry(runs_root, path.name)) is not None
    ]
    if plan is not None:
        entries = [
            entry for entry in entries if entry.plan == plan or entry.plan is None
        ]
    return sorted(entries, key=_order, reverse=True)


def latest_run(runs_root: Path, *, plan: str) -> RunEntry | None:
    """The newest run of this plan whose record can be read, or nothing where there is none.

    Readable, because the answer is "how did it go": a run whose record was refused has no
    verdict to report, and returning it would make the resolver's answer depend on damage
    rather than on recency. Those runs are in the listing beside this one, which is where a
    reader learns that a newer run exists and could not be read.
    """
    readable = [
        entry
        for entry in list_runs(runs_root, plan=plan)
        if entry.fault is None and entry.plan == plan
    ]
    return readable[0] if readable else None


def unreadable_runs(runs_root: Path, *, plan: str | None = None) -> list[RunEntry]:
    """Every entry a reader has to know about before trusting the answer above."""
    return [entry for entry in list_runs(runs_root, plan=plan) if entry.fault is not None]


__all__ = [
    "LOCK_NODE",
    "NEVER_BUILT",
    "RunEntry",
    "latest_run",
    "list_runs",
    "unreadable_runs",
]
