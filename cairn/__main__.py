"""Cairn's internal command line.

Runtime subcommands are invoked by emitted engine steps and self-identify from the
engine's environment. `plan` and `supervise` run outside any run, take no identity and
leave no report.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
from collections.abc import Callable
from pathlib import Path

from cairn.baseconfig import (
    assert_dag_retry_disabled,
    base_config_path,
)
from cairn.commands import run_exec, run_wait_duration, run_wait_until
from cairn.core import (
    EXIT_FAILED,
    EXIT_OK,
    CairnError,
    Cancelled,
    CommandResult,
    RuntimeContext,
    cancel_on_termination,
    stop_orphans,
    survive_termination,
    sweep_stale_reports,
    write_report,
)
from cairn.enginehome import run_records_path
from cairn.gitio import refuse_unusable_repository
from cairn.hooks import HOOK_VERB, hook_main
from cairn.locks import (
    acquire_run_lock,
    describe_holder,
    git_write_mutex,
    refuse_dirty_repository,
    refuse_lost_repository,
    refuse_unresolved_merge,
    release_run_lock,
)
from cairn.marker import (
    current_key,
    is_fresh,
    mint_occasion,
    read_marker,
    resolve_occasion,
    run_marker_write,
)
from cairn.merge import run_merge, verify_landed
from cairn.parameters import parent_branch, refuse_misfiled_records
from cairn.parameters import repository as declared_repository
from cairn.plan.cli import main as plan_main
from cairn.plan.schema import SCOPES
from cairn.providers import run_provider
from cairn.record.cli import main as record_main
from cairn.record.store import build_run_record, write_record
from cairn.report.cli import main as report_main
from cairn.schedule_cli import main as schedule_main
from cairn.skill.cli import explain_main, run_main
from cairn.supervise import find_run_record
from cairn.supervise_cli import main as supervise_main
from cairn.topology import BRANCH_PREFIX, worktrees_root_for
from cairn.verify import gate_main as verify_gate_main
from cairn.wave import run_join
from cairn.workflow.cli import main as workflow_main
from cairn.workflow.stamp import read_stamp, workflow_path
from cairn.worktrees import commit_all, prune_worktrees, setup_worktree

Handler = Callable[[argparse.Namespace, RuntimeContext], CommandResult]
DEFAULT_SHELL = "/bin/sh"


GATE_VERB = "absent"
# The gate answers a precondition, where the engine reads success as "run the step". So
# its two outcomes are named for what the engine does with them, not for a step's fate.
GATE_WORK_PENDING = EXIT_OK
GATE_MARKER_FRESH = EXIT_FAILED


def _exec(args: argparse.Namespace, context: RuntimeContext) -> CommandResult:
    refuse_lost_repository(context.working_directory, context.run_id)
    return run_exec(args.command, context.working_directory, args.shell)


def _wait(args: argparse.Namespace, context: RuntimeContext) -> CommandResult:
    if args.duration is not None:
        return run_wait_duration(args.duration, args.timeout)
    return run_wait_until(
        args.until,
        context.working_directory,
        args.shell,
        args.timeout,
        args.interval,
    )


def _agent(args: argparse.Namespace, context: RuntimeContext) -> CommandResult:
    # The cheapest moment this run can discover it no longer owns the repository: one ref
    # read, against a session that is about to cost an hour of paid time.
    refuse_lost_repository(context.working_directory, context.run_id)
    return run_provider(
        args.provider,
        args.prompt,
        context.working_directory,
        "auto",
        args.model,
        args.max_budget_usd,
        args.tool or [],
    )


def _marker(args: argparse.Namespace, context: RuntimeContext) -> CommandResult:
    return run_marker_write(
        args.step, args.scope, args.reads or [], context, os.environ
    )


def _lock(args: argparse.Namespace, context: RuntimeContext) -> CommandResult:
    repository = context.working_directory
    if args.lock_command == "release":
        # The record is left however the release itself goes. A release that could not give
        # the lock back is exactly the run a person needs the record of — and the record has
        # to say so, because the engine's own node for this handler is not written until
        # after it returns.
        failure: CairnError | None = None
        record = None
        try:
            with git_write_mutex(repository):
                record = release_run_lock(repository, run_id=context.run_id)
        except CairnError as exc:
            failure = exc
        except BaseException:
            # The record is what this handler exists to leave, and it cannot reach the exit
            # status — so it is written for a cancellation or a bare OSError exactly as for
            # a cause Cairn named. Only the cause is conditional; the record is not.
            record_the_run(context)
            raise
        closing = record_the_run(context, failure)
        if failure is not None:
            raise failure
        if record is None:
            # The release runs however the run ends, including on a run that never took
            # the lock, so having nothing to give back is an outcome and not a failure.
            return CommandResult(
                EXIT_OK, "noop", "no run lock to release", closing, False, None, {}
            )
        return CommandResult(
            EXIT_OK,
            "done",
            f"released {record['repository']}",
            closing,
            False,
            None,
            {"released": dict(record)},
        )
    # Checked before the run's first spend rather than trusted: a scheduler on this machine
    # would re-execute every failed run of the last day, Cairn's or not (09).
    assert_dag_retry_disabled(
        Path(args.base_config) if args.base_config else base_config_path()
    )
    # Every value a caller varied, judged here because this is the only node downstream of
    # all four trigger surfaces — the view's start dialog, `dagu start --params`, a cron
    # firing and a webhook — and Cairn owns none of them ([parameters.py]).
    declared_repository(repository)
    refuse_misfiled_records(repository)
    parent_branch(repository)
    refuse_unusable_repository(repository)
    refuse_unresolved_merge(repository)
    refuse_dirty_repository(repository)
    # Seeded at the run's first act so every later gate reads one value rather than racing
    # to mint its own, and so the record can name the occasion even for a plan whose steps
    # never read one.
    occasion = resolve_occasion(context.runs_root, context.run_id, os.environ)
    record = find_run_record(run_records_path(), context.run_id)
    with git_write_mutex(repository):
        held = acquire_run_lock(
            repository,
            run_id=context.run_id,
            plan=args.plan,
            run_timeout_seconds=args.run_timeout,
            status_file=str(record) if record is not None else None,
        )
    follow_up = (
        [f"reclaimed the lock from {describe_holder(held.reclaimed_from)}"]
        if held.reclaimed_from is not None
        else []
    )
    return CommandResult(
        EXIT_OK,
        "done",
        f"acquired {held.record['repository']} for run {context.run_id}",
        follow_up,
        False,
        None,
        {
            "lock": dict(held.record),
            "object_id": held.object_id,
            # The occasion every scoped step in this run keys on, recorded here because the
            # declared parameter is empty whenever the run minted its own.
            "occasion": occasion,
            # Which plan content this run is executing, read at the run's first act — the
            # one moment the file on disk is certainly the file that is running. Read at
            # record time instead it would name whatever has been re-authored since.
            **_authored_plan(repository, args.plan),
        },
    )


def record_the_run(
    context: RuntimeContext, failure: CairnError | None = None
) -> list[str]:
    """Leave the run's record where a person will find it, whatever else happened.

    A run nobody watched is the case the record exists for, and the exit handler is the only
    body that runs however the run ends — a node whose dependency failed is never dispatched,
    so a report node could not cover the failure path at all.

    **Nothing here may reach the exit status, and that is the whole design constraint.**
    Measured against Dagu 2.11.0: a `handler_on.exit` body exiting nonzero records the whole
    run as `failed` and makes `dagu start` exit 1 even when every step succeeded — and
    because this node is load-bearing infrastructure in the record, Cairn's own verdict flips
    with it. A failure to write the record would then be reported as the run having failed,
    on the one path nobody is watching. So the release exits nonzero for exactly one reason,
    which is that it could not give the lock back, and everything learned here rides in the
    report instead.

    The record written here is honest but not final: measured, the last state line already
    carries the run's final status and every step node final, while the run's own finish time
    and this handler's node are not yet recorded. It is regenerable, and a later
    `cairn record build` supersedes it ([run-model.md]).
    """
    try:
        record = build_run_record(
            context.runs_root,
            run_records_path(),
            context.run_id,
            in_flight_node=context.step_id,
            in_flight_cause=None if failure is None else failure.cause,
        )
        if record is None:
            return ["no run record could be built: neither the engine nor Cairn holds one"]
        path = write_record(context.runs_root, record)
        return [f"recorded {record['verdict']} at {path}"]
    except Exception as exc:  # noqa: BLE001 — see the docstring: nothing may reach the status
        return [f"the run's record could not be written: {exc}"]


def _authored_plan(repository: Path, plan: str) -> dict[str, str]:
    """The plan digest the running workflow was generated from, where Cairn still has it."""
    stamp = read_stamp(workflow_path(repository, plan))
    if stamp is None:
        return {}
    return {"plan": plan, "graph_sha256": stamp["graph_sha256"]}


def _worktree(args: argparse.Namespace, context: RuntimeContext) -> CommandResult:
    """Derive every worktree path from the repository this step already stands in.

    The paths are not carried in the body. A worktree lives beside the repository, so its
    path names one target, and a generated workflow that named one could not be pointed at
    another ([workflow/schema.py]).
    """
    refuse_lost_repository(context.working_directory, context.run_id)
    root = worktrees_root_for(context.working_directory, args.plan)
    if args.worktree_command == "prune":
        steps: list[str] = list(args.step or [])
        return prune_worktrees(
            context.working_directory,
            [str(root / step_id) for step_id in steps],
            [f"{BRANCH_PREFIX}{step_id}" for step_id in steps],
            parent=parent_branch(),
            force=args.force,
        )
    return setup_worktree(
        context.working_directory,
        root / args.step,
        args.branch,
        parent_branch(),
    )


def _commit(args: argparse.Namespace, context: RuntimeContext) -> CommandResult:
    refuse_lost_repository(context.working_directory, context.run_id)
    return commit_all(context.working_directory, args.message)


def _merge(args: argparse.Namespace, context: RuntimeContext) -> CommandResult:
    refuse_lost_repository(context.working_directory, context.run_id)
    if args.merge_command == "verify":
        return verify_landed(
            context.working_directory,
            merge=args.merge,
            into=parent_branch(),
            candidates=args.branch,
            context=context,
        )
    return run_merge(
        context.working_directory,
        slot=args.slot,
        into=parent_branch(),
        candidates=args.branch,
        provider=args.provider,
        model=args.model,
        max_budget_usd=args.max_budget_usd,
        context=context,
    )


def _wave(args: argparse.Namespace, context: RuntimeContext) -> CommandResult:
    refuse_lost_repository(context.working_directory, context.run_id)
    return run_join(
        args.wave, args.branch, parent_branch(), context
    )


COMMAND_HANDLERS: dict[str, Handler] = {
    "exec": _exec,
    "wait": _wait,
    "agent": _agent,
    "marker": _marker,
    "lock": _lock,
    "worktree": _worktree,
    "commit": _commit,
    "merge": _merge,
    "wave": _wave,
}


def _add_freshness_arguments(child: argparse.ArgumentParser) -> None:
    child.add_argument("--step", required=True)
    child.add_argument("--scope", choices=SCOPES, required=True)
    child.add_argument("--reads", action="append")


def _parser(*, add_help: bool = True) -> argparse.ArgumentParser:
    """The whole command line. Dispatch builds it without help, and see `asks_for_help`."""
    parser = argparse.ArgumentParser(
        prog="cairn",
        description=__doc__,
        add_help=add_help,
        epilog=(
            "Twelve more commands are dispatched before these and take no step identity: "
            "`cairn plan`, `cairn occasion new`, `cairn marker absent`, "
            "`cairn verify gate`, `cairn supervise`, `cairn schedule`, "
            "`cairn workflow`, `cairn record`, `cairn report`, `cairn run`, "
            "`cairn explain` and `cairn hook stop`. Each has its own --help. None of them "
            "is the surface: type `/cairn` and say what you want, and the skill runs "
            "these for you."
        ),
    )
    subcommands = parser.add_subparsers(dest="command_name", required=True)

    child = subcommands.add_parser("exec", add_help=add_help)
    child.add_argument("--command", required=True)
    child.add_argument("--shell", default=DEFAULT_SHELL)

    child = subcommands.add_parser("wait", add_help=add_help)
    form = child.add_mutually_exclusive_group(required=True)
    form.add_argument("--until")
    form.add_argument("--for", dest="duration", type=float)
    child.add_argument("--timeout", type=float, required=True)
    child.add_argument("--interval", type=float, default=1.0)
    child.add_argument("--shell", default=DEFAULT_SHELL)

    agent = subcommands.add_parser("agent", add_help=add_help)
    agent_subcommands = agent.add_subparsers(dest="agent_command", required=True)
    child = agent_subcommands.add_parser("run", add_help=add_help)
    child.add_argument("--provider", required=True)
    child.add_argument("--prompt", required=True)
    child.add_argument("--model")
    child.add_argument("--max-budget-usd", type=float)
    child.add_argument("--tool", action="append")

    # `marker absent` is the precondition and is deliberately absent from this parser: it
    # runs before a step starts rather than as one, so it never reaches this dispatch.
    marker = subcommands.add_parser("marker", add_help=add_help)
    marker_subcommands = marker.add_subparsers(dest="marker_command", required=True)
    child = marker_subcommands.add_parser("write", add_help=add_help)
    _add_freshness_arguments(child)

    lock = subcommands.add_parser("lock", add_help=add_help)
    lock_subcommands = lock.add_subparsers(dest="lock_command", required=True)
    child = lock_subcommands.add_parser("acquire", add_help=add_help)
    child.add_argument("--plan", required=True)
    child.add_argument("--run-timeout", type=int, required=True)
    child.add_argument("--base-config")
    lock_subcommands.add_parser("release", add_help=add_help)

    # Neither verb takes a worktree path. A path names one repository, and the whole point
    # of the generated file's parameters is that it names none — so the plan and the step
    # are given instead and the path is derived from the repository the step stands in.
    worktree = subcommands.add_parser("worktree", add_help=add_help)
    worktree_subcommands = worktree.add_subparsers(dest="worktree_command", required=True)
    child = worktree_subcommands.add_parser("setup", add_help=add_help)
    child.add_argument("--plan", required=True)
    child.add_argument("--step", required=True)
    child.add_argument("--branch", required=True)
    child = worktree_subcommands.add_parser("prune", add_help=add_help)
    child.add_argument("--plan", required=True)
    child.add_argument("--step", action="append")
    child.add_argument("--force", action="store_true")

    child = subcommands.add_parser("commit", add_help=add_help)
    child.add_argument("--message", required=True)

    merge = subcommands.add_parser("merge", add_help=add_help)
    merge_subcommands = merge.add_subparsers(dest="merge_command", required=True)
    # Every slot is given the whole candidate list. Which of them it lands is its own
    # decision on evidence that does not exist until run time, so the emitter cannot pin
    # one branch per slot without deleting the ordering the prediction exists to advise.
    child = merge_subcommands.add_parser("land", add_help=add_help)
    child.add_argument("--slot", type=int, required=True)
    child.add_argument("--branch", action="append", required=True)
    child.add_argument("--provider", required=True)
    # A resolution is a paid session like any other, and until these existed it was the one
    # session in a run that no caller could price or choose a model for. The emitter writes
    # neither, so a generated workflow is unchanged; what they buy is a resolver that can be
    # bounded by whoever is paying for it.
    child.add_argument("--model")
    child.add_argument("--max-budget-usd", type=float)
    child = merge_subcommands.add_parser("verify", add_help=add_help)
    child.add_argument("--merge", required=True)
    child.add_argument("--branch", action="append", required=True)

    wave = subcommands.add_parser("wave", add_help=add_help)
    wave_subcommands = wave.add_subparsers(dest="wave_command", required=True)
    child = wave_subcommands.add_parser("join", add_help=add_help)
    child.add_argument("--wave", type=int, required=True)
    child.add_argument("--branch", action="append", required=True)

    return parser


def _dispatch(arguments: list[str], context: RuntimeContext) -> CommandResult:
    try:
        # Built without help, so this parser has exactly one way to stop: rejecting the
        # arguments. A parser that could also answer a help flag would exit zero here,
        # printing usage over a step that never ran and leaving the engine to read the
        # zero as success — with any earlier run's report still standing.
        args = _parser(add_help=False).parse_args(arguments)
    except SystemExit as exc:
        # Argument skew between an emitted workflow and an upgraded binary is a routing
        # fact, not a usage message: it has to survive as a report like any other cause.
        raise CairnError(
            "invalid_arguments", f"cairn rejected its own arguments: {arguments}"
        ) from exc
    handler = COMMAND_HANDLERS[args.command_name]
    try:
        return handler(args, context)
    except (KeyboardInterrupt, Cancelled) as exc:
        stop_orphans()
        raise CairnError("cancelled", f"{args.command_name} cancelled") from exc


def asks_for_help(arguments: list[str]) -> bool:
    """Whether these arguments are a help request rather than a step to run.

    This is the only thing that answers a help flag: the dispatch parser is built without
    one, so nothing else can exit zero over a step that never ran. A request counts only
    as a leading run of subcommand names ending in the flag, because a step's own argument
    values are operands — a task whose text happens to be `--help` must run the step.
    """
    for argument in arguments:
        if argument in ("-h", "--help"):
            return True
        if argument.startswith("-"):
            return False
    return False


def occasion_main(arguments: list[str]) -> int:
    """Mint the identity of one real occasion of running a plan.

    Whether an invocation is a new occasion or the continuation of an existing one is the
    operator's call, so minting is a command rather than something a run decides for
    itself.
    """
    parser = argparse.ArgumentParser(prog="cairn occasion")
    parser.add_subparsers(dest="occasion_command", required=True).add_parser("new")
    parser.parse_args(arguments)
    print(mint_occasion())
    return EXIT_OK


def gate_main(arguments: list[str]) -> int:
    """Answer whether a step's work still has to happen. Exit 0 means it does.

    This is a precondition rather than a step: it runs before the step starts and decides
    whether the step starts at all. Two consequences shape it. It writes a report only on
    the path where no step will run to write one — a report on the other path would
    outlive a step that was then killed, and claim an outcome for work that never
    happened. And every error exits 0, because a gate that cannot tell whether the work is
    done must let the work happen: the task is convergent and its end state is asserted
    either way, whereas skipping unverified work leaves nothing to catch it.
    """
    started = time.monotonic()
    parser = argparse.ArgumentParser(
        prog="cairn marker",
        epilog="`cairn marker write` records a verified step; see its own --help.",
    )
    parser.add_argument("verb", choices=(GATE_VERB,))
    _add_freshness_arguments(parser)
    step = scope = "?"
    try:
        args = parser.parse_args(arguments)
        step, scope = args.step, args.scope
        context = RuntimeContext.from_env()
        marker = read_marker(context.working_directory, step)
        if marker is None:
            return GATE_WORK_PENDING
        key = current_key(
            scope,
            root=context.working_directory,
            reads=args.reads or [],
            environment=os.environ,
            runs_root=context.runs_root,
            run_id=context.run_id,
        )
        if not is_fresh(marker, scope, key):
            return GATE_WORK_PENDING
        result = CommandResult(
            GATE_MARKER_FRESH,
            "noop",
            marker["summary"],
            [],
            False,
            None,
            {
                "scope": scope,
                "key": key,
                "recorded_scope": marker["scope"],
                "recorded_key": marker["key"],
                # Which run did the work this one is skipping. Without it a recovery run
                # renders as a screen of no-ops with no account of who paid for them.
                "recorded_run": marker["run_id"],
            },
        )
        with survive_termination():
            write_report(context, result, time.monotonic() - started)
    except SystemExit as exc:
        # argparse exits zero for `--help`, which is a request answered rather than skew.
        if not exc.code:
            raise
        print(f"marker gate rejected its own arguments: {arguments}", file=sys.stderr)
        return GATE_WORK_PENDING
    except Exception as exc:  # noqa: BLE001 - see the docstring: nothing may reach the engine
        # An exception escaping here would leave Python to exit nonzero, which the engine
        # reads as a fresh marker and answers by skipping the step. The gate therefore
        # cannot afford to enumerate the faults it survives.
        traceback.print_exc()
        print(f"marker gate [{step} scope={scope}]: {exc}; running the step", file=sys.stderr)
        return GATE_WORK_PENDING
    return GATE_MARKER_FRESH


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    # The hook a step's session runs under. Routed here, ahead of everything that resolves
    # a runtime identity, because it runs as a grandchild of a step and inherits the step's
    # own environment — one that reached `RuntimeContext.from_env()` would overwrite the
    # very report the verify gate reads to decide that step's fate ([hooks.py]).
    if arguments and arguments[0] == HOOK_VERB:
        return hook_main(arguments[1:])
    if arguments and arguments[0] == "plan":
        return plan_main(arguments[1:])
    if arguments and arguments[0] == "occasion":
        return occasion_main(arguments[1:])
    if arguments and arguments[0] == "supervise":
        return supervise_main(arguments[1:])
    # Installing a schedule and starting the daemon it costs are one namespace, because the
    # daemon *is* the price of a recurring trigger and separating them is how a person ends
    # up with a trigger that silently does nothing ([triggers.md]).
    if arguments and arguments[0] == "schedule":
        return schedule_main(arguments[1:])
    if arguments and arguments[0] == "workflow":
        return workflow_main(arguments[1:])
    # Reading a run is not part of one. It resolves no runtime identity and leaves no step
    # report, and its exit status is the run's verdict rather than its own health.
    if arguments and arguments[0] == "record":
        return record_main(arguments[1:])
    if arguments and arguments[0] == "report":
        return report_main(arguments[1:])
    # The skill's own two. `run` is the only path in Cairn's code that can begin a paid run,
    # and it accepts an authorisation rather than a request; `explain` answers three
    # questions and starts nothing ([skill/cli.py]).
    if arguments and arguments[0] == "run":
        return run_main(arguments[1:])
    if arguments and arguments[0] == "explain":
        return explain_main(arguments[1:])
    # The whole marker namespace but `write` belongs to the gate, so a mistyped verb is
    # answered by the fail-open precondition rather than falling through to the step
    # dispatch, which would skip the step and leave a report claiming it failed.
    if arguments[:1] == ["marker"] and arguments[1:2] != ["write"]:
        return gate_main(arguments[1:])
    # The verify gate is a precondition too, and it is the fail-open gate's exact inverse:
    # `marker absent` runs the work again whenever it cannot tell, and this one records
    # nothing whenever it cannot tell. Redoing convergent work is cheap; a marker over
    # unverified work reaches git and makes the next run skip the step that would catch it.
    if arguments[:1] == ["verify"]:
        return verify_gate_main(arguments[1:])
    # Both answered before identity resolves: an operator debugging an emitted workflow is
    # not standing inside a step, and has no Dagu environment to offer. Bare `cairn` is
    # the same question asked with no words, and `parse_args` would answer it by naming
    # the missing subcommand rather than listing the ones that exist.
    if not arguments:
        _parser().print_help()
        return 0
    if asks_for_help(arguments):
        _parser().parse_args(arguments)

    context: RuntimeContext | None = None
    started = time.monotonic()
    try:
        context = RuntimeContext.from_env()
        sweep_stale_reports(context)
        with cancel_on_termination():
            result = _dispatch(arguments, context)
    except CairnError as exc:
        print(str(exc), file=sys.stderr)
        if context is None:
            return exc.exit_code
        result = CommandResult(
            exc.exit_code, "failed", str(exc), [], False, exc.cause, exc.detail
        )
    except KeyboardInterrupt:
        if context is None:
            return 1
        result = CommandResult(
            1, "failed", "step cancelled", [], False, "cancelled", {}
        )
    except Exception as exc:  # noqa: BLE001 - the report is the contract; see below
        # A report a router can read beats a traceback it cannot: an unclassified crash
        # still leaves the frozen shape behind, with the exception type as its detail.
        traceback.print_exc()
        if context is None:
            return 1
        result = CommandResult(
            1,
            "failed",
            str(exc) or type(exc).__name__,
            [],
            False,
            "internal_error",
            {"exception": type(exc).__name__},
        )
    duration = time.monotonic() - started
    with survive_termination():
        try:
            write_report(context, result, duration)
        except Exception as exc:  # noqa: BLE001 - the last chance to leave a record
            # A result the writer cannot serialise, or a status outside the vocabulary,
            # would otherwise cost the router the whole report. Degrade to a shape that
            # cannot fail rather than to nothing.
            traceback.print_exc()
            write_report(
                context,
                CommandResult(
                    1,
                    "failed",
                    f"step outcome could not be recorded: {exc}",
                    [],
                    False,
                    "invalid_report",
                    {"exception": type(exc).__name__},
                ),
                duration,
            )
            return 1
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
