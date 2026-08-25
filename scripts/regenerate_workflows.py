"""Rewrite the golden workflows, and refuse to when the generator moved without saying so.

`fixtures/workflows/<shape>.yaml` holds the whole file the emitter writes for one plan, one
per topology shape. The suite compares them byte for byte, which is what a property cannot
do: a property holds for any document that has it, so an unintended change elsewhere in the
file still passes. Run this after any deliberate change to the emitted file:

    python3 -m scripts.regenerate_workflows

Every input is pinned here rather than taken from the machine, so the bytes are the same
wherever they are generated: a fictional repository and package root, a fictional parent
branch, and a plan graph from the corpus. A golden that named this checkout would be a
golden only this checkout could reproduce.

## The refusal, and why the command is the thing that carries it

`GENERATOR_VERSION` claims that a stamp written by an older generator is recognisable as
one. That claim only holds if the version moves whenever the emitted file does — and
regenerating a snapshot is exactly the moment nobody is thinking about the version, because
the suite goes green either way once the bytes are rewritten. So this command refuses to
rewrite them.

The evidence is the golden itself. It is re-serialised and compared whole, so a new label
and a reordering count as the file moving alongside a changed command; and everything the
emitter was handed — `cairn_graph_sha256`, and the pins the file carries back in its
`params` and `env` — says whether the input moved too. **Output moved while input did not**
is the generator having moved, and nothing else; a plan edited in the corpus moves the graph
digest and a re-pin moves a pin, and both regenerate freely. When the generator moved and
the version recorded in the golden does not sit below the current one, this refuses and
names the constant. Nothing is written in that case — not even the shapes that did not move
— because a corpus half in one shape and half in another is a state nobody chose.

What it does not force: a person who rewrites or deletes the recorded bytes by hand is
choosing to, and a change to a value's meaning that leaves the bytes alone is invisible here
as everywhere.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, NamedTuple, cast

from cairn.layout import RUNS_DIRECTORY
from cairn.plan.schema import Graph, normalise
from cairn.topology import derive
from cairn.workflow.build import PYTHONPATH_ENV, build
from cairn.workflow.cli import EXIT_REFUSED, EXIT_USAGE
from cairn.workflow.schema import (
    GENERATOR_VERSION,
    LABEL_GENERATOR,
    LABEL_GRAPH_DIGEST,
    PARENT_BRANCH_PARAM,
    REPOSITORY_PARAM,
    WORKFLOW_SUFFIX,
    Workflow,
    read,
    serialise,
)

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PLANS = PACKAGE_ROOT / "fixtures" / "plans"
GOLDENS = PACKAGE_ROOT / "fixtures" / "workflows"
SCHEMA_MODULE = "cairn/workflow/schema.py"

# The pins. Each names a machine that does not exist, so a golden regenerated on any other
# checkout is the same file, and one carrying a real path is visible on sight.
REPOSITORY = Path("/srv/work/product")
PARENT_BRANCH = "main"
# The emitter declares this parameter empty on every file, because the run mints its own
# occasion at its first act ([marker.py]) — so the reproducible value is the default, and
# there is nothing here for a golden to pin against a clock. It is passed explicitly so a
# golden records what a caller handed the emitter rather than what the emitter defaults to.
OCCASION = ""
PYTHON_PATH = "/opt/cairn"
# Derived from the fictional repository rather than resolved through git, for the same reason
# the package root is a fiction: a golden must be the same file on every machine, and asking
# git would answer with whichever checkout regenerated it.
RUNS_ROOT = str(REPOSITORY / ".git" / "cairn" / RUNS_DIRECTORY)

# Where those values land in the emitted file, so a golden can be asked what it was built
# from rather than being trusted to have been built from these.
#
# `CAIRN_OCCASION` is deliberately absent: the emitter declares it empty on every file, so a
# change to it is the shape moving, and reading it as a pin would let the occasion's default
# change without anything asking about the generator version.
PINNED = frozenset({REPOSITORY_PARAM, PARENT_BRANCH_PARAM, PYTHONPATH_ENV})

# Every topology shape, simplest first. `single-step` is the degenerate chain, `all-roots`
# is the one whose first wave is itself a fan — so its worktrees hang off the lock rather
# than off a previous wave's commit — and `mixed-kinds` spans the kind table and the
# freshness scopes; the rest are what they say.
SHAPES: tuple[str, ...] = (
    "single-step",
    "linear-chain",
    "all-roots",
    "fan-out",
    "multi-wave",
    "mixed-kinds",
)


class Regeneration(NamedTuple):
    """What one run did, or refused to do, by shape."""

    written: list[str]
    unchanged: list[str]
    refused: dict[str, str]


def plan_graph(shape: str) -> Graph:
    with open(PLANS / shape / "graph.json", encoding="utf-8") as handle:
        return normalise(json.load(handle))


def build_shape(
    shape: str, *, python_path: str = PYTHON_PATH, runs_root: str = RUNS_ROOT
) -> Workflow:
    """The one statement of how a golden is built.

    The suite builds through here too, so a snapshot can never measure a second copy of the
    pipeline instead of the one this command writes. Only the package root is open: the
    engine-driving classes need a workflow whose steps can really import Cairn, and a golden
    must never carry the path where they found it.
    """
    graph = plan_graph(shape)
    topology = derive(graph, repository_root=REPOSITORY, parent_branch=PARENT_BRANCH)
    return build(
        graph,
        topology,
        occasion=OCCASION,
        python_path=python_path,
        runs_root=runs_root,
    )


def emitted(shape: str) -> str:
    """The text of one golden, which is what the suite compares a recorded file against."""
    return serialise(build_shape(shape))


def golden_path(shape: str, *, into: Path = GOLDENS) -> Path:
    return into / f"{shape}{WORKFLOW_SUFFIX}"


def _label(document: Any, key: str) -> str | None:
    """One label off a document Cairn may not have written.

    A label written as a JSON number is read as the text it stands for, because `describe`
    reads the same value that way and two readers of one constant that disagree about a file
    are worse than either reading alone.
    """
    if not isinstance(document, dict):
        return None
    labels: Any = cast(dict[str, Any], document).get("labels")
    if not isinstance(labels, dict):
        return None
    value: Any = cast(dict[str, Any], labels).get(key)
    return str(value) if isinstance(value, (str, int)) else None


def golden_document(path: Path) -> Any | None:
    """The golden on disk, or None where there is nothing to contradict.

    A file that is absent, no longer parses, or is not a document at all carries no claim
    about any generator version, so writing over it is a repair rather than a shape moving
    under a version that described another one.
    """
    try:
        recorded: Any = read(path)
    except (OSError, ValueError):
        return None
    if not isinstance(recorded, dict):
        return None
    return cast(dict[str, Any], recorded)


def inputs_of(document: Any) -> tuple[str | None, dict[str, str]]:
    """Everything the emitter was given, read back out of a document it wrote.

    The plan is a digest the file carries; the pins are carried as their own values, in the
    parameters and the environment. They are read **by name** rather than as whole
    `params`/`env` blocks, so that a value being re-pinned stays distinguishable from the
    emitter declaring a different set of parameters.
    """
    pins: dict[str, str] = {}
    for field in ("params", "env"):
        raw: Any = (
            cast(dict[str, Any], document).get(field) if isinstance(document, dict) else None
        )
        for entry in cast(list[Any], raw) if isinstance(raw, list) else []:
            if isinstance(entry, dict):
                for name, value in cast(dict[str, Any], entry).items():
                    if name in PINNED and isinstance(value, str):
                        pins[name] = value
    return _label(document, LABEL_GRAPH_DIGEST), pins


def input_moved(recorded: Any, rebuilt: Workflow) -> bool:
    """Whether the difference between two documents is one the emitter was handed.

    A re-pin is an input moving. A pinned parameter the emitter no longer declares, or one
    it has started declaring, is **not**: which values a caller may vary is part of the
    shape, so reading that as an input would let the file's whole parameter block change
    without anything asking about the version.
    """
    was_plan, was_pins = inputs_of(recorded)
    now_plan, now_pins = inputs_of(rebuilt)
    if was_plan != now_plan:
        # Two digests that disagree are the plan having moved. One that is missing on either
        # side is the emitter's own labels having moved, which is the shape — the same rule
        # the pins keep below.
        return was_plan is not None and now_plan is not None
    return set(was_pins) == set(now_pins) and was_pins != now_pins


def refusal(
    shape: str, recorded: Any, rebuilt: Workflow, *, generator: int = GENERATOR_VERSION
) -> str | None:
    """Why this shape must not be rewritten under this generator version, if it must not.

    The whole emitted text is what counts as the shape, not a digest of part of it:
    `body_digest` strips every `cairn_` label and re-sorts the keys, so judging on it would
    be blind to a new label, a moved engine pin and a reordering — and the labels are the
    region this constant is most about.
    """
    if recorded is None or serialise(cast(Workflow, recorded)) == serialise(rebuilt):
        return None
    if input_moved(recorded, rebuilt):
        return None
    stamped = _label(recorded, LABEL_GENERATOR)
    if stamped is None or (stamped.isdecimal() and generator > int(stamped)):
        return None
    collision = (
        f"generator version {stamped} already describes another one"
        if stamped == str(generator)
        else f"the golden records generator version {stamped}, which this generator "
        f"({generator}) does not sit above"
    )
    return (
        f"{shape}: the emitted file moved while everything it is built from stayed the "
        f"same, and {collision} — so a file written by one could not be told from a file "
        f"written by the other. Raise GENERATOR_VERSION in {SCHEMA_MODULE} above "
        f"{stamped}, undo the change to the emitted file, or restore the recorded golden — "
        "then run this again."
    )


def _publish(path: Path, payload: bytes) -> None:
    """Write one golden so a reader sees either the old file or the whole new one.

    The same discipline every other writer in the package keeps: an interrupt here would
    otherwise leave a truncated file, which the next run reads as a golden carrying no claim
    and repairs silently — a hole in the very refusal this command exists to make.
    """
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        # `mkstemp` opens at 0600, and a golden is a committed artefact other people read.
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def regenerate(*, into: Path = GOLDENS, generator: int = GENERATOR_VERSION) -> Regeneration:
    """Judge every shape before writing any of them."""
    rebuilt = {shape: build_shape(shape) for shape in SHAPES}
    refused = {
        shape: message
        for shape in SHAPES
        if (
            message := refusal(
                shape,
                golden_document(golden_path(shape, into=into)),
                rebuilt[shape],
                generator=generator,
            )
        )
    }
    if refused:
        return Regeneration([], [], refused)

    written: list[str] = []
    unchanged: list[str] = []
    for shape in SHAPES:
        path = golden_path(shape, into=into)
        # The bytes written are the bytes judged, and they are compared as bytes because
        # the suite does: a golden differing only in its line endings would otherwise be
        # called unchanged by the one command that could fix it.
        payload = serialise(rebuilt[shape]).encode("utf-8")
        if path.exists() and path.read_bytes() == payload:
            unchanged.append(shape)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        _publish(path, payload)
        written.append(shape)
    return Regeneration(written, unchanged, {})


def main(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="regenerate_workflows",
        description=__doc__.split("##")[0] if __doc__ else None,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--into",
        default=str(GOLDENS),
        help=f"the directory of recorded goldens (default {GOLDENS})",
    )
    parsed = parser.parse_args(arguments)
    into = Path(parsed.into)
    if into.exists() and not into.is_dir():
        # A refusal is a line, never a traceback: a caller cannot tell a crash from a
        # rejection, and `mkdir` over a file raises rather than reporting.
        print(f"error  {into} is not a directory", file=sys.stderr)
        return EXIT_USAGE

    outcome = regenerate(into=into)
    for message in outcome.refused.values():
        print(f"refused  {message}", file=sys.stderr)
    if outcome.refused:
        return EXIT_REFUSED
    for shape in outcome.written:
        print(f"wrote      {golden_path(shape, into=into)}")
    for shape in outcome.unchanged:
        print(f"unchanged  {golden_path(shape, into=into)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
