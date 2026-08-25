"""`python3 -m cairn supervise` — repairs that happen outside any run.

Reconciling a killed run's record and checking the engine's machine-wide configuration are
both about runs that are not happening, so like `cairn plan` these commands take no runtime
identity and leave no step report.
"""

from __future__ import annotations

import argparse
import sys
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
