"""The gate on a step's assertion: whether it runs at all, and the account it leaves.

A step's assertion is the plan's own command, emitted bare ([verify-gate.md]). What Cairn
owns is whether it is worth running, and this is the precondition that decides. It says no
in two cases. The step's work node left no report of this run — a step an upstream halt
skipped, whose assertion would run against a tree the step never touched and whose gate
would close `not_reached` regardless ([24 B]). A step the engine killed at its bound leaves
no report either, so its assertion is declined on the same rule and the record carries no
verdict over what it left; the two are indistinguishable from here, and letting the wrong
one through would assert over a tree its step never touched. Or the same command has already been proven,
in this run, against exactly this tree: a recovery no-ops every finished step in
milliseconds and then re-proves one command fourteen times, half an hour of the same suite
over the same bytes ([24 A]). A marker no-op leaves its `noop` report and keeps its
assertion, which is the recovery guarantee; sharing only spares the second proof of what
the first already proved.

**The key is the command's bytes and the tree's state**, never the command alone. A step
doing new work commits, and every later gate quoting the same command would otherwise read
a proof taken before the work it is meant to assert. A shared failure closes every gate
quoting it, and a failing proof is never replaced by a passing one: sharing never widens
what passes. The state is what git reports, so a step whose whole effect lands in paths git
ignores moves neither the tree nor the digest, and a later gate quoting the same command
shares its proof.

**Publishing a proof is a concurrent decision**, so it happens in a critical section keyed
by the proof: two steps quoting one command run it at the same moment, each having asked
before either filed, and an unlocked read/check/write would let whichever finished last
decide what every later gate reads. Failure's dominance is what that lock protects, and it
holds whatever order the two writers arrive in ([_publish]).

**It fails open, like the marker gate.** Running an assertion that need not run costs
minutes; skipping one that must run costs the step its record. Every fault, argument skew
included, exits zero — which is also why this verb has its own routing arm rather than a
place inside the fail-closed `verify gate` parser ([__main__.py]).

**It always writes its decision** to the assertion node's own report before it exits. The
mark gate reads that report first, and trusts the engine's exit-status reference only where
the decision says the assertion ran: measured against Dagu 2.11.0, `${<id>.exit_code}`
resolves to `0` for a node its precondition skipped, so a gate that read the reference over
a skipped assertion would record a marker over an assertion that never ran. Where the
assertion did run, the mark gate completes the same report with the exit it read and
files that exit as the proof later gates share.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any, NamedTuple, cast

from cairn.core import (
    EXIT_FAILED,
    EXIT_OK,
    CairnError,
    CommandResult,
    RuntimeContext,
    read_step_report,
    survive_termination,
    write_json,
    write_report,
    write_report_for,
)
from cairn.gitio import digest_states, git, tree_entries
from cairn.layout import MARKER_DIRECTORY, assertion_lock_path, assertion_result_path
from cairn.locks import exclusive_lock
from cairn.plan.schema import REMEDY_PREFIX, VERIFY_PREFIX, WORK_PREFIX

NEEDED_VERB = "needed"
REMEDY_VERB = "remedy"

# How long publishing one proof waits for the key's lock. The critical section is a read of
# one small file and a replace of another, so a wait this long is already pathological
# rather than busy — and the fallback is only a spared execution, never a weaker proof.
PUBLICATION_WAIT_SECONDS = 30.0
PROOF_LOCK_UNAVAILABLE = "proof_lock_unavailable"

# The precondition's two answers, named for what the engine does with them.
NEEDED_RUN_IT = EXIT_OK
NEEDED_SKIP_IT = EXIT_FAILED

# What the gate decided, recorded in the assertion node's own report. Frozen: the mark gate
# turns on these words and a spelling outside the set is a report it cannot read.
DECISION_RUN = "run"
DECISION_SHARED = "shared"
DECISION_SKIPPED_UPSTREAM = "skipped_upstream"
# A remedied step's second assertion, declined because no remedy session reported doing
# anything: running it again over an unchanged tree would only ask the first question twice.
DECISION_NOT_REMEDIED = "not_remedied"
DECISIONS: tuple[str, ...] = (
    DECISION_RUN,
    DECISION_SHARED,
    DECISION_SKIPPED_UPSTREAM,
    DECISION_NOT_REMEDIED,
)

# The remedy gate's two answers. It fails **closed**, the opposite of the assertion's own
# gate: a remedy it wrongly declines costs a step that was failing anyway, while one it
# wrongly opens is a paid session nobody needed.
REMEDY_OPEN_IT = EXIT_OK
REMEDY_DECLINE_IT = EXIT_FAILED

# Which execution backed a step's assertion, once one did: its own, or another step's
# proof of the same command against the same tree.
ASSERTION_EXECUTED = "executed"
ASSERTION_SHARED = "shared"
ASSERTION_SOURCES: tuple[str, ...] = (ASSERTION_EXECUTED, ASSERTION_SHARED)

DECISION_KEY = "decision"
EXIT_KEY = "exit"
SOURCE_KEY = "source"
BACKED_BY_KEY = "backed_by"
COMMAND_KEY = "command_sha256"
TREE_KEY = "tree_sha256"
# Wall-clock time the gate let the assertion run. The assertion writes nothing, so this is
# the only start the mark gate can time it from; the gate's own duration is milliseconds.
RELEASED_AT_KEY = "released_at"
# The assertion's own bound, so its account can tell the engine's kill at that bound from
# any other signal: the remedy for one is a larger `verify_timeout`, for the other a hunt.
BOUND_KEY = "bound_seconds"
# How close to its bound a signalled assertion has to have run for the bound to be what
# ended it. The engine's kill and the gate's clock are seconds apart, never minutes.
BOUND_SLACK_SECONDS = 5.0

# The exit status Dagu 2.11.0 hands on for a process a signal ended: Go's `ExitCode()`
# answers -1 there, and the signal itself does not reach `${<id>.exit_code}`. Read as an
# exit it says the assertion failed, when it never got to say anything.
SIGNALLED_EXIT = -1


def command_digest(command: str) -> str:
    """The assertion command's bytes, as the key sharing is looked up under."""
    return hashlib.sha256(command.encode("utf-8")).hexdigest()


