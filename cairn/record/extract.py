"""Building one run's record: Cairn's own reports first, the engine's state as a supplement.

Every step goes through Cairn's CLI, so the reports are uniform across kinds and are the
richer source — they are the only place session identity, turns and an agent's own
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
import os
import shlex
from collections.abc import Container, Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple, cast

from cairn.assertions import ASSERTION_SOURCES, BACKED_BY_KEY, EXIT_KEY, SOURCE_KEY
from cairn.core import ReportFault, validate_step_report
from cairn.layout import view_url
from cairn.providers import resume_command
from cairn.record import engine
from cairn.record.model import (
    AllowanceHold,
    AllowanceWindow,
    Attention,
    Diffstat,
    Divergence,
    Edge,
    EngineNode,
    ExcludedBranch,
    Freshness,
    GitFacts,
    Headroom,
    Infrastructure,
    Integrity,
    Lineage,
    NextAction,
    Remedy,
    RunRecord,
    StepRecord,
    Trigger,
    WaveCensus,
)
from cairn.record.vocabulary import (
    ATTENTION_BLOCKED,
    ATTENTION_DIVERGENCE,
    ATTENTION_EXCLUDED,
    ATTENTION_FAILURE,
    ATTENTION_FOLLOW_UP,
    ATTENTION_HOUSEKEEPING_FAILURE,
    ATTENTION_INTEGRITY,
    ATTRIBUTION_CAIRN,
    ATTRIBUTION_PARENT_RUN,
    ATTRIBUTION_RETRY_SCANNER,
    ATTRIBUTION_SCHEDULER,
    ATTRIBUTION_UNKNOWN,
    ATTRIBUTION_USER,
    ATTRIBUTION_WEBHOOK,
    EDGE_DEPENDENCY,
    EDGE_RUN,
    EDGE_STEP,
    EDGE_WAVE,
    INTEGRITY_DUPLICATE_NODE,
    INTEGRITY_ENGINE_RUN_STATUS,
    INTEGRITY_GATE_CONTRADICTS_WORK,
    INTEGRITY_REPORT_CONTRADICTS_ENGINE,
    INTEGRITY_REPORT_UNREADABLE,
    NEXT_AWAIT_ALLOWANCE,
    NEXT_DECIDE,
    NEXT_FIX_ASSERTION,
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
from cairn.skill.vocabulary import TRIGGER_RECOVERY
from cairn.supervise import owner_liveness
from cairn.text import (
    LINE_LIMIT,
    LIST_LIMIT,
    TEXT_LIMIT,
    as_count,
    flatten,
    normalise,
    normalise_all,
)
from cairn.topology import (
    RUN_ROLES,
    WAVE_ROLES,
    TopologyError,
    dependency_levels,
    node_name,
    step_of_branch,
)
from cairn.verify import (
    ASSERTION_INTERRUPTED,
    EXCLUSION_CAUSES,
    GATE_INDETERMINATE,
    NOT_REACHED,
    ORCHESTRATOR_DIED,
    PROVIDER_UNREACHABLE,
    QUOTA_HELD,
    REPORTED_KILLED,
    TIMED_OUT,
    USER_DECISION_REQUIRED,
)
from cairn.verify import (
    divergence_line as _divergence_line,
)
from cairn.workflow.schema import (
    CAIRN_INVOCATION,
    ENGINE_VERSION,
    OCCASION_PARAM,
    REPOSITORY_PARAM,
)

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


class ReportSet(NamedTuple):
    """Every account this run's steps left, and every document that was not one.

    The two travel together because a reader that took the accounts alone would be back to
    discarding damaged evidence silently, which is the one reading this module may not
    offer: a report refused is a fact the record does not have, and the refusal is the only
    account of it there is.
    """

    reports: dict[str, dict[str, Any]]
    integrity: list[Integrity]


def _refused(subject: str, fault: ReportFault) -> Integrity:
    """One refusal, bounded before it enters the record.

    The text names a filename and a damaged file's own bytes, both of which are as long as
    whatever wrote them chose, so it is capped like every other untrusted value.
    """
    return Integrity(
        subject=subject, fault=fault.fault, detail=flatten(fault.detail, limit=LINE_LIMIT)
    )


def read_reports(directory: Path, run_id: str) -> ReportSet:
    """Every account this run's steps left, by the engine node name it was found under.

    Each document is held to the same validation the runtime gates hold it to
    ([core.validate_step_report]), and one that fails contributes nothing at all — no
    summary, no session, no freshness, no outcome. It is not discarded either: the step it
    was found under reads as having left no account, and the refusal is kept beside the
    accounts so the record can say which of the two happened. The extraction of a whole run
    never dies on one damaged file.
    """
    if not directory.is_dir():
        return ReportSet({}, [])
    found: dict[str, dict[str, Any]] = {}
    refused: list[Integrity] = []
    for path in sorted(directory.glob("*.json")):
        try:
            raw: Any = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            refused.append(
                _refused(
                    path.stem,
                    ReportFault(INTEGRITY_REPORT_UNREADABLE, f"{path.name}: {exc}"),
                )
            )
            continue
        fault = validate_step_report(raw, node_name=path.stem, run_id=run_id)
        if fault is not None:
            refused.append(
                _refused(
                    path.stem,
                    ReportFault(fault.fault, f"{path.name}: {fault.detail}"),
                )
            )
            continue
        found[path.stem] = cast(dict[str, Any], raw)
    return ReportSet(found, refused[:LIST_LIMIT])


def read_holds(directory: Path, run_id: str) -> dict[str, dict[str, Any]]:
    """Every hold this run's steps are announcing now, by the node that is holding.

    An announcement is written while a step waits at the allowance and taken back when it
    stops waiting, so one is read only for a step the engine still calls running; a step
    killed mid-hold leaves one behind that its own outcome already outranks.
    """
    if not directory.is_dir():
        return {}
    found: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.glob("*.json")):
        try:
            raw: Any = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(raw, dict) and cast(dict[str, Any], raw).get("run_id") == run_id:
            found[path.stem] = cast(dict[str, Any], raw)
    return found


def _fraction(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if 0.0 <= number <= 1.0 else None


def _allowance_window(name: str, raw: object) -> AllowanceWindow | None:
    if not isinstance(raw, dict):
        return None
    entry = cast(dict[str, Any], raw)
    return AllowanceWindow(
        window=flatten(name, limit=LINE_LIMIT),
        used=_fraction(entry.get("used")),
        status=_reported_text(entry.get("status")),
        resets_at=_reported_text(entry.get("resets_at")),
        source=_reported_text(entry.get("source")),
        read_at=_reported_text(entry.get("read_at")),
    )


def _allowance_hold(raw: object) -> AllowanceHold | None:
    if not isinstance(raw, dict):
        return None
    entry = cast(dict[str, Any], raw)
    return AllowanceHold(
        window=_reported_text(entry.get("window")),
        started=_reported_text(entry.get("started")),
        until=_reported_text(entry.get("until")),
        why=_reported_text(entry.get("why")),
        after=_reported_text(entry.get("after")),
    )


def _headroom(work_report: dict[str, Any] | None, holding: object) -> Headroom | None:
    """An agent step's dealings with the allowance, from its report and its announcement."""
    found: Any = _detail(work_report).get("headroom")
    account = cast(dict[str, Any], found) if isinstance(found, dict) else None
    now = _allowance_hold(holding)
    if account is None and now is None:
        return None
    account = account or {}
    reading: Any = account.get("reading")
    holds: Any = account.get("holds")
    resumes: Any = account.get("resumes")
    return Headroom(
        admission=_reported_text(account.get("admission")),
        reason=_reported_text(account.get("reason")),
        reading=[
            window
            for name, entry in sorted(cast(dict[str, Any], reading).items())
            if (window := _allowance_window(name, entry)) is not None
        ]
        if isinstance(reading, dict)
        else [],
        holds=[
            hold
            for entry in cast(list[Any], holds)
            if (hold := _allowance_hold(entry)) is not None
        ]
        if isinstance(holds, list)
        else [],
        resumes=len(cast(list[Any], resumes)) if isinstance(resumes, list) else 0,
        held_window=_reported_text(account.get("held_window")),
        held_until=_reported_text(account.get("held_until")),
        holding=now,
    )


