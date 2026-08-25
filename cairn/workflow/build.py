"""Plan graph to whole engine definition: the pipeline, and every default written out.

The pipeline is `Graph` → `Topology` ([topology.py]) → one `EngineStep` per node through the
kind table ([emitters.py]) → a `Workflow`. Nothing here decides what the branches are or what
a body says; what it owns is the file around them, which is where every machine-level default
either gets stated or gets inherited from a configuration file Cairn did not write.

**Omission is inheritance, not neutrality.** The engine writes `~/.config/dagu/base.yaml` on
its first invocation and every DAG on that machine inherits it ([01]), so a field this
builder leaves out is a field the machine decides. Each one below is emitted for a measured
reason, and `max_active_steps` is the one that had to be measured twice.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, cast

from cairn.emitters import emit_node
from cairn.layout import RUNS_ROOT_ENV
from cairn.plan.schema import RETRY_INTERVAL, Graph, Step
from cairn.topology import WORKTREES_SUFFIX, Topology
from cairn.workflow.schema import (
    CATCHUP_DISABLED,
    ENGINE_VERSION,
    GENERATOR_VERSION,
    GRAPH_TYPE,
    LABEL_BODY_DIGEST,
    LABEL_ENGINE,
    LABEL_GENERATOR,
    LABEL_GRAPH_DIGEST,
    LABEL_PLAN,
    OCCASION_PARAM,
    OVERLAP_SKIP,
    PARENT_BRANCH_PARAM,
    REPOSITORY_PARAM,
    EngineStep,
    RetryPolicy,
    Workflow,
    body_digest,
    reference,
)

PYTHONPATH_ENV = "PYTHONPATH"
EXIT_HANDLER = "exit"


def disabled_retry() -> RetryPolicy:
    """The DAG-level policy, off, spelled with the interval the schema demands.

    The engine's shipped configuration carries an active `limit: 3`, and its scheduler's
    retry scanner reaches every failed run on the machine — for Cairn a failed run is a paid
    agent session that already changed a repository ([01]).
    """
    return {"limit": 0, "interval_sec": RETRY_INTERVAL}


def step_concurrency(nodes: int) -> int:
    """The cap, stated as a number that cannot be read as "unset".

    `max_active_steps: 0` does **not** mean unlimited: measured against Dagu 2.11.0, a file
    emitting zero alongside a `base.yaml` carrying ten ran ten steps at a time, exactly as a
    file that omitted the field did. Zero is inheritance wearing the shape of an override,
    which is the one thing this builder exists to prevent — so the cap is the node count,
    which no wave can exceed.
    """
    return max(nodes, 1)


def worktree_directory(plan_slug: str, step_id: str) -> str:
    """Where an isolated step stands, written against the repository parameter.

    The worktrees root is the repository's own path plus a suffix ([topology.py]), so one
    parameter reaches both — and a caller who retargets the repository moves the worktrees
    with it rather than leaving half the run pointing at the old one.
    """
    return f"{reference(REPOSITORY_PARAM)}{WORKTREES_SUFFIX}/{plan_slug}/{step_id}"


def _working_directory(node_directory: str, topology: Topology) -> str:
    """Rewrite a derived absolute directory as the parameter it was derived from.

    An exact match against the values the topology actually produced, never a substring
    replacement over the text: a commit message or an agent's prose that happened to quote
    the repository path would otherwise be rewritten into a reference.
    """
    repository = topology["repository"]
    if node_directory == repository:
        return reference(REPOSITORY_PARAM)
    root = topology["worktrees_root"]
    if node_directory.startswith(f"{root}/"):
        return worktree_directory(
            topology["plan"], node_directory[len(root) + 1 :]
        )
    raise ValueError(
        f"node directory {node_directory!r} is neither the repository nor a worktree of "
        "this plan, so it cannot be written against the repository parameter"
    )


def envelope(
    steps: list[EngineStep],
    *,
    repository: str,
    parent_branch: str,
    occasion: str,
    python_path: str,
    runs_root: str,
    handler: EngineStep | None = None,
    schedule: str | None = None,
) -> Workflow:
    """The file around a set of steps, with every machine-level default written out.

    This is the only statement of the format. Anything that needs a workflow — the
    generator, and the suites that drive a real engine — comes through here, so there is one
    place where a default is either overridden or inherited.

    A `schedule` is the one thing here a caller asks for rather than receives. It is an
    escalation with a stated cost — a persistent daemon, a watched directory, and a retry
    policy neutralised before it starts ([triggers.md]) — so it is never a side effect of
    wanting a recurring plan.
    """
    # Placed straight after `type` so a person opening the file reads what it is and when it
    # runs before anything else. The expression is the engine's to judge: measured, `dagu
    # validate` refuses a malformed cron, which is one of the few places its own validator
    # is not blind, so Cairn parses none of it.
    when: dict[str, str] = {} if schedule is None else {"schedule": schedule}
    document: dict[str, Any] = {
        "type": GRAPH_TYPE,
        **when,
        "max_active_steps": step_concurrency(len(steps)),
        "retry_policy": disabled_retry(),
        # Both of these exist only to stop the machine deciding. Measured on this machine,
        # the engine's shipped `base.yaml` carries `catchup_window: "6h"` — so a scheduler
        # restarting after downtime replays every missed slot as a fresh paid agent session
        # — and an `overlap_policy` a file does not state is whatever that same file holds.
        "catchup_window": CATCHUP_DISABLED,
        "overlap_policy": OVERLAP_SKIP,
        "labels": {},
        "params": [
            {REPOSITORY_PARAM: repository},
            {PARENT_BRANCH_PARAM: parent_branch},
            {OCCASION_PARAM: occasion},
        ],
        # The engine hands a step a curated environment rather than the caller's, so
        # `PYTHONPATH` does not survive into one ([06]). A gate that cannot import Cairn
        # exits nonzero from outside Cairn, which the engine reads as a skip — and a run
        # whose every step skipped, with no failed node anywhere, reports a clean success.
        #
        # The runs root travels the same way, and it is what makes a step's report land in
        # Cairn's own state rather than wherever the engine happened to put its log
        # ([run-model.md]). Measured against Dagu 2.11.0, an `env:` entry reaches the
        # lifecycle handler as well as every step, so the run's release can write its own
        # report — which is the one report a failed run always has to leave.
        "env": [{PYTHONPATH_ENV: python_path}, {RUNS_ROOT_ENV: runs_root}],
        # The release is a lifecycle handler rather than a node: the engine never dispatches
        # a step whose dependency failed, so a failed run would otherwise hold its
        # repository for the whole reclaim window ([topology.py]).
        "handler_on": {} if handler is None else {EXIT_HANDLER: handler},
        "steps": steps,
    }
    return cast(Workflow, document)


def build(
    graph: Graph,
    topology: Topology,
    *,
    occasion: str,
    python_path: str,
    runs_root: str,
    schedule: str | None = None,
) -> Workflow:
    """Assemble the whole file: graph → topology → bodies → a validated definition."""
    steps: dict[str, Step] = {step["id"]: step for step in graph["steps"]}
    run_timeout = topology["max_seconds"]

    emitted: list[EngineStep] = []
    for node in topology["nodes"]:
        engine_step = emit_node(node, steps=steps, run_timeout_seconds=run_timeout)
        engine_step["working_dir"] = _working_directory(
            node["working_directory"], topology
        )
        emitted.append(engine_step)

    release = emit_node(
        topology["on_exit"], steps=steps, run_timeout_seconds=run_timeout
    )
    release["working_dir"] = _working_directory(
        topology["on_exit"]["working_directory"], topology
    )

    document = envelope(
        emitted,
        repository=topology["repository"],
        parent_branch=topology["parent_branch"],
        occasion=occasion,
        python_path=python_path,
        runs_root=runs_root,
        handler=release,
        schedule=schedule,
    )
    document["labels"] = _stamp_labels(graph, document)
    return document


def _stamp_labels(graph: Graph, document: Workflow) -> dict[str, str]:
    return {
        LABEL_PLAN: graph["plan"]["slug"],
        LABEL_GENERATOR: str(GENERATOR_VERSION),
        LABEL_ENGINE: ENGINE_VERSION,
        LABEL_GRAPH_DIGEST: graph_digest(graph),
        LABEL_BODY_DIGEST: body_digest(document),
    }


def graph_digest(graph: Graph) -> str:
    """The identity of the plan this workflow was built from."""
    canonical = json.dumps(graph, indent=2, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "EXIT_HANDLER",
    "PYTHONPATH_ENV",
    "build",
    "disabled_retry",
    "envelope",
    "graph_digest",
    "step_concurrency",
    "worktree_directory",
]
