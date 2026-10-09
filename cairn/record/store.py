"""Where a run's record is written, how a killed refresh cannot leave half of one, and what
a stored one has to be before it is handed back.

The record is regenerable: it is derived from the engine's state and this run's own reports,
both of which outlive it, so losing it costs a rebuild and nothing else. What it must never
do is exist in a truncated state, because a reader has no way to tell a short record from a
run that did little.

`core.write_json` already gives exactly that — a temporary file in the target directory,
fsynced, then `os.replace` — so the write side adds the sweep that a `kill -9` needs and
nothing else. A crash cannot unwind, so a fragment it leaves behind is cleared by the next
writer rather than by the one that died.

**The read side validates the whole shape before anything acts on it.** A record on disk has
met no normaliser: it may have been hand-edited, or truncated by something outside Cairn's
reach. Every caller treats what comes back as the model, so a document that merely carries
the current version number would hand them a shape missing the fields the model requires,
which fails later at whichever field is read first — far from the damage. Validation is
driven by the model's own declarations, so there is no second description of the record to
keep in step with `model.py`.
"""

from __future__ import annotations

import json
import types
from pathlib import Path
from typing import Any, Union, cast, get_args, get_origin, get_type_hints

from cairn.core import CairnError, write_json
from cairn.layout import RECORD_FILE, holds_directory, record_path, reports_directory
from cairn.record.engine import RUN_STATUS, TRIGGER_TYPE, find_attempts
from cairn.record.extract import extract, read_holds, read_reports
from cairn.record.model import RunRecord
from cairn.record.vocabulary import (
    ATTENTION_ORDER,
    ATTRIBUTIONS,
    EDGE_KINDS,
    INTEGRITY_FAULTS,
    NEXT_ACTIONS,
    OUTCOME_VERIFIED,
    OVERLAYS,
    PROVENANCES,
    RECORD_VERSION,
    STEP_OUTCOMES,
    VERDICT_EXIT_CODES,
    VERDICT_PRECEDENCE,
)
from cairn.verify import EXCLUSION_CAUSES

