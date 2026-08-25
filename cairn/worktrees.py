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

Nothing here deletes content it cannot attribute. A directory that has to go is renamed
aside, because the one thing a killed agent leaves that matters is uncommitted work.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from cairn.core import EXIT_OK, CairnError, CommandResult
from cairn.gitio import (
    branch_exists,
    checked_out_branch,
    common_directory,
    git,
    is_ancestor,
    main_working_tree,
    same_repository,
    tree_state,
    working_tree_root,
    worktree_entries,
)
from cairn.locks import git_write_mutex, refuse_unresolved_merge, unresolved_merge
from cairn.topology import WORKTREES_SUFFIX

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

# Ordered by the cost of getting it wrong: every refusal is decided before any repair, and
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
    # A registration is only a holder while its directory is still there. Branch names
    # carry no plan slug while worktree paths do, so a crashed run of another plan leaves
    # exactly this: a registration for `step/<id>` at a path nothing occupies. Refusing on
    # it would halt every later plan naming that step, permanently, over a directory the
    # create arm's own `worktree prune` clears.
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
    repository: Path, worktree: Path, branch: str, base: str
) -> CommandResult:
    """Bring the step's worktree to a state its agent can work in, from any starting point."""
    if Path(os.path.realpath(worktree)) == main_working_tree(repository):
        raise CairnError(
            "worktree_unusable",
            f"{worktree} is the repository's own working tree, not a step worktree",
            detail={"worktree": str(worktree)},
        )
    with git_write_mutex(repository):
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
        detail={
            **outcome.detail,
            "state": state,
            "worktree": str(worktree),
            "branch": branch,
            "base": base,
        }
    )


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
    than being checked out over. Convergence never costs work.
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
    worktrees: list[str],
    branches: list[str],
    *,
    parent: str,
    force: bool = False,
) -> CommandResult:
    """Remove a wave's worktrees and delete its fully merged branches, never unmerged ones.

    Every green run prunes, so nothing accumulates. A dirty worktree is refused rather than
    discarded unless the caller asks for it explicitly: uncommitted work in a worktree is a
    killed agent's output.
    """
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
        for branch in branches:
            if not branch_exists(repository, branch):
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


def _staged_diffstat(working_directory: Path) -> dict[str, int]:
    """What the index would commit, counted. A binary file counts as changed and nothing more.

    git spells a binary file's line counts `-`, which is not "zero lines changed" but "the
    question does not apply"; counting it as zero would be a plausible default in the one
    record that refuses them.
    """
    counted = git(working_directory, ("diff", "--cached", "--numstat"), check=False)
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


def commit_all(working_directory: Path, message: str) -> CommandResult:
    """Commit whatever the step left behind, distinguishing nothing-to-do from a failure.

    A no-op when there is nothing staged and a failure when staging itself fails: the two
    must never be confused, because one is a step that had nothing to say and the other is
    a step whose output was lost.
    """
    refuse_unresolved_merge(working_directory)
    root = working_tree_root(working_directory)
    with git_write_mutex(working_directory):
        git(working_directory, ("add", "--all", "--", str(root)))
        # The question is what the commit would record, so it is asked of the index. A
        # working tree can hold residue `add` cannot stage — dirty submodule content, for
        # one — and reading the tree instead turns a step that had nothing to say into a
        # step whose commit failed.
        staged = git(working_directory, ("diff", "--cached", "--quiet"), check=False)
        if staged.exit_code == 0:
            return CommandResult(
                EXIT_OK,
                "noop",
                "nothing to commit",
                [],
                False,
                None,
                {"working_directory": str(working_directory)},
            )
        # Counted before the commit, while the index still holds exactly what is about to
        # become it. The run record must be readable on a machine that no longer has the
        # repository, so a step's diffstat is recorded at the one moment it is true rather
        # than re-derived from git by a reader.
        changed = _staged_diffstat(working_directory)
        git(working_directory, ("commit", "--no-verify", "-m", message))
        head = git(working_directory, ("rev-parse", "HEAD")).stdout
    return CommandResult(
        EXIT_OK,
        "done",
        f"committed {head[:12]}",
        [],
        False,
        None,
        {
            "commit": head,
            "working_directory": str(working_directory),
            "diffstat": changed,
        },
    )


__all__ = [
    "commit_all",
    "prune_worktrees",
    "setup_worktree",
]
