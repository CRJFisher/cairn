"""The scheduler, which is the one daemon that is dangerous, and what it costs to start it.

There are two engine daemons and only one of them carries a hazard. The **view's** server
binds loopback, holds no run state, and reads the same files the CLI writes; killing it and
starting another loses nothing. The **scheduler** is different, and every recurring trigger
costs it — a cron schedule obviously, and an external webhook non-obviously, because a
webhook does not execute a run, it enqueues one, and the queue is drained by the scheduler.

While it is up it does two things unasked. It reconciles crashed runs, which Cairn wants.
And its retry scanner **re-executes every failed run recorded on the machine inside the
window `RETRY_SCANNER_HOURS` names** — including runs outside the directory it watches,
three attempts each, under the engine's own retry policy. For a tool whose failed runs are paid agent sessions against git
repositories that is unacceptable, so starting it asserts two machine-wide properties rather
than assuming them, and names what it found.

Both hazards live in one file every DAG on the machine inherits. Retry is closed there
([baseconfig.py]); catchup is closed on every file Cairn emits and only *asserted* there,
because the scanner reaches DAGs Cairn did not write:

- **DAG-level retry**, which replays a failed run ([baseconfig.py]).
- **Catchup**, which replays every cron slot missed while the machine slept. Measured on
  this machine the engine ships `catchup_window: "6h"`, and its own comment reads "all
  missed cron intervals within this window are executed (max 1000)". Every emitted file
  states the empty window that turns it off ([workflow.md]), so this is a hazard only for
  the DAGs Cairn did not write — which is exactly what the scanner reaches.

The assertion runs **at start**, not at install. A machine that was safe when a schedule was
installed is not evidence about the machine a month later, and this is the only moment the
hazard can actually fire.
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple

from cairn.baseconfig import (
    assert_catchup_disabled,
    assert_dag_retry_disabled,
    base_config_path,
)
from cairn.core import CairnError
from cairn.enginehome import ENGINE_BINARY, dags_directory, run_records_path
from cairn.plan.ids import is_plan_slug
from cairn.record import engine
from cairn.supervise import find_status_files, last_record
from cairn.workflow.schema import WORKFLOW_SUFFIX
from cairn.workflow.stamp import workflow_path

# The window the engine's own retry scanner sweeps. A refusal names the runs inside it, so
# the number is stated here rather than described.
RETRY_SCANNER_HOURS = 24


class EngineRun(NamedTuple):
    """One run the engine recorded, named the way a refusal has to name it."""

    run_id: str
    dag: str
    status: str
    finished_at: str | None


def _runs(root: Path) -> list[EngineRun]:
    """Every run the engine has recorded, or none on a machine that has never run one.

    An absent history is a refusal for `supervise reconcile`, which was asked to repair
    something; here it is the honest answer to "what would a scheduler re-execute", and
    refusing would make a fresh machine the one place the safety check cannot run.
    """
    if not root.exists():
        return []
    found: list[EngineRun] = []
    for path in find_status_files(root):
        try:
            record = last_record(path)
        except CairnError:
            continue
        if record is None:
            continue
        run_id = engine.text(record.get("dagRunId"))
        raw: Any = record.get("status")
        if run_id is None or not isinstance(raw, int):
            continue
        try:
            status = engine.run_status_name(raw)
        except CairnError:
            # A status this engine version does not map is still a run, and a refusal that
            # dropped it would under-report exactly the thing it exists to count.
            status = str(raw)
        found.append(
            EngineRun(
                run_id=run_id,
                dag=engine.text(record.get("name")) or "?",
                status=status,
                finished_at=engine.moment(record.get("finishedAt")),
            )
        )
    return found


def failed_runs_since(
    root: Path, *, hours: int = RETRY_SCANNER_HOURS, now: float | None = None
) -> list[EngineRun]:
    """Every failed run the retry scanner would reach.

    A run whose finish time cannot be read counts as inside the window rather than outside
    it. The scanner's own reading is what decides, and under-naming a run that is about to
    be re-executed is the one error this list must not make.
    """
    moment = time.time() if now is None else now
    cutoff = moment - hours * 3600
    inside: list[EngineRun] = []
    for run in _runs(root):
        if run.status != engine.RUN_FAILED:
            continue
        finished = _seconds(run.finished_at)
        if finished is None or finished >= cutoff:
            inside.append(run)
    # Unknown-finish runs first, so the elision can never drop the very runs this refuses
    # to under-name; the rest newest first, because those are the ones a person triggering
    # now will recognise.
    unknown = [run for run in inside if run.finished_at is None]
    known = sorted(
        (run for run in inside if run.finished_at is not None),
        key=lambda run: run.finished_at or "",
        reverse=True,
    )
    return [*unknown, *known]


def queued_runs(root: Path) -> list[EngineRun]:
    """Every run sitting enqueued with nothing draining it.

    This is what an external trigger produces when no scheduler is up: the webhook returned
    success, the run exists, and it will sit there indefinitely. It is the only way a person
    can see a trigger that was accepted and does nothing.
    """
    return [run for run in _runs(root) if run.status == engine.RUN_QUEUED]


def _seconds(moment: str | None) -> float | None:
    if not moment:
        return None
    try:
        return datetime.fromisoformat(moment).timestamp()
    except (ValueError, OSError, OverflowError):
        return None


NAMED_LIMIT = 20


def _named(runs: list[EngineRun]) -> str:
    """The runs a refusal spells out, saying so when it stops short of all of them."""
    shown = "; ".join(describe_run(run) for run in runs[:NAMED_LIMIT])
    if len(runs) <= NAMED_LIMIT:
        return shown
    return f"{shown}; and {len(runs) - NAMED_LIMIT} more"


def describe_run(run: EngineRun) -> str:
    """One line naming a run, used verbatim in a refusal."""
    when = f" finished {run.finished_at}" if run.finished_at else ""
    return f"{run.run_id} of {run.dag}{when}"


def assert_safe_to_start(
    *,
    base_config: Path | None = None,
    records: Path | None = None,
    now: float | None = None,
) -> list[EngineRun]:
    """Refuse to start a scheduler on a machine it would do damage from, naming the damage.

    Returns the queued runs it found, which is what the caller reports as the work this
    scheduler is about to drain.
    """
    config = base_config_path() if base_config is None else base_config
    # Judged before the records root is resolved, because resolving it asks the engine — and
    # invoking the engine creates this very file with retry active ([enginehome.py]). Reading
    # where things are must not arm the hazard the reader is about to judge.
    try:
        assert_dag_retry_disabled(config)
    except CairnError as exc:
        # Naming the runs means asking the engine where its history is, and asking creates
        # the very configuration being refused — so on a machine with no base config yet
        # the refusal stands alone rather than arming the hazard to describe it.
        countable = records is not None or config.exists()
        would = (
            failed_runs_since(
                run_records_path() if records is None else records, now=now
            )
            if countable
            else []
        )
        named = _named(would)
        # The holder's own message already names the file and the command that fixes it, so
        # what is added here is the only thing it cannot know: what starting now would cost.
        counted = (
            f" Starting a scheduler now would re-execute {len(would)} failed run(s) "
            f"recorded in the last {RETRY_SCANNER_HOURS} hours"
            + (f": {named}." if named else ".")
            if countable
            else ""
        )
        raise CairnError(
            exc.cause,
            f"{exc}.{counted}",
            detail={**exc.detail, "would_re_execute": [run.run_id for run in would]},
        ) from exc
    assert_catchup_disabled(config)
    return queued_runs(run_records_path() if records is None else records)


def published_path(plan: str, *, dags: Path | None = None) -> Path:
    """Where a plan's definition has to be reachable from for a schedule to fire at all.

    The scheduler watches one directory and Cairn writes into the repository's own admin
    directory, so a file carrying a schedule that never reached here fires never, silently.
    """
    root = dags_directory() if dags is None else dags
    # The plan reaches here from a command line and becomes a filename in a directory
    # shared with every other job on the machine, so it is held to the grammar a plan slug
    # already has — which is a single lower-case segment, and cannot spell a separator or a
    # parent directory. The engine's node-name rules are not that grammar: they forbid the
    # hyphen every real plan slug carries.
    if not is_plan_slug(plan):
        raise CairnError(
            "invalid_arguments",
            f"{plan!r} is not a plan slug, so it cannot name a file in the watched "
            "directory",
            detail={"plan": plan},
        )
    return root / f"{plan}{WORKFLOW_SUFFIX}"


def install(repository: Path, plan: str, *, dags: Path | None = None) -> Path:
    """Make a plan's definition reachable by the scheduler, without copying it.

    A symlink rather than a copy — measured, the engine resolves a linked definition by name
    — so the workflow keeps one source of truth and re-authoring is picked up without a
    second install. The engine's name for the DAG is this link's filename, which is what the
    view's URL and any webhook endpoint are keyed on.

    A name already taken by something that is not this plan's own link is refused rather
    than replaced: two repositories whose plans share a slug would otherwise fork one DAG
    history between them, and the second install would silently retarget the first.
    """
    source = workflow_path(repository, plan)
    if not source.exists():
        raise CairnError(
            "invalid_arguments",
            f"{source} does not exist, so there is no definition to schedule; author the "
            "plan first",
            detail={"plan": plan, "workflow": str(source)},
        )
    target = published_path(plan, dags=dags)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() and target.readlink() == source:
        return target
    if target.exists() or target.is_symlink():
        raise CairnError(
            "invalid_arguments",
            f"{target} already exists and does not point at {source}. Two plans sharing a "
            "slug would fork one DAG history between them; rename one, or remove that "
            "entry first",
            detail={"plan": plan, "published": str(target)},
        )
    target.symlink_to(source)
    return target


def remove(repository: Path, plan: str, *, dags: Path | None = None) -> Path | None:
    """Stop a plan firing on its schedule, leaving the definition itself alone.

    Only this plan's own link is removed. The watched directory is the machine's, holding
    whatever else a person keeps there, and `install` already refuses to write over an entry
    it did not make — a remove that deleted one would destroy the file that refusal exists to
    protect, on the command the refusal tells a person to run.
    """
    target = published_path(plan, dags=dags)
    if not target.is_symlink():
        if target.exists():
            raise CairnError(
                "invalid_arguments",
                f"{target} is not a link Cairn installed, so removing it would delete a "
                "definition Cairn did not publish",
                detail={"plan": plan, "published": str(target)},
            )
        return None
    source = workflow_path(repository, plan)
    if target.readlink() != source:
        raise CairnError(
            "invalid_arguments",
            f"{target} points at {target.readlink()}, not at {source}; it belongs to "
            "another repository's plan of the same name",
            detail={"plan": plan, "published": str(target)},
        )
    target.unlink()
    return target


def installed(*, dags: Path | None = None) -> list[Path]:
    """Every definition reachable from the watched directory, whoever put it there."""
    root = dags_directory() if dags is None else dags
    if not root.is_dir():
        return []
    return sorted(path for path in root.glob(f"*{WORKFLOW_SUFFIX}"))


def scheduler_command(*, dags: Path | None = None) -> list[str]:
    """The engine invocation this command execs, stated so a person can run it themselves."""
    root = dags_directory() if dags is None else dags
    return [ENGINE_BINARY, "scheduler", "--dags", str(root)]


def start(*, dags: Path | None = None) -> None:
    """Become the scheduler, having asserted the machine is safe to be one on.

    `execvp` rather than a child, so the daemon inherits this process rather than gaining a
    supervisor Cairn would then have to keep alive — a signal sent to it reaches the engine,
    and Cairn leaves no process tree behind.
    """
    command = scheduler_command(dags=dags)
    os.execvp(command[0], command)


__all__ = [
    "RETRY_SCANNER_HOURS",
    "EngineRun",
    "assert_safe_to_start",
    "describe_run",
    "failed_runs_since",
    "install",
    "installed",
    "published_path",
    "queued_runs",
    "remove",
    "scheduler_command",
    "start",
]
