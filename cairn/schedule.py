"""The scheduler, which is the one daemon that is dangerous, and what starting it entails.

There are two engine daemons and only one of them carries a hazard. The **view's** server
binds loopback, holds no run state, and reads the same files the CLI writes; killing it and
starting another loses nothing. The **scheduler** is different, and every recurring trigger
needs it — a cron schedule obviously, and an external webhook non-obviously, because a
webhook does not execute a run, it enqueues one, and the queue is drained by the scheduler.

While it is up it does two things unasked. It reconciles crashed runs, which Cairn wants.
And its retry scanner **re-executes every failed run recorded on the machine inside the
window `RETRY_SCANNER_HOURS` names** — including runs outside the directory it watches —
wherever a DAG-level retry policy is armed. For a tool whose failed runs are agent sessions
against git repositories that is unacceptable, so starting it asserts two machine-wide
properties rather than assuming them, and names what it found.

Both hazards live in one file every DAG on the machine inherits. Retry is closed there
([baseconfig.py]); catchup is closed on every file Cairn emits and only *asserted* there,
because the scanner reaches DAGs Cairn did not write:

- **DAG-level retry**, which replays a failed run ([baseconfig.py]).
- **Catchup**, which replays every cron slot missed while the machine slept. The engine
  writes `catchup_window: "6h"` into a base configuration it creates, and its comment reads "all
  missed cron intervals within this window are executed (max 1000)". Every emitted file
  states the empty window that turns it off ([workflow.md]), so this is a hazard only for
  the DAGs Cairn did not write — which is exactly what the scanner reaches.

The assertion runs **at start**, not at install. A machine that was safe when a schedule was
installed is not evidence about the machine a month later, and this is the only moment the
hazard can actually fire.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple, cast

from cairn import liveness
from cairn.baseconfig import (
    assert_catchup_disabled,
    assert_dag_retry_disabled,
    base_config_path,
)
from cairn.core import CairnError
from cairn.enginehome import (
    ENGINE_BINARY,
    dags_directory,
    run_records_path,
    scheduler_pause_path,
    scheduler_registry_directory,
)
from cairn.plan.ids import is_plan_slug
from cairn.record import engine
from cairn.supervise import find_status_files, last_record
from cairn.workflow.gate import Admission, admit
from cairn.workflow.pin import package_root
from cairn.workflow.schema import WORKFLOW_SUFFIX
from cairn.workflow.stamp import workflow_path

# The window the engine's own retry scanner sweeps. A refusal names the runs inside it, so
# the number is stated here rather than described.
RETRY_SCANNER_HOURS = 24
ADMITTED_DIRECTORY = "scheduled"


class EngineRun(NamedTuple):
    """One run the engine recorded, named the way a refusal has to name it."""

    run_id: str
    dag: str
    status: str
    finished_at: str | None


def _runs(root: Path) -> list[EngineRun]:
    """Every run the engine has recorded, or none on a machine that has never run one.

    An absent history is a refusal for `supervise reconcile`, which was asked to repair
    something; here it is the honest answer to "what would a scheduler re-execute", and
    refusing would make a fresh machine the one place the safety check cannot run.
    """
    if not root.exists():
        return []
    found: list[EngineRun] = []
    for path in find_status_files(root):
        try:
            record = last_record(path)
        except CairnError:
            continue
        if record is None:
            continue
        run_id = engine.text(record.get("dagRunId"))
        raw: Any = record.get("status")
        if run_id is None or not isinstance(raw, int):
            continue
        try:
            status = engine.run_status_name(raw)
        except CairnError:
            # A status this engine version does not map is still a run, and a refusal that
            # dropped it would under-report exactly the thing it exists to count.
            status = str(raw)
        found.append(
            EngineRun(
                run_id=run_id,
                dag=engine.text(record.get("name")) or "?",
                status=status,
                finished_at=engine.moment(record.get("finishedAt")),
            )
        )
    return found


def failed_runs_since(
    root: Path, *, hours: int = RETRY_SCANNER_HOURS, now: float | None = None
) -> list[EngineRun]:
    """Every failed run the retry scanner would reach.

    A run whose finish time cannot be read counts as inside the window rather than outside
    it. The scanner's own reading is what decides, and under-naming a run that is about to
    be re-executed is the one error this list must not make.
    """
    moment = time.time() if now is None else now
    cutoff = moment - hours * 3600
    inside: list[EngineRun] = []
    for run in _runs(root):
        if run.status != engine.RUN_FAILED:
            continue
        finished = _seconds(run.finished_at)
        if finished is None or finished >= cutoff:
            inside.append(run)
    # Unknown-finish runs first, so the elision can never drop the very runs this refuses
    # to under-name; the rest newest first, because those are the ones a person triggering
    # now will recognise.
    unknown = [run for run in inside if run.finished_at is None]
    known = sorted(
        (run for run in inside if run.finished_at is not None),
        key=lambda run: run.finished_at or "",
        reverse=True,
    )
    return [*unknown, *known]


def queued_runs(root: Path) -> list[EngineRun]:
    """Every run sitting enqueued with nothing draining it.

    This is what an external trigger produces when no scheduler is up: the webhook returned
    success, the run exists, and it will sit there indefinitely. It is the only way a person
    can see a trigger that was accepted and does nothing.
    """
    return [run for run in _runs(root) if run.status == engine.RUN_QUEUED]


def _seconds(moment: str | None) -> float | None:
    if not moment:
        return None
    try:
        return datetime.fromisoformat(moment).timestamp()
    except (ValueError, OSError, OverflowError):
        return None


NAMED_LIMIT = 20


def _named(runs: list[EngineRun]) -> str:
    """The runs a refusal spells out, saying so when it stops short of all of them."""
    shown = "; ".join(describe_run(run) for run in runs[:NAMED_LIMIT])
    if len(runs) <= NAMED_LIMIT:
        return shown
    return f"{shown}; and {len(runs) - NAMED_LIMIT} more"


def describe_run(run: EngineRun) -> str:
    """One line naming a run, used verbatim in a refusal."""
    when = f" finished {run.finished_at}" if run.finished_at else ""
    return f"{run.run_id} of {run.dag}{when}"


def assert_safe_to_start(
    *,
    base_config: Path | None = None,
    records: Path | None = None,
    now: float | None = None,
) -> list[EngineRun]:
    """Refuse to start a scheduler on a machine it would do damage from, naming the damage.

    Returns the queued runs it found, which is what the caller reports as the work this
    scheduler is about to drain.
    """
    config = base_config_path() if base_config is None else base_config
    # Judged before the records root is resolved, because resolving it asks the engine — and
    # invoking the engine creates this very file with its catch-up window armed
    # ([enginehome.py]). Reading where things are must not arm the hazard the reader is
    # about to judge.
    assert_catchup_disabled(config)
    try:
        assert_dag_retry_disabled(config)
    except CairnError as exc:
        would = failed_runs_since(run_records_path() if records is None else records, now=now)
        named = _named(would)
        # The holder's own message already names the file and the command that fixes it, so
        # what is added here is the only thing it cannot know: what starting now would do.
        raise CairnError(
            exc.cause,
            f"{exc}. Starting a scheduler now would re-execute {len(would)} failed run(s) "
            f"recorded in the last {RETRY_SCANNER_HOURS} hours"
            + (f": {named}." if named else "."),
            detail={**exc.detail, "would_re_execute": [run.run_id for run in would]},
        ) from exc
    return queued_runs(run_records_path() if records is None else records)


# What a reader is told about the one daemon a recurring trigger needs. Six words, because
# "is there a scheduler" has six honest answers and the old reading had one: it listed the
# definitions and the queue and said nothing at all about whether anything was alive, so a
# queued run was reported as draining into nothing while a scheduler was running ([29 C]).
SCHEDULER_RUNNING = "running"
# A live process the engine's API has been told to hold. A distinct state rather than a
# dead one: the process is there, it is firing nothing, and starting a second would not help.
SCHEDULER_PAUSED = "paused"
SCHEDULER_STOPPED = "stopped"
# The engine's registry names a process that is now somebody else's — an identifier the
# kernel handed out again after the scheduler died without clearing its record.
SCHEDULER_STALE = "stale_identity"
# The probe itself was refused. Cairn is ordinarily driven from inside a harness whose shell
# may not inspect processes, and "I could not look" is never "it is not there" ([liveness.py]).
SCHEDULER_INACCESSIBLE = "inaccessible"
# The engine has no record of a scheduler at all, which does not prove none is running: one
# started against another `DAGU_HOME` keeps its registry somewhere this reader is not looking.
SCHEDULER_UNKNOWN = "unknown"
SCHEDULER_STATES: tuple[str, ...] = (
    SCHEDULER_RUNNING,
    SCHEDULER_PAUSED,
    SCHEDULER_STOPPED,
    SCHEDULER_STALE,
    SCHEDULER_INACCESSIBLE,
    SCHEDULER_UNKNOWN,
)

SENTENCE_BY_SCHEDULER_STATE: dict[str, str] = {
    SCHEDULER_RUNNING: "a scheduler process is running: cron schedules fire and the queue drains",
    SCHEDULER_PAUSED: (
        "a scheduler process is running and the engine's own pause is set, so it fires "
        "nothing until it is resumed through the engine's API; starting a second scheduler "
        "would not change that"
    ),
    SCHEDULER_STOPPED: (
        "no scheduler is running: every process the engine recorded has ended, so a cron "
        "schedule fires never and a queued run waits indefinitely"
    ),
    SCHEDULER_STALE: (
        "no scheduler is running: the engine's record names a process identifier that now "
        "belongs to something else, which is what a scheduler killed outright leaves behind"
    ),
    SCHEDULER_INACCESSIBLE: (
        "whether a scheduler is running could not be established from this shell, which may "
        "not inspect processes — so this says nothing either way"
    ),
    SCHEDULER_UNKNOWN: (
        "the engine holds no record of a scheduler on this machine. That is what a machine "
        "with none looks like, and also what one looks like whose scheduler keeps its state "
        "under a different DAGU_HOME"
    ),
}

# Only this state is evidence that nothing will drain a queue. Every other one either proves
# a drainer or proves nothing, and a report that said "nothing is draining the queue" on the
# strength of not having looked is the defect this exists to close.
DRAINS_NOTHING: tuple[str, ...] = (SCHEDULER_STOPPED, SCHEDULER_STALE)


class SchedulerProcess(NamedTuple):
    """One process the engine's registry claims is a scheduler, and what it really is."""

    identity: str
    pid: int
    started_at: str | None
    reading: str


