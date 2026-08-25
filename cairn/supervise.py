"""Reconciling a killed run's engine record.

A run killed without a scheduler up stays `Running` with no finish time forever: `dagu
retry` refuses it as already running, `dagu stop` reports success while changing nothing,
and clearing the socket and process file does not help. The block lives in the status
record, so that is where the repair goes.

The obvious cure — leave a scheduler running, which reconciles zombies for free — is worse
than the disease, and refusing it is [baseconfig.py](baseconfig.py)'s job.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple, cast

from cairn.core import CairnError
from cairn.liveness import process_is_alive

STATUS_FILE = "status.jsonl"

# The engine's numeric run and node statuses. Only the two this document acts on are
# named: a record frozen at `running` is the crash, and `failed` is the terminal value
# that unblocks `dagu retry`.
STATUS_RUNNING = 1
STATUS_FAILED = 2

RECONCILED_ERROR = "cairn: run reconciled after its process was found gone"

ALREADY_TERMINAL = "already terminal"
NO_RECORD = "no readable record"
OWNER_UNKNOWN = "owner unknown, left alone"
RECONCILED = "reconciled to failed"
STILL_RUNNING = "still running"
UNRECOGNISED = "unrecognised record shape, left alone"
WOULD_RECONCILE = "would reconcile"


class Reconciliation(NamedTuple):
    """What one attempt's record needed, and what was done about it."""

    path: Path
    changed: bool
    verdict: str


def _timestamp() -> str:
    """The engine's own timestamp spelling, so a repaired line reads like a written one."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def last_record(path: Path) -> dict[str, Any] | None:
    """The final valid snapshot in an append-only status file.

    Every line is a whole snapshot rather than a diff, and the file is compacted to one
    line when the attempt closes, so a cold reader scans to the end and takes the last
    line that parses — never a fixed tail. A line the kill cut mid-character decodes to
    replacement characters, fails to parse, and is skipped like any other damage.
    """
    found: dict[str, Any] | None = None
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    parsed: Any = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    found = cast(dict[str, Any], parsed)
    except OSError as exc:
        raise CairnError(
            "run_record_unreadable",
            f"{path}: {exc.strerror}",
            detail={"path": str(path)},
        ) from exc
    return found


def find_status_files(root: Path) -> Iterator[Path]:
    """Every attempt record under a run directory, a DAG's history, or a whole data root."""
    if not root.exists():
        raise CairnError(
            "run_record_unreadable",
            f"{root} does not exist, so there is nothing to reconcile there",
            detail={"path": str(root)},
        )
    if root.is_file():
        yield root
        return
    yield from sorted(root.rglob(STATUS_FILE))


def find_run_record(root: Path, run_id: str) -> Path | None:
    """The status file for one run, found by the identity it carries, not by its path.

    The engine's directory layout carries no external contract, so the run id inside the
    record is what confirms a match and the layout only narrows the search.
    """
    if not root.is_dir():
        return None
    for candidate in sorted(root.rglob(STATUS_FILE), reverse=True):
        try:
            record = last_record(candidate)
        except CairnError:
            continue
        if record is not None and record.get("dagRunId") == run_id:
            return candidate
    return None


def owner_liveness(record: dict[str, Any]) -> bool | None:
    """Whether the recording process is alive, or None when the record does not say.

    A record that names no usable process is not evidence of death. Treating it as one
    would write a terminal status into a run that is still going — the mirror of the
    failure this whole module exists to correct, and the more damaging direction.
    """
    pid = record.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int):
        return None
    started_at_ms = record.get("pidStartedAt")
    started_at = (
        started_at_ms / 1000.0
        if isinstance(started_at_ms, (int, float)) and not isinstance(started_at_ms, bool)
        else None
    )
    return process_is_alive(pid, started_at)


def _append(path: Path, record: dict[str, Any]) -> None:
    """Append one snapshot in a single write.

    Real records reach ten kilobytes, past the buffer a text handle would split them at,
    and two reconcilers appending at once would interleave their halves and leave neither
    line parseable — which reads back as the original `running` snapshot.
    """
    blob = (json.dumps(record, separators=(",", ":")) + "\n").encode("utf-8")
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(descriptor, blob)
    finally:
        os.close(descriptor)


def reconcile_status_file(path: Path, *, dry_run: bool = False) -> Reconciliation:
    """Give a killed run's record a terminal status, deciding liveness from the process.

    The status field is never the evidence — after a crash it says `running` forever. The
    recorded process identifier and its start time are, so a recycled identifier cannot
    make a dead run look alive.
    """
    record = last_record(path)
    if record is None:
        return Reconciliation(path, False, NO_RECORD)
    if record.get("status") != STATUS_RUNNING:
        return Reconciliation(path, False, ALREADY_TERMINAL)
    nodes: Any = record.get("nodes")
    if nodes is not None and not isinstance(nodes, list):
        return Reconciliation(path, False, UNRECOGNISED)
    alive = owner_liveness(record)
    if alive is None:
        return Reconciliation(path, False, OWNER_UNKNOWN)
    if alive:
        return Reconciliation(path, False, STILL_RUNNING)
    if dry_run:
        return Reconciliation(path, False, WOULD_RECONCILE)

    finished = _timestamp()
    repaired = dict(record)
    repaired["status"] = STATUS_FAILED
    repaired["finishedAt"] = finished
    repaired["error"] = RECONCILED_ERROR
    if isinstance(nodes, list):
        repaired["nodes"] = [
            _reconcile_node(cast(dict[str, Any], node), finished)
            if isinstance(node, dict)
            else node
            for node in cast(list[Any], nodes)
        ]
    # Appended rather than rewritten: the file is the engine's own append-only log, and a
    # reader takes the last line that parses, so the repair is the record from here on.
    _append(path, repaired)
    return Reconciliation(path, True, RECONCILED)


def _reconcile_node(node: dict[str, Any], finished: str) -> dict[str, Any]:
    if node.get("status") != STATUS_RUNNING:
        return node
    repaired = dict(node)
    repaired["status"] = STATUS_FAILED
    repaired["finishedAt"] = finished
    repaired["error"] = RECONCILED_ERROR
    return repaired


def reconcile(root: Path, *, dry_run: bool = False) -> list[Reconciliation]:
    """Reconcile every attempt record under `root`.

    One unreadable file costs its own verdict and nothing more: a sweep that stopped at the
    first damaged record would leave the rest of a crashed machine unrepaired, which is
    exactly the state it was called to clear.
    """
    results: list[Reconciliation] = []
    for path in find_status_files(root):
        try:
            results.append(reconcile_status_file(path, dry_run=dry_run))
        except CairnError as exc:
            results.append(Reconciliation(path, False, f"unreadable: {exc}"))
    return results


__all__ = [
    "ALREADY_TERMINAL",
    "NO_RECORD",
    "OWNER_UNKNOWN",
    "RECONCILED",
    "RECONCILED_ERROR",
    "STATUS_FAILED",
    "STATUS_RUNNING",
    "STILL_RUNNING",
    "UNRECOGNISED",
    "WOULD_RECONCILE",
    "Reconciliation",
    "find_run_record",
    "find_status_files",
    "last_record",
    "owner_liveness",
    "reconcile",
    "reconcile_status_file",
]
