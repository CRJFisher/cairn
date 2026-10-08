"""Converging a worktree from whatever a killed step left, and committing in it.

The engine's `git.worktree.add` covers one of four convergence cases and fails one of them
_green_: a worktree that was merged and left behind its parent is reused at the stale head
and succeeds ([01]). A built-in that fails a case green is the one that must not be
trusted, so all four cases are here rather than three of them wrapping the built-in.

The shape is deliberate. `inspect` gathers facts, `classify` turns them into exactly one
state with no I/O at all, and `converge` acts on that state. The decision is therefore a
fast unit test rather than a workflow run, and a shape nobody anticipated reaches a state
that refuses and reports what it saw instead of falling into whichever arm happened to be
last.

**A branch is reused only on proof that it is this plan's.** The name carries plan and step
([topology.branch_name]), and beside it a durable owner record under
`refs/cairn/branch-owner/` says which plan, step and parent branch the ref was created for.
A branch nothing can attribute is refused rather than adopted, which is the positive
ownership a run writes through ([27]).

Nothing here deletes content it cannot attribute. A directory that has to go is renamed
aside, because the one thing a killed agent leaves that matters is uncommitted work.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple, TypedDict, cast

from cairn.core import (
    EXIT_OK,
    CairnError,
    CommandResult,
    RuntimeContext,
    read_step_report,
)
from cairn.gitio import (
    branch_exists,
    checked_out_branch,
    common_directory,
    git,
    hash_object,
    is_ancestor,
    main_working_tree,
    read_blob,
    resolve_ref,
    same_repository,
    tree_state,
    update_ref,
    working_tree_root,
    worktree_entries,
)
from cairn.locks import git_write_mutex, refuse_unresolved_merge, unresolved_merge
from cairn.marker import marker_path
from cairn.topology import (
    BRANCH_PREFIX,
    WORKTREES_SUFFIX,
    branch_name,
    branch_namespace,
    node_name,
    worktrees_parent,
    worktrees_root_for,
)

QUARANTINE_SUFFIX = ".broken"

FOREIGN = "foreign"
LOCKED = "locked"
ELSEWHERE = "branch_elsewhere"
INTERRUPTED = "interrupted"
UNREADABLE = "unreadable"
HEALTHY = "healthy"
MERGED_BEHIND = "merged_behind"
WRONG_BRANCH = "wrong_branch"
STALE_REGISTRATION = "stale_registration"
REPAIRABLE = "repairable"
JUNK = "junk"
ABSENT = "absent"
UNCLASSIFIED = "unclassified"

# Ordered by the harm of getting it wrong: every refusal is decided before any repair, and
# every repair before any creation.
STATES = (
    FOREIGN,
    LOCKED,
    ELSEWHERE,
    INTERRUPTED,
    UNREADABLE,
    HEALTHY,
    MERGED_BEHIND,
    WRONG_BRANCH,
    REPAIRABLE,
    STALE_REGISTRATION,
    JUNK,
    ABSENT,
    UNCLASSIFIED,
)

# Where the branch's tip sits relative to the branch this wave started from. "Behind" is
# never a concept here on its own: only an ancestry proof lets anything move, so the value
# that permits movement is named for the proof rather than for the appearance.
NO_BRANCH = "no_branch"
SAME_AS_PARENT = "same_as_parent"
ANCESTOR_OF_PARENT = "ancestor_of_parent"
UNMERGED = "unmerged"


@dataclass(frozen=True)
class Facts:
    """Everything the classifier is allowed to look at, gathered once."""

    registration: str = "none"
    registered_branch: str | None = None
    prunable: bool = False
    locked: bool = False
    branch_checked_out_at: str | None = None
    disk: str = "absent"
    identity: str = "none"
    foreign_common: str | None = None
    head: str = "none"
    in_progress: str | None = None
    tree: str = "none"
    relation: str = NO_BRANCH
    dirty_paths: tuple[str, ...] = field(default=())


BRANCH_OWNER_PREFIX = "refs/cairn/branch-owner/"

# What a legacy ref's classification concluded, reported by the setup node that reached it.
MIGRATED = "migrated"
OWNED_ELSEWHERE = "owned_elsewhere"
UNATTRIBUTABLE = "unattributable"
SUPERSEDED = "superseded"


class BranchOwner(TypedDict):
    """Who a branch belongs to, recorded where only Cairn's own writes can reach it.

    Durable because the question outlives every process: a run that died between creating a
    branch and committing to it leaves a ref whose next reader has nothing but this record
    to decide the ref's plan, step and starting point by.
    """

    branch: str
    plan: str
    step: str
    parent: str


class LegacyVerdict(NamedTuple):
    """What a bare `step/<id>` ref was classified as, and what was done about it."""

    ref: str
    verdict: str


def owner_ref(plan: str, step: str) -> str:
    return f"{BRANCH_OWNER_PREFIX}{plan}/{step}"


def read_branch_owner(repository: Path, plan: str, step: str) -> BranchOwner | None:
    """The owner record for this plan's step, or None where there is no proof to read.

    Unreadable and absent answer the same None on purpose. Both mean the same thing to the
    only caller: nothing here proves a branch belongs to this plan, so an existing one is
    refused rather than adopted. Distinguishing them would offer a second arm, and the
    only move that arm could make is the adoption this exists to prevent.
    """
    object_id = resolve_ref(repository, owner_ref(plan, step))
    if object_id is None:
        return None
    kind = git(repository, ("cat-file", "-t", object_id), check=False)
    if kind.exit_code != 0 or kind.stdout != "blob":
        return None
    try:
        payload: Any = json.loads(read_blob(repository, object_id))
    except (json.JSONDecodeError, CairnError):
        return None
    if not isinstance(payload, dict):
        return None
    record = cast(dict[str, Any], payload)
    if any(not isinstance(record.get(name), str) for name in BranchOwner.__annotations__):
        return None
    return cast(BranchOwner, record)


def write_branch_owner(
    repository: Path, *, branch: str, plan: str, step: str, parent: str
) -> BranchOwner:
    """Record that this plan's step owns this branch, from this parent.

    Written unconditionally rather than by compare-and-swap: the caller holds the git write
    mutex and has already established that no branch exists for this identity, so there is
    no second writer to lose to and a record left by a dead run is exactly what has to be
    replaced.
    """
    record: BranchOwner = {
        "branch": branch,
        "plan": plan,
        "step": step,
        "parent": parent,
    }
    payload = json.dumps(record, indent=2, sort_keys=True, allow_nan=False) + "\n"
    object_id = hash_object(repository, payload)
    if not update_ref(repository, f"update {owner_ref(plan, step)} {object_id}"):
        raise CairnError(
            "branch_unowned",
            f"the ownership of {branch} could not be recorded, so this step would work on "
            "a branch no later run could prove is its own",
            detail={"branch": branch, "plan": plan, "step": step},
        )
    return record


def _delete_branch_owner(repository: Path, plan: str, step: str) -> None:
    object_id = resolve_ref(repository, owner_ref(plan, step))
    if object_id is not None:
        update_ref(repository, f"delete {owner_ref(plan, step)} {object_id}")


def _refuse_namespace_collision(repository: Path, plan: str) -> None:
    """Refuse a ref sitting where this plan's whole branch namespace goes.

    `refs/heads/step/<plan>` and `refs/heads/step/<plan>/<step>` cannot both exist — git
    stores a ref as a file and a namespace as a directory — so one such ref blocks every
    branch the plan owns. Left to git it surfaces as `cannot lock ref` from whichever step
    created a branch first, which reads as contention; named here it reads as the one ref
    that has to go. The worktree path has the same collision in the same place: a file
    where the plan's worktree directory belongs.
    """
    namespace = branch_namespace(plan)
    if branch_exists(repository, namespace):
        raise CairnError(
            "branch_unowned",
            f"refs/heads/{namespace} occupies the whole branch namespace of plan {plan!r}, "
            f"so no step of it can have a branch; rename or delete that ref",
            detail={"plan": plan, "blocking_ref": f"refs/heads/{namespace}"},
        )


def _legacy_owning_plan(repository: Path, step: str) -> str | None:
    """The plan a bare `step/<id>` ref can be *proved* to belong to, or None.

    The proof is the ref's own worktree registration, because a worktree path has carried
    the plan slug since worktrees were namespaced: a registration at
    `<repository>.cairn-worktrees/<plan>/<step>` is the plan that created the ref saying so,
    in a place no later plan writes.

    None is every other answer — no registration, a registration at a path of another
    shape, or registrations naming two plans. A ref whose registration has simply gone
    answers None too, and deliberately: a disappeared registration is the absence of
    evidence, and adopting a branch on it is how one plan would take another's work.
    """
    legacy = f"{BRANCH_PREFIX}{step}"
    parent = Path(os.path.realpath(worktrees_parent(repository)))
    plans: set[str] = set()
    for entry in worktree_entries(repository):
        if entry.branch != legacy:
            continue
        path = Path(os.path.realpath(entry.path))
        if path.name != step or Path(os.path.realpath(path.parent.parent)) != parent:
            return None
        plans.add(path.parent.name)
    if len(plans) != 1:
        return None
    return plans.pop()


def _classify_legacy_branch(
    repository: Path, *, plan: str, step: str, branch: str, parent: str
) -> LegacyVerdict | None:
    """Classify a bare `step/<id>` ref, and migrate only the one that is provably ours.

    Four outcomes, and only the last moves anything. A plan that already has its namespaced
    branch is answered first and whatever the legacy ref is: that branch is this plan's
    current line of work and nothing may rewrite it. A ref nothing can attribute is left
    where it is and named, because the one thing that must never happen is a plan adopting a
    branch on the strength of a step id they happen to share. A ref owned by another plan is
    left where it is too, because it is that plan's to migrate. Only a ref whose owning plan
    is this plan is renamed onto the namespaced identity and recorded, which carries a
    killed run's work forward.

    The migration establishes the plan, never the parent: the parent recorded is the one
    this run lands on, and whether the ref's tip may move onto it is the ancestry question
    the convergence asks next.
    """
    legacy = f"{BRANCH_PREFIX}{step}"
    if not branch_exists(repository, legacy):
        return None
    if branch_exists(repository, branch):
        return LegacyVerdict(legacy, SUPERSEDED)
    owning = _legacy_owning_plan(repository, step)
    if owning is None:
        return LegacyVerdict(legacy, UNATTRIBUTABLE)
    if owning != plan:
        return LegacyVerdict(legacy, OWNED_ELSEWHERE)
    git(repository, ("branch", "-m", legacy, branch))
    write_branch_owner(repository, branch=branch, plan=plan, step=step, parent=parent)
    return LegacyVerdict(legacy, MIGRATED)


def _claim_branch(repository: Path, *, plan: str, step: str, parent: str) -> str:
    """Prove this plan's step owns its branch, or refuse; return the branch it owns.

    A branch that does not exist is claimed outright — there is nothing to adopt, so the
    record is written for the branch the convergence is about to create. A branch that does
    exist is reused only when the record proves all three things a reuse rests on: this
    plan, this step, and the parent the branch was started from. A branch reused from
    another parent would carry the wrong history into this run's merges while every name
    and path said it was right.
    """
    branch = branch_name(plan, step)
    _refuse_namespace_collision(repository, plan)
    owner = read_branch_owner(repository, plan, step)
    if not branch_exists(repository, branch):
        write_branch_owner(
            repository, branch=branch, plan=plan, step=step, parent=parent
        )
        return branch
    if owner is None:
        raise CairnError(
            "branch_unowned",
            f"{branch} exists and nothing records which plan, step and parent it was "
            "created for, so Cairn will not work on it; delete it or settle it by hand",
            detail={"branch": branch, "plan": plan, "step": step},
        )
    if (owner["branch"], owner["plan"], owner["step"]) != (branch, plan, step):
        raise CairnError(
            "branch_unowned",
            f"the owner record for {branch} names {owner['branch']!r} of step "
            f"{owner['step']!r} in plan {owner['plan']!r}, so it proves nothing about this "
            "step's branch",
            detail={"branch": branch, "recorded": dict(owner)},
        )
    if owner["parent"] != parent:
        raise CairnError(
            "branch_unowned",
            f"{branch} was created from {owner['parent']!r} and this run lands on "
            f"{parent!r}, so reusing it would carry the wrong history into the merge; "
            "delete the branch or run against the parent it was started from",
            detail={"branch": branch, "recorded_parent": owner["parent"], "parent": parent},
        )
    return branch


def classify(facts: Facts) -> str:
    """Exactly one state per fact record, with no fall-through into an action."""
    if facts.identity == FOREIGN:
        return FOREIGN
    if facts.locked:
        return LOCKED
    if facts.branch_checked_out_at is not None:
        return ELSEWHERE
    ours = facts.identity == "ours"
    if ours and facts.in_progress:
        return INTERRUPTED
    if ours and facts.tree == UNREADABLE:
        return UNREADABLE
    if ours and facts.registration == "here" and facts.head == "ours":
        return MERGED_BEHIND if facts.relation == ANCESTOR_OF_PARENT else HEALTHY
    if ours and facts.registration == "here":
        return WRONG_BRANCH
    # A directory that is still there outranks the registration's own verdict on it: git
    # calls a worktree with a broken `.git` file prunable, and pruning it would step over
    # the repair that keeps the work inside it.
    if facts.registration == "here" and facts.disk == "dir" and facts.identity == UNREADABLE:
        return REPAIRABLE
    if facts.registration == "here" and facts.disk in ("absent", "empty_dir"):
        return STALE_REGISTRATION
    if facts.disk in ("dir", "file", "symlink"):
        return JUNK
    if facts.disk in ("absent", "empty_dir"):
        return ABSENT
    return UNCLASSIFIED


def inspect(repository: Path, worktree: Path, branch: str, base: str) -> Facts:
    """Ask git every question the classifier needs, and nothing it does not."""
    entries = worktree_entries(repository)
    resolved = Path(os.path.realpath(worktree))
    here = next((entry for entry in entries if entry.path == resolved), None)
    # A registration is only a holder while its directory is still there. A run killed
    # after its worktrees root was moved or deleted leaves exactly this: a registration for
    # this plan's own branch at a path nothing occupies. Refusing on it would halt every
    # later run of the plan, permanently, over a directory the create arm's own
    # `worktree prune` clears.
    holder = next(
        (
            entry
            for entry in entries
            if entry.branch == branch
            and entry.path != resolved
            and not entry.prunable
            and entry.path.exists()
        ),
        None,
    )
    disk = _disk(worktree)
    identity, foreign = _identity(repository, worktree, disk)
    ours = identity == "ours"
    tree, dirty = _tree(worktree) if ours else ("none", ())
    return Facts(
        registration="here" if here else "none",
        registered_branch=here.branch if here else None,
        prunable=bool(here and here.prunable),
        locked=bool(here and here.locked),
        branch_checked_out_at=str(holder.path) if holder else None,
        disk=disk,
        identity=identity,
        foreign_common=foreign,
        head=_head(worktree, branch) if ours else "none",
        in_progress=unresolved_merge(worktree) if ours else None,
        tree=tree,
        relation=_relation(repository, branch, base),
        dirty_paths=dirty,
    )


def _disk(path: Path) -> str:
    if os.path.islink(path):
        return "symlink"
    if not path.exists():
        return "absent"
    if path.is_file():
        return "file"
    return "dir" if any(path.iterdir()) else "empty_dir"


def _identity(repository: Path, worktree: Path, disk: str) -> tuple[str, str | None]:
    """Whose repository this directory belongs to, as git answers it.

    A directory git will not answer about is *unreadable* rather than nobody's. Paired
    with a registration that still names it, that is the state a repair fixes without
    touching the work inside; with no registration left it is indistinguishable from junk
    and is moved aside instead.
    """
    if disk in ("absent", "empty_dir"):
        return "none", None
    try:
        common = common_directory(worktree)
    except CairnError:
        return UNREADABLE, None
    if same_repository(common_directory(repository), common):
        return "ours", None
    return FOREIGN, str(common)


def _head(worktree: Path, branch: str) -> str:
    on = checked_out_branch(worktree)
    if on is None:
        return "detached"
    return "ours" if on == branch else "other"


def _tree(worktree: Path) -> tuple[str, tuple[str, ...]]:
    entries = tree_state(worktree)
    if entries is None:
        return UNREADABLE, ()
    return ("dirty" if entries else "clean"), entries


def _relation(repository: Path, branch: str, base: str) -> str:
    if not branch_exists(repository, branch):
        return NO_BRANCH
    ancestor = is_ancestor(repository, branch, base)
    descendant = is_ancestor(repository, base, branch)
    if ancestor and descendant:
        return SAME_AS_PARENT
    if ancestor:
        return ANCESTOR_OF_PARENT
    return UNMERGED


def setup_worktree(
    repository: Path, worktree: Path, base: str, *, plan: str, step: str
) -> CommandResult:
    """Bring the step's worktree to a state its agent can work in, from any starting point.

    The branch is derived here from the plan and the step rather than passed in, so the one
    identity the ownership record is written under is the same one the worktree is checked
    out on. A branch carried in alongside them would be a second derivation of one name,
    and the only thing two derivations can do is disagree.
    """
    if Path(os.path.realpath(worktree)) == main_working_tree(repository):
        raise CairnError(
            "worktree_unusable",
            f"{worktree} is the repository's own working tree, not a step worktree",
            detail={"worktree": str(worktree)},
        )
    with git_write_mutex(repository):
        # Ownership is settled before a single fact is gathered: every arm below creates,
        # moves or checks out this branch, and none of them may run against a ref this plan
        # cannot prove is its own ([27 A]).
        legacy = _classify_legacy_branch(
            repository,
            plan=plan,
            step=step,
            branch=branch_name(plan, step),
            parent=base,
        )
        branch = _claim_branch(repository, plan=plan, step=step, parent=base)
        facts = inspect(repository, worktree, branch, base)
        state = classify(facts)
        if state == REPAIRABLE:
            # Repair relinks a worktree whose `.git` file a killed step or a move broke,
            # without touching the work inside — so it is tried before any arm that would
            # move the directory aside.
            git(repository, ("worktree", "repair"), check=False)
            facts = inspect(repository, worktree, branch, base)
            state = classify(facts)
        outcome = _converge(repository, worktree, branch, base, facts, state)
    return outcome._replace(
        follow_up_work=[*outcome.follow_up_work, *_legacy_follow_up(legacy)],
        detail={
            **outcome.detail,
            "state": state,
            "worktree": str(worktree),
            "branch": branch,
            "base": base,
            "legacy_branch": None if legacy is None else legacy._asdict(),
        },
    )


def _legacy_follow_up(legacy: LegacyVerdict | None) -> list[str]:
    """One line for a legacy ref left standing, and none for one nothing had to decide."""
    if legacy is None or legacy.verdict == MIGRATED:
        return []
    if legacy.verdict == UNATTRIBUTABLE:
        return [
            (
                f"{legacy.ref} is a branch no worktree registration attributes to a plan, so "
                "nothing adopted it; delete it or rename it onto the plan that owns it"
            )
        ]
    if legacy.verdict == OWNED_ELSEWHERE:
        return [f"{legacy.ref} belongs to another plan and was left where it is"]
    return [
        (
            f"{legacy.ref} was left where it is: this step already has its own branch, which "
            "nothing may rewrite"
        )
    ]


def _converge(
    repository: Path,
    worktree: Path,
    branch: str,
    base: str,
    facts: Facts,
    state: str,
) -> CommandResult:
    if state == FOREIGN:
        raise CairnError(
            "worktree_foreign",
            f"{worktree} is a worktree of {facts.foreign_common}, not of {repository}; "
            "Cairn will not take a directory that belongs to another repository",
            detail={"worktree": str(worktree), "belongs_to": facts.foreign_common},
        )
    if state == LOCKED:
        raise CairnError(
            "worktree_unusable",
            f"{worktree} is locked, and Cairn never unlocks a worktree",
            detail={"worktree": str(worktree)},
        )
    if state == ELSEWHERE:
        raise CairnError(
            "worktree_unusable",
            f"{branch} is already checked out at {facts.branch_checked_out_at}",
            detail={"other_worktree": facts.branch_checked_out_at, "branch": branch},
        )
    if state == INTERRUPTED:
        raise CairnError(
            "merge_in_progress",
            f"{worktree} has {facts.in_progress} in progress, which is left as it is",
            detail={"worktree": str(worktree), "pending": facts.in_progress},
        )
    if state == UNREADABLE:
        raise CairnError(
            "worktree_unusable",
            f"{worktree} is registered here but will not answer git",
            detail={"worktree": str(worktree)},
        )
    if state == REPAIRABLE:
        raise CairnError(
            "worktree_unusable",
            f"{worktree} is registered here and git cannot read it even after a repair, "
            "so it is left for a person rather than pruned over",
            detail={"worktree": str(worktree)},
        )
    if state == UNCLASSIFIED:
        raise CairnError(
            "worktree_unusable",
            f"{worktree} is in a state Cairn does not recognise, so it is left alone",
            detail={"worktree": str(worktree), "facts": str(facts)},
        )
    if state == HEALTHY:
        return _result(
            "noop", f"{worktree} is already the step's worktree", {"case": "reused"}
        )
    if state == MERGED_BEHIND:
        return _fast_forward(worktree, base)
    if state == WRONG_BRANCH:
        return _switch(worktree, branch, facts)
    quarantined = _quarantine(worktree) if state == JUNK else None
    # A registration whose directory is gone hard-errors `worktree add` and demands an
    # explicit prune, so a retry loop without one never escapes. Pruning first is
    # idempotent, which is cheaper than deciding whether this state needs it.
    git(repository, ("worktree", "prune"))
    _create(repository, worktree, branch, base, facts)
    case = "recreated" if quarantined else "created"
    if facts.relation == ANCESTOR_OF_PARENT:
        # A worktree created onto an existing branch checks out that branch's tip, which
        # for a branch that already landed is behind the parent — the same stale base the
        # reuse path moves off, and the same refusal when it cannot.
        moved = _fast_forward(worktree, base)
        return _result(
            "done",
            f"{worktree} is the step's worktree on {branch}",
            {
                **moved.detail,
                "case": f"{case}_{moved.detail['case']}",
                "quarantined": quarantined,
            },
        )
    return _result(
        "done",
        f"{worktree} is the step's worktree on {branch}",
        {"case": case, "quarantined": quarantined},
    )


def _result(status: str, summary: str, detail: dict[str, object]) -> CommandResult:
    return CommandResult(EXIT_OK, status, summary, [], False, None, detail)


def _fast_forward(worktree: Path, base: str) -> CommandResult:
    """Advance a merged branch to the parent's head, or keep the work that stops it.

    A fast-forward rather than a reset: the branch is provably an ancestor, so the move
    cannot drop a commit, and git itself refuses when the move would overwrite a killed
    agent's uncommitted edits. That refusal is the outcome, not an error.
    """
    outcome = git(worktree, ("merge", "--ff-only", base), check=False)
    if outcome.exit_code == 0:
        return _result(
            "done", f"{worktree} moved forward to {base}", {"case": "fast_forwarded"}
        )
    # Only a working tree that would lose an edit is a refusal Cairn accepts. Anything
    # else — a jammed index lock, an unreadable object — left the branch at a head the
    # parent has moved past, which is the very state this arm exists to clear. Reporting
    # that as a deliberate preservation would hand the agent a stale base and say nothing.
    if _tree(worktree)[0] != "clean":
        return CommandResult(
            EXIT_OK,
            "done",
            f"{worktree} keeps its uncommitted work and stays behind {base}",
            [f"{worktree} is behind {base} because uncommitted work blocks the move"],
            False,
            None,
            {"case": "stale_head_preserved"},
        )
    raise CairnError(
        "worktree_unusable",
        f"{worktree} is behind {base} and would not move forward: {outcome.stderr}",
        detail={"worktree": str(worktree), "base": base},
    )


def _switch(worktree: Path, branch: str, facts: Facts) -> CommandResult:
    """Move a clean worktree of ours onto the branch this step owns.

    Uncommitted work here is a killed agent's output on some other ref, so it halts rather
    than being checked out over. Convergence never loses work.
    """
    if facts.tree != "clean":
        on = facts.registered_branch or "an unknown ref"
        raise CairnError(
            "worktree_dirty",
            f"{worktree} holds uncommitted work on {on} rather than {branch}; commit or "
            "clear it before Cairn moves the worktree",
            detail={"dirty_paths": list(facts.dirty_paths[:50]), "branch": branch},
        )
    outcome = git(worktree, ("checkout", branch), check=False)
    if outcome.exit_code != 0:
        raise CairnError(
            "worktree_unusable",
            f"{worktree} would not move to {branch}: {outcome.stderr}",
            detail={"worktree": str(worktree), "branch": branch},
        )
    return _result("done", f"{worktree} moved to {branch}", {"case": "switched_to_branch"})


def _quarantine(worktree: Path) -> str:
    """Move an unattributable directory aside rather than deleting what is in it."""
    _refuse_outside_worktrees_root(worktree)
    candidate = worktree.with_name(worktree.name + QUARANTINE_SUFFIX)
    ordinal = 1
    while candidate.exists():
        ordinal += 1
        candidate = worktree.with_name(f"{worktree.name}{QUARANTINE_SUFFIX}.{ordinal}")
    shutil.move(str(worktree), str(candidate))
    return str(candidate)


def _refuse_outside_worktrees_root(worktree: Path) -> None:
    """Refuse to move anything that is not inside a Cairn worktrees root.

    Checked component-wise rather than on the string, because `/repo-backup` is not inside
    `/repo` however much the two look alike. The marker is the topology's own suffix, so
    the guard and the layout cannot drift apart.

    Both the path as given and the path with symlinks resolved are accepted: keeping the
    worktrees root on another volume behind a symlink is an ordinary thing to do, and
    resolving first would refuse every convergence under it while naming a path the
    operator never typed.
    """
    candidates = (Path(os.path.abspath(worktree)), Path(os.path.realpath(worktree)))
    if any(
        parent.name.endswith(WORKTREES_SUFFIX)
        for candidate in candidates
        for parent in candidate.parents
    ):
        return
    raise CairnError(
        "worktree_unusable",
        f"{worktree} is not inside a '*{WORKTREES_SUFFIX}' directory, so Cairn will not "
        "move it aside",
        detail={"worktree": str(worktree)},
    )


def _create(
    repository: Path, worktree: Path, branch: str, base: str, facts: Facts
) -> None:
    """Create the branch and the worktree as two explicit, separately verified acts.

    Never `git worktree add -b <branch> <path> <base>`: a start point is silently ignored
    once the branch exists — even a garbage value succeeds, using the branch's current tip
    ([research-dagu.md]) — so expressing "start from the parent" as an argument is a lie
    the second time it runs.
    """
    if facts.relation == NO_BRANCH:
        head = git(repository, ("rev-parse", "--verify", base)).stdout
        git(repository, ("branch", branch, head))
    worktree.parent.mkdir(parents=True, exist_ok=True)
    git(repository, ("worktree", "add", str(worktree), branch))


def prune_worktrees(
    repository: Path,
    *,
    plan: str,
    steps: list[str],
    parent: str,
    force: bool = False,
) -> CommandResult:
    """Remove a wave's worktrees and delete its fully merged branches, never unmerged ones.

    Every green run prunes, so nothing accumulates. A dirty worktree is refused rather than
    discarded unless the caller asks for it explicitly: uncommitted work in a worktree is a
    killed agent's output.

    **The cleanup is bounded to the plan being pruned, by derivation rather than by a
    check.** Paths and branches are composed here from the plan and its step ids, so there
    is no argument through which a ref outside `step/<plan>/` could be named — which is
    what keeps one plan's prune off another plan's branch for a step id they share ([27 A]).
    """
    worktrees = [str(worktrees_root_for(repository, plan) / step) for step in steps]
    branches = [branch_name(plan, step) for step in steps]
    removed: list[str] = []
    kept: list[str] = []
    absent: list[str] = []
    failed: list[str] = []
    deleted: list[str] = []
    unmerged: list[str] = []
    follow_up: list[str] = []
    with git_write_mutex(repository):
        for path in worktrees:
            arguments = ["worktree", "remove"]
            if force:
                arguments.append("--force")
            arguments.append(path)
            outcome = git(repository, arguments, check=False)
            if outcome.exit_code == 0:
                removed.append(path)
                continue
            # Why it refused is read back off the worktree itself, not out of git's
            # wording. A blanket "still holds uncommitted work" would send someone to
            # rescue work from a directory that is not there, and matching git's prose
            # would make the distinction turn on a message nobody promised to keep.
            if _disk(Path(path)) in ("absent", "empty_dir"):
                absent.append(path)
            elif _tree(Path(path))[0] == "dirty":
                kept.append(path)
                follow_up.append(f"{path} still holds uncommitted work and was not removed")
            else:
                failed.append(path)
                follow_up.append(f"{path} could not be removed: {outcome.stderr}")
        git(repository, ("worktree", "prune"))
        for step, branch in zip(steps, branches, strict=True):
            if not branch_exists(repository, branch):
                _delete_branch_owner(repository, plan, step)
                continue
            # Merged into the branch the topology named, not into whatever HEAD happens to
            # be: `git branch -d` asks about HEAD, so a branch already folded into the
            # parent would be retained forever whenever the repository sits elsewhere.
            if not is_ancestor(repository, branch, parent):
                unmerged.append(branch)
                continue
            outcome = git(repository, ("branch", "-D", branch), check=False)
            if outcome.exit_code == 0:
                deleted.append(branch)
                # The record goes with the branch it describes. A retained branch keeps
                # its record, because that record is the only thing that will let a later
                # run of this plan prove the branch is its own and pick the work back up.
                _delete_branch_owner(repository, plan, step)
            else:
                unmerged.append(branch)
    detail = {
        "removed": removed,
        "kept": kept,
        "already_gone": absent,
        "failed": failed,
        "deleted_branches": deleted,
        "retained_branches": unmerged,
    }
    if kept or failed:
        return CommandResult(
            EXIT_OK,
            "done",
            f"pruned {len(removed)} worktree(s); {len(kept)} held uncommitted work "
            f"and {len(failed)} could not be removed",
            follow_up,
            False,
            None,
            detail,
        )
    if not removed and not deleted:
        return CommandResult(
            EXIT_OK, "noop", "nothing to prune", [], False, None, detail
        )
    return CommandResult(
        EXIT_OK,
        "done",
        f"pruned {len(removed)} worktree(s) and {len(deleted)} branch(es)",
        [],
        False,
        None,
        detail,
    )


def _staged_diffstat(working_directory: Path, paths: list[str]) -> dict[str, int]:
    """What this commit will record, counted over the step's own paths and no others.

    Scoped to the same pathspec the commit carries, because the index can hold work a
    concurrent session staged and a count taken over all of it would attribute that work to
    this step in the one number a reader checks the commit by.

    A binary file counts as changed and nothing more: git spells its line counts `-`, which
    is not "zero lines changed" but "the question does not apply", and counting it as zero
    would be a plausible default in the one record that refuses them.
    """
    counted = git(
        working_directory,
        ("--literal-pathspecs", "diff", "--cached", "--numstat", "--", *paths),
        check=False,
    )
    if counted.exit_code != 0:
        return {"files": 0, "insertions": 0, "deletions": 0}
    files = insertions = deletions = 0
    for line in counted.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) < 3:
            continue
        files += 1
        added, removed = fields[0], fields[1]
        insertions += int(added) if added.isdigit() else 0
        deletions += int(removed) if removed.isdigit() else 0
    return {"files": files, "insertions": insertions, "deletions": deletions}


# The key under which a work step records what was already dirty when its session started,
# path by path with the content of each. Named here because it crosses a seam: the work
# handlers write it and the commit reads it, and a literal spelled twice would drift with
# nothing failing — the commit would silently stage only the marker, for ever.
DIRTY_BEFORE = "dirty_before"

# What a baseline records for a path there is no content to take: a path dirty because it is
# gone, and a path the filesystem would not read. They are kept apart because only the first
# is an answer — the second is the absence of one, and a commit cannot prove anything about
# content it could not read either time.
ABSENT_CONTENT = "absent"
UNREADABLE_CONTENT = "unreadable"

_DIGEST_CHUNK = 1 << 20


def _content_digest(path: Path) -> str:
    """One path's content as it stands now, for comparison against the same path later.

    A symlink is digested as its target rather than as what it points at: the link is the
    content a commit would carry, and following it would read a file outside the tree.
    """
    try:
        if path.is_symlink():
            return hashlib.sha256(
                b"symlink\0" + os.readlink(path).encode("utf-8", "surrogateescape")
            ).hexdigest()
        running = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(_DIGEST_CHUNK):
                running.update(chunk)
        return running.hexdigest()
    except FileNotFoundError:
        return ABSENT_CONTENT
    except OSError:
        return UNREADABLE_CONTENT


def dirty_snapshot(working_directory: Path) -> dict[str, str] | None:
    """What is uncommitted in this tree right now, path by path and content by content.

    Taken before a step's session and compared after it. **The content is the point.** A
    before-and-after set of paths can say that a path was somebody else's when the step
    started; it cannot say whether the step then changed that very path — and a commit that
    excluded such a path while publishing the step's marker would record completion over
    work absent from `HEAD`, which the next run would skip rather than redo ([21 B]).

    None where git would not say what is dirty. Absent is not clean: a tree whose
    uncommitted work cannot be established is one whose commit cannot be scoped, and the
    commit refuses rather than taking the marker alone.
    """
    paths = tree_state(working_directory)
    if paths is None:
        return None
    root = working_tree_root(working_directory)
    return {path: _content_digest(root / path) for path in paths}


def _altered_excluded_paths(
    root: Path, before: dict[str, str], marker: str
) -> list[str]:
    """The excluded paths this step cannot prove it left alone.

    Every path the baseline holds is one the commit will not stage, so every one of them
    must still hold the content it held — a changed one is work the commit would leave
    behind a marker, and a path that is no longer dirty at all is a person's edit the step
    wrote over. Content that could not be read either time proves nothing and counts as
    altered, because the one thing this may not do is pass on silence.

    The marker is the exception, and it is not an exclusion: the step takes its own marker
    by path whoever else had it dirty, so a marker the mark node rewrote is the commit
    working as intended.
    """
    altered: list[str] = []
    for path, recorded in sorted(before.items()):
        if path == marker:
            continue
        current = _content_digest(root / path)
        if current != recorded or current == UNREADABLE_CONTENT:
            altered.append(path)
    return altered


def _addable_paths(working_directory: Path, root: Path, paths: list[str]) -> list[str]:
    """The paths `git add` can be asked about: those the working tree or the index still holds.

    A deletion the step staged itself (`git rm`) is gone from both, and a pathspec naming a
    path git has nothing for fails the whole add — one removed file would lose every other
    path's staging, and the step's verified work with it. Such a path needs no add: its
    deletion is already in the index, and the commit's pathspec still carries it.
    """
    indexed = set(
        git(
            working_directory,
            ("--literal-pathspecs", "ls-files", "-z", "--", *paths),
        ).stdout.split("\0")
    )
    return [path for path in paths if path in indexed or (root / path).exists() or (root / path).is_symlink()]


def commit_step(
    working_directory: Path, message: str, *, step_id: str, context: RuntimeContext
) -> CommandResult:
    """Commit what the step's own session left behind, and nothing else in the tree.

    A chain step runs in the repository itself, which is the one topology where "commit
    whatever is dirty" is false by construction whenever a person is also working in the
    checkout — the ordinary way this tool is driven. So the commit stages the paths that
    are dirty now and were not dirty when the step's session started, plus the step's own
    marker by path; a path dirty both before and after is somebody else's in-flight work
    and is left alone and named ([21]). A step whose work node left no snapshot — no report,
    as a step its marker gate skipped, or a report without the key, as a marker no-op —
    stages the marker alone, because the alternative is sweeping the whole tree on every
    no-op of every recovery. A snapshot that is present and unreadable is a refusal.

    **A marker is published only over state the commit carries.** Path membership alone
    cannot establish that: a path dirty before the step and changed by it is classified as
    somebody else's, so the commit would hold the marker and not the work the assertion
    passed over. Every excluded path is therefore proved unchanged against the content the
    baseline recorded, and a step that altered one commits nothing at all ([21 B]).

    A no-op when there is nothing staged and a failure when staging itself fails: the two
    must never be confused, because one is a step that had nothing to say and the other is
    a step whose output was lost. A worktree run starts clean, so nothing here changes what
    it commits.
    """
    root = working_tree_root(working_directory)
    marker = marker_path(root, step_id).relative_to(root).as_posix()
    try:
        return _commit_scoped(
            working_directory, message, step_id=step_id, context=context,
            root=root, marker=marker,
        )
    except CairnError:
        # Every refusal below leaves the mark node's marker sitting in the working tree,
        # and the gate that decides whether this step runs again reads the tree rather than
        # `HEAD` — so a marker this commit declined to publish would make the next run skip
        # the very step that would redo the work ([21 B]). Withdrawing it is part of the
        # refusal, not cleanup after one.
        _withdraw_marker(working_directory, root, marker)
        raise


def _withdraw_marker(working_directory: Path, root: Path, marker: str) -> None:
    """Take a marker this commit refused to publish back out of the working tree.

    What `HEAD` already holds is restored rather than deleted. An earlier run's committed
    marker is not this commit's to withdraw: the work behind it is in history, and removing
    it would make a step that really is done run again with no marker left to say so.

    Where git will not answer at all, the file goes. What `HEAD` holds cannot be established
    then, and the two errors are not the same size: redoing a convergent step costs a run,
    while a marker left standing over unproved state makes every later run skip the step
    that would catch it. The withdrawal also cannot raise over the refusal that called it —
    that refusal is the fault a person has to read.
    """
    try:
        committed = git(
            working_directory, ("cat-file", "-e", f"HEAD:{marker}"), check=False
        )
        if committed.exit_code == 0:
            git(
                working_directory,
                ("--literal-pathspecs", "checkout", "HEAD", "--", marker),
                check=False,
            )
            return
    except CairnError:
        pass
    try:
        (root / marker).unlink(missing_ok=True)
    except OSError:
        pass


def _commit_scoped(
    working_directory: Path,
    message: str,
    *,
    step_id: str,
    context: RuntimeContext,
    root: Path,
    marker: str,
) -> CommandResult:
    refuse_unresolved_merge(working_directory)
    before = _dirty_before(context, step_id)
    with git_write_mutex(working_directory):
        now = tree_state(working_directory)
        if now is None:
            raise CairnError(
                "git_failed",
                f"git would not say what is dirty in {working_directory}, so what this "
                "step may commit cannot be established",
                detail={"working_directory": str(working_directory)},
            )
        if before is not None:
            altered = _altered_excluded_paths(root, before, marker)
            if altered:
                raise CairnError(
                    "excluded_path_changed",
                    f"step {step_id!r} changed {len(altered)} path(s) that were already "
                    "uncommitted when it started, so its commit can neither claim them as "
                    "its own work nor prove it left them alone: "
                    f"{', '.join(altered)}. Nothing was committed, and this step's marker "
                    "was withdrawn so the next run redoes it; settle that work first",
                    detail={"step": step_id, "altered_excluded_paths": altered},
                )
        own = sorted(set(now) - set(before)) if before is not None else []
        # The marker is staged by path whenever the tree has something to say about it,
        # whoever else had it dirty; a pathspec naming a path git has nothing for fails
        # the whole add, so an unchanged or absent marker is not named at all.
        staged_paths: list[str] = sorted(set(own) | ({marker} if marker in now else set()))
        # What the step found dirty and did not take. The marker is the one path it takes
        # regardless, so naming it here would make the record contradict the commit.
        left = (
            sorted((set(now) & set(before)) - set(staged_paths)) if before is not None else []
        )
        if not staged_paths:
            return CommandResult(
                EXIT_OK, "noop", "nothing to commit", _follow_up(left), False, None,
                {"working_directory": str(working_directory), "left_uncommitted": left},
            )
        addable = _addable_paths(working_directory, root, staged_paths)
        if addable:
            git(working_directory, ("--literal-pathspecs", "add", "--all", "--", *addable))
        # The question is what the commit would record, so it is asked of the index, and of
        # the step's own paths within it. A working tree can hold residue `add` cannot stage
        # — dirty submodule content, for one — and reading the tree instead turns a step
        # that had nothing to say into a step whose commit failed.
        staged = git(
            working_directory,
            ("--literal-pathspecs", "diff", "--cached", "--quiet", "--", *staged_paths),
            check=False,
        )
        detail: dict[str, Any] = {
            "working_directory": str(working_directory),
            "left_uncommitted": left,
        }
        follow_up = _follow_up(left)
        if staged.exit_code == 0:
            return CommandResult(EXIT_OK, "noop", "nothing to commit", follow_up, False, None, detail)
        # Counted before the commit, over the paths the commit is about to carry. The run
        # record must be readable on a machine that no longer has the repository, so a
        # step's diffstat is recorded at the one moment it is true rather than re-derived
        # from git by a reader.
        changed = _staged_diffstat(working_directory, staged_paths)
        # Named paths rather than the whole index: anything a concurrent session staged
        # while the step ran is in the index too, and a commit that swept it in would be
        # the very "somebody else's work landed as this step's" fault this scoping closes.
        git(
            working_directory,
            ("--literal-pathspecs", "commit", "--no-verify", "-m", message, "--", *staged_paths),
        )
        head = git(working_directory, ("rev-parse", "HEAD")).stdout
    return CommandResult(
        EXIT_OK,
        "done",
        f"committed {head[:12]}",
        follow_up,
        False,
        None,
        {**detail, "commit": head, "diffstat": changed},
    )


def _follow_up(left: list[str]) -> list[str]:
    """One line for every path the step found dirty and left alone, or none where it left none."""
    if not left:
        return []
    return [
        (
            f"left {len(left)} path(s) uncommitted that were already dirty before "
            f"the step started: {', '.join(left)}"
        )
    ]


def _dirty_before(context: RuntimeContext, step_id: str) -> dict[str, str] | None:
    """What the work step saw dirty before its session, or None where it recorded none.

    None is one answer only: the work node left no snapshot — no report of this run at all,
    because its marker gate skipped it, or a report without the key, as a marker no-op or a
    timed wait writes. Such a step dirtied nothing of its own, so the marker alone is its
    whole commit.

    **Every other departure from a snapshot is a refusal**, never that answer. A report that
    will not parse, a `detail` that is not a mapping, a snapshot recorded as absent because
    git would not say, and a snapshot of any shape but path to digest all leave unknown what
    this step may take — and reading any of them as "no snapshot" commits the marker over
    whatever work the step did, which the next run then skips. That is the [21 B] outcome
    this scoping exists to prevent, so a present key is never read as an absent one.
    """
    try:
        report = read_step_report(
            context.report_path.parent, node_name("work", step_id), context.run_id
        )
    except CairnError as exc:
        if exc.cause == "missing_report":
            return None
        raise
    detail = report.get("detail")
    if not isinstance(detail, dict):
        raise _unreadable_snapshot(step_id, "its report carries no detail mapping")
    found = cast(dict[str, Any], detail).get(DIRTY_BEFORE, _NO_SNAPSHOT)
    if found is _NO_SNAPSHOT:
        return None
    if found is None:
        raise CairnError(
            "git_failed",
            f"the work node of step {step_id!r} could not read what was already dirty when "
            "it started, so what this commit may take cannot be established",
            detail={"step": step_id},
        )
    if not isinstance(found, dict):
        raise _unreadable_snapshot(
            step_id, f"its snapshot is a {type(found).__name__}, not a mapping of path to digest"
        )
    recorded = cast(dict[Any, Any], found)
    if not all(
        isinstance(path, str) and isinstance(digest, str)
        for path, digest in recorded.items()
    ):
        raise _unreadable_snapshot(
            step_id, "its snapshot maps something other than path to digest"
        )
    return cast(dict[str, str], recorded)


def _unreadable_snapshot(step_id: str, why: str) -> CairnError:
    return CairnError(
        "invalid_report",
        f"the work node of step {step_id!r} left a report this commit cannot read as a "
        f"snapshot — {why} — so what this commit may take cannot be established. A report "
        "written by a different Cairn than the one committing it reads this way",
        detail={"step": step_id},
    )


# Distinguishes a report with no snapshot key from one whose snapshot is `null`.
_NO_SNAPSHOT = object()


__all__ = [
    "ABSENT_CONTENT",
    "BRANCH_OWNER_PREFIX",
    "DIRTY_BEFORE",
    "MIGRATED",
    "OWNED_ELSEWHERE",
    "SUPERSEDED",
    "UNATTRIBUTABLE",
    "UNREADABLE_CONTENT",
    "BranchOwner",
    "LegacyVerdict",
    "commit_step",
    "dirty_snapshot",
    "owner_ref",
    "prune_worktrees",
    "read_branch_owner",
    "setup_worktree",
    "write_branch_owner",
]