class SchedulerStatus(NamedTuple):
    """Whether a scheduler exists, read from the engine's record and Cairn's own probe.

    `paused_since` is set wherever the engine's pause flag is on, whatever the state: a
    paused flag over a stopped scheduler is a standing instruction the next one will obey,
    and a person starting one has to know it is there.
    """

    state: str
    processes: list[SchedulerProcess]
    paused_since: str | None

    @property
    def drains(self) -> bool:
        return self.state == SCHEDULER_RUNNING

    @property
    def sentence(self) -> str:
        return SENTENCE_BY_SCHEDULER_STATE[self.state]


def _paused_since(path: Path) -> str | None:
    """The moment the engine's global pause was set, where it is set at all."""
    try:
        document: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(document, dict) or not cast(dict[str, Any], document).get("paused"):
        return None
    return engine.moment(cast(dict[str, Any], document).get("pausedAt")) or "an unrecorded time"


def _registered_schedulers(directory: Path) -> list[SchedulerProcess] | None:
    """Every process the engine's registry claims, adjudicated by Cairn's own probe.

    None where the registry itself could not be read, which is a reader that could not look
    rather than a machine with no scheduler. A registry entry is a *claim*: measured against
    Dagu 2.18.2, killing a scheduler outright leaves its file in place, so the file says
    which process to ask about and nothing more ([liveness.py]).
    """
    try:
        # Listed rather than globbed: `Path.glob` swallows the refusal and answers with an
        # empty directory, which would report a registry this shell may not read as a
        # machine that has never registered a scheduler.
        names = sorted(os.listdir(directory))
    except FileNotFoundError:
        # The engine has registered no scheduler here, which is a different answer from
        # not being able to look.
        return []
    except OSError:
        return None
    entries = [directory / name for name in names if name.endswith(".json")]
    found: list[SchedulerProcess] = []
    for path in entries:
        try:
            document: Any = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # A half-written or unreadable entry is a claim Cairn cannot adjudicate, which
            # is the inaccessible reading rather than a scheduler that is not there.
            found.append(
                SchedulerProcess(path.stem, 0, None, liveness.PROCESS_UNREADABLE)
            )
            continue
        if not isinstance(document, dict):
            found.append(
                SchedulerProcess(path.stem, 0, None, liveness.PROCESS_UNREADABLE)
            )
            continue
        claim = cast(dict[str, Any], document)
        pid = claim.get("pid")
        started = engine.moment(claim.get("startedAt"))
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            found.append(
                SchedulerProcess(
                    engine.text(claim.get("id")) or path.stem,
                    0,
                    started,
                    liveness.PROCESS_UNREADABLE,
                )
            )
            continue
        found.append(
            SchedulerProcess(
                identity=engine.text(claim.get("id")) or path.stem,
                pid=pid,
                started_at=started,
                reading=liveness.process_reading(pid, _seconds(started)),
            )
        )
    return found


