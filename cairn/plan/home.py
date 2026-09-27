"""Where a plan's reviewed graph lives: one file per plan, beside the repository's workflows.

A graph is the only record of what an author reviewed and answered, and workflows, offers
and run records are all addressed by plan. A graph addressed by repository alone would let
authoring a second plan overwrite the first plan's answers, so each lives under its own slug
in git's admin directory, where no commit step can sweep it up and every worktree of the
repository finds the same one.

A repository that still holds the single `graph.json` every plan once shared is settled here,
by the slug that graph itself names — never by the plan being asked about, because a graph
filed under the wrong plan would be published as that plan's reviewed reading.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, cast

from cairn.core import CairnError
from cairn.gitio import state_directory
from cairn.plan.ids import is_plan_slug

GRAPHS_DIRECTORY = "graphs"
GRAPH_SUFFIX = ".json"
SINGLETON_GRAPH = "graph.json"


def graphs_directory(repository: Path) -> Path:
    return state_directory(repository) / GRAPHS_DIRECTORY


def graph_path(repository: Path, plan_slug: str) -> Path:
    if not is_plan_slug(plan_slug):
        raise CairnError(
            "invalid_arguments",
            f"{plan_slug!r} is not a plan slug, so it names no graph; derive one with "
            "`python3 -m cairn plan slug <path>`",
        )
    return graphs_directory(repository) / f"{plan_slug}{GRAPH_SUFFIX}"


def _singleton_slug(singleton: Path) -> str:
    """The plan a shared `graph.json` says it belongs to, or a refusal naming the file."""
    try:
        raw: Any = json.loads(singleton.read_text(encoding="utf-8"))
    except (OSError, ValueError) as unreadable:
        raise CairnError(
            "invalid_arguments",
            f"{singleton} is not a graph Cairn can read ({unreadable}), so the plan it "
            "belongs to cannot be established. Move it under graphs/<plan>.json yourself, "
            "or delete it",
        ) from unreadable
    plan: Any = cast(dict[str, Any], raw).get("plan") if isinstance(raw, dict) else None
    slug: Any = cast(dict[str, Any], plan).get("slug") if isinstance(plan, dict) else None
    if not isinstance(slug, str) or not is_plan_slug(slug):
        raise CairnError(
            "invalid_arguments",
            f"{singleton} names no valid plan slug, so the plan it belongs to cannot be "
            "established. Move it under graphs/<plan>.json yourself, or delete it",
        )
    return slug


def settle_singleton(repository: Path) -> Path | None:
    """File a shared `graph.json` under the plan it names, and say where it went.

    Refused rather than resolved when the plan's own home already holds a different graph:
    two reviewed readings of one plan is a choice for the person who reviewed them.
    """
    singleton = state_directory(repository) / SINGLETON_GRAPH
    if not singleton.exists():
        return None
    home = graph_path(repository, _singleton_slug(singleton))
    if home.exists():
        if home.read_bytes() != singleton.read_bytes():
            raise CairnError(
                "invalid_arguments",
                f"{singleton} and {home} are two different graphs for one plan, so which "
                "one was reviewed cannot be told from here. Keep one and delete the other",
            )
        singleton.unlink()
        return home
    home.parent.mkdir(parents=True, exist_ok=True)
    os.replace(singleton, home)
    return home


__all__ = [
    "GRAPHS_DIRECTORY",
    "SINGLETON_GRAPH",
    "graph_path",
    "graphs_directory",
    "settle_singleton",
]
