"""`python3 -m cairn run` and `python3 -m cairn explain` — what the skill invokes.

Like `cairn plan`, `cairn workflow`, `cairn schedule`, `cairn record` and `cairn report`,
these run outside any run: they resolve no runtime identity and leave no step report. They
are an implementation surface for the skill, not a user interface — a person asks for what
they want and never learns one of these lines.

`run` has two verbs and they are two on purpose. `offer` states the price and mints the one
token `start` accepts, so there is no path to a run whose cost was never stated and no way
to spend one acceptance twice. `explain` has three, one per question it answers, and none of
them starts, locks or writes anything.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, cast

from cairn.core import EXIT_OK, CairnError
from cairn.enginehome import run_records_path
from cairn.gitio import runs_root
from cairn.layout import RECORD_FILE, check_run_id
from cairn.marker import mint_occasion
from cairn.record.store import build_run_record
from cairn.skill.consent import Refused, make_offer, record_engine_command, spend
from cairn.skill.explain import explainable, meaning, why_excluded, would_do
from cairn.skill.resolve import (
    OccasionSignal,
    Resolved,
    decide_occasion,
    refuse_missing_definition,
    resolve_repository,
)
from cairn.skill.trigger import (
    EngineUnavailable,
    address,
    refuse_unusable_engine,
    start,
)
from cairn.skill.vocabulary import TRIGGER_SHAPES
from cairn.workflow.stamp import workflow_path

EXIT_REFUSED = 1


def mint_run_id() -> str:
    """A run identity, in the shape the engine and the record layout both accept.

    The same minting the occasion uses, because both answer "which occasion of this is
    this" and a caller should not have to invent either.
    """
    return mint_occasion()


def _repository(stated: str | None, workflow: Path | None) -> Path:
    resolution = resolve_repository(stated, workflow)
    if isinstance(resolution, Resolved):
        return resolution.repository
    raise CairnError("invalid_arguments", resolution.question)


def _has_run_before(repository: Path, plan: str) -> bool:
    """Whether this plan has run here before.

    Per plan rather than per repository, because the answer decides whether the occasion
    reading is worth stating and "this plan has run before" is what makes it worth stating.
    A repository-wide count would disclose on a plan's first run because some other plan had
    one.

    Read from each run's own record on disk rather than rebuilt from the engine's history:
    rebuilding walks the engine's whole machine-wide `dag-runs` tree once per run, and this
    is a question asked in the middle of a conversation. Anything under the runs root that is
    not a run Cairn recorded is not a run of this plan.
    """
    root = runs_root(repository)
    if not root.exists():
        return False
    for entry in sorted(root.iterdir()):
        try:
            recorded = json.loads((entry / RECORD_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(recorded, dict) and cast(dict[str, Any], recorded).get("plan") == plan:
            return True
    return False


def _cmd_offer(args: argparse.Namespace) -> int:
    plan = str(args.plan)
    workflow = workflow_path(Path(str(args.repository)).resolve(), plan)
    refuse_missing_definition(workflow, plan, str(args.repository))
    repository = _repository(str(args.repository), workflow)
    record = None
    if args.recovering:
        record = build_run_record(
            runs_root(repository), run_records_path(), str(args.recovering)
        )
        if record is None:
            raise CairnError(
                "invalid_arguments",
                f"no record of run {args.recovering} against {repository}, so there is "
                "nothing to continue",
            )
    reading = decide_occasion(
        OccasionSignal(
            trigger=str(args.trigger),
            named_run=args.recovering,
            pinned=args.occasion,
            prior_runs=1 if _has_run_before(repository, plan) else 0,
        ),
        record,
    )
    offer, cost = make_offer(
        repository,
        plan=plan,
        workflow=workflow,
        parent_branch=args.parent_branch,
        occasion_reading=reading.reading,
        occasion=reading.occasion,
    )
    print(f"offer   {offer.offer_id}")
    print(f"plan    {offer.plan} → {offer.repository} on {offer.parent_branch}")
    print("cost    a run of this plan is not free:")
    for line in cost:
        print(f"  - {line}")
    print(f"occasion  {reading.reading}: {reading.taken}")
    if reading.disclose:
        print(f"          the other reading would mean: {reading.forgone}")
    print(
        "\nThis run happens only if you say so. Quote the offer id when you do; an "
        "acceptance authorises exactly one execution."
    )
    return 0


def _cmd_start(args: argparse.Namespace) -> int:
    """Spend one offer and begin the run it bought.

    Everything that can refuse is asked before the offer is spent, so a refusal here leaves
    the acceptance standing and the person is not asked to decide the same thing twice.
    """
    repository = Path(str(args.repository)).resolve()
    run_id = mint_run_id() if args.run_id is None else str(args.run_id)
    try:
        check_run_id(run_id)
        refuse_unusable_engine()
        # Asked here rather than where it is used. It shells out to the engine and can
        # refuse; raised after the spend that would be a crash over a consumed acceptance,
        # and every refusal has to happen while the yes is still standing.
        records = run_records_path()
    except EngineUnavailable as unavailable:
        print(f"refused  {unavailable}", file=sys.stderr)
        return EXIT_REFUSED
    except ValueError as malformed:
        print(f"refused  {run_id!r} is not a run id: {malformed}", file=sys.stderr)
        return EXIT_REFUSED

    granted = spend(repository, str(args.offer), reply=str(args.reply), run_id=run_id)
    if isinstance(granted, Refused):
        print(f"refused  {granted.outcome}: {granted.why}", file=sys.stderr)
        return EXIT_REFUSED

    # Composed and recorded before the engine is invoked, so a start that dies leaves both
    # the run id and the invocation it was about to make ([19 B]).
    where = address(granted, run_id, runs_root(repository))
    record_engine_command(repository, granted.offer_id, where.command)

    # **Printed before the launch.** These four lines are known the moment the offer is
    # spent, and a caller killed while the engine is starting has still been told the name
    # of the run its acceptance bought.
    print(f"started  {where.run_id}")
    print(f"branch   verified work lands on {granted.parent_branch}, as the offer stated")
    print(f"watch    {where.view}")
    print(f"read     python3 -m cairn report --run {where.run_id} --repository {repository}")

    started = start(where, records=records, wait=bool(args.wait))
    if not started.taken_on:
        if started.exit_code is None:
            # Neither registered nor exited. The engine may still take it on, so this is a
            # caution rather than a refusal — and the process is left alone.
            print(
                f"waiting  the engine has not registered {where.run_id} yet and is still "
                f"running; what it says is at {where.log}",
            )
            return EXIT_OK
        # The engine declining to take the run on at all — a run id it already holds, a
        # definition it cannot load — leaves no record for anyone to read, so it is the one
        # engine status this command must not swallow. A run it *accepted* is a different
        # matter: whether that run worked is the record's answer ([docs/run-model.md]).
        print(
            f"refused  the engine exited {started.exit_code} without taking the run on: "
            f"{' '.join(where.command)} — what it said is at {where.log}",
            file=sys.stderr,
        )
        return EXIT_REFUSED
    if args.wait:
        print(
            f"engine   exited {started.exit_code}; the verdict is the record's, not this "
            "status"
        )
    return EXIT_OK


def _cmd_explain_workflow(args: argparse.Namespace) -> int:
    repository = Path(str(args.repository)).resolve()
    workflow = workflow_path(repository, str(args.plan))
    refuse_missing_definition(workflow, str(args.plan), str(repository))
    account = would_do(workflow, str(args.plan))
    print(f"plan      {account.plan}")
    print(f"target    {account.repository} on {account.parent_branch}")
    print(f"schedule  {account.schedule or 'none — it runs when it is asked to'}")
    print(f"paid      {account.agent_steps} agent step(s)")
    print(f"file      {account.provenance.summary}")
    print("steps")
    for step in account.steps:
        does = " ".join(step.subcommand) or (step.assertion or "—")
        print(f"  {step.node:<40} {does}")
    return 0


def _cmd_explain_word(args: argparse.Namespace) -> int:
    found = meaning(str(args.word))
    for family, sentence in zip(found.families, found.sentences, strict=True):
        print(f"{family:<16} {sentence}")
    if found.exit_code is not None:
        print(f"{'exit status':<16} {found.exit_code}")
    return 0


def _cmd_explain_exclusion(args: argparse.Namespace) -> int:
    repository = Path(str(args.repository)).resolve()
    record = build_run_record(runs_root(repository), run_records_path(), str(args.run))
    if record is None:
        raise CairnError(
            "invalid_run_id", f"no record of run {args.run} against {repository}"
        )
    found = why_excluded(record, str(args.step))
    print(f"step      {found.step_id} — {found.outcome}")
    print(f"cause     {found.cause or 'none'}")
    print(f"meaning   {found.meaning}")
    if found.divergence is not None:
        print(f"diverged  {found.divergence}")
    print(f"next      {found.consequence}")
    return 0


def _run_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cairn run", description=__doc__)
    verbs = parser.add_subparsers(dest="verb", required=True)

    child = verbs.add_parser("offer")
    child.add_argument("--plan", required=True)
    # No default. The repository comes from what was asked for, never from the directory
    # this process happens to be in and never from the workflow ([resolve.py]).
    child.add_argument("--repository", required=True)
    # Optional, because the definition already declares one and that is what gets priced.
    # Given, it is the branch the offer is for and the branch the run will use.
    child.add_argument("--parent-branch")
    child.add_argument("--trigger", choices=TRIGGER_SHAPES, required=True)
    child.add_argument("--recovering")
    child.add_argument("--occasion")

    child = verbs.add_parser("start")
    child.add_argument("--repository", required=True)
    child.add_argument("--offer", required=True)
    # The accepting words, verbatim. Recorded as provenance and checked against the one
    # thing a filesystem can check about them ([consent.py]).
    child.add_argument("--reply", required=True)
    # Minted here when it is not given, so nobody has to invent one. There is no
    # `--parent-branch`: the branch is the offer's, and a term settled after the offer would
    # be one nobody agreed to.
    child.add_argument("--run-id")
    # The default is detached: the command returns once the engine has the run, because a
    # caller with its own timeout is killed by a start that blocks for the whole run and
    # the acceptance dies with it ([19 B]). `--wait` is for a caller that wants the engine's
    # exit status in line and has no timeout of its own.
    child.add_argument("--wait", action="store_true")
    return parser


def _explain_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cairn explain", description=__doc__)
    verbs = parser.add_subparsers(dest="verb", required=True)

    child = verbs.add_parser("workflow")
    child.add_argument("--plan", required=True)
    child.add_argument("--repository", required=True)

    child = verbs.add_parser("word")
    # argparse itself refuses a word no vocabulary holds, so there is no second list of
    # explainable words anywhere to keep in step with the vocabularies.
    child.add_argument("word", choices=explainable())

    child = verbs.add_parser("exclusion")
    child.add_argument("--run", required=True)
    child.add_argument("--step", required=True)
    child.add_argument("--repository", required=True)
    return parser


def run_main(argv: list[str]) -> int:
    args = _run_parser().parse_args(argv)
    try:
        return _cmd_offer(args) if args.verb == "offer" else _cmd_start(args)
    except CairnError as refused:
        print(f"refused  {refused}", file=sys.stderr)
        return EXIT_REFUSED


def explain_main(argv: list[str]) -> int:
    args = _explain_parser().parse_args(argv)
    handlers = {
        "workflow": _cmd_explain_workflow,
        "word": _cmd_explain_word,
        "exclusion": _cmd_explain_exclusion,
    }
    try:
        return handlers[str(args.verb)](args)
    except CairnError as refused:
        print(f"refused  {refused}", file=sys.stderr)
        return EXIT_REFUSED


__all__ = ["explain_main", "mint_run_id", "run_main"]
