"""Thin engine-step emitters for the buildable vocabulary.

Every emitted step carries an explicit timeout and an explicit retry bound. I7 forbids an
unbounded step, and the engine supplies neither by default — its own step timeout is none,
and a step it inherits nothing from retries not at all while the *DAG* around it retries
three times ([01]). Neither default is one Cairn is willing to run on, so both are written
on every step and a test fails if any step is emitted without them. An agent step carries
two bounds more, its model and its dollar ceiling, because its body opens a paid session:
a session with no ceiling is the one thing an offer cannot price, and one whose model the
environment chose leaves a record that cannot say which model did the work ([17.3]).
"""

from __future__ import annotations

import shlex
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cairn.plan.schema import (
    AGENT_FAMILY,
    INPUTS_SCOPE,
    MERGE_RETRIES,
    MERGE_TIMEOUT,
    RETRY_INTERVAL,
    SUPPORT_RETRIES,
    SUPPORT_TIMEOUT,
    WAIT_REPORT_GRACE,
    Step,
    cannot_fail,
    has_assertion,
    is_unasserted,
)
from cairn.topology import ROLES, Node
from cairn.verify import (
    BRANCH,
    POSITIONS,
    exit_status_reference,
    mark_name,
    verify_handle,
    verify_name,
)
from cairn.workflow.schema import CAIRN_INVOCATION, EngineStep

Emitter = Callable[[Step, str], EngineStep]


def retry_policy(limit: int, interval_seconds: int) -> dict[str, Any]:
    """The engine's retry policy, always spelled in full.

    `retry_policy: {limit: 0}` alone is rejected — `interval_sec` is required whenever the
    policy is present, so "never retry" is written with an interval it will never use
    ([01]).
    """
    return {"limit": limit, "interval_sec": interval_seconds}


def _base(step: Step, working_directory: str) -> EngineStep:
    return {
        "name": step["id"],
        "working_dir": working_directory,
        "timeout_sec": step["timeout"],
        # Zero unless the plan asked for more. Every failure an agent step can have is
        # either a wrong task or a paid session that already changed the repository, and
        # retrying either buys nothing; a rate limit is reported with the moment it clears
        # rather than waited out blind (09).
        "retry_policy": retry_policy(step["retries"], RETRY_INTERVAL),
    }


def emit_command(step: Step, working_directory: str) -> EngineStep:
    command = step.get("command")
    if command is None:
        raise ValueError(f"command step {step['id']!r} has no executable command")
    command_type = step.get("command_type")
    if command_type is None:
        raise ValueError(f"command step {step['id']!r} has no command type")
    if command_type == "wait_until":
        arguments = (
            *CAIRN_INVOCATION,
            "wait",
            "--until",
            command,
            "--timeout",
            str(step["timeout"]),
        )
    else:
        arguments = (*CAIRN_INVOCATION, "exec", "--command", command)
    emitted = _base(step, working_directory)
    if command_type == "wait_until":
        emitted["timeout_sec"] = step["timeout"] + WAIT_REPORT_GRACE
    emitted["run"] = shlex.join(arguments)
    return emitted


def emit_agent(step: Step, working_directory: str) -> EngineStep:
    """One paid session, with every bound it runs under written into its own body.

    The model and the ceiling are written here and not resolved at run time, because the
    definition is what an offer prices: a bound the environment supplied would let a person
    accept a run whose price and model nobody had stated ([17.3]).
    """
    provider = step["kind"][len(AGENT_FAMILY) :]
    model = step.get("model")
    budget = step.get("max_budget_usd")
    if not model or budget is None or budget <= 0:
        raise ValueError(
            f"agent step {step['id']!r} carries no model or no positive dollar ceiling, "
            "so the session it opens could not be priced"
        )
    arguments = [
        *CAIRN_INVOCATION,
        "agent",
        "run",
        "--provider",
        provider,
        "--prompt",
        step["task"],
        "--model",
        model,
        "--max-budget-usd",
        str(budget),
    ]
    for deny_pattern in step["tools"] or []:
        arguments.extend(("--tool", deny_pattern))
    emitted = _base(step, working_directory)
    emitted["run"] = shlex.join(arguments)
    return emitted


KIND_EMITTERS: dict[str, Emitter] = {"command": emit_command, "agent.*": emit_agent}


def _refuse_unquoted(step_id: str, body: str) -> None:
    if body != shlex.join(shlex.split(body)):
        raise ValueError(f"step {step_id!r} emits a body that is not one quoted invocation")


