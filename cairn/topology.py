"""Graph to branches, worktrees and nodes — a pure derivation with no engine words in it.

Steps that do not depend on each other get their own worktree and branch; steps that do,
chain. The dependencies bound the merge order, and a merge-join folds the branches back
into the parent branch.

Concurrency itself is not derived here. `depends` *is* the engine's concurrency model, so
emitting edges is the whole of it ([research-dagu.md]); what the engine cannot do is decide
what the branches are, and that is this module.

Nothing here touches git, a clock or the filesystem: the same graph and the same repository
root always give the same topology, which is what makes the golden tests structural.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, NamedTuple, TypedDict

from cairn.plan.schema import (
    AGENT_FAMILY,
    DEFAULT_KIND,
    MERGE_RETRIES,
    MERGE_TIMEOUT,
    RETRY_INTERVAL,
    SUPPORT_RETRIES,
    SUPPORT_TIMEOUT,
    WAIT_REPORT_GRACE,
    Graph,
    Step,
    has_assertion,
    step_max_seconds,
)
from cairn.verify import BRANCH, CHAIN

# The engine enforces `^[a-zA-Z][a-zA-Z0-9_]*$` on a step name and rejects a hyphen with a
# `use '_' instead of '-'` hint (01). The length bound and the reserved words are Cairn's:
# a name is parsed back into a role and a step id by the run model (12), so a truncated one
# would silently stop round-tripping.
ENGINE_NAME_MAX_BYTES = 40
RESERVED_NAMES = frozenset(
    {"env", "params", "args", "stdout", "stderr", "output", "outputs"}
)

# Every node's name is `<role>_<subject>`, and the role is the text before the first
# underscore. Roles are a closed set, so a step whose own id begins with a role name still
# parses: `work_work_config` splits into the role `work` and the step `work_config`. The
# closure is enforced by both the producer and the parser, because the round-trip is what
# the run model reads names back with and a comment cannot hold it.
STEP_ROLES = ("setup", "work", "verify", "mark", "commit")
WAVE_ROLES = ("join", "prune", "merge")
RUN_ROLES = ("lock",)
ROLES = STEP_ROLES + WAVE_ROLES + RUN_ROLES

# The engine enforces this on every step name and rejects a hyphen with its own hint.
ENGINE_NAME = re.compile(r"[a-zA-Z][a-zA-Z0-9_]*")

WORKTREES_SUFFIX = ".cairn-worktrees"
BRANCH_PREFIX = "step/"

# Two numbers, because they answer two questions at different scales.
#
# The **critical path** is how long the run can plausibly take, and a plan whose slowest
# chain runs past two working days is refused at generation time: past that, a repository
# held against every other run is itself the failure, whatever the plan would achieve. This
# is the number a plan author can act on — shorten the chain and it moves.
#
# The **sum** is how long the run might *still be writing*, which is what a lease has to
# survive: the engine caps concurrent steps, so a wave wider than the cap outruns its own
# critical path. Gating admission on the sum instead would refuse a plan of sixteen
# ordinary agent steps, which is a plan-size cap nobody asked for.
RUN_CEILING_SECONDS = 172800


class TopologyError(Exception):
    """A graph that cannot become a topology — refused at generation time, never at run time."""


class Node(TypedDict):
    """One unit of work in the topology, before anything decides what an engine calls it."""

    name: str
    role: str
    step: str | None
    wave: int
    working_directory: str
    after: list[str]
    max_seconds: int
    detail: dict[str, Any]


class Branch(TypedDict):
    """One step's isolated line of work, and where it is checked out."""

    name: str
    step: str
    wave: int
    worktree: str
    base: str


class Wave(TypedDict):
    """One level of the dependency graph, and whether its steps are isolated."""

    index: int
    steps: list[str]
    isolated: bool


class Topology(TypedDict):
    """The whole derivation. `on_exit` runs however the run ends, `nodes` only if it gets there."""

    plan: str
    repository: str
    parent_branch: str
    worktrees_root: str
    waves: list[Wave]
    branches: list[Branch]
    nodes: list[Node]
    on_exit: Node
    merge_order: list[list[str]]
    max_seconds: int
    critical_path_seconds: int