REBUILD = "rebuild it with `cairn record build`"


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
    this run at all — which a refused report is not: a document that was there and could
    not be read is evidence that this run existed, and the record says so.
    """
    attempts = find_attempts(records, run_id)
    found = read_reports(reports_directory(runs_root, run_id), run_id)
    if not attempts and not found.reports and not found.integrity:
        return None
    return extract(
        attempts[-1].record if attempts else None,
        found.reports,
        run_id=run_id,
        attempt_count=max(len(attempts), 1),
        in_flight_node=in_flight_node,
        in_flight_cause=in_flight_cause,
        holds=read_holds(holds_directory(runs_root, run_id), run_id),
        integrity=found.integrity,
    )


def _is_section(hint: object) -> bool:
    return isinstance(hint, type) and hasattr(hint, "__required_keys__")


def _shape(value: object, hint: Any, where: str) -> str | None:
    """Whether one value has the shape the model declares for it, and what is wrong if not.

    Read off `model.py`'s own annotations rather than restated, because a second
    description of the record is a description that goes out of date without failing. A
    Boolean is never a number here: Python's `bool` is an `int`, and a record carrying
    `true` where a count belongs is damaged however legal the subtype is.
    """
    if hint is Any:
        return None
    origin = get_origin(hint)
    if origin in (Union, types.UnionType):
        if any(_shape(value, member, where) is None for member in get_args(hint)):
            return None
        return f"{where} is a {type(value).__name__}, which the model does not allow there"
    if hint is type(None):
        return None if value is None else f"{where} is not null"
    if hint is bool:
        return None if isinstance(value, bool) else f"{where} is not a boolean"
    if hint is int:
        return (
            None
            if isinstance(value, int) and not isinstance(value, bool)
            else f"{where} is not a whole number"
        )
    if hint is float:
        return (
            None
            if isinstance(value, (int, float)) and not isinstance(value, bool)
            else f"{where} is not a number"
        )
    if hint is str:
        return None if isinstance(value, str) else f"{where} is not a string"
    if origin is list:
        if not isinstance(value, list):
            return f"{where} is not an array"
        (member,) = get_args(hint)
        for index, item in enumerate(cast(list[Any], value)):
            found = _shape(item, member, f"{where}[{index}]")
            if found is not None:
                return found
        return None
    if origin is dict:
        if not isinstance(value, dict):
            return f"{where} is not an object"
        _, member = get_args(hint)
        for key, item in cast(dict[Any, Any], value).items():
            if not isinstance(key, str):
                return f"{where} is keyed by something that is not a string"
            found = _shape(item, member, f"{where}.{key}")
            if found is not None:
                return found
        return None
    if _is_section(hint):
        return _section(value, hint, where)
    return f"{where} has a declared type this reader cannot check: {hint!r}"


def _section(value: object, shape: Any, where: str) -> str | None:
    """One section of the record against the fields its own declaration requires.

    A field the declaration does not name is refused rather than ignored: a record carrying
    one is a record some other version of this model wrote, and reading through it would
    hand a caller a shape nothing here described.
    """
    if not isinstance(value, dict):
        return f"{where} is not an object"
    found = cast(dict[str, Any], value)
    hints = get_type_hints(shape)
    for name in sorted(cast(frozenset[str], shape.__required_keys__)):
        if name not in found:
            return f"{where}{'.' if where else ''}{name} is missing"
    for name in sorted(found):
        if name not in hints:
            return f"{where}{'.' if where else ''}{name} is not a field of the record"
        complaint = _shape(
            found[name], hints[name], f"{where}{'.' if where else ''}{name}"
        )
        if complaint is not None:
            return complaint
    return None


def _vocabulary(record: RunRecord, run_id: str) -> str | None:
    """Every frozen word, every identity and every uniqueness rule the model rests on.

    The shape check above proves each field is a string; this proves it is one of the words
    the record is allowed to hold. A verdict outside the frozen set, or an exit code that
    is not the one that verdict carries, is a record that would tell automation something
    no extraction could have produced.
    """
    if record["run_id"] != run_id:
        return f"this is the record of run {record['run_id']!r}, not of {run_id!r}"
    if record["verdict"] not in VERDICT_PRECEDENCE:
        return f"{record['verdict']!r} is not one of the frozen verdicts"
    if record["exit_code"] != VERDICT_EXIT_CODES[record["verdict"]]:
        return (
            f"a {record['verdict']!r} run exits "
            f"{VERDICT_EXIT_CODES[record['verdict']]}, and this record says "
            f"{record['exit_code']}"
        )
    if record["engine_run_status_name"] != RUN_STATUS.get(record["engine_run_status"], ""):
        return (
            f"the engine's run status {record['engine_run_status']} and the word "
            f"{record['engine_run_status_name']!r} do not agree"
        )
    if record["next_action"]["action"] not in NEXT_ACTIONS:
        return f"{record['next_action']['action']!r} is not one of the frozen next actions"
    if record["trigger"]["kind"] not in TRIGGER_TYPE.values():
        return f"{record['trigger']['kind']!r} is not one of the trigger kinds"
    if record["trigger"]["attribution"] not in ATTRIBUTIONS:
        return f"{record['trigger']['attribution']!r} is not one of the attributions"
    for edge in record["edges"]:
        if edge["kind"] not in EDGE_KINDS:
            return f"{edge['kind']!r} is not one of the edge kinds"
    for item in record["attention"]:
        if item["kind"] not in ATTENTION_ORDER:
            return f"{item['kind']!r} is not one of the attention kinds"
    for refusal in record["integrity"]:
        if refusal["fault"] not in INTEGRITY_FAULTS:
            return f"{refusal['fault']!r} is not one of the integrity faults"
    for step in record["steps"]:
        if step["outcome"] not in STEP_OUTCOMES:
            return f"{step['step_id']} is {step['outcome']!r}, which is no step outcome"
        if step["verified"] != (step["outcome"] == OUTCOME_VERIFIED):
            return f"{step['step_id']} is {step['outcome']!r} and claims to be verified"
        if step["cause"] is not None and step["cause"] not in EXCLUSION_CAUSES:
            return f"{step['step_id']} carries the cause {step['cause']!r}, which is none"
        if step["overlays"] != [
            overlay for overlay in OVERLAYS if overlay in step["overlays"]
        ]:
            return (
                f"{step['step_id']} carries the overlays {step['overlays']}, which are "
                "not the frozen ones in their frozen order"
            )
    for name, values in (
        ("step", [step["step_id"] for step in record["steps"]]),
        ("engine node", [node["name"] for node in record["nodes"]]),
        ("housekeeping node", [item["name"] for item in record["infrastructure"]]),
    ):
        found = next(
            (value for value in values if values.count(value) > 1),
            None,
        )
        if found is not None:
            # Two rows under one name is one key in the projection, so a surface would show
            # one of them under the other's facts ([28 C]).
            return f"the {name} {found!r} is in this record more than once"
    return _provenances(record)


def _provenances(record: RunRecord) -> str | None:
    maps: list[dict[str, str]] = [
        record["provenance"],
        record["git"]["provenance"],
        record["trigger"]["provenance"],
        record["lineage"]["provenance"],
        *(step["provenance"] for step in record["steps"]),
        *(item["provenance"] for item in record["infrastructure"]),
        *(node["provenance"] for node in record["nodes"]),
        *(census["provenance"] for census in record["waves"]),
    ]
    for found in maps:
        for field, authority in found.items():
            if authority not in PROVENANCES:
                return f"{field!r} claims the authority {authority!r}, which is none"
    return None


def read_record(runs_root: Path, run_id: str) -> RunRecord | None:
    """One run's record as it was last written, or None where none has been.

    Anything that is not this run's whole current record is refused, and the refusal names
    the command that makes a new one. The record is derived from state that outlives it, so
    a rebuild is the remedy for every fault here — reading a damaged one through would hand
    a caller facts no extraction produced.
    """
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
        raise CairnError(
            "run_record_unreadable",
            f"{path} records version {found!r} and this Cairn writes {RECORD_VERSION}; "
            f"{REBUILD}",
        )
    document = cast(dict[str, Any], raw)
    complaint = _section(document, RunRecord, "") or _vocabulary(
        cast(RunRecord, document), run_id
    )
    if complaint is not None:
        raise CairnError("run_record_unreadable", f"{path}: {complaint}; {REBUILD}")
    return cast(RunRecord, document)


__all__ = ["build_run_record", "read_record", "write_record"]
