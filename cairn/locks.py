"""The git write mutex and the repository run lock.

Two locks with two different lifetimes, and the difference is the whole design.

The **mutex** serialises the git writes of one moment. It is an advisory file lock the
kernel drops the instant its holder dies, which is what a mutex wants: a crashed writer
must never keep the next one out. Agent subprocesses are deliberately outside it — they
write their own worktree's index and their own branch's ref, neither of which any other
step touches, and they are where the wall-clock is.

The **run lock** outlives every process that touches it. One step takes it, a different
step gives it back, and a crash between them leaves it held with nobody running. So it
cannot be a file lock; it is a git ref, updated only by compare-and-swap, which is what
makes two racing acquisitions resolve to one winner instead of two.
"""

from __future__ import annotations

import fcntl
import json
import os
import socket
import time
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, NamedTuple, TypedDict, cast

from cairn.core import CairnError
from cairn.gitio import (
    common_directory,
    git,
    git_directory,
    hash_object,
    read_blob,
    resolve_ref,
    state_directory,
    update_ref,
)
from cairn.liveness import self_start_time
from cairn.plan.schema import MUTEX_WAIT
from cairn.supervise import last_record, owner_liveness

RUN_LOCK_REF = "refs/cairn/run-lock"
MUTEX_FILE = "git-write.lock"

# How long a git write waits for the mutex before calling the repository stuck. The
# relation that matters is stated where all three numbers live: this wait plus one git
# invocation's own timeout fits inside a support step's budget with room to write the
# report, so a jammed mutex reports itself rather than being killed with nothing recorded.
MUTEX_WAIT_SECONDS = float(MUTEX_WAIT)
MUTEX_POLL_SECONDS = 0.05

# A git lock file younger than this may belong to a live git process — an agent's own
# commit runs outside the mutex by design — so only an older one is treated as debris a
# killed step left behind. No ordinary git write on a working repository holds its lock
# for minutes.
STALE_GIT_LOCK_SECONDS = 300.0

# The reclaim window is never configured on its own: it is the run's own maximum duration
# scaled by this one factor, so a lock can only be taken from a run that has already
# outlived every bound its plan gave it, with margin. A second, absolute grace would be an
# independent number to keep in step with the first, which is how the two drift apart.
RECLAIM_FACTOR = 1.25

_ADMIN_LOCK_NAMES = (
    "index.lock",
    "HEAD.lock",
    "config.lock",
    "packed-refs.lock",
    "shallow.lock",
    "ORIG_HEAD.lock",
)

_MERGE_STATE_FILES = (
    ("MERGE_HEAD", "a merge"),
    ("CHERRY_PICK_HEAD", "a cherry-pick"),
    ("REVERT_HEAD", "a revert"),
    ("rebase-merge", "a rebase"),
    ("rebase-apply", "a rebase"),
)


class LockRecord(TypedDict):
    """What a held run lock says about its holder, so a refusal can name it."""

    run_id: str
    plan: str
    repository: str
    host: str
    pid: int
    pid_started_at: float | None
    acquired_at: float
    run_timeout_seconds: float
    reclaim_after: float
    # Where the engine records the *run*, as against the step that took the lock. The
    # orchestrator outlives every step, so this is the only process whose liveness answers
    # whether the holder is still working.
    status_file: str | None


class HeldLock(NamedTuple):
    """A held run lock, and the object id a release must compare against."""

    record: LockRecord
    object_id: str
    reclaimed_from: LockRecord | None


class Reclaimability(NamedTuple):
    """Whether a held lock may be taken, and the reason in the language of the refusal."""

    reclaimable: bool
    reason: str


def _now() -> float:
    return time.time()


def describe_holder(record: LockRecord) -> str:
    """One line naming the holder, used verbatim in a refusal (I6).

    Identity and age only. When the lock comes free is deliberately not stated here: the
    window is one of three answers and the weakest, so a caller that wants it asks
    `taking_is_allowed`, which knows whether liveness has overruled the clock. Promising a
    reclaim time this record cannot honour would send someone back at the named minute to
    the identical refusal, for ever.
    """
    held_for = max(0.0, _now() - record["acquired_at"])
    return (
        f"run {record['run_id']!r} of plan {record['plan']!r} on {record['host']} "
        f"(pid {record['pid']}), held for {held_for / 60:.1f} minutes"
    )


