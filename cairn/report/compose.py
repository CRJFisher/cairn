"""One run said once, in the frozen order, for every sink to render without deciding anything.

This is the only module in the report that imports the record, and that is the whole
mechanism behind doc 14's rule. A renderer is handed the document this builds; it has no
record to re-read, no projection to re-order and no verdict to re-derive, so "renderers
render" is a fact about what a renderer was given rather than a discipline it observes.

The division of labour is exact. **The record supplies enumeration** — which steps, nodes,
edges, attention items and waves exist, and in what order. **The projection supplies every
scalar**, because a number a surface worked out for itself is a second opinion, and doc 14
exists because four systems out of seven print a green summary over work that failed.

Sections are built by walking `SECTIONS`, so their order is the tuple's and no caller's.
"""

from __future__ import annotations

from cairn.record.engine import RUN_RUNNING
from cairn.record.model import RunRecord
from cairn.record.vocabulary import (
    ATTENTION_ORDER,
    OUTCOME_EXCLUDED,
    OUTCOME_NO_OP,
    OVERLAY_DIVERGENCE,
    STEP_OUTCOMES,
    VERDICT_ALL_NO_OP,
)
from cairn.report import graph
from cairn.report.phrases import (
    HEADLINE_BY_VERDICT,
    LABEL_BY_ATTENTION,
    SENTENCE_BY_ACTION,
    TONE_BY_VERDICT,
)
from cairn.report.spine import (
    GRAPH_NODE_CAP,
    ITEMS_PER_ATTENTION_KIND,
    RULE_ACTOR,
    RULE_ASSERTION,
    RULE_LINK,
    RULE_MONEY,
    SECTION_ATTENTION,
    SECTION_NEXT,
    SECTION_RECEIPTS,
    SECTION_SHAPE,
    SECTION_STEPS,
    SECTION_VERDICT,
    SECTIONS,
    Block,
    Chrome,
    Diagram,
    Document,
    Facing,
    Fact,
    Fields,
    Headline,
    Nothing,
    Section,
    Statement,
    Table,
    Verbatim,
)


def document(record: RunRecord) -> Document:
    """The whole report, in the one order every rendering conforms to."""
    builders = {
        SECTION_VERDICT: _verdict,
        SECTION_NEXT: _next_action,
        SECTION_ATTENTION: _attention,
        SECTION_STEPS: _steps,
        SECTION_SHAPE: _shape,
        SECTION_RECEIPTS: _receipts,
    }
    sections: list[Section] = []
    for question in SECTIONS:
        blocks = builders[question.key](record)
        sections.append(
            Section(question, tuple(blocks) or (Nothing("nothing", question.nothing),))
        )
    return Document(record["run_id"], tuple(sections))


