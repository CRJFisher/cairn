"""Building one run's record: Cairn's own reports first, the engine's state as a supplement.

Every step goes through Cairn's CLI, so the reports are uniform across kinds and are the
richer source — they are the only place cost, session identity, turns and an agent's own
account of itself exist at all. The engine contributes what only it holds: when each node
started and finished, what status it reached, where its logs are, and how the run was
triggered.

**The run verdict is derived by walking every node, and the engine's run status is never
read as one.** A run whose exclusions are all `skipped`, with no failed node anywhere,
reports plain `Succeeded` with exit 0, and the engine's own success helper treats
`PartiallySucceeded` as a success variant. Cairn's routing pattern is designed so that a
real exclusion always leaves a `failed` node behind ([verify-gate.md]); this walk is the
check that it did.

Nothing here writes to the engine's own record. A killed run is *read* as failed from the
recorded process and its start time — never from the status field, which says `running`
forever — while repairing that file stays [supervision.md]'s, so the two never race.
"""

from __future__ import annotations

import json
import shlex
from collections.abc import Container, Mapping
from pathlib import Path
from typing import Any, cast

from cairn.layout import view_url
from cairn.providers import resume_command
from cairn.record import engine
from cairn.record.model import (
    Attention,
    Budget,
    Diffstat,
    Divergence,
    Edge,
    EngineNode,
    ExcludedBranch,
    Freshness,
    GitFacts,
    Infrastructure,
    Lineage,
    NextAction,
    RunRecord,
    StepRecord,
    Trigger,
    WaveCensus,
)
from cairn.record.vocabulary import (
    ATTENTION_BLOCKED,
    ATTENTION_BUDGET,
    ATTENTION_DIVERGENCE,
    ATTENTION_EXCLUDED,
    ATTENTION_FAILURE,
    ATTENTION_FOLLOW_UP,
    ATTENTION_HOUSEKEEPING_FAILURE,
    EDGE_DEPENDENCY,
    EDGE_RUN,
    EDGE_STEP,
    EDGE_WAVE,
    NEXT_DECIDE,
    NEXT_NOTHING,
    NEXT_RERUN,
    NEXT_SETTLE_MERGE,
    NEXT_START_SCHEDULER,
    NEXT_WAIT,
    OUTCOME_EXCLUDED,
    OUTCOME_FAILED,
    OUTCOME_NO_OP,
    OUTCOME_NOT_REACHED,
    OUTCOME_PENDING,
    OUTCOME_RUNNING,
    OUTCOME_VERIFIED,
    OVERLAY_BLOCKED,
    OVERLAY_DIVERGENCE,
    OVERLAY_UNVERIFIED,
    OVERLAYS,
    PROVENANCE_ABSENT,
    PROVENANCE_DERIVED,
    RECORD_VERSION,
    VERDICT_ALL_NO_OP,
    VERDICT_BLOCKED,
    VERDICT_EXIT_CODES,
    VERDICT_FAILED,
    VERDICT_GREEN,
    VERDICT_GREEN_WITH_EXCLUSIONS,
    VERDICT_RUNNING,
)
from cairn.supervise import owner_liveness
from cairn.text import (
    LINE_LIMIT,
    TEXT_LIMIT,
    as_count,
    as_money,
    flatten,
    normalise,
    normalise_all,
)
from cairn.topology import BRANCH_PREFIX, RUN_ROLES, WAVE_ROLES, node_name
from cairn.verify import (
    EXCLUSION_CAUSES,
    GATE_INDETERMINATE,
    ORCHESTRATOR_DIED,
    USER_DECISION_REQUIRED,
)
from cairn.workflow.schema import ENGINE_VERSION, OCCASION_PARAM, REPOSITORY_PARAM

# The role whose failure costs a run its worktrees and nothing else. Every other piece of
# Cairn's own housekeeping stands between the plan and its result, so its failure is the
# run's; a prune runs after the landing and can only leave litter behind.
HOUSEKEEPING_ROLES = frozenset({"prune"})

# A step's five roles. `work` is the one that names a step into existence, because every
# step emits exactly one and nothing else does.
WORK_ROLE = "work"
MARK_ROLE = "mark"
COMMIT_ROLE = "commit"


def _provenance(
    fields: Mapping[str, object], derived: Container[str] = ()
) -> dict[str, str]:
    """Where each field's authority sits, listing only what is not plainly recorded.

    The invariant this exists for: a field whose value is None is listed as absent, so an
    absence can never be read as a measured zero. Recorded is the default and is not listed
    — a map that repeated the whole record would double it for no reader.
    """
    marks: dict[str, str] = {}
    for name, value in fields.items():
        if value is None:
            marks[name] = PROVENANCE_ABSENT
        elif name in derived:
            marks[name] = PROVENANCE_DERIVED
    return marks


def _asked(node: dict[str, Any]) -> str | None:
    command = engine.node_command(node)
    return None if command is None else normalise(command, limit=TEXT_LIMIT)