# ---------------------------------------------------------------------------
# Git working state: the debris a killed step leaves, and the state it must not write over
# ---------------------------------------------------------------------------


def _admin_directories(directory: Path) -> Iterator[Path]:
    common = common_directory(directory)
    yield common
    worktrees = common / "worktrees"
    if worktrees.is_dir():
        for child in sorted(worktrees.iterdir()):
            if child.is_dir():
                yield child


def stale_git_locks(
    directory: Path, *, older_than_seconds: float = STALE_GIT_LOCK_SECONDS
) -> list[Path]:
    """Every git lock file old enough to be debris rather than a live writer's."""
    cutoff = _now() - older_than_seconds
    found: list[Path] = []
    for admin in _admin_directories(directory):
        candidates = [admin / name for name in _ADMIN_LOCK_NAMES]
        references = admin / "refs"
        if references.is_dir():
            candidates.extend(sorted(references.rglob("*.lock")))
        for candidate in candidates:
            try:
                if candidate.is_file() and candidate.stat().st_mtime < cutoff:
                    found.append(candidate)
            except OSError:
                continue
    return found


def clear_stale_git_locks(
    directory: Path, *, older_than_seconds: float = STALE_GIT_LOCK_SECONDS
) -> list[str]:
    """Remove the lock files a killed step left, and report what was removed.

    Cairn's own mutex file is never a candidate: it lives under the admin directory's
    `cairn/` subdirectory, which nothing here walks.
    """
    removed: list[str] = []
    for path in stale_git_locks(directory, older_than_seconds=older_than_seconds):
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError:
            continue
        removed.append(str(path))
    return removed


def unresolved_merge(directory: Path) -> str | None:
    """The name of the operation left half-finished in this worktree, or None.

    A killed step can leave a conflicted index behind. Committing over it would record a
    merge nobody resolved, so every path that is about to write halts here instead.
    """
    admin = git_directory(directory)
    for name, description in _MERGE_STATE_FILES:
        if (admin / name).exists():
            return description
    unmerged = git(directory, ("ls-files", "--unmerged"), check=False)
    if unmerged.exit_code == 0 and unmerged.stdout:
        return "an unresolved conflict in the index"
    return None


def refuse_dirty_repository(directory: Path) -> None:
    """Halt a run whose target repository already has uncommitted work in it.

    A chain step runs in the repository's own working tree and its commit stages
    everything there, so anything the user left uncommitted would be swept into a commit
    the plan claims as a step's output. Refusing here costs nothing — the run has not spent
    anything yet — and it is the only moment at which the two are still distinguishable.
    """
    dirty = git(directory, ("status", "--porcelain")).stdout
    if not dirty:
        return
    paths = [line[3:] for line in dirty.splitlines()[:10]]
    raise CairnError(
        "repository_dirty",
        f"{directory} has uncommitted work in it, which a step's commit would sweep up as "
        f"its own output: {', '.join(paths)}. Commit it, remove it, or `git stash -u` "
        "before running — plain `git stash` leaves untracked files behind",
        detail={"working_directory": str(directory), "paths": paths},
    )


def refuse_unresolved_merge(directory: Path) -> None:
    """Halt rather than write over a half-finished merge."""
    pending = unresolved_merge(directory)
    if pending is not None:
        raise CairnError(
            "merge_in_progress",
            f"{directory} has {pending} in progress; resolve or abort it before Cairn "
            "writes to this repository",
            detail={"working_directory": str(directory), "pending": pending},
        )


# ---------------------------------------------------------------------------
# The git write mutex
# ---------------------------------------------------------------------------


