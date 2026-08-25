"""`python3 -m cairn schedule` — the recurring trigger, and the daemon it costs.

Like `cairn plan`, `cairn supervise`, `cairn workflow` and `cairn record`, these run outside
any run: they take no runtime identity and leave no step report.

The verbs are in one namespace with the daemon on purpose. Doc 13's argument is that the
scheduler *is* the price of a recurring trigger — a cron schedule and an external webhook
cost the same process — and separating the two is how a person ends up with a trigger that
silently does nothing, or a daemon they did not know they had asked for.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, cast

from cairn.baseconfig import base_config_path
from cairn.core import CairnError, write_json
from cairn.enginehome import dags_directory, run_records_path
from cairn.schedule import (
    RETRY_SCANNER_HOURS,
    assert_safe_to_start,
    describe_run,
    install,
    installed,
    published_path,
    queued_runs,
    remove,
    scheduler_command,
    start,
)
from cairn.workflow.stamp import stamp_path, workflow_path

VERBS = frozenset({"install", "remove", "status", "start"})
EXIT_REFUSED = 1

# What a person is agreeing to. Printed wherever the escalation is made, because a schedule
# is never a side effect of wanting a recurring plan.
COST = f"""a schedule costs a persistent process, not just a line in a file:

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


def _cmd_install(args: argparse.Namespace) -> int:
    repository = Path(args.repository).resolve()
    if not args.accept_daemon:
        print(f"refused  {COST}\n\nRe-run with --accept-daemon.", file=sys.stderr)
        return EXIT_REFUSED
    dags = Path(args.dags) if args.dags else None
    # The record is written before the link, because the link is what arms the scheduler:
    # a failure between the two must leave a note about a schedule that does not fire
    # rather than a schedule nothing recorded.
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
    write_json(_triggers_path(repository, args.plan), record)
    published = install(repository, args.plan, dags=dags)
    print(f"linked   {published} -> {record['workflow']}")
    print(f"recorded {_triggers_path(repository, args.plan)}")
    print(f"fires    {_fires(published)}")
    print(COST)
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
    """What is installed, whether the machine is safe, and what is waiting on nothing."""
    dags = Path(args.dags) if args.dags else dags_directory()
    records = Path(args.engine_records) if args.engine_records else run_records_path()
    print(f"watched  {dags}")
    for path in installed(dags=dags):
        target = path.readlink() if path.is_symlink() else None
        print(f"  {path.name}" + (f" -> {target}" if target else "  (not Cairn's)"))
    config = Path(args.base_config) if args.base_config else base_config_path()
    try:
        assert_safe_to_start(base_config=config, records=records)
        print(f"safe     {config}: a scheduler may be started")
    except CairnError as exc:
        # A report never refuses. Asking what the state is has to be answerable on a machine
        # whose state is bad, which is the only machine the question is interesting on.
        print(f"unsafe   {exc}")
    waiting = queued_runs(records)
    for run in waiting:
        print(f"queued   {describe_run(run)} — nothing is draining the queue")
    if not waiting:
        print("queued   none")
    return 0


def _cmd_start(args: argparse.Namespace) -> int:
    """Assert the machine is safe, then become the scheduler.

    The assertion is here rather than at install because this is the only moment the hazard
    can fire, and a machine that was safe a month ago is not evidence about this one.
    """
    dags = Path(args.dags) if args.dags else None
    if not args.accept_daemon:
        print(f"refused  {COST}\n\nRe-run with --accept-daemon.", file=sys.stderr)
        return EXIT_REFUSED
    waiting = assert_safe_to_start(
        base_config=Path(args.base_config) if args.base_config else None,
        records=Path(args.engine_records) if args.engine_records else None,
    )
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
    child.add_argument("--repository", default=".")
    child.add_argument("--dags")
    child.add_argument("--accept-daemon", action="store_true")
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
    child.set_defaults(handler=_cmd_status)

    child = sub.add_parser("start")
    child.add_argument("--dags")
    child.add_argument("--base-config")
    child.add_argument("--engine-records")
    child.add_argument("--accept-daemon", action="store_true")
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


__all__ = ["COST", "VERBS", "main"]
