"""`python3 -m cairn schedule` — the recurring trigger, and the daemon it needs.

Like `cairn plan`, `cairn supervise`, `cairn workflow` and `cairn record`, these run outside
any run: they take no runtime identity and leave no step report.

The verbs are in one namespace with the daemon on purpose. Doc 13's argument is that the
scheduler *is* what a recurring trigger runs on — a cron schedule and an external webhook
need the same process — and separating the two is how a person ends up with a trigger that
silently does nothing, or a daemon they did not know they had asked for.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, cast

from cairn.baseconfig import base_config_path
from cairn.core import CairnError, write_json, write_text
from cairn.enginehome import dags_directory, run_records_path
from cairn.schedule import (
    DRAINS_NOTHING,
    RETRY_SCANNER_HOURS,
    SCHEDULER_PAUSED,
    SchedulerStatus,
    assert_safe_to_start,
    describe_run,
    install,
    installed,
    published_path,
    queued_runs,
    remove,
    scheduler_command,
    scheduler_status,
    service_plan,
    start,
)
from cairn.skill.resolve import Resolved, repository_line, resolve_repository
from cairn.workflow.gate import admit
from cairn.workflow.stamp import stamp_path, workflow_path

VERBS = frozenset({"install", "remove", "status", "start", "service"})
EXIT_REFUSED = 1

# What scheduling puts on this machine. Printed with the install, because it decides what
# will execute; it is information and asks nothing.
SCHEDULER_FACTS = f"""a schedule needs a persistent process, not just a line in a file:

  - `dagu scheduler` must be running for a cron schedule or a webhook to fire at all. A
    webhook does not execute a run — it enqueues one, and only the scheduler drains the
    queue, so an external trigger without one is accepted and does nothing.
  - the definition must be reachable from the directory that scheduler watches, which is
    not where Cairn writes it. `install` links it there.
  - while it is up, its retry scanner re-executes every failed run recorded on this machine
    in the previous {RETRY_SCANNER_HOURS} hours — including runs Cairn never wrote. That is
    asserted off before it starts, and `start` refuses otherwise, naming what it found."""


def _triggers_path(repository: Path, plan: str) -> Path:
    """Where Cairn records what was installed against a plan, beside the definition itself."""
    return stamp_path(workflow_path(repository, plan)).with_name(
        f"{plan}.triggers.json"
    )


def _dags(args: argparse.Namespace) -> Path:
    return Path(args.dags).resolve() if args.dags else dags_directory().resolve()


def _cmd_install(args: argparse.Namespace) -> int:
    resolution = resolve_repository(
        args.repository, session=Path(args.session) if args.session else None
    )
    if not isinstance(resolution, Resolved):
        raise CairnError("invalid_arguments", resolution.question)
    print(repository_line(resolution))
    repository = resolution.repository
    dags = _dags(args)
    source = workflow_path(repository, args.plan)
    admission, faults = admit(source, expected_plan=args.plan)
    if admission is None:
        raise CairnError(
            "workflow_not_admitted",
            f"{source} did not pass the execution gate: "
            + "; ".join(str(fault) for fault in faults),
        )
    record: dict[str, Any] = {
        "plan": args.plan,
        "repository": str(repository),
        "published": str(published_path(args.plan, dags=dags)),
        "workflow": str(workflow_path(repository, args.plan)),
    }
    if args.webhook_token_sink:
        # Cairn records where the token went and never what it is. The engine shows a
        # webhook's bearer token once, at creation, and a tool that stored one would be
        # handling a credential — which Cairn does not do, by construction.
        record["webhook"] = {"token_sink": args.webhook_token_sink}
    # **The link first, the record only once it holds.** An install is one act or none: a
    # sidecar written ahead of a link that was then refused — a name already taken by
    # another repository's plan of the same slug — claims a published schedule and a
    # webhook-token disposition for a trigger that does not exist, and the next reader of
    # it has no way to tell that from a schedule that fires ([29 C]). Nothing between the
    # two can be left half done the other way either: the sidecar is this repository's own
    # file, so a write that fails takes the link back out.
    published = install(repository, args.plan, dags=dags, admission=admission)
    try:
        write_json(_triggers_path(repository, args.plan), record)
    except OSError:
        remove(repository, args.plan, dags=dags)
        raise
    print(f"linked   {published} -> {record['workflow']}")
    print(f"recorded {_triggers_path(repository, args.plan)}")
    print(f"fires    {_fires(published)}")
    print(SCHEDULER_FACTS)
    if args.webhook_token_sink:
        name = published.stem
        print(
            "\nto create the external trigger, against a running server:\n"
            f"  POST /api/v1/dags/{name}/webhook\n"
            "the bearer token it returns is shown once; put it where you said "
            f"({args.webhook_token_sink}). Cairn never stores it."
        )
    return 0


def _fires(published: Path) -> str:
    """What will actually fire this definition, read from the definition itself.

    Linking a plan authored without a schedule is the trigger-that-does-nothing this
    namespace exists to prevent, in the one direction the watched directory cannot show.
    """
    try:
        document: Any = json.loads(published.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "unreadable — the engine will refuse it"
    cron: Any = (
        cast(dict[str, Any], document).get("schedule")
        if isinstance(document, dict)
        else None
    )
    if isinstance(cron, str) and cron:
        return f"cron {cron!r}, once a scheduler is running"
    return (
        "nothing on its own — this definition declares no schedule, so only an external "
        "trigger will fire it; author it with `--schedule` for a cron one"
    )


def _cmd_remove(args: argparse.Namespace) -> int:
    repository = Path(args.repository).resolve()
    dags = Path(args.dags) if args.dags else None
    gone = remove(repository, args.plan, dags=dags)
    # The record goes either way: it is this repository's own, and one left behind would
    # claim a schedule that is not installed.
    _triggers_path(repository, args.plan).unlink(missing_ok=True)
    if gone is None:
        print(f"nothing installed at {published_path(args.plan, dags=dags)}")
        return 0
    print(f"removed  {gone}")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    """Whether a scheduler exists, what is installed, and what each trigger would do.

    The first question is liveness, because every other line here depends on it: a cron
    expression fires nothing and a queued run drains into nothing unless something is
    running, and a status that listed the definitions and the queue while saying nothing
    about the daemon answered the wrong question confidently ([29 C]).

    Nothing here refuses. Asking what the state is has to be answerable on a machine whose
    state is bad, which is the only machine the question is interesting on.
    """
    dags = Path(args.dags) if args.dags else dags_directory()
    records = Path(args.engine_records) if args.engine_records else run_records_path()
    status = scheduler_status(
        registry=Path(args.scheduler_registry) if args.scheduler_registry else None,
        pause=Path(args.scheduler_pause) if args.scheduler_pause else None,
    )
    print(f"scheduler {status.state} — {status.sentence}")
    for process in status.processes:
        print(
            f"  process  {process.identity}: pid {process.pid}, started "
            f"{process.started_at or 'at an unrecorded time'} — Cairn's own check of that "
            f"process says {process.reading}"
        )
    if status.paused_since is not None and status.state != SCHEDULER_PAUSED:
        print(
            f"  paused   the engine's scheduler pause has been set since "
            f"{status.paused_since}, so the next scheduler to start fires nothing until it "
            "is resumed through the engine's API"
        )
    print(f"watched  {dags}")
    for path in installed(dags=dags):
        if not path.is_symlink():
            print(f"  {path.name}  not Cairn's: Cairn installs a link and this is a file")
            continue
        # The link and what it resolves to are two facts. A link whose target has been
        # pruned still lists, and the engine refuses it at load — which is a schedule that
        # silently never fires, the one failure this namespace exists to prevent.
        resolved = "resolves" if path.exists() else "DOES NOT RESOLVE, so the engine cannot load it"
        print(f"  {path.name} -> {path.readlink()} ({resolved})")
        print(f"    fires  {_fires(path)}")
    config = Path(args.base_config) if args.base_config else base_config_path()
    try:
        assert_safe_to_start(base_config=config, records=records)
        print(f"safe     {config}: a scheduler may be started")
    except CairnError as exc:
        print(f"unsafe   {exc}")
    waiting = queued_runs(records)
    for run in waiting:
        print(f"queued   {describe_run(run)} — {_drainage(status)}")
    if not waiting:
        print("queued   none")
    service = service_plan(dags=dags)
    installed_unit = service.unit_path.exists()
    print(
        f"service  {service.manager}: {service.unit_path}"
        + (
            " is in place"
            if installed_unit
            else " is where this machine would keep a scheduler alive; "
            "`python3 -m cairn schedule service` writes it and says how"
        )
    )
    if installed_unit:
        print(f"         {' '.join(service.status)}")
    return 0


def _drainage(status: SchedulerStatus) -> str:
    """What a queued run is waiting on, claiming only what liveness established.

    "Nothing is draining the queue" is a measurement, not a default. Said on the strength of
    not having looked, it sends a person to start a second scheduler beside the one that is
    already running — and the scheduler is the one daemon on this machine that re-executes
    failed runs ([schedule.py]).
    """
    if status.state in DRAINS_NOTHING:
        return "nothing is draining the queue"
    if status.drains:
        return "a scheduler is running, so it is queued behind other work rather than stranded"
    return f"whether anything is draining it is not established: {status.sentence}"


def _cmd_service(args: argparse.Namespace) -> int:
    """The supported way to keep a scheduler alive, printed, and written where asked.

    Printed by default and written only when asked, because a unit file is a change to how
    the machine behaves at every boot. Loading it is always the person's own command: this
    prints the one line that does it, so nothing here starts a daemon.
    """
    dags = _dags(args)
    service = service_plan(
        dags=dags, home=Path(args.home).resolve() if args.home else None
    )
    print(f"manager  {service.manager}")
    print(f"unit     {service.unit_path}")
    if args.write:
        existing = (
            service.unit_path.read_text(encoding="utf-8")
            if service.unit_path.exists()
            else None
        )
        if existing is not None and existing != service.unit:
            raise CairnError(
                "invalid_arguments",
                f"{service.unit_path} already exists and is not the unit Cairn would write; "
                "read it, then move it aside if it is no longer wanted",
                detail={"unit": str(service.unit_path)},
            )
        if existing is None:
            service.unit_path.parent.mkdir(parents=True, exist_ok=True)
            write_text(service.unit_path, service.unit)
            print("wrote    the unit above; nothing is running yet")
        else:
            print("wrote    nothing: the unit above is already exactly this")
    print(f"install  {' '.join(service.install)}")
    print(f"status   {' '.join(service.status)}")
    print(f"stop     {' '.join(service.stop)}")
    print(f"\n{service.unit}")
    print(SCHEDULER_FACTS)
    return 0


def _cmd_start(args: argparse.Namespace) -> int:
    """Assert the machine is safe, then become the scheduler.

    The assertion is here rather than at install because this is the only moment the hazard
    can fire, and a machine that was safe a month ago is not evidence about this one.
    """
    dags = _dags(args)
    waiting = assert_safe_to_start(
        base_config=Path(args.base_config) if args.base_config else None,
        records=Path(args.engine_records) if args.engine_records else None,
    )
    print(SCHEDULER_FACTS)
    for run in waiting:
        print(f"draining {describe_run(run)}")
    print(f"starting {' '.join(scheduler_command(dags=dags))}")
    sys.stdout.flush()
    if args.dry_run:
        return 0
    start(dags=dags)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cairn schedule", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    child = sub.add_parser("install")
    child.add_argument("--plan", required=True)
    child.add_argument("--repository")
    # The conversation's directory, passed in rather than read: `python3 -m cairn` is run
    # from the skill's own directory, so the process's own names Cairn's checkout whatever
    # repository the person is in ([cairn/skill/resolve.py]).
    child.add_argument("--session")
    child.add_argument("--dags")
    child.add_argument(
        "--webhook-token-sink",
        help="where the bearer token will be kept; recorded, never the token itself",
    )
    child.set_defaults(handler=_cmd_install)

    child = sub.add_parser("remove")
    child.add_argument("--plan", required=True)
    # Required rather than defaulted: removing a link means proving it is this repository's,
    # and the watched directory is shared with every other job on the machine.
    child.add_argument("--repository", required=True)
    child.add_argument("--dags")
    child.set_defaults(handler=_cmd_remove)

    child = sub.add_parser("status")
    child.add_argument("--dags")
    child.add_argument("--base-config")
    child.add_argument("--engine-records")
    # Both are the engine's own files, named here for the same reason the records root is:
    # a test, and an operator on a machine with a second engine home, must be able to ask
    # about that one rather than about whichever this process resolves.
    child.add_argument("--scheduler-registry")
    child.add_argument("--scheduler-pause")
    child.set_defaults(handler=_cmd_status)

    child = sub.add_parser("service")
    child.add_argument("--dags")
    # The unit belongs to the account that will run the agent sessions, so it goes under
    # that account's own home — named here so a caller can say which one.
    child.add_argument("--home")
    # Writing the unit is asked for, never a side effect of reading the guidance: it changes
    # what the machine does at every boot from then on.
    child.add_argument("--write", action="store_true")
    child.set_defaults(handler=_cmd_service)

    child = sub.add_parser("start")
    child.add_argument("--dags")
    child.add_argument("--base-config")
    child.add_argument("--engine-records")
    child.add_argument("--dry-run", action="store_true")
    child.set_defaults(handler=_cmd_start)

    args = parser.parse_args(argv)
    handler: Any = args.handler
    try:
        return int(handler(args))
    except CairnError as exc:
        print(f"refused  {exc}", file=sys.stderr)
        return EXIT_REFUSED
    except OSError as exc:
        # These run outside a run and leave no report, so an unclassified failure would
        # reach a person as a traceback with nothing to act on.
        print(f"error  {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_REFUSED


__all__ = ["SCHEDULER_FACTS", "VERBS", "main"]
