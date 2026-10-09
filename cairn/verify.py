"""The verify gate: the assertion's exit status decides, and self-report can only veto.

The plan's own command runs bare, so nothing of Cairn's stands between it and the engine.
What Cairn owns is the consequence: a gate that reads the assertion's exit status and the
step's own account of itself, opens only when both agree the end state holds, and records
what it saw when it closes.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
import traceback
from typing import Any, TypedDict, cast

from cairn.assertions import (
    DECISION_RUN,
    DECISION_SHARED,
    DECISION_SKIPPED_UPSTREAM,
    SIGNALLED_EXIT,
    assertion_report,
    decision_of,
    record_executed,
    recorded_exit,
    remedy_ran,
    shared_exit,
)
from cairn.core import (
    ENDED_WITHOUT_REPORTING,
    EXIT_FAILED,
    EXIT_OK,
    CairnError,
    CommandResult,
    RuntimeContext,
    read_step_report,
    survive_termination,
    write_report,
)
from cairn.plan.schema import (
    ENGINE_NAME_MAX_BYTES,
    MARK_PREFIX,
    RECHECK_PREFIX,
    REMEDY_PREFIX,
    VERIFY_PREFIX,
    WORK_PREFIX,
)

# Why a step contributed no verified work. Frozen: every exclusion the run record names
# comes from here, and a message string never stands in for one. `retry_exhausted` and
# `orchestrator_died` are the engine's own verdicts on a step, derived from the run record
# rather than from this gate — and the second is what a step carries when nothing decided its
# fate at all, because the process that would have was killed under it.
VERIFY_FAILED = "verify_failed"
REPORTED_FAILURE = "reported_failure"
PROVIDER_PROTOCOL = "provider_protocol"
USER_DECISION_REQUIRED = "user_decision_required"
NOT_REACHED = "not_reached"
GATE_INDETERMINATE = "gate_indeterminate"
TIMED_OUT = "timed_out"
RETRY_EXHAUSTED = "retry_exhausted"
ORCHESTRATOR_DIED = "orchestrator_died"
# The assertion was ended by a signal — the engine's kill at its bound or anything else —
# before it exited. It decided nothing about the work, so it is never `verify_failed`.
ASSERTION_INTERRUPTED = "assertion_interrupted"
# The step stopped at the subscription's allowance: held longer than it may wait, or met the
# limit and could not be resumed. The work is not wrong and was not judged; the step's own
# report names the window and the moment it reopens ([headroom.py]).
QUOTA_HELD = "quota_held"
# The step's session ended on a fault of the provider's process — a crash, a launch that
# failed, a limit on turns — before it gave any verdict of its own. Never `reported_failure`:
# the runtime writes `failed` because a report has no other status for "nothing was said".
PROVIDER_FAILED = "provider_failed"
# The step's session lost the model provider — no connection, an edge or server error, a
# credential refused — and the provider did not answer again within the time the step may
# wait for it. The work is not wrong and was not judged ([headroom.py]).
PROVIDER_UNREACHABLE = "provider_unreachable"
EXCLUSION_CAUSES: tuple[str, ...] = (
    VERIFY_FAILED,
    REPORTED_FAILURE,
    PROVIDER_PROTOCOL,
    USER_DECISION_REQUIRED,
    NOT_REACHED,
    GATE_INDETERMINATE,
    TIMED_OUT,
    RETRY_EXHAUSTED,
    ORCHESTRATOR_DIED,
    ASSERTION_INTERRUPTED,
    QUOTA_HELD,
    PROVIDER_FAILED,
    PROVIDER_UNREACHABLE,
)

# How a failure routes onward. The engine spells a chain halt and a branch exclusion both
# `skipped`, so the distinction is the one Cairn made when it emitted the step, recorded
# here rather than guessed back out of the run afterwards.
CHAIN = "chain"
BRANCH = "branch"
POSITIONS: tuple[str, ...] = (CHAIN, BRANCH)

# The engine's bound on every name it loads, stated once in [plan/schema.py]. The corpus
# already carries a 67-character step id, which is why the handle below exists at all.
_DIGEST_LENGTH = 16

GATE_VERB = "gate"
# Exit 0 runs `mark_<id>` and the step's work is recorded; nonzero skips it and the step
# is excluded. Named for the step's fate, because that is what the report's cause and the
# run record speak in.
GATE_RECORD_IT = EXIT_OK
GATE_EXCLUDE_IT = EXIT_FAILED


# What a divergence records where the step gave no account of itself at all. A step whose
# session ended without reporting is written `failed` by the runtime, because that is the
# only status a report can carry when there is nothing to carry — and quoting `failed` back
# puts a verdict in the session's mouth that it never gave ([19 D]).
REPORTED_NOTHING = "nothing"
# And where the step did say something Cairn could not read. Two readings rather than one,
# because the divergence is the only channel this fact has into the record and a single
# word would make the run record contradict the gate's own summary beside it.
REPORTED_UNREADABLE = "unreadable"
# And where the step was stopped at a bound before it could say anything: the engine's, read
# back out of its record ([22 A]), or its own, enforced by the wrapper ([22 B]). The
# assertion still ran over whatever the step left, and this is the reading that lets the
# record weigh that against a session nobody heard from.
REPORTED_KILLED = "killed"
# And where the step stopped at the subscription's allowance before it reported. Not
# `killed`: nothing stopped the work for taking too long, and a reader weighing a re-run
# needs to know the step is waiting on the account rather than on the task.
REPORTED_HELD = "held"

# The report causes of a step that stopped before it reported without ever saying its work
# failed, with the exclusion cause the gate records, the reading its divergence carries, and
# the words its verdict says it in. Only `reported_failure` is the session's own veto; every
# cause here is the runtime's account of a session that gave none.
_PROVIDER_FAULT = (
    PROVIDER_FAILED,
    REPORTED_NOTHING,
    "the step's session ended on a provider fault before it reported",
)
_STOPPED_BEFORE_REPORTING: dict[str, tuple[str, str, str]] = {
    TIMED_OUT: (
        TIMED_OUT,
        REPORTED_KILLED,
        "the step was stopped at its own bound before it reported",
    ),
    QUOTA_HELD: (
        QUOTA_HELD,
        REPORTED_HELD,
        "the step was held at the subscription's allowance before it reported",
    ),
    PROVIDER_UNREACHABLE: (
        PROVIDER_UNREACHABLE,
        REPORTED_NOTHING,
        (
            "the step's session lost the model provider, which did not answer again in time, "
            "before it reported"
        ),
    ),
    PROVIDER_FAILED: _PROVIDER_FAULT,
    "turn_limit": _PROVIDER_FAULT,
    "process_launch_failed": _PROVIDER_FAULT,
    "provider_unavailable": _PROVIDER_FAULT,
}


class Divergence(TypedDict):
    """Two accounts of one step that do not agree, kept side by side and never resolved.

    `reported` is the step's own word for itself, or `REPORTED_NOTHING` where it gave none.
    The second is still a divergence worth recording: the assertion found the end state
    holding, and nothing said so — which is the difference between work that is merely
    unrecorded and work that was never done. It is also the only channel that fact has. The
    gate's own summary never reaches the record, and neither does the assertion's exit
    status; a mark report contributes exactly its cause, its position and this.
    """

    reported: str
    asserted: bool


class Verdict(TypedDict):
    """What the gate decided, before it becomes a report and an exit status."""

    record: bool
    cause: str | None
    divergence: Divergence | None
    summary: str


# The runtime's own account of a step, or an empty one. A report's `detail` is not validated
# by `read_step_report`, so it is read defensively wherever the gate turns on it.
def _detail(report: dict[str, Any]) -> dict[str, Any]:
    found = report.get("detail")
    return cast(dict[str, Any], found) if isinstance(found, dict) else {}


def divergence_line(divergence: Divergence) -> str:
    """The one sentence weighing two accounts of a step against each other.

    Stated once because there are two renderings of it — the run record's attention section
    and `cairn explain exclusion` — and they must not phrase one fact two ways. A divergence
    is recorded and never resolved ([docs/verify-gate.md]), so the sentence weighs the two
    accounts rather than settling them, and it never makes a step that said nothing appear
    to have said the word "nothing".
    """
    asserted = "passed" if divergence["asserted"] else "did not pass"
    if divergence["reported"] == REPORTED_NOTHING:
        return f"the step's session ended without reporting, and its assertion {asserted}"
    if divergence["reported"] == REPORTED_UNREADABLE:
        return f"the step left no readable account of itself, and its assertion {asserted}"
    if divergence["reported"] == REPORTED_KILLED:
        return (
            f"the step was stopped at its bound before it reported, and its assertion "
            f"{asserted} over the work it left"
        )
    if divergence["reported"] == REPORTED_HELD:
        return (
            f"the step was held at the subscription's allowance before it reported, and its "
            f"assertion {asserted} over the work it left"
        )
    return f"the step reported {divergence['reported']!r} while its assertion {asserted}"


def work_name(step_id: str) -> str:
    return f"{WORK_PREFIX}{step_id}"


def verify_name(step_id: str) -> str:
    return f"{VERIFY_PREFIX}{step_id}"


def mark_name(step_id: str) -> str:
    return f"{MARK_PREFIX}{step_id}"


def recheck_name(step_id: str) -> str:
    return f"{RECHECK_PREFIX}{step_id}"


def remedy_name(step_id: str) -> str:
    return f"{REMEDY_PREFIX}{step_id}"


def _handle(name: str, marker: str, step_id: str) -> str:
    if len(name.encode("utf-8")) <= ENGINE_NAME_MAX_BYTES:
        return name
    return f"{marker}_{hashlib.sha256(step_id.encode()).hexdigest()[:_DIGEST_LENGTH]}"


def verify_handle(step_id: str) -> str:
    """The engine id the gate's exit-status reference names.

    Measured against Dagu 2.11.0: a step is reachable as `${<id>.exit_code}` only when it
    declares an explicit `id`, and an id over the engine's bound is refused at load. A
    digest keeps the handle inside the bound without letting two steps share one.
    """
    return _handle(verify_name(step_id), "v", step_id)


def recheck_handle(step_id: str) -> str:
    """The engine id of a remedied step's second assertion, bounded as `verify_handle` is."""
    return _handle(recheck_name(step_id), "r", step_id)