def _reported_text(value: object) -> str | None:
    """One string a step reported about itself, bounded before it enters the record.

    The engine's own fields are its to size; a report's `detail` is an agent's output and is
    held to the same cap as every other untrusted value ([run-model.md]).
    """
    text = engine.text(value)
    return None if text is None else flatten(text, limit=LINE_LIMIT)


def _detail(report: dict[str, Any] | None) -> dict[str, Any]:
    if report is None:
        return {}
    found: Any = report.get("detail")
    return cast(dict[str, Any], found) if isinstance(found, dict) else {}


def read_reports(directory: Path, run_id: str) -> dict[str, dict[str, Any]]:
    """Every account this run's steps left, by the engine node name that wrote it.

    A report from another run is skipped rather than read: reports outlive the run that
    wrote them, and one left by yesterday's run would speak for a step this run never
    started. A report that cannot be parsed is skipped the same way — the extraction of a
    whole run must not die on one damaged file, and the step it belonged to reads as having
    left no account, which is itself an outcome.
    """
    if not directory.is_dir():
        return {}
    found: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.glob("*.json")):
        try:
            raw: Any = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(raw, dict):
            continue
        report = cast(dict[str, Any], raw)
        if report.get("run_id") != run_id or not isinstance(report.get("status"), str):
            continue
        found[path.stem] = report
    return found


def _freshness(report: dict[str, Any] | None) -> Freshness | None:
    detail = _detail(report)
    scope = detail.get("recorded_scope")
    key = detail.get("recorded_key")
    if not isinstance(scope, str) or not isinstance(key, str):
        return None
    return Freshness(
        scope=str(detail.get("scope", scope)),
        key=str(detail.get("key", key)),
        recorded_scope=scope,
        recorded_key=key,
    )


def _divergence(report: dict[str, Any] | None) -> Divergence | None:
    found: Any = _detail(report).get("divergence")
    if not isinstance(found, dict):
        return None
    entry = cast(dict[str, Any], found)
    reported = entry.get("reported")
    asserted = entry.get("asserted")
    if not isinstance(reported, str) or not isinstance(asserted, bool):
        return None
    return Divergence(reported=reported, asserted=asserted)


def _cause(report: dict[str, Any] | None) -> str | None:
    """The exclusion the gate recorded, quoted only where it is one of the frozen causes.

    A gate report naming something outside the vocabulary is a report Cairn cannot read, and
    saying so is honest where passing the string through would mint an eighth cause.
    """
    if report is None:
        return None
    found = report.get("cause")
    if found is None:
        return None
    return found if isinstance(found, str) and found in EXCLUSION_CAUSES else GATE_INDETERMINATE


def classify_step(
    *,
    work_status: int | None,
    mark_status: int | None,
    work_report: dict[str, Any] | None,
    mark_report: dict[str, Any] | None,
    has_assertion: bool,
    run_settled: bool,
    orchestrator_gone: bool,
) -> tuple[str, list[str], str | None]:
    """One step's outcome, its overlays and its cause, from the engine and the reports together.

    Read top to bottom; the first case that matches decides. The engine says whether the
    step ran, and Cairn's own reports say what came of it — neither can raise what the other
    lowered, which is the same rule the verify gate itself is built on.
    """
    overlays: list[str] = []
    if not has_assertion:
        overlays.append(OVERLAY_UNVERIFIED)
    reported: str | None = None
    if work_report is not None:
        status = work_report.get("status")
        reported = status if isinstance(status, str) else None

    # The marker gate skipped the step and left the one report that says so. This outranks
    # every node status because a no-op is the only outcome the engine spells `skipped` and
    # Cairn can prove was correct.
    if reported == "noop":
        return OUTCOME_NO_OP, overlays, None

    if work_status == engine.NODE_STATUS_ABORTED:
        return OUTCOME_NOT_REACHED, overlays, None
    if work_status is None or work_status == engine.NODE_STATUS_NOT_STARTED:
        return (
            OUTCOME_NOT_REACHED if run_settled else OUTCOME_PENDING
        ), overlays, None
    if work_status == engine.NODE_STATUS_RUNNING:
        if orchestrator_gone:
            return OUTCOME_FAILED, overlays, ORCHESTRATOR_DIED
        return OUTCOME_RUNNING, overlays, None
    if work_status == engine.NODE_STATUS_SKIPPED:
        # Skipped with no no-op report: the gate ran and decided, but left nothing behind
        # that says what it decided, so the step's fate is unestablished rather than fresh.
        return OUTCOME_EXCLUDED, overlays, GATE_INDETERMINATE

    # The step ran. What came of it is the gate's to say.
    if _divergence(mark_report) is not None:
        overlays.append(OVERLAY_DIVERGENCE)
    if work_report is not None and work_report.get("needs_user_decision") is True:
        overlays.append(OVERLAY_BLOCKED)
        return OUTCOME_EXCLUDED, _ordered(overlays), USER_DECISION_REQUIRED

    cause = _cause(mark_report)
    if cause == USER_DECISION_REQUIRED:
        overlays.append(OVERLAY_BLOCKED)
        return OUTCOME_EXCLUDED, _ordered(overlays), cause
    if mark_status == engine.NODE_STATUS_SUCCEEDED:
        return OUTCOME_VERIFIED, _ordered(overlays), None
    if cause is not None:
        return OUTCOME_EXCLUDED, _ordered(overlays), cause
    if work_status == engine.NODE_STATUS_FAILED:
        return OUTCOME_FAILED, _ordered(overlays), None
    # The work node succeeded and nothing recorded it as verified — no marker step, or one
    # that ran and said nothing. Either way the step's own end state was never asserted, and
    # a record that called that verified would be the marker-over-unverified-work failure the
    # gate itself fails closed to prevent.
    return OUTCOME_EXCLUDED, _ordered(overlays), GATE_INDETERMINATE