def scheduler_status(
    *, registry: Path | None = None, pause: Path | None = None
) -> SchedulerStatus:
    """Whether this machine has a scheduler, saying only what was measured.

    The engine's own registry says which processes to ask about and Cairn's process check
    decides what they are, because the registry outlives the process it names. Nothing here
    asks the engine's API: Cairn holds no credential for it by construction, and a question
    that could only be answered through it could not be answered at all.
    """
    directory = scheduler_registry_directory() if registry is None else registry
    paused_since = _paused_since(scheduler_pause_path() if pause is None else pause)
    processes = _registered_schedulers(directory)
    if processes is None:
        return SchedulerStatus(SCHEDULER_INACCESSIBLE, [], paused_since)
    readings = {process.reading for process in processes}
    if not processes:
        return SchedulerStatus(SCHEDULER_UNKNOWN, processes, paused_since)
    if liveness.PROCESS_LIVE in readings:
        state = SCHEDULER_PAUSED if paused_since is not None else SCHEDULER_RUNNING
    elif liveness.PROCESS_UNREADABLE in readings:
        state = SCHEDULER_INACCESSIBLE
    elif liveness.PROCESS_RECYCLED in readings:
        state = SCHEDULER_STALE
    else:
        state = SCHEDULER_STOPPED
    return SchedulerStatus(state, processes, paused_since)