def marker_gate(step: Step) -> str:
    """The command whose exit status decides whether this step's work still has to happen."""
    arguments = [
        *CAIRN_INVOCATION,
        "marker",
        "absent",
        "--step",
        step["id"],
        "--scope",
        step["scope"],
    ]
    if step["scope"] == INPUTS_SCOPE:
        for path in step["reads"]:
            arguments.extend(("--reads", path))
    return shlex.join(arguments)


def _refuse_unkeyable_reads(step: Step) -> None:
    """Refuse an `inputs` declaration whose key could only ever fail to compute.

    The gate fails open on an unkeyable declaration while the marker write fails closed,
    so such a step does its work and is then excluded, every run, for ever. Emission is
    where the plan author can still be told, and where the tree is not yet consulted.
    """
    if step["scope"] != INPUTS_SCOPE:
        return
    if not step["reads"]:
        raise ValueError(
            f"step {step['id']!r} declares scope 'inputs' and reads nothing, so no key "
            "can be computed for it"
        )
    escaping = [path for path in step["reads"] if Path(path).is_absolute()]
    if escaping:
        raise ValueError(
            f"step {step['id']!r} reads {escaping} outside its own working directory"
        )


def _refuse_unasserted(step: Step) -> None:
    """Refuse a step nobody has been asked about.

    A missing assertion is a warning at authoring time and an error here, because this is
    the last place it can be raised against a human rather than discovered in a run that
    reported success. An unverified step is honest only when someone declined a proposal;
    one that simply never came up would read the same in the report and mean nothing.
    """
    if is_unasserted(step):
        raise ValueError(
            f"step {step['id']!r} has no verify command and no recorded answer, so nobody "
            "has been asked what asserts its end state. Run `cairn plan propose` and "
            "record an answer with `cairn plan answer`."
        )


def _refuse_unassertable(step: Step) -> None:
    """Refuse an assertion that cannot fail.

    A command that always exits zero reads as verified in the report while asserting
    nothing, which is worse than the declared absence the author could have chosen
    instead.
    """
    command = step["verify"]
    if command is None:
        return
    if cannot_fail(command):
        raise ValueError(
            f"step {step['id']!r} has the verify command {command!r}, which cannot fail. "
            "Assert the step's end state, or decline the assertion and record why."
        )


def emit_step(step: Step, working_directory: str) -> EngineStep:
    key = "agent.*" if step["kind"].startswith(AGENT_FAMILY) else step["kind"]
    try:
        emitter = KIND_EMITTERS[key]
    except KeyError as exc:
        raise ValueError(f"no plan-step emitter for {step['kind']!r}") from exc
    _refuse_unkeyable_reads(step)
    _refuse_unasserted(step)
    _refuse_unassertable(step)
    emitted = emitter(step, working_directory)
    gate = marker_gate(step)
    emitted["preconditions"] = [{"condition": gate}]
    # `skipped` keeps the engine from cascading a correct no-op into this step's own
    # verify, its marker, and the merge join beyond it — and a run whose nodes are all
    # skipped, with no failed node anywhere, reports plain Succeeded with exit 0.
    # `failure` keeps a step's own reported failure from aborting its assertion: the
    # assertion has to run over a step that says it failed, or a step that reported
    # failure over work that is actually there could never be told from one that did not.
    emitted["continue_on"] = {"failure": True, "skipped": True}
    for body in (emitted["run"], gate):
        _refuse_unquoted(step["id"], body)
    return emitted


def emit_verify(step: Step, working_directory: str) -> EngineStep:
    """The plan's assertion, run verbatim, with nothing of Cairn's between it and the engine.

    It carries an explicit `id` because that is the only name the gate's exit-status
    reference can reach it by, and it never retries: an assertion is a fact check, and
    asking it twice asks a different question.
    """
    command = step["verify"]
    if command is None:
        raise ValueError(f"step {step['id']!r} declares no assertion to emit")
    return {
        "name": verify_name(step["id"]),
        "id": verify_handle(step["id"]),
        "run": command,
        # The assertion reads the tree the step wrote, so it stands where the step stood.
        "working_dir": working_directory,
        "timeout_sec": SUPPORT_TIMEOUT,
        "retry_policy": retry_policy(0, RETRY_INTERVAL),
        # Stay recorded as failed while the run survives to reach the join. Without this
        # the failure aborts everything downstream, and in a fan-out that is the merge —
        # so one branch's failed assertion would land nothing at all.
        "continue_on": {"failure": True},
    }


