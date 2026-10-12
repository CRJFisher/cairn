"""The one resume a run arms for itself when it stops at the subscription's allowance.

A person queues work and walks away. A run that stops at the allowance already records
everything a resume needs — the moment, the window, and the exact recovery command — so the
queue should start again when the allowance reopens rather than when the person next looks
([44], [principle 4](../PRINCIPLES.md)).

**It is not a scheduler.** The engine's own scheduler carries a retry scanner that
re-executes every failed run on the machine, Cairn's and not ([docs/triggers.md]), and
nothing here needs one running. What the run's release arms is one detached process, waiting
on one moment, for one run — started the way `run start` starts the engine, so it outlives
the session and the terminal that armed it.

**It measures; it never trusts the clock.** A window reported to reopen at T is measured
after T plus `RESET_SETTLE`, through the allowance's own instruments, and one still closed is
backed off along `RECHECK_BACKOFF` exactly as a hold inside a step is ([headroom.py]).

**It refuses where a person is needed, and records why.** A dirty tree, a repository another
run holds, a workflow re-authored since, a moment beyond the horizon: each ends the wait with
a sentence on the run and nothing started. A refusal never retries in a loop.

The recovery it starts is `cairn run start`, composed nowhere here: the run's own record
carries the recovery command a person would paste, and the waiter runs that very command, so
the automatic path and the manual one cannot differ.
"""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple, cast

from cairn.core import CairnError, PopenFactory, write_json
from cairn.headroom import (
    HELD,
    PROBE_TIMEOUT,
    RECHECK_BACKOFF,
    RESET_SETTLE,
    Gauge,
    Instruments,
    decide,
    default_instruments,
    local_moment,
    measure,
    window_phrase,
)
from cairn.layout import (
    NOTIFY_COMMAND_ENV,
    RUN_ID,
    check_run_id,
    headroom_directory,
    resume_log_path,
    resume_path,
)
from cairn.locks import (
    describe_holder,
    read_run_lock,
    refuse_dirty_repository,
    refuse_unresolved_merge,
)
from cairn.marker import mint_occasion
from cairn.providers import iso_moment
from cairn.record.extract import read_resume
from cairn.record.model import AllowanceAccount, PendingResume, RunRecord
from cairn.record.store import read_record
from cairn.record.vocabulary import (
    NEXT_AWAIT_ALLOWANCE,
    RESUME_REFUSED,
    RESUME_STARTED,
    RESUME_STARTING,
    RESUME_SUPERSEDED,
    RESUME_WAITING,
)
from cairn.skill.trigger import launch_detached
from cairn.workflow.schema import CAIRN_INVOCATION
from cairn.workflow.stamp import read_stamp, workflow_path

# How far ahead a resume will wait. One weekly window, which is the longest allowance a hold
# can name: a moment beyond it is a run nobody should expect to restart itself, and saying so
# is better than a process that sits for a fortnight.
RESUME_HORIZON = 7 * 24 * 60 * 60.0

# The shell the owner's notify command is run through, given the sentence on standard input.
# Unset by default and never required: without it the facts are in the view and the report
# only ([layout.NOTIFY_COMMAND_ENV]).
NOTIFY_SHELL = "/bin/sh"
# Long enough for a webhook or a desktop notification, short enough that a command which
# never returns cannot hold the resume it is announcing.
NOTIFY_TIMEOUT = 30.0

# What a measurement costs at most, and the room the waiter leaves for one. The probe is the
# allowance's own and is bounded there; this is the budget `measure` is given to fit it in.
MEASURE_BUDGET = PROBE_TIMEOUT + 60.0

# How long the recovery's own start may take before the waiter stops waiting on it. `run
# start` returns once the engine has registered the run rather than when the run ends, and
# its own bound on that is thirty seconds ([skill/trigger.TAKEN_ON_TIMEOUT]).
START_TIMEOUT = 300.0


class Armed(NamedTuple):
    """What the release armed, as the line it says and the waiter it started.

    `pid` is `None` where nothing was armed, which is every run that did not stop at the
    allowance — the ordinary case, and never a failure.
    """

    said: list[str]
    pid: int | None


def _epoch(moment: str | None) -> float | None:
    if not moment:
        return None
    try:
        return datetime.fromisoformat(moment).timestamp()
    except ValueError:
        return None


