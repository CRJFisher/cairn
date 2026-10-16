"""The verify gate: the assertion's exit status decides, and self-report can only veto.

The plan's own command runs bare, so nothing of Cairn's stands between it and the engine.
What Cairn owns is the consequence: a gate that reads the assertion's exit status and the
step's own account of itself, opens only when both agree the end state holds, and records
what it saw when it closes.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
import traceback
from collections.abc import Mapping
from pathlib import Path
from typing import Any, NamedTuple, TypedDict, cast

from cairn.assertions import (
    DECISION_NOTHING_TO_ASSERT,
    DECISION_RUN,
    DECISION_SHARED,
    DECISION_SKIPPED_UPSTREAM,
    SIGNALLED_EXIT,
    UNRUNNABLE_EXITS,
    assertion_report,
    decision_of,
    engine_exit,
    record_executed,
    recorded_exit,
    remedy_ran,
    shared_exit,
)
from cairn.core import (
    ENDED_WITHOUT_REPORTING,
    EXIT_FAILED,
    EXIT_OK,
    REPORTS_EXHAUSTED,
    CairnError,
    CommandResult,
    RuntimeContext,
    held_before_session,
    read_step_report,
    survive_termination,
    write_report,
)
from cairn.gitio import git
from cairn.layout import MARKED_AT
from cairn.plan.schema import (
    ENGINE_NAME_MAX_BYTES,
    MARK_PREFIX,
    RECHECK_PREFIX,
    REMEDY_PREFIX,
    VERIFY_PREFIX,
    WORK_PREFIX,
)
from cairn.text import flatten, last_line

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
# The assertion's command never executed: the shell found nothing to run, or could not run
# what it found. It never judged the work, so it is never `verify_failed`; what it met is
# most often the environment the assertion stands in ([54 B]).
ASSERTION_UNRUNNABLE = "assertion_unrunnable"
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
    ASSERTION_UNRUNNABLE,
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

# How many of the commits since a done step's marker its summary lists; the rest are counted.
LAPSE_SHOWN = 5
# How much of the end of an assertion's standard error the gate reads for its last line.
_STDERR_TAIL_BYTES = 8192

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
# And where the step did say something Cairn could not read. Separate readings rather than
# one, because the divergence is the only channel this fact has into the record and a single
# word would make the run record contradict the gate's own summary beside it.
REPORTED_UNREADABLE = "unreadable"
# And where the step filed its account and the provider refused every version of it. The
# narrowest of the three: the session had something to say and the shape of the saying is
# what stopped it, which is a different thing to fix from a session that never spoke ([46 C]).
REPORTED_REFUSED = "refused"
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
    unrecorded and work that was never done. It is also the only channel that fact has: the
    record reads a mark report's cause, its position and this, and quotes the gate's own
    summary only for a step the marker gate skipped, whose own words are an earlier run's.
    """

    reported: str
    asserted: bool


class Lapse(NamedTuple):
    """What has landed since a done step's marker, where its re-assertion no longer passes.

    A recovery re-asserts every step it no-ops, so a step recorded done can fail over a
    tree that moved on after it. The commits are the evidence a person reads first: the
    marker was true when it was written, and one of these is the likeliest to have undone it.
    """

    # Newest first, each as `<short hash> <subject>`, at most `LAPSE_SHOWN` of them.
    commits: tuple[str, ...]
    total: int



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
    if divergence["reported"] == REPORTED_REFUSED:
        return f"every report the step's session filed was refused, and its assertion {asserted}"
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

    Measured against Dagu 2.18.2: a step is reachable as `${<id>.exit_code}` only when it
    declares an explicit `id`, and an id over the engine's bound is refused at load. A
    digest keeps the handle inside the bound without letting two steps share one.
    """
    return _handle(verify_name(step_id), "v", step_id)


def recheck_handle(step_id: str) -> str:
    """The engine id of a remedied step's second assertion, bounded as `verify_handle` is."""
    return _handle(recheck_name(step_id), "r", step_id)


def exit_status_reference(step_id: str) -> str:
    """The engine's own name for the assertion's exit status.

    Measured against Dagu 2.18.2: `${<id>.exit_code}` resolves in a precondition to the
    predecessor's exit status, while `${steps.<id>.exit_code}` resolves to nothing at all
    and fails the precondition without ever launching the command it names.
    """
    return f"${{{verify_handle(step_id)}.exit_code}}"


def recheck_exit_reference(step_id: str) -> str:
    return f"${{{recheck_handle(step_id)}.exit_code}}"


