"""Sanitising a plan's own step names into engine ids, and deriving the plan slug."""

import os
import re
from typing import TypedDict

from cairn.plan.schema import PLAN_SLUG_PATTERN, STEP_ID_PATTERN, Collision

# A leading ordinal is how plan documents number their steps; it is display information
# and never part of the identity, so `01-config-schema` and `config-schema` collide.
_ORDINAL = re.compile(r"^\d+(\.\d+)*\s*[.)_-]+\s*")
_NON_ID = re.compile(r"[^a-z0-9]+")
_NON_SLUG = re.compile(r"[^a-z0-9]+")

_INDEX_NAMES = ("README.md", "WORKLIST.md", "PLAN.md", "index.md")


class Assignment(TypedDict):
    slug: str
    id: str


def sanitise_id(source_id: str) -> str:
    """Map a plan's own step name to the engine's identifier grammar."""
    text = _ORDINAL.sub("", source_id.strip().lower())
    text = _NON_ID.sub("_", text).strip("_")
    if not text:
        text = "step"
    if not text[0].isalpha():
        text = "s_" + text
    return text


def assign_ids(source_ids: list[str]) -> tuple[list[Assignment], list[Collision]]:
    """Return the id per slug in document order, plus the collisions that had to be broken.

    Collision breaking is positional and stable: the first occurrence keeps the plain id
    and later ones take a numeric suffix, so re-deriving the same document twice produces
    the same ids and a run record stays comparable across runs.
    """
    assignments: list[Assignment] = []
    collisions: list[Collision] = []
    used: dict[str, str] = {}
    for source_id in source_ids:
        base = sanitise_id(source_id)
        engine_id = base
        suffix = 1
        while engine_id in used:
            suffix += 1
            engine_id = f"{base}_{suffix}"
        used[engine_id] = source_id
        assignments.append({"slug": source_id, "id": engine_id})
        if engine_id != base:
            collisions.append(
                {
                    "slug": source_id,
                    "sanitised_to": base,
                    "assigned": engine_id,
                    "clashed_with": used[base],
                }
            )
    return assignments, collisions


def is_engine_id(value: str) -> bool:
    return re.fullmatch(STEP_ID_PATTERN, value) is not None


def is_plan_slug(value: str) -> bool:
    return re.fullmatch(PLAN_SLUG_PATTERN, value) is not None


def derive_plan_slug(source_path: str) -> str:
    """Derive the plan slug from the document's own location.

    A plan written as a folder of numbered task documents is named by the folder; a plan
    written as one document is named by the file. The slug names the worktree parent, the
    workflow file, and the run record, so it is derived once here and never re-derived.
    """
    path = os.path.abspath(source_path)
    if os.path.isdir(path):
        name = os.path.basename(path)
    else:
        parent, filename = os.path.split(path)
        if filename in _INDEX_NAMES:
            name = os.path.basename(parent)
        else:
            name = os.path.splitext(filename)[0]
    slug = _NON_SLUG.sub("-", name.lower()).strip("-")
    if not slug or not slug[0].isalnum():
        slug = "plan-" + slug.strip("-")
    return slug


def plan_slug_collisions(slug: str, namespaces: list[str]) -> list[str]:
    """Report every namespace directory that already holds an entry named `slug`.

    The namespaces are supplied by the caller rather than derived here: the worktree
    parent belongs to the topology, the workflow file to the generator, and the run
    record to the run model. This function only owns the rule that all three must be free.
    """
    return [
        os.path.join(directory, slug)
        for directory in namespaces
        if os.path.exists(os.path.join(directory, slug))
        or os.path.exists(os.path.join(directory, slug + ".yaml"))
    ]