def verify_gate(step: Step, position: str) -> str:
    """The command whose exit status decides whether this step's work may be recorded."""
    arguments = [
        *CAIRN_INVOCATION,
        "verify",
        "gate",
        "--step",
        step["id"],
        "--position",
        position,
    ]
    if has_assertion(step):
        arguments.extend(("--verify-exit", exit_status_reference(step["id"])))
    return shlex.join(arguments)


def emit_marker(step: Step, working_directory: str, position: str) -> EngineStep:
    """The step that records verified work, gated on the assertion and the step's own report.

    It carries no `continue_on` of its own in either position. A closed gate skips this
    step, and that skip has to reach the commit — measured against Dagu 2.11.0, a
    `continue_on: {skipped: true}` here lets the commit run anyway, which lands exactly
    the unverified work the gate refused to record. The flag belongs to the commit, the
    last node before the join.
    """
    if position not in POSITIONS:
        raise ValueError(f"step {step['id']!r} has an unknown graph position {position!r}")
    arguments = [
        *CAIRN_INVOCATION,
        "marker",
        "write",
        "--step",
        step["id"],
        "--scope",
        step["scope"],
    ]
    if step["scope"] == INPUTS_SCOPE:
        for path in step["reads"]:
            arguments.extend(("--reads", path))
    emitted: EngineStep = {
        "name": mark_name(step["id"]),
        "run": shlex.join(arguments),
        # The marker lands beside the work it describes, so this step stands where the
        # step it marks stood.
        "working_dir": working_directory,
        "timeout_sec": SUPPORT_TIMEOUT,
        "retry_policy": retry_policy(0, RETRY_INTERVAL),
        "preconditions": [{"condition": verify_gate(step, position)}],
    }
    return emitted


def _support_step(
    name: str,
    working_directory: str,
    arguments: list[str],
    *,
    timeout: int = SUPPORT_TIMEOUT,
    retries: int = SUPPORT_RETRIES,
) -> EngineStep:
    return {
        "name": name,
        "working_dir": working_directory,
        "timeout_sec": timeout,
        "retry_policy": retry_policy(retries, 1),
        "run": shlex.join([*CAIRN_INVOCATION, *arguments]),
    }


def emit_lock(node: Node, run_timeout_seconds: int) -> EngineStep:
    action = str(node["detail"]["action"])
    arguments = ["lock", action]
    if action == "acquire":
        arguments.extend(
            ("--plan", str(node["detail"]["plan"]), "--run-timeout", str(run_timeout_seconds))
        )
    return _support_step(node["name"], node["working_directory"], arguments)


def emit_setup(node: Node) -> EngineStep:
    """Where the worktree goes is derived at run time, not written into the body.

    The path is `<repository>.cairn-worktrees/<plan>/<step>`, so writing it here would bake
    one repository into the file — and a parameter reference cannot stand in a body, because
    the only spelling that survives quoting also runs whatever the value holds
    ([workflow.md]). The step's own working directory is the repository, so the subcommand
    derives the path from where it already stands.
    """
    detail = node["detail"]
    return _support_step(
        node["name"],
        node["working_directory"],
        [
            "worktree",
            "setup",
            "--plan",
            str(detail["plan"]),
            "--step",
            str(node["step"]),
            "--branch",
            str(detail["branch"]),
        ],
    )


def emit_prune(node: Node) -> EngineStep:
    """The wave's steps name what to remove; the paths and branches derive from them."""
    detail = node["detail"]
    arguments = ["worktree", "prune", "--plan", str(detail["plan"])]
    for step_id in list(detail["steps"]):
        arguments.extend(("--step", str(step_id)))
    return _support_step(node["name"], node["working_directory"], arguments)


def emit_join(node: Node) -> EngineStep:
    """The wave's one census, taken at the only moment it can be taken.

    A slot's landing moves a branch tip, and a branch that has landed is an ancestor of the
    parent exactly as a branch that never carried work is — so after the first merge,
    nothing can tell an excluded step from a landed one ([merge-step.md]). The join stands
    before any landing and is the only node that sees the whole wave, so it records which
    branches arrived with work and why the others did not.

    It carries no `continue_on` in either spelling. An excluded branch reaches it already,
    because that branch's commit stops the skip cascade in a fan-out; what must not be
    absorbed is a genuine failure, which has to stop the slots rather than let them land
    over a wave nobody could survey.
    """
    arguments = ["wave", "join", "--wave", str(node["wave"])]
    for branch in list(node["detail"]["branches"]):
        arguments.extend(("--branch", str(branch)))
    return _support_step(node["name"], node["working_directory"], arguments)


