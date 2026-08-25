"""`python3 -m cairn report` — one run, rendered for a terminal, a repository or a browser.

Reading a run is not part of one. Like `cairn plan`, `cairn supervise`, `cairn workflow` and
`cairn record`, this resolves no runtime identity, leaves no step report, takes no lock and
starts nothing — a report is a thing you can produce with the engine stopped.

**The exit status is the run's verdict, not this command's health**, exactly as `cairn
record`'s is. That is the point of freezing the exit-code contract apart from the display
verdict: a rendering can never redefine what automation reads, so a run with exclusions exits
3 whatever any of the three renderings chose to say about it.

The record is built fresh rather than read back from disk, for the same reason `cairn record
facts` builds one: it is derived from the engine's state and the run's own reports, both of
which outlive it, so a report never shows a reader a record that has since been superseded.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

from cairn.core import CairnError, write_text
from cairn.enginehome import run_records_path
from cairn.gitio import runs_root
from cairn.record.facts import as_mapping
from cairn.record.store import build_run_record
from cairn.record.vocabulary import EXIT_NO_RECORD
from cairn.report import html, markdown, terminal
from cairn.report.compose import document
from cairn.report.sinks import Rendering
from cairn.report.spine import (
    SINK_HTML,
    SINK_MARKDOWN,
    SINK_TERMINAL,
    SINKS,
    Document,
)

Renderer = Callable[[Document, Mapping[str, str]], Rendering]

RENDERERS: dict[str, Renderer] = {
    SINK_TERMINAL: terminal.render,
    SINK_MARKDOWN: markdown.render,
    SINK_HTML: html.render,
}


def _runs_root(args: argparse.Namespace) -> Path:
    """Where this run's reports are, from the request rather than from where it was typed.

    A repository is never defaulted to the directory the caller happens to be in
    ([SKILL.md]): a report found that way is a report about whatever tree the terminal was
    sitting in, and reading one run's receipts out of another repository is a wrong answer
    delivered confidently. `--reports` names a runs root outright and needs no repository to
    derive one from, which is the one caller reading a recorded corpus rather than a tree.
    """
    if args.reports:
        return Path(args.reports)
    if args.repository is None:
        raise CairnError(
            "invalid_arguments",
            "--repository names the repository whose run this is, and it has no default: "
            "the directory this was typed in is not the repository the run happened in "
            "unless someone says so. Name it, or name a runs root with --reports",
        )
    return runs_root(Path(args.repository).resolve())


def _render(args: argparse.Namespace) -> int:
    root = _runs_root(args)
    records = Path(args.engine_records) if args.engine_records else run_records_path()
    try:
        # One statement of the pipeline, shared with `cairn record`: two readers of one run
        # that assembled it separately would be two records to keep in step.
        record = build_run_record(root, records, args.run)
    except ValueError as exc:
        # A run id that is not one. Raised as a plain `ValueError` by `check_run_id`, and
        # answered here rather than at the outer handler so that a `ValueError` from the
        # rendering itself stays a crash instead of reading as a missing record.
        raise CairnError("invalid_run_id", str(exc), detail={"run": args.run}) from exc
    if record is None:
        print(
            f"neither Cairn nor the engine holds a record of run {args.run!r}; looked in "
            f"{root} and {records}",
            file=sys.stderr,
        )
        return EXIT_NO_RECORD
    rendering = RENDERERS[args.format](document(record), as_mapping(record))
    if args.out:
        # Replaced in one step, for the reason the record itself is: a reader has no way to
        # tell a half-written report from a run that did little, and the sections a truncated
        # one loses are the receipts at the end.
        write_text(Path(args.out), rendering.text)
        print(args.out)
    else:
        sys.stdout.write(rendering.text)
    return record["exit_code"]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cairn report", description=__doc__)
    parser.add_argument("--run", required=True)
    parser.add_argument("--repository")
    parser.add_argument("--format", choices=SINKS, default=SINK_TERMINAL)
    parser.add_argument("--out")
    parser.add_argument("--engine-records")
    # The runs root to read, for a caller reading a recorded corpus rather than a repository.
    parser.add_argument("--reports")
    return parser


def main(arguments: list[str]) -> int:
    args = _parser().parse_args(arguments)
    try:
        return _render(args)
    except CairnError as exc:
        # An unmapped engine status lands here, which is where a version bump surfaces: a
        # refusal to read rather than a verdict about the run, so it borrows no verdict's code.
        print(f"error  {exc}", file=sys.stderr)
        return EXIT_NO_RECORD
    except OSError as exc:
        print(f"error  {exc}", file=sys.stderr)
        return EXIT_NO_RECORD


__all__ = ["RENDERERS", "main"]