def _holding(
    step_id: str,
    outcome: str,
    nodes: dict[str, dict[str, Any]],
    holds: dict[str, dict[str, Any]],
    orchestrator_gone: bool,
) -> dict[str, Any] | None:
    """The hold a step's own running node is announcing, if one is.

    A remedy runs after the work node has ended, so the step's outcome no longer reads
    running while its remedy holds; the remedy's own node is what says it still is.
    """
    if outcome == OUTCOME_RUNNING:
        return holds.get(f"{WORK_ROLE}_{step_id}")
    remedy = nodes.get(f"remedy_{step_id}")
    if (
        remedy is not None
        and not orchestrator_gone
        and _status(remedy) == engine.NODE_STATUS_RUNNING
    ):
        return holds.get(f"remedy_{step_id}")
    return None


def _latest_allowance(steps: list[StepRecord]) -> list[AllowanceWindow]:
    """Each window's most recent measurement across every step's admission."""
    latest: dict[str, AllowanceWindow] = {}
    for step in steps:
        headroom = step["headroom"]
        for window in [] if headroom is None else headroom["reading"]:
            held = latest.get(window["window"])
            if held is None or (window["read_at"] or "") > (held["read_at"] or ""):
                latest[window["window"]] = window
    return [latest[name] for name in sorted(latest)]


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
    saying so is honest where passing the string through would mint a cause outside the frozen set.
    """
    if report is None:
        return None
    found = report.get("cause")
    if found is None:
        return None
    return found if isinstance(found, str) and found in EXCLUSION_CAUSES else GATE_INDETERMINATE


# Which engine node statuses each report status can stand beside. A report is written
# either by the node it names or by the gate that is that node's own precondition, so a
# `skipped` node with a gate's `failed` or `noop` report beside it is the ordinary shape of
# a gate that closed. A node the engine never started, and one it aborted because an
# upstream node failed, evaluated no precondition and ran nothing — so any report of this
# run under such a name is evidence that disagrees with itself.
#
# `noop` is the load-bearing row, because it is the only report status that can raise a
# step's outcome: it is the marker gate's word that the work was already done, and it is
# that only where the engine says the gate skipped the work. Beside a node that ran, failed
# or completed, it is a claim the work never started over an engine record saying it did.
COMPATIBLE_NODE_STATUS: dict[str, frozenset[int]] = {
    "done": frozenset(
        {
            engine.NODE_STATUS_RUNNING,
            engine.NODE_STATUS_FAILED,
            engine.NODE_STATUS_SUCCEEDED,
        }
    ),
    "failed": frozenset(
        {
            engine.NODE_STATUS_RUNNING,
            engine.NODE_STATUS_FAILED,
            engine.NODE_STATUS_SUCCEEDED,
            engine.NODE_STATUS_SKIPPED,
        }
    ),
    "noop": frozenset({engine.NODE_STATUS_SKIPPED}),
}


class Classification(NamedTuple):
    """What one node's evidence came to, and what of it could not be read at all.

    `integrity` carries the contradictions found while classifying, without a subject: the
    caller knows which node it asked about, and attaching the name here would let one
    reading of one node be recorded under two names.
    """

    outcome: str
    overlays: list[str]
    cause: str | None
    integrity: tuple[ReportFault, ...]


def _contradictions(
    work_report: dict[str, Any] | None, work_status: int | None, mark_status: int | None
) -> tuple[ReportFault, ...]:
    """Where this node's own evidence disagrees with itself, in the engine's words and its own."""
    found: list[ReportFault] = []
    reported = None if work_report is None else work_report.get("status")
    if isinstance(reported, str) and reported in COMPATIBLE_NODE_STATUS:
        status = engine.NODE_STATUS.get(work_status) if work_status is not None else None
        if work_status not in COMPATIBLE_NODE_STATUS[reported]:
            found.append(
                ReportFault(
                    INTEGRITY_REPORT_CONTRADICTS_ENGINE,
                    f"the report says {reported!r} and the engine says the node "
                    f"{status or 'has no readable status'}",
                )
            )
    if (
        mark_status == engine.NODE_STATUS_SUCCEEDED
        and work_status is not None
        and work_status != engine.NODE_STATUS_SUCCEEDED
    ):
        found.append(
            ReportFault(
                INTEGRITY_GATE_CONTRADICTS_WORK,
                "the marker gate recorded the work and the engine says the work node "
                f"{engine.NODE_STATUS.get(work_status) or 'has no readable status'}",
            )
        )
    return tuple(found)


def classify_step(
    *,
    work_status: int | None,
    mark_status: int | None,
    work_report: dict[str, Any] | None,
    mark_report: dict[str, Any] | None,
    has_assertion: bool,
    run_settled: bool,
    orchestrator_gone: bool,
    engine_killed: bool = False,
) -> Classification:
    """One step's outcome, its overlays and its cause, from the engine and the reports together.

    Read top to bottom; the first case that matches decides. The engine says whether the
    step ran, and Cairn's own reports say what came of it — neither can raise what the other
    lowered, which is the same rule the verify gate itself is built on. Where the two
    contradict each other, neither raises: the step keeps the outcome its engine node
    supports and the contradiction is recorded as it stands ([28 B]).

    `engine_killed` is the one fact the gate cannot see: the engine stopped the step at its
    bound, spelled only in the node's own error. It decides exactly one cell — a failed work
    node that left no report — and there it outranks the gate's `not_reached`, because the
    gate read an absent report as a step that never ran and the engine says it ran until it
    was killed ([22 A]). It never outranks a cause the gate established on evidence of its
    own, and it never touches a step that did report.
    """
    integrity = _contradictions(work_report, work_status, mark_status)
    overlays: list[str] = []
    if not has_assertion:
        overlays.append(OVERLAY_UNVERIFIED)
    reported: str | None = None
    if work_report is not None:
        status = work_report.get("status")
        reported = status if isinstance(status, str) else None

    # The marker gate skipped the step and left the one report that says so — the one that
    # names the marker it matched. It outranks the node statuses below because a no-op is
    # the only outcome the engine spells `skipped` and Cairn can prove was correct, and it
    # requires that `skipped`: a session that ran and said `noop` of itself has not been
    # skipped by anything, and neither has one whose node failed, so the gate judges it
    # below instead.
    if (
        reported == "noop"
        and work_status == engine.NODE_STATUS_SKIPPED
        and _freshness(work_report) is not None
    ):
        return Classification(OUTCOME_NO_OP, overlays, None, integrity)

    if work_status == engine.NODE_STATUS_ABORTED:
        return Classification(OUTCOME_NOT_REACHED, overlays, None, integrity)
    if work_status is None or work_status == engine.NODE_STATUS_NOT_STARTED:
        return Classification(
            OUTCOME_NOT_REACHED if run_settled else OUTCOME_PENDING,
            overlays,
            None,
            integrity,
        )
    if work_status == engine.NODE_STATUS_RUNNING:
        if orchestrator_gone:
            return Classification(OUTCOME_FAILED, overlays, ORCHESTRATOR_DIED, integrity)
        return Classification(OUTCOME_RUNNING, overlays, None, integrity)
    if work_status == engine.NODE_STATUS_SKIPPED:
        if _cause(mark_report) == NOT_REACHED:
            # The engine spells a chain halt and a marker no-op both `skipped`; the gate
            # tells them apart. A no-op leaves its report and was caught above; a step
            # behind a halt leaves none, so its gate closed `not_reached` — and that is a
            # step that never ran and never will, not one excluded on its own account
            # ([23 B]). Reading it as excluded made a halted chain a near-clean success.
            return Classification(OUTCOME_NOT_REACHED, overlays, NOT_REACHED, integrity)
        # Skipped with no readable no-op report and no gate account of a halt: the step's
        # fate is unestablished rather than fresh.
        return Classification(OUTCOME_EXCLUDED, overlays, GATE_INDETERMINATE, integrity)

    # The step ran. What came of it is the gate's to say.
    if _divergence(mark_report) is not None:
        overlays.append(OVERLAY_DIVERGENCE)
    if work_report is not None and work_report.get("needs_user_decision") is True:
        overlays.append(OVERLAY_BLOCKED)
        return Classification(
            OUTCOME_EXCLUDED, _ordered(overlays), USER_DECISION_REQUIRED, integrity
        )

    cause = _cause(mark_report)
    if cause == USER_DECISION_REQUIRED:
        overlays.append(OVERLAY_BLOCKED)
        return Classification(OUTCOME_EXCLUDED, _ordered(overlays), cause, integrity)
    if (
        engine_killed
        and work_report is None
        and work_status == engine.NODE_STATUS_FAILED
        and cause in (None, NOT_REACHED, GATE_INDETERMINATE)
    ):
        return Classification(OUTCOME_FAILED, _ordered(overlays), TIMED_OUT, integrity)
    # Verified is the marker gate's word and the engine's together. The gate records a step
    # whose work node succeeded; over one the engine says failed, the gate's word is the
    # half of a contradiction that would raise an outcome, so it does not.
    if (
        mark_status == engine.NODE_STATUS_SUCCEEDED
        and work_status == engine.NODE_STATUS_SUCCEEDED
    ):
        return Classification(OUTCOME_VERIFIED, _ordered(overlays), None, integrity)
    if cause is not None:
        return Classification(OUTCOME_EXCLUDED, _ordered(overlays), cause, integrity)
    if work_status == engine.NODE_STATUS_FAILED:
        return Classification(OUTCOME_FAILED, _ordered(overlays), None, integrity)
    # The work node succeeded and nothing recorded it as verified — no marker step, or one
    # that ran and said nothing. Either way the step's own end state was never asserted, and
    # a record that called that verified would be the marker-over-unverified-work failure the
    # gate itself fails closed to prevent.
    return Classification(
        OUTCOME_EXCLUDED, _ordered(overlays), GATE_INDETERMINATE, integrity
    )


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
    *,
    engine_status_readable: bool,
) -> str:
    """The run's own verdict, from every node it has and never from the engine's word for the run.

    A queued run reads as running rather than as an outcome: anything triggered externally
    arrives that way and may sit there indefinitely if no scheduler is up, and every one of
    its nodes is at not-started.

    `engine_status_readable` is false where the engine's own status for this run is missing
    or outside the pinned table. The status is read as a verdict nowhere, but it is what
    says whether the run is still going — so a reading without it cannot tell a finished
    run from one mid-step, and calling such a run clean would be the stronger outcome
    damaged evidence may never produce.
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
    if not engine_status_readable:
        return VERDICT_FAILED
    if OUTCOME_EXCLUDED in outcomes or census_exclusions(waves):
        return VERDICT_GREEN_WITH_EXCLUSIONS
    if outcomes == {OUTCOME_NO_OP}:
        return VERDICT_ALL_NO_OP
    return VERDICT_GREEN