class Naming(NamedTuple):
    """The node-name grammar, stated once because the run model parses it (12)."""

    role: str
    subject: str


def worktrees_parent(repository_root: Path) -> Path:
    """Where every plan's worktrees live, beside the repository they belong to.

    One statement of the arithmetic, because there are two derivations of it and they must
    agree: this one, from the repository a step is standing in, and the emitter's, which
    concatenates the same suffix onto the repository **parameter** as text. A caller who
    varies that parameter into a spelling the two read differently sends the work to a
    directory the setup never created ([parameters.py]).
    """
    return repository_root.parent / f"{repository_root.name}{WORKTREES_SUFFIX}"


def worktrees_root_for(repository_root: Path, plan_slug: str) -> Path:
    """The per-plan worktree parent, derived from the repository's own location.

    Beside the repository rather than inside it, so no commit step can sweep a worktree
    into a commit, and namespaced by plan, so two plans with the same step ids can never
    adopt each other's worktrees.
    """
    return worktrees_parent(repository_root) / plan_slug


def check_name(name: str) -> None:
    """Refuse a node name the engine would reject or the run model could not parse."""
    if len(name.encode("utf-8")) > ENGINE_NAME_MAX_BYTES:
        raise TopologyError(
            f"node name {name!r} is {len(name.encode('utf-8'))} bytes, over the "
            f"{ENGINE_NAME_MAX_BYTES}-byte engine bound; shorten the step's name in the plan"
        )
    if name in RESERVED_NAMES:
        raise TopologyError(f"node name {name!r} is reserved by the engine")
    if "-" in name:
        raise TopologyError(f"node name {name!r} contains a hyphen, which the engine rejects")
    if ENGINE_NAME.fullmatch(name) is None:
        raise TopologyError(
            f"node name {name!r} is not an engine identifier, which must match "
            f"{ENGINE_NAME.pattern}"
        )


def node_name(role: str, subject: str) -> str:
    if role not in ROLES:
        raise TopologyError(f"{role!r} is not one of the topology's roles: {', '.join(ROLES)}")
    name = f"{role}_{subject}"
    check_name(name)
    return name


def parse_node_name(name: str) -> Naming:
    """Split a node name back into its role and its subject — the contract 12 reads.

    The role is the text before the first underscore and must be one of the closed set,
    which is what makes the split unambiguous for a step whose own id opens with a role
    name: `work_work_config` is the `work` node of the step `work_config`.
    """
    role, _, subject = name.partition("_")
    if role not in ROLES or not subject:
        raise TopologyError(f"{name!r} is not a topology node name")
    return Naming(role, subject)


def _levels(graph: Graph) -> list[list[str]]:
    """The dependency levels, each holding steps with no dependency on each other.

    Deterministic in id order at every level, so the same graph always waves the same way
    and a run record stays comparable across runs.
    """
    by_id = {step["id"]: step for step in graph["steps"]}
    pending = {
        step_id: {dep["id"] for dep in step["deps"] if dep["id"] in by_id}
        for step_id, step in by_id.items()
    }
    waves: list[list[str]] = []
    settled: set[str] = set()
    while pending:
        ready = sorted(
            step_id for step_id, deps in pending.items() if deps <= settled
        )
        if not ready:
            raise TopologyError(
                "the graph has a dependency cycle among "
                + ", ".join(sorted(pending))
                + "; the validator refuses this before a topology is derived"
            )
        waves.append(ready)
        settled.update(ready)
        for step_id in ready:
            del pending[step_id]
    return waves


def _step_seconds(step: Step) -> int:
    # A wait's emitted bound carries the report grace, so the arithmetic counts it too:
    # the number stated and the number the engine enforces have to be the same one.
    grace = WAIT_REPORT_GRACE if step.get("command_type") == "wait_until" else 0
    return step_max_seconds(step["timeout"] + grace, step["retries"], RETRY_INTERVAL)


