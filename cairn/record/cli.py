"""`python3 -m cairn record` — build one run's record, read the facts back, or find a run.

This runs outside any run, like `cairn plan`, `cairn supervise` and `cairn workflow`: it
takes no runtime identity and leaves no step report. It reads a finished run, a live one, or
one whose orchestrator was killed, all through the same path — the record is a thing you can
read with nothing running.

**The exit status is the run's verdict, not this command's health.** That is the whole point
of freezing the exit-code contract apart from the display verdict: automation reads the
number, a person reads the record, and a severity judgement in a report can never redefine
what the number means. A command that could not produce a record at all exits on a code no
verdict owns.

`list` and `latest` are the two verbs that take a plan rather than a run id, because "how
did the last run of plan A go?" is a question asked with the plan's name and nothing else
([runs.py]). Both read what is already on disk and build nothing.
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
from cairn.record.runs import RunEntry, latest_run, list_runs, unreadable_runs
from cairn.record.store import build_run_record, write_record
from cairn.record.vocabulary import EXIT_NO_RECORD
from cairn.report.width import display_width, pad

VERBS = frozenset({"build", "facts", "list", "latest"})

# What a listing says where the record does not carry a value. The projection's own word,
# for the projection's own reason: a blank column reads as a measured nothing.
NOT_RECORDED = "not recorded"
UNREADABLE = "unreadable"

COLUMNS = ("verdict", "run", "started", "trigger", "plan")


def _build(args: argparse.Namespace) -> int:
    root = _runs_root(args)
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


def _runs_root(args: argparse.Namespace) -> Path:
    """Where this repository keeps its runs, or the root a caller named outright.

    Every verb takes both, because the one caller reading a recorded corpus rather than a
    tree has a runs root and no repository to derive one from ([report/cli.py]).
    """
    if args.reports:
        return Path(args.reports)
    return runs_root(Path(args.repository).resolve())


def _row(entry: RunEntry) -> tuple[str, ...]:
    return (
        UNREADABLE if entry.fault is not None else (entry.verdict or NOT_RECORDED),
        entry.run_id,
        entry.started_at or NOT_RECORDED,
        entry.trigger or NOT_RECORDED,
        entry.plan or NOT_RECORDED,
    )


def _print(entries: list[RunEntry]) -> None:
    """One line per run, aligned, with each refusal spelled out under its own entry."""
    rows = [_row(entry) for entry in entries]
    widths = [
        max(display_width(column), *(display_width(row[index]) for row in rows))
        if rows
        else display_width(column)
        for index, column in enumerate(COLUMNS)
    ]
    print("  ".join(pad(column, width) for column, width in zip(COLUMNS, widths, strict=True)))
    for entry, row in zip(entries, rows, strict=True):
        print("  ".join(pad(value, width) for value, width in zip(row, widths, strict=True)).rstrip())
        if entry.fault is not None:
            print(f"    {entry.fault}")


def _cmd_list(args: argparse.Namespace) -> int:
    root = _runs_root(args)
    entries = list_runs(root, plan=args.plan)
    if not entries:
        named = f" of plan {args.plan!r}" if args.plan else ""
        print(f"no runs{named} under {root}", file=sys.stderr)
        return 0
    _print(entries)
    return 0


def _cmd_latest(args: argparse.Namespace) -> int:
    """The one run of this plan a reader means by "the last one", and how it went.

    The exit status is that run's own verdict, so the question can be asked by a script as
    well as by a person. Where there is no readable run of the plan at all, the status is
    the one no verdict owns.
    """
    root = _runs_root(args)
    entry = latest_run(root, plan=args.plan)
    newer = unreadable_runs(root, plan=args.plan)
    if entry is None:
        print(
            f"no readable run of plan {args.plan!r} under {root}",
            file=sys.stderr,
        )
        if newer:
            _print(newer)
        return EXIT_NO_RECORD
    _print([entry])
    # Said before anything else about the answer: a reader choosing this run as "the last
    # one" has to know that a run Cairn could not read may be newer than it.
    for candidate in newer:
        print(
            f"also     {candidate.run_id} is in this repository and could not be read: "
            f"{candidate.fault}",
            file=sys.stderr,
        )
    print(
        f"read     python3 -m cairn report --run {entry.run_id} "
        f"--repository {args.repository}"
    )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cairn record", description=__doc__)
    verbs = parser.add_subparsers(dest="verb", required=True)
    for verb in ("build", "facts"):
        child = verbs.add_parser(verb)
        child.add_argument("--run", required=True)
        child.add_argument("--repository", default=".")
        child.add_argument("--engine-records")
        child.add_argument("--reports")
        child.set_defaults(handler=_build)

    child = verbs.add_parser("list")
    # Optional: without one this is "what has this repository run", which is the question
    # asked by somebody who does not yet know the plan's slug.
    child.add_argument("--plan")
    child.add_argument("--repository", default=".")
    child.add_argument("--reports")
    child.set_defaults(handler=_cmd_list)

    child = verbs.add_parser("latest")
    child.add_argument("--plan", required=True)
    child.add_argument("--repository", default=".")
    child.add_argument("--reports")
    child.set_defaults(handler=_cmd_latest)
    return parser


def main(arguments: list[str]) -> int:
    args = _parser().parse_args(arguments)
    try:
        return int(args.handler(args))
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