def _verdict(record: RunRecord) -> list[Block]:
    """Did it work — and everything that makes the answer not a clean success, above the fold.

    The exclusion count, the no-op count and the engine's disagreement are all here rather
    than in sections of their own. A section can be scrolled past; the first block of the
    first section cannot, and I5 fails quietly if a dropped branch is a subsection.
    """
    verdict = record["verdict"]
    blocks: list[Block] = [
        Headline(
            "headline",
            TONE_BY_VERDICT[verdict],
            (Chrome(HEADLINE_BY_VERDICT[verdict]),),
        )
    ]
    if any(step["outcome"] == OUTCOME_EXCLUDED for step in record["steps"]):
        blocks.append(
            Statement(
                "statement",
                (
                    Fact((f"run.steps.{OUTCOME_EXCLUDED}",)),
                    Chrome("of"),
                    Fact(("run.step_count",)),
                    Chrome("steps contributed no verified work."),
                ),
            )
        )
    for index, census in enumerate(record["waves"]):
        if census["excluded"]:
            blocks.append(
                Statement(
                    "statement",
                    (
                        Chrome("Wave"),
                        Fact((f"wave.{index}.number",)),
                        Chrome("declined to land"),
                        Fact((f"wave.{index}.excluded",)),
                        Chrome("."),
                    ),
                )
            )
    if verdict == VERDICT_ALL_NO_OP or any(
        step["outcome"] == OUTCOME_NO_OP for step in record["steps"]
    ):
        blocks.append(
            Statement(
                "statement",
                (
                    Fact((f"run.steps.{OUTCOME_NO_OP}",)),
                    Chrome("steps skipped: already complete. Earlier runs did the work:"),
                    Fact(("run.previous_runs",)),
                    Chrome("."),
                ),
            )
        )
    if record["engine_contradicted"]:
        blocks.append(
            Statement(
                "statement",
                (
                    Chrome("The engine calls this run"),
                    Fact(("run.engine_status",)),
                    Chrome("and Cairn does not. Cairn's verdict is derived by walking"),
                    Fact(("run.node_count",)),
                    Chrome("nodes; the engine's own status is not read as a verdict."),
                ),
            )
        )
    # The engine still calls this run running and the process that was running it is gone.
    # `owner_alive` alone would be false for every finished run too — the process really is
    # gone — so it is the pair that means a crash, exactly as the extraction reads it.
    if record["engine_run_status_name"] == RUN_RUNNING and record["owner_alive"] is False:
        blocks.append(
            Statement(
                "statement",
                (
                    Chrome(
                        "The process running this run is gone, so what it was doing when it "
                        "died is not recorded. The engine's own record still says"
                    ),
                    Fact(("run.engine_status",)),
                    Chrome(", and this report does not."),
                ),
            )
        )
    blocks.append(
        Fields(
            "fields",
            None,
            (
                ("verdict", Fact(("run.verdict",))),
                ("exit code", Fact(("run.exit_code",))),
                ("engine's own status", Fact(("run.engine_status",))),
                ("run", Fact(("run.id",))),
                ("plan", Fact(("run.plan",))),
                ("started", Fact(("run.started_at",))),
                ("finished", Fact(("run.finished_at",))),
            ),
        )
    )
    return blocks


def _next_action(record: RunRecord) -> list[Block]:
    """What to do now, from the record's own derivation rather than from prose."""
    action = record["next_action"]
    blocks: list[Block] = [
        Statement("statement", (Chrome(SENTENCE_BY_ACTION[action["action"]]),))
    ]
    if action["subject"] is not None:
        blocks.append(
            Statement(
                "statement",
                (Chrome("It concerns"), Fact(("run.next_subject",)), Chrome(".")),
            )
        )
    if action["command"] is not None:
        blocks.append(Verbatim("verbatim", None, Fact(("run.next_command",))))
    # The action's own frozen word, beside the sentence that phrases it. Automation reads
    # one and a person reads the other, and a report that carried only the sentence would
    # leave the three renderings with nothing checkable to agree about.
    blocks.append(
        Fields("fields", None, (("action", Fact(("run.next_action",))),))
    )
    return blocks


def _attention(record: RunRecord) -> list[Block]:
    """Everything a person has to act on, in the record's own frozen order.

    Grouped by kind so that fifty follow-ups cannot bury one exclusion, and capped per kind
    with the remainder counted rather than dropped — a report that silently truncated would
    be doing the thing this document exists to catch.
    """
    if not record["attention"]:
        return []
    blocks: list[Block] = [
        Statement(
            "statement",
            (
                Chrome("Things needing a person:"),
                Fact(("run.attention_count",)),
                Chrome("in all, in the order they need one."),
            ),
        )
    ]
    unplaced = [
        index
        for index, item in enumerate(record["attention"])
        if item["kind"] not in ATTENTION_ORDER
    ]
    for kind in ATTENTION_ORDER:
        indexed = [
            index
            for index, item in enumerate(record["attention"])
            if item["kind"] == kind
        ]
        if not indexed:
            continue
        shown = indexed[:ITEMS_PER_ATTENTION_KIND]
        blocks.append(
            Table(
                "table",
                LABEL_BY_ATTENTION[kind],
                ("subject", "what", "cause"),
                tuple(
                    (
                        Fact((f"attention.{index}.subject",)),
                        Fact((f"attention.{index}.summary",)),
                        Fact((f"attention.{index}.cause",)),
                    )
                    for index in shown
                ),
            )
        )
        if len(indexed) > len(shown):
            blocks.append(
                Statement(
                    "statement",
                    (
                        Chrome(
                            f"and {len(indexed) - len(shown)} more of this kind, in the "
                            "record"
                        ),
                    ),
                )
            )
    if unplaced:
        # A kind this vocabulary does not name still gets counted above, so dropping it here
        # would leave a report whose own total disagrees with what it shows.
        blocks.append(
            Table(
                "table",
                "Of a kind this report does not know",
                ("subject", "what", "cause"),
                tuple(
                    (
                        Fact((f"attention.{index}.subject",)),
                        Fact((f"attention.{index}.summary",)),
                        Fact((f"attention.{index}.cause",)),
                    )
                    for index in unplaced
                ),
            )
        )
    return blocks


