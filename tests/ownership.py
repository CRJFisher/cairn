"""The run ownership every runtime subcommand proves before it writes.

A real step runs downstream of `cairn lock acquire`, so the repository it stands in holds
that run's lock for the whole of the step's life. Every runtime subcommand reads that lock
and halts without it ([27 C]) — an absent lock is the loss of the only thing that said the
run may write here, never permission.

A harness that drives those subcommands directly therefore has to put the repository in the
state a run leaves it in. Stated once, because a dozen harnesses need the same two acts and
a dozen spellings of them would each be a different idea of what a step stands in.
"""

from __future__ import annotations

from pathlib import Path

from cairn.gitio import git
from cairn.locks import acquire_run_lock

OWNED_RUN_TIMEOUT_SECONDS = 36_000.0


def _make_repository(root: Path) -> None:
    git(root, ("init", "--initial-branch=main", "--quiet", "."))
    git(root, ("config", "user.email", "cairn@test"))
    git(root, ("config", "user.name", "Cairn Test"))
    (root / "README.md").write_text("start\n", encoding="utf-8")
    git(root, ("add", "--all"))
    git(root, ("commit", "--quiet", "-m", "init"))


def own_repository(root: Path, run_id: str, *, plan: str = "p") -> Path:
    """Make `root` a repository this run holds, as its own first act would have left it.

    A directory that is already a repository keeps the history it has, so a harness that
    built its own fixture commits onto it rather than past them.

    The lease is long on purpose: a harness is not testing reclaim, and a window that
    expired mid-suite would turn an unrelated test into an intermittent one.
    """
    if not (root / ".git").exists():
        _make_repository(root)
    acquire_run_lock(
        root,
        run_id=run_id,
        plan=plan,
        run_timeout_seconds=OWNED_RUN_TIMEOUT_SECONDS,
    )
    return root


__all__ = ["OWNED_RUN_TIMEOUT_SECONDS", "own_repository"]