def held_step(record: RunRecord) -> str | None:
    """The step this run stopped at the allowance on, where that is what stopped it.

    Read off the record's own next action rather than by hunting for a cause: the derivation
    has already decided which step the fault is at, and a second search could name another
    ([record/extract.derive_next_action]).
    """
    if record["next_action"]["action"] != NEXT_AWAIT_ALLOWANCE:
        return None
    return record["next_action"]["subject"]


class Held(NamedTuple):
    """Everything a resume needs, read off the record of the run that stopped."""

    at: float
    moment: str
    window: str | None
    account: AllowanceAccount | None
    command: str


def held_at(record: RunRecord) -> Held | None:
    """The moment, the window, the account and the command, or None where any is missing.

    All four or nothing. A resume that could not spell the command completely, or that had
    no moment to wait for, would be a process waiting on something it cannot act on
    ([44 A]).
    """
    step_id = held_step(record)
    command = record["next_action"]["command"]
    if step_id is None or command is None:
        return None
    step = next((item for item in record["steps"] if item["step_id"] == step_id), None)
    headroom = None if step is None else step["headroom"]
    if headroom is None:
        return None
    at = _epoch(headroom["held_until"])
    if at is None:
        return None
    return Held(
        at=at,
        moment=cast(str, headroom["held_until"]),
        window=headroom["held_window"],
        account=headroom["account"],
        command=command,
    )


def waiter_command(run_id: str, repository: str) -> tuple[str, ...]:
    """The invocation the release detaches, spelled where a person can read it off the run."""
    return (
        *CAIRN_INVOCATION,
        "run",
        "resume-at",
        "--run",
        check_run_id(run_id),
        "--repository",
        repository,
    )