@contextmanager
def git_write_mutex(
    directory: Path,
    *,
    wait_seconds: float = MUTEX_WAIT_SECONDS,
    stale_after_seconds: float = STALE_GIT_LOCK_SECONDS,
) -> Generator[None]:
    """Hold every worktree operation and parent-branch write of one repository, one at a time.

    Taken inside the subcommand that writes, never around it, so the serialisation holds
    however the step was invoked — the engine's own `flock` guards only its own worktree
    add and remove, and nothing an agent does ([research-dagu.md]).
    """
    path = state_directory(directory) / MUTEX_FILE
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    deadline = time.monotonic() + wait_seconds
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise CairnError(
                        "git_mutex_timeout",
                        f"waited {wait_seconds:g} seconds for the git write mutex on "
                        f"{directory} and it never came free",
                        detail={"mutex": str(path)},
                    ) from None
                time.sleep(MUTEX_POLL_SECONDS)
            except OSError as exc:
                # A filesystem that cannot lock at all is not contention, and waiting five
                # minutes to say so would burn most of the step's budget before reporting.
                raise CairnError(
                    "git_mutex_timeout",
                    f"the git write mutex on {directory} cannot be locked: {exc}",
                    detail={"mutex": str(path), "errno": exc.errno},
                ) from exc
        os.ftruncate(descriptor, 0)
        os.write(descriptor, f"{os.getpid()} {socket.gethostname()}\n".encode())
        # Debris is cleared under the mutex rather than before it, so two writers can
        # never both decide a lock file is stale and race to unlink it.
        clear_stale_git_locks(directory, older_than_seconds=stale_after_seconds)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


# ---------------------------------------------------------------------------
# The run lock
# ---------------------------------------------------------------------------


_REQUIRED_FIELDS: tuple[tuple[str, type | tuple[type, ...]], ...] = (
    ("run_id", str),
    ("plan", str),
    ("repository", str),
    ("host", str),
    ("pid", int),
    ("acquired_at", (int, float)),
    ("run_timeout_seconds", (int, float)),
    ("reclaim_after", (int, float)),
)


def _read_record(directory: Path, object_id: str) -> LockRecord | None:
    """The holder's record, or None when the ref points at something Cairn cannot read.

    Every field the refusal and the reclaim decision go on to read is checked here, and a
    record short of any of them is no record at all. Validating two fields and casting the
    rest would turn a half-written or version-skewed payload into a `KeyError` on the
    reclaim path — a repository nobody can run against and no way back that is not an
    operator procedure, which is precisely what this branch exists to prevent.

    A payload git could not produce is a different thing from a payload that does not
    parse, and it is raised rather than returned: a momentary failure to read the lock
    must never be grounds for taking it.
    """
    kind = git(directory, ("cat-file", "-t", object_id), check=False)
    if kind.exit_code != 0 or kind.stdout != "blob":
        # The ref points at something that is not a lock record at all, permanently. That
        # is an unreadable lock, not a failure to read one, so it stays reclaimable.
        return None
    payload: Any
    try:
        payload = json.loads(read_blob(directory, object_id))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    record = cast(dict[str, Any], payload)
    for name, expected in _REQUIRED_FIELDS:
        value = record.get(name)
        if isinstance(value, bool) or not isinstance(value, expected):
            return None
    return cast(LockRecord, record)


def _held(directory: Path) -> tuple[str, LockRecord | None] | None:
    """The object the ref points at and the record it holds, or None when the ref is absent.

    The two cases the ref existing can mean are kept apart here. A lock whose payload
    cannot be read is still a lock — the ref is there and every compare-and-swap must name
    it — but it names no holder, so it is reclaimable rather than a permanent refusal. The
    alternative is a repository nobody can run against and no way back that is not an
    operator procedure, which I4 forbids.
    """
    object_id = resolve_ref(directory, RUN_LOCK_REF)
    if object_id is None:
        return None
    return object_id, _read_record(directory, object_id)


def read_run_lock(directory: Path) -> tuple[LockRecord, str] | None:
    """The current holder and the object id it is pinned to, or None when nobody holds it."""
    held = _held(directory)
    if held is None or held[1] is None:
        return None
    return held[1], held[0]


def reclaimability(record: LockRecord, *, now: float | None = None) -> Reclaimability:
    """Whether the lock's own window has passed, and why.

    The window is the fallback criterion, used whenever nothing can say what the holder is
    doing. It is never decided from the process that *recorded* the lock: that lock is
    taken by one short-lived step and given back by another, so the recorder has already
    exited by the time the run's second step starts, and reclaiming on its death would
    take the lock off every live run on the machine. The recorded process is kept for the
    refusal to name, and for nothing else.
    """
    moment = _now() if now is None else now
    if moment >= record["reclaim_after"]:
        return Reclaimability(True, "the holder outlived its run timeout")
    return Reclaimability(
        False,
        f"the holder may run until {record['reclaim_after'] - moment:.0f} seconds from now",
    )