def tree_digest(root: Path, *, runs_root: Path | None = None) -> str | None:
    """The working tree's state: the commit it stands on, plus every dirty path's content.

    A pure recovery no-ops every step and commits nothing, so fourteen readings of one
    tree give one digest; a commit landing, or a file changing, moves it. Two things Cairn
    itself writes during a run are left out, because a digest that moved with them would
    never hit: the markers under `.steps/`, one of which is written on every step that is
    recorded, and the runs root where every gate files its account — ordinarily inside the
    git admin directory and invisible here, but not necessarily. None where git will not
    say, which reads as a tree nothing may be shared against.
    """
    entries = tree_entries(root)
    if entries is None:
        return None
    head = git(root, ("rev-parse", "--verify", "HEAD"), check=False)
    states: dict[str, str] = {"HEAD": head.stdout if head.exit_code == 0 else "unborn"}
    churn = [f"{MARKER_DIRECTORY}/", *_inside(root, runs_root)]
    for status, path in entries:
        if any(path.startswith(prefix) for prefix in churn):
            continue
        file = root / path
        try:
            content = (
                hashlib.sha256(file.read_bytes()).hexdigest() if file.is_file() else "absent"
            )
        except OSError:
            content = "unreadable"
        states[path] = f"{status}:{content}"
    return digest_states(states)


def _inside(root: Path, directory: Path | None) -> list[str]:
    """The prefix `directory` has under `root`, or nothing where it lies outside it."""
    if directory is None:
        return []
    try:
        relative = directory.resolve().relative_to(root.resolve())
    except ValueError:
        return []
    return [f"{relative.as_posix()}/"]


def assertion_report(
    directory: Path, step_id: str, run_id: str, *, prefix: str = VERIFY_PREFIX
) -> dict[str, Any] | None:
    """The account an assertion's gate left for this step, or None where it left none.

    `prefix` names which execution: the step's assertion, or a remedied step's second one.
    """
    try:
        return read_step_report(directory, f"{prefix}{step_id}", run_id)
    except CairnError as exc:
        if exc.cause == "missing_report":
            return None
        raise