def _ordered(overlays: list[str]) -> list[str]:
    return [overlay for overlay in OVERLAYS if overlay in overlays]


def _edge_kind(upstream: engine.Naming | None, downstream: engine.Naming | None) -> str:
    if upstream is None or downstream is None:
        return EDGE_DEPENDENCY
    if upstream.role in RUN_ROLES or downstream.role in RUN_ROLES:
        return EDGE_RUN
    if upstream.role in WAVE_ROLES or downstream.role in WAVE_ROLES:
        return EDGE_WAVE
    if upstream.subject == downstream.subject:
        return EDGE_STEP
    return EDGE_DEPENDENCY


def census_exclusions(waves: list[WaveCensus]) -> list[ExcludedBranch]:
    """Every branch a wave's join declined, in wave order then branch order.

    A wave exclusion is an exclusion of the run, and it is the one kind no step outcome can
    speak for: the join reads a step's gate report while the record reads the gate's *node*,
    so a step whose report is damaged is declined by the join and verified by the walk. The
    branch was still dropped, and I5 admits no run that dropped work reporting a clean
    success — so the census is read into the verdict rather than only into the git facts.
    """
    return [entry for census in waves for entry in census["excluded"]]


def derive_verdict(
    steps: list[StepRecord],
    infrastructure: list[Infrastructure],
    engine_state: str,
    waves: list[WaveCensus],
) -> str:
    """The run's own verdict, from every node it has and never from the engine's word for the run.

    A queued run reads as running rather than as an outcome: anything triggered externally
    arrives that way and may sit there indefinitely if no scheduler is up, and every one of
    its nodes is at not-started.
    """
    outcomes = {step["outcome"] for step in steps}
    # Everything of Cairn's own that stands between the plan and its result. A prune is
    # deliberately not among them: it runs after the landing, so its failure leaves litter
    # rather than changing what the run achieved.
    load_bearing = {
        item["outcome"]
        for item in infrastructure
        if item["role"] not in HOUSEKEEPING_ROLES
    }
    if OUTCOME_FAILED in outcomes or OUTCOME_NOT_REACHED in outcomes:
        return VERDICT_FAILED
    if OUTCOME_FAILED in load_bearing or OUTCOME_NOT_REACHED in load_bearing:
        return VERDICT_FAILED
    if any(OVERLAY_BLOCKED in step["overlays"] for step in steps):
        return VERDICT_BLOCKED
    if OUTCOME_RUNNING in outcomes or OUTCOME_PENDING in outcomes:
        return VERDICT_RUNNING
    if engine_state in (engine.RUN_RUNNING, engine.RUN_QUEUED):
        return VERDICT_RUNNING
    if not outcomes:
        # No step at all. Every verdict here is a statement about steps, so there is nothing
        # for this run to have succeeded at — and reading it as green is the exact shape I5
        # forbids, a run that achieved nothing presented as a clean success. It fails closed,
        # the way the verify gate does, because the alternative is silent.
        #
        # This sits above the exclusion clause rather than below it: a run whose steps all
        # vanished and whose join report survived would otherwise read as green-with-
        # exclusions, which is a near-clean verdict over a run that recorded nothing at all.
        return VERDICT_FAILED
    if OUTCOME_EXCLUDED in outcomes or census_exclusions(waves):
        return VERDICT_GREEN_WITH_EXCLUSIONS
    if outcomes == {OUTCOME_NO_OP}:
        return VERDICT_ALL_NO_OP
    return VERDICT_GREEN