def step_order(nodes: dict[str, dict[str, Any]], step_ids: list[str]) -> list[str]:
    """The run's steps in dependency order, read off the edges the engine enforced.

    The one order both "what halted this run" and "what to do next" are answered in
    ([23 B]): a chain reads front to back, and a fan-out reads wave by wave with a wave's
    steps in id order. The levelling is the derivation's own function, but the graph is
    not the derivation's: this levels the engine's nodes and collapses each level back to
    the steps they belong to, so the steps of one level are sorted here rather than
    inherited — node order would put a step named late behind one of its own roles.
    Total and never raising: a record building over a hand-edited or truncated state file
    falls back to id order rather than costing the person the record.
    """
    pending = {
        name: {upstream for upstream in engine.node_depends(node) if upstream in nodes}
        for name, node in nodes.items()
    }
    try:
        levels = dependency_levels(pending)
    except TopologyError:
        return sorted(step_ids)
    wanted = set(step_ids)
    ordered: list[str] = []
    for level in levels:
        subjects = {
            naming.subject
            for naming in (engine.classify(name) for name in level)
            if naming is not None and naming.subject in wanted
        }
        ordered.extend(sorted(subjects - set(ordered)))
    return ordered + sorted(wanted - set(ordered))


def _halts(steps: list[StepRecord], order: list[str]) -> list[str]:
    """Every step, in dependency order, whose gate closed for a cause of its own.

    A step behind a halt, and one whose fate nothing established, both carry a cause that
    is not theirs; neither is where the fault is, and naming one as the subject of "what
    to do next" sends a person to a bystander ([23 B]).
    """
    by_id = {step["step_id"]: step for step in steps}
    return [
        step_id
        for step_id in order
        if by_id[step_id]["outcome"] in (OUTCOME_FAILED, OUTCOME_EXCLUDED)
        and by_id[step_id]["cause"] not in (NOT_REACHED, GATE_INDETERMINATE)
    ]