def _detail(report: dict[str, Any] | None) -> dict[str, Any]:
    if report is None:
        return {}
    found: Any = report.get("detail")
    return cast(dict[str, Any], found) if isinstance(found, dict) else {}


def decision_of(report: dict[str, Any] | None) -> str | None:
    """The gate's decision, or None where the report carries none this module minted."""
    found = _detail(report).get(DECISION_KEY)
    return found if isinstance(found, str) and found in DECISIONS else None


def remedy_ran(directory: Path, step_id: str, run_id: str) -> bool:
    """Whether a remedy session ran for this step and reported its work done."""
    try:
        report = read_step_report(directory, f"{REMEDY_PREFIX}{step_id}", run_id)
    except CairnError:
        return False
    return report.get("status") == "done" and not report.get("needs_user_decision")


def recorded_exit(report: dict[str, Any] | None) -> int | None:
    """The exit an executed assertion's account was already completed with, if it was."""
    detail = _detail(report)
    if detail.get(SOURCE_KEY) != ASSERTION_EXECUTED:
        return None
    return shared_exit(cast(dict[str, Any], report))


def shared_exit(report: dict[str, Any]) -> int | None:
    found = _detail(report).get(EXIT_KEY)
    return found if isinstance(found, int) and not isinstance(found, bool) else None


def _read_shared(path: Path) -> dict[str, Any] | None:
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return cast(dict[str, Any], raw) if isinstance(raw, dict) else None


def _decide(
    context: RuntimeContext,
    step_id: str,
    digest: str,
    bound: int | None,
    *,
    after_remedy: bool = False,
) -> tuple[int, CommandResult]:
    detail: dict[str, Any] = {
        DECISION_KEY: DECISION_RUN,
        COMMAND_KEY: digest,
        TREE_KEY: None,
        BOUND_KEY: bound,
    }
    if after_remedy and not remedy_ran(context.report_path.parent, step_id, context.run_id):
        detail[DECISION_KEY] = DECISION_NOT_REMEDIED
        return NEEDED_SKIP_IT, CommandResult(
            NEEDED_SKIP_IT,
            "noop",
            "no remedy session reported changing anything, so there is nothing new to assert",
            [],
            False,
            None,
            detail,
        )
    try:
        read_step_report(context.report_path.parent, f"{WORK_PREFIX}{step_id}", context.run_id)
    except CairnError as exc:
        if exc.cause == "missing_report":
            detail[DECISION_KEY] = DECISION_SKIPPED_UPSTREAM
            return NEEDED_SKIP_IT, CommandResult(
                NEEDED_SKIP_IT,
                "noop",
                "the step left no report of this run, so its assertion has nothing to assert",
                [],
                False,
                None,
                detail,
            )
        # An account the gate cannot read is the mark gate's to refuse; the assertion still
        # runs, because running one that need not run is the cheap mistake.
    tree = tree_digest(context.working_directory, runs_root=context.runs_root)
    detail[TREE_KEY] = tree
    proof = (
        _read_shared(assertion_result_path(context.runs_root, context.run_id, digest))
        if tree is not None
        else None
    )
    if (
        proof is not None
        and proof.get(COMMAND_KEY) == digest
        and proof.get(TREE_KEY) == tree
        and proof.get("run_id") == context.run_id
        and isinstance(proof.get(EXIT_KEY), int)
        and not isinstance(proof.get(EXIT_KEY), bool)
        and isinstance(proof.get("step_id"), str)
    ):
        detail.update(
            {
                DECISION_KEY: DECISION_SHARED,
                EXIT_KEY: proof[EXIT_KEY],
                SOURCE_KEY: ASSERTION_SHARED,
                BACKED_BY_KEY: proof["step_id"],
            }
        )
        return NEEDED_SKIP_IT, CommandResult(
            NEEDED_SKIP_IT,
            "noop",
            f"the assertion was already proven against this tree by {proof['step_id']}, "
            f"exiting {proof[EXIT_KEY]}",
            [],
            False,
            None,
            detail,
        )
    detail[RELEASED_AT_KEY] = time.time()
    return NEEDED_RUN_IT, CommandResult(
        NEEDED_RUN_IT, "done", "the assertion runs", [], False, None, detail
    )


