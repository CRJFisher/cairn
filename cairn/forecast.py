"""What a step like this one has cost here before, and whether the queue fits in what is left.

A queue is bounded by the allowance behind it, and a person learning that the week ran out
from a failed run learns it too late to choose a model, an order or a day. This module
answers the question before the run: from this repository's own records, what did steps of
this kind and model cost each window, and what does the queue ahead add up to.

**It estimates from measurements and nothing else.** A step's record carries a window delta
only where two measurements of one window bracketed it ([headroom.Steward.consumption]), so
every figure here rests on recorded differences — never on a token count converted into a
share of a week, and never on one reading extrapolated. Where no record has such a delta,
the honest answer is that there is nothing to estimate from, and that is what it says.

**It is a sentence, never a refusal.** Nothing here decides anything; `decide` holds on the
figure this produces ([headroom.decide]), and what a person does with the sentence is theirs
(principle 3). The median is used rather than the mean, because one runaway session is the
observation a queue forecast must not be dominated by, and the number of records behind the
figure is stated so a reader can weigh it.
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, NamedTuple, cast

from cairn.headroom import Reading, fresh_windows, local_moment, percent, window_phrase
from cairn.layout import RECORD_FILE

# The subcommand an agent step's engine body invokes, and the two flags it names its kind and
# model in. Read off the generated definition, which is the only place the queue ahead of a
# run exists before the run does ([skill/explain.would_do]).
AGENT_VERBS: tuple[str, str] = ("agent", "run")
PROVIDER_FLAG = "--provider"
MODEL_FLAG = "--model"

NOTHING_TO_ESTIMATE_FROM = "no past runs to estimate from"


class QueueStep(NamedTuple):
    """One agent step of a queue, keyed the way past records are grouped."""

    kind: str
    model: str


class Estimate(NamedTuple):
    """What a queue is expected to cost one window, and what the figure rests on."""

    window: str
    fraction: float
    records: int
    steps: int


class Forecast(NamedTuple):
    """What the whole queue is expected to cost, window by window.

    `steps` is every agent step in the queue and `windows` carries only those a record could
    speak for, each naming how many of those steps it covers — so a queue half of whose steps
    have never run here is never presented as a whole-queue figure.
    """

    steps: int
    records: int
    windows: tuple[Estimate, ...]


def queue_of(subcommands: Iterable[Sequence[str]]) -> tuple[QueueStep, ...]:
    """The agent steps of one definition, in the order it declares them.

    A step whose body names no model is not one this can estimate: the emitter refuses an
    agent step without one ([docs/step-kinds.md]), so such a body is a hand-edited definition
    and is passed over rather than guessed at.
    """
    found: list[QueueStep] = []
    for argv in subcommands:
        words = list(argv)
        if tuple(words[:2]) != AGENT_VERBS:
            continue
        provider = _after(words, PROVIDER_FLAG)
        model = _after(words, MODEL_FLAG)
        if provider is None or model is None:
            continue
        found.append(QueueStep(kind=f"agent.{provider}", model=model))
    return tuple(found)


def _after(words: list[str], flag: str) -> str | None:
    try:
        index = words.index(flag)
    except ValueError:
        return None
    return words[index + 1] if index + 1 < len(words) else None


class Observation(NamedTuple):
    """One recorded step's measured cost of one window."""

    kind: str
    model: str
    window: str
    delta: float


History = dict[QueueStep, dict[str, list[float]]]


