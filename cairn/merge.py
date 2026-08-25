"""Landing a wave's branches on the parent branch, one at a time, halting rather than guessing.

This is the one place in the git layer that uses judgement. Everything else here is
deterministic, and merging is not: a conflict between two steps' edits is a question about
intent, and the plan is the only place that intent is written down. So the conflict is the
agent's, and everything around it exists to bound the damage to exactly that case — a
merge that meets no conflict never reaches an agent at all, and one that does is proven
against git afterwards rather than believed.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any, NamedTuple, TypedDict, cast

from cairn.core import (
    EXIT_FAILED,
    EXIT_OK,
    CairnError,
    CommandResult,
    RuntimeContext,
    read_step_report,
)
from cairn.gitio import (
    REDIRECTING_VARIABLES,
    branch_exists,
    checked_out_branch,
    committed_paths,
    git,
    is_ancestor,
    read_committed_text,
    resolve_ref,
    tree_state,
)
from cairn.locks import git_write_mutex, refuse_unresolved_merge, unresolved_merge
from cairn.providers import run_provider
from cairn.verify import EXCLUSION_CAUSES, GATE_INDETERMINATE, NOT_REACHED, mark_name

# What a prediction can say. `unavailable` is a third answer and never a verdict: git
# declining to predict is not a clean merge and not a conflicted one.
CLEAN = "clean"
CONFLICTED = "conflicted"
UNAVAILABLE = "unavailable"

# What a candidate branch is, before anything is merged.
MERGEABLE = "mergeable"
NOTHING_TO_MERGE = "nothing_to_merge"
EXCLUDED = "excluded"

# What settled a slot, recorded so the run record can tell a merge that cost a session
# from one that cost a command.
BY_COMMAND = "command"
BY_AGENT = "agent"

# An object id in either format git writes, and the only thing that tells a prediction from
# a failure to make one.
OBJECT_ID = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")

# git writes a label after the opening and closing markers and nothing after the
# separator. Requiring the label is what keeps the scan off a Markdown setext underline,
# which is a run of `=` on its own line and is otherwise indistinguishable.
CONFLICT_MARKERS = ("<<<<<<< ", ">>>>>>> ")

# The subset of git's redirecting variables that can send a *write* somewhere else. Cairn
# strips all of them from its own invocations, but a resolving session inherits the step's
# environment as it is. The two omitted — a discovery ceiling and a boolean — change where
# git looks, never where it writes, and refusing on those would halt correct merges.
RETARGETING_VARIABLES = tuple(
    name
    for name in REDIRECTING_VARIABLES
    if name not in ("GIT_CEILING_DIRECTORIES", "GIT_DISCOVERY_ACROSS_FILESYSTEM")
)


class Prediction(NamedTuple):
    """What a read-only three-way merge says about a pair, before either is landed."""

    left: str
    right: str
    outcome: str
    paths: tuple[str, ...]
    note: str


class Candidate(NamedTuple):
    """One branch a slot could land, and why it is or is not available to land."""

    branch: str
    step: str
    disposition: str
    cause: str | None
    summary: str


class MergeFacts(NamedTuple):
    """Everything the proof reads, gathered before any of it is judged.

    The gathering touches git and the judging does not, so every way a merge can lie is a
    unit test rather than a workflow run.
    """

    into: str
    landed: str | None
    before: str
    after: str
    ancestor: bool
    pending: str | None
    # None on all three where git declined to answer, which is never the same as an empty
    # answer: a check that cannot be made must close the proof rather than pass it.
    dirty: tuple[str, ...] | None
    changed: tuple[str, ...] | None
    marked: tuple[str, ...] | None


class MergeVerdict(TypedDict):
    """What the proof decided, before it becomes a report and an exit status."""

    proven: bool
    cause: str | None
    summary: str
    # None where the check could not be made, so a record never states a fault nobody saw.
    checks: dict[str, bool | None]


ProviderCall = Callable[..., CommandResult]


def merge_prompt(branch: str, into: str, conflicted: Sequence[str]) -> str:
    """The task the resolving agent is given, and the whole of what it is asked to decide."""
    files = "\n".join(f"  {path}" for path in conflicted)
    return MERGE_TASK.format(branch=branch, into=into, files=files)


MERGE_TASK = """\
A merge of the branch {branch} into {into} is in progress in this repository and has \
stopped on a conflict. These files are conflicted:

