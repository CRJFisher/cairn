"""Deciding whether a recorded process is still the process that was recorded.

After a crash the engine's own status field says `running` forever ([01]), so liveness is
never read from a record's status. It is decided from the process identifier together with
the moment that process started: a bare `kill -0` would call a recycled identifier alive
and reclaim nothing, or worse, refuse to reclaim a lock whose owner died hours ago.
"""

from __future__ import annotations

import os
import subprocess
import time

# `ps` reports elapsed time to the second and the engine records a start in milliseconds,
# so two readings of one process differ by under a second by construction. Five absorbs
# that and the interval between asking `ps` and reading the clock, while still separating
# any two processes a recycled identifier could name — identifiers are handed out in
# sequence, so a reused one belongs to a process started far more than seconds later.
START_TIME_TOLERANCE_SECONDS = 5.0
PS_TIMEOUT_SECONDS = 10


def parse_elapsed(reported: str) -> float | None:
    """Seconds from `ps -o etime=`, which spells them `[[dd-]hh:]mm:ss`."""
    days, _, clock = reported.rpartition("-")
    parts = clock.split(":")
    if not 1 <= len(parts) <= 3:
        return None
    try:
        seconds = 0.0
        for part in parts:
            seconds = seconds * 60 + int(part)
        if days:
            seconds += int(days) * 86400
    except ValueError:
        return None
    return seconds


def process_start_time(pid: int) -> float | None:
    """The epoch second `pid` started at, or None when no such process exists.

    Read as an elapsed time rather than as a start date. `ps -o lstart=` prints local
    civil time, so the same process reads an hour different across a daylight-saving
    boundary or a timezone change — and an hour's disagreement against a two-second
    tolerance would call a live run dead and write a terminal status into its record.
    Elapsed seconds carry no timezone at all.
    """
    if pid <= 0:
        return None
    try:
        completed = subprocess.run(
            ["ps", "-p", str(pid), "-o", "etime="],
            capture_output=True,
            text=True,
            timeout=PS_TIMEOUT_SECONDS,
            env={**os.environ, "LC_ALL": "C"},
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    now = time.time()
    if completed.returncode != 0:
        return None
    elapsed = parse_elapsed(completed.stdout.strip())
    if elapsed is None:
        return None
    return now - elapsed


def _process_exists(pid: int) -> bool:
    """Whether the identifier names a live process at all, ignoring which one."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Owned by another user and therefore alive; signalling it is not the question.
        return True
    return True


def process_is_alive(
    pid: int,
    started_at: float | None,
    *,
    tolerance_seconds: float = START_TIME_TOLERANCE_SECONDS,
) -> bool:
    """Whether `pid` is still the process that started at `started_at`.

    A record with no start time is trusted no further than the identifier itself: the
    process is called alive when one exists, because calling it dead would let a reclaim
    run against work that is still going.
    """
    if not _process_exists(pid):
        return False
    if started_at is None:
        return True
    actual = process_start_time(pid)
    if actual is None:
        return False
    return abs(actual - started_at) <= tolerance_seconds


def self_start_time() -> float | None:
    """This process's own start time, read exactly the way a reader will read it back."""
    return process_start_time(os.getpid())


__all__ = [
    "START_TIME_TOLERANCE_SECONDS",
    "parse_elapsed",
    "process_is_alive",
    "process_start_time",
    "self_start_time",
]