def observations(runs_root: Path) -> list[Observation]:
    """Every measured window delta this repository's records hold.

    Read from the records on disk rather than rebuilt from the engine's history: this is a
    question asked in the middle of a conversation, and anything under the runs root that is
    not a record Cairn wrote contributes nothing. A record that cannot be read costs its own
    observations and never the forecast.
    """
    found: list[Observation] = []
    if not runs_root.is_dir():
        return found
    for entry in sorted(runs_root.iterdir()):
        try:
            raw: Any = json.loads((entry / RECORD_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(raw, dict):
            continue
        steps: object = cast(dict[str, Any], raw).get("steps")
        if not isinstance(steps, list):
            continue
        for step in cast(list[Any], steps):
            if isinstance(step, dict):
                found.extend(_observations_of(cast(dict[str, Any], step).get("consumption")))
    return found


def _observations_of(consumption: object) -> list[Observation]:
    if not isinstance(consumption, dict):
        return []
    account = cast(dict[str, Any], consumption)
    kind = account.get("kind")
    model = account.get("model")
    windows: object = account.get("windows")
    if not isinstance(kind, str) or not isinstance(model, str) or not isinstance(windows, list):
        return []
    found: list[Observation] = []
    for entry in cast(list[Any], windows):
        if not isinstance(entry, dict):
            continue
        fields = cast(dict[str, Any], entry)
        name = fields.get("window")
        delta = fields.get("delta")
        if not isinstance(name, str) or isinstance(delta, bool):
            continue
        # A delta is only ever stated between two readings sharing a reset
        # ([headroom.Steward.consumption]), so a negative one is a reading corrected downward
        # rather than a window that rolled over: dropping it would leave a step measured twice
        # with nothing to estimate from ([45 A]).
        if isinstance(delta, (int, float)) and -1.0 <= delta <= 1.0:
            found.append(Observation(kind=kind, model=model, window=name, delta=float(delta)))
    return found


def history(runs_root: Path) -> History:
    """Every measured delta, grouped by the kind and model that spent it."""
    grouped: History = {}
    for seen in observations(runs_root):
        step = QueueStep(kind=seen.kind, model=seen.model)
        grouped.setdefault(step, {}).setdefault(seen.window, []).append(seen.delta)
    return grouped


def step_estimate(past: History, step: QueueStep) -> dict[str, Estimate]:
    """What one step of this kind and model has cost each window, as a median."""
    return {
        window: Estimate(
            window=window, fraction=statistics.median(deltas), records=len(deltas), steps=1
        )
        for window, deltas in past.get(step, {}).items()
        if deltas
    }


def pace(runs_root: Path, *, kind: str, model: str | None) -> dict[str, float]:
    """What admission holds one step against: a share of each window, or nothing at all.

    A step whose model the plan did not name opens no session this can estimate, and a kind
    nothing has recorded yields an empty map — which admits on the thresholds alone.
    """
    if model is None:
        return {}
    estimates = step_estimate(history(runs_root), QueueStep(kind=kind, model=model))
    return {window: found.fraction for window, found in estimates.items()}


def forecast(runs_root: Path, queue: Sequence[QueueStep]) -> Forecast:
    """What this queue has cost in past runs, window by window."""
    past = history(runs_root)
    summed: dict[str, list[Estimate]] = {}
    for step in queue:
        for window, found in step_estimate(past, step).items():
            summed.setdefault(window, []).append(found)
    windows = tuple(
        Estimate(
            window=window,
            fraction=sum(found.fraction for found in parts),
            records=sum(found.records for found in parts),
            steps=len(parts),
        )
        for window, parts in sorted(summed.items())
    )
    return Forecast(
        steps=len(queue),
        records=sum(estimate.records for estimate in windows),
        windows=windows,
    )


def lines(found: Forecast, reading: Reading, *, now: float) -> list[str]:
    """The forecast as the sentences `run start` and `explain workflow` both state.

    One line per window a record can speak for, each against the current reading of that
    window: the figure alone answers "what will this cost" and leaves "does it fit"
    unanswered, which is the question that was actually asked ([45 B]).
    """
    if not found.windows:
        return [NOTHING_TO_ESTIMATE_FROM]
    measured = fresh_windows(reading, now=now, model=None)
    said: list[str] = []
    for estimate in found.windows:
        covered = (
            f"this queue's {estimate.steps} agent step(s)"
            if estimate.steps == found.steps
            else f"{estimate.steps} of this queue's {found.steps} agent step(s)"
        )
        sentence = (
            f"{covered} have cost about {percent(estimate.fraction)} of "
            f"{window_phrase(estimate.window)} in past runs (from {estimate.records} record(s))"
        )
        said.append(f"{sentence}; {_remaining(measured.get(estimate.window))}")
    return said


def _remaining(window: object) -> str:
    """What is left of one window, said only where a measurement put a figure on it."""
    if window is None:
        return "the current reading carries no measurement of it"
    measured = cast(dict[str, Any], window)
    used = measured.get("used")
    if not isinstance(used, (int, float)) or isinstance(used, bool):
        return "the current reading carries no figure for it"
    left = f"{percent(1.0 - float(used))} remains"
    resets = measured.get("resets_at")
    if isinstance(resets, int) and not isinstance(resets, bool):
        return f"{left}, reopening {local_moment(float(resets))}"
    return left


__all__ = [
    "AGENT_VERBS",
    "NOTHING_TO_ESTIMATE_FROM",
    "Estimate",
    "Forecast",
    "History",
    "Observation",
    "QueueStep",
    "forecast",
    "history",
    "lines",
    "observations",
    "pace",
    "queue_of",
    "step_estimate",
]