def _halted_by(steps: list[StepRecord], order: list[str]) -> str | None:
    """The first step the run halted at, or None where nothing halted it."""
    halts = _halts(steps, order)
    return halts[0] if halts else None


def _never_reached_line(unreached: list[str], fault: str | None) -> str:
    """One line for every step a halt left behind, naming the halt where the record knows it.

    `fault` is passed only where the run halted in one place. Two independent halts leave
    two sets of steps behind, and one line naming the first of them would tell a person the
    other branch stopped for a reason it did not.
    """
    behind = "" if fault is None else f" behind {fault}"
    if len(unreached) == 1:
        return f"never reached{behind}"
    return flatten(
        f"never reached{behind}, with {len(unreached) - 1} more: {', '.join(unreached[1:])}",
        limit=LINE_LIMIT,
    )


def derive_attention(
    steps: list[StepRecord],
    infrastructure: list[Infrastructure],
    waves: list[WaveCensus],
    order: list[str],
    integrity: Sequence[Integrity],
) -> list[Attention]:
    """Everything a reader has to act on, assembled in the frozen order rather than sorted into it.

    Failures come in dependency order, and the steps a halt left behind come as **one**
    item rather than one each: fourteen identical "never ran" lines outnumber the one line
    that names the fault, and none of the fourteen is a thing a person can act on. Every
    such step keeps its own outcome in `steps`; the collapse is what the attention list is
    for, which is action ([23 B]).

    An integrity refusal is an item of its own, above every failure, because the lines
    below it were read off the same evidence: a reader who does not know a report was
    refused cannot know which of the facts beneath it are missing rather than false.
    """
    items: list[Attention] = []
    by_id = {step["step_id"]: step for step in steps}
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
    for refusal in integrity:
        items.append(
            Attention(
                kind=ATTENTION_INTEGRITY,
                subject=refusal["subject"],
                summary=refusal["detail"],
                cause=refusal["fault"],
            )
        )
    for step_id in order:
        step = by_id[step_id]
        if step["outcome"] == OUTCOME_FAILED:
            items.append(
                Attention(
                    kind=ATTENTION_FAILURE,
                    subject=step["step_id"],
                    summary=_failure_summary(step),
                    cause=step["cause"],
                )
            )
    unreached = [step_id for step_id in order if by_id[step_id]["outcome"] == OUTCOME_NOT_REACHED]
    if unreached:
        halts = _halts(steps, order)
        items.append(
            Attention(
                kind=ATTENTION_FAILURE,
                subject=unreached[0],
                summary=_never_reached_line(unreached, halts[0] if len(halts) == 1 else None),
                cause=NOT_REACHED,
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
        (step["step_id"], step["cause"])
        for step in steps
        if step["outcome"] in (OUTCOME_EXCLUDED, OUTCOME_FAILED, OUTCOME_NOT_REACHED)
    }
    for entry in census_exclusions(waves):
        if (step_of_branch(entry["branch"]), entry["cause"]) in spoken_for:
            continue
        items.append(
            Attention(
                kind=ATTENTION_EXCLUDED,
                subject=entry["branch"],
                summary=entry["summary"] or "the branch carried no work the gate would land",
                cause=entry["cause"],
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
                    summary=_divergence_line(step["divergence"]),
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


def _failure_summary(step: StepRecord) -> str:
    """What a failed step's attention line says, in the step's own words where it has any.

    A step a bound stopped has none, so the line carries the two numbers a person weighs
    before deciding whether to run it again: the bound that fired and how long it had run.
    """
    if step["said"]:
        return step["said"]
    if step["cause"] == TIMED_OUT and step["timeout_seconds"] is not None:
        elapsed = step["elapsed_seconds"]
        after = "" if elapsed is None else f" after {elapsed:.1f} s"
        return f"the engine stopped the step at its {step['timeout_seconds']} s bound{after}"
    return f"the step is {step['outcome']}"


def derive_next_action(
    verdict: str,
    steps: list[StepRecord],
    *,
    engine_state: str,
    run_id: str,
    plan: str | None,
    repository: str | None,
    waves: list[WaveCensus],
    infrastructure: list[Infrastructure],
    order: list[str],
) -> NextAction:
    """What a reader does now, in one value plus the command that does it.

    The subject is the step the fault is at — the first in dependency order whose gate
    closed for a cause of its own — and never a step behind it ([23 B]). A command is
    carried only where one can be spelled correctly and completely: the recovery command
    needs the plan and the repository as well as the run, and one missing any of them
    fails when it is pasted, which is worse than the report saying plainly it has none.
    """
    by_id = {step["step_id"]: step for step in steps}
    if verdict == VERDICT_BLOCKED:
        blocked = next(
            (step_id for step_id in order if OVERLAY_BLOCKED in by_id[step_id]["overlays"]),
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
            command="python3 -m cairn schedule start",
        )
    if verdict == VERDICT_RUNNING:
        return NextAction(action=NEXT_WAIT, subject=None, command=None)
    if verdict == VERDICT_FAILED:
        subject = _halted_by(steps, order) or next(
            (
                step_id
                for step_id in order
                if by_id[step_id]["outcome"]
                in (OUTCOME_FAILED, OUTCOME_EXCLUDED, OUTCOME_NOT_REACHED)
            ),
            None,
        )
        return NextAction(
            action=_rerun_or_fix(by_id.get(subject) if subject else None),
            subject=subject,
            command=_recovery_command(run_id, plan, repository),
        )
    if verdict == VERDICT_GREEN_WITH_EXCLUSIONS:
        excluded = next(
            (step_id for step_id in order if by_id[step_id]["outcome"] == OUTCOME_EXCLUDED),
            None,
        ) or next((entry["branch"] for entry in census_exclusions(waves)), None)
        # A merge is settled only where the topology has one. A wave's census is taken by
        # a join, and a join stands only in an isolated wave, which always lands through
        # merge slots — so a census is evidence of a merge even where the engine's own
        # node list has been cut short. A chain has neither, and re-running is its whole
        # remedy ([23 B]).
        has_merge = bool(waves) or any(item["role"] == "merge" for item in infrastructure)
        subject = by_id.get(excluded) if excluded is not None else None
        held = subject is not None and subject["cause"] in (QUOTA_HELD, PROVIDER_UNREACHABLE)
        if has_merge and not held:
            return NextAction(action=NEXT_SETTLE_MERGE, subject=excluded, command=None)
        return NextAction(
            action=_rerun_or_fix(by_id.get(excluded) if excluded else None),
            subject=excluded,
            command=_recovery_command(run_id, plan, repository),
        )
    return NextAction(action=NEXT_NOTHING, subject=None, command=None)


def _rerun_or_fix(subject: StepRecord | None) -> str:
    """A re-run is the remedy unless the step's assertion never got to decide anything, or
    the step is waiting on the subscription rather than on anything a person can change."""
    if subject is not None and subject["cause"] == ASSERTION_INTERRUPTED:
        return NEXT_FIX_ASSERTION
    if subject is not None and subject["cause"] == QUOTA_HELD:
        return NEXT_AWAIT_ALLOWANCE
    return NEXT_RERUN


def _recovery_command(run_id: str, plan: str | None, repository: str | None) -> str | None:
    """The skill's own command for a run that continues this one ([skill/cli.py]).

    Never `dagu retry`: re-running a plan is the whole recovery story, and a continued
    occasion is what keeps it short, so the record hands a person this command rather than
    the engine's verb — which also refuses a run the engine still believes is going.
    """
    if not plan or not repository:
        return None
    return shlex.join(
        [
            *CAIRN_INVOCATION,
            "run",
            "start",
            "--plan",
            plan,
            "--repository",
            repository,
            "--trigger",
            TRIGGER_RECOVERY,
            "--recovering",
            run_id,
        ]
    )


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
    holds: dict[str, dict[str, Any]],
    run_settled: bool,
    orchestrator_gone: bool,
) -> tuple[StepRecord, list[Integrity]]:
    """One step, assembled from the nodes it became and the accounts they left.

    The refusals come back beside it rather than inside it: a contradiction between a
    step's report and its node is a fact about this run's evidence, and the record accounts
    for all of them in one place a reader can find.
    """
    work = nodes.get(f"{WORK_ROLE}_{step_id}")
    mark = nodes.get(f"{MARK_ROLE}_{step_id}")
    assertion = nodes.get(f"verify_{step_id}")
    has_assertion = assertion is not None

    work_report = reports.get(f"{WORK_ROLE}_{step_id}")
    mark_report = reports.get(f"{MARK_ROLE}_{step_id}")
    commit_report = reports.get(f"{COMMIT_ROLE}_{step_id}")
    assertion_account = _detail(reports.get(f"verify_{step_id}"))
    assertion_tail_node = assertion
    remedy_report = reports.get(f"remedy_{step_id}")
    recheck_account = _detail(reports.get(f"recheck_{step_id}"))
    if remedy_report is not None and remedy_report.get("status") == "done" and (
        recheck_account.get(SOURCE_KEY) is not None
    ):
        # The step's verdict is the assertion run after the remedy, so that is the one the
        # record names as the step's; the first one survives inside `remedy`. Its tail
        # follows the same swap, or a step the remedy fixed would quote the pre-remedy
        # failure forever, under a verdict that now reads as verified.
        assertion_account = recheck_account
        assertion_tail_node = nodes.get(f"recheck_{step_id}")

    killed = None if work is None else engine.parse_timeout(work.get("error"))
    outcome, overlays, cause, refusals = classify_step(
        work_status=None if work is None else _status(work),
        mark_status=None if mark is None else _status(mark),
        work_report=work_report,
        mark_report=mark_report,
        has_assertion=has_assertion,
        run_settled=run_settled,
        orchestrator_gone=orchestrator_gone,
        engine_killed=killed is not None,
    )
    divergence = _divergence(mark_report)
    assertion_tail = (
        _assertion_tail(assertion_tail_node)
        if assertion_tail_node is not None
        and _status(assertion_tail_node) == engine.NODE_STATUS_FAILED
        else None
    )
    divergence_is_derived = False
    if cause == TIMED_OUT and divergence is None:
        # The gate saw no report and recorded no divergence; the engine node for the
        # assertion says whether it passed over what the killed step left, and that is the
        # one account the record can still weigh against a session nobody heard from.
        # Reached only where the assertion ran at all, which behind a step that left no
        # report means its own gate faulted open ([24 B]).
        divergence = _asserted_over_a_killed_step(assertion)
        if divergence is not None:
            divergence_is_derived = True
            overlays = _ordered([*overlays, OVERLAY_DIVERGENCE])

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
        "turns": as_count(work_detail.get("turn_count")),
        "session_id": session_id,
        "model": _reported_text(work_detail.get("model")),
        "transcript": None if work is None else engine.text(work.get("stdout")),
        "stderr_log": None if work is None else engine.text(work.get("stderr")),
        "resume_command": _resume_command(session_id, working_directory),
        "started_at": None if work is None else engine.moment(work.get("startedAt")),
        "finished_at": None if work is None else engine.moment(work.get("finishedAt")),
        "exit_code": exit_code,
        "assertion_exit": as_count(assertion_account.get(EXIT_KEY)),
        "assertion_source": _source(assertion_account.get(SOURCE_KEY)),
        "assertion_backed_by": _reported_text(assertion_account.get(BACKED_BY_KEY)),
        "timeout_seconds": None if killed is None else killed.bound_seconds,
        "elapsed_seconds": None if killed is None else killed.elapsed_seconds,
        "assertion_tail": assertion_tail,
        "remedy": _remedy(remedy_report, reports.get(f"verify_{step_id}")),
        "divergence": divergence,
        "headroom": _headroom(work_report, _holding(step_id, outcome, nodes, holds, orchestrator_gone)),
    }
    record = StepRecord(
        step_id=step_id,
        outcome=outcome,
        overlays=overlays,
        verified=outcome == OUTCOME_VERIFIED,
        # The commit's follow-up is the step's too: a path it left uncommitted because
        # somebody else had it dirty is work the step found and did not do ([21]).
        follow_up_work=normalise_all(
            [*_follow_ups(work_report), *_follow_ups(commit_report)]
        ),
        left_uncommitted=normalise_all(commit_detail.get("left_uncommitted")),
        nodes=sorted(_nodes_of_step(nodes, step_id)),
        provenance=_provenance(
            fields,
            derived=(
                "exit_code",
                "resume_command",
                "timeout_seconds",
                "elapsed_seconds",
                *(("divergence",) if divergence_is_derived else ()),
            ),
        ),
        **cast(Any, fields),
    )
    return record, [
        _refused(f"{WORK_ROLE}_{step_id}", fault) for fault in refusals
    ]


def _remedy(
    report: dict[str, Any] | None, first: dict[str, Any] | None
) -> Remedy | None:
    """A remedy node's account, bounded like every other string an agent wrote."""
    if report is None:
        return None
    detail = _detail(report)
    said = report.get("summary")
    return Remedy(
        status=str(report.get("status")),
        said=None if not isinstance(said, str) else normalise(said, limit=LINE_LIMIT),
        first_exit=as_count(_detail(first).get(EXIT_KEY)),
        resumed_session=_reported_text(detail.get("resumed_session")),
    )


# What a failed assertion's output is read back for. Its last lines are where a test
# runner or a type checker names what failed, and a person deciding whether to re-run wants
# that line, not the log's path ([23 B]).
ASSERTION_TAIL_BYTES = 8192


def _assertion_tail(assertion: dict[str, Any]) -> str | None:
    """The end of what a failed assertion printed, from the logs the engine kept for it.

    Standard output first — a test runner's verdict and a type checker's findings land
    there — and standard error where that is empty. The logs live in the engine's home and
    outlive the run; where they are gone the record says so by carrying nothing.
    """
    for channel in ("stdout", "stderr"):
        path = engine.text(assertion.get(channel))
        if path is None:
            continue
        try:
            with open(path, "rb") as handle:
                handle.seek(0, os.SEEK_END)
                handle.seek(max(0, handle.tell() - ASSERTION_TAIL_BYTES))
                text = handle.read().decode("utf-8", errors="replace")
        except OSError:
            continue
        tail = normalise(text[-TEXT_LIMIT:], limit=TEXT_LIMIT)
        if tail:
            return tail
    return None


def _asserted_over_a_killed_step(assertion: dict[str, Any] | None) -> Divergence | None:
    """What the assertion node found, where the step it asserted was killed before reporting."""
    if assertion is None:
        return None
    status = _status(assertion)
    if status == engine.NODE_STATUS_SUCCEEDED:
        return Divergence(reported=REPORTED_KILLED, asserted=True)
    if status == engine.NODE_STATUS_FAILED:
        return Divergence(reported=REPORTED_KILLED, asserted=False)
    return None


def _source(value: object) -> str | None:
    """Which execution backed an assertion, quoted only where it is one of the two words."""
    return value if isinstance(value, str) and value in ASSERTION_SOURCES else None


def _follow_ups(report: dict[str, Any] | None) -> list[Any]:
    found: Any = None if report is None else report.get("follow_up_work")
    return list(cast(list[Any], found)) if isinstance(found, list) else []


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
) -> tuple[Infrastructure, list[Integrity]]:
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
        ), []
    outcome, _, cause, refusals = classify_step(
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
    resolution = _detail(report).get("resolution")
    resolution_field: dict[str, object] = (
        {"resolution": cast(dict[str, Any], resolution)}
        if isinstance(resolution, dict)
        else {}
    )
    fields: dict[str, object] = {
        "role": None if naming is None else naming.role,
        "cause": cause if cause is not None else _report_cause(report),
        "summary": None if summary is None else normalise(summary, limit=LINE_LIMIT),
        "started_at": engine.moment(node.get("startedAt")),
        "finished_at": engine.moment(node.get("finishedAt")),
        **resolution_field,
    }
    return Infrastructure(
        name=name, outcome=outcome, provenance=_provenance(fields), **cast(Any, fields)
    ), [_refused(name, fault) for fault in refusals]


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
    holds: dict[str, dict[str, Any]] | None = None,
    integrity: Sequence[Integrity] = (),
) -> RunRecord:
    """One run's whole record, from the engine's last snapshot and this run's own reports.

    `in_flight_node` names the one node a record cannot judge, which is the node building
    it: the run's own release writes a record for the run it is still finishing
    ([triggers.md]), and nothing else passes it. `holds` is what running steps are
    announcing about their wait at the allowance ([read_holds]). `integrity` is what the
    reading of the reports already refused ([read_reports]); everything this derivation
    refuses is added to it.
    """
    record = status_record if status_record is not None else {}
    refused: list[Integrity] = list(integrity)
    # One entry per node identity, because every projection downstream is keyed on a node's
    # name: two occurrences of one name would put two rows and one key into the record, so
    # the detailed rows would show both using whichever the mapping kept while the verdict
    # used the other. The identity is kept and the second claim about it is refused, named
    # here rather than dropped — a node whose failure nothing can report is the one thing
    # this walk exists to prevent.
    recorded, duplicates = _one_node_per_identity(engine.nodes_of(record))
    refused.extend(duplicates)
    nodes = {
        name: node
        for node in recorded
        if (name := engine.node_name(node))
    }

    reading = engine.run_reading(record) if status_record is not None else engine.RunReading(None, "", None)
    engine_state = reading.name
    if reading.why is not None:
        refused.append(
            Integrity(
                subject=run_id,
                fault=INTEGRITY_ENGINE_RUN_STATUS,
                detail=flatten(reading.why, limit=LINE_LIMIT),
            )
        )
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
    steps: list[StepRecord] = []
    for step_id in step_ids:
        step, step_refusals = _step_record(
            step_id,
            nodes=nodes,
            reports=reports,
            holds={} if holds is None else holds,
            run_settled=run_settled,
            orchestrator_gone=orchestrator_gone,
        )
        steps.append(step)
        refused.extend(step_refusals)

    infrastructure: list[Infrastructure] = []
    for node in recorded:
        name = engine.node_name(node)
        if not _is_infrastructure(name, step_ids):
            continue
        item, item_refusals = _infrastructure(
            name,
            node,
            reports.get(name),
            run_settled=run_settled,
            orchestrator_gone=orchestrator_gone,
            in_flight=name == in_flight_node,
            in_flight_cause=in_flight_cause,
        )
        infrastructure.append(item)
        refused.extend(item_refusals)

    engine_nodes = [
        _engine_node(engine.node_name(node), node, step_ids) for node in recorded
    ]
    edges = _edges(nodes)
    waves = _census(reports)
    order = step_order(nodes, step_ids)
    refused = refused[:LIST_LIMIT]
    verdict = derive_verdict(
        steps,
        infrastructure,
        engine_state,
        waves,
        engine_status_readable=reading.why is None,
    )
    attention = derive_attention(steps, infrastructure, waves, order, refused)

    parameters = _parameters(record)
    lock_detail = _detail(reports.get("lock_acquire"))
    plan = engine.text(lock_detail.get("plan")) or engine.text(record.get("name"))
    git = _git(parameters, reports, waves)
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
        engine_run_status=reading.status if reading.status is not None else -1,
        engine_run_status_name=engine_state,
        # The engine calls this run clean and Cairn does not. It is a fact about two
        # readings rather than a judgement, and it is I5's whole point made checkable.
        engine_contradicted=(
            engine_state in (engine.RUN_SUCCEEDED, engine.RUN_PARTIALLY_SUCCEEDED)
            and verdict not in (VERDICT_GREEN, VERDICT_ALL_NO_OP)
        ),
        verdict=verdict,
        exit_code=VERDICT_EXIT_CODES[verdict],
        allowance=_latest_allowance(steps),
        trigger=_trigger(record),
        lineage=_lineage(parameters, steps, reports),
        steps=steps,
        infrastructure=infrastructure,
        nodes=engine_nodes,
        edges=edges,
        waves=waves,
        attention=attention,
        integrity=refused,
        git=git,
        next_action=derive_next_action(
            verdict,
            steps,
            engine_state=engine_state,
            run_id=run_id,
            plan=plan,
            repository=git["repository"],
            waves=waves,
            infrastructure=infrastructure,
            order=order,
        ),
        provenance=_provenance(fields, derived=("owner_alive", "view_url")),
        **cast(Any, fields),
    )


