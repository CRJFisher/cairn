"""Provenance, and the divergence nothing else will report.

The engine's editing surface rewrites a workflow in place and records nothing about having
done so: no version field, no content hash, no modification metadata, and the one facility
that would have carried it is licensed and unavailable at the pin ([03]). So detection is
Cairn's, and it costs one record and no engine cooperation.

The stamp lives in **two** places, and the second is what closes the cases a state record
alone cannot see. In Cairn's state it carries the emitted file's own hash, which is byte
identity. In the file's `labels` — measured to accept arbitrary keys and to survive both
engine checks — it carries the plan's identity and a hash of everything but itself, which is
semantic identity. A workflow deleted and re-created, or replaced wholesale, arrives with no
labels at all; that is the divergence, and no state record could have told it from a file
Cairn simply had not seen.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, NamedTuple, TypedDict, cast

from cairn.core import write_json
from cairn.gitio import state_directory
from cairn.workflow.schema import (
    ENGINE_VERSION,
    GENERATOR_VERSION,
    LABEL_BODY_DIGEST,
    LABEL_GENERATOR,
    LABEL_GRAPH_DIGEST,
    LABEL_PLAN,
    STAMP_SUFFIX,
    WORKFLOW_SUFFIX,
    Workflow,
    body_digest,
    read,
)

WORKFLOWS_DIRECTORY = "workflows"

UNSTAMPED = "unstamped"
ABSENT = "absent"
UNCHANGED = "unchanged"
HAND_EDITED = "hand_edited"
ANOTHER_PLAN = "another_plan"
REPLACED_WHOLESALE = "replaced_wholesale"
# Any generator but this one, rather than an earlier one: the comparison is equality, and a
# file from a checkout further ahead is exactly as unlike what Cairn now emits.
ANOTHER_GENERATOR = "another_generator"
# The workflow is byte-identical to what Cairn wrote, and the plan it was generated from has
# moved since. Nothing else detects this: a hand edit to the *workflow* is caught by the body
# digest, while an edit to the plan leaves the workflow untouched and silently stale.
PLAN_CHANGED = "plan_changed"


class Stamp(TypedDict):
    """What one authoring recorded, so the next one can say what it replaces."""

    plan: str
    generator: int
    engine: str
    graph_sha256: str
    body_sha256: str
    workflow_sha256: str
    workflow_path: str


class Divergence(NamedTuple):
    """What is about to be replaced, and whether anyone but Cairn wrote it."""

    state: str
    summary: str


def workflows_directory(repository: Path) -> Path:
    """Where generated definitions live.

    Inside git's admin directory rather than the working tree, so no commit step and no
    worktree removal can sweep a generated file into the user's history.
    """
    return state_directory(repository) / WORKFLOWS_DIRECTORY


def workflow_path(repository: Path, plan_slug: str) -> Path:
    return workflows_directory(repository) / f"{plan_slug}{WORKFLOW_SUFFIX}"


def stamp_path(workflow: Path) -> Path:
    return workflow.with_name(workflow.name + STAMP_SUFFIX)


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_stamp(
    workflow: Path, document: Workflow, graph_sha256: str, *, published: Path | None = None
) -> Stamp:
    """Record what was written, beside the bytes it describes.

    `published` names where the bytes are going when the stamp is written before them, so the
    record describes the file's final home rather than the temporary one it was gated in.
    """
    home = published or workflow
    record: Stamp = {
        "plan": document["labels"][LABEL_PLAN],
        "generator": GENERATOR_VERSION,
        "engine": ENGINE_VERSION,
        "graph_sha256": graph_sha256,
        "body_sha256": document["labels"][LABEL_BODY_DIGEST],
        "workflow_sha256": file_digest(workflow),
        "workflow_path": str(home),
    }
    write_json(stamp_path(workflow), cast(dict[str, Any], record))
    return record


def read_stamp(workflow: Path) -> Stamp | None:
    path = stamp_path(workflow)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    # A record whose shape is not the one Cairn writes is no record at all. Reading it as one
    # would make every later authoring of this plan die on the sidecar rather than replace it.
    if not isinstance(raw, dict):
        return None
    record = cast(dict[str, Any], raw)
    if not isinstance(record.get("workflow_sha256"), str):
        return None
    return cast(Stamp, record)


def describe(workflow: Path, plan: str, graph_sha256: str | None = None) -> Divergence:
    """What re-authoring is about to replace, stated plainly and never merged.

    Re-authoring always proceeds. A hand-edited workflow is replaced wholesale rather than
    reconciled, because the plan document is the source of truth and a merge between a
    generated file and an edited one would produce something neither was reviewed as.
    """
    if not workflow.exists():
        stamp = read_stamp(workflow)
        if stamp is None:
            return Divergence(ABSENT, f"writing {workflow}")
        return Divergence(
            REPLACED_WHOLESALE,
            f"{workflow} was generated by Cairn and is no longer there; writing it again",
        )

    try:
        document = read(workflow)
    except (OSError, ValueError):
        return Divergence(
            HAND_EDITED,
            f"{workflow} is no longer the JSON document Cairn writes, so it can only be "
            f"described by its hash ({file_digest(workflow)[:12]}); replacing it wholesale",
        )

    raw = cast(dict[str, Any], document).get("labels") if isinstance(document, dict) else None
    labels: dict[str, Any] = cast(dict[str, Any], raw) if isinstance(raw, dict) else {}
    if LABEL_BODY_DIGEST not in labels:
        return Divergence(
            UNSTAMPED,
            f"{workflow} carries no Cairn provenance, so Cairn did not write it; "
            "replacing it wholesale",
        )
    if labels.get(LABEL_PLAN) != plan:
        return Divergence(
            ANOTHER_PLAN,
            f"{workflow} was generated from plan {labels.get(LABEL_PLAN)!r}, not {plan!r}",
        )

    recorded = str(labels[LABEL_BODY_DIGEST])
    found = body_digest(document)
    if recorded != found:
        return Divergence(
            HAND_EDITED,
            f"{workflow} has been modified since Cairn wrote it "
            f"({recorded[:12]} → {found[:12]}); the edits are being replaced, not merged",
        )

    stamp = read_stamp(workflow)
    if stamp is not None and stamp["workflow_sha256"] != file_digest(workflow):
        return Divergence(
            HAND_EDITED,
            f"{workflow} matches its stamp but not Cairn's record of the bytes it wrote "
            f"({stamp['workflow_sha256'][:12]} → {file_digest(workflow)[:12]}); "
            "replacing it",
        )

    # Asked after both edit checks and before the missing record below, and each half of that
    # matters. An edit outranks it either way it was found, because someone having touched the
    # file is the more urgent thing to say. A missing record does not, because the generation
    # rides in the file's own labels — so it is still answered for a workflow whose record is
    # gone, which is the one case a record could never speak for. `body_digest` strips every
    # `cairn_` label, so a generation moving never reads as an edit.
    written_by = str(labels.get(LABEL_GENERATOR, ""))
    if written_by != str(GENERATOR_VERSION):
        return Divergence(
            ANOTHER_GENERATOR,
            f"{workflow} was written by Cairn generator {written_by or 'unknown'} and this "
            f"one is {GENERATOR_VERSION}, so its shape is not the shape Cairn now emits; "
            "replacing it",
        )

    # The plan's own digest, read from the labels for the same reason the generation is: it
    # rides in the file, so it is still answered for a workflow whose record is gone. Asked
    # only where a graph is in hand, which `check` never has — and the absence of a plan is
    # never reported as agreement with it.
    recorded_plan = str(labels.get(LABEL_GRAPH_DIGEST, ""))
    if graph_sha256 is not None and recorded_plan and recorded_plan != graph_sha256:
        return Divergence(
            PLAN_CHANGED,
            f"the plan changed since {workflow} was generated "
            f"({recorded_plan[:12]} → {graph_sha256[:12]}); the workflow itself is "
            "untouched, so nothing but this would have said so",
        )

    if stamp is None:
        return Divergence(
            UNCHANGED,
            f"replacing {workflow}, which matches its own stamp; Cairn's own record of it "
            "is gone",
        )
    return Divergence(UNCHANGED, f"replacing {workflow}, unmodified since Cairn wrote it")


__all__ = [
    "ABSENT",
    "ANOTHER_GENERATOR",
    "ANOTHER_PLAN",
    "HAND_EDITED",
    "PLAN_CHANGED",
    "REPLACED_WHOLESALE",
    "UNCHANGED",
    "UNSTAMPED",
    "WORKFLOWS_DIRECTORY",
    "Divergence",
    "Stamp",
    "describe",
    "file_digest",
    "read_stamp",
    "stamp_path",
    "workflow_path",
    "workflows_directory",
    "write_stamp",
]
