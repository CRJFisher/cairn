"""Logic owned by the command and bounded-wait subcommands."""

from __future__ import annotations

import math
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from cairn.core import (
    EXIT_FAILED,
    EXIT_OK,
    CairnError,
    Cancelled,
    Child,
    CommandResult,
    PopenFactory,
    launch,
    stop_orphans,
)

TERMINATE_GRACE_SECONDS = 0.5


def _shell_command(shell: str, command: str) -> list[str]:
    path = Path(shell)
    if not path.is_absolute():
        raise CairnError("invalid_command", "shell must be an absolute path")
    return [str(path), "-c", command]


def _exit_code(return_code: int) -> int:
    """Report a signalled child the way a shell does, so the status survives the hop."""
    if return_code < 0:
        return 128 - return_code
    return return_code if 0 < return_code < 256 else EXIT_FAILED


def _positive_bound(name: str, value: float) -> None:
    if not math.isfinite(value) or value <= 0:
        raise CairnError("invalid_wait", f"{name} must be a positive, finite number")


def run_exec(
    command: str,
    working_directory: Path,
    shell: str,
    *,
    popen_factory: PopenFactory = subprocess.Popen,
) -> CommandResult:
    process: Child | None = None
    try:
        process = launch(
            popen_factory,
            _shell_command(shell, command),
            cwd=working_directory,
            stdin=subprocess.DEVNULL,
            stdout=None,
            stderr=None,
            text=True,
        )
        return_code = process.wait()
    except (KeyboardInterrupt, Cancelled):
        if process is not None:
            stop_child(process)
        raise
    detail = {"command": command, "process_exit": return_code, "shell": shell}
    if return_code == 0:
        return CommandResult(
            EXIT_OK, "done", "command completed", [], False, None, detail
        )
    return CommandResult(
        _exit_code(return_code),
        "failed",
        f"command exited {return_code}",
        [],
        False,
        "command_failed",
        detail,
    )


def stop_child(process: Child) -> None:
    """Stop one child without moving it outside the engine-owned process group."""
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def run_wait_duration(
    duration_seconds: float,
    bound_seconds: float,
    *,
    sleeper: Callable[[float], None] = time.sleep,
) -> CommandResult:
    _positive_bound("duration", duration_seconds)
    _positive_bound("timeout", bound_seconds)
    if duration_seconds > bound_seconds:
        raise CairnError(
            "invalid_wait", "duration must be no greater than timeout"
        )
    sleeper(duration_seconds)
    return CommandResult(
        EXIT_OK,
        "done",
        f"waited {duration_seconds:g} seconds",
        [],
        False,
        None,
        {
            "form": "duration",
            "duration_seconds": duration_seconds,
            "bound_seconds": bound_seconds,
        },
    )


def run_wait_until(
    command: str,
    working_directory: Path,
    shell: str,
    timeout_seconds: float,
    interval_seconds: float,
    *,
    popen_factory: PopenFactory = subprocess.Popen,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> CommandResult:
    _positive_bound("timeout", timeout_seconds)
    _positive_bound("interval", interval_seconds)
    deadline = monotonic() + timeout_seconds
    attempts = 0
    child: Child | None = None
    detail = {
        "form": "until",
        "timeout_seconds": timeout_seconds,
        "interval_seconds": interval_seconds,
        "shell": shell,
    }
    try:
        while True:
            attempts += 1
            child = None
            child = launch(
                popen_factory,
                _shell_command(shell, command),
                cwd=working_directory,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            remaining = max(0.0, deadline - monotonic())
            try:
                return_code = child.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                stop_child(child)
                stop_orphans()
                return CommandResult(
                    EXIT_FAILED,
                    "failed",
                    f"wait timed out after {timeout_seconds:g} seconds",
                    [],
                    False,
                    "wait_timeout",
                    {**detail, "attempts": attempts},
                )
            if return_code == 0:
                return CommandResult(
                    EXIT_OK,
                    "done",
                    f"condition succeeded after {attempts} attempt(s)",
                    [],
                    False,
                    None,
                    {**detail, "attempts": attempts},
                )
            stop_child(child)
            stop_orphans()
            remaining = deadline - monotonic()
            if remaining <= 0:
                return CommandResult(
                    EXIT_FAILED,
                    "failed",
                    f"wait timed out after {timeout_seconds:g} seconds",
                    [],
                    False,
                    "wait_timeout",
                    {**detail, "attempts": attempts},
                )
            sleeper(min(interval_seconds, remaining))
    except (KeyboardInterrupt, Cancelled):
        if child is not None:
            stop_child(child)
        raise