def _steps(record: RunRecord) -> list[Block]:
    """What each step did, its own account of itself, and what it was asked."""
    if not record["steps"]:
        return []
    blocks: list[Block] = [
        Table(
            "table",
            None,
            ("step", "outcome", "also", "cause", "what it said"),
            tuple(
                (
                    Chrome(step["step_id"]),
                    Fact((f"step.{step['step_id']}.outcome",)),
                    Fact((f"step.{step['step_id']}.overlays",)),
                    Fact((f"step.{step['step_id']}.cause",)),
                    Fact((f"step.{step['step_id']}.said",)),
                )
                for step in record["steps"]
            ),
        ),
        Fields(
            "fields",
            "Outcomes",
            tuple(
                (outcome, Fact((f"run.steps.{outcome}",))) for outcome in STEP_OUTCOMES
            ),
        ),
    ]
    for step in record["steps"]:
        key = f"step.{step['step_id']}"
        if step["outcome"] == OUTCOME_NO_OP:
            blocks.append(
                Fields(
                    "fields",
                    f"{step['step_id']} was already complete",
                    (
                        ("freshness scope", Fact((f"{key}.scope",))),
                        ("freshness key", Fact((f"{key}.key",))),
                        ("done by run", Fact((f"{key}.completed_by",))),
                    ),
                )
            )
        if OVERLAY_DIVERGENCE in step["overlays"]:
            blocks.append(
                Facing(
                    "facing",
                    Chrome(step["step_id"]),
                    "the step's own account",
                    Fact((f"{key}.divergence_reported",)),
                    "what verification found",
                    Fact((f"{key}.divergence_asserted",), RULE_ASSERTION),
                )
            )
        blocks.append(
            Verbatim("verbatim", f"{step['step_id']} was asked", Fact((f"{key}.asked",)))
        )
    return blocks


def _shape(record: RunRecord) -> list[Block]:
    """What shape the run was — the fifth question, and never the first."""
    blocks: list[Block] = [
        Fields(
            "fields",
            None,
            (
                ("steps", Fact(("run.step_count",))),
                ("engine nodes", Fact(("run.node_count",))),
                ("edges", Fact(("run.edge_count",))),
                ("waves", Fact(("run.wave_count",))),
                ("attempts", Fact(("run.attempts",))),
                ("trigger", Fact(("run.trigger",))),
                ("started by", Fact(("run.actor", "run.started_by_cairn"), RULE_ACTOR)),
            ),
        )
    ]
    for index, census in enumerate(record["waves"]):
        wave = f"wave.{index}"
        blocks.append(
            Fields(
                "fields",
                "A wave",
                (
                    ("wave", Fact((f"{wave}.number",))),
                    ("landed into", Fact((f"{wave}.into",))),
                    ("arrived with work", Fact((f"{wave}.arrived",))),
                    ("declined", Fact((f"{wave}.excluded",))),
                    ("already contained", Fact((f"{wave}.settled",))),
                ),
            )
        )
        if census["excluded"]:
            blocks.append(
                Table(
                    "table",
                    "What the wave declined, and why",
                    ("branch", "cause", "what the gate said"),
                    tuple(
                        (
                            Chrome(entry["branch"]),
                            Fact((f"{wave}.excluded.{entry['branch']}.cause",)),
                            Fact((f"{wave}.excluded.{entry['branch']}.summary",)),
                        )
                        for entry in census["excluded"]
                    ),
                )
            )
    if record["infrastructure"]:
        blocks.append(
            Table(
                "table",
                "Cairn's own housekeeping",
                ("node", "outcome", "cause", "what it said"),
                tuple(
                    (
                        Chrome(item["name"]),
                        Fact((f"infrastructure.{item['name']}.outcome",)),
                        Fact((f"infrastructure.{item['name']}.cause",)),
                        Fact((f"infrastructure.{item['name']}.summary",)),
                    )
                    for item in record["infrastructure"]
                ),
            )
        )
    if not record["nodes"]:
        # The engine's own state is gone and only Cairn's reports survived. Everything above
        # still holds — the census especially — but there is no graph to draw.
        return blocks
    if len(record["nodes"]) > GRAPH_NODE_CAP:
        blocks.append(
            Statement(
                "statement",
                (
                    Chrome("This run has"),
                    Fact(("run.node_count",)),
                    Chrome(
                        "engine nodes, past the point a drawn graph reads as one. The "
                        "engine draws the same graph live and zoomable; its link is in the "
                        "receipts."
                    ),
                ),
            )
        )
        return blocks
    nodes, edges = graph.layout(record["nodes"], record["edges"])
    blocks.append(
        Diagram(
            "diagram",
            nodes,
            edges,
            (
                Chrome("Every node the engine recorded:"),
                Fact(("run.node_count",)),
                Chrome("of them, in"),
                Fact(("run.edge_count",)),
                Chrome("dependencies."),
            ),
        )
    )
    return blocks