{files}

Resolve them so that the intended change from both sides survives. Read the commits on \
each side — `git log --merge -p` shows them — and work out what each was for. The two \
sides are steps of one plan, so both intentions are meant to hold at once.

Then: stage the resolved files and complete the merge with a commit. Leave no conflict \
marker anywhere, in any file, including any you edit by hand.

If a resolution is not clearly correct — if you cannot tell what one side meant, or the \
two intentions genuinely contradict — stop and report failure, naming the files and what \
disagrees. Leave the merge exactly as you found it. Do not run `git merge --abort`, `git \
reset` or `git checkout` to undo it: the half-finished merge is what a person needs to \
settle this, and discarding it only means the next run stops in the same place.

Never resolve a conflict by taking one side wholesale to make the merge pass.\
"""


def classify_prediction(
    left: str, right: str, exit_code: int, stdout: str, stderr: str
) -> Prediction:
    """Read `git merge-tree`'s answer, which the exit status alone does not carry.

    Measured against git 2.42.1: a clean merge exits 0, a conflicted one exits 1 with the
    merged tree's id on the first line, and **a ref that does not resolve also exits 1**,
    with nothing on stdout — so exit 1 alone means either "these conflict" or "I could not
    look". The object id is the discriminator. Unrelated histories exit 128.

    The documented contract says an error exits something other than 0 or 1; this git does
    not honour that, and a prediction that trusted it would read a broken ref as a conflict
    in every file.
    """
    if exit_code == 0:
        return Prediction(left, right, CLEAN, (), "")
    lines = stdout.splitlines()
    if exit_code == 1 and lines and OBJECT_ID.match(lines[0]):
        # The paths run until the blank line that separates them from git's own prose.
        paths: list[str] = []
        for line in lines[1:]:
            if not line:
                break
            paths.append(line)
        return Prediction(left, right, CONFLICTED, tuple(paths), "")
    return Prediction(left, right, UNAVAILABLE, (), stderr or stdout)


def predict(repository: Path, left: str, right: str) -> Prediction:
    """A read-only three-way merge, which touches no ref, no index and no working tree.

    One pair per invocation: with `--stdin` the engine's own exit status is 0 for a
    conflicted merge too, so a batch would predict everything clean.
    """
    outcome = git(
        repository,
        ("merge-tree", "--write-tree", "--name-only", left, right),
        check=False,
    )
    return classify_prediction(left, right, outcome.exit_code, outcome.stdout, outcome.stderr)


def predict_wave(repository: Path, branches: Sequence[str]) -> list[Prediction]:
    ordered = sorted(branches)
    return [
        predict(repository, left, right)
        for index, left in enumerate(ordered)
        for right in ordered[index + 1 :]
    ]


def overlap(branch: str, predictions: Iterable[Prediction]) -> int:
    """How much conflict this branch is predicted to bring, as a weight and not a count.

    Conflicted paths summed over every prediction the branch appears in, so a file it
    conflicts with two others in counts twice. Nothing needs the true file count: this only
    ever orders the branches against each other. A conflict git named no file for still
    weighs one, because the measured fact is that an empty file list is not a clean merge.
    """
    return sum(
        max(1, len(prediction.paths))
        for prediction in predictions
        if prediction.outcome == CONFLICTED and branch in (prediction.left, prediction.right)
    )


def landing_order(pending: Sequence[str], predictions: Sequence[Prediction]) -> list[str]:
    """The order to try, lightest first, so the branch that conflicts most lands last.

    This is advice. A slot that follows it still proves what it landed, and a slot that
    could not be advised at all lands in a settled order rather than an arbitrary one.
    """
    return sorted(pending, key=lambda branch: (overlap(branch, predictions), branch))


def unowned_conflicts(predictions: Iterable[Prediction], owned: Iterable[str]) -> list[str]:
    """Predicted conflicts in files no candidate of this wave changes.

    A conflict in a file the plan never touched is not a merge problem: something moved the
    parent branch for a reason outside the plan, and no prompt covers that resolution.
    """
    claimed = set(owned)
    found = {
        path
        for prediction in predictions
        if prediction.outcome == CONFLICTED
        for path in prediction.paths
        if path not in claimed
    }
    return sorted(found)


def scan_markers(files: Sequence[tuple[str, str]]) -> list[str]:
    """Which of these files still carry a conflict marker.

    A file qualifies only when it holds both an opening and a closing marker, each with the
    label git writes after it. The separator alone is not evidence: `=======` on its own
    line is a Markdown setext underline, and a repository whose committed content
    legitimately discusses merges would otherwise redden every run.
    """
    marked: list[str] = []
    for path, content in files:
        lines = content.splitlines()
        if all(any(line.startswith(marker) for line in lines) for marker in CONFLICT_MARKERS):
            marked.append(path)
    return sorted(marked)


def classify_candidate(
    branch: str,
    step: str,
    exists: bool,
    has_work: bool,
    gate_cause: str | None,
    gate_summary: str,
) -> Candidate:
    """What a slot may do with this branch, and why, before anything is merged.

    A branch lands only where the gate recorded its step. A step's own session can commit
    inside its worktree, so a branch can carry commits over a gate that closed, and landing
    those is landing exactly the unverified work the gate refused to record — the commit
    count alone cannot tell that from work the gate approved.

    A branch with nothing to land needs no permission, because there is nothing to permit.
    It is an exclusion only where the gate actually declined; otherwise it is already
    contained in the parent, which is what an earlier slot or an earlier run leaves behind
    and is not something to report against the step.
    """
    if not exists:
        return Candidate(
            branch, step, EXCLUDED, NOT_REACHED, f"{branch} was never created"
        )
    if not has_work:
        if gate_cause is not None and gate_cause != NOT_REACHED:
            return Candidate(branch, step, EXCLUDED, gate_cause, gate_summary)
        return Candidate(
            branch, step, NOTHING_TO_MERGE, None, f"{branch} is already contained in the parent"
        )
    if gate_cause is not None:
        return Candidate(branch, step, EXCLUDED, gate_cause, gate_summary)
    return Candidate(branch, step, MERGEABLE, None, f"{branch} has work to land")


def judge_merge(facts: MergeFacts) -> MergeVerdict:
    """Whether what this slot claims to have landed is actually in the parent branch.

    A merge's success is proven, not reported. An agent that says done over a branch it
    never merged, or that commits a file still carrying markers, fails here — which is the
    whole reason this is a subcommand rather than a shell line in the emitted workflow.
    """
    # A check reads False only where it was made and failed. An unestablished one is None,
    # so the record never states a fault nobody observed.
    checks: dict[str, bool | None] = {
        "ancestry": facts.landed is None or facts.ancestor,
        "settled": facts.pending is None,
        "clean_tree": None if facts.dirty is None else facts.dirty == (),
        "no_conflict_markers": None if facts.marked is None else facts.marked == (),
    }
    if not checks["settled"]:
        return MergeVerdict(
            proven=False,
            cause="merge_conflict",
            summary=f"{facts.pending} is still in progress in the repository",
            checks=checks,
        )
    if not checks["ancestry"]:
        return MergeVerdict(
            proven=False,
            cause="merge_not_landed",
            summary=f"{facts.landed} reported as landed is not an ancestor of {facts.into}",
            checks=checks,
        )
    # A fact git declined to give closes the proof, the same way every fault closes the
    # verify gate. Redoing a merge costs one run; recording an unproven one reaches git.
    if facts.dirty is None or facts.changed is None or facts.marked is None:
        return MergeVerdict(
            proven=False,
            cause="merge_indeterminate",
            summary=f"git would not say what {facts.into} holds after the merge",
            checks=checks,
        )
    if not checks["clean_tree"]:
        return MergeVerdict(
            proven=False,
            cause="repository_dirty",
            summary=f"{facts.into} has uncommitted work after the merge: {', '.join(facts.dirty[:5])}",
            checks=checks,
        )
    if not checks["no_conflict_markers"]:
        return MergeVerdict(
            proven=False,
            cause="conflict_markers_committed",
            summary=f"the merge committed conflict markers in {', '.join(facts.marked)}",
            checks=checks,
        )
    landed = "nothing" if facts.landed is None else facts.landed
    return MergeVerdict(
        proven=True, cause=None, summary=f"{landed} is in {facts.into}", checks=checks
    )


def has_work(repository: Path, branch: str, into: str) -> bool:
    """Whether this branch holds a commit the parent does not.

    This answers only whether there is anything to land, never why there is not. A branch
    whose step was excluded never committed and is therefore already contained in the
    parent, exactly like one whose work landed on an earlier run — so what separates the
    two is the gate's own report and nothing git can be asked.
    """
    counted = git(repository, ("rev-list", "--count", f"{into}..{branch}"), check=False)
    return counted.exit_code == 0 and counted.stdout.strip() not in ("", "0")


def changed_paths(repository: Path, before: str, after: str) -> tuple[str, ...] | None:
    """What the merge changed, or None when git would not say.

    None is not the empty set. A diff Cairn could not run leaves the marker scan with
    nothing to read, and scoring that as "no markers found" would pass the one check this
    step exists to make.
    """
    if not before or not after:
        return None
    outcome = git(repository, ("diff", "--name-only", f"{before}..{after}"), check=False)
    if outcome.exit_code != 0:
        return None
    return tuple(line for line in outcome.stdout.splitlines() if line)


def owned_paths(repository: Path, branches: Sequence[str], into: str) -> list[str]:
    """Every path this wave's branches change, which is the only ownership the plan states.

    A step declares what it reads and never what it writes, so what a step owns is derived
    from what its branch actually changed against the point it forked from. Rename
    detection is off, so a branch that moved a file claims both names: git reports only a
    rename's destination, and a conflict recorded against the source would otherwise look
    like a path nobody owns.
    """
    owned: set[str] = set()
    for branch in branches:
        base = git(repository, ("merge-base", into, branch), check=False)
        if base.exit_code != 0:
            continue
        outcome = git(
            repository,
            ("diff", "--no-renames", "--name-only", f"{base.stdout}..{branch}"),
            check=False,
        )
        if outcome.exit_code == 0:
            owned.update(line for line in outcome.stdout.splitlines() if line)
    return sorted(owned)


def committed_markers(
    repository: Path, commit: str, paths: Sequence[str]
) -> tuple[str, ...] | None:
    """Conflict markers in what the merge committed, or None where the scan could not run.

    Read out of the commit rather than the working tree: an agent can tidy a file after
    committing it, and history is what every later run and every later merge will carry.

    Which paths the commit holds is established first, so a file the merge deleted is told
    from one git declined to give. Only the first carries nothing; folding the second into
    it would report a clean scan over a file that was never read.
    """
    if not paths:
        return ()
    present = committed_paths(repository, commit)
    if present is None:
        return None
    contents: list[tuple[str, str]] = []
    for path in paths:
        if path not in present:
            continue
        content = read_committed_text(repository, commit, path)
        if content is None:
            return None
        contents.append((path, content))
    return tuple(scan_markers(contents))


def survey(
    repository: Path,
    candidates: Sequence[str],
    into: str,
    reports: Path,
    run_id: str,
) -> list[Candidate]:
    """Each candidate branch's disposition, and for an excluded one the gate's own cause."""
    surveyed: list[Candidate] = []
    for branch in candidates:
        step = branch.split("/", 1)[-1]
        exists = branch_exists(repository, branch)
        carries_work = exists and has_work(repository, branch, into)
        cause, summary = (
            _gate_cause(reports, step, run_id, f"{branch} contributed no verified work")
            if exists
            else (None, "")
        )
        surveyed.append(
            classify_candidate(branch, step, exists, carries_work, cause, summary)
        )
    return surveyed


def _gate_cause(reports: Path, step: str, run_id: str, fallback: str) -> tuple[str | None, str]:
    """Why the gate declined to record this step, quoted from its own report.

    None means it did not decline — the step's work is recorded and its branch may land.
    The merge never mints an exclusion cause: the gate froze that vocabulary, and a second
    spelling for the same fact would put two words in the run record for one event.
    """
    try:
        report = read_step_report(reports, mark_name(step), run_id)
    except CairnError as exc:
        # A report that is absent and one that cannot be read are different facts. Folding
        # the second into the first would claim a step never ran when it may have done all
        # of its work and left an account nothing can parse.
        if exc.cause == "missing_report":
            return NOT_REACHED, f"{step} left no gate report of this run"
        return GATE_INDETERMINATE, f"{step} left a gate report that cannot be read"
    cause = report.get("cause")
    if cause is None and report.get("status") != "failed":
        # The marker step ran, which only a gate that opened allows. Its own report is the
        # evidence that this step's work is recorded, so there is nothing to exclude.
        return None, f"{step} is recorded as verified"
    if not isinstance(cause, str) or cause not in EXCLUSION_CAUSES:
        return GATE_INDETERMINATE, f"{step} left a gate report naming no known cause"
    summary = report.get("summary")
    return cause, summary if isinstance(summary, str) and summary else fallback


def _refuse_redirected_environment() -> None:
    """Refuse before spending if anything would point the agent's git elsewhere.

    Cairn strips these from its own invocations, but the resolving agent inherits the
    step's environment as it is — and an inherited `GIT_DIR` sends its resolution commit to
    a repository nobody named, which this step would then correctly report as unlanded an
    agent session later.
    """
    redirected = [name for name in RETARGETING_VARIABLES if os.environ.get(name)]
    if redirected:
        raise CairnError(
            "merge_environment_redirected",
            f"{', '.join(redirected)} would send the resolution's commits to a repository "
            "this plan never named; unset it before running",
            detail={"variables": redirected},
        )


def inspect_merge(
    repository: Path, into: str, landed: str | None, before: str, after: str
) -> MergeFacts:
    """Gather what the proof reads, keeping "git would not say" apart from "nothing there".

    Both facts git can decline to give — the working tree's state and what the merge
    changed — are carried as None rather than as an empty answer, because the judgement
    treats the two oppositely.
    """
    changed = changed_paths(repository, before, after) if before != after else ()
    return MergeFacts(
        into=into,
        landed=landed,
        before=before,
        after=after,
        ancestor=landed is not None and is_ancestor(repository, landed, into),
        pending=unresolved_merge(repository),
        dirty=tree_state(repository),
        changed=changed,
        marked=None if changed is None else committed_markers(repository, after, changed),
    )


def run_merge(
    repository: Path,
    *,
    slot: int,
    into: str,
    candidates: Sequence[str],
    provider: str,
    model: str | None,
    max_budget_usd: float | None,
    context: RuntimeContext,
    run_agent: ProviderCall = run_provider,
) -> CommandResult:
    """Land one of this wave's branches, or report honestly why none was landed."""
    refuse_unresolved_merge(repository)
    _refuse_redirected_environment()
    on = checked_out_branch(repository)
    if on != into:
        raise CairnError(
            "merge_wrong_branch",
            f"the repository is on {on or 'a detached HEAD'} rather than {into}, and a "
            "merge landed anywhere else is not the plan's",
            detail={"expected": into, "actual": on},
        )
    before = resolve_ref(repository, into)
    if before is None:
        # Named once, up front. An empty revision would make every later range read as one
        # against HEAD, so the marker scan would run over a scope nobody chose.
        raise CairnError(
            "merge_wrong_branch",
            f"{into} names no commit in {repository}, so there is nothing to land onto",
            detail={"expected": into, "actual": on},
        )

    reports = context.report_path.parent
    surveyed = survey(repository, candidates, into, reports, context.run_id)
    excluded = [c for c in surveyed if c.disposition == EXCLUDED]
    pending = [c.branch for c in surveyed if c.disposition == MERGEABLE]
    follow_up = [f"{c.branch} is excluded: {c.summary} ({c.cause})" for c in excluded]
    detail: dict[str, Any] = {
        "slot": slot,
        "into": into,
        "candidates": list(candidates),
        "excluded": [c._asdict() for c in excluded],
    }

    if not pending:
        return CommandResult(
            EXIT_OK,
            "noop",
            f"slot {slot} has nothing left to land",
            follow_up,
            False,
            None,
            {**detail, "landed": None},
        )

    # Each pending branch against the parent as well as against its peers. The pairwise
    # predictions can only name paths the wave itself changed — every branch forks from the
    # parent, so a conflict between two of them is a file both touched. A path no step owns
    # can appear only where the parent is an operand, which is the whole of what "the parent
    # moved for a reason outside the plan" means.
    predictions = [*predict_wave(repository, pending), *(predict(repository, into, b) for b in pending)]
    detail["prediction"] = [p._asdict() for p in predictions]
    unowned = unowned_conflicts(predictions, owned_paths(repository, pending, into))
    if unowned:
        raise CairnError(
            "merge_unowned_conflict",
            f"the merge is predicted to conflict in {', '.join(unowned)}, which no step of "
            "this wave changes — the parent branch moved for a reason outside the plan",
            detail={**detail, "unowned": unowned},
        )

    order = landing_order(pending, predictions)
    branch = order[0]
    detail["order"] = order
    detail["branch"] = branch

    with git_write_mutex(repository):
        merged = git(
            repository,
            ("merge", "--no-ff", "--no-edit", "-m", f"cairn(merge): {branch} into {into}", branch),
            check=False,
        )
    resolved_by = BY_COMMAND
    agent: CommandResult | None = None
    if merged.exit_code != 0:
        # The index decides whether this is a conflict, not git's prose: the same nonzero
        # exit covers a refusal that left nothing behind.
        if unresolved_merge(repository) is None:
            raise CairnError(
                "git_failed",
                f"merging {branch} into {into} failed without leaving a merge to settle: "
                f"{merged.stderr}",
                detail={**detail, "stderr": merged.stderr},
            )
        resolved_by = BY_AGENT
        conflicted = _conflicted_paths(repository)
        if not conflicted:
            # A merge git stopped over something other than conflicted content — a rejecting
            # hook, a signing failure — leaves the same state behind. Handing it to a session
            # would pay for one and tell it that these files are conflicted, then name none.
            raise CairnError(
                "git_failed",
                f"merging {branch} into {into} stopped with no conflicted file to resolve, "
                f"and the merge is left in place to settle: {merged.stderr}",
                detail={**detail, "stderr": merged.stderr},
            )
        detail["conflicted"] = list(conflicted)
        # Outside the write mutex: a session can run for an hour and the mutex's own wait
        # is five minutes, so holding it across one would turn every contender into a
        # failure rather than a wait. Nothing else in the run writes here — the slots are
        # chained, the join is upstream and the prune is downstream.
        agent = run_agent(
            provider,
            merge_prompt(branch, into, conflicted),
            repository,
            "auto",
            model,
            max_budget_usd,
            [],
        )
        if agent.status == "failed" or agent.needs_user_decision:
            # Left exactly as git left it. An abort does not converge: the branch would
            # still be unmerged, so the next run re-attempts the same merge and stops here
            # again, with nothing preserved for a person to settle.
            return agent._replace(
                exit_code=agent.exit_code or EXIT_FAILED,
                cause=agent.cause or "merge_conflict",
                detail={**detail, **agent.detail, "resolved_by": resolved_by},
                follow_up_work=[
                    *agent.follow_up_work,
                    *follow_up,
                    f"settle the merge of {branch} into {into} in {repository}, then re-run",
                ],
            )

    after = resolve_ref(repository, into) or ""
    facts = inspect_merge(repository, into, branch, before, after)
    verdict = judge_merge(facts)
    detail.update(
        {
            # What git holds, which is not the same question as whether it is proven. A
            # merge can reach the parent and still fail its proof, and a record that said
            # nothing landed would leave the next run with no reason to look at it.
            "landed": branch if facts.ancestor else None,
            "proven": verdict["proven"],
            "resolved_by": resolved_by,
            "before": before,
            "after": after,
            "changed": None if facts.changed is None else list(facts.changed),
            "checks": verdict["checks"],
        }
    )
    if not verdict["proven"]:
        unsettled = [f"settle the merge of {branch} into {into} in {repository}, then re-run"]
        if facts.ancestor:
            # The merge is already on the parent branch. A re-run finds the branch landed
            # and attempts nothing, so nothing will catch this a second time.
            unsettled = [
                (
                    f"{after} merged {branch} into {into} and did not pass its proof "
                    f"({verdict['cause']}); settle that commit before re-running, because "
                    "a re-run finds the branch landed and will not look again"
                )
            ]
        return CommandResult(
            EXIT_FAILED,
            "failed",
            verdict["summary"],
            [*follow_up, *unsettled],
            False,
            verdict["cause"],
            detail,
        )
    return CommandResult(
        EXIT_OK,
        "done",
        f"landed {branch} on {into}",
        # A session that resolved the conflict may have found work it did not do, and that
        # reads downstream exactly as a work step's does.
        [*follow_up, *(agent.follow_up_work if agent is not None else [])],
        False,
        None,
        detail if agent is None else {**agent.detail, **detail},
    )