def derive_attention(
    steps: list[StepRecord],
    infrastructure: list[Infrastructure],
    budget: Budget,
    waves: list[WaveCensus],
) -> list[Attention]:
    """Everything a reader has to act on, assembled in the frozen order rather than sorted into it."""
    items: list[Attention] = []
    for step in steps:
        if OVERLAY_BLOCKED in step["overlays"]:
            items.append(
                Attention(
                    kind=ATTENTION_BLOCKED,
                    subject=step["step_id"],
                    summary=step["said"] or "a human decision is owed before this can proceed",
                    cause=step["cause"],
                )
            )
    for step in steps:
        if step["outcome"] in (OUTCOME_FAILED, OUTCOME_NOT_REACHED):
            items.append(
                Attention(
                    kind=ATTENTION_FAILURE,
                    subject=step["step_id"],
                    summary=step["said"] or f"the step is {step['outcome']}",
                    cause=step["cause"],
                )
            )
    for step in steps:
        if step["outcome"] == OUTCOME_EXCLUDED and OVERLAY_BLOCKED not in step["overlays"]:
            items.append(
                Attention(
                    kind=ATTENTION_EXCLUDED,
                    subject=step["step_id"],
                    summary=step["said"] or "the step contributed no verified work",
                    cause=step["cause"],
                )
            )
    # A branch whose own step is named above for the same reason is the same event seen
    # twice: the join reads the gate's report and this walk reads the gate's node. But the
    # two disagree exactly when something went wrong between them — a blocked step whose
    # report the join could not read is two facts, not one — so the cause is part of the
    # match. Suppressing on the step's identity alone would drop the only line naming a
    # dropped branch.
    spoken_for = {
        (f"{BRANCH_PREFIX}{step['step_id']}", step["cause"])
        for step in steps
        if step["outcome"] in (OUTCOME_EXCLUDED, OUTCOME_FAILED, OUTCOME_NOT_REACHED)
    }
    for entry in census_exclusions(waves):
        if (entry["branch"], entry["cause"]) in spoken_for:
            continue
        items.append(
            Attention(
                kind=ATTENTION_EXCLUDED,
                subject=entry["branch"],
                summary=entry["summary"] or "the branch carried no work the gate would land",
                cause=entry["cause"],
            )
        )
    if budget["notional"] and budget["cost_usd"]:
        items.append(
            Attention(
                kind=ATTENTION_BUDGET,
                subject="run",
                # The figure itself is deliberately not spelled here. The record carries the
                # cost once, in `budget`, and a second formatting of it would be a second
                # spelling of one number for every surface to choose between.
                summary=(
                    "the run's cost is an API-equivalent price rather than money spent"
                ),
                cause=None,
            )
        )
    for item in infrastructure:
        if item["outcome"] in (OUTCOME_FAILED, OUTCOME_NOT_REACHED):
            items.append(
                Attention(
                    kind=ATTENTION_HOUSEKEEPING_FAILURE,
                    subject=item["name"],
                    summary=item["summary"] or f"the step is {item['outcome']}",
                    cause=item["cause"],
                )
            )
    for step in steps:
        if OVERLAY_DIVERGENCE in step["overlays"] and step["divergence"] is not None:
            items.append(
                Attention(
                    kind=ATTENTION_DIVERGENCE,
                    subject=step["step_id"],
                    summary=(
                        f"the step reported {step['divergence']['reported']!r} over an "
                        f"assertion that "
                        f"{'passed' if step['divergence']['asserted'] else 'failed'}"
                    ),
                    cause=step["cause"],
                )
            )
    for step in steps:
        for found in step["follow_up_work"]:
            items.append(
                Attention(
                    kind=ATTENTION_FOLLOW_UP,
                    subject=step["step_id"],
                    summary=found,
                    cause=None,
                )
            )
    return items


def derive_next_action(
    verdict: str,
    steps: list[StepRecord],
    engine_state: str,
    run_id: str,
    plan: str | None,
    waves: list[WaveCensus],
    orchestrator_gone: bool,
) -> NextAction:
    """What a reader does now, in one value plus the command that does it.

    A command is carried only where one can be spelled correctly and completely. The retry
    needs the plan as well as the run, because the engine takes the run as a flag and the
    plan as its operand — a command missing either is a command that fails when it is
    pasted, which is worse than the report saying plainly that it has none.
    """
    if verdict == VERDICT_BLOCKED:
        blocked = next(
            (step["step_id"] for step in steps if OVERLAY_BLOCKED in step["overlays"]),
            None,
        )
        return NextAction(action=NEXT_DECIDE, subject=blocked, command=None)
    if engine_state == engine.RUN_QUEUED:
        return NextAction(
            action=NEXT_START_SCHEDULER,
            subject=None,
            # Never the bare engine command: starting a scheduler re-executes every failed
            # run on the machine from the previous day unless the machine-wide retry
            # override is in place, and this verb is where that is asserted ([triggers.md]).
            command="python3 -m cairn schedule start --accept-daemon",
        )
    if verdict == VERDICT_RUNNING:
        return NextAction(action=NEXT_WAIT, subject=None, command=None)
    if verdict == VERDICT_FAILED:
        # A killed run stays `Running` in the engine's own record, and `dagu retry` refuses a
        # run it still believes is going ([01-engine-spike.md]) — so the retry would fail when
        # pasted, and the reconciliation that would unblock it takes a path this derivation
        # does not carry. A command is carried only where it can be spelled correctly and
        # completely, and here it cannot be.
        if orchestrator_gone:
            return NextAction(action=NEXT_RERUN, subject=None, command=None)
        return NextAction(action=NEXT_RERUN, subject=None, command=_retry_command(run_id, plan))
    if verdict == VERDICT_GREEN_WITH_EXCLUSIONS:
        excluded = next(
            (step["step_id"] for step in steps if step["outcome"] == OUTCOME_EXCLUDED),
            None,
        ) or next((entry["branch"] for entry in census_exclusions(waves)), None)
        return NextAction(action=NEXT_SETTLE_MERGE, subject=excluded, command=None)
    return NextAction(action=NEXT_NOTHING, subject=None, command=None)