def exit_status_reference(step_id: str) -> str:
    """The engine's own name for the assertion's exit status.

    Measured against Dagu 2.11.0: `${<id>.exit_code}` resolves in a precondition to the
    predecessor's exit status, while `${steps.<id>.exit_code}` resolves to nothing at all
    and fails the precondition without ever launching the command it names.
    """
    return f"${{{verify_handle(step_id)}.exit_code}}"


def recheck_exit_reference(step_id: str) -> str:
    return f"${{{recheck_handle(step_id)}.exit_code}}"


def judge(verify_exit: int | None, report: dict[str, Any] | None) -> Verdict:
    """Decide whether this step's work may be recorded as verified.

    Verify owns the green light and self-report owns the veto, so the two are read
    together and neither can raise what the other lowered. `verify_exit` is None exactly
    where the plan declared the step has no checkable effect, and there the step's own
    report is the only routing signal there is.
    """
    if report is None:
        # A cascade-skipped step evaluates no precondition and runs no body, while its
        # assertion still executes and can pass against a tree its predecessor never
        # touched. The absent report is what tells the two apart.
        return Verdict(
            record=False,
            cause="not_reached",
            divergence=None,
            summary="the step left no report of this run, so it never ran",
        )
    asserted = None if verify_exit is None else verify_exit == 0
    reported: str = report["status"]
    if report["needs_user_decision"]:
        return Verdict(
            record=False,
            cause="user_decision_required",
            divergence=None,
            summary="the step is blocked on a human decision",
        )
    if reported == "failed" and report.get("cause") == PROVIDER_PROTOCOL:
        # The step did not report failure; its account could not be read. Reading the
        # runtime's own `failed` as a veto records a divergence over an assertion nobody
        # contradicted, and tells the person their session claimed something it never did.
        #
        # **The cause is broader than the sentence.** `provider_protocol` covers every
        # unreadable-protocol fault, and only one of them is a session that said nothing at
        # all — so the summary is narrowed by the fact the runtime recorded rather than by
        # the cause, which means more than that ([core.ENDED_WITHOUT_REPORTING]).
        silent = _detail(report).get(ENDED_WITHOUT_REPORTING) is True
        reading = REPORTED_NOTHING if silent else REPORTED_UNREADABLE
        said = (
            "the step's session ended without reporting"
            if silent
            else "the step left no readable account of itself"
        )
        return Verdict(
            record=False,
            cause=PROVIDER_PROTOCOL,
            divergence=Divergence(reported=reading, asserted=True)
            if asserted
            else None,
            summary=(
                f"{said}, over an assertion that passed"
                if asserted
                else f"{said}, so nothing said what it did"
            ),
        )
    cause = report.get("cause")
    stopped = _STOPPED_BEFORE_REPORTING.get(cause) if isinstance(cause, str) else None
    if reported == "failed" and stopped is not None:
        # Stopped at the step's own bound, held at the allowance, or cut off by the provider:
        # either way the step never said its work failed. That is not a veto, so the
        # divergence weighs the assertion against a session nobody heard from, as the
        # engine's own kill does in the run record ([22 B]).
        excluded, reading, said = stopped
        return Verdict(
            record=False,
            cause=excluded,
            divergence=Divergence(reported=reading, asserted=True) if asserted else None,
            summary=f"{said}, over an assertion that passed" if asserted else said,
        )
    if reported == "failed":
        return Verdict(
            record=False,
            cause="reported_failure",
            divergence=Divergence(reported=reported, asserted=True) if asserted else None,
            summary=(
                "the step reported failure over an assertion that passed"
                if asserted
                else "the step reported failure"
            ),
        )
    if verify_exit == SIGNALLED_EXIT:
        return Verdict(
            record=False,
            cause=ASSERTION_INTERRUPTED,
            divergence=None,
            summary=(
                "the assertion was ended by a signal before it exited, so it decided "
                "nothing about the work"
            ),
        )
    if asserted is False:
        return Verdict(
            record=False,
            cause="verify_failed",
            divergence=Divergence(reported=reported, asserted=False),
            summary=(
                f"the assertion exited {verify_exit} over a step reporting {reported!r}"
            ),
        )
    return Verdict(record=True, cause=None, divergence=None, summary=report["summary"])


