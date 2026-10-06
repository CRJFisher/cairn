"""Where a plan's reviewed graph lives, and where a plan stated in a request is written down.

A graph is the only record of what an author reviewed and answered, and workflows and run
records are both addressed by plan. A graph addressed by repository alone would let
authoring a second plan overwrite the first plan's answers, so each lives under its own slug
in git's admin directory, where no commit step can sweep it up and every worktree of the
repository finds the same one.

A plan the request states rather than points at has no document until authoring writes one,
and it lives beside the graph for the same reason: a run's first act refuses over a dirty
tree, so a plan document written into the working tree would stop the very run it was written
for.

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
PLANS_DIRECTORY = "plans"
PLAN_SUFFIX = ".md"


def graphs_directory(repository: Path) -> Path:
    return state_directory(repository) / GRAPHS_DIRECTORY


def plans_directory(repository: Path) -> Path:
    return state_directory(repository) / PLANS_DIRECTORY


def _checked(plan_slug: str, what: str) -> str:
    if not is_plan_slug(plan_slug):
        raise CairnError(
            "invalid_arguments",
            f"{plan_slug!r} is not a plan slug, so it names no {what}; derive one with "
            "`python3 -m cairn plan slug <path>`",
        )
    return plan_slug


def graph_path(repository: Path, plan_slug: str) -> Path:
    return graphs_directory(repository) / f"{_checked(plan_slug, 'graph')}{GRAPH_SUFFIX}"


def plan_document_path(repository: Path, plan_slug: str) -> Path:
    """Where a plan stated in a request is written down, named by its own slug.

    The file name is the slug, so `plan slug` run over this path derives the same one back:
    the plan's name, its graph, its workflow and its run record all answer to one word.
    """
    return plans_directory(repository) / f"{_checked(plan_slug, 'plan document')}{PLAN_SUFFIX}"


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
    "PLANS_DIRECTORY",
    "SHARED_GRAPH",
    "graph_path",
    "graphs_directory",
    "plan_document_path",
    "plans_directory",
    "refuse_shared_graph",
]