def judge(
    verify_exit: int | None,
    report: dict[str, Any] | None,
    *,
    said: str | None = None,
    lapse: Lapse | None = None,
) -> Verdict:
    """Decide whether this step's work may be recorded as verified.

    Verify owns the green light and self-report owns the veto, so the two are read
    together and neither can raise what the other lowered. `verify_exit` is None where no
    assertion decided anything — the plan declared the step has no checkable effect, or the
    allowance stopped the step before it did any work for one to run over — and there the
    step's own report is the only routing signal there is.

    `said` is the last line the assertion wrote to standard error, and `lapse` what landed
    since a done step's marker. Neither moves the verdict; each only lets its summary name
    the fact a person acts on.
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
        # unreadable-protocol fault, and a session that said nothing at all is only one of
        # them — so the summary is narrowed by the facts the runtime recorded rather than by
        # the cause, which means more than any of them ([core.ENDED_WITHOUT_REPORTING],
        # [core.REPORTS_EXHAUSTED]). A session whose every report the provider refused had
        # an account and could not get it accepted, which is a different thing for a person
        # to act on than a session that never spoke ([46 C]).
        detail = _detail(report)
        silent = detail.get(ENDED_WITHOUT_REPORTING) is True
        if not silent:
            reading = REPORTED_UNREADABLE
            said = "the step left no readable account of itself"
        elif detail.get("terminal_reason") == REPORTS_EXHAUSTED:
            reading = REPORTED_REFUSED
            said = "every report the step's session filed was refused"
        else:
            reading = REPORTED_NOTHING
            said = "the step's session ended without reporting"
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
    if reported == "failed" and cause == QUOTA_HELD and held_before_session(report):
        # A divergence pairs a session's account of itself with an assertion over that
        # session's work, and a step the allowance stopped before it started has neither.
        # Its assertion was declined for the same reason ([assertions.py]), so there is no
        # exit here to weigh against anything ([42 A]).
        return Verdict(
            record=False,
            cause=QUOTA_HELD,
            divergence=None,
            summary=(
                "the step was held at the subscription's allowance before it started, so "
                "nothing was done and nothing asserted over it"
            ),
        )
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
    if verify_exit in UNRUNNABLE_EXITS:
        never = "was not found" if verify_exit == 127 else "could not be executed"
        return Verdict(
            record=False,
            cause=ASSERTION_UNRUNNABLE,
            divergence=None,
            summary=(
                f"the assertion exited {verify_exit}: the command it runs {never} where it "
                "ran, so it could not judge the work"
                + (f" — {said}" if said else "")
            ),
        )
    if asserted is False and reported == "noop":
        # The step did nothing this run because its marker said it was done, and its
        # assertion no longer passes over the tree as it stands. The cascade behind it is
        # right — nothing may build on a state nobody can prove — but "the step failed" is
        # not what happened, and a person needs to know the tree moved under it ([54 C]).
        # No divergence: `noop` is the marker gate's word, not an account this run's session
        # gave, so there is nothing of the step's own to weigh the assertion against.
        return Verdict(
            record=False,
            cause=VERIFY_FAILED,
            divergence=None,
            summary=_no_longer_holds(
                verify_exit, _detail(report).get("recorded_run"), lapse
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


def _no_longer_holds(
    verify_exit: int | None, recorded_run: object, lapse: Lapse | None
) -> str:
    """The summary of a step recorded done whose assertion no longer passes."""
    named = isinstance(recorded_run, str) and recorded_run
    by = f" by run {flatten(recorded_run)}" if named else ""
    head = (
        f"the step was already recorded done{by}, and its assertion no longer passes over "
        f"the current tree (it exited {verify_exit})"
    )
    if lapse is None:
        return head
    if lapse.total == 0:
        return f"{head}; nothing has been committed since its marker"
    more = lapse.total - len(lapse.commits)
    listed = "; ".join(lapse.commits) + (f"; and {more} more" if more > 0 else "")
    return f"{head}; {lapse.total} commit(s) since its marker: {listed}"


def assertion_said(
    step_id: str, rechecked: bool, environment: Mapping[str, str]
) -> str | None:
    """The last line the assertion wrote to standard error, from the engine's own log.

    Measured against Dagu 2.18.2: a precondition is given its step's own log paths, and
    every node of one attempt logs into one directory as `<name>.<moment>.<run>.err`. So the
    assertion's log is a sibling of this gate's, named for the node that ran it. Anything
    short of finding it leaves the summary without a quote rather than failing the gate.
    """
    own = environment.get("DAG_RUN_STEP_STDERR_FILE")
    if not own:
        return None
    name = recheck_name(step_id) if rechecked else verify_name(step_id)
    try:
        logs = sorted(
            Path(own).parent.glob(f"{name}.*.err"), key=lambda path: path.stat().st_mtime
        )
        if not logs:
            return None
        with logs[-1].open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - _STDERR_TAIL_BYTES))
            text = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    return last_line(text)



def _lapse(context: RuntimeContext, report: dict[str, Any]) -> Lapse | None:
    """What has been committed since the step's marker, or None where that is unknowable.

    The marker gate names the commit that last wrote the marker when it finds the marker
    fresh, so the range is read from what that gate saw rather than derived again here.
    """
    marked = _detail(report).get(MARKED_AT)
    if not isinstance(marked, str) or not marked:
        return None
    root = context.working_directory
    since = f"{marked}..HEAD"
    try:
        counted = git(root, ("rev-list", "--count", since), check=False)
        shown = git(
            root,
            ("log", f"--max-count={LAPSE_SHOWN}", "--format=%h %s", since),
            check=False,
        )
        if counted.exit_code != 0 or shown.exit_code != 0:
            return None
        total = int(counted.stdout.strip())
    except (CairnError, ValueError):
        return None
    commits = tuple(flatten(line) for line in shown.stdout.splitlines() if line.strip())
    return Lapse(commits=commits, total=total)


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

    Measured against Dagu 2.18.2: `${<id>.exit_code}` resolves to `0` for a node its
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
    if decision == DECISION_NOTHING_TO_ASSERT:
        # The precondition's own decision, not a fault: the step did nothing, so its
        # assertion was declined and there is no exit to read ([42 A]).
        return None
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
    exit_code = engine_exit(account, _read_verify_exit(verify_exit_text))
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
    exit_code = engine_exit(account, _read_verify_exit(recheck_exit_text))
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
        work = cast(dict[str, Any], report)
        verdict = judge(
            verify_exit,
            work,
            said=assertion_said(step_id, first_exit is not None, os.environ)
            if verify_exit in UNRUNNABLE_EXITS
            else None,
            lapse=_lapse(context, work)
            if work["status"] == "noop" and verify_exit not in (None, 0)
            else None,
        )
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
    "ASSERTION_UNRUNNABLE",
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
    "REPORTED_REFUSED",
    "REPORTED_UNREADABLE",
    "Divergence",
    "Lapse",
    "Verdict",
    "assertion_said",
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