def published_path(plan: str, *, dags: Path | None = None) -> Path:
    """Where a plan's definition has to be reachable from for a schedule to fire at all.

    The scheduler watches one directory and Cairn writes into the repository's own admin
    directory, so a file carrying a schedule that never reached here fires never, silently.
    """
    root = dags_directory() if dags is None else dags
    # The plan reaches here from a command line and becomes a filename in a directory
    # shared with every other job on the machine, so it is held to the grammar a plan slug
    # already has — which is a single lower-case segment, and cannot spell a separator or a
    # parent directory. The engine's node-name rules are not that grammar: they forbid the
    # hyphen every real plan slug carries.
    if not is_plan_slug(plan):
        raise CairnError(
            "invalid_arguments",
            f"{plan!r} is not a plan slug, so it cannot name a file in the watched "
            "directory",
            detail={"plan": plan},
        )
    return root / f"{plan}{WORKFLOW_SUFFIX}"


def _scheduled_snapshot(
    repository: Path, plan: str, admission: Admission
) -> Path:
    """Persist admitted bytes at an immutable path the scheduler may safely follow."""
    root = workflow_path(repository, plan).parent.parent / ADMITTED_DIRECTORY
    target = root / plan / admission.sha256 / f"{plan}{WORKFLOW_SUFFIX}"
    if target.exists():
        if hashlib.sha256(target.read_bytes()).hexdigest() != admission.sha256:
            raise CairnError(
                "invalid_arguments",
                f"{target} does not hold the admitted bytes its path names",
            )
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(admission.body)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o400)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    if hashlib.sha256(target.read_bytes()).hexdigest() != admission.sha256:
        raise CairnError(
            "invalid_arguments",
            f"{target} did not preserve the workflow bytes admission approved",
        )
    return target