def _support_seconds() -> int:
    return step_max_seconds(SUPPORT_TIMEOUT, SUPPORT_RETRIES, 1)


def _merge_seconds() -> int:
    """A merge slot is priced as the agent step it can become, not as the git work it is.

    Most slots merge cleanly and cost seconds. The one that meets a real conflict pays for
    a session, and a bound that assumed the common case would kill it mid-merge.
    """
    return step_max_seconds(MERGE_TIMEOUT, MERGE_RETRIES, RETRY_INTERVAL)


def merge_provider(default_kind: str) -> str:
    """Which agent resolves a conflict this plan's merges meet.

    A conflict is a question about intent, so the resolution is always an agent session —
    including for a plan whose steps are all commands, which names no agent anywhere.
    """
    kind = default_kind if default_kind.startswith(AGENT_FAMILY) else DEFAULT_KIND
    return kind[len(AGENT_FAMILY) :]


def _chain_nodes(
    step: Step, wave: int, repository: str, plan_slug: str, after: list[str]
) -> list[Node]:
    """A dependent step runs on the parent branch: work, verify, mark, commit."""
    return _step_nodes(
        step, wave, repository, plan_slug, CHAIN, branch=None, after=after
    )


def _isolated_nodes(
    step: Step,
    wave: int,
    repository: str,
    plan_slug: str,
    branch: Branch,
    after: list[str],
) -> list[Node]:
    """An independent step gets a worktree: setup, work, verify, mark, commit."""
    return _step_nodes(
        step, wave, repository, plan_slug, BRANCH, branch=branch, after=after
    )


def _step_nodes(
    step: Step,
    wave: int,
    repository: str,
    plan_slug: str,
    position: str,
    *,
    branch: Branch | None,
    after: list[str],
) -> list[Node]:
    """The nodes one step becomes, in either graph position.

    Only three things differ between the two: an isolated step gets a `setup` node and
    runs in its own worktree, and its `mark` carries the position that decides whether a
    failed assertion excludes one branch or halts the chain behind it. The shape is
    otherwise one shape, because a step routing differently by position is exactly what
    the single-pattern rule forbids.
    """
    step_id = step["id"]
    working_directory = repository if branch is None else branch["worktree"]
    nodes: list[Node] = []
    if branch is not None:
        nodes.append(
            {
                "name": node_name("setup", step_id),
                "role": "setup",
                "step": step_id,
                "wave": wave,
                "working_directory": repository,
                "after": list(after),
                "max_seconds": _support_seconds(),
                "detail": {
                    "plan": plan_slug,
                    "branch": branch["name"],
                    "worktree": branch["worktree"],
                    "base": branch["base"],
                },
            }
        )
    nodes.append(
        {
            "name": node_name("work", step_id),
            "role": "work",
            "step": step_id,
            "wave": wave,
            "working_directory": working_directory,
            "after": [nodes[-1]["name"]] if nodes else list(after),
            "max_seconds": _step_seconds(step),
            "detail": {"kind": step["kind"]},
        }
    )
    # A step declared unverified has nothing on disk to assert, so it emits no assertion
    # node and its `mark` is gated on its own report alone.
    if has_assertion(step):
        nodes.append(
            {
                "name": node_name("verify", step_id),
                "role": "verify",
                "step": step_id,
                "wave": wave,
                "working_directory": working_directory,
                "after": [nodes[-1]["name"]],
                "max_seconds": _support_seconds(),
                "detail": {"command": step["verify"]},
            }
        )
    nodes.append(
        {
            "name": node_name("mark", step_id),
            "role": "mark",
            "step": step_id,
            "wave": wave,
            "working_directory": working_directory,
            "after": [nodes[-1]["name"]],
            "max_seconds": _support_seconds(),
            "detail": {"verified": has_assertion(step), "position": position},
        }
    )
    nodes.append(
        {
            "name": node_name("commit", step_id),
            "role": "commit",
            "step": step_id,
            "wave": wave,
            "working_directory": working_directory,
            "after": [nodes[-1]["name"]],
            "max_seconds": _support_seconds(),
            "detail": {
                "branch": None if branch is None else branch["name"],
                "position": position,
            },
        }
    )
    return nodes