def _read_verify_exit(raw: str) -> int:
    try:
        return int(raw)
    except ValueError as exc:
        raise CairnError(
            "gate_indeterminate",
            f"the assertion's exit status reached the gate as {raw!r}, which is not a "
            "number, so what the assertion decided cannot be established",
        ) from exc


def _assertion_exit(
    step_id: str, verify_exit_text: str | None, context: RuntimeContext
) -> int | None:
    """The assertion's exit status, read only where the assertion's own gate says it ran.

    Measured against Dagu 2.11.0: `${<id>.exit_code}` resolves to `0` for a node its
    precondition skipped, so the reference alone would record a marker over an assertion
    that never ran. The assertion node's report says whether it did, or names the step
    whose proof of the same command against the same tree stands in for it; where it ran,
    the exit read here completes that report and becomes the proof later gates share
    ([assertions.py]).
    """
    if verify_exit_text is None:
        return None
    account = assertion_report(context.report_path.parent, step_id, context.run_id)
    decision = decision_of(account)
    if decision == DECISION_SHARED and account is not None:
        exit_code = shared_exit(account)
        if exit_code is None:
            raise CairnError(
                "gate_indeterminate",
                "the assertion's gate shared a proof that carries no exit status",
            )
        return exit_code
    if decision != DECISION_RUN or account is None:
        raise CairnError(
            "gate_indeterminate",
            "the assertion did not run"
            if decision == DECISION_SKIPPED_UPSTREAM
            else "the assertion's own gate left no account of whether the assertion ran, "
            "so its exit status cannot be trusted",
        )
    # A remedied step's remedy gate completes this account before the session opens, and
    # completing it twice would time the assertion across the session that followed it.
    exit_code = _read_verify_exit(verify_exit_text)
    if recorded_exit(account) != exit_code:
        record_executed(context, step_id, account, exit_code)
    return exit_code


