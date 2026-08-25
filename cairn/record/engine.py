"""Reading the engine's own record, and the three tables that make its integers mean something.

The engine's state file has no external contract. Unlike its input format — a strict JSON
Schema, numbered specs, a conformance suite — it is an internal struct whose enums
serialise as bare integers keyed to declaration order, with no schema, no spec, no
conformance test, and live legacy remaps proving it has already churned
([research-dagu.md]). So the tables below are where a version bump surfaces first, and an
unmapped value is a hard error rather than a silent default.

**The tables are indexed by which vocabulary is being read, never by the number alone.**
Three vocabularies overlap numerically and mean different things at the same value: `5` is
`skipped` for a node, `queued` for a run and `retry` for a trigger. One table keyed on the
integer would answer all three questions with whichever answer was written last.

Two mitigations belong beside the pin. The engine's REST API exposes a `status` **and** a
`statusLabel` for every node, so a Cairn willing to run a server has a named alternative to
integer archaeology; and `ENGINE_VERSION` is imported rather than restated, so there is one
pin in the codebase.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple, cast

from cairn.core import CairnError
from cairn.supervise import last_record
from cairn.topology import Naming, TopologyError, parse_node_name
from cairn.workflow.schema import ENGINE_VERSION

NODE_VOCABULARY = "node"
RUN_VOCABULARY = "run"
TRIGGER_VOCABULARY = "trigger"

# The integers, named, so a comparison reads as the state it means rather than as a number
# whose vocabulary a reader has to infer from context.
NODE_STATUS_NOT_STARTED = 0
NODE_STATUS_RUNNING = 1
NODE_STATUS_FAILED = 2
NODE_STATUS_ABORTED = 3
NODE_STATUS_SUCCEEDED = 4
NODE_STATUS_SKIPPED = 5

NODE_NOT_STARTED = "not_started"
NODE_RUNNING = "running"
NODE_FAILED = "failed"
NODE_ABORTED = "aborted"
NODE_SUCCEEDED = "succeeded"
NODE_SKIPPED = "skipped"

# Measured by execution against the pinned engine. `aborted` is the cascade: a node whose
# dependency failed is recorded at 3 with `error: "upstream failed"` and an empty start
# time, which is what tells a step that will never run from one that has not started yet.
NODE_STATUS: dict[int, str] = {
    NODE_STATUS_NOT_STARTED: NODE_NOT_STARTED,
    NODE_STATUS_RUNNING: NODE_RUNNING,
    NODE_STATUS_FAILED: NODE_FAILED,
    NODE_STATUS_ABORTED: NODE_ABORTED,
    NODE_STATUS_SUCCEEDED: NODE_SUCCEEDED,
    NODE_STATUS_SKIPPED: NODE_SKIPPED,
}

RUN_RUNNING = "running"
RUN_FAILED = "failed"
RUN_SUCCEEDED = "succeeded"
RUN_QUEUED = "queued"
RUN_PARTIALLY_SUCCEEDED = "partially_succeeded"

# 5 is the load-bearing row. Anything triggered externally arrives queued and may sit there
# indefinitely with every node at 0 if no scheduler is up, so a queued run is pending work
# and never an outcome — while the same number means `skipped` one table up.
RUN_STATUS: dict[int, str] = {
    1: RUN_RUNNING,
    2: RUN_FAILED,
    4: RUN_SUCCEEDED,
    5: RUN_QUEUED,
    6: RUN_PARTIALLY_SUCCEEDED,
}

TRIGGER_UNKNOWN = "unknown"
TRIGGER_SCHEDULER = "scheduler"
TRIGGER_MANUAL = "manual"
TRIGGER_WEBHOOK = "webhook"
TRIGGER_SUBDAG = "subdag"
TRIGGER_RETRY = "retry"
TRIGGER_CATCHUP = "catchup"

# The trigger persists as an integer, not as the word the engine's view displays, so it is
# an iota-keyed vocabulary like the other two and gets the same treatment. `manual` is
# measured; the rest are the declared order around it.
TRIGGER_TYPE: dict[int, str] = {
    0: TRIGGER_UNKNOWN,
    1: TRIGGER_SCHEDULER,
    2: TRIGGER_MANUAL,
    3: TRIGGER_WEBHOOK,
    4: TRIGGER_SUBDAG,
    5: TRIGGER_RETRY,
    6: TRIGGER_CATCHUP,
}

# The exit code as a number survives only inside this string. The engine holds the real
# number live — `${steps.<name>.exit_code}` resolves for a later step in the same run — but
# the live-to-persisted transform drops it, so nothing reaches disk or the REST API.
EXIT_STATUS = re.compile(r"exit status (\d+)")


class Attempt(NamedTuple):
    """One attempt's record, and the moment it says it began."""

    path: Path
    record: dict[str, Any]
    started_at: str