def verify_landed(
    repository: Path,
    *,
    merge: str,
    into: str,
    candidates: Sequence[str],
    context: RuntimeContext,
) -> CommandResult:
    """Prove, in a process of its own, what the slot before it says it landed.

    The slot's report says what to look for; git says whether it is there. What the two
    have in common is only the claim — the answer is read afresh, in a process that starts
    after the slot's has exited, so a slot that died between landing and reporting and one
    whose repository changed afterwards are both caught here.
    """
    reports = context.report_path.parent
    report = read_step_report(reports, merge, context.run_id)
    raw: Any = report.get("detail")
    detail: dict[str, Any] = cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}
    claimed_value: Any = detail.get("landed")
    before_value: Any = detail.get("before")
    claimed = claimed_value if isinstance(claimed_value, str) else None
    before = before_value if isinstance(before_value, str) else ""
    after = resolve_ref(repository, into) or ""
    recorded: dict[str, Any] = {
        "merge": merge,
        "into": into,
        "candidates": list(candidates),
        "claimed": claimed,
        # Seeded so every outcome of this step writes the same shape, including the one
        # that refuses before any check is made.
        "checks": {},
    }
    if not after:
        return CommandResult(
            EXIT_FAILED,
            "failed",
            f"{into} names no commit, so there is nothing to prove landed on it",
            [],
            False,
            "merge_wrong_branch",
            recorded,
        )
    if claimed is not None and claimed not in candidates:
        # The slot may only land what the topology named. A claim outside that list is the
        # one thing this process can catch that re-reading git cannot.
        return CommandResult(
            EXIT_FAILED,
            "failed",
            f"{merge} claims to have landed {claimed}, which is not one of its candidates",
            [],
            False,
            "merge_not_landed",
            recorded,
        )
    facts = inspect_merge(repository, into, claimed, before or after, after)
    verdict = judge_merge(facts)
    recorded["checks"] = verdict["checks"]
    if not verdict["proven"]:
        return CommandResult(
            EXIT_FAILED, "failed", verdict["summary"], [], False, verdict["cause"], recorded
        )
    return CommandResult(EXIT_OK, "done", verdict["summary"], [], False, None, recorded)


def _conflicted_paths(repository: Path) -> tuple[str, ...]:
    outcome = git(
        repository, ("diff", "--name-only", "--diff-filter=U"), check=False
    )
    if outcome.exit_code != 0:
        return ()
    return tuple(line for line in outcome.stdout.splitlines() if line)


__all__ = [
    "CONFLICT_MARKERS",
    "MERGE_TASK",
    "Candidate",
    "MergeFacts",
    "MergeVerdict",
    "Prediction",
    "classify_candidate",
    "classify_prediction",
    "committed_markers",
    "judge_merge",
    "landing_order",
    "merge_prompt",
    "predict",
    "predict_wave",
    "run_merge",
    "scan_markers",
    "survey",
    "unowned_conflicts",
    "verify_landed",
]