def _retry_command(run_id: str, plan: str | None) -> str | None:
    """Measured against Dagu 2.11.0: `--run-id` is a required flag and the plan is the operand."""
    if not plan:
        return None
    return f"dagu retry --run-id={shlex.quote(run_id)} {shlex.quote(plan)}"


def _parameters(record: dict[str, Any]) -> dict[str, str]:
    """The workflow's declared parameters as the engine recorded them, `KEY=VALUE` a line."""
    raw: Any = record.get("paramsList")
    found: dict[str, str] = {}
    for entry in cast(list[Any], raw) if isinstance(raw, list) else []:
        if isinstance(entry, str) and "=" in entry:
            name, _, value = entry.partition("=")
            found[name] = value
    return found


def _resume_command(session_id: str | None, working_directory: str | None) -> str | None:
    """A receipt a person can paste, spelled by the module that owns provider command lines."""
    if session_id is None or working_directory is None:
        return None
    return resume_command(session_id, working_directory)


def _step_record(
    step_id: str,
    *,
    nodes: dict[str, dict[str, Any]],
    reports: dict[str, dict[str, Any]],
    run_settled: bool,
    orchestrator_gone: bool,
) -> StepRecord:
    """One step, assembled from the nodes it became and the accounts they left."""
    work = nodes.get(f"{WORK_ROLE}_{step_id}")
    mark = nodes.get(f"{MARK_ROLE}_{step_id}")
    has_assertion = f"verify_{step_id}" in nodes

    work_report = reports.get(f"{WORK_ROLE}_{step_id}")
    mark_report = reports.get(f"{MARK_ROLE}_{step_id}")
    commit_report = reports.get(f"{COMMIT_ROLE}_{step_id}")

    outcome, overlays, cause = classify_step(
        work_status=None if work is None else _status(work),
        mark_status=None if mark is None else _status(mark),
        work_report=work_report,
        mark_report=mark_report,
        has_assertion=has_assertion,
        run_settled=run_settled,
        orchestrator_gone=orchestrator_gone,
    )

    work_detail = _detail(work_report)
    commit_detail = _detail(commit_report)
    mark_detail = _detail(mark_report)

    said = work_report.get("summary") if work_report is not None else None
    # A report's `detail` is an agent's own output, so every string out of it is capped here
    # rather than trusted. The session id is the sharp one: it is pasted into the resume
    # command, and an unbounded value would ride into the record and out of every renderer.
    session_id = _reported_text(work_detail.get("session_id"))
    working_directory = (
        engine.text(work_report.get("working_directory")) if work_report is not None else None
    )
    cost = as_money(work_detail.get("total_cost_usd"))
    exit_code = engine.parse_exit_code(None if work is None else work.get("error"))
    diffstat = _diffstat(commit_detail.get("diffstat"))
    freshness = _freshness(work_report) if outcome == OUTCOME_NO_OP else None

    fields: dict[str, object] = {
        "cause": cause,
        "position": _reported_text(mark_detail.get("position")),
        # The command the engine recorded, which for an agent step carries the whole prompt
        # — a plan's task document, at whatever length its author wrote it. It is prose and
        # keeps its shape, but it is bounded like every other untrusted value.
        "asked": None if work is None else _asked(work),
        "said": None if said is None else normalise(said, limit=LINE_LIMIT),
        "freshness": freshness,
        "completed_by_run": _reported_text(work_detail.get("recorded_run")),
        "branch": _reported_text(commit_detail.get("branch")),
        "commit": _reported_text(commit_detail.get("commit")),
        "diffstat": diffstat,
        "cost_usd": cost,
        "turns": as_count(work_detail.get("turn_count")),
        "session_id": session_id,
        "model": _reported_text(work_detail.get("model")),
        "transcript": None if work is None else engine.text(work.get("stdout")),
        "stderr_log": None if work is None else engine.text(work.get("stderr")),
        "resume_command": _resume_command(session_id, working_directory),
        "started_at": None if work is None else engine.moment(work.get("startedAt")),
        "finished_at": None if work is None else engine.moment(work.get("finishedAt")),
        "exit_code": exit_code,
        "divergence": _divergence(mark_report),
    }
    return StepRecord(
        step_id=step_id,
        outcome=outcome,
        overlays=overlays,
        verified=outcome == OUTCOME_VERIFIED,
        cost_is_notional=work_detail.get("cost_is_notional") is True,
        follow_up_work=normalise_all(
            work_report.get("follow_up_work") if work_report is not None else None
        ),
        nodes=sorted(_nodes_of_step(nodes, step_id)),
        provenance=_provenance(
            fields,
            derived=("exit_code", "resume_command"),
        ),
        **cast(Any, fields),
    )


def _status(node: dict[str, Any]) -> int:
    """The node's status as an integer, refusing anything the pinned table does not name."""
    engine.node_status_name(node.get("status"))
    return cast(int, node["status"])


def _diffstat(value: object) -> Diffstat | None:
    if not isinstance(value, dict):
        return None
    entry = cast(dict[str, Any], value)
    counts = [as_count(entry.get(name)) for name in ("files", "insertions", "deletions")]
    if any(count is None for count in counts):
        return None
    return Diffstat(
        files=cast(int, counts[0]),
        insertions=cast(int, counts[1]),
        deletions=cast(int, counts[2]),
    )