def holder_liveness(record: LockRecord) -> bool | None:
    """Whether the holding *run* is still going, or None when nothing can say.

    Asked of the engine's record for the run, whose `pid` is the orchestrator — the one
    process that spans the whole run. A record Cairn cannot find or read answers None, and
    the window decides instead.
    """
    path = record.get("status_file")
    if not isinstance(path, str) or not path:
        return None
    try:
        engine_record = last_record(Path(path))
    except CairnError:
        return None
    if engine_record is None:
        return None
    return owner_liveness(engine_record)


def taking_is_allowed(record: LockRecord, *, now: float | None = None) -> Reclaimability:
    """Whether a held lock may be taken from its holder, on the best evidence available.

    Liveness outranks the clock in both directions, and that is the point. A run that is
    provably still going keeps its repository however far past its estimate it has gone —
    the estimate bounds what a plan may declare, not what a live run is permitted to
    finish. A run whose orchestrator is provably gone gives its repository up at once,
    without waiting out a window measured for a crash nobody can see.

    The window is what answers when neither is provable, which is the only case it was
    ever a good answer to.
    """
    alive = holder_liveness(record)
    if alive is True:
        return Reclaimability(False, "the holder's run is still running")
    if alive is False:
        return Reclaimability(True, "the holder's run is gone")
    return reclaimability(record, now=now)


def _refuse(record: LockRecord, repository: Path) -> CairnError:
    """Name the holder and say what would have to change for the repository to come free.

    The reason is the one the decision actually turned on, so a refusal never names a
    minute at which nothing will be different.
    """
    verdict = taking_is_allowed(record)
    return CairnError(
        "repository_busy",
        f"{repository} is already held by {describe_holder(record)}; {verdict.reason}",
        detail={
            "holder": dict(record),
            "repository": str(repository),
            "reason": verdict.reason,
        },
    )


def acquire_run_lock(
    directory: Path,
    *,
    run_id: str,
    plan: str,
    run_timeout_seconds: float,
    reclaim_factor: float = RECLAIM_FACTOR,
    status_file: str | None = None,
) -> HeldLock:
    """Take the repository's run lock, or refuse naming the run that holds it.

    Keyed on the repository's shared admin directory, so two plans against one repository
    contend and two worktrees of it are one contender — which is the case the engine's own
    per-DAG-name serialisation would let through ([research-dagu.md]).
    """
    repository = common_directory(directory)
    acquired_at = _now()
    record: LockRecord = {
        "run_id": run_id,
        "plan": plan,
        "repository": str(repository),
        "host": socket.gethostname(),
        "pid": os.getpid(),
        "pid_started_at": self_start_time(),
        "acquired_at": acquired_at,
        "run_timeout_seconds": run_timeout_seconds,
        "reclaim_after": acquired_at + run_timeout_seconds * reclaim_factor,
        "status_file": status_file,
    }
    payload = json.dumps(record, indent=2, sort_keys=True) + "\n"
    object_id = hash_object(directory, payload)

    held = _held(directory)
    if held is None:
        if update_ref(directory, f"create {RUN_LOCK_REF} {object_id}"):
            return HeldLock(record, object_id, None)
        held = _held(directory)
        if held is None:
            raise CairnError(
                "repository_busy",
                f"{repository} was taken and released again during acquisition; run again",
                detail={"repository": str(repository)},
            )

    holder_object, holder = held
    own = holder is not None and holder["run_id"] == run_id
    if not own and holder is not None and not taking_is_allowed(holder).reclaimable:
        raise _refuse(holder, repository)
    # A run retaking its own lock renews the lease rather than inheriting it. `dagu retry`
    # reuses the run identifier, so refusing would block the recovery — but returning the
    # old record unchanged would leave an already-expired window expired, and a third run
    # would reclaim the repository while the retry was writing to it.
    # The swap names the exact object the refusal was judged against, so two runs that
    # both found the same dead holder cannot both replace it.
    if not update_ref(directory, f"update {RUN_LOCK_REF} {object_id} {holder_object}"):
        current = read_run_lock(directory)
        if current is None:
            raise CairnError(
                "repository_busy",
                f"{repository} changed hands during reclaim; run again",
                detail={"repository": str(repository)},
            )
        raise _refuse(current[0], repository)
    return HeldLock(record, object_id, None if own else holder)