def install(
    repository: Path,
    plan: str,
    *,
    dags: Path | None = None,
    admission: Admission | None = None,
) -> Path:
    """Make a gated immutable snapshot reachable by the scheduler.

    The watched link never points at the editable authored file. It points at a
    content-addressed snapshot of bytes admitted by preflight and both engine checks, so a
    later edit cannot change what the daemon fires. The engine's name for the DAG is this
    link's filename, which is what the view's URL and any webhook endpoint are keyed on.

    A name already taken by something that is not this plan's own link is refused rather
    than replaced: two repositories whose plans share a slug would otherwise fork one DAG
    history between them, and the second install would silently retarget the first.
    """
    source = workflow_path(repository, plan)
    if not source.exists():
        raise CairnError(
            "invalid_arguments",
            f"{source} does not exist, so there is no definition to schedule; author the "
            "plan first",
            detail={"plan": plan, "workflow": str(source)},
        )
    admitted = admission
    if admitted is None:
        admitted, faults = admit(source, expected_plan=plan)
        if admitted is None:
            raise CairnError(
                "workflow_not_admitted",
                f"{source} did not pass the execution gate: "
                + "; ".join(str(fault) for fault in faults),
            )
    snapshot = _scheduled_snapshot(repository, plan, admitted)
    target = published_path(plan, dags=dags)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() and target.readlink() == snapshot:
        return target
    admitted_root = workflow_path(repository, plan).parent.parent / ADMITTED_DIRECTORY
    if target.is_symlink():
        try:
            current = target.readlink().resolve()
        except OSError:
            current = Path()
        if admitted_root.resolve() in current.parents:
            replacement = target.with_name(f".{target.name}.{admitted.sha256[:12]}.tmp")
            replacement.unlink(missing_ok=True)
            replacement.symlink_to(snapshot)
            os.replace(replacement, target)
            return target
    if target.exists() or target.is_symlink():
        raise CairnError(
            "invalid_arguments",
            f"{target} already exists and does not point at {source}. Two plans sharing a "
            "slug would fork one DAG history between them; rename one, or remove that "
            "entry first",
            detail={"plan": plan, "published": str(target)},
        )
    target.symlink_to(snapshot)
    return target


def remove(repository: Path, plan: str, *, dags: Path | None = None) -> Path | None:
    """Stop a plan firing on its schedule, leaving the definition itself alone.

    Only this plan's own link is removed. The watched directory is the machine's, holding
    whatever else a person keeps there, and `install` already refuses to write over an entry
    it did not make — a remove that deleted one would destroy the file that refusal exists to
    protect, on the command the refusal tells a person to run.
    """
    target = published_path(plan, dags=dags)
    if not target.is_symlink():
        if target.exists():
            raise CairnError(
                "invalid_arguments",
                f"{target} is not a link Cairn installed, so removing it would delete a "
                "definition Cairn did not publish",
                detail={"plan": plan, "published": str(target)},
            )
        return None
    admitted_root = workflow_path(repository, plan).parent.parent / ADMITTED_DIRECTORY
    linked = target.readlink().resolve()
    if admitted_root.resolve() not in linked.parents:
        raise CairnError(
            "invalid_arguments",
            f"{target} points at {target.readlink()}, not at an admitted snapshot for "
            f"{workflow_path(repository, plan)}; it belongs to "
            "another repository's plan of the same name",
            detail={"plan": plan, "published": str(target)},
        )
    target.unlink()
    return target


def installed(*, dags: Path | None = None) -> list[Path]:
    """Every definition reachable from the watched directory, whoever put it there."""
    root = dags_directory() if dags is None else dags
    if not root.is_dir():
        return []
    return sorted(path for path in root.glob(f"*{WORKFLOW_SUFFIX}"))


def scheduler_command(*, dags: Path | None = None) -> list[str]:
    """The engine invocation this command execs, stated so a person can run it themselves."""
    root = dags_directory() if dags is None else dags
    return [ENGINE_BINARY, "scheduler", "--dags", str(root)]


def start(*, dags: Path | None = None) -> None:
    """Become the scheduler, having asserted the machine is safe to be one on.

    `execvp` rather than a child, so the daemon inherits this process rather than gaining a
    supervisor Cairn would then have to keep alive — a signal sent to it reaches the engine,
    and Cairn leaves no process tree behind.

    A foreground process is not an operational story on its own: it dies with the terminal
    that started it. The machine's own service manager is what keeps it alive across a
    logout and a reboot, and `service_plan` states the unit for it ([29 C]).
    """
    command = scheduler_command(dags=dags)
    os.execvp(command[0], command)


# The label and filename the unit is installed under, on both platforms, so the status and
# stop commands name the same service the install created.
SERVICE_LABEL = "dev.cairn.scheduler"
SERVICE_LAUNCHD = "launchd"
SERVICE_SYSTEMD = "systemd"