def _unmapped(vocabulary: str, value: object) -> CairnError:
    """A status the table does not name — which is where a version bump surfaces first.

    Told apart from a status that is not a number at all, because the two ask for different
    things from the person reading the message: one is an engine that has moved, the other
    is a record that is damaged, and sending someone to re-measure the table over a corrupt
    file wastes the trail.
    """
    return CairnError(
        "engine_status_unmapped",
        f"engine {vocabulary} status {value!r} is not in the table measured against Dagu "
        f"{ENGINE_VERSION}. The engine's node, run and trigger vocabularies overlap "
        "numerically, so this table is indexed by which one is being read; re-measure it "
        "against the installed engine rather than widening it by guess",
        detail={"vocabulary": vocabulary, "value": value, "engine": ENGINE_VERSION},
    )


def _unreadable(vocabulary: str, value: object) -> CairnError:
    return CairnError(
        "engine_status_unmapped",
        f"engine {vocabulary} status {value!r} is not a number, so this record does not "
        "carry the status it is required to; the file is damaged rather than the table "
        "being out of date",
        detail={"vocabulary": vocabulary, "value": value, "engine": ENGINE_VERSION},
    )


def _read(table: dict[int, str], vocabulary: str, value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _unreadable(vocabulary, value)
    try:
        return table[value]
    except KeyError as exc:
        raise _unmapped(vocabulary, value) from exc


def node_status_name(value: object) -> str:
    return _read(NODE_STATUS, NODE_VOCABULARY, value)


def run_status_name(value: object) -> str:
    return _read(RUN_STATUS, RUN_VOCABULARY, value)


def trigger_name(value: object) -> str:
    return _read(TRIGGER_TYPE, TRIGGER_VOCABULARY, value)


def parse_exit_code(error: object) -> int | None:
    """The exit status the engine recorded, dug back out of the sentence holding it."""
    if not isinstance(error, str):
        return None
    found = EXIT_STATUS.search(error)
    return None if found is None else int(found.group(1))


def moment(value: object) -> str | None:
    """One of the engine's timestamps, or None where it spells "not yet" as an empty string."""
    return value if isinstance(value, str) and value else None


def text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def classify(name: str) -> Naming | None:
    """A node's role and subject, or None for a name the topology's grammar does not cover.

    A node Cairn did not emit — a hand-edited workflow, the lifecycle handler, a role a
    later version adds — is carried unclassified rather than refused. Every node reaches
    the record, and one that is dropped for being unrecognisable is one whose failure
    nothing can report.
    """
    try:
        return parse_node_name(name)
    except TopologyError:
        return None


def find_attempts(root: Path, run_id: str) -> list[Attempt]:
    """Every attempt of one run, oldest first, ordered by what each record says of itself.

    The engine's directory layout carries no external contract, so the run id inside a
    record is what confirms a match and the layout only narrows the search. The order comes
    from the recorded start time rather than from the path, because attempt directory names
    sort lexicographically and that is only incidentally chronological — a record built
    from the wrong attempt would report an earlier failure over a run that later succeeded.
    """
    if not root.is_dir():
        return []
    found: list[Attempt] = []
    for candidate in sorted(root.rglob("status.jsonl")):
        try:
            record = last_record(candidate)
        except CairnError:
            continue
        if record is None or record.get("dagRunId") != run_id:
            continue
        found.append(Attempt(candidate, record, moment(record.get("startedAt")) or ""))
    return sorted(found, key=began)


def began(attempt: Attempt) -> tuple[int, float]:
    """When an attempt says it started, as a moment rather than as text.

    The engine writes an offset-aware timestamp, so two attempts either side of a
    daylight-saving change sort by their text in the opposite order to real time — and a
    record built from the wrong attempt reports an earlier failure over a run that later
    succeeded. An attempt that names no start sorts first, because unknown is not later.
    """
    try:
        return (1, datetime.fromisoformat(attempt.started_at).timestamp())
    except (ValueError, OSError, OverflowError):
        # A record can arrive damaged, and a reader that died sorting one would cost a
        # person the record of every other attempt beside it.
        return (0, 0.0)


def nodes_of(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Every node the engine recorded, and the lifecycle handler it keeps beside them.

    The handler is a top-level key rather than a member of `nodes`, so a reader that took
    only `nodes` would lose the run's own release — the step whose failure means a
    repository is still held.
    """
    raw: Any = record.get("nodes")
    found: list[dict[str, Any]] = [
        cast(dict[str, Any], node)
        for node in (cast(list[Any], raw) if isinstance(raw, list) else [])
        if isinstance(node, dict)
    ]
    handler: Any = record.get("onExit")
    if isinstance(handler, dict):
        found.append(cast(dict[str, Any], handler))
    return found


def node_name(node: dict[str, Any]) -> str:
    step: Any = node.get("step")
    name: Any = cast(dict[str, Any], step).get("name") if isinstance(step, dict) else None
    return name if isinstance(name, str) and name else ""


def node_depends(node: dict[str, Any]) -> list[str]:
    step: Any = node.get("step")
    raw: Any = cast(dict[str, Any], step).get("depends") if isinstance(step, dict) else None
    return [name for name in cast(list[Any], raw) if isinstance(name, str)] if isinstance(raw, list) else []


def node_command(node: dict[str, Any]) -> str | None:
    """What the engine was actually told to run, which is the only durable record of the ask.

    The plan's own task text does not survive into a run — the generator consumes the graph
    and keeps only its digest — so a step's command, prompt and all, is what it was asked.
    """
    step: Any = node.get("step")
    if not isinstance(step, dict):
        return None
    raw: Any = cast(dict[str, Any], step).get("commands")
    for entry in cast(list[Any], raw) if isinstance(raw, list) else []:
        if isinstance(entry, dict):
            spelled: Any = cast(dict[str, Any], entry).get("cmdWithArgs")
            if isinstance(spelled, str) and spelled:
                return spelled
    return None


__all__ = [
    "ENGINE_VERSION",
    "EXIT_STATUS",
    "NODE_ABORTED",
    "NODE_FAILED",
    "NODE_NOT_STARTED",
    "NODE_RUNNING",
    "NODE_SKIPPED",
    "NODE_STATUS",
    "NODE_STATUS_ABORTED",
    "NODE_STATUS_FAILED",
    "NODE_STATUS_NOT_STARTED",
    "NODE_STATUS_RUNNING",
    "NODE_STATUS_SKIPPED",
    "NODE_STATUS_SUCCEEDED",
    "NODE_SUCCEEDED",
    "NODE_VOCABULARY",
    "RUN_FAILED",
    "RUN_PARTIALLY_SUCCEEDED",
    "RUN_QUEUED",
    "RUN_RUNNING",
    "RUN_STATUS",
    "RUN_SUCCEEDED",
    "RUN_VOCABULARY",
    "TRIGGER_CATCHUP",
    "TRIGGER_MANUAL",
    "TRIGGER_RETRY",
    "TRIGGER_SCHEDULER",
    "TRIGGER_SUBDAG",
    "TRIGGER_TYPE",
    "TRIGGER_UNKNOWN",
    "TRIGGER_VOCABULARY",
    "TRIGGER_WEBHOOK",
    "Attempt",
    "Naming",
    "classify",
    "find_attempts",
    "moment",
    "node_command",
    "node_depends",
    "node_name",
    "node_status_name",
    "nodes_of",
    "parse_exit_code",
    "run_status_name",
    "text",
    "trigger_name",
]