def _receipts(record: RunRecord) -> list[Block]:
    """What the run cost and what it left behind, per step and for the run.

    The resume command is a `Verbatim` block rather than a field, because every sink must
    agree not to wrap it, fold it into prose or decorate it. The working directory sits
    beside it: on a green run the wave's prune removes the worktree the command changes into,
    and a reader who can see why is better served than one left guessing. The report does not
    check whether it is still there — that would be a fact the model does not carry.
    """
    blocks: list[Block] = [
        Fields(
            "fields",
            "The run",
            (
                ("cost", Fact(("budget.cost_usd", "budget.notional"), RULE_MONEY)),
                ("turns", Fact(("budget.turns",))),
                ("steps with a price", Fact(("budget.priced_steps",))),
                ("steps without one", Fact(("budget.unpriced_steps",))),
                ("repository", Fact(("git.repository",))),
                ("parent branch", Fact(("git.parent_branch",))),
                ("commits", Fact(("git.commits",))),
                ("landed", Fact(("git.landed",))),
                ("did not land", Fact(("git.excluded",))),
                ("occasion", Fact(("run.occasion",))),
                ("earlier runs", Fact(("run.previous_runs",))),
                ("the engine's own view", Fact(("run.view_url",), RULE_LINK)),
                ("engine version", Fact(("run.engine_version",))),
            ),
        )
    ]
    for step in record["steps"]:
        key = f"step.{step['step_id']}"
        blocks.append(
            Fields(
                "fields",
                step["step_id"],
                (
                    (
                        "cost",
                        Fact((f"{key}.cost_usd", f"{key}.cost_is_notional"), RULE_MONEY),
                    ),
                    ("turns", Fact((f"{key}.turns",))),
                    ("model", Fact((f"{key}.model",))),
                    ("session", Fact((f"{key}.session",))),
                    ("transcript", Fact((f"{key}.transcript",))),
                    ("standard error", Fact((f"{key}.stderr_log",))),
                    ("branch", Fact((f"{key}.branch",))),
                    ("commit", Fact((f"{key}.commit",))),
                    ("changed", Fact((f"{key}.diffstat",))),
                    ("exit code", Fact((f"{key}.exit_code",))),
                    ("where a failure routes", Fact((f"{key}.position",))),
                    ("started", Fact((f"{key}.started_at",))),
                    ("finished", Fact((f"{key}.finished_at",))),
                ),
            )
        )
        if step["resume_command"] is not None:
            blocks.append(
                Verbatim(
                    "verbatim",
                    f"open {step['step_id']}'s session",
                    Fact((f"{key}.resume_command",)),
                )
            )
    return blocks



__all__ = ["document"]
