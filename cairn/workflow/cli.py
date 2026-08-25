"""`python3 -m cairn workflow` — authoring, and checking what was authored.

These run at authoring time, outside any run, so they take no runtime identity and leave no
step report. Two verbs and no more: `author` is the only thing in Cairn that writes an engine
definition, and `check` reads one and writes nothing.

A definition is **always** written to a file and handed to the engine by path. It is tens of
kilobytes and it is never re-emitted inline through a conversation, where it could not be
reproduced faithfully.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, cast

from cairn.core import CairnError
from cairn.gitio import (
    checked_out_branch,
    refuse_unusable_repository,
    runs_root,
    working_tree_root,
)
from cairn.plan.schema import SchemaError
from cairn.plan.validate import validate
from cairn.topology import TopologyError, derive
from cairn.workflow.build import build, graph_digest
from cairn.workflow.gate import EngineUnavailable, gate
from cairn.workflow.preflight import preflight
from cairn.workflow.schema import LABEL_PLAN, serialise
from cairn.workflow.stamp import (
    describe,
    stamp_path,
    workflow_path,
    write_stamp,
)

# Two verbs and no more. A third would have to be reviewed against the criterion that
# the generator is the only thing that writes an engine definition.
VERBS = frozenset({"author", "check"})
EXIT_REFUSED = 1
EXIT_USAGE = 2


class Refused(Exception):
    """Something a person can act on, reported as a line rather than a traceback."""


def _load(path: str) -> Any:
    """Read a JSON document, or refuse.

    A file that does not parse is the ordinary case here rather than an exceptional one: a
    hand-edited workflow is exactly what `check` exists to judge, and a graph is written by
    a conversation that may have been interrupted.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError as exc:
        raise Refused(f"{path}: not the JSON document Cairn writes — {exc}") from exc


def _author(args: argparse.Namespace) -> int:
    repository = Path(args.repository).resolve()
    # Established before anything is read, so a directory that is not a repository is named
    # as one rather than diagnosed by whatever fails first inside it.
    refuse_unusable_repository(repository)
    raw = _load(args.graph)
    result = validate(raw)
    if not result.ok or result.graph is None:
        for finding in result.errors:
            print(f"error  {finding}", file=sys.stderr)
        return EXIT_REFUSED
    graph = result.graph

    parent = args.parent_branch or checked_out_branch(repository)
    if not parent:
        print(
            f"{repository} has no branch checked out, so there is nothing for the plan's "
            "work to land on; pass --parent-branch",
            file=sys.stderr,
        )
        return EXIT_REFUSED
    # Always empty. A value pinned into the file is fixed for its life, and a cron firing
    # has no override point, so every firing after the first would find a fresh marker for
    # each `run`- and period-scoped step and report a clean success having done nothing. The
    # run mints its own at its first act ([marker.py]), and this parameter stays declared
    # because it is the override a *trigger* supplies — which is the only place supplying one
    # means anything.
    occasion = ""
    python_path = args.python_path or str(Path(__file__).resolve().parents[2])

    digest = graph_digest(graph)
    topology = derive(graph, repository_root=repository, parent_branch=parent)
    document = build(
        graph,
        topology,
        occasion=occasion,
        python_path=python_path,
        runs_root=str(runs_root(repository)),
        schedule=args.schedule,
    )

    target = Path(args.out) if args.out else workflow_path(repository, graph["plan"]["slug"])
    _refuse_inside_the_working_tree(repository, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    divergence = describe(target, graph["plan"]["slug"], digest)

    # Gated where it cannot be run from, then moved into place, so a definition that failed
    # the gate is never left where anything could start it. The name is unique per authoring
    # rather than per plan: two authorings sharing one pending path could publish bytes the
    # other one gated.
    descriptor, pending_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".authoring", dir=target.parent
    )
    pending = Path(pending_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(serialise(document))
        faults = preflight(_load(str(pending)))
        if not faults:
            faults = gate(pending)
        if faults:
            for fault in faults:
                print(f"refused  {fault}", file=sys.stderr)
            return EXIT_REFUSED
        # The record of the bytes is written before they are published, so an authoring that
        # dies here leaves a stamp with no file — a state re-authoring names honestly —
        # rather than a file whose stamp accuses a person of having edited it.
        write_stamp(pending, document, digest, published=target)
        os.replace(stamp_path(pending), stamp_path(target))
        os.replace(pending, target)
    finally:
        pending.unlink(missing_ok=True)
        stamp_path(pending).unlink(missing_ok=True)

    print(divergence.summary)
    print(f"{target}")
    return 0


def _refuse_inside_the_working_tree(repository: Path, target: Path) -> None:
    """Refuse to write a definition anywhere a commit step could sweep it up.

    The default path is inside git's admin directory, which nothing stages. An `--out` the
    caller chose is not, so it is checked: `cairn commit` stages the whole working tree, so a
    definition written there lands in the user's history.
    """
    tree = working_tree_root(repository)
    resolved = target.resolve()
    if tree in resolved.parents and ".git" not in resolved.parts:
        raise Refused(
            f"{target} is inside {tree}, which a plan's own commit step stages in full; "
            "generated definitions live outside the working tree"
        )


def _check(args: argparse.Namespace) -> int:
    """Judge a definition already on disk, and write nothing.

    It reports the provenance too, because this is the verb a person reaches for when they
    suspect a file was edited — and `author`, which is the only other thing that can tell
    them, replaces the edit in the same breath.
    """
    path = Path(args.workflow).resolve()
    document = _load(str(path))
    plan = ""
    raw = cast(dict[str, Any], document).get("labels") if isinstance(document, dict) else None
    if isinstance(raw, dict):
        labels: dict[str, Any] = cast(dict[str, Any], raw)
        plan = str(labels.get(LABEL_PLAN, ""))
    print(describe(path, plan).summary)
    faults = preflight(document)
    if not faults:
        faults = gate(path)
    for fault in faults:
        print(f"refused  {fault}", file=sys.stderr)
    print(f"{path}: {len(faults)} refusal(s)")
    return EXIT_REFUSED if faults else 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cairn workflow", description=__doc__)
    verbs = parser.add_subparsers(dest="verb", required=True)

    child = verbs.add_parser("author")
    child.add_argument("graph")
    child.add_argument("--repository", required=True)
    child.add_argument("--parent-branch")
    child.add_argument("--python-path")
    child.add_argument("--out")
    # A recurring plan is asked for, never inferred. The cron expression itself is the
    # engine's to judge — measured, its validator refuses a malformed one — and the daemon
    # a schedule costs is `cairn schedule` ([triggers.md]).
    child.add_argument("--schedule")

    child = verbs.add_parser("check")
    child.add_argument("workflow")
    return parser


def workflow_verbs() -> frozenset[str]:
    """Every verb this command line offers — the whole surface that can write a definition."""
    return VERBS


def main(arguments: list[str]) -> int:
    args = _parser().parse_args(arguments)
    try:
        return _author(args) if args.verb == "author" else _check(args)
    # Every refusal this pipeline can raise, in one place. The emitters refuse a plan with a
    # bare `ValueError` — a step nobody has been asked to assert, a task that would duplicate
    # its own work — and `_refuse_unasserted`'s own docstring calls this the last place such a
    # thing can be raised against a human. A human gets a line, not a stack trace.
    except (Refused, CairnError, EngineUnavailable, SchemaError, TopologyError, ValueError) as exc:
        print(f"error  {exc}", file=sys.stderr)
        return EXIT_REFUSED
    except OSError as exc:
        print(f"error  {exc}", file=sys.stderr)
        return EXIT_USAGE


__all__ = ["main", "workflow_verbs"]