def needed_main(arguments: list[str]) -> int:
    """Answer whether this step's assertion has anything to assert. Exit 0 means run it.

    Every error exits 0, and the decision is written before the answer is given: a
    precondition that exited nonzero on a crash would skip the assertion, and the engine
    would then hand the mark gate a `0` for it.
    """
    started = time.monotonic()
    parser = argparse.ArgumentParser(prog=f"cairn verify {NEEDED_VERB}", add_help=False)
    parser.add_argument("--step", required=True)
    parser.add_argument("--command-digest", dest="command_digest", required=True)
    parser.add_argument("--bound", type=int, default=None)
    parser.add_argument("--after-remedy", dest="after_remedy", action="store_true")
    try:
        args = parser.parse_args(arguments)
        context = RuntimeContext.from_env()
        answer, result = _decide(
            context,
            str(args.step),
            str(args.command_digest),
            args.bound,
            after_remedy=bool(args.after_remedy),
        )
        with survive_termination():
            write_report(context, result, time.monotonic() - started)
        print(
            f"assertion gate [{args.step}]: {result.detail[DECISION_KEY]} — {result.summary}",
            file=sys.stderr,
        )
        return answer
    except SystemExit:
        print(f"assertion gate rejected its own arguments: {arguments}", file=sys.stderr)
        return NEEDED_RUN_IT
    except Exception as exc:  # noqa: BLE001 - see the docstring: every fault runs the assertion
        traceback.print_exc()
        print(f"assertion gate: {exc}; running the assertion", file=sys.stderr)
        return NEEDED_RUN_IT


def _remedy_decision(
    context: RuntimeContext, step_id: str, verify_exit_text: str
) -> tuple[int, str]:
    """Whether a remedy session is worth opening, and why, in one sentence.

    Opened only over an assertion that ran — or shared a proof — and exited nonzero, behind
    a step that reported its work done or already done. Everything else is something a
    session cannot fix: a pass, an assertion that never ran, one a signal ended, a step that
    vetoed itself or is waiting on a person.
    """
    directory = context.report_path.parent
    account = assertion_report(directory, step_id, context.run_id)
    decision = decision_of(account)
    if decision == DECISION_SHARED and account is not None:
        exit_code = shared_exit(account)
    elif decision == DECISION_RUN and account is not None:
        exit_code = recorded_exit(account)
        if exit_code is None:
            exit_code = int(verify_exit_text)
            record_executed(context, step_id, account, exit_code)
    else:
        return REMEDY_DECLINE_IT, "the assertion did not run, so there is nothing to remedy"
    if exit_code is None:
        return REMEDY_DECLINE_IT, "the assertion's account carries no exit status"
    if exit_code == 0:
        return REMEDY_DECLINE_IT, "the assertion passed"
    if exit_code == SIGNALLED_EXIT:
        return REMEDY_DECLINE_IT, (
            "a signal ended the assertion before it exited, which no change to the work "
            "can fix"
        )
    try:
        work = read_step_report(directory, f"{WORK_PREFIX}{step_id}", context.run_id)
    except CairnError as exc:
        return REMEDY_DECLINE_IT, f"the step's own report cannot be read: {exc}"
    if work.get("needs_user_decision"):
        return REMEDY_DECLINE_IT, "the step is waiting on a person's decision"
    if work.get("status") not in ("done", "noop"):
        return REMEDY_DECLINE_IT, "the step reported failure, which a remedy may not overrule"
    return REMEDY_OPEN_IT, f"the assertion exited {exit_code} over work the step reported"


class RemedyBrief(NamedTuple):
    """What a remedy session is told, and which session it continues."""

    exit_code: int
    said: str
    resume_session: str | None


