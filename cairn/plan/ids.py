"""Sanitising a plan's own step names into engine ids, and deriving the plan slug."""

import hashlib
import os
import re
from typing import TypedDict

from cairn.plan.schema import (
    ENGINE_NAME_MAX_BYTES,
    PLAN_SLUG_PATTERN,
    STEP_ID_PATTERN,
    Collision,
)

# A leading ordinal is how plan documents number their steps; it is display information
# and never part of the identity, so `01-config-schema` and `config-schema` collide.
_ORDINAL = re.compile(r"^\d+(\.\d+)*\s*[.)_-]+\s*")
_NON_ID = re.compile(r"[^a-z0-9]+")
_NON_SLUG = re.compile(r"[^a-z0-9]+")

_INDEX_NAMES = ("README.md", "WORKLIST.md", "PLAN.md", "index.md")

# A backlog document names itself by the id every other document in that plan already uses
# for it, and the rest of the file name is its title — `task-381 - Report entry points for
# a repository of vscode's scale…`. Two spellings measured against real documents:
# `task-381 - Title` and `TASK-381.4 Title`. The whitespace after the id is required, so a
# folder genuinely called `task-381-migration` is a name and keeps all of itself.
_TASK_ID = re.compile(r"^(task[-_ ]?\d+(?:\.\d+)*)\s", re.IGNORECASE)

# Eight hex characters, against the sixteen `verify_handle` spends on a step handle. The
# population here is the plans in one repository's three namespaces rather than every step
# of every plan, and the slug has only forty characters to spend on staying readable.
SLUG_DIGEST_LENGTH = 8


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
    """Whether this is a slug the engine could actually load, grammar and length alike.

    The length half is not a preference. The slug is the workflow file's name and the file's
    name is what the engine reads as the DAG name, so a slug over the bound names a
    definition that is refused at load — and refusing it here means refusing it at
    derivation, where a person can still act on it, rather than at the gate ([19 A]).
    """
    return (
        re.fullmatch(PLAN_SLUG_PATTERN, value) is not None
        and len(value.encode("utf-8")) <= ENGINE_NAME_MAX_BYTES
    )


def derive_plan_slug(source_path: str) -> str:
    """Derive the plan slug from the document's own location, inside the engine's bound.

    A plan written as a folder of numbered task documents is named by the folder; a plan
    written as one document is named by the file. The slug names the worktree parent, the
    workflow file, and the run record, so it is derived once here and never re-derived.

    **Bounded, because the workflow file's name is the DAG name.** A backlog document's file
    name is its whole title and runs to a hundred characters and more, which the engine
    refuses at load — so a name that opens with a task id takes the id, and any other long
    name is cut at the last hyphen inside the bound and carries a digest of the whole name.

    **The digest rides on every cut, not only on one that collides.** Making it conditional
    on what is already on disk would make the slug a function of filesystem state: the same
    document would derive one name before its worktree parent existed and another after,
    and a re-derivation would quietly re-point the plan at a different worktree, a different
    workflow file and a different run record. Two plans cannot adopt each other's worktrees
    ([topology.worktrees_root_for]) only because this function is pure.
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
    task = _TASK_ID.match(name)
    if task is not None:
        name = task.group(1)
    slug = _NON_SLUG.sub("-", name.lower()).strip("-")
    if not slug or not slug[0].isalnum():
        slug = "plan-" + slug.strip("-")
    if len(slug.encode("utf-8")) <= ENGINE_NAME_MAX_BYTES:
        return slug
    # The digest is of the whole sanitised name rather than of the cut, so two documents
    # that agree for the first thirty-one characters still derive two slugs.
    digest = hashlib.sha256(slug.encode("utf-8")).hexdigest()[:SLUG_DIGEST_LENGTH]
    head = slug[: ENGINE_NAME_MAX_BYTES - SLUG_DIGEST_LENGTH - 1]
    return f"{head.rpartition('-')[0].strip('-') or head.strip('-')}-{digest}"


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