def _recheck_exit(
    step_id: str, recheck_exit_text: str | None, context: RuntimeContext
) -> int | None:
    """A remedied step's second assertion, read only where a remedy ran and it did too.

    Anything short of both leaves the first assertion as the step's verdict. A second
    assertion with no remedy session before it would let a flaky command pass on its
    second asking, which is a retry the plan never declared ([plan-contract.md]).
    """
    directory = context.report_path.parent
    if recheck_exit_text is None or not remedy_ran(directory, step_id, context.run_id):
        return None
    account = assertion_report(directory, step_id, context.run_id, prefix=RECHECK_PREFIX)
    decision = decision_of(account)
    if decision == DECISION_SHARED and account is not None:
        return shared_exit(account)
    if decision != DECISION_RUN or account is None:
        return None
    exit_code = _read_verify_exit(recheck_exit_text)
    record_executed(context, step_id, account, exit_code, prefix=RECHECK_PREFIX)
    return exit_code


def run_verify_gate(
    step_id: str,
    position: str,
    verify_exit_text: str | None,
    context: RuntimeContext,
    recheck_exit_text: str | None = None,
) -> tuple[Verdict, dict[str, Any]]:
    """Decide, and assemble what the record needs when the answer is no.

    The work report outranks everything about the assertion: a step that left no report
    never ran, whatever the assertion's exit status reads ([24 B]). The assertion's exit
    is still read and filed where it ran, because the work it proved is in the tree even
    when the step that did it was killed before reporting ([22 A]).
    """
    verify_exit: int | None = None
    report: dict[str, Any] | None = None
    unread: CairnError | None = None
    try:
        if position not in POSITIONS:
            raise CairnError("invalid_arguments", f"unknown graph position {position!r}")
        # The work node's own name, not the bare step id: the topology names every node
        # `<role>_<subject>`, so that is the file the step actually wrote.
        report = read_step_report(
            context.report_path.parent, work_name(step_id), context.run_id
        )
    except CairnError as exc:
        unread = exc
    first_exit: int | None = None
    try:
        verify_exit = _assertion_exit(step_id, verify_exit_text, context)
        rechecked = _recheck_exit(step_id, recheck_exit_text, context)
        if rechecked is not None:
            first_exit, verify_exit = verify_exit, rechecked
        fault = unread
    except CairnError as exc:
        fault = unread or exc
    if fault is not None:
        # A step that left no report never ran; every other fault leaves what happened
        # unestablished, and the two must not be recorded as the same thing.
        cause = "not_reached" if fault.cause == "missing_report" else "gate_indeterminate"
        verdict = Verdict(record=False, cause=cause, divergence=None, summary=str(fault))
    else:
        verdict = judge(verify_exit, cast(dict[str, Any], report))
    detail: dict[str, Any] = {
        "position": position,
        "verify_exit": verify_exit,
        "reported": None if report is None else report["status"],
    }
    if first_exit is not None:
        detail["first_verify_exit"] = first_exit
    if verdict["divergence"] is not None:
        detail["divergence"] = verdict["divergence"]
    return verdict, detail