def remedy_brief(context: RuntimeContext, step_id: str) -> RemedyBrief:
    """Read back what the remedy gate read: the assertion's exit and the step's account.

    The session resumed is the one the step's own report names. A report that names none
    leaves a fresh session to do the remedy, told everything the brief holds.
    """
    directory = context.report_path.parent
    account = assertion_report(directory, step_id, context.run_id)
    exit_code = recorded_exit(account)
    if exit_code is None and decision_of(account) == DECISION_SHARED and account is not None:
        exit_code = shared_exit(account)
    if exit_code is None:
        raise CairnError(
            "invalid_arguments",
            f"step {step_id!r} has no assertion exit a remedy could answer",
        )
    work = read_step_report(directory, f"{WORK_PREFIX}{step_id}", context.run_id)
    detail = _detail(work)
    session = detail.get("session_id")
    return RemedyBrief(
        exit_code,
        str(work.get("summary") or ""),
        session if isinstance(session, str) and session else None,
    )


def remedy_main(arguments: list[str]) -> int:
    """Answer whether a remedied step's remedy session opens. Exit 0 means it does.

    The precondition of the remedy node. A decline is written as the remedy node's own
    `noop` report, because the node it gates will not run to write one; an opening writes
    nothing, because the session writes its own. Every fault declines.
    """
    started = time.monotonic()
    parser = argparse.ArgumentParser(prog=f"cairn verify {REMEDY_VERB}", add_help=False)
    parser.add_argument("--step", required=True)
    parser.add_argument("--verify-exit", dest="verify_exit", required=True)
    try:
        args = parser.parse_args(arguments)
        context = RuntimeContext.from_env()
        answer, reason = _remedy_decision(context, str(args.step), str(args.verify_exit))
    except SystemExit:
        print(f"remedy gate rejected its own arguments: {arguments}", file=sys.stderr)
        return REMEDY_DECLINE_IT
    except Exception as exc:  # noqa: BLE001 - see the docstring: every fault declines
        traceback.print_exc()
        print(f"remedy gate: {exc}; no remedy session opens", file=sys.stderr)
        return REMEDY_DECLINE_IT
    print(f"remedy gate [{args.step}]: {reason}", file=sys.stderr)
    if answer == REMEDY_DECLINE_IT:
        with survive_termination():
            write_report(
                context,
                CommandResult(EXIT_OK, "noop", reason, [], False, None, {}),
                time.monotonic() - started,
            )
    return answer


def _standing_failure(standing: dict[str, Any] | None, command: str, tree: str) -> bool:
    """Whether a failure for this command against this tree is already filed."""
    if standing is None:
        return False
    exit_code = standing.get(EXIT_KEY)
    return (
        standing.get(COMMAND_KEY) == command
        and standing.get(TREE_KEY) == tree
        and isinstance(exit_code, int)
        and not isinstance(exit_code, bool)
        and exit_code != 0
    )


def _publish(
    context: RuntimeContext, step_id: str, command: str, tree: str, exit_code: int
) -> None:
    """File this execution as the proof every later gate quoting the command may share.

    **Publication is a critical section, keyed by the proof.** Two steps quoting one
    command execute it concurrently, each having asked before either filed, so both can
    find no standing proof — and whichever writes last would decide what every later gate
    reads. So the key is locked, the standing result is re-read inside the lock, and
    failure is dominant: a filed failure for this command against this tree is never
    replaced by a pass, in either arrival order. Sharing never widens what passes.

    The proof is replaced in one step, so a gate reading it concurrently sees the whole of
    one result or none ([core.write_text]), and the lock is one the kernel drops when its
    holder dies — a writer killed mid-publication leaves the key free and no proof it did
    not finish writing.

    A lock that cannot be taken publishes nothing, which is the one safe way to be without
    one: an absent proof costs the next gate quoting this command its own execution, while
    a write outside the critical section would cost a failure its dominance.
    """
    path = assertion_result_path(context.runs_root, context.run_id, command)
    try:
        with exclusive_lock(
            assertion_lock_path(context.runs_root, context.run_id, command),
            wait_seconds=PUBLICATION_WAIT_SECONDS,
            cause=PROOF_LOCK_UNAVAILABLE,
            subject=f"the proof of assertion command {command[:12]}",
        ):
            if _standing_failure(_read_shared(path), command, tree):
                return
            write_json(
                path,
                {
                    COMMAND_KEY: command,
                    TREE_KEY: tree,
                    EXIT_KEY: exit_code,
                    "step_id": step_id,
                    "run_id": context.run_id,
                },
            )
    except CairnError as exc:
        print(f"assertion proof [{step_id}]: {exc}; nothing published", file=sys.stderr)


