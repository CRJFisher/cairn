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

from cairn.headroom import AFTER_OUTAGE, READING_TTL
from cairn.providers import TOKEN_KINDS
from cairn.record.model import (
    AllowanceAccount,
    AllowanceHold,
    AllowanceWindow,
    Consumption,
    ConsumptionWindow,
    Headroom,
    ModelSpend,
    PendingResume,
    RunRecord,
    Where,
)
from cairn.record.vocabulary import PROVENANCE_ABSENT, STEP_OUTCOMES
from cairn.text import since

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


def _window(window: AllowanceWindow) -> str:
    """One window's measurement as one fact: how full, its state, when it reopens, whose word."""
    used = "usage not given" if window["used"] is None else f"{window['used'] * 100:.0f}% used"
    parts = [f"{window['window']} {used}"]
    if window["status"] is not None:
        parts.append(window["status"])
    if window["resets_at"] is not None:
        parts.append(f"reopens {window['resets_at']}")
    parts.append(f"read {window['read_at'] or 'at an unrecorded time'} from the {window['source'] or 'unknown source'}")
    return ", ".join(parts)


def _windows(windows: list[AllowanceWindow]) -> str:
    return "; ".join(_window(window) for window in windows) if windows else NONE


def _account(account: AllowanceAccount | None) -> str:
    """One subscription said so two of them can be told apart, and no further.

    The organisation id is the identity; the subscription type is what a person recognises
    an account by. The email address the account signs in with is in neither ([43 A]).
    """
    if account is None:
        return ABSENT
    kind = account["subscription_type"] or "subscription"
    return f"{kind}, organisation {account['org_id']}"


def _accounts(accounts: list[AllowanceAccount]) -> str:
    return "; ".join(_account(account) for account in accounts) if accounts else NONE


def _set_aside(names: list[str]) -> str:
    """Windows admission ignored, said as what they were rather than as a measurement.

    A window too old to rest on is never printed as a reading: a reader shown one would
    believe the step was admitted against a figure its admission never looked at ([42 B]).
    """
    if not names:
        return NONE
    return f"{', '.join(names)} — set aside, older than {round(READING_TTL / 60)} min"


TOKEN_LABELS: dict[str, str] = {
    "input": "input",
    "output": "output",
    "cache_read": "cache read",
    "cache_creation": "cache creation",
}


def _money(figure: float | None) -> str:
    return "cost not given" if figure is None else f"${figure:.2f} API-equivalent"


def _token_line(tokens: dict[str, int]) -> str:
    counted = [
        f"{tokens[kind]:,} {TOKEN_LABELS[kind]}" for kind in TOKEN_KINDS if kind in tokens
    ]
    return ", ".join(counted) + " tokens" if counted else "no token count given"


def _model_spend(spend: ModelSpend) -> str:
    return f"{spend['model']} {_money(spend['cost_usd'])}, {_token_line(spend['tokens'])}"


def _consumption(consumption: Consumption | None) -> str:
    """What was spent, in the provider's own account and nothing derived from it."""
    if consumption is None:
        return ABSENT
    models = (
        "; ".join(_model_spend(spend) for spend in consumption["by_model"])
        or "no model named"
    )
    return (
        f"{_money(consumption['cost_usd'])}; {_token_line(consumption['tokens'])}; "
        f"by model: {models}"
    )


def _consumption_window(window: ConsumptionWindow) -> str:
    """One window read before and after, and the distance only where there was one.

    A window measured once says so rather than showing a zero: the difference between "it
    moved by nothing" and "nothing measured it twice" is the whole of what makes a delta
    worth carrying ([45 A]).
    """
    before = "unmeasured" if window["before"] is None else f"{window['before'] * 100:.0f}%"
    after = "unmeasured" if window["after"] is None else f"{window['after'] * 100:.0f}%"
    moved = (
        "no delta between two measurements"
        if window["delta"] is None
        else f"{window['delta'] * 100:.0f}% of the window"
    )
    return f"{window['window']} {before} before, {after} after, {moved}"


def _consumption_windows(consumption: Consumption | None) -> str:
    if consumption is None:
        return ABSENT
    windows = consumption["windows"]
    return "; ".join(_consumption_window(window) for window in windows) if windows else NONE


def _hold(hold: AllowanceHold) -> str:
    held_on = "the model provider" if hold["after"] == AFTER_OUTAGE else hold["window"] or "the allowance"
    return (
        f"{held_on} from {hold['started'] or 'an unrecorded time'} "
        f"until {hold['until'] or 'an unrecorded time'}: {hold['why'] or 'no reason recorded'}"
    )


def _resume(resume: PendingResume | None) -> list[tuple[str, str]]:
    """The resume a held run armed for itself, as the facts a rendering may state.

    Always these five keys, present or not, because a run-level key that appeared only for
    the runs that have one would leave every other run's projection a different shape.
    """
    if resume is None:
        return [
            ("run.resume", ABSENT),
            ("run.resume_at", ABSENT),
            ("run.resume_window", ABSENT),
            ("run.resume_reason", ABSENT),
            ("run.resume_run", ABSENT),
        ]
    return [
        ("run.resume", _value(resume["state"])),
        ("run.resume_at", _value(resume["at"])),
        ("run.resume_window", _value(resume["window"])),
        ("run.resume_reason", _value(resume["reason"])),
        ("run.resume_run", _value(resume["started_run"])),
    ]


