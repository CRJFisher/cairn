"""Where the engine keeps its own files, asked of the engine rather than re-derived.

The engine puts its configuration and its data in **different** places, and only one of
them is the same on every platform. Measured against Dagu 2.18.2 on macOS with no
`DAGU_HOME`: the base configuration is `~/.config/dagu/base.yaml`, while the run history
is `~/Library/Application Support/dagu/data/dag-runs`. Deriving the second from the first
by path arithmetic answers with a directory that does not exist — and every reader of it
then reports an empty machine, which is the shape of failure this repository exists to
refuse: `supervise reconcile` prints that it read no records, `find_run_record` leaves a
lock with no status file to prove its holder alive, and the scheduler's safety check names
none of the failed runs it is there to name.

So the data root is the engine's own answer, read from `dagu config`, which prints every
resolved path under a label. The labels are the contract; a missing one is a hard error
naming the pinned version, the same mitigation the run model's status tables carry, because
this output is human-readable text with no schema behind it.

**The base configuration is deliberately *not* resolved this way**, and the asymmetry is the
point. Its directory is `os.UserConfigDir` on every platform the engine supports, so the
arithmetic is right where the data directory's is wrong. And asking the binary would put a
subprocess in front of the checks that judge that file — a file that **invoking the engine
creates**, carrying `catchup_window: "6h"` armed. Reading where things are must not arm the
hazard the reader is about to judge.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from functools import cache
from pathlib import Path
from typing import NamedTuple

from cairn.core import CairnError

# The engine, as a name resolved from `PATH`. Stated here rather than in the gate, because
# where the engine keeps its files is asked before there is anything to gate.
ENGINE_BINARY = "dagu"

# One short subprocess that prints paths and exits. Bounded well under a support step's own
# bound, so a wedged binary reports itself rather than being killed with nothing recorded.
CONFIG_TIMEOUT = 30

# The labels `dagu config` prints, and the field each answers — only the two Cairn reads.
# A label the engine stops printing is a hard error rather than a silent absence: both of
# these are directories something later walks, and a walk of the wrong directory reports an
# empty machine. The base configuration is deliberately not among them; `baseconfig.py`
# resolves that one and says why.
_LABELS = {
    "DAGs directory": "dags_directory",
    "DAG runs": "dag_runs",
    "Data directory": "data_directory",
}

REMEDY = (
    f"Put {ENGINE_BINARY!r} on PATH, or set DAGU_HOME to the directory it keeps its files in"
)


class EnginePaths(NamedTuple):
    """The engine's own directories that Cairn reads or writes beside."""

    dags_directory: Path
    dag_runs: Path
    data_directory: Path


def _from_home(home: Path) -> EnginePaths:
    """The layout under an explicit `DAGU_HOME`, which is pure arithmetic.

    Measured: with the variable set, every path hangs off it — so no subprocess is needed,
    and the isolated homes the suite and the preflight's gate run against stay free of one.
    """
    return EnginePaths(
        dags_directory=home / "dags",
        dag_runs=home / "data" / "dag-runs",
        data_directory=home / "data",
    )


