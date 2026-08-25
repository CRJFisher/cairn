"""The canonical-facts projection: every fact a rendering may state, keyed and ordered.

[14]'s renderers read this alongside the record, and it is the drift oracle between them:
where two renderings disagree, both are compared against this list rather than against each
other. So it is total — a fact no key names is a fact no rendering may state — and every
value is a string, because containment is what an oracle test can actually assert.

A pair list rather than a mapping, because the ordering is part of the contract and a JSON
object's key order is incidental in some readers. An absent value spells `absent` rather
than an empty string, so a renderer that printed a zero disagrees with the oracle instead of
agreeing with it quietly.
"""

from __future__ import annotations

from cairn.record.model import RunRecord
from cairn.record.vocabulary import PROVENANCE_ABSENT, STEP_OUTCOMES

ABSENT = PROVENANCE_ABSENT
NONE = "none"


def _value(value: object) -> str:
    # An empty string is an absence wearing a value's clothes, and this projection's whole
    # posture is that a renderer which printed nothing should disagree with the oracle
    # rather than agree with it quietly.
    if value is None or value == "":
        return ABSENT
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _list(values: list[str]) -> str:
    return ", ".join(values) if values else NONE


def canonical_facts(record: RunRecord) -> list[tuple[str, str]]:
    """Every fact of one run, in the one order every renderer conforms to."""
    facts: list[tuple[str, str]] = [
        ("run.id", _value(record["run_id"])),
        ("run.plan", _value(record["plan"])),
        ("run.verdict", _value(record["verdict"])),
        ("run.exit_code", _value(record["exit_code"])),
        ("run.engine_status", _value(record["engine_run_status_name"])),
        ("run.engine_contradicted", _value(record["engine_contradicted"])),
        ("run.engine_version", _value(record["engine_version"])),
        ("run.attempts", _value(record["attempts"])),
        ("run.owner_alive", _value(record["owner_alive"])),
        ("run.trigger", _value(record["trigger"]["kind"])),
        ("run.actor", _value(record["trigger"]["actor"])),
        ("run.started_by_cairn", _value(record["trigger"]["started_by_cairn"])),
        ("run.started_at", _value(record["started_at"])),
        ("run.finished_at", _value(record["finished_at"])),
        ("run.occasion", _value(record["lineage"]["occasion"])),
        ("run.previous_runs", _list(record["lineage"]["previous_runs"])),
        ("run.step_count", _value(len(record["steps"]))),
        ("run.node_count", _value(len(record["nodes"]))),
        ("run.edge_count", _value(len(record["edges"]))),
        ("run.wave_count", _value(len(record["waves"]))),
        ("run.attention_count", _value(len(record["attention"]))),
        ("run.next_action", _value(record["next_action"]["action"])),
        ("run.next_subject", _value(record["next_action"]["subject"])),
        ("run.next_command", _value(record["next_action"]["command"])),
        ("run.view_url", _value(record["view_url"])),
        ("budget.cost_usd", _value(record["budget"]["cost_usd"])),
        ("budget.notional", _value(record["budget"]["notional"])),
        ("budget.turns", _value(record["budget"]["turns"])),
        ("budget.priced_steps", _value(record["budget"]["priced_steps"])),
        ("budget.unpriced_steps", _value(record["budget"]["unpriced_steps"])),
        ("git.repository", _value(record["git"]["repository"])),
        ("git.parent_branch", _value(record["git"]["parent_branch"])),
        ("git.commits", _list(record["git"]["commits"])),
        ("git.landed", _list(record["git"]["landed"])),
        ("git.excluded", _list(record["git"]["excluded"])),
    ]
    # One key per outcome, always, zero where none. A count a surface would otherwise work
    # out for itself is the arithmetic that turns a renderer into a second opinion — and
    # "N steps skipped" is exactly the sentence a no-op run is unreadable without.
    outcomes = [step["outcome"] for step in record["steps"]]
    facts.extend(
        (f"run.steps.{outcome}", _value(outcomes.count(outcome)))
        for outcome in STEP_OUTCOMES
    )
    for step in record["steps"]:
        key = f"step.{step['step_id']}"
        freshness = step["freshness"]
        diffstat = step["diffstat"]
        divergence = step["divergence"]
        facts.extend(
            [
                (f"{key}.outcome", _value(step["outcome"])),
                (f"{key}.overlays", _list(step["overlays"])),
                (f"{key}.cause", _value(step["cause"])),
                (f"{key}.position", _value(step["position"])),
                (f"{key}.asked", _value(step["asked"])),
                (f"{key}.said", _value(step["said"])),
                # Two accounts of one step, projected apart so neither can be rendered as
                # the truth by a surface that only carried one of them.
                (
                    f"{key}.divergence_reported",
                    ABSENT if divergence is None else _value(divergence["reported"]),
                ),
                (
                    f"{key}.divergence_asserted",
                    ABSENT if divergence is None else _value(divergence["asserted"]),
                ),
                (f"{key}.cost_usd", _value(step["cost_usd"])),
                (f"{key}.cost_is_notional", _value(step["cost_is_notional"])),
                (f"{key}.turns", _value(step["turns"])),
                (f"{key}.model", _value(step["model"])),
                (f"{key}.session", _value(step["session_id"])),
                (f"{key}.transcript", _value(step["transcript"])),
                (f"{key}.stderr_log", _value(step["stderr_log"])),
                (f"{key}.resume_command", _value(step["resume_command"])),
                (f"{key}.started_at", _value(step["started_at"])),
                (f"{key}.finished_at", _value(step["finished_at"])),
                (f"{key}.exit_code", _value(step["exit_code"])),
                (f"{key}.branch", _value(step["branch"])),
                (f"{key}.commit", _value(step["commit"])),
                (
                    f"{key}.diffstat",
                    ABSENT
                    if diffstat is None
                    else (
                        f"{diffstat['files']} files "
                        f"+{diffstat['insertions']} -{diffstat['deletions']}"
                    ),
                ),
                (
                    f"{key}.scope",
                    ABSENT if freshness is None else _value(freshness["recorded_scope"]),
                ),
                (f"{key}.key", ABSENT if freshness is None else _value(freshness["recorded_key"])),
                (f"{key}.completed_by", _value(step["completed_by_run"])),
                (f"{key}.follow_up_work", _list(step["follow_up_work"])),
            ]
        )
    for index, item in enumerate(record["attention"]):
        facts.append((f"attention.{index}.kind", _value(item["kind"])))
        facts.append((f"attention.{index}.subject", _value(item["subject"])))
        facts.append((f"attention.{index}.summary", _value(item["summary"])))
        facts.append((f"attention.{index}.cause", _value(item["cause"])))
    for item in record["infrastructure"]:
        name = f"infrastructure.{item['name']}"
        facts.append((f"{name}.outcome", _value(item["outcome"])))
        facts.append((f"{name}.cause", _value(item["cause"])))
        facts.append((f"{name}.summary", _value(item["summary"])))
    # Keyed on position rather than on the wave's own number: a join report that recorded no
    # census reads as wave 0, so two of them would collide and `as_mapping` would quietly
    # drop one wave's facts from the very projection the renderings are checked against.
    for index, census in enumerate(record["waves"]):
        name = f"wave.{index}"
        facts.append((f"{name}.number", _value(census["wave"])))
        facts.append((f"{name}.into", _value(census["into"])))
        facts.append((f"{name}.arrived", _list(census["arrived"])))
        facts.append((f"{name}.settled", _list(census["settled"])))
        facts.append(
            (f"{name}.excluded", _list([entry["branch"] for entry in census["excluded"]]))
        )
        for entry in census["excluded"]:
            branch = f"{name}.excluded.{entry['branch']}"
            facts.append((f"{branch}.cause", _value(entry["cause"])))
            facts.append((f"{branch}.summary", _value(entry["summary"])))
    return facts


def as_mapping(record: RunRecord) -> dict[str, str]:
    """The same facts, for a caller asking about one of them rather than reading all."""
    return dict(canonical_facts(record))


__all__ = ["ABSENT", "NONE", "as_mapping", "canonical_facts"]
