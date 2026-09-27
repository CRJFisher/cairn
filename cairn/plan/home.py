"""Where a plan's reviewed graph lives: one file per plan, beside the repository's workflows.

A graph is the only record of what an author reviewed and answered, and workflows, offers
and run records are all addressed by plan. A graph addressed by repository alone would let
authoring a second plan overwrite the first plan's answers, so each lives under its own slug
in git's admin directory, where no commit step can sweep it up and every worktree of the
repository finds the same one.

A repository that still holds the single `graph.json` every plan once shared is refused, not
migrated: which plan that graph belongs to, and whether it is still the reviewed reading, is
the reviewer's call, so it is named and left for them to move or delete.
"""

from __future__ import annotations

from pathlib import Path

from cairn.core import CairnError
from cairn.gitio import state_directory
from cairn.plan.ids import is_plan_slug

GRAPHS_DIRECTORY = "graphs"
GRAPH_SUFFIX = ".json"
SHARED_GRAPH = "graph.json"


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


def refuse_shared_graph(repository: Path) -> None:
    """Refuse while the shared `graph.json` of the one-graph-per-repository layout remains."""
    shared = state_directory(repository) / SHARED_GRAPH
    if shared.exists():
        raise CairnError(
            "invalid_arguments",
            f"{shared} is the shared graph every plan once used, and graphs now live one per "
            "plan. Move it to graphs/<plan>.json under the plan it belongs to, or delete it",
        )


__all__ = [
    "GRAPHS_DIRECTORY",
    "SHARED_GRAPH",
    "graph_path",
    "graphs_directory",
    "refuse_shared_graph",
]