def emit_merge(node: Node) -> EngineStep:
    """One slot of a wave's landing, bounded like the session it may have to pay for.

    Every slot carries the whole candidate list, because which branch it lands is its own
    decision on evidence that does not exist until run time — the prediction compares
    committed tips. It carries no `continue_on` in either spelling: a merge that halts must
    stop the slots behind it and the prune after them, and a flag here would let the next
    slot write over a conflicted index.
    """
    detail = node["detail"]
    arguments = [
        "merge",
        "land",
        "--slot",
        str(detail["slot"]),
        "--provider",
        str(detail["provider"]),
    ]
    for branch in list(detail["candidates"]):
        arguments.extend(("--branch", str(branch)))
    return _support_step(
        node["name"],
        node["working_directory"],
        arguments,
        timeout=MERGE_TIMEOUT,
        retries=MERGE_RETRIES,
    )


def emit_merge_verify(node: Node) -> EngineStep:
    """The proof that what the slot before it says it landed is in the parent branch."""
    detail = node["detail"]
    arguments = ["merge", "verify", "--merge", str(detail["merge"])]
    for branch in list(detail["candidates"]):
        arguments.extend(("--branch", str(branch)))
    return _support_step(node["name"], node["working_directory"], arguments)


def emit_commit(node: Node, message: str) -> EngineStep:
    """The step that lands verified work, and the one node in a step's group that routes.

    Exactly one node carries the position flag, and it is this one because it is the last
    before the join. A closed gate skips the marker, that skip cascades here, and what
    happens next is the whole of how failure routes: in a fan-out the flag stops the
    cascade at this branch so the join still runs and the merge sees no new work, and in a
    chain its absence lets the cascade carry on into everything that depended on the work.
    """
    emitted = _support_step(
        node["name"], node["working_directory"], ["commit", "--message", message]
    )
    position = str(node["detail"]["position"])
    if position not in POSITIONS:
        raise ValueError(f"node {node['name']!r} has an unknown graph position {position!r}")
    if position == BRANCH:
        emitted["continue_on"] = {"skipped": True}
    return emitted


def emit_node(
    node: Node, *, steps: dict[str, Step], run_timeout_seconds: int
) -> EngineStep:
    """Turn one topology node into one engine step. Every role emits."""
    role = node["role"]
    if role not in ROLES:
        raise ValueError(f"{node['name']!r} carries {role!r}, which is not a topology role")
    if role == "lock":
        emitted = emit_lock(node, run_timeout_seconds)
    elif role == "setup":
        emitted = emit_setup(node)
    elif role == "prune":
        emitted = emit_prune(node)
    elif role == "commit":
        step = _step_of(node, steps)
        emitted = emit_commit(node, f"cairn({step['id']}): {step['title']}")
    elif role == "work":
        emitted = emit_step(_step_of(node, steps), node["working_directory"])
        emitted["name"] = node["name"]
    elif role == "mark":
        emitted = emit_marker(
            _step_of(node, steps), node["working_directory"], str(node["detail"]["position"])
        )
    elif role == "merge":
        emitted = emit_merge(node)
    elif role == "join":
        emitted = emit_join(node)
    elif role == "verify":
        # A `verify` node names a step when it runs that step's own assertion, and names
        # none when it proves a merge. The two are the same role because both answer "is
        # what was claimed actually there", and only the first is a command the plan's
        # author wrote.
        emitted = (
            emit_merge_verify(node)
            if node["step"] is None
            else emit_verify(_step_of(node, steps), node["working_directory"])
        )
    else:
        raise ValueError(f"no emitter for the topology role {role!r}")
    if node["after"]:
        emitted["depends"] = list(node["after"])
    # The quoting rule is about bodies Cairn builds, so it is applied to those and not to
    # a step's assertion, whose body is the plan author's own shell line — pipes, globs and
    # all. A merge's proof is Cairn's own body and is held to it.
    if role != "verify" or node["step"] is None:
        _refuse_unquoted(node["name"], str(emitted["run"]))
    return emitted


def _step_of(node: Node, steps: dict[str, Step]) -> Step:
    step_id = node["step"]
    if step_id is None:
        raise ValueError(f"node {node['name']!r} names no step")
    try:
        return steps[step_id]
    except KeyError as exc:
        raise ValueError(
            f"node {node['name']!r} names step {step_id!r}, which is not in this graph"
        ) from exc


__all__ = [
    "EngineStep",
    "emit_agent",
    "emit_command",
    "emit_commit",
    "emit_join",
    "emit_lock",
    "emit_marker",
    "emit_merge",
    "emit_merge_verify",
    "emit_node",
    "emit_prune",
    "emit_setup",
    "emit_step",
    "emit_verify",
    "marker_gate",
    "retry_policy",
]