# Worst first. A name the engine recorded twice is read as the worst of what its
# occurrences claim, so the reading of a run cannot be improved by whichever copy happened
# to be written last — and so the two orders of the same pair read identically.
_WORST_FIRST: tuple[int, ...] = (
    engine.NODE_STATUS_FAILED,
    engine.NODE_STATUS_ABORTED,
    engine.NODE_STATUS_RUNNING,
    engine.NODE_STATUS_NOT_STARTED,
    engine.NODE_STATUS_SKIPPED,
    engine.NODE_STATUS_SUCCEEDED,
)


def _severity(node: dict[str, Any]) -> tuple[int, str]:
    """How bad one occurrence of a name claims to be, and a tie-break that reads the same
    whichever order the occurrences arrived in."""
    status = node.get("status")
    rank = _WORST_FIRST.index(status) if status in _WORST_FIRST else -1
    return rank, json.dumps(node, sort_keys=True, default=str)


def _one_node_per_identity(
    recorded: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[Integrity]]:
    """The engine's nodes with one entry per name, in the order the engine first named each.

    A name recorded twice is one identity with two claims about it. Keeping both would give
    the record two rows and the projection one key, so a surface would show one occurrence's
    outcome under the other's name; keeping the last would let input order decide the
    verdict. So the identity keeps the worst of what its occurrences claim, by a total order
    over their own content, and the refusal of the rest is recorded against the name.
    """
    occurrences: dict[str, list[dict[str, Any]]] = {}
    for node in recorded:
        occurrences.setdefault(engine.node_name(node), []).append(node)
    kept: list[dict[str, Any]] = []
    refused: list[Integrity] = []
    for name, found in occurrences.items():
        kept.append(found[0] if len(found) == 1 else min(found, key=_severity))
        if len(found) > 1:
            statuses = ", ".join(
                sorted(
                    engine.NODE_STATUS.get(cast(int, node.get("status")), "unreadable")
                    for node in found
                )
            )
            refused.append(
                Integrity(
                    subject=name,
                    fault=INTEGRITY_DUPLICATE_NODE,
                    detail=flatten(
                        f"the engine recorded {len(found)} nodes under this one name "
                        f"({statuses}); the record reads the worst of them and attributes "
                        "nothing to the others",
                        limit=LINE_LIMIT,
                    ),
                )
            )
    return kept, refused


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


