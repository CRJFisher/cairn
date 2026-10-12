"""`python3 -m cairn supervise` — repairs and settings that happen outside any run.

Reconciling a killed run's record, checking the engine's machine-wide configuration and
answering the usage endpoint's one question are all about runs that are not happening, so
like `cairn plan` these commands take no runtime identity and leave no step report.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

from cairn.baseconfig import (
    assert_catchup_disabled,
    assert_dag_retry_disabled,
    base_config_path,
    ensure_dag_retry_disabled,
)
from cairn.core import CairnError
from cairn.enginehome import run_records_path
from cairn.gitio import runs_root
from cairn.headroom import (
    HEADROOM_ENDPOINT_ENV,
    Gauge,
    blindness,
    endpoint_choice,
    endpoint_enabled,
    headroom_directory,
    record_endpoint_choice,
)
from cairn.supervise import WOULD_RECONCILE, reconcile


def _cmd_reconcile(args: argparse.Namespace) -> int:
    results = reconcile(Path(args.path) if args.path else run_records_path(), dry_run=args.dry_run)
    changed = 0
    for result in results:
        if result.changed or args.verbose or result.verdict == WOULD_RECONCILE:
            print(f"{result.verdict}: {result.path}")
        changed += 1 if result.changed else 0
    print(f"{len(results)} record(s) read, {changed} reconciled")
    return 0


def _cmd_base_config(args: argparse.Namespace) -> int:
    path = Path(args.path) if args.path else base_config_path()
    if args.disable:
        changed = ensure_dag_retry_disabled(path)
        print(f"{'wrote' if changed else 'already disabled in'} {path}")
        return 0
    assert_dag_retry_disabled(path)
    assert_catchup_disabled(path)
    print(f"DAG-level retry and cron catchup are both disabled in {path}")
    return 0


def _cmd_headroom(args: argparse.Namespace) -> int:
    """Answer the usage endpoint's one question, or say what the instruments can see.

    The endpoint reads a credential Cairn did not create, so it is the owner's call and
    nobody else's. Recorded here once rather than asked at every run, and said back with the
    blindness it leaves: with it off, the stream puts a figure on a window only from 90% up
    ([45 D]).
    """
    root = runs_root(Path(args.repository) if args.repository else Path.cwd())
    if args.endpoint is not None:
        on = args.endpoint == "on"
        path = record_endpoint_choice(root, on=on, now=time.time())
        print(f"endpoint {'on' if on else 'off'} — recorded in {path}")
        print(
            "applies  to every workflow authored from now on; re-author a plan to carry "
            "this into the runs it starts"
        )
        return 0
    chosen = endpoint_choice(root)
    enabled = endpoint_enabled(root)
    print(f"endpoint {'on' if enabled else 'off'}")
    if chosen is None:
        print(
            f"asked    nobody has answered here; this is {HEADROOM_ENDPOINT_ENV} in the "
            "environment. Record an answer with --endpoint on|off"
        )
    else:
        print(f"asked    recorded in {headroom_directory(root) / 'endpoint.json'}")
    if not enabled:
        gauge = Gauge(headroom_directory(root))
        blind = blindness(gauge.read(), now=gauge.clock())
        if blind is not None:
            print(f"blind    {blind}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cairn supervise", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    child = sub.add_parser("reconcile")
    child.add_argument(
        "path",
        nargs="?",
        help="a status file, a run directory, or a whole data root "
        "(default: the engine's own run history)",
    )
    child.add_argument("--dry-run", action="store_true")
    child.add_argument("--verbose", action="store_true")
    child.set_defaults(handler=_cmd_reconcile)

    child = sub.add_parser("base-config")
    child.add_argument("path", nargs="?")
    child.add_argument(
        "--disable",
        action="store_true",
        help="write the disabling policy instead of asserting it",
    )
    child.set_defaults(handler=_cmd_base_config)

    child = sub.add_parser("headroom")
    child.add_argument(
        "--repository",
        help="the repository whose runs root records the answer (default: this directory)",
    )
    child.add_argument(
        "--endpoint",
        choices=("on", "off"),
        help="record the owner's answer to whether the subscription's usage endpoint may "
        "be read",
    )
    child.set_defaults(handler=_cmd_headroom)

    args = parser.parse_args(argv)
    handler: Any = args.handler
    try:
        return int(handler(args))
    except CairnError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except OSError as exc:
        # These commands run outside a run and leave no report, so an unclassified failure
        # would reach the user as a traceback with nothing to act on.
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
