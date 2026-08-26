"""Runtime identity, result vocabulary, and atomic step-report persistence."""

from __future__ import annotations

import json
import os
import signal
import tempfile
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import Any, NamedTuple, Protocol, TypeAlias, cast

from cairn.layout import RUNS_ROOT_ENV, reports_directory

EXIT_OK = 0
EXIT_FAILED = 1
# A rate limit is the one failure an agent step can distinguish from outside, and the only
# one worth retrying, so it leaves on its own exit status. The emitted step's retry policy
# names this code and no other, which is how a deliberate bounded retry is expressed to an
# engine that cannot see a cause (09). 75 is the conventional "temporary failure".
EXIT_RATE_LIMITED = 75
STATUSES = ("done", "noop", "failed")

# One key of a report's `detail`, named here because it crosses a seam: the provider writes
# it and the verify gate reads it, and a literal spelled twice would drift with nothing
# failing — the gate would silently fall back to its broader sentence for ever.
ENDED_WITHOUT_REPORTING = "ended_without_reporting"


class CommandResult(NamedTuple):
    """What one subcommand decided, before it becomes a report and an exit status."""

    exit_code: int
    status: str
    summary: str
    follow_up_work: list[str]
    needs_user_decision: bool
    cause: str | None
    detail: dict[str, Any]


class Child(Protocol):
    """The slice of a running child process every subcommand actually touches."""

    returncode: int | None
    stdin: Any
    stdout: Any

    def wait(self, timeout: float | None = ...) -> int: ...
    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...


PopenFactory: TypeAlias = Callable[..., Child]


class Cancelled(Exception):
    """Raised inside the running subcommand when the step is asked to stop."""


def _raise_cancelled(_signum: int, _frame: FrameType | None) -> None:
    raise Cancelled


@contextmanager
def _sigterm(
    handler: Callable[[int, FrameType | None], None] | int,
) -> Generator[None]:
    previous = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, handler)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def cancel_on_termination() -> Any:
    """Turn a step-directed SIGTERM into an exception the subcommand can unwind from.

    Every subcommand runs inside this, so a cancelled step stops its own children and
    records a cause instead of dying silently mid-work.
    """
    return _sigterm(_raise_cancelled)


def survive_termination() -> Any:
    """Hold off a stop signal for the moment a report is being persisted.

    A step killed while unwinding is the case the report matters most for, so the write
    is the one stretch that must not itself be interruptible.
    """
    return _sigterm(signal.SIG_IGN)


