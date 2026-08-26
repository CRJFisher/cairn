"""The one path a run starts from, and the address it hands back.

Every other trigger surface belongs to the engine — its start dialog, a cron firing, a
webhook — and Cairn owns none of them ([docs/triggers.md]). This is the skill's, and it is
the only one in Cairn's own code that can begin a paid run. It accepts an `Authorisation`
and nothing else, so a run has to have been offered, priced and accepted before this module
has anything to act on.

**It composes no parameter it was not asked for.** The repository is the one the definition
was authored for, established before the offer was made ([resolve.py]), so nothing here
retargets a workflow — that is re-authoring, and varying the parameter instead writes the
run's whole record into the wrong repository ([cairn/parameters.py]).

**The view is a link, not a rendering.** The address comes from `layout.view_url`, the same
composer the run record uses, so there is one spelling of where a run can be watched.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from cairn.core import Child, PopenFactory, launch
from cairn.enginehome import ENGINE_BINARY
from cairn.layout import check_run_id, engine_log_path, view_url
from cairn.skill.consent import Authorisation, refuse_uncarriable
from cairn.supervise import find_run_record
from cairn.workflow.gate import EngineUnavailable, assert_pinned, rehearse_start
from cairn.workflow.schema import OCCASION_PARAM, PARENT_BRANCH_PARAM, WORKFLOW_SUFFIX

# Whether the engine has taken a run on, asked of the engine's own history. A seam, so a
# test can drive the three outcomes without an engine.
RunRegistered = Callable[[str], bool]

# How long to wait for the engine to say it has the run. Measured: from an unsandboxed
# shell the engine took the run on within a second, and a shell that cannot bind the run's
# socket is refused before the offer is spent ([gate.rehearse_start]) — so this bound is
# reached only where neither happened, and it must stay well under the two minutes a
# harness's own tool call allows, because that caller is who this exists for.
TAKEN_ON_TIMEOUT = 30.0
TAKEN_ON_INTERVAL = 0.2


class Address(NamedTuple):
    """Everything about a run that is known before the engine is invoked.

    Composed from an `Authorisation` and nothing else, which is what lets the four lines a
    person needs be printed *before* the launch — the run id, the branch, the view and the
    command that reads the record. A start killed at any point after this has already handed
    over the name of the run it bought ([19 B]).
    """

    run_id: str
    dag_name: str
    view: str
    command: tuple[str, ...]
    log: Path


class Started(NamedTuple):
    """How the engine answered, beside the address that was known before it was asked.

    `exit_code` is `None` wherever the engine is still running, which is the ordinary case
    for a detached start and never a missing value. `taken_on` is the question this command
    actually answers: a run the engine has registered leaves a record whatever becomes of
    the process that started it, and a run it never took on leaves nothing at all.
    """

    address: Address
    taken_on: bool
    exit_code: int | None


def refuse_unusable_engine() -> None:
    """Halt before an offer is spent if the engine could not run the plan anyway.

    Asked here rather than inside `start`, because an acceptance is consumed the moment the
    offer's marker is claimed and a machine carrying the wrong engine is a cause a person
    can clear. Making them answer for the run twice, for a refusal that started nothing, is
    the one thing the single-use rule must not be allowed to cost.

    **Two questions, and the order is load-bearing.** The version pin is answered by asking
    the binary what it is, which costs nothing and needs no engine home; the rehearsal
    actually starts a one-step run, which is the only way to find out whether this shell can
    bind the socket every run opens ([gate.rehearse_start]). Pinning first means a machine
    with no engine at all is refused by the cheaper question, and the rehearsal is never
    reached with nothing to rehearse against.
    """
    assert_pinned()
    rehearse_start()


def dag_name(workflow: Path) -> str:
    """The engine's name for a definition, which is its filename and nothing else.

    The same value the view's URL and any webhook endpoint are keyed on ([docs/triggers.md]),
    so it is derived here rather than carried, and a definition renamed on disk is a
    different DAG to the engine whatever Cairn recorded.
    """
    return workflow.name.removesuffix(WORKFLOW_SUFFIX)


def start_command(authorisation: Authorisation, run_id: str) -> tuple[str, ...]:
    """The engine invocation this run is, spelled so a person can read or repeat it.

    **Every value here comes from the authorisation**, so the run that happens is the run
    that was priced and accepted. A parameter settled after the offer would be a term of the
    agreement nobody agreed to — the branch most of all, since it is what verified work is
    merged into.

    Never `dagu retry`. Re-running a plan is the whole recovery story (I4), and a continued
    occasion is what makes it cheap — so a recovery is an ordinary start carrying the
    occasion it continues, and there is no second recovery path to keep correct.
    """
    check_run_id(run_id)
    parameters = [f"{PARENT_BRANCH_PARAM}={authorisation.parent_branch}"]
    if authorisation.occasion is not None:
        parameters.append(f"{OCCASION_PARAM}={authorisation.occasion}")
    # Measured against Dagu 2.11.0, `--params` takes `P1=foo P2=bar` and splits on
    # whitespace, so a value holding a space arrives as two parameters. `make_offer` refuses
    # such a value when the offer is priced; this is the same rule where it can no longer
    # cost anybody their acceptance.
    for parameter in parameters:
        refuse_uncarriable(parameter)
    return (
        ENGINE_BINARY,
        "start",
        "--run-id",
        run_id,
        "--params",
        " ".join(parameters),
        authorisation.workflow,
    )


def address(authorisation: Authorisation, run_id: str, runs_root: Path) -> Address:
    """Where this run will be, composed before anything is asked to start it.

    Every value comes from the authorisation, so nothing here can name a run that was not
    priced and accepted.
    """
    name = dag_name(Path(authorisation.workflow))
    return Address(
        run_id=run_id,
        dag_name=name,
        view=view_url(name, run_id),
        command=start_command(authorisation, run_id),
        log=engine_log_path(runs_root, run_id),
    )


def engine_holds(records: Path, run_id: str) -> bool:
    """Whether the engine has taken this run on, asked of its own history.

    The engine writes the run's status data as it accepts the run, and that data is what
    every later reader keys on — so a run it has recorded is a run a recovery can name
    whatever happens to the process that started it. Matched on the run id *inside* the
    record rather than on the path, because the engine's directory layout carries no
    external contract ([supervise.find_run_record]).
    """
    return find_run_record(records, run_id) is not None


def launch_detached(command: tuple[str, ...], log: Path, factory: PopenFactory) -> Child:
    """The engine in its own session, with everything it says going to the run's own log.

    Each of these is load-bearing:

    - **`start_new_session`** puts the child in its own session and process group, so a
      harness killing its process tree and a hangup on a closing terminal both stop at the
      boundary. Measured 2026-08-25: a detached start outlived the session that launched it
      by 1h18m, and the release still gave the repository back.
    - **stdin from `/dev/null`** so an orphan cannot inherit a terminal and be stopped by
      `SIGTTIN`, nor hold the harness's own stdin open.
    - **stdout and stderr into the log, and the parent's handle closed** — an inherited pipe
      held open by an orphan is exactly how a parent waits on an EOF that never comes.
    - **appended, never truncated**, because a run directory is per run and not per attempt:
      a recovery against the same run id must not delete the evidence of what it recovers.
    """
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("ab") as handle:
        return launch(
            factory,
            command,
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


def start(
    authorisation: Authorisation,
    run_id: str,
    *,
    runs_root: Path,
    records: Path,
    wait: bool = False,
    popen_factory: PopenFactory | None = None,
    registered: RunRegistered | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> Started:
    """Begin the run this authorisation bought, and return once the engine has it.

    **It returns when the engine has the run, not when the run ends.** The engine is
    launched detached and this waits only until the engine's own history says it took the
    run on. A plan whose slowest chain is bounded at forty-four hours is forty-four hours of
    a blocked terminal otherwise, and any caller with its own timeout — an agent harness's
    tool call at two minutes — kills the process tree under it, spending the offer and
    losing the run id with the dying process ([19 B]).

    Nothing is lost by not waiting: the release handler writes the run's record whether
    anyone is watching or not ([12]).

    The engine's exit status is carried rather than interpreted: a run with exclusions is
    the case Cairn does not trust the engine about, and the verdict is the record's to
    derive by walking every node ([docs/run-model.md]). What this returns is that a run
    started and where it is, never how it went.

    The engine is checked before the authorisation is spent rather than here — see
    `refuse_unusable_engine` — so a machine that cannot run the plan does not cost a person
    their acceptance.
    """
    where = address(authorisation, run_id, runs_root)
    factory: PopenFactory = subprocess.Popen if popen_factory is None else popen_factory
    holds: RunRegistered = (
        (lambda identity: engine_holds(records, identity))
        if registered is None
        else registered
    )
    process = launch_detached(where.command, where.log, factory)
    deadline = monotonic() + TAKEN_ON_TIMEOUT
    while True:
        if holds(run_id):
            # The engine has the run. Under `--wait` the caller asked for the status in
            # line and gets it; otherwise the process is left to outlive this one.
            return Started(
                address=where,
                taken_on=True,
                exit_code=process.wait() if wait else None,
            )
        exited = process.poll()
        if exited is not None:
            # Asked once more after the exit: a run short enough to finish between the two
            # checks did register, and reporting it as never taken on would refuse a run
            # that actually happened.
            return Started(address=where, taken_on=holds(run_id), exit_code=exited)
        if monotonic() >= deadline:
            # Neither taken on nor exited. The child is deliberately **not** killed: it may
            # be a moment from registering, and killing a run the offer has already paid
            # for, on a timer, is the one destructive move available here.
            #
            # `--wait` is still honoured: a caller that asked to block until the run ends
            # asked for exactly that, and returning without waiting would hand it a zero
            # exit for a run whose fate is unknown. **And the question is asked again
            # afterwards** — the engine may have registered the run during the wait, and a
            # `taken_on` sampled before it would refuse a run that then ran to completion.
            if not wait:
                return Started(address=where, taken_on=False, exit_code=None)
            waited = process.wait()
            return Started(address=where, taken_on=holds(run_id), exit_code=waited)
        sleeper(TAKEN_ON_INTERVAL)


__all__ = [
    "Address",
    "EngineUnavailable",
    "RunRegistered",
    "Started",
    "address",
    "dag_name",
    "engine_holds",
    "launch_detached",
    "refuse_unusable_engine",
    "rehearse_start",
    "start",
    "start_command",
]
