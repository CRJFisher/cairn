"""Deciding whether a recorded process is still the process that was recorded.

After a crash the engine's own status field says `running` forever ([01]), so liveness is
never read from a record's status. It is decided from the process identifier together with
the moment that process started: a bare `kill -0` would call a recycled identifier alive
and reclaim nothing, or worse, refuse to reclaim a lock whose owner died hours ago.

**The answer has three values, not two.** A probe that could not look is not a probe that
found nothing: Cairn is ordinarily driven from inside a coding-agent harness whose shell may
not inspect processes at all, and a reader there that reported a live run as dead would
send a person to spend money undoing work that is still going ([23 A]). So "I could not
look" is `None`, kept apart from "I looked and it is not there", which is `False` and is
the only answer that ever reads as a death.
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
    """The epoch second `pid` started at, or None when `ps` could not say.

    Read as an elapsed time rather than as a start date. `ps -o lstart=` prints local
    civil time, so the same process reads an hour different across a daylight-saving
    boundary or a timezone change — and an hour's disagreement against a two-second
    tolerance would call a live run dead and write a terminal status into its record.
    Elapsed seconds carry no timezone at all.

    None is deliberately one answer for two facts — no such process, and a `ps` the shell
    refused — because this is not the function that decides death. `_process_exists` is,
    and it is asked first; by the time this is consulted the identifier is known to be
    live, so a None here means the identity could not be confirmed, never that it failed.
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


def _process_exists(pid: int) -> bool | None:
    """Whether the identifier names a live process at all, ignoring which one.

    The kernel answers this before it answers anything about permission: a signal to an
    identifier nobody holds is `ESRCH` whoever asks, so `ProcessLookupError` is the one
    decisive death. `PermissionError` is a process owned by someone else, and therefore
    alive. Any other refusal is the probe itself being denied — a sandbox that will not
    let this shell ask — and that is neither answer.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


def process_is_alive(
    pid: int,
    started_at: float | None,
    *,
    tolerance_seconds: float = START_TIME_TOLERANCE_SECONDS,
) -> bool | None:
    """Whether `pid` is still the process that started at `started_at`.

    Three answers. `False` is a death the probe established — the identifier names
    nothing, or names a process started at a different moment, which is a recycled
    identifier and not the run. `True` is the recorded process, still going. `None` is a
    reader that could not look: the existence probe was refused, or the identifier is live
    and `ps` would not confirm which process it is. A caller that reads None as a death
    writes `orchestrator_died` over a run that is working, and one that reads it as life
    refuses to reclaim a lock whose holder may be gone — so it is handed on as itself, and
    each consumer says what it does with it.

    A record with no start time is trusted no further than the identifier itself: the
    process is called alive when one exists, because calling it dead would let a reclaim
    run against work that is still going.
    """
    exists = _process_exists(pid)
    if exists is not True:
        return exists
    if started_at is None:
        return True
    actual = process_start_time(pid)
    if actual is None:
        return None
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