def notify(
    sentence: str,
    *,
    environment: Mapping[str, str] | None = None,
    runner: Callable[..., Any] = subprocess.run,
) -> bool:
    """Tell the owner one fact, through the command they configured, and never fail on it.

    Principle 2: the run stopping and the run resuming are the two facts a person away from
    the terminal needs, and a notification that could break either of them would be worse
    than none. So every fault here is swallowed — an unset command, a command that does not
    exist, one that exits nonzero or never returns.
    """
    values = os.environ if environment is None else environment
    command = (values.get(NOTIFY_COMMAND_ENV) or "").strip()
    if not command:
        return False
    try:
        runner(
            (NOTIFY_SHELL, "-c", command),
            input=sentence.encode("utf-8"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=NOTIFY_TIMEOUT,
            check=False,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return False
    return True


def notify_command(environment: Mapping[str, str] | None = None) -> str | None:
    """The command the owner configured, read where a workflow is generated.

    Read at authoring rather than at run time for the reason the usage endpoint's opt-in is:
    the engine hands a step a curated environment, so a value the owner set in their own
    shell reaches the release only if the generated file carries it ([layout.py]).
    """
    values = os.environ if environment is None else environment
    return (values.get(NOTIFY_COMMAND_ENV) or "").strip() or None


def write_resume(runs_root: Path, resume: PendingResume) -> Path:
    """Replace the run's pending resume atomically, so no reader sees half of one."""
    path = resume_path(runs_root, resume["run_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, cast(dict[str, Any], resume))
    return path


def clear_resume(runs_root: Path, run_id: str) -> bool:
    """Remove the pending resume, and say whether there was one to remove."""
    path = resume_path(runs_root, run_id)
    try:
        path.unlink()
    except OSError:
        return False
    return True


def arm(
    runs_root: Path,
    run_id: str,
    *,
    popen_factory: PopenFactory | None = None,
    environment: Mapping[str, str] | None = None,
    notifier: Callable[..., Any] = subprocess.run,
) -> Armed:
    """Start one waiter for a run that stopped at the allowance, and say what was armed.

    Called by the run's own release, which is the one body that runs however a run ends, and
    bounded by what that body may do: nothing here may reach the exit status, so every fault
    comes back as a line rather than as an exception ([__main__.record_the_run]).

    The record is read from disk rather than passed in, because the release has just written
    it and a second derivation here could decide a different next action from the one the run
    was recorded with.

    The owner is told here rather than by the waiter, because this is the moment the run
    stopped — the first of the two facts principle 2 names.
    """
    try:
        record = read_record(runs_root, run_id)
    except CairnError as unreadable:
        return Armed([f"no resume: {unreadable}"], None)
    if record is None:
        return Armed([], None)
    held = held_at(record)
    if held is None:
        return Armed([], None)
    repository = record["git"]["repository"]
    if repository is None:
        return Armed(["no resume: the record names no repository to run the recovery in"], None)
    try:
        child = launch_detached(
            waiter_command(run_id, repository),
            resume_log_path(runs_root, run_id),
            subprocess.Popen if popen_factory is None else popen_factory,
        )
    except (OSError, ValueError) as unlaunched:
        return Armed([f"no resume: the waiter could not be started: {unlaunched}"], None)
    notify(
        f"Cairn stopped run {run_id} at {window_phrase(held.window)}. It resumes itself "
        f"after {local_moment(held.at)}.",
        environment=environment,
        runner=notifier,
    )
    return Armed(
        [f"resuming {run_id} after {local_moment(held.at)}, pid {child.pid}"], child.pid
    )


def _pending(
    record: RunRecord,
    held: Held,
    *,
    state: str,
    now: float,
    reason: str | None = None,
    started_run: str | None = None,
    command: str | None = None,
) -> PendingResume:
    return PendingResume(
        run_id=record["run_id"],
        state=state,
        at=held.moment,
        window=held.window,
        account=held.account,
        command=held.command if command is None else command,
        pid=os.getpid(),
        reason=reason,
        started_run=started_run,
        written_at=iso_moment(now),
    )


def _refusal(repository: Path, record: RunRecord) -> str | None:
    """Why a person is needed before this recovery may start, or None where none is.

    Each of these is a state a run start would meet anyway, asked here so the refusal lands
    on the run that was waiting rather than in a log nobody opens ([44 B]).
    """
    try:
        refuse_unresolved_merge(repository)
        refuse_dirty_repository(repository)
    except CairnError as unsettled:
        return (
            f"the resume needs a clean tree and {unsettled}. Settle it and run the recovery"
        )
    held = read_run_lock(repository)
    if held is not None:
        return (
            f"{repository} is held by {describe_holder(held[0])}, so the recovery would be "
            "refused; run it once that run has finished"
        )
    plan = record["plan"]
    if plan is None:
        return "the record names no plan, so the workflow it ran cannot be identified"
    recorded = record["graph_sha256"]
    if recorded is None:
        return (
            "this run recorded no graph digest, so it cannot be established that the "
            f"workflow now published for {plan!r} holds the steps it ran; start a fresh run"
        )
    stamp = read_stamp(workflow_path(repository, plan))
    published = None if stamp is None else stamp["graph_sha256"]
    if published != recorded:
        return (
            f"the workflow published for {plan!r} was re-authored from a different graph "
            "since this run, so a recovery would continue this occasion with other steps; "
            "start a fresh run of the re-authored plan"
        )
    return None


class Waiter:
    """One run's wait on one window, from the moment it was armed to what it came to.

    Every seam a test drives is an argument: the clock, the sleep, the allowance's
    instruments and the process factory. A hold is hours long by design, so a wait that slept
    would be measuring the machine rather than the decision.
    """

    def __init__(
        self,
        run_id: str,
        *,
        repository: Path,
        runs_root: Path,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        instruments: Instruments | None = None,
        gauge: Gauge | None = None,
        runner: Callable[..., Any] = subprocess.run,
        environment: Mapping[str, str] | None = None,
        mint: Callable[[], str] | None = None,
    ) -> None:
        self.run_id = check_run_id(run_id)
        self.repository = repository
        self.runs_root = runs_root
        self.clock = clock
        self.sleep = sleep
        self.instruments = default_instruments() if instruments is None else instruments
        self.gauge = (
            Gauge(headroom_directory(runs_root), clock=clock) if gauge is None else gauge
        )
        self.runner = runner
        self.environment = environment
        self.mint = mint

    def run(self) -> PendingResume | None:
        """Wait out the window and start the recovery, or record why neither happened.

        None where there is nothing to resume at all — a run that did not stop at the
        allowance, or one whose record is gone. Nothing is written for those: a file saying
        a resume was refused belongs only to a run that armed one.
        """
        try:
            record = read_record(self.runs_root, self.run_id)
        except CairnError:
            return None
        if record is None:
            return None
        held = held_at(record)
        if held is None:
            return None
        if held.at - self.clock() > RESUME_HORIZON:
            return self._write(
                record,
                held,
                state=RESUME_REFUSED,
                reason=(
                    f"{local_moment(held.at)} is further off than a resume waits "
                    f"({round(RESUME_HORIZON / 86400)} days); run the recovery when the "
                    "window reopens"
                ),
            )
        self._write(record, held, state=RESUME_WAITING)
        measured = self._wait_for_the_window(record, held)
        if measured is not None:
            return measured
        if self._state() != RESUME_WAITING:
            # Superseded by a run somebody else started, or called off. Either way the
            # waiter's whole job has been taken over, and it starts nothing ([44 D]).
            return self._stored()
        refusal = _refusal(self.repository, record)
        if refusal is not None:
            return self._write(record, held, state=RESUME_REFUSED, reason=refusal)
        return self._start(record, held)

    def _wait_for_the_window(self, record: RunRecord, held: Held) -> PendingResume | None:
        """Sleep to the reported moment, then measure until one admits. The refusal, if one.

        The clock is never the evidence. A window reported to reopen at T is measured after
        T plus the settle, and one still closed is waited on again rather than entered —
        which is the whole of what `wait_until_clear` does inside a held step ([headroom.py]).
        """
        self.sleep(max(0.0, held.at + RESET_SETTLE - self.clock()))
        deadline = held.at + RESUME_HORIZON
        # Only a reading taken after the moment just waited out is believed, and the bar
        # rises with every hold: a measurement from before this pass's own wait is what the
        # last pass already decided on, and resting on it again would admit on the reading
        # that held ([headroom.wait_until_clear]).
        newer_than = held.at
        rechecks = 0
        while True:
            if self._state() != RESUME_WAITING:
                return None
            measure(
                self.gauge,
                model=None,
                newer_than=newer_than,
                budget_until=self.clock() + MEASURE_BUDGET,
                instruments=self.instruments,
            )
            decision = decide(
                self.gauge.read(), now=self.clock(), model=None, newer_than=newer_than
            )
            if decision.admission != HELD:
                return None
            now = self.clock()
            until = (
                decision.until + RESET_SETTLE
                if decision.until is not None
                else now + RECHECK_BACKOFF[min(rechecks, len(RECHECK_BACKOFF) - 1)]
            )
            rechecks += 1
            if until > deadline:
                return self._write(
                    record,
                    held,
                    state=RESUME_REFUSED,
                    reason=(
                        f"{window_phrase(decision.window)} had still not reopened "
                        f"{round(RESUME_HORIZON / 86400)} days after the moment this run "
                        f"named: {decision.reason or 'it measured as closed'}"
                    ),
                )
            self._write(
                record,
                held,
                state=RESUME_WAITING,
                reason=(
                    f"{window_phrase(decision.window)} measured closed at "
                    f"{local_moment(now)}; measuring again after {local_moment(until)}"
                ),
            )
            self.sleep(max(0.0, until - self.clock()))
            newer_than = decision.until if decision.until is not None else until

    def _start(self, record: RunRecord, held: Held) -> PendingResume:
        """Run the recovery command the record carries, under a run id minted here.

        The id is minted before the launch for the reason a start prints one before invoking
        the engine: a waiter killed mid-launch has still recorded the name of the run it
        began ([19 B]).
        """
        identity = self.mint() if self.mint is not None else mint_occasion()
        command = [*shlex.split(held.command), "--run-id", identity]
        self._write(
            record,
            held,
            state=RESUME_STARTING,
            started_run=identity,
            command=shlex.join(command),
        )
        try:
            finished = self.runner(
                command,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=START_TIMEOUT,
                check=False,
            )
        except (OSError, ValueError, subprocess.SubprocessError) as unstarted:
            return self._write(
                record,
                held,
                state=RESUME_REFUSED,
                reason=f"the recovery could not be started: {unstarted}",
                command=shlex.join(command),
            )
        if int(finished.returncode) != 0:
            return self._write(
                record,
                held,
                state=RESUME_REFUSED,
                reason=(
                    f"the recovery exited {finished.returncode}: "
                    f"{_said(finished) or 'it said nothing'}"
                ),
                started_run=identity,
                command=shlex.join(command),
            )
        notify(
            f"Cairn resumed run {self.run_id} as {identity}: "
            f"{window_phrase(held.window)} reopened.",
            environment=self.environment,
            runner=self.runner,
        )
        return self._write(
            record,
            held,
            state=RESUME_STARTED,
            started_run=identity,
            command=shlex.join(command),
        )

    def _write(
        self,
        record: RunRecord,
        held: Held,
        *,
        state: str,
        reason: str | None = None,
        started_run: str | None = None,
        command: str | None = None,
    ) -> PendingResume:
        pending = _pending(
            record,
            held,
            state=state,
            now=self.clock(),
            reason=reason,
            started_run=started_run,
            command=command,
        )
        write_resume(self.runs_root, pending)
        return pending

    def _stored(self) -> PendingResume | None:
        return read_resume(resume_path(self.runs_root, self.run_id), self.run_id)

    def _state(self) -> str | None:
        stored = self._stored()
        return None if stored is None else stored["state"]


def _said(finished: Any) -> str:
    """What a failed recovery start said, in one line, from whichever stream carried it."""
    streams: list[str] = []
    for raw in (getattr(finished, "stderr", None), getattr(finished, "stdout", None)):
        if isinstance(raw, bytes):
            streams.append(raw.decode("utf-8", errors="replace"))
        elif isinstance(raw, str):
            streams.append(raw)
    return " ".join(" ".join(stream.split()) for stream in streams if stream.strip())[:400]


def cancel(
    runs_root: Path, run_id: str, *, signaller: Callable[[int, int], None] | None = None
) -> str:
    """Call off a pending resume: stop the waiter, and remove what it wrote.

    The file goes whatever became of the process. A waiter that is already gone leaves one
    behind, and a file nobody is waiting behind would otherwise keep showing the run as
    resuming for ever.
    """
    stored = read_resume(resume_path(runs_root, run_id), run_id)
    if stored is None:
        clear_resume(runs_root, run_id)
        return f"no resume was pending for {run_id}"
    stopped = _stop(stored["pid"], signaller)
    clear_resume(runs_root, run_id)
    return (
        f"the resume pending for {run_id} is called off"
        + (f"; the waiter at pid {stored['pid']} was stopped" if stopped else "")
    )


def _stop(pid: int, signaller: Callable[[int, int], None] | None) -> bool:
    if pid <= 0 or pid == os.getpid():
        return False
    send = os.kill if signaller is None else signaller
    try:
        send(pid, signal.SIGTERM)
    except (OSError, ValueError):
        return False
    return True


def supersede(
    runs_root: Path, *, started_by: str, signaller: Callable[[int, int], None] | None = None
) -> list[str]:
    """Stand down every resume still waiting, because a run is starting against this
    repository.

    A resume exists to start a run nobody is there to start. A person starting one has
    answered that, so a waiter still sleeping on a window would start a second run behind
    the first ([44 D]). One already launching its own recovery is left alone: it is the run
    that is starting.
    """
    stood_down: list[str] = []
    if not runs_root.is_dir():
        return stood_down
    for directory in sorted(runs_root.iterdir()):
        waiting = directory.name
        if not directory.is_dir() or waiting == started_by or not RUN_ID.fullmatch(waiting):
            continue
        stored = read_resume(resume_path(runs_root, waiting), waiting)
        if stored is None or stored["state"] != RESUME_WAITING:
            continue
        _stop(stored["pid"], signaller)
        stood = dict(stored)
        stood["state"] = RESUME_SUPERSEDED
        stood["reason"] = f"run {started_by} was started against this repository first"
        write_resume(runs_root, cast(PendingResume, stood))
        stood_down.append(waiting)
    return stood_down


__all__ = [
    "MEASURE_BUDGET",
    "NOTIFY_TIMEOUT",
    "RESUME_HORIZON",
    "START_TIMEOUT",
    "Armed",
    "Held",
    "Waiter",
    "arm",
    "cancel",
    "clear_resume",
    "held_at",
    "held_step",
    "notify",
    "notify_command",
    "read_resume",
    "supersede",
    "waiter_command",
    "write_resume",
]
