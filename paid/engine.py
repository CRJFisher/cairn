"""A real definition, bounded before it is run, and a real engine to run it with.

Two cases here drive a whole workflow rather than a conversation, and both need the same
thing: the definition Cairn's own generator produces, with every body that can open a paid
session given a model and a ceiling first.

**The bound is an appended override, not a rewrite of anything unbounded.** `emit_agent`
writes every agent body's `--model` and `--max-budget-usd` from the step record (17.3), and
`emit_merge` still writes neither, so this harness appends the flags to both: on a merge
body they are the only bound, and on an agent body argparse's last-one-wins makes them the
harness's override of the plan's own — which is what pins every session this suite pays for
to one model and one ceiling whatever the fixture plans say. The append is the same move the
free suite makes substituting a provider at `tests/test_merge_step.py:1036`.

Every body is bounded or the run is refused. A definition holding one unbounded session is
the one thing this suite cannot price before it runs, which is what the whole ladder in
`spend.py` exists to prevent.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path
from typing import Any, cast

from cairn.core import CairnError, launch
from cairn.emitters import emit_node
from cairn.enginehome import ENGINE_BINARY
from cairn.gitio import runs_root
from cairn.layout import record_path
from cairn.plan.schema import normalise
from cairn.record.model import RunRecord, StepRecord
from cairn.record.vocabulary import OUTCOME_NOT_REACHED, OUTCOME_PENDING
from cairn.topology import derive
from cairn.verify import NOT_REACHED
from cairn.workflow.build import build
from cairn.workflow.schema import CAIRN_INVOCATION, EngineStep, Workflow, is_agent_body

from paid.session import Group, GroupFactory, stop_group

MERGE_SUBCOMMAND = ("merge", "land")


def is_merge_body(body: str) -> bool:
    """Whether this step body lands a wave's branch, and so may reach a resolving session."""
    argv = shlex.split(body)
    prefix = len(CAIRN_INVOCATION)
    return (
        argv[:prefix] == list(CAIRN_INVOCATION)
        and argv[prefix : prefix + len(MERGE_SUBCOMMAND)] == list(MERGE_SUBCOMMAND)
    )


def bound_body(body: str, *, model: str, budget_usd: float) -> str:
    """One body, with the model and the ceiling appended — or unchanged, having neither."""
    if not (is_agent_body(body) or is_merge_body(body)):
        return body
    return shlex.join([*shlex.split(body), "--model", model, "--max-budget-usd", str(budget_usd)])


def bound(document: Workflow, *, model: str, budget_usd: float) -> Workflow:
    """Every body that can open a session, bounded, and the rest left exactly as emitted."""
    for step in document["steps"]:
        step["run"] = bound_body(str(step["run"]), model=model, budget_usd=budget_usd)
    return document


def unbounded_bodies(document: Workflow) -> list[str]:
    """Every body that would open a session without a ceiling. Empty is the only acceptable
    answer, and the runner refuses on anything else rather than discovering it in a bill."""
    return [
        str(step["run"])
        for step in document["steps"]
        if (is_agent_body(str(step["run"])) or is_merge_body(str(step["run"])))
        and "--max-budget-usd" not in shlex.split(str(step["run"]))
    ]


def definition(
    graph: dict[str, Any],
    *,
    repository: Path,
    parent_branch: str,
    occasion: str,
    python_path: str,
    runs_root: Path,
    model: str,
    budget_usd: float,
) -> Workflow:
    """The generator's own output for this graph, bounded before anything can run it."""
    normalised = normalise(graph)
    topology = derive(normalised, repository_root=repository, parent_branch=parent_branch)
    document = bound(
        build(
            normalised,
            topology,
            occasion=occasion,
            python_path=python_path,
            runs_root=str(runs_root),
        ),
        model=model,
        budget_usd=budget_usd,
    )
    left = unbounded_bodies(document)
    if left:
        raise CairnError(
            "invalid_arguments",
            f"{len(left)} body/bodies would open an unbounded session: {left[0]}",
        )
    return document


def write_definition(document: Workflow, path: Path) -> Path:
    """JSON, which the engine reads as YAML, so what runs is exactly what was built."""
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return path


def start(
    definition_path: Path,
    run_id: str,
    *,
    cwd: Path,
    variables: dict[str, str],
    parameters: dict[str, str],
    timeout: float,
    popen_factory: GroupFactory = subprocess.Popen,
) -> subprocess.CompletedProcess[str]:
    """One real engine run, blocking, in a process group of its own.

    The group is the point on the timeout path. `subprocess.run(timeout=…)` kills the direct
    child and nothing else, so a `dagu` that overran would leave the sessions it started
    running, spending, and writing into a repository this suite is about to remove.
    """
    line = (
        ENGINE_BINARY,
        "start",
        "--run-id",
        run_id,
        "--params",
        " ".join(f"{name}={value}" for name, value in parameters.items()),
        str(definition_path),
    )
    process = cast(
        Group,
        launch(
            popen_factory,
            line,
            cwd=str(cwd),
            env=variables,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        ),
    )
    try:
        out, err = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        stop_group(process, immediate=True)
        out, err = process.communicate()
    return subprocess.CompletedProcess(
        line, process.returncode or 0, out or "", err or ""
    )


def emitted_node(node: Any, steps: dict[str, Any], seconds: int) -> EngineStep:
    """One node's body, the way the generator writes it, for a chain assembled by hand."""
    return emit_node(node, steps=steps, run_timeout_seconds=seconds)


def record_of(repository: Path, run_id: str) -> RunRecord | None:
    """The run's own record, read from where `cairn record build` left it.

    Typed as the model rather than as raw JSON, because every case here asks the same six
    questions of it and answering them against `Any` is how a field name goes stale without
    anything noticing.
    """
    path = record_path(runs_root(repository), run_id)
    if not path.is_file():
        return None
    try:
        loaded: Any = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return cast(RunRecord, loaded) if isinstance(loaded, dict) else None


def steps_of(record: RunRecord | None) -> list[StepRecord]:
    if record is None or "steps" not in record:
        return []
    return list(record["steps"])


def divergences(record: RunRecord | None) -> tuple[int, int]:
    """Numerator and denominator for the divergence rate, over one run's steps.

    The denominator is the gates that actually ran: a step downstream of a halt never
    reached its assertion, and counting it would put the run's shape into the rate.
    """
    # On the outcome rather than the cause: a step behind a halt is recorded `not_reached`
    # with cause None, so filtering on the cause alone would keep in the denominator exactly
    # the steps whose assertion never ran.
    reached = [
        step
        for step in steps_of(record)
        if step["outcome"] not in (OUTCOME_NOT_REACHED, OUTCOME_PENDING)
        and step["cause"] != NOT_REACHED
    ]
    return sum(1 for step in reached if step["divergence"] is not None), len(reached)
