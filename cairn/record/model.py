"""The record's shape: what one run leaves behind, with nothing in it that decides how to say it.

Every field is here or it is nowhere. A renderer reads this model and computes no fact of
its own ([14]), so a value the extraction could not establish has to be representable as
*not established* — which is what the `provenance` map on each section is for.

The provenance map lists every field that is not plainly recorded, and only those: a map
that repeated the whole record would double it for no reader. The invariant a test asserts
across the whole fixture corpus is the useful half — a field whose value is None appears in
its section's map as `absent`, so no absence can be mistaken for a measured zero.
"""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict

from cairn.verify import Divergence


class Diffstat(TypedDict):
    """What one step's commit changed, counted while the index still held it."""

    files: int
    insertions: int
    deletions: int


class Freshness(TypedDict):
    """The scope a no-op matched under, and both keys, so caching is legible as caching.

    A step permanently done and a step merely fresh enough are the same grey line in a
    report otherwise, and the difference between correct caching and stale research is
    exactly the difference between `once` and `daily`.
    """

    scope: str
    key: str
    recorded_scope: str
    recorded_key: str


class Remedy(TypedDict):
    """What a remedied step's remedy node did: declined, with why, or ran.

    `first_exit` is the assertion's exit before the remedy; the step's own
    `assertion_exit` is then the exit of the assertion run again after it.
    """

    status: str
    said: str | None
    first_exit: int | None
    resumed_session: str | None


class Where(TypedDict):
    """Where a running step's session is, read from the transcript it is still writing.

    Present only on a step the record holds at `running`, and only where its transcript is
    on disk. `turns`, `action` and `object` are absent together where the file held nothing
    this could read — the age still stands, because it is the file's own last-write time
    and is what says a session has gone quiet ([20 A]).
    """

    turns: int | None
    action: str | None
    object: str | None
    last_event_age_seconds: float


class AllowanceAccount(TypedDict):
    """Which subscription funded a session: the organisation, and what kind it is.

    Never the email address the account signs in with. Two accounts have to be tellable
    apart — a hold taken on one says nothing about the other — and that is all this is for.
    """

    org_id: str
    subscription_type: str | None


class AllowanceWindow(TypedDict):
    """One window of the subscription's allowance as one measurement saw it."""

    window: str
    used: float | None
    status: str | None
    resets_at: str | None
    source: str | None
    read_at: str | None


class AllowanceHold(TypedDict):
    """One stretch a step waited at the allowance: before its session, or after a limit."""

    window: str | None
    started: str | None
    until: str | None
    why: str | None
    after: str | None


class Headroom(TypedDict):
    """What an agent step's session was admitted on, every hold, and every resume.

    `holding` is the hold the step is in right now, read from its announcement while the
    step is still running; it is what keeps a held run from reading as a stalled one.

    `reading` is what admission actually rested on, and `set_aside` names the windows it
    ignored as too old to rest on. The two are kept apart so a stale measurement is never
    shown as the reading a step was admitted against.

    `account` is the subscription the session spent. Two steps of one run can be funded by
    two accounts, and a window one of them was held on says nothing about the other ([43 A]).
    """

    admission: str | None
    reason: str | None
    account: AllowanceAccount | None
    reading: list[AllowanceWindow]
    set_aside: list[str]
    holds: list[AllowanceHold]
    resumes: int
    held_window: str | None
    held_until: str | None
    holding: AllowanceHold | None