@cache
def _asked() -> EnginePaths:
    """The engine's own answer, cached for the life of the process.

    Cached because a step asks at most a handful of times and the answer cannot change
    under a process whose whole life is one step — and because this is a subprocess on the
    path of a command that has a repository lock to take.
    """
    try:
        completed = subprocess.run(
            (ENGINE_BINARY, "config"),
            capture_output=True,
            text=True,
            timeout=CONFIG_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CairnError(
            "engine_paths_unreadable",
            f"could not ask {ENGINE_BINARY!r} where it keeps its files: {exc}. Cairn reads "
            "the engine's run history to decide whether a lock's holder is still alive and "
            "which failed runs a scheduler would re-execute, and it will not guess at the "
            f"location. {REMEDY}",
        ) from exc
    if completed.returncode != 0:
        raise CairnError(
            "engine_paths_unreadable",
            f"{ENGINE_BINARY} config exited {completed.returncode}: "
            f"{completed.stderr.strip()[:200]}. {REMEDY}",
        )
    # Read from stdout alone: the engine writes an authentication warning to stderr on every
    # invocation, and a reader that merged the streams would parse it as a label.
    found: dict[str, Path] = {}
    for line in completed.stdout.splitlines():
        label, separator, value = line.partition(":")
        field = _LABELS.get(label.strip())
        # Split on the first colon only, because a path may hold one.
        if not (separator and field is not None and value.strip()):
            continue
        if field in found:
            # A second line under one label is the output having changed shape, and taking
            # the later of two would be the same silent wrong answer a missing label is
            # refused for.
            raise CairnError(
                "engine_paths_unreadable",
                f"{ENGINE_BINARY} config printed {label.strip()!r} more than once",
            )
        found[field] = Path(value.strip())
    missing = sorted(set(_LABELS.values()) - set(found))
    if missing:
        raise CairnError(
            "engine_paths_unreadable",
            f"{ENGINE_BINARY} config named none of {missing}. Cairn reads these paths out "
            "of that command's labelled output, which carries no schema, so a label that "
            "moved is a version question rather than a bug in this run",
            detail={"missing": missing},
        )
    return EnginePaths(**found)


def forget_engine_paths() -> None:
    """Drop the cached answer, for a test standing a different engine on `PATH`."""
    _asked.cache_clear()


def engine_paths(environ: Mapping[str, str] | None = None) -> EnginePaths:
    """Where this machine's engine keeps everything, however it was configured."""
    values = os.environ if environ is None else environ
    home = values.get("DAGU_HOME")
    if home:
        return _from_home(Path(home))
    return _asked()


def run_records_path(environ: Mapping[str, str] | None = None) -> Path:
    """Where the engine keeps its run history.

    Every caller walks this directory looking for one run or for all of them, so an answer
    that is merely plausible reads as a machine with no runs on it.
    """
    return engine_paths(environ).dag_runs


# What the engine itself records about the scheduler, both measured against Dagu 2.18.2 and
# both inside the data directory, which is why that directory is now read from `dagu config`
# as well. Neither needs the API, and that is the point: Cairn holds no credential for the
# engine's API by construction, so a status that could only be answered through it could not
# be answered at all ([29 C]).
#
# One file per scheduler process, written at startup and removed on a clean exit, carrying
# `pid`, `startedAt` and a status code. Measured: a scheduler killed with `SIGKILL` leaves
# its file behind, so the file's presence is a claim about a process rather than proof of one
# — which is exactly why Cairn adjudicates it with its own process check ([liveness.py]).
SCHEDULER_REGISTRY = ("service-registry", "scheduler")
# The global pause the engine's own API writes, as `{"paused": bool, "pausedAt": moment}`.
# Measured: it survives a restart, so a paused scheduler is a live process that is deliberately
# firing nothing, and reporting that as a dead one would send a person to start a second.
SCHEDULER_PAUSE = ("scheduler", "paused.json")


def scheduler_registry_directory(environ: Mapping[str, str] | None = None) -> Path:
    """Where the engine records which process is being the scheduler."""
    return engine_paths(environ).data_directory.joinpath(*SCHEDULER_REGISTRY)


def scheduler_pause_path(environ: Mapping[str, str] | None = None) -> Path:
    """Where the engine records that the scheduler as a whole is paused."""
    return engine_paths(environ).data_directory.joinpath(*SCHEDULER_PAUSE)


def dags_directory(environ: Mapping[str, str] | None = None) -> Path:
    """The directory the scheduler watches.

    Cairn writes its definitions into the repository's own admin directory, which is not
    this one — so a schedule fires only once its definition is reachable from here
    ([triggers.md]).
    """
    return engine_paths(environ).dags_directory


__all__ = [
    "CONFIG_TIMEOUT",
    "ENGINE_BINARY",
    "SCHEDULER_PAUSE",
    "SCHEDULER_REGISTRY",
    "EnginePaths",
    "dags_directory",
    "engine_paths",
    "forget_engine_paths",
    "run_records_path",
    "scheduler_pause_path",
    "scheduler_registry_directory",
]
