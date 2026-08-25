"""Every git invocation Cairn makes, and the repository identity its locks key on.

One module runs git so that a failed invocation is a typed cause with the command and
stderr attached, rather than a `CalledProcessError` surfacing wherever it was raised.
"""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Sequence
from functools import cache
from pathlib import Path
from typing import NamedTuple

from cairn.core import CairnError
from cairn.layout import RUNS_DIRECTORY
from cairn.plan.schema import GIT_TIMEOUT

# Cairn's own directory inside git's admin directory, named once because two callers
# compose paths under it and only one of them may create it.
CAIRN_STATE = "cairn"

GIT = "git"
GIT_TIMEOUT_SECONDS = GIT_TIMEOUT

# Every variable that can point git at a different repository than the directory it is run
# in. They are removed rather than trusted: an inherited `GIT_DIR` overrides discovery from
# the working directory entirely, so one exported by a shell, a hook or a parent process
# would send a plan's every commit to a repository nobody named.
REDIRECTING_VARIABLES = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_NAMESPACE",
    "GIT_CEILING_DIRECTORIES",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM",
)

# Agents commit in their own worktrees while Cairn writes refs in the same repository, and
# the write mutex deliberately does not cover them. git gives up after 100ms on a loose ref
# and 1s on packed-refs; waiting instead turns a collision into a pause rather than a
# spurious failure inside a paid step.
REF_LOCK_TIMEOUT_MILLISECONDS = 3000
LOCK_CONFIGURATION = (
    "-c",
    f"core.filesRefLockTimeout={REF_LOCK_TIMEOUT_MILLISECONDS}",
    "-c",
    f"core.packedRefsTimeout={REF_LOCK_TIMEOUT_MILLISECONDS}",
    # Every path Cairn reads back is fed to another git command, so it has to be the path
    # and not a display of it. `core.quotePath` defaults on, and `LC_ALL=C` makes every
    # non-ASCII byte unusual, so a file named `café.md` is reported as the ten-character
    # token `"caf\303\251.md"` — which no later command can open, and which a scan that
    # drops what it cannot read would pass over in silence.
    "-c",
    "core.quotePath=false",
)


class GitOutcome(NamedTuple):
    """One git invocation's result, with both streams already decoded and stripped."""

    exit_code: int
    stdout: str
    stderr: str


def _environment() -> dict[str, str]:
    """Run git with no inherited target, no interactive prompt and no localised output.

    A prompt would hang a step with no terminal attached until its timeout, and localised
    porcelain would make the few outputs Cairn matches on machine-dependent.
    """
    environment = dict(os.environ)
    for name in REDIRECTING_VARIABLES:
        environment.pop(name, None)
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    environment["LC_ALL"] = "C"
    return environment


def absolute_directory(path: str | Path, name: str) -> Path:
    """Refuse a path that is empty or relative before any git sees it.

    An unresolved engine reference expands to the empty string and the step still runs, so
    a git command given one would silently fall back to whatever directory the step
    happened to start in ([research-dagu.md]).
    """
    text = str(path)
    if not text.strip():
        raise CairnError("invalid_arguments", f"{name} is empty")
    if not os.path.isabs(text):
        raise CairnError("invalid_arguments", f"{name} {text!r} is not an absolute path")
    return Path(text)