def _infrastructure(
    name: str,
    node: dict[str, Any],
    report: dict[str, Any] | None,
    *,
    run_settled: bool,
    orchestrator_gone: bool,
    in_flight: bool = False,
    in_flight_cause: str | None = None,
) -> Infrastructure:
    naming = engine.classify(name)
    if in_flight:
        # The one node a record cannot judge is the node building it. The engine records
        # its lifecycle handler before dispatching it, and a run whose steps are all
        # finished reads any not-started node as one that will never run — so the release
        # writing its own run's record would report itself as never reached, and a green
        # run as failed. It is running, because this is it running.
        fields: dict[str, object] = {
            "role": None if naming is None else naming.role,
            "cause": in_flight_cause,
            "summary": None,
            "started_at": engine.moment(node.get("startedAt")),
            "finished_at": None,
        }
        return Infrastructure(
            name=name,
            # A node that already knows it failed says so; one still doing its work is
            # running. Either way the engine has not recorded this node yet, so its own
            # status cannot be the answer.
            outcome=OUTCOME_RUNNING if in_flight_cause is None else OUTCOME_FAILED,
            provenance=_provenance(fields),
            **cast(Any, fields),
        )
    outcome, _, cause = classify_step(
        work_status=_status(node),
        mark_status=None,
        work_report=report,
        mark_report=None,
        has_assertion=False,
        run_settled=run_settled,
        orchestrator_gone=orchestrator_gone,
    )
    # Housekeeping is not gated, so "the marker step did not record it" is meaningless here:
    # a node the engine says succeeded, succeeded.
    if _status(node) == engine.NODE_STATUS_SUCCEEDED:
        outcome, cause = OUTCOME_VERIFIED, None
    summary = report.get("summary") if report is not None else None
    fields: dict[str, object] = {
        "role": None if naming is None else naming.role,
        "cause": cause if cause is not None else _report_cause(report),
        "summary": None if summary is None else normalise(summary, limit=LINE_LIMIT),
        "started_at": engine.moment(node.get("startedAt")),
        "finished_at": engine.moment(node.get("finishedAt")),
    }
    return Infrastructure(name=name, outcome=outcome, provenance=_provenance(fields), **cast(Any, fields))


def _report_cause(report: dict[str, Any] | None) -> str | None:
    if report is None:
        return None
    found = report.get("cause")
    return found if isinstance(found, str) and found else None


def _census(reports: dict[str, dict[str, Any]]) -> list[WaveCensus]:
    """Each wave's exclusions, read from the join and never re-derived from git.

    A branch in `settled` carries no cause: it landed on an earlier run, or its step had
    nothing to commit. Recording one for it would put an invented cause in the one census
    that cannot be taken again.
    """
    found: list[WaveCensus] = []
    for name, report in sorted(reports.items()):
        if not name.startswith("join_"):
            continue
        detail = _detail(report)
        wave = as_count(detail.get("wave"))
        raw: Any = detail.get("excluded")
        excluded = [
            ExcludedBranch(
                branch=flatten(branch, limit=LINE_LIMIT),
                # The gate froze this vocabulary and the merge quotes it rather than minting
                # one ([verify-gate.md]); a damaged report naming something else is a report
                # Cairn cannot read, and saying so beats passing an eighth cause through.
                cause=_frozen_cause(cast(dict[str, Any], entry).get("cause")),
                summary=flatten(
                    cast(dict[str, Any], entry).get("summary") or "", limit=LINE_LIMIT
                ),
            )
            for branch, entry in sorted(cast(dict[str, Any], raw).items())
            if isinstance(entry, dict)
        ] if isinstance(raw, dict) else []
        fields: dict[str, object] = {"wave": wave, "into": engine.text(detail.get("into"))}
        found.append(
            WaveCensus(
                wave=wave if wave is not None else 0,
                into=str(detail.get("into") or ""),
                arrived=_branches(detail.get("arrived")),
                excluded=excluded,
                settled=_branches(detail.get("settled")),
                provenance=_provenance(fields),
            )
        )
    return sorted(found, key=lambda census: census["wave"])


def _frozen_cause(value: object) -> str:
    """A cause the gate could have recorded, or the word for one it did not."""
    return value if isinstance(value, str) and value in EXCLUSION_CAUSES else GATE_INDETERMINATE


