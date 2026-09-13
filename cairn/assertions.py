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
from typing import Any, cast

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
from cairn.layout import MARKER_DIRECTORY, assertion_result_path
from cairn.plan.schema import VERIFY_PREFIX, WORK_PREFIX

NEEDED_VERB = "needed"

# The precondition's two answers, named for what the engine does with them.
NEEDED_RUN_IT = EXIT_OK
NEEDED_SKIP_IT = EXIT_FAILED

# What the gate decided, recorded in the assertion node's own report. Frozen: the mark gate
# turns on these words and a spelling outside the set is a report it cannot read.
DECISION_RUN = "run"
DECISION_SHARED = "shared"
DECISION_SKIPPED_UPSTREAM = "skipped_upstream"
DECISIONS: tuple[str, ...] = (DECISION_RUN, DECISION_SHARED, DECISION_SKIPPED_UPSTREAM)

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
    directory: Path, step_id: str, run_id: str
) -> dict[str, Any] | None:
    """The account the assertion's gate left for this step, or None where it left none."""
    try:
        return read_step_report(directory, f"{VERIFY_PREFIX}{step_id}", run_id)
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
    context: RuntimeContext, step_id: str, digest: str
) -> tuple[int, CommandResult]:
    detail: dict[str, Any] = {DECISION_KEY: DECISION_RUN, COMMAND_KEY: digest, TREE_KEY: None}
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
    try:
        args = parser.parse_args(arguments)
        context = RuntimeContext.from_env()
        answer, result = _decide(context, str(args.step), str(args.command_digest))
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


def record_executed(
    context: RuntimeContext, step_id: str, report: dict[str, Any], exit_code: int
) -> None:
    """Complete an assertion's account with the exit it produced, and file it as a proof.

    Written by the mark gate, under the assertion node's name, because the assertion runs
    bare and cannot write anything itself. The tree digest is the one the assertion's gate
    took before the assertion ran — never recomputed here, where the assertion may already
    have changed what it read. A proof against a tree git would not digest is filed
    nowhere: nothing may be shared against it.

    A failing proof already filed for the same command against the same tree is left
    standing. Two steps quoting one command can execute it concurrently — each missing the
    other's proof — and a pass written over a failure would reopen every gate the failure
    closed. Sharing never widens what passes.
    """
    detail = {
        **_detail(report),
        EXIT_KEY: exit_code,
        SOURCE_KEY: ASSERTION_EXECUTED,
        BACKED_BY_KEY: step_id,
    }
    completed = CommandResult(
        exit_code,
        "done" if exit_code == 0 else "failed",
        f"the assertion exited {exit_code}",
        [],
        False,
        None,
        detail,
    )
    duration = report.get("duration")
    with survive_termination():
        write_report_for(
            context,
            f"{VERIFY_PREFIX}{step_id}",
            completed,
            float(duration) if isinstance(duration, (int, float)) else 0.0,
        )
        tree = detail.get(TREE_KEY)
        command = detail.get(COMMAND_KEY)
        if isinstance(tree, str) and isinstance(command, str):
            path = assertion_result_path(context.runs_root, context.run_id, command)
            standing = _read_shared(path)
            if (
                standing is not None
                and standing.get(COMMAND_KEY) == command
                and standing.get(TREE_KEY) == tree
                and standing.get(EXIT_KEY) not in (0, None)
            ):
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


__all__ = [
    "ASSERTION_EXECUTED",
    "ASSERTION_SHARED",
    "ASSERTION_SOURCES",
    "BACKED_BY_KEY",
    "COMMAND_KEY",
    "DECISIONS",
    "DECISION_KEY",
    "DECISION_RUN",
    "DECISION_SHARED",
    "DECISION_SKIPPED_UPSTREAM",
    "EXIT_KEY",
    "NEEDED_RUN_IT",
    "NEEDED_SKIP_IT",
    "NEEDED_VERB",
    "SOURCE_KEY",
    "TREE_KEY",
    "assertion_report",
    "command_digest",
    "decision_of",
    "needed_main",
    "record_executed",
    "shared_exit",
    "tree_digest",
]