def refuse_lost_repository(directory: Path, run_id: str) -> None:
    """Halt before spending or writing if this repository now belongs to a different run.

    A run whose lock was reclaimed while it queued would otherwise find out at its next
    commit — an hour of paid agent time later, with a second run already writing to the
    same repository. One ref read in front of that is cheap.

    Only a lock held by *somebody else* is evidence of loss. An absent lock is not: these
    subcommands are the step vocabulary, exercisable on their own, and a working directory
    that is no repository at all has nothing to lose. The same three-valued discipline the
    reclaim decision uses — refuse on proof, never on silence.

    This is a check at the head of a step, not a heartbeat. A run that renewed its lease
    as it worked would make the reclaim window meaningless as a bound on how long a
    *crashed* run holds a repository, which is the only job that window has.
    """
    try:
        held = read_run_lock(directory)
    except CairnError as exc:
        # Only "there is no repository here" is silence. A repository that will not answer
        # is the case this module elsewhere insists must fail closed: reading it as "no
        # lock, carry on" would spend a whole agent budget in a repository another run may
        # well own, which is the one outcome the guard exists to prevent.
        if exc.cause == "not_a_repository":
            return
        raise
    if held is None:
        return
    record, _ = held
    if record["run_id"] == run_id:
        return
    raise CairnError(
        "lock_not_held",
        f"run {run_id!r} no longer holds {record['repository']}, which is held by "
        f"{describe_holder(record)}",
        detail={"run_id": run_id, "holder": dict(record)},
    )


def release_run_lock(directory: Path, *, run_id: str) -> LockRecord | None:
    """Give the lock back, refusing only when it belongs to a different run.

    The release runs on every path a run can end on, including the failure path, so it is
    stated as a postcondition rather than an action: afterwards, this run does not hold
    this repository. A lock that was never taken, or was already given back, satisfies that
    and returns None.

    The owner check is what stays hard. A run that halted *because* the repository was busy
    reaches this same code, and it must never release the lock of the run it lost to.
    """
    repository = common_directory(directory)
    held = _held(directory)
    if held is None:
        return None
    holder_object, holder = held
    if holder is None:
        raise CairnError(
            "lock_not_held",
            f"the lock on {repository} names no holder Cairn can read, so run {run_id!r} "
            "cannot prove it owns it; it stays until a later run reclaims it",
            detail={"repository": str(repository), "run_id": run_id},
        )
    if holder["run_id"] != run_id:
        raise CairnError(
            "lock_not_held",
            f"run {run_id!r} does not hold {repository}; it is held by "
            f"{describe_holder(holder)}",
            detail={"holder": dict(holder), "run_id": run_id},
        )
    if not update_ref(directory, f"delete {RUN_LOCK_REF} {holder_object}"):
        raise CairnError(
            "lock_not_held",
            f"the lock on {repository} changed hands while run {run_id!r} was releasing it",
            detail={"holder": dict(holder), "run_id": run_id},
        )
    return holder


__all__ = [
    "MUTEX_WAIT_SECONDS",
    "RECLAIM_FACTOR",
    "RUN_LOCK_REF",
    "STALE_GIT_LOCK_SECONDS",
    "HeldLock",
    "LockRecord",
    "Reclaimability",
    "acquire_run_lock",
    "clear_stale_git_locks",
    "describe_holder",
    "git_write_mutex",
    "holder_liveness",
    "read_run_lock",
    "reclaimability",
    "refuse_dirty_repository",
    "refuse_lost_repository",
    "refuse_unresolved_merge",
    "release_run_lock",
    "stale_git_locks",
    "taking_is_allowed",
    "unresolved_merge",
]