def git(
    directory: Path,
    arguments: Sequence[str],
    *,
    check: bool = True,
    stdin: str | None = None,
) -> GitOutcome:
    """Run one git command in `directory`, raising `git_failed` unless `check` is off."""
    command = [GIT, *LOCK_CONFIGURATION, *arguments]
    try:
        completed = subprocess.run(
            command,
            cwd=directory,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            env=_environment(),
            check=False,
        )
    except OSError as exc:
        raise CairnError(
            "process_launch_failed",
            f"could not run git: {exc}",
            detail={"command": command, "errno": exc.errno},
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise CairnError(
            "git_failed",
            f"git {' '.join(arguments)} did not finish within "
            f"{GIT_TIMEOUT_SECONDS} seconds",
            detail={"command": command},
        ) from exc
    outcome = GitOutcome(
        completed.returncode, completed.stdout.strip(), completed.stderr.strip()
    )
    if check and outcome.exit_code != 0:
        raise CairnError(
            "git_failed",
            f"git {' '.join(arguments)} failed: {outcome.stderr or outcome.stdout}",
            detail={
                "command": command,
                "exit_code": outcome.exit_code,
                "stderr": outcome.stderr,
            },
        )
    return outcome


@cache
def common_directory(directory: Path) -> Path:
    """The admin directory every worktree of one repository shares.

    This is the repository's identity for everything Cairn locks. Two worktrees of one
    repository resolve to the same path and two clones never do, which is exactly the
    distinction I6 refuses on — a lock keyed on the working tree would let two worktrees
    of one repository each believe they held it.

    Cached because it is asked on every mutex entry and cannot change under a process
    whose whole life is one step.
    """
    outcome = git(
        directory, ("rev-parse", "--path-format=absolute", "--git-common-dir"), check=False
    )
    if outcome.exit_code != 0:
        raise CairnError(
            "not_a_repository",
            f"{directory} is not inside a git repository",
            detail={"path": str(directory), "stderr": outcome.stderr},
        )
    return Path(outcome.stdout).resolve()


def git_directory(directory: Path) -> Path:
    """The admin directory of this worktree alone.

    An in-progress merge, rebase or cherry-pick is recorded here rather than in the shared
    common directory, so a guard that read the common directory would miss a conflict in
    the very worktree it is about to write to.
    """
    outcome = git(
        directory, ("rev-parse", "--path-format=absolute", "--git-dir"), check=False
    )
    if outcome.exit_code != 0 or not outcome.stdout:
        raise CairnError(
            "not_a_repository",
            f"{directory} is not inside a git repository",
            detail={"path": str(directory), "stderr": outcome.stderr},
        )
    return Path(outcome.stdout).resolve()


def working_tree_root(directory: Path) -> Path:
    """The root of the working tree `directory` sits in — a worktree's own, not the main one."""
    outcome = git(
        directory, ("rev-parse", "--path-format=absolute", "--show-toplevel"), check=False
    )
    if outcome.exit_code != 0 or not outcome.stdout:
        raise CairnError(
            "not_a_repository",
            f"{directory} is not inside a git working tree",
            detail={"path": str(directory), "stderr": outcome.stderr},
        )
    return Path(outcome.stdout).resolve()


def main_working_tree(directory: Path) -> Path:
    """The repository's own working tree, reached from any of its worktrees.

    The common directory is `<main>/.git` for an ordinary repository. A repository whose
    admin directory is not named `.git` — a `--separate-git-dir` checkout — is refused
    rather than guessed at, because every path Cairn derives hangs off this one.
    """
    common = common_directory(directory)
    if common.name != ".git":
        raise CairnError(
            "not_a_repository",
            f"{directory} resolves to the admin directory {common}, which is not a "
            "working repository's own '.git'",
            detail={"common_dir": str(common)},
        )
    return common.parent


class WorktreeEntry(NamedTuple):
    """One worktree as git itself reports it, main first."""

    path: Path
    branch: str | None
    prunable: bool
    locked: bool


def worktree_entries(directory: Path) -> list[WorktreeEntry]:
    """Every worktree git has registered for this repository.

    `prunable` and `locked` are carried because they are the two answers that change what
    may be done to a directory: a locked worktree is never touched, and a prunable one is
    a registration whose directory may still hold work.
    """
    text = git(directory, ("worktree", "list", "--porcelain")).stdout
    entries: list[WorktreeEntry] = []
    path: Path | None = None
    branch: str | None = None
    prunable = False
    locked = False
    for line in [*text.splitlines(), ""]:
        if line.startswith("worktree "):
            path = Path(os.path.realpath(line[len("worktree ") :]))
        elif line.startswith("branch "):
            branch = line[len("branch refs/heads/") :]
        elif line.startswith("prunable"):
            prunable = True
        elif line.startswith("locked"):
            locked = True
        elif not line and path is not None:
            entries.append(WorktreeEntry(path, branch, prunable, locked))
            path, branch, prunable, locked = None, None, False, False
    return entries


def same_repository(one: Path, other: Path) -> bool:
    """Whether two admin directories are the same one.

    Compared by inode where both exist, because a string comparison is wrong on a
    symlinked path, on the temp-directory aliasing macOS does, and on a case-insensitive
    filesystem.
    """
    if one == other:
        return True
    try:
        return os.path.samefile(one, other)
    except OSError:
        return False


def refuse_unusable_repository(directory: Path) -> None:
    """Refuse the two repository shapes whose worktrees Cairn cannot own.

    A bare repository has no working tree to check a step's worktree out beside. A
    submodule shares its superproject's admin directory, so a lock keyed on that directory
    would silently serialise the parent repository too — and a worktree added from inside
    one lands in the superproject's administrative space rather than the submodule's.
    """
    if git(directory, ("rev-parse", "--is-bare-repository")).stdout == "true":
        raise CairnError(
            "not_a_repository",
            f"{directory} is a bare repository, so it has no working tree for a step",
            detail={"path": str(directory)},
        )
    superproject = git(
        directory, ("rev-parse", "--show-superproject-working-tree")
    ).stdout
    if superproject:
        raise CairnError(
            "not_a_repository",
            f"{directory} is a submodule of {superproject}, whose admin directory it "
            "shares; run against the superproject or a standalone clone",
            detail={"path": str(directory), "superproject": superproject},
        )


def state_directory(directory: Path) -> Path:
    """Cairn's own directory inside the repository's admin directory, created if absent.

    It lives beside git's admin files rather than in the working tree so that no commit
    step can sweep it into a commit and no worktree removal can take it away.
    """
    path = common_directory(directory) / CAIRN_STATE
    path.mkdir(parents=True, exist_ok=True)
    return path


def runs_root(directory: Path) -> Path:
    """Where every run against this repository keeps its reports and its record.

    Beside the generated workflows in Cairn's own state, so a run's records share the
    admin directory every worktree of the repository resolves to — one place a step in a
    worktree and a step in the repository both write to, and one place a reader finds
    without the engine.
    """
    return state_directory(directory) / RUNS_DIRECTORY


def hash_object(directory: Path, content: str) -> str:
    """Write `content` into the object database and return its id."""
    return git(directory, ("hash-object", "-w", "--stdin"), stdin=content).stdout


def read_blob(directory: Path, object_id: str) -> str:
    """Read a blob written by `hash_object`."""
    return git(directory, ("cat-file", "blob", object_id)).stdout


def committed_paths(directory: Path, commit: str) -> frozenset[str] | None:
    """Every path this commit holds, or None when git would not say."""
    outcome = git(directory, ("ls-tree", "-r", "--name-only", commit), check=False)
    if outcome.exit_code != 0:
        return None
    return frozenset(line for line in outcome.stdout.splitlines() if line)


def read_committed_text(directory: Path, commit: str, path: str) -> str | None:
    """A committed file's bytes as text, or None when git would not give them.

    Decoded leniently and outside `git`'s own strict decoding, because a repository holds
    whatever bytes it holds: an image or a latin-1 fixture would otherwise raise out of
    `subprocess` itself, and a scan that a PNG can crash is not a scan. A failure to launch
    or a timeout is still the typed cause every other git call raises, so a caller can never
    mistake "git did not answer" for an answer.
    """
    arguments = ("show", f"{commit}:{path}")
    try:
        completed = subprocess.run(
            (GIT, *LOCK_CONFIGURATION, *arguments),
            cwd=directory,
            capture_output=True,
            env=_environment(),
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except OSError as exc:
        raise CairnError("process_launch_failed", f"git {' '.join(arguments)}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CairnError(
            "git_failed", f"git {' '.join(arguments)} timed out after {GIT_TIMEOUT_SECONDS}s"
        ) from exc
    if completed.returncode != 0:
        return None
    return completed.stdout.decode("utf-8", errors="replace")


def resolve_ref(directory: Path, ref: str) -> str | None:
    """The object a ref points at, or None when the ref does not exist."""
    outcome = git(directory, ("rev-parse", "--verify", "--quiet", ref), check=False)
    if outcome.exit_code != 0 or not outcome.stdout:
        return None
    return outcome.stdout


def branch_exists(directory: Path, branch: str) -> bool:
    return resolve_ref(directory, f"refs/heads/{branch}") is not None


def is_ancestor(directory: Path, commit: str, into: str) -> bool:
    """Whether `commit` is already contained in `into`.

    This is what "already landed" means, and it is asked of git rather than of anything
    Cairn wrote down: a ref cannot outlive the history it names, while a record can.
    """
    return (
        git(directory, ("merge-base", "--is-ancestor", commit, into), check=False).exit_code
        == 0
    )


def checked_out_branch(directory: Path) -> str | None:
    """The branch HEAD is on, or None when HEAD is detached."""
    outcome = git(directory, ("symbolic-ref", "--quiet", "--short", "HEAD"), check=False)
    return outcome.stdout if outcome.exit_code == 0 else None


def tree_state(directory: Path) -> tuple[str, ...] | None:
    """Every path the working tree has something to say about, or None if git will not say.

    Absent is not clean. A directory git cannot answer about has to stay distinguishable
    from one it answered "nothing here" about, because the two license opposite moves.
    """
    outcome = git(
        directory, ("status", "--porcelain", "--untracked-files=all"), check=False
    )
    if outcome.exit_code != 0:
        return None
    return tuple(line[3:] for line in outcome.stdout.splitlines() if line.strip())


REF_CONTENTION_ATTEMPTS = 5
REF_CONTENTION_PAUSE_SECONDS = 0.1


def update_ref(directory: Path, instruction: str) -> bool:
    """Apply one `git update-ref --stdin` instruction, reporting whether it took.

    Every transition of a Cairn ref goes through this: `create` fails if the ref exists,
    and `update`/`delete` fail unless the ref still holds the value the caller read. That
    compare-and-swap is what makes two racing runs resolve to one winner rather than two.

    The verdict is read from the ref, never from git's wording. `cannot lock ref` is the
    same prose for a refused swap and for a `.lock` file another git process happens to be
    holding — and those are opposite answers, one meaning "somebody else won" and the other
    "ask again in a moment". So a failed instruction is decided by looking at what the ref
    now holds, and plain contention is retried instead of being reported as a loss.
    """
    verb, _, rest = instruction.partition(" ")
    ref, _, values = rest.partition(" ")
    expected = values.split(" ")[-1] if verb in ("update", "delete") else None

    for attempt in range(REF_CONTENTION_ATTEMPTS):
        outcome = git(
            directory, ("update-ref", "--stdin"), check=False, stdin=instruction + "\n"
        )
        if outcome.exit_code == 0:
            return True
        current = resolve_ref(directory, ref)
        if verb == "create":
            if current is not None:
                return False
        elif current != expected:
            return False
        # The ref still holds what the caller swapped against, so nothing won and nothing
        # lost. Only a lock file is worth waiting on; a structural failure — a ref that
        # cannot exist because a child of it does — would never come right.
        contended = any(
            phrase in outcome.stderr
            for phrase in ("Unable to create", "File exists", "cannot lock ref")
        )
        if not contended or attempt + 1 == REF_CONTENTION_ATTEMPTS:
            raise CairnError(
                "git_failed",
                f"git update-ref left {ref} unchanged: {outcome.stderr or outcome.stdout}",
                detail={"instruction": instruction, "stderr": outcome.stderr},
            )
        time.sleep(REF_CONTENTION_PAUSE_SECONDS)
    return False


__all__ = [
    "CAIRN_STATE",
    "GIT",
    "REDIRECTING_VARIABLES",
    "REF_LOCK_TIMEOUT_MILLISECONDS",
    "GitOutcome",
    "absolute_directory",
    "branch_exists",
    "checked_out_branch",
    "committed_paths",
    "common_directory",
    "git",
    "git_directory",
    "hash_object",
    "is_ancestor",
    "main_working_tree",
    "read_blob",
    "read_committed_text",
    "refuse_unusable_repository",
    "resolve_ref",
    "state_directory",
    "tree_state",
    "update_ref",
    "working_tree_root",
]