def stop_orphans() -> None:
    """Signal descendants a direct child left behind, without leaving the engine's group.

    Reached only after Dagu's identity resolved, and only when this process leads its own
    group — which the engine guarantees for a step, and which bounds the group to this
    process and its descendants. SIGTERM is where it stops: escalating to SIGKILL across
    the group would kill this process before it writes its report, so a descendant that
    ignores SIGTERM stays for the engine's own reaping.
    """
    if os.getpgrp() != os.getpid():
        return
    with _sigterm(signal.SIG_IGN):
        try:
            os.killpg(os.getpgrp(), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass


class CairnError(Exception):
    """A terminal Cairn error with a stable report cause."""

    def __init__(
        self,
        cause: str,
        message: str,
        *,
        exit_code: int = EXIT_FAILED,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.cause = cause
        self.exit_code = exit_code
        self.detail = {} if detail is None else detail


def launch(
    popen_factory: PopenFactory, command: Sequence[str], **options: Any
) -> Child:
    """Start a child, turning the one failure only the launch can produce into a cause."""
    try:
        return popen_factory(list(command), **options)
    except OSError as exc:
        raise CairnError(
            "process_launch_failed",
            str(exc),
            detail={"errno": exc.errno, "command": list(command)},
        ) from exc


@dataclass(frozen=True)
class RuntimeContext:
    """Identity Dagu injects into one running step.

    `working_directory` is the process's own, because that is where the engine puts a step
    it was given a `working_dir` for [V]. `DAG_RUN_WORK_DIR` names something else entirely
    — a scratch directory under the run's data, where `git rev-parse` reports no repository
    — so it is required as proof the step was engine-launched and never used as a path.
    """

    run_id: str
    step_id: str
    working_directory: Path
    report_path: Path
    # Carried whole rather than reconstructed from `report_path`'s ancestors: the run's own
    # occasion lives beside its reports rather than under them ([marker.py]), and two
    # derivations of one root are how they come to disagree.
    runs_root: Path

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> RuntimeContext:
        """Identify this step from what the engine injected and where it put us.

        The step's directory is the process's own, because that is the only place the
        engine's `working_dir:` lands.
        """
        values = os.environ if env is None else env
        # The step's own log paths are deliberately not required. Nothing reads them, and
        # the engine sets no per-step stdout or stderr path for a lifecycle handler —
        # which is where the run's release has to run if a failed run is to give its
        # repository back.
        required = (
            "DAG_RUN_ID",
            "DAG_RUN_STEP_NAME",
            "DAG_RUN_WORK_DIR",
            RUNS_ROOT_ENV,
        )
        missing = [name for name in required if not values.get(name)]
        if missing:
            raise CairnError(
                "missing_runtime_identity",
                "missing required Dagu environment: " + ", ".join(missing),
            )
        run_id = values["DAG_RUN_ID"]
        step_id = values["DAG_RUN_STEP_NAME"]
        try:
            reports = reports_directory(Path(values[RUNS_ROOT_ENV]), run_id)
        except ValueError as exc:
            raise CairnError("invalid_run_id", str(exc), detail={"run_id": run_id}) from exc
        return cls(
            run_id=run_id,
            step_id=step_id,
            working_directory=Path.cwd().resolve(),
            report_path=reports / f"{step_id}.json",
            runs_root=Path(values[RUNS_ROOT_ENV]),
        )


def write_text(path: Path, text: str) -> None:
    """Write one file so a reader sees either the old one or the whole new one.

    Anything Cairn writes may be read while it is being written — a step's report by the
    gate that decides its fate, a graph by the conversation rewriting it one answer at a
    time, a rendered report by whoever opened it — and none of them may ever be observed
    half-written, because a reader cannot tell a truncated document from a short one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def write_json(path: Path, payload: dict[str, Any]) -> None:
    """One JSON document, replaced in a single step."""
    write_text(
        path, json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    )


def write_report(
    context: RuntimeContext, result: CommandResult, duration_seconds: float
) -> dict[str, Any]:
    """Atomically write the shared report shape used by every subcommand."""
    if result.status not in STATUSES:
        raise CairnError("invalid_report", f"unknown report status {result.status!r}")
    report: dict[str, Any] = {
        "step_id": context.step_id,
        # The run this account belongs to. A report is read by the gate that decides
        # whether a step's work may be recorded, and one left by an earlier run would
        # otherwise green-light a step this run never started.
        "run_id": context.run_id,
        "status": result.status,
        "duration": duration_seconds,
        "working_directory": str(context.working_directory),
        "summary": result.summary,
        "follow_up_work": result.follow_up_work,
        "needs_user_decision": result.needs_user_decision,
        "cause": result.cause,
        "detail": result.detail,
    }
    write_json(context.report_path, report)
    return report


def read_step_report(directory: Path, step_id: str, run_id: str) -> dict[str, Any]:
    """The account a step of *this* run left of itself.

    A report from another run is refused rather than read. Reports outlive the run that
    wrote them, and the one question this answers — may this step's work be recorded —
    must never be answered by a step that ran yesterday.
    """
    path = directory / f"{step_id}.json"
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CairnError(
            "missing_report", f"step {step_id!r} left no report at {path}"
        ) from exc
    except (OSError, ValueError) as exc:
        raise CairnError("invalid_report", f"{path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise CairnError("invalid_report", f"{path}: expected an object")
    report = cast(dict[str, Any], raw)
    for name, expected in (("status", str), ("run_id", str), ("summary", str)):
        if not isinstance(report.get(name), expected):
            raise CairnError(
                "invalid_report", f"{path}: {name!r} is missing or not a string"
            )
    if not isinstance(report.get("needs_user_decision"), bool):
        raise CairnError("invalid_report", f"{path}: 'needs_user_decision' is not a boolean")
    # A reader that accepted an unknown status would hand it to a caller whose only
    # question is "did this fail", and every such caller reads anything that is not
    # `failed` as a green light.
    if report["status"] not in STATUSES:
        raise CairnError("invalid_report", f"{path}: unknown status {report['status']!r}")
    if report["run_id"] != run_id:
        raise CairnError(
            "missing_report",
            f"{path} was written by run {report['run_id']!r}, not {run_id!r}",
        )
    return report


def sweep_stale_reports(context: RuntimeContext) -> None:
    """Clear temp files a killed predecessor left beside the reports a router globs."""
    directory = context.report_path.parent
    if not directory.is_dir():
        return
    for stale in directory.glob(f".{context.report_path.name}.*.tmp"):
        try:
            stale.unlink()
        except OSError:
            pass


__all__ = [
    "ENDED_WITHOUT_REPORTING",
    "EXIT_FAILED",
    "EXIT_OK",
    "EXIT_RATE_LIMITED",
    "STATUSES",
    "CairnError",
    "Cancelled",
    "Child",
    "CommandResult",
    "PopenFactory",
    "RuntimeContext",
    "cancel_on_termination",
    "launch",
    "read_step_report",
    "stop_orphans",
    "survive_termination",
    "sweep_stale_reports",
    "write_json",
    "write_report",
    "write_text",
]