def record_executed(
    context: RuntimeContext,
    step_id: str,
    report: dict[str, Any],
    exit_code: int,
    *,
    prefix: str = VERIFY_PREFIX,
) -> None:
    """Complete an assertion's account with the exit it produced, and file it as a proof.

    Written by the mark gate, under the assertion node's name, because the assertion runs
    bare and cannot write anything itself. The tree digest is the one the assertion's gate
    took before the assertion ran — never recomputed here, where the assertion may already
    have changed what it read. A proof against a tree git would not digest is filed
    nowhere: nothing may be shared against it.
    """
    detail = {
        **_detail(report),
        EXIT_KEY: exit_code,
        SOURCE_KEY: ASSERTION_EXECUTED,
        BACKED_BY_KEY: step_id,
    }
    released = detail.get(RELEASED_AT_KEY)
    duration = (
        max(0.0, time.time() - released)
        if isinstance(released, (int, float)) and not isinstance(released, bool)
        else 0.0
    )
    completed = CommandResult(
        exit_code,
        "done" if exit_code == 0 else "failed",
        _account(exit_code, duration, detail.get(BOUND_KEY)),
        [],
        False,
        None,
        detail,
    )
    with survive_termination():
        write_report_for(context, f"{prefix}{step_id}", completed, duration)
        tree = detail.get(TREE_KEY)
        command = detail.get(COMMAND_KEY)
        # An assertion a signal ended decided nothing, so it is no proof to share: filed,
        # its failure would dominate and close every later gate quoting the command.
        if exit_code != SIGNALLED_EXIT and isinstance(tree, str) and isinstance(command, str):
            _publish(context, step_id, command, tree, exit_code)


def _account(exit_code: int, duration: float, bound: Any) -> str:
    if exit_code != SIGNALLED_EXIT:
        return f"the assertion exited {exit_code}"
    if (
        isinstance(bound, int)
        and not isinstance(bound, bool)
        and duration >= bound - BOUND_SLACK_SECONDS
    ):
        return (
            f"the assertion was stopped at its {bound} s bound before it exited; "
            "the plan's verify_timeout is smaller than the assertion needs"
        )
    return f"the assertion was ended by a signal after {duration:.0f}s, before it exited"


__all__ = [
    "ASSERTION_EXECUTED",
    "ASSERTION_SHARED",
    "ASSERTION_SOURCES",
    "BACKED_BY_KEY",
    "BOUND_KEY",
    "COMMAND_KEY",
    "DECISIONS",
    "DECISION_KEY",
    "DECISION_NOT_REMEDIED",
    "DECISION_RUN",
    "DECISION_SHARED",
    "DECISION_SKIPPED_UPSTREAM",
    "EXIT_KEY",
    "NEEDED_RUN_IT",
    "NEEDED_SKIP_IT",
    "NEEDED_VERB",
    "PROOF_LOCK_UNAVAILABLE",
    "PUBLICATION_WAIT_SECONDS",
    "RELEASED_AT_KEY",
    "REMEDY_DECLINE_IT",
    "REMEDY_OPEN_IT",
    "REMEDY_VERB",
    "SIGNALLED_EXIT",
    "SOURCE_KEY",
    "TREE_KEY",
    "RemedyBrief",
    "assertion_report",
    "command_digest",
    "decision_of",
    "needed_main",
    "record_executed",
    "recorded_exit",
    "remedy_brief",
    "remedy_main",
    "remedy_ran",
    "shared_exit",
    "tree_digest",
]