def _wave_is_isolated(wave: list[str]) -> bool:
    return len(wave) > 1


def derive(
    graph: Graph,
    *,
    repository_root: Path,
    parent_branch: str,
) -> Topology:
    """Derive the whole topology: waves, branches, nodes, and the merge order's bound."""
    plan_slug = graph["plan"]["slug"]
    repository = str(repository_root)
    roots = worktrees_root_for(repository_root, plan_slug)
    by_id = {step["id"]: step for step in graph["steps"]}
    levels = _levels(graph)
    provider = merge_provider(graph["plan"]["default_kind"])

    waves: list[Wave] = []
    branches: list[Branch] = []
    nodes: list[Node] = []
    merge_order: list[list[str]] = []

    acquire = node_name("lock", "acquire")
    nodes.append(
        {
            "name": acquire,
            "role": "lock",
            "step": None,
            "wave": 0,
            "working_directory": repository,
            "after": [],
            "max_seconds": _support_seconds(),
            "detail": {"action": "acquire", "plan": plan_slug},
        }
    )
    frontier = [acquire]

    for index, level in enumerate(levels, start=1):
        isolated = _wave_is_isolated(level)
        waves.append({"index": index, "steps": list(level), "isolated": isolated})
        if not isolated:
            step = by_id[level[0]]
            produced = _chain_nodes(step, index, repository, plan_slug, frontier)
            nodes.extend(produced)
            frontier = [produced[-1]["name"]]
            continue

        wave_branches: list[Branch] = []
        for step_id in level:
            branch: Branch = {
                "name": f"{BRANCH_PREFIX}{step_id}",
                "step": step_id,
                "wave": index,
                "worktree": str(roots / step_id),
                "base": parent_branch,
            }
            wave_branches.append(branch)
            branches.append(branch)
            nodes.extend(
                _isolated_nodes(
                    by_id[step_id], index, repository, plan_slug, branch, frontier
                )
            )
        merge_order.append([branch["name"] for branch in wave_branches])

        join = node_name("join", f"w{index}")
        nodes.append(
            {
                "name": join,
                "role": "join",
                "step": None,
                "wave": index,
                "working_directory": repository,
                "after": [node_name("commit", step_id) for step_id in level],
                "max_seconds": _support_seconds(),
                "detail": {"branches": [branch["name"] for branch in wave_branches]},
            }
        )
        previous = join
        # Merge nodes are slots rather than a fixed sequence. The wave's steps are
        # independent by construction, so no dependency justifies an order among them;
        # which branch each slot lands is the merge step's own decision on the evidence
        # in front of it (10).
        for slot in range(1, len(level) + 1):
            name = node_name("merge", f"w{index}_{slot}")
            candidates = [branch["name"] for branch in wave_branches]
            nodes.append(
                {
                    "name": name,
                    "role": "merge",
                    "step": None,
                    "wave": index,
                    "working_directory": repository,
                    "after": [previous],
                    "max_seconds": _merge_seconds(),
                    "detail": {
                        "slot": slot,
                        "candidates": candidates,
                        "into": parent_branch,
                        "provider": provider,
                    },
                }
            )
            # The slot's own account of itself is not what lets the next slot run. A merge
            # asserts what reached the parent branch, in a process of its own, for the same
            # reason a step's assertion is not the step ([verify-gate.md]).
            assertion = node_name("verify", name)
            nodes.append(
                {
                    "name": assertion,
                    "role": "verify",
                    "step": None,
                    "wave": index,
                    "working_directory": repository,
                    "after": [name],
                    "max_seconds": _support_seconds(),
                    "detail": {
                        "merge": name,
                        "candidates": candidates,
                        "into": parent_branch,
                    },
                }
            )
            previous = assertion
        prune = node_name("prune", f"w{index}")
        nodes.append(
            {
                "name": prune,
                "role": "prune",
                "step": None,
                "wave": index,
                "working_directory": repository,
                "after": [previous],
                "max_seconds": _support_seconds(),
                "detail": {
                    "plan": plan_slug,
                    "steps": [branch["step"] for branch in wave_branches],
                    "worktrees": [branch["worktree"] for branch in wave_branches],
                    "branches": [branch["name"] for branch in wave_branches],
                    "parent": parent_branch,
                },
            }
        )
        frontier = [prune]

    # The release is not a node. A node depends on the one before it, and the engine does
    # not run a step whose dependency failed — so a release wired into the graph would run
    # only when the run succeeded, and a failed run would hold its repository for the whole
    # reclaim window. It runs on the way out instead, whichever way the run leaves.
    release: Node = {
        "name": node_name("lock", "release"),
        "role": "lock",
        "step": None,
        "wave": 0,
        "working_directory": repository,
        "after": [],
        "max_seconds": _support_seconds(),
        "detail": {"action": "release", "plan": plan_slug},
    }

    topology: Topology = {
        "plan": plan_slug,
        "repository": repository,
        "parent_branch": parent_branch,
        "worktrees_root": str(roots),
        "waves": waves,
        "branches": branches,
        "nodes": nodes,
        "on_exit": release,
        "merge_order": merge_order,
        "max_seconds": total_seconds([*nodes, release]),
        "critical_path_seconds": critical_path_seconds([*nodes, release]),
    }
    _refuse_over_ceiling(topology)
    return topology


