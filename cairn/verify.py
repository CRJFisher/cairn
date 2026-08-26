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
from typing import Any, TypedDict

from cairn.core import (
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
    VERIFY_PREFIX,
    WORK_PREFIX,
)

# Why a step contributed no verified work. Frozen: every exclusion the run record names
# comes from here, and a message string never stands in for one. The last three are the
# engine's own verdicts on a step, derived from the run record rather than from this gate —
# and the last of them is what a step carries when nothing decided its fate at all, because
# the process that would have was killed under it.
VERIFY_FAILED = "verify_failed"
REPORTED_FAILURE = "reported_failure"
USER_DECISION_REQUIRED = "user_decision_required"
NOT_REACHED = "not_reached"
GATE_INDETERMINATE = "gate_indeterminate"
TIMED_OUT = "timed_out"
RETRY_EXHAUSTED = "retry_exhausted"
ORCHESTRATOR_DIED = "orchestrator_died"
EXCLUSION_CAUSES: tuple[str, ...] = (
    VERIFY_FAILED,
    REPORTED_FAILURE,
    USER_DECISION_REQUIRED,
    NOT_REACHED,
    GATE_INDETERMINATE,
    TIMED_OUT,
    RETRY_EXHAUSTED,
    ORCHESTRATOR_DIED,
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


class Divergence(TypedDict):
    """Two accounts of one step that do not agree, kept side by side and never resolved."""

    reported: str
    asserted: bool


class Verdict(TypedDict):
    """What the gate decided, before it becomes a report and an exit status."""

    record: bool
    cause: str | None
    divergence: Divergence | None
    summary: str


def work_name(step_id: str) -> str:
    return f"{WORK_PREFIX}{step_id}"


def verify_name(step_id: str) -> str:
    return f"{VERIFY_PREFIX}{step_id}"


def mark_name(step_id: str) -> str:
    return f"{MARK_PREFIX}{step_id}"


def verify_handle(step_id: str) -> str:
    """The engine id the gate's exit-status reference names.

    Measured against Dagu 2.11.0: a step is reachable as `${<id>.exit_code}` only when it
    declares an explicit `id`, and an id over the engine's bound is refused at load. A
    digest keeps the handle inside the bound without letting two steps share one.
    """
    name = verify_name(step_id)
    if len(name) <= ENGINE_NAME_MAX_BYTES:
        return name
    return f"v_{hashlib.sha256(step_id.encode()).hexdigest()[:_DIGEST_LENGTH]}"


def exit_status_reference(step_id: str) -> str:
    """The engine's own name for the assertion's exit status.

    Measured against Dagu 2.11.0: `${<id>.exit_code}` resolves in a precondition to the
    predecessor's exit status, while `${steps.<id>.exit_code}` resolves to nothing at all
    and fails the precondition without ever launching the command it names.
    """
    return f"${{{verify_handle(step_id)}.exit_code}}"


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


def _read_verify_exit(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise CairnError(
            "gate_indeterminate",
            f"the assertion's exit status reached the gate as {raw!r}, which is not a "
            "number, so what the assertion decided cannot be established",
        ) from exc


def run_verify_gate(
    step_id: str, position: str, verify_exit_text: str | None, context: RuntimeContext
) -> tuple[Verdict, dict[str, Any]]:
    """Decide, and assemble what the record needs when the answer is no."""
    verify_exit: int | None = None
    report: dict[str, Any] | None = None
    try:
        if position not in POSITIONS:
            raise CairnError("invalid_arguments", f"unknown graph position {position!r}")
        verify_exit = _read_verify_exit(verify_exit_text)
        # The work node's own name, not the bare step id: the topology names every node
        # `<role>_<subject>`, so that is the file the step actually wrote.
        report = read_step_report(
            context.report_path.parent, work_name(step_id), context.run_id
        )
        verdict = judge(verify_exit, report)
    except CairnError as exc:
        # A step that left no report never ran; every other fault leaves what happened
        # unestablished, and the two must not be recorded as the same thing.
        cause = "not_reached" if exc.cause == "missing_report" else "gate_indeterminate"
        verdict = Verdict(record=False, cause=cause, divergence=None, summary=str(exc))
    detail: dict[str, Any] = {
        "position": position,
        "verify_exit": verify_exit,
        "reported": None if report is None else report["status"],
    }
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
            args.step, args.position, args.verify_exit, context
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
    "BRANCH",
    "CHAIN",
    "EXCLUSION_CAUSES",
    "GATE_EXCLUDE_IT",
    "GATE_RECORD_IT",
    "ORCHESTRATOR_DIED",
    "POSITIONS",
    "Divergence",
    "Verdict",
    "exit_status_reference",
    "gate_main",
    "judge",
    "mark_name",
    "run_verify_gate",
    "verify_handle",
    "verify_name",
]
