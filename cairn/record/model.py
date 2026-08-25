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

from typing import TypedDict

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


class StepRecord(TypedDict):
    """One step of the plan, and everything this run knows about it."""

    step_id: str
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
    cost_usd: float | None
    cost_is_notional: bool
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


class Budget(TypedDict):
    """What the run spent, and how much of that figure is money.

    `notional` is not a footnote: on a subscription login the figure is an API-equivalent
    price rather than money spent, and a rendering that dropped the flag would be inventing
    a number the run never paid.
    """

    cost_usd: float | None
    notional: bool
    turns: int | None
    priced_steps: int
    unpriced_steps: int
    provenance: dict[str, str]


class GitFacts(TypedDict):
    """What the run left in the repository, as its own steps recorded it."""

    repository: str | None
    parent_branch: str | None
    commits: list[str]
    landed: list[str]
    excluded: list[str]
    provenance: dict[str, str]


class Trigger(TypedDict):
    """How the run was started, and by whom where the engine knows.

    An absent actor means Cairn started it, and is never rendered as unknown — `unknown` is
    a trigger kind the engine can record, and one word for two facts is one word too few.
    """

    kind: str
    actor: str | None
    started_by_cairn: bool
    provenance: dict[str, str]


class Lineage(TypedDict):
    """The runs this one rests on, and nothing that changes what it does.

    Completion authority stays with the marker in git. This is an observability contract:
    a missing or corrupt lineage costs a reader an explanation, never a run its correctness.
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
    trigger: Trigger
    lineage: Lineage
    steps: list[StepRecord]
    infrastructure: list[Infrastructure]
    nodes: list[EngineNode]
    edges: list[Edge]
    waves: list[WaveCensus]
    attention: list[Attention]
    budget: Budget
    git: GitFacts
    next_action: NextAction
    provenance: dict[str, str]


__all__ = [
    "Attention",
    "Budget",
    "Diffstat",
    "Divergence",
    "Edge",
    "EngineNode",
    "ExcludedBranch",
    "Freshness",
    "GitFacts",
    "Infrastructure",
    "Lineage",
    "NextAction",
    "RunRecord",
    "StepRecord",
    "Trigger",
    "WaveCensus",
]