def _branches(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [name for name in cast(list[Any], value) if isinstance(name, str)]


def _nodes_of_step(nodes: dict[str, dict[str, Any]], step_id: str) -> list[str]:
    """Every engine node one step became, found by the name grammar rather than by guess."""
    found: list[str] = []
    for name in nodes:
        naming = engine.classify(name)
        if naming is not None and naming.subject == step_id:
            found.append(name)
    return found


def extract(
    status_record: dict[str, Any] | None,
    reports: dict[str, dict[str, Any]],
    *,
    run_id: str,
    attempt_count: int = 1,
    in_flight_node: str | None = None,
    in_flight_cause: str | None = None,
) -> RunRecord:
    """One run's whole record, from the engine's last snapshot and this run's own reports.

    `in_flight_node` names the one node a record cannot judge, which is the node building
    it: the run's own release writes a record for the run it is still finishing
    ([triggers.md]), and nothing else passes it.
    """
    record = status_record if status_record is not None else {}
    # Two views of the same nodes. `recorded` is the engine's own list, in its own order,
    # and everything the record carries is built from it — a node dropped for being
    # unnameable is a node whose failure nothing can report, which is the whole of what
    # "every node, never silently dropped" is protecting against. `nodes` is a lookup for
    # assembling a step from the five nodes it became, and may collapse a duplicate name
    # without costing the record anything, because the record does not read it.
    recorded = engine.nodes_of(record)
    nodes: dict[str, dict[str, Any]] = {}
    for node in recorded:
        name = engine.node_name(node)
        if name:
            nodes.setdefault(name, node)

    engine_status = record.get("status")
    engine_state = engine.run_status_name(engine_status) if engine_status is not None else ""
    alive = owner_liveness(record) if record else None
    # After a crash the record lies: a killed run stays `running` with no finish time
    # forever, so liveness is decided from the recorded process and its start time and the
    # status field is never the evidence.
    orchestrator_gone = engine_state == engine.RUN_RUNNING and alive is False
    run_settled = engine_state not in (engine.RUN_RUNNING, engine.RUN_QUEUED) or orchestrator_gone

    step_ids = sorted(
        naming.subject
        for name in nodes
        if (naming := engine.classify(name)) is not None and naming.role == WORK_ROLE
    )
    steps = [
        _step_record(
            step_id,
            nodes=nodes,
            reports=reports,
            run_settled=run_settled,
            orchestrator_gone=orchestrator_gone,
        )
        for step_id in step_ids
    ]

    infrastructure = [
        _infrastructure(
            engine.node_name(node),
            node,
            reports.get(engine.node_name(node)),
            run_settled=run_settled,
            orchestrator_gone=orchestrator_gone,
            in_flight=engine.node_name(node) == in_flight_node,
            in_flight_cause=in_flight_cause,
        )
        for node in recorded
        if _is_infrastructure(engine.node_name(node), step_ids)
    ]

    engine_nodes = [
        _engine_node(engine.node_name(node), node, step_ids) for node in recorded
    ]
    edges = _edges(nodes)
    waves = _census(reports)
    budget = _budget(steps)
    verdict = derive_verdict(steps, infrastructure, engine_state, waves)
    attention = derive_attention(steps, infrastructure, budget, waves)

    parameters = _parameters(record)
    lock_detail = _detail(reports.get("lock_acquire"))
    plan = engine.text(lock_detail.get("plan")) or engine.text(record.get("name"))
    fields: dict[str, object] = {
        "plan": plan,
        "graph_sha256": engine.text(lock_detail.get("graph_sha256")),
        "attempt_id": engine.text(record.get("attemptId")),
        "started_at": engine.moment(record.get("startedAt")),
        "finished_at": engine.moment(record.get("finishedAt")),
        "owner_alive": alive,
        # Composed from the engine's own name for the workflow, which is the filename it was
        # started from — never from the plan's slug, because a definition published under a
        # second name is served under that one and nowhere else ([layout.py]).
        "view_url": (
            view_url(name, run_id) if (name := engine.text(record.get("name"))) else None
        ),
    }
    return RunRecord(
        record_version=RECORD_VERSION,
        run_id=run_id,
        attempts=attempt_count,
        engine_version=ENGINE_VERSION,
        engine_run_status=engine_status if isinstance(engine_status, int) else -1,
        engine_run_status_name=engine_state,
        # The engine calls this run clean and Cairn does not. It is a fact about two
        # readings rather than a judgement, and it is I5's whole point made checkable.
        engine_contradicted=(
            engine_state in (engine.RUN_SUCCEEDED, engine.RUN_PARTIALLY_SUCCEEDED)
            and verdict not in (VERDICT_GREEN, VERDICT_ALL_NO_OP)
        ),
        verdict=verdict,
        exit_code=VERDICT_EXIT_CODES[verdict],
        trigger=_trigger(record),
        lineage=_lineage(parameters, steps, reports),
        steps=steps,
        infrastructure=infrastructure,
        nodes=engine_nodes,
        edges=edges,
        waves=waves,
        attention=attention,
        budget=budget,
        git=_git(parameters, reports, waves),
        next_action=derive_next_action(
            verdict, steps, engine_state, run_id, plan, waves, orchestrator_gone
        ),
        provenance=_provenance(fields, derived=("owner_alive", "view_url")),
        **cast(Any, fields),
    )


def _is_infrastructure(name: str, step_ids: list[str]) -> bool:
    """Whether a node is Cairn's own housekeeping rather than a step of the plan.

    Membership is the name's own answer: a node whose parsed subject is no step's id is
    infrastructure, which is what puts a wave's join, its merge slots and the proof of each
    on the right side of the line without a second list to keep in step.
    """
    naming = engine.classify(name)
    return naming is None or naming.subject not in step_ids


def _engine_node(name: str, node: dict[str, Any], step_ids: list[str]) -> EngineNode:
    naming = engine.classify(name)
    status = _status(node)
    fields: dict[str, object] = {
        "role": None if naming is None else naming.role,
        "subject": None if naming is None else naming.subject,
        "step_id": (
            naming.subject if naming is not None and naming.subject in step_ids else None
        ),
        "started_at": engine.moment(node.get("startedAt")),
        "finished_at": engine.moment(node.get("finishedAt")),
        "working_directory": engine.text(node.get("workingDir")),
        "stdout": engine.text(node.get("stdout")),
        "stderr": engine.text(node.get("stderr")),
        "error": engine.text(node.get("error")),
        "exit_code": engine.parse_exit_code(node.get("error")),
    }
    return EngineNode(
        name=name,
        status=status,
        status_name=engine.node_status_name(status),
        depends=engine.node_depends(node),
        provenance=_provenance(fields, derived=("exit_code",)),
        **cast(Any, fields),
    )


def _edges(nodes: dict[str, dict[str, Any]]) -> list[Edge]:
    found: list[Edge] = []
    for name, node in sorted(nodes.items()):
        downstream = engine.classify(name)
        for upstream_name in engine.node_depends(node):
            found.append(
                Edge(
                    upstream=upstream_name,
                    downstream=name,
                    kind=_edge_kind(engine.classify(upstream_name), downstream),
                )
            )
    return found


def _budget(steps: list[StepRecord]) -> Budget:
    priced = [step for step in steps if step["cost_usd"] is not None]
    turns = [step["turns"] for step in steps if step["turns"] is not None]
    total = sum(cast(float, step["cost_usd"]) for step in priced) if priced else None
    fields: dict[str, object] = {
        "cost_usd": total,
        "turns": sum(turns) if turns else None,
    }
    return Budget(
        cost_usd=total,
        notional=any(step["cost_is_notional"] for step in priced),
        turns=sum(turns) if turns else None,
        priced_steps=len(priced),
        unpriced_steps=len(steps) - len(priced),
        provenance=_provenance(fields, derived=("cost_usd", "turns")),
    )


def _trigger(record: dict[str, Any]) -> Trigger:
    raw = record.get("triggerType")
    kind = engine.trigger_name(raw) if raw is not None else engine.TRIGGER_UNKNOWN
    actor = engine.text(record.get("triggerActor"))
    fields: dict[str, object] = {"actor": actor}
    return Trigger(
        kind=kind,
        actor=actor,
        # An absent actor means Cairn started it. The engine names the authenticated user
        # only for a run started through its own view, so a run a person began and a run the
        # skill began are the same record but for this one field.
        started_by_cairn=actor is None,
        provenance=_provenance(fields),
    )


def _lineage(
    parameters: dict[str, str],
    steps: list[StepRecord],
    reports: dict[str, dict[str, Any]],
) -> Lineage:
    completed: dict[str, str] = {
        step["step_id"]: run
        for step in steps
        if (run := step["completed_by_run"]) is not None
    }
    # The declared parameter is the caller's override and is empty whenever the run minted
    # its own occasion at its first act, so the lock's report is the authority and the
    # parameter is only read for a run that was given one ([marker.py]).
    occasion = parameters.get(OCCASION_PARAM) or _lock_occasion(reports)
    fields: dict[str, object] = {"occasion": occasion}
    return Lineage(
        occasion=occasion,
        previous_runs=sorted(set(completed.values())),
        completed_by=completed,
        provenance=_provenance(fields),
    )


def _lock_occasion(reports: dict[str, dict[str, Any]]) -> str | None:
    """The occasion the run's first act recorded, which is where a minted one lives."""
    report = reports.get(node_name("lock", "acquire"))
    detail = _detail(report) if report is not None else {}
    found = detail.get("occasion")
    return found if isinstance(found, str) and found else None


def _from_reports(
    reports: dict[str, dict[str, Any]], prefix: str, field: str
) -> list[str]:
    """One field, gathered from every report of one role that actually recorded it."""
    found: list[str] = []
    for name, report in reports.items():
        if not name.startswith(prefix):
            continue
        value = engine.text(_detail(report).get(field))
        if value is not None:
            found.append(value)
    return found


def _git(
    parameters: dict[str, str],
    reports: dict[str, dict[str, Any]],
    waves: list[WaveCensus],
) -> GitFacts:
    commits = sorted(_from_reports(reports, "commit_", "commit"))
    landed = sorted(_from_reports(reports, "merge_", "landed"))
    repository = parameters.get(REPOSITORY_PARAM) or None
    parent = next((census["into"] for census in waves if census["into"]), None)
    fields: dict[str, object] = {"repository": repository, "parent_branch": parent}
    return GitFacts(
        repository=repository,
        parent_branch=parent,
        commits=commits,
        landed=landed,
        excluded=sorted(
            entry["branch"] for census in waves for entry in census["excluded"]
        ),
        provenance=_provenance(fields),
    )


__all__ = [
    "census_exclusions",
    "classify_step",
    "derive_attention",
    "derive_next_action",
    "derive_verdict",
    "extract",
    "read_reports",
]
