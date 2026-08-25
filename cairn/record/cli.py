"""`python3 -m cairn record` — build one run's record, or read the facts back.

This runs outside any run, like `cairn plan`, `cairn supervise` and `cairn workflow`: it
takes no runtime identity and leaves no step report. It reads a finished run, a live one, or
one whose orchestrator was killed, all through the same path — the record is a thing you can
read with nothing running.

**The exit status is the run's verdict, not this command's health.** That is the whole point
of freezing the exit-code contract apart from the display verdict: automation reads the
number, a person reads the record, and a severity judgement in a report can never redefine
what the number means. A command that could not produce a record at all exits on a code no
verdict owns.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cairn.core import CairnError
from cairn.enginehome import run_records_path
from cairn.gitio import runs_root
from cairn.layout import reports_directory
from cairn.record.facts import canonical_facts
from cairn.record.store import build_run_record, write_record
from cairn.record.vocabulary import EXIT_NO_RECORD

VERBS = frozenset({"build", "facts"})


def _build(args: argparse.Namespace) -> int:
    repository = Path(args.repository).resolve()
    root = runs_root(repository)
    records = Path(args.engine_records) if args.engine_records else run_records_path()

    record = build_run_record(root, records, args.run)
    if record is None:
        print(
            f"neither Cairn nor the engine holds a record of run {args.run!r}; looked in "
            f"{reports_directory(root, args.run)} and {records}",
            file=sys.stderr,
        )
        return EXIT_NO_RECORD

    if args.verb == "facts":
        for key, value in canonical_facts(record):
            print(f"{key}\t{value}")
    else:
        print(write_record(root, record))
    return record["exit_code"]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cairn record", description=__doc__)
    verbs = parser.add_subparsers(dest="verb", required=True)
    for verb in ("build", "facts"):
        child = verbs.add_parser(verb)
        child.add_argument("--run", required=True)
        child.add_argument("--repository", default=".")
        child.add_argument("--engine-records")
    return parser


def main(arguments: list[str]) -> int:
    args = _parser().parse_args(arguments)
    try:
        return _build(args)
    except CairnError as exc:
        # An unmapped engine status lands here, which is where a version bump surfaces: it
        # is a refusal to read rather than a verdict about the run, so it never borrows one
        # of the verdict's codes.
        print(f"error  {exc}", file=sys.stderr)
        return EXIT_NO_RECORD
    except OSError as exc:
        print(f"error  {exc}", file=sys.stderr)
        return EXIT_NO_RECORD


__all__ = ["VERBS", "main"]