class ServicePlan(NamedTuple):
    """One machine's supported way to keep a scheduler alive, as text a person can run.

    Every value is read from this process rather than described: the interpreter running
    now, the Cairn being imported now, and the directory this engine watches. A unit naming
    a bare `python3` or a guessed checkout is a unit that starts something else — or
    nothing, with the failure in a log nobody reads.
    """

    manager: str
    unit_path: Path
    unit: str
    install: list[str]
    status: list[str]
    stop: list[str]


def _program(dags: Path) -> list[str]:
    """What a unit runs. Cairn's own command, never the engine's, and for one reason: the
    hazard assertion is Cairn's and a unit that ran `dagu scheduler` would skip it — arming,
    on every boot, the retry scanner that re-executes agent sessions ([assert_safe_to_start]).
    """
    return [
        sys.executable,
        "-m",
        "cairn",
        "schedule",
        "start",
        "--dags",
        str(dags),
    ]


def _launchd_plist(program: list[str], log: Path) -> str:
    arguments = "\n".join(f"    <string>{value}</string>" for value in program)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{SERVICE_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
{arguments}
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PYTHONPATH</key><string>{package_root()}</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
</dict>
</plist>
"""


def _systemd_unit(program: list[str], dags: Path) -> str:
    return f"""[Unit]
Description=Cairn's Dagu scheduler, watching {dags}

[Service]
Type=simple
Environment=PYTHONPATH={package_root()}
ExecStart={" ".join(program)}
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
"""


def service_plan(*, dags: Path | None = None, home: Path | None = None) -> ServicePlan:
    """How this machine keeps a scheduler running, and the three commands that drive it.

    A user-level service on both platforms, never a system one. The scheduler runs agent
    sessions against the repositories of the person who installed it and authenticates as
    that person's own agent installation, so a root service would be running their work as
    somebody else ([README security posture]).

    A refusal still refuses under a service manager: `schedule start` asserts the machine is
    safe before it becomes the scheduler, so an unsafe machine produces a service that exits
    and is restarted, with the refusal in the log the unit names. That is the intended
    behaviour — the remedy is the machine, which the refusal names, and not the unit.
    """
    root = Path.home() if home is None else home
    watched = dags_directory() if dags is None else dags
    program = _program(watched)
    if sys.platform == "darwin":
        log = root / "Library" / "Logs" / f"{SERVICE_LABEL}.log"
        target = f"gui/{os.getuid()}"
        return ServicePlan(
            manager=SERVICE_LAUNCHD,
            unit_path=root / "Library" / "LaunchAgents" / f"{SERVICE_LABEL}.plist",
            unit=_launchd_plist(program, log),
            install=[
                "launchctl",
                "bootstrap",
                target,
                str(root / "Library" / "LaunchAgents" / f"{SERVICE_LABEL}.plist"),
            ],
            status=["launchctl", "print", f"{target}/{SERVICE_LABEL}"],
            stop=["launchctl", "bootout", f"{target}/{SERVICE_LABEL}"],
        )
    return ServicePlan(
        manager=SERVICE_SYSTEMD,
        unit_path=root / ".config" / "systemd" / "user" / f"{SERVICE_LABEL}.service",
        unit=_systemd_unit(program, watched),
        install=["systemctl", "--user", "enable", "--now", f"{SERVICE_LABEL}.service"],
        status=["systemctl", "--user", "status", f"{SERVICE_LABEL}.service"],
        stop=["systemctl", "--user", "disable", "--now", f"{SERVICE_LABEL}.service"],
    )


__all__ = [
    "DRAINS_NOTHING",
    "RETRY_SCANNER_HOURS",
    "SCHEDULER_INACCESSIBLE",
    "SCHEDULER_PAUSED",
    "SCHEDULER_RUNNING",
    "SCHEDULER_STALE",
    "SCHEDULER_STATES",
    "SCHEDULER_STOPPED",
    "SCHEDULER_UNKNOWN",
    "SENTENCE_BY_SCHEDULER_STATE",
    "SERVICE_LABEL",
    "SERVICE_LAUNCHD",
    "SERVICE_SYSTEMD",
    "EngineRun",
    "SchedulerProcess",
    "SchedulerStatus",
    "ServicePlan",
    "assert_safe_to_start",
    "describe_run",
    "failed_runs_since",
    "install",
    "installed",
    "published_path",
    "queued_runs",
    "remove",
    "scheduler_command",
    "scheduler_status",
    "service_plan",
    "start",
]
