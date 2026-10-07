"""`python3 -m cairn run` and `python3 -m cairn explain` — what the skill invokes.

Like `cairn plan`, `cairn workflow`, `cairn schedule`, `cairn record` and `cairn report`,
these run outside any run: they resolve no runtime identity and leave no step report. They
are an implementation surface for the skill, not a user interface — a person asks for what
they want and never learns one of these lines.

`run` has one verb, `start`: a request to run is the go-ahead, so it gates the definition,
begins the run and hands back the run's id and where it can be watched. `explain` has four
verbs, one per question it answers, and none of them starts, locks or writes anything.

**Every one of them says which repository it took and where that came from**, in its first
line, before whatever else it prints. A repository is resolved from what was named, from
where the request's subjects live, or from the session's own directory ([resolve.py]) — so
the line that says which candidate answered is what a person checks the answer against.
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
from cairn.locks import refuse_dirty_repository, refuse_unresolved_merge
from cairn.marker import mint_occasion
from cairn.record.store import build_run_record
from cairn.skill.explain import explainable, meaning, why_excluded, would_do
from cairn.skill.resolve import (
    OccasionDecision,
    OccasionSignal,
    Resolved,
    decide_occasion,
    refuse_foreign_recovery,
    refuse_missing_definition,
    repository_line,
    resolve_repository,
)
from cairn.skill.trigger import (
    EngineUnavailable,
    Launch,
    address,
    declared_branch,
    refuse_uncarriable,
    refuse_unusable_engine,
    snapshot_admitted,
    start,
)
from cairn.skill.vocabulary import TRIGGER_SHAPES
from cairn.workflow.gate import Admission, admit
from cairn.workflow.schema import LABEL_GRAPH_DIGEST, PARENT_BRANCH_PARAM
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


def _resolved(args: argparse.Namespace) -> Resolved:
    """The repository this invocation is about, said back on the way through.

    The one place these commands settle it, and the line is printed here rather than by each
    caller so that no surface can resolve a repository without saying which one it took.
    """
    resolution = resolve_repository(
        args.repository,
        subjects=[Path(subject) for subject in (args.subject or ())],
        session=Path(args.session) if args.session else None,
    )
    if not isinstance(resolution, Resolved):
        raise CairnError("invalid_arguments", resolution.question)
    print(repository_line(resolution))
    return resolution


def _repository_arguments(child: argparse.ArgumentParser) -> None:
    """The three candidates a repository is resolved from, and no default among them.

    `--session` rather than the process's own directory: these commands are run from the
    skill's directory, so the process's own directory answers "Cairn's checkout" to every question and a
    default of `.` would be a wrong answer that always looks plausible ([resolve.py]).
    """
    child.add_argument("--repository")
    child.add_argument("--subject", action="append")
    child.add_argument("--session")


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


def _admitted_graph(admission: Admission) -> str | None:
    """The graph digest the admitted bytes carry — the very bytes the engine will run."""
    try:
        document: Any = json.loads(admission.body)
    except ValueError:
        return None
    labels: Any = cast(dict[str, Any], document).get("labels") if isinstance(document, dict) else None
    digest: Any = cast(dict[str, Any], labels).get(LABEL_GRAPH_DIGEST) if isinstance(labels, dict) else None
    return digest if isinstance(digest, str) else None


def _launch(
    args: argparse.Namespace, repository: Path, run_id: str
) -> tuple[Launch, OccasionDecision]:
    """Everything the run is asked for, settled and gated before anything is written.

    Returns the launch and the occasion decision, which is stated where it is worth stating.
    The admitted bytes are what the engine runs: they are snapshotted here under the run's id,
    so a definition edited between the gate and the engine's read cannot change the run.
    """
    plan = str(args.plan)
    workflow = workflow_path(repository, plan)
    refuse_missing_definition(workflow, plan, str(repository))
    resolved = _repository(str(repository), workflow)
    admission, faults = admit(workflow, expected_plan=plan)
    if admission is None:
        detail = "\n".join(f"refused  {fault}" for fault in faults)
        raise CairnError(
            "workflow_not_admitted",
            f"{workflow} did not pass the execution gate:\n{detail}",
        )
    record = None
    if args.recovering:
        record = build_run_record(
            runs_root(resolved), run_records_path(), str(args.recovering)
        )
        if record is None:
            raise CairnError(
                "invalid_arguments",
                f"no record of run {args.recovering} against {resolved}, so there is "
                "nothing to continue",
            )
        refuse_foreign_recovery(
            record,
            run_id=str(args.recovering),
            plan=plan,
            graph_sha256=_admitted_graph(admission),
        )
    reading = decide_occasion(
        OccasionSignal(
            trigger=str(args.trigger),
            named_run=args.recovering,
            pinned=args.occasion,
            prior_runs=1 if _has_run_before(resolved, plan) else 0,
        ),
        record,
    )
    branch = args.parent_branch or declared_branch(admission)
    if branch is None:
        raise CairnError(
            "invalid_arguments",
            f"{workflow} declares no {PARENT_BRANCH_PARAM} and none was asked for, so "
            "there is no branch this run could land on",
        )
    refuse_uncarriable(f"{PARENT_BRANCH_PARAM}={branch}")
    snapshot = snapshot_admitted(resolved, run_id, workflow.name, admission)
    launch = Launch(
        plan=plan,
        workflow=str(snapshot),
        repository=str(resolved),
        parent_branch=branch,
        occasion=reading.occasion,
    )
    return launch, reading


def _cmd_start(args: argparse.Namespace) -> int:
    """Begin the run that was asked for.

    Everything that can refuse is asked before anything is written or launched, so a refusal
    here leaves the repository as it was.
    """
    run_id = mint_run_id() if args.run_id is None else str(args.run_id)
    try:
        repository = _resolved(args).repository
        check_run_id(run_id)
        refuse_unusable_engine()
        # The working tree, read in the same breath as the engine: the run's first act
        # refuses a dirty tree and an unresolved merge. Asked here through the very
        # functions the lock asks it through, so the two refusals are one refusal; the
        # run's own stays as the backstop for a tree that dirties itself in between ([24 D]).
        refuse_unresolved_merge(repository)
        refuse_dirty_repository(repository)
        # Asked here rather than where it is used: it shells out to the engine and can
        # refuse, and every refusal has to happen before the launch is composed.
        records = run_records_path()
    except EngineUnavailable as unavailable:
        print(f"refused  {unavailable}", file=sys.stderr)
        return EXIT_REFUSED
    except CairnError as unsettled:
        print(f"refused  {unsettled.cause}: {unsettled}", file=sys.stderr)
        return EXIT_REFUSED
    except ValueError as malformed:
        print(f"refused  {run_id!r} is not a run id: {malformed}", file=sys.stderr)
        return EXIT_REFUSED

    launch, reading = _launch(args, repository, run_id)

    # Composed before the engine is invoked, so a start that dies leaves the run id and the
    # invocation it was about to make ([19 B]).
    where = address(launch, run_id, runs_root(repository))

    # **Printed before the launch.** These lines are known before the engine is invoked, and
    # a caller killed while the engine is starting has still been told the name of the run.
    print(f"started  {where.run_id}")
    print(f"branch   verified work lands on {launch.parent_branch}")
    print(f"watch    {where.view}")
    print(f"read     python3 -m cairn report --run {where.run_id} --repository {repository}")
    if reading.disclose:
        print(f"occasion  {reading.reading}: {reading.taken}")
        print(f"          the other reading would mean: {reading.forgone}")

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
        # engine status this command must not swallow. A run it *took on* is a different
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


def _cmd_explain_repository(args: argparse.Namespace) -> int:
    """Which repository this request is about, asked on its own.

    The question every capability settles before it does anything, answerable without doing
    any of it: authoring writes into the repository's admin directory and scheduling installs
    against it, so both need the answer — and the provenance — before their first command.
    """
    _resolved(args)
    return 0


def _cmd_explain_workflow(args: argparse.Namespace) -> int:
    repository = _resolved(args).repository
    workflow = workflow_path(repository, str(args.plan))
    refuse_missing_definition(workflow, str(args.plan), str(repository))
    account = would_do(workflow, str(args.plan))
    print(f"plan      {account.plan}")
    print(f"target    {account.repository} on {account.parent_branch}")
    print(f"schedule  {account.schedule or 'none — it runs when it is asked to'}")
    print(f"agents    {account.agent_steps} agent step(s)")
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
    repository = _resolved(args).repository
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

    child = verbs.add_parser("start")
    child.add_argument("--plan", required=True)
    _repository_arguments(child)
    # Optional, because the definition already declares one. Given, it is the branch the
    # run will use.
    child.add_argument("--parent-branch")
    child.add_argument("--trigger", choices=TRIGGER_SHAPES, required=True)
    child.add_argument("--recovering")
    child.add_argument("--occasion")
    # Minted here when it is not given, so nobody has to invent one.
    child.add_argument("--run-id")
    # The default is detached: the command returns once the engine has the run, because a
    # caller with its own timeout is killed by a start that blocks for the whole run
    # ([19 B]). `--wait` is for a caller that wants the engine's exit status in line and has
    # no timeout of its own.
    child.add_argument("--wait", action="store_true")
    return parser


def _explain_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cairn explain", description=__doc__)
    verbs = parser.add_subparsers(dest="verb", required=True)

    child = verbs.add_parser("repository")
    _repository_arguments(child)

    child = verbs.add_parser("workflow")
    child.add_argument("--plan", required=True)
    _repository_arguments(child)

    child = verbs.add_parser("word")
    # argparse itself refuses a word no vocabulary holds, so there is no second list of
    # explainable words anywhere to keep in step with the vocabularies.
    child.add_argument("word", choices=explainable())

    child = verbs.add_parser("exclusion")
    child.add_argument("--run", required=True)
    child.add_argument("--step", required=True)
    _repository_arguments(child)
    return parser


def run_main(argv: list[str]) -> int:
    args = _run_parser().parse_args(argv)
    try:
        return _cmd_start(args)
    except CairnError as refused:
        print(f"refused  {refused}", file=sys.stderr)
        return EXIT_REFUSED


def explain_main(argv: list[str]) -> int:
    args = _explain_parser().parse_args(argv)
    handlers = {
        "repository": _cmd_explain_repository,
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
