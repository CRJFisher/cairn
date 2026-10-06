"""`python3 -m cairn plan` — the deterministic half of the plan contract.

These commands run at derivation time, against a graph on disk and outside any run, so
they take no runtime identity and leave no step report.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, cast

from cairn.core import CairnError, read_standard_json, write_json
from cairn.plan.assertions import AnswerError, answer, propose
from cairn.plan.assertions import render as render_proposals
from cairn.plan.home import graph_path, plan_document_path, refuse_shared_graph
from cairn.plan.ids import assign_ids, derive_plan_slug, plan_slug_collisions
from cairn.plan.questions import ResolutionError, resolve
from cairn.plan.questions import render as render_questions
from cairn.plan.report import render
from cairn.plan.schema import (
    MISSING_VERIFY,
    QUESTION_KINDS,
    Graph,
    SchemaError,
    normalise,
)
from cairn.plan.validate import open_questions, validate


class UsageError(Exception):
    """A graph that could not be read at all — distinct from one that failed validation."""


def _load(path: str) -> Any:
    try:
        return read_standard_json(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise UsageError(f"{path}: {exc.strerror}") from exc
    except ValueError as exc:
        raise UsageError(f"{path}: not valid JSON — {exc}") from exc


def _cmd_validate(args: argparse.Namespace) -> int:
    result = validate(_load(args.graph), source_root=args.source_root)
    if args.json:
        json.dump(result.as_dict(), sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        for finding in result.errors:
            print(f"error  {finding}")
        for finding in result.warnings:
            print(f"warn   {finding}")
        print(
            f"{args.graph}: {len(result.errors)} error(s), {len(result.warnings)} warning(s)"
        )
    return 0 if result.ok else 1


def _cmd_report(args: argparse.Namespace) -> int:
    raw = _load(args.graph)
    result = validate(raw, source_root=args.source_root)
    if result.graph is None:
        for finding in result.errors:
            print(f"error  {finding}", file=sys.stderr)
        return 1
    text = render(raw, result)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text)
    else:
        sys.stdout.write(text)
    return 0 if result.ok else 1


def _cmd_normalise(args: argparse.Namespace) -> int:
    try:
        graph = normalise(_load(args.graph))
    except SchemaError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    json.dump(graph, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


def _graph(path: str) -> Graph:
    try:
        return normalise(_load(path))
    except SchemaError as exc:
        raise UsageError(f"{path}: {exc}") from exc


def _cmd_propose(args: argparse.Namespace) -> int:
    """Show everything the author has still to answer, and how each answer is recorded.

    It writes nothing, which is what makes "a step is never given a synthesised command"
    a property rather than a promise. Its exit status says whether the listing was made,
    never what it contains — `complete` is what says whether anything is left to ask.
    """
    graph = _graph(args.graph)
    proposals = propose(graph)
    questions = [q for q in open_questions(graph) if q["kind"] != MISSING_VERIFY]
    if args.json:
        listing = {
            "complete": not proposals and not questions,
            "assertions": proposals,
            "questions": questions,
        }
        json.dump(listing, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(render_proposals(proposals, args.graph))
        worksheet = render_questions(questions, args.graph)
        if worksheet:
            sys.stdout.write("\n" + worksheet)
    return 0


def _answer_missing_verify(args: argparse.Namespace, graph: Graph) -> Graph:
    if args.step is None or args.dep is not None:
        raise UsageError("a missing_verify answer names its --step, and no --dep")
    if args.accept or args.edit is not None:
        raise UsageError(
            "a missing_verify answer is --command (the assertion, whether it accepts the "
            "proposal or not) or --decline"
        )
    if args.decline and not (args.reason or "").strip():
        raise UsageError("--decline needs --reason: an unverified step must say why")
    try:
        return answer(
            graph,
            args.step,
            command=None if args.decline else args.command,
            reason=args.reason if args.decline else None,
        )
    except AnswerError as exc:
        raise UsageError(str(exc)) from exc


def _cmd_answer(args: argparse.Namespace) -> int:
    graph = _graph(args.graph)
    if args.kind == MISSING_VERIFY:
        answered = _answer_missing_verify(args, graph)
    else:
        if args.command is not None:
            raise UsageError(f"--command answers a missing_verify question, not {args.kind}")
        outcome = "accepted" if args.accept else "edited" if args.edit is not None else "declined"
        try:
            answered = resolve(
                graph,
                args.kind,
                step=args.step,
                dep=args.dep,
                outcome=outcome,
                reading=args.edit,
                reason=args.reason,
            )
        except ResolutionError as exc:
            raise UsageError(str(exc)) from exc
    result = validate(answered)
    if not result.ok:
        for finding in result.errors:
            print(f"error  {finding}", file=sys.stderr)
        return 1
    if args.out:
        # The graph is the only record of every answer already given, and the conversation
        # rewrites it once per answer, so a half-written file would lose the whole of it.
        write_json(Path(args.out), cast(dict[str, Any], answered))
    else:
        json.dump(answered, sys.stdout, indent=2, sort_keys=True, ensure_ascii=False)
        sys.stdout.write("\n")
    return 0


def _cmd_home(args: argparse.Namespace) -> int:
    repository = Path(args.repository).resolve()
    try:
        home = (
            plan_document_path(repository, args.slug)
            if args.document
            else graph_path(repository, args.slug)
        )
        refuse_shared_graph(repository)
    except CairnError as exc:
        raise UsageError(str(exc)) from exc
    print(home)
    return 0


def _cmd_slug(args: argparse.Namespace) -> int:
    slug = derive_plan_slug(args.path)
    print(slug)
    taken = plan_slug_collisions(slug, args.against or [])
    for path in taken:
        print(f"collides with {path}", file=sys.stderr)
    return 1 if taken else 0


def _cmd_ids(args: argparse.Namespace) -> int:
    assignments, collisions = assign_ids(args.slug)
    json.dump({"assignments": assignments, "collisions": collisions}, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cairn plan", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    for name, handler in (("validate", _cmd_validate), ("report", _cmd_report)):
        child = sub.add_parser(name)
        child.add_argument("graph")
        child.add_argument(
            "--source-root",
            help="directory holding the plan documents, to recheck every pin and quotation",
        )
        child.set_defaults(handler=handler)

    sub.choices["validate"].add_argument("--json", action="store_true")
    sub.choices["report"].add_argument("--out")

    child = sub.add_parser("propose")
    child.add_argument("graph")
    child.add_argument("--json", action="store_true")
    child.set_defaults(handler=_cmd_propose)

    child = sub.add_parser("answer")
    child.add_argument("graph")
    child.add_argument("--kind", choices=QUESTION_KINDS, required=True)
    child.add_argument("--step")
    child.add_argument("--dep", help="the other end of an edge question: --step depends on it")
    form = child.add_mutually_exclusive_group(required=True)
    form.add_argument("--command", help="missing_verify: the assertion the author gave")
    form.add_argument("--accept", action="store_true", help="adopt the reading proposed")
    form.add_argument("--edit", help="the author's own restatement of the step's task")
    form.add_argument("--decline", action="store_true")
    child.add_argument("--reason", help="why: required to decline, and to accept an edge")
    child.add_argument("--out")
    child.set_defaults(handler=_cmd_answer)

    child = sub.add_parser("home")
    child.add_argument("slug")
    child.add_argument("--repository", required=True)
    # A plan the request states has no document until authoring writes one, and it belongs
    # where the graph does rather than in the working tree a run refuses to start over.
    child.add_argument("--document", action="store_true")
    child.set_defaults(handler=_cmd_home)

    child = sub.add_parser("normalise")
    child.add_argument("graph")
    child.set_defaults(handler=_cmd_normalise)

    child = sub.add_parser("slug")
    child.add_argument("path")
    child.add_argument(
        "--against",
        nargs="*",
        help="namespace directories the slug must be free in",
    )
    child.set_defaults(handler=_cmd_slug)

    child = sub.add_parser("ids")
    child.add_argument("slug", nargs="+")
    child.set_defaults(handler=_cmd_ids)

    args = parser.parse_args(argv)
    handler: Any = args.handler
    try:
        return int(handler(args))
    except UsageError as exc:
        print(str(exc), file=sys.stderr)
        return 2