def _record_exclusion(
    context: RuntimeContext, verdict: Verdict, detail: dict[str, Any], started: float
) -> None:
    """Leave the account of an exclusion on the one path where no step will write one."""
    result = CommandResult(
        GATE_EXCLUDE_IT, "failed", verdict["summary"], [], False, verdict["cause"], detail
    )
    print(
        f"verify gate [{detail.get('step', '?')} {detail['position']}]: "
        f"{verdict['cause']} — {verdict['summary']}",
        file=sys.stderr,
    )
    with survive_termination():
        write_report(context, result, time.monotonic() - started)


def gate_main(arguments: list[str]) -> int:
    """Answer whether this step's work may be recorded as verified. Exit 0 means it may.

    This is a precondition rather than a step, so it writes a report only on the path
    where no step will run to write one. **Every fault closes it**, which is the exact
    inverse of the marker gate: a gate that cannot tell whether the work was asserted must
    never record it as verified, because a marker over unverified work reaches git, rides
    every merge, and makes the next run skip the step that would have caught it.
    """
    started = time.monotonic()
    parser = argparse.ArgumentParser(prog="cairn verify", add_help=False)
    parser.add_argument("verb", choices=(GATE_VERB,))
    parser.add_argument("--step", required=True)
    parser.add_argument("--position", default="?")
    # Absent exactly where the plan declared the step has no checkable effect, so the
    # step's own report is the only routing signal it has.
    parser.add_argument("--verify-exit", dest="verify_exit")
    # Present exactly where the plan declared `remediate`: the second assertion's exit.
    parser.add_argument("--recheck-exit", dest="recheck_exit")
    # Identity is resolved before the arguments are judged, because an exclusion with no
    # account of itself is the one outcome this gate must never produce — and argument
    # skew between an emitted workflow and an upgraded binary is exactly a case where the
    # arguments are what failed.
    context: RuntimeContext | None = None
    step = "?"
    verdict = Verdict(
        record=False,
        cause="gate_indeterminate",
        divergence=None,
        summary=f"the gate could not read its own arguments: {arguments}",
    )
    detail: dict[str, Any] = {"position": "?", "verify_exit": None, "reported": None}
    try:
        context = RuntimeContext.from_env()
        args = parser.parse_args(arguments)
        step = args.step
        verdict, detail = run_verify_gate(
            args.step, args.position, args.verify_exit, context, args.recheck_exit
        )
    except SystemExit:
        pass
    except Exception as exc:  # noqa: BLE001 - see the docstring: every fault closes the gate
        traceback.print_exc()
        verdict = Verdict(
            record=False, cause="gate_indeterminate", divergence=None, summary=str(exc)
        )
    if verdict["record"]:
        return GATE_RECORD_IT
    detail["step"] = step
    if context is None:
        # Nowhere to write to, which is the one exception the report contract already
        # makes: say so where a person running the engine will see it.
        print(f"verify gate [{step}]: {verdict['summary']}", file=sys.stderr)
        return GATE_EXCLUDE_IT
    try:
        _record_exclusion(context, verdict, detail, started)
    except Exception:  # noqa: BLE001 - the gate still closes; the record is best effort
        traceback.print_exc()
    return GATE_EXCLUDE_IT


__all__ = [
    "ASSERTION_INTERRUPTED",
    "BRANCH",
    "CHAIN",
    "EXCLUSION_CAUSES",
    "GATE_EXCLUDE_IT",
    "GATE_RECORD_IT",
    "ORCHESTRATOR_DIED",
    "POSITIONS",
    "PROVIDER_FAILED",
    "PROVIDER_PROTOCOL",
    "PROVIDER_UNREACHABLE",
    "REPORTED_KILLED",
    "REPORTED_NOTHING",
    "REPORTED_UNREADABLE",
    "Divergence",
    "Verdict",
    "divergence_line",
    "exit_status_reference",
    "gate_main",
    "judge",
    "mark_name",
    "recheck_exit_reference",
    "recheck_handle",
    "recheck_name",
    "remedy_name",
    "run_verify_gate",
    "verify_handle",
    "verify_name",
]