def _where(where: Where | None) -> str:
    """Where one running session is, as the one line every rendering states.

    The age is always in it, because it is the half that does not depend on the events
    being legible. A transcript that held nothing readable says so and keeps the age,
    rather than leaving a reader to read an absent line as a quiet run ([20 A]).
    """
    if where is None:
        return ABSENT
    age = f"last event {since(where['last_event_age_seconds'])} ago"
    turns = where["turns"]
    if turns is None:
        return f"nothing readable in its transcript, {age}"
    action = where["action"]
    subject = where["object"]
    if action is None:
        did = "no tool call yet"
    else:
        did = f"last did {action}" + (f" {subject}" if subject is not None else "")
    return f"turn {turns}, {did}, {age}"


def _headroom(key: str, headroom: Headroom | None) -> list[tuple[str, str]]:
    """An agent step's dealings with the allowance, each one fact a rendering can state."""
    if headroom is None:
        return [
            (f"{key}.allowance", ABSENT),
            (f"{key}.allowance_reason", ABSENT),
            (f"{key}.account", ABSENT),
            (f"{key}.allowance_reading", ABSENT),
            (f"{key}.allowance_set_aside", ABSENT),
            (f"{key}.holds", ABSENT),
            (f"{key}.resumes", ABSENT),
            (f"{key}.held_window", ABSENT),
            (f"{key}.held_until", ABSENT),
            (f"{key}.holding", ABSENT),
        ]
    holding = headroom["holding"]
    return [
        (f"{key}.allowance", _value(headroom["admission"])),
        (f"{key}.allowance_reason", _value(headroom["reason"])),
        (f"{key}.account", _account(headroom["account"])),
        (f"{key}.allowance_reading", _windows(headroom["reading"])),
        (f"{key}.allowance_set_aside", _set_aside(headroom["set_aside"])),
        (
            f"{key}.holds",
            "; ".join(_hold(hold) for hold in headroom["holds"]) if headroom["holds"] else NONE,
        ),
        (f"{key}.resumes", _value(headroom["resumes"])),
        (f"{key}.held_window", _value(headroom["held_window"])),
        (f"{key}.held_until", _value(headroom["held_until"])),
        (f"{key}.holding", ABSENT if holding is None else _hold(holding)),
    ]


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
        ("run.attribution", _value(record["trigger"]["attribution"])),
        ("run.started_at", _value(record["started_at"])),
        ("run.finished_at", _value(record["finished_at"])),
        ("run.occasion", _value(record["lineage"]["occasion"])),
        ("run.previous_runs", _list(record["lineage"]["previous_runs"])),
        ("run.step_count", _value(len(record["steps"]))),
        ("run.node_count", _value(len(record["nodes"]))),
        ("run.edge_count", _value(len(record["edges"]))),
        ("run.wave_count", _value(len(record["waves"]))),
        ("run.attention_count", _value(len(record["attention"]))),
        # How much of this run's own evidence the record refused. One count rather than the
        # refusals themselves: each one is an attention item carrying its subject, its
        # fault and what it refused, and projecting both would be the same fact twice.
        ("run.integrity_count", _value(len(record["integrity"]))),
        ("run.next_action", _value(record["next_action"]["action"])),
        ("run.next_subject", _value(record["next_action"]["subject"])),
        ("run.next_command", _value(record["next_action"]["command"])),
        ("run.view_url", _value(record["view_url"])),
        ("run.allowance", _windows(record["allowance"])),
        ("run.consumption", _consumption(record["consumption"])),
        ("run.consumption_windows", _consumption_windows(record["consumption"])),
        ("run.accounts", _accounts(record["accounts"])),
        ("run.account_in_use", _account(record["account_in_use"])),
        *_resume(record["resume"]),
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
        remedy = step["remedy"]
        facts.extend(
            [
                (f"{key}.title", _value(step["title"])),
                (f"{key}.slug", _value(step["slug"])),
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
                (f"{key}.turns", _value(step["turns"])),
                # Where a running session is, which is the one fact about a step in flight
                # that is not in the engine's own status ([20 A]).
                (f"{key}.where", _where(step["where"])),
                (f"{key}.model", _value(step["model"])),
                (f"{key}.session", _value(step["session_id"])),
                (f"{key}.transcript", _value(step["transcript"])),
                (f"{key}.stderr_log", _value(step["stderr_log"])),
                (f"{key}.resume_command", _value(step["resume_command"])),
                (f"{key}.started_at", _value(step["started_at"])),
                (f"{key}.finished_at", _value(step["finished_at"])),
                (f"{key}.exit_code", _value(step["exit_code"])),
                (f"{key}.assertion_exit", _value(step["assertion_exit"])),
                (f"{key}.assertion_source", _value(step["assertion_source"])),
                (f"{key}.assertion_backed_by", _value(step["assertion_backed_by"])),
                (f"{key}.timeout_seconds", _value(step["timeout_seconds"])),
                (f"{key}.elapsed_seconds", _value(step["elapsed_seconds"])),
                (f"{key}.assertion_stdout", _value(step["assertion_stdout"])),
                (f"{key}.assertion_stderr", _value(step["assertion_stderr"])),
                (
                    f"{key}.remedy",
                    ABSENT if remedy is None else _value(remedy["status"]),
                ),
                (
                    f"{key}.remedy_said",
                    ABSENT if remedy is None else _value(remedy["said"]),
                ),
                (
                    f"{key}.remedy_first_exit",
                    ABSENT if remedy is None else _value(remedy["first_exit"]),
                ),
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
                (f"{key}.left_uncommitted", _list(step["left_uncommitted"])),
                (f"{key}.follow_up_work", _list(step["follow_up_work"])),
                (f"{key}.consumption", _consumption(step["consumption"])),
                (f"{key}.consumption_windows", _consumption_windows(step["consumption"])),
                *_headroom(key, step["headroom"]),
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