# Who an absent actor means, per trigger kind. The engine names an authenticated user only
# for a run started through its own view, so every other start arrives with no actor at all
# — and what that absence means is the kind's to say: Cairn's own skill for a manual start,
# which is what a `dagu start` from the CLI is recorded as, the scheduler for a firing and
# for the catch-up it performs, the retry scanner for a retry, and the run above for a
# sub-run. Total over the trigger vocabulary, which a test asserts.
ATTRIBUTION_BY_TRIGGER: dict[str, str] = {
    engine.TRIGGER_UNKNOWN: ATTRIBUTION_UNKNOWN,
    engine.TRIGGER_SCHEDULER: ATTRIBUTION_SCHEDULER,
    engine.TRIGGER_MANUAL: ATTRIBUTION_CAIRN,
    engine.TRIGGER_WEBHOOK: ATTRIBUTION_WEBHOOK,
    engine.TRIGGER_SUBDAG: ATTRIBUTION_PARENT_RUN,
    engine.TRIGGER_RETRY: ATTRIBUTION_RETRY_SCANNER,
    engine.TRIGGER_CATCHUP: ATTRIBUTION_SCHEDULER,
}


def _trigger(record: dict[str, Any]) -> Trigger:
    raw = record.get("triggerType")
    kind = engine.trigger_name(raw) if raw is not None else engine.TRIGGER_UNKNOWN
    actor = engine.text(record.get("triggerActor"))
    fields: dict[str, object] = {"actor": actor}
    return Trigger(
        kind=kind,
        actor=actor,
        # A named actor is the one thing that speaks for itself. Everything else is the
        # kind's to attribute, because an absent actor is the ordinary case for every kind
        # and means something different in each.
        attribution=ATTRIBUTION_USER
        if actor is not None
        else ATTRIBUTION_BY_TRIGGER[kind],
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
    "ATTRIBUTION_BY_TRIGGER",
    "COMPATIBLE_NODE_STATUS",
    "Classification",
    "ReportSet",
    "census_exclusions",
    "classify_step",
    "derive_attention",
    "derive_next_action",
    "derive_verdict",
    "extract",
    "read_reports",
    "step_order",
]