class StepRecord(TypedDict):
    """One step of the plan, and everything this run knows about it."""

    step_id: str
    # What the plan document calls this step: its `title`, and the `slug` it was written
    # under, both read from the graph this run was generated from. The engine id above is
    # that slug sanitised, with a numeric suffix where two of them sanitised alike, so a
    # report carrying only it sends a reader to the graph to find out which step it is
    # ([29 B]). Absent where the graph this run ran is no longer on disk.
    title: str | None
    slug: str | None
    outcome: str
    overlays: list[str]
    cause: str | None
    position: str | None
    asked: str | None
    said: str | None
    verified: bool
    divergence: Divergence | None
    freshness: Freshness | None
    completed_by_run: str | None
    branch: str | None
    commit: str | None
    diffstat: Diffstat | None
    # Paths the step's commit left alone because they were already dirty when its session
    # started: somebody else's in-flight work, named rather than swept in or lost ([21]).
    left_uncommitted: list[str]
    turns: int | None
    session_id: str | None
    model: str | None
    transcript: str | None
    stderr_log: str | None
    resume_command: str | None
    follow_up_work: list[str]
    started_at: str | None
    finished_at: str | None
    exit_code: int | None
    # What the assertion exited, and which execution backed it: the step's own, or another
    # step's proof of the same command against the same tree ([24 A]). Recorded by the
    # assertion's own gate and the mark gate rather than derived, because the engine drops
    # the number on the way to disk.
    assertion_exit: int | None
    assertion_source: str | None
    assertion_backed_by: str | None
    # Present only on a step a bound stopped: the bound that fired and how long the step
    # had run. Derived from the engine's own sentence, because the node is a plain `failed`
    # and the kill survives nowhere else ([22 A]).
    timeout_seconds: int | None
    elapsed_seconds: float | None
    # The end of what a failed assertion printed, read from the engine's own logs for it, so
    # the report can quote why the gate closed rather than name a log path ([23 B]). One
    # field per channel, because a runner that chatters on standard output and names the
    # failure on standard error would otherwise have the failure hidden by the chatter — the
    # two are different evidence and the record keeps them apart ([29 D]).
    assertion_stdout: str | None
    assertion_stderr: str | None
    # Present only on a step that declared `remediate` and whose remedy gate left an account.
    remedy: Remedy | None
    # Present on an agent step that reached its session's admission, or is holding now.
    headroom: Headroom | None
    # Present only while the step is running: which turn its session is on, what it last
    # called and on what, and how long since its transcript was last written. It is what
    # lets a report say where a live session is rather than only that it is live ([20 A]).
    where: Where | None
    nodes: list[str]
    provenance: dict[str, str]


class EngineNode(TypedDict):
    """One node exactly as the engine recorded it, with Cairn's reading beside it.

    Every node reaches the record, including one whose name the topology's grammar does not
    cover: a node dropped for being unrecognisable is a node whose failure nothing reports.
    """

    name: str
    role: str | None
    subject: str | None
    step_id: str | None
    status: int
    status_name: str
    depends: list[str]
    started_at: str | None
    finished_at: str | None
    working_directory: str | None
    stdout: str | None
    stderr: str | None
    error: str | None
    exit_code: int | None
    provenance: dict[str, str]


class Edge(TypedDict):
    """One dependency the engine enforced, and what kind of ordering it is."""

    upstream: str
    downstream: str
    kind: str


class Infrastructure(TypedDict):
    """One node that is Cairn's own housekeeping rather than a step of the plan."""

    name: str
    role: str | None
    outcome: str
    cause: str | None
    summary: str | None
    started_at: str | None
    finished_at: str | None
    resolution: NotRequired[dict[str, Any]]
    provenance: dict[str, str]


class ExcludedBranch(TypedDict):
    branch: str
    cause: str
    summary: str


class WaveCensus(TypedDict):
    """What one wave produced, read from the join and never re-derived.

    After the first slot lands, a landed branch and a branch that never carried work are
    both ancestors of the parent, so git cannot answer this question at all. The join runs
    before any landing and is the only node that sees the whole wave intact.
    """

    wave: int
    into: str
    arrived: list[str]
    excluded: list[ExcludedBranch]
    settled: list[str]
    provenance: dict[str, str]


class Attention(TypedDict):
    """One thing a reader has to act on, in the run's own words."""

    kind: str
    subject: str
    summary: str
    cause: str | None


