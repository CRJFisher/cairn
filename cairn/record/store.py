"""Where a run's record is written, and how a killed refresh cannot leave half of one.

The record is regenerable: it is derived from the engine's state and this run's own reports,
both of which outlive it, so losing it costs a rebuild and nothing else. What it must never
do is exist in a truncated state, because a reader has no way to tell a short record from a
run that did little.

`core.write_json` already gives exactly that — a temporary file in the target directory,
fsynced, then `os.replace` — so this module adds the sweep that a `kill -9` needs and
nothing else. A crash cannot unwind, so a fragment it leaves behind is cleared by the next
writer rather than by the one that died.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from cairn.core import CairnError, write_json
from cairn.layout import RECORD_FILE, record_path, reports_directory
from cairn.record.engine import find_attempts
from cairn.record.extract import extract, read_reports
from cairn.record.model import RunRecord
from cairn.record.vocabulary import RECORD_VERSION


def _sweep(path: Path) -> None:
    for stale in path.parent.glob(f".{RECORD_FILE}.*.tmp"):
        try:
            stale.unlink()
        except OSError:
            pass


def write_record(runs_root: Path, record: RunRecord) -> Path:
    """Replace this run's record atomically, leaving the previous one whole if anything fails."""
    path = record_path(runs_root, record["run_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    _sweep(path)
    write_json(path, cast(dict[str, Any], record))
    return path


def build_run_record(
    runs_root: Path,
    records: Path,
    run_id: str,
    *,
    in_flight_node: str | None = None,
    in_flight_cause: str | None = None,
) -> RunRecord | None:
    """Assemble one run's record from the engine's state and this run's own reports.

    One statement of the pipeline, because there are two callers and they must not drift: a
    person asking `cairn record build`, and the run's own release writing the record nobody
    was there to ask for ([triggers.md]). None where neither source holds anything about
    this run at all.
    """
    attempts = find_attempts(records, run_id)
    reports = read_reports(reports_directory(runs_root, run_id), run_id)
    if not attempts and not reports:
        return None
    return extract(
        attempts[-1].record if attempts else None,
        reports,
        run_id=run_id,
        attempt_count=max(len(attempts), 1),
        in_flight_node=in_flight_node,
        in_flight_cause=in_flight_cause,
    )


def read_record(runs_root: Path, run_id: str) -> RunRecord | None:
    """One run's record as it was last written, or None where none has been."""
    path = record_path(runs_root, run_id)
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise CairnError("run_record_unreadable", f"{path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise CairnError("run_record_unreadable", f"{path}: expected an object")
    found = cast(dict[str, Any], raw).get("record_version")
    if found != RECORD_VERSION:
        # The record is regenerable from state that outlives it, so an older one is rebuilt
        # rather than read through — reading it would hand a caller a shape missing fields
        # the model requires, which is a lie about what was measured.
        raise CairnError(
            "run_record_unreadable",
            f"{path} records version {found!r} and this Cairn writes {RECORD_VERSION}; "
            "rebuild it with `cairn record build`",
        )
    return cast(RunRecord, raw)


__all__ = ["build_run_record", "read_record", "write_record"]