def total_seconds(nodes: list[Node]) -> int:
    """How long the run might still be writing: every node's bound, added up.

    This is what the run lock's lease is derived from. The slowest path would be tighter
    and wrong for that purpose: it is only an upper bound under unbounded concurrency, and
    the engine caps concurrent steps, so a wave wider than the cap really does take longer
    than its critical path — and a lease derived from that path would come free while the
    run was still writing.

    Every node's own weight already counts its retries, because the engine applies a
    timeout to each attempt rather than to the step.
    """
    return sum(node["max_seconds"] for node in nodes)


def critical_path_seconds(nodes: list[Node]) -> int:
    """How long the run plausibly takes: the slowest chain through it.

    This is what the ceiling is judged against, because it is the number a plan author can
    do something about. Nodes are priced in dependency order, which `derive` produces.
    """
    longest: dict[str, int] = {}
    for node in nodes:
        upstream = max((longest.get(name, 0) for name in node["after"]), default=0)
        longest[node["name"]] = upstream + node["max_seconds"]
    return max(longest.values(), default=0)


def _refuse_over_ceiling(topology: Topology) -> None:
    """Refuse a plan that could hold the repository longer than Cairn will wait.

    A deliberate `cairn wait` holds the run lock for its whole duration, so a plan's
    declared waits are part of this arithmetic — and a plan whose waits push it past the
    ceiling is an error here, naming the numbers, rather than a run that dies halfway.
    """
    if topology["critical_path_seconds"] <= RUN_CEILING_SECONDS:
        return
    raise TopologyError(
        f"plan {topology['plan']!r} has a worst-case duration of "
        f"{topology['critical_path_seconds'] / 3600:.1f} hours along its slowest chain, over the "
        f"{RUN_CEILING_SECONDS / 3600:.0f}-hour ceiling. Every step's timeout counts once "
        "per attempt plus the wait between attempts, and a declared wait counts in full; "
        "shorten a wait, lower a timeout, or split the plan"
    )


__all__ = [
    "BRANCH_PREFIX",
    "ENGINE_NAME_MAX_BYTES",
    "RESERVED_NAMES",
    "ROLES",
    "RUN_CEILING_SECONDS",
    "Branch",
    "Naming",
    "Node",
    "Topology",
    "TopologyError",
    "Wave",
    "check_name",
    "critical_path_seconds",
    "derive",
    "node_name",
    "parse_node_name",
    "total_seconds",
    "worktrees_parent",
    "worktrees_root_for",
]