class Integrity(TypedDict):
    """One piece of this run's evidence the record refused, and what it refused about it.

    `subject` is the engine node the evidence was found under, or the run id where the fault
    is the run's own. A refused fact is absent from the record rather than approximated, so
    this list is the only place it is accounted for — which is why the list is carried in
    the record and raised into attention rather than logged and dropped.
    """

    subject: str
    fault: str
    detail: str


class GitFacts(TypedDict):
    """What the run left in the repository, as its own steps recorded it."""

    repository: str | None
    parent_branch: str | None
    commits: list[str]
    landed: list[str]
    excluded: list[str]
    provenance: dict[str, str]


class Trigger(TypedDict):
    """How the run was started, and by whom.

    `attribution` is derived from the kind together with the actor, because an absent actor
    means something different for each kind — Cairn's own skill for a manual start, the
    scheduler for a cron firing, the retry scanner for a retry. The actor is kept beside it
    and is absent for every start that did not come through the engine's own view.
    """

    kind: str
    actor: str | None
    attribution: str
    provenance: dict[str, str]


class Lineage(TypedDict):
    """The runs this one rests on, and nothing that changes what it does.

    Completion authority stays with the marker in git. This is an observability contract:
    a missing or corrupt lineage leaves a reader without an explanation, never a run
    without its correctness.
    """

    occasion: str | None
    previous_runs: list[str]
    completed_by: dict[str, str]
    provenance: dict[str, str]


class NextAction(TypedDict):
    """What to do now, derived from the record rather than composed as prose."""

    action: str
    subject: str | None
    command: str | None


class RunRecord(TypedDict):
    """What happened in one run. Presentation-free, and the only thing a surface reads."""

    record_version: int
    run_id: str
    plan: str | None
    graph_sha256: str | None
    attempt_id: str | None
    attempts: int
    engine_version: str
    engine_run_status: int
    engine_run_status_name: str
    engine_contradicted: bool
    owner_alive: bool | None
    verdict: str
    exit_code: int
    # Where the engine's own view serves this run, live and cold alike. Carried rather than
    # composed by each renderer, because no reader computes a fact this model does not hold
    # — and because it is composed from the engine's own name for the workflow, which a
    # renderer holding only the plan's slug could not know ([layout.py]).
    view_url: str | None
    started_at: str | None
    finished_at: str | None
    # The latest measurement of each window any step's admission rested on, with the moment
    # and the source of each — so a reader can see how full the account was, and how old
    # that knowledge is, without opening a step.
    allowance: list[AllowanceWindow]
    # Every subscription this run's sessions were funded by, each named once. A run whose
    # steps ran either side of a credential switch was funded by two.
    accounts: list[AllowanceAccount]
    # Which account the provider is logged in to where this record was built — this machine
    # now, not this run, like `owner_alive`. It is what lets a hold taken on another
    # account's week be read as the statement about another account that it is ([43 B]).
    account_in_use: AllowanceAccount | None
    trigger: Trigger
    lineage: Lineage
    steps: list[StepRecord]
    infrastructure: list[Infrastructure]
    nodes: list[EngineNode]
    edges: list[Edge]
    waves: list[WaveCensus]
    attention: list[Attention]
    # Every fact this run's evidence could not establish because the evidence contradicted
    # itself or could not be read. Empty on a run whose evidence is whole.
    integrity: list[Integrity]
    git: GitFacts
    next_action: NextAction
    provenance: dict[str, str]


__all__ = [
    "AllowanceAccount",
    "AllowanceHold",
    "AllowanceWindow",
    "Attention",
    "Diffstat",
    "Divergence",
    "Edge",
    "EngineNode",
    "ExcludedBranch",
    "Freshness",
    "GitFacts",
    "Headroom",
    "Infrastructure",
    "Integrity",
    "Lineage",
    "NextAction",
    "Remedy",
    "RunRecord",
    "StepRecord",
    "Trigger",
    "WaveCensus",
    "Where",
]
