"""The subscription's allowance: one shared reading of it, and the holds it decides.

The subscription behind a metered provider meters work in a 5-hour window and a weekly one.
A queue of agent steps that runs into either would fail every session it starts there, so a
step **holds** instead
([38]): before its session, when the shared reading says a window is closed or nearly so,
and after it, when the session meets the limit mid-turn — and then resumes that session once
the window reopens. A limit is a pause in the run, never the end of it.

**One reading, three feeders.** Every step and every session reads and writes one file under
the runs root, so concurrent steps share one fact and no step measures on its own account.
The feeders run cheapest first. The stream: every session already emits a `rate_limit_event`,
free, and it is the only feeder that sees `rejected`. The usage endpoint, off unless the owner
sets `CAIRN_HEADROOM_USAGE_ENDPOINT=1`, because it reads a credential Cairn did not create.
And a probe: one settings-free haiku turn read for its event and discarded, spent only when
nothing fresher is known.

**Unknown admits.** A reading nobody could take lets the session start, and the step's report
says why. The backstop — hold and resume a session that met the limit — makes a wrong admit a
short wait, so a guard that blocked on its own blindness would stall a queue for a fault in
the instrument. That is the inverse of the verify gate, and deliberately so: nothing durable
depends on this check having run.

**Cairn never computes a reset.** A hold lasts until the moment a measurement reported, and
the step then measures again rather than believing the clock. A window that reopens late or
moves its reset is a few more minutes of hold, never a session started into a closed window.
"""

from __future__ import annotations

import json
import math
import os
import time
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any, NamedTuple, TypedDict, cast

from cairn.core import (
    EXIT_RATE_LIMITED,
    CairnError,
    CommandResult,
    RuntimeContext,
    write_json,
)
from cairn.layout import HEADROOM_ENDPOINT_ENV, headroom_directory, holds_directory
from cairn.locks import exclusive_lock
from cairn.plan.schema import HANG_GUARD, HOLD_THRESHOLDS, QUOTA_WAIT
from cairn.protocol import RESUME_AFTER_LIMIT
from cairn.providers import (
    METERED_PROVIDERS,
    RATE_LIMITED,
    USAGE_TIMEOUT,
    EndpointRefused,
    Unmeasured,
    iso_moment,
    read_usage,
    reset_epoch,
    run_probe,
    run_provider,
)
from cairn.verify import QUOTA_HELD

WINDOW_FIVE_HOUR = "five_hour"
WINDOW_SEVEN_DAY = "seven_day"
# A weekly window that meters one model family only, and the family it meters. A session on
# another model is not held by it.
MODEL_WINDOWS: dict[str, str] = {"seven_day_opus": "opus", "seven_day_sonnet": "sonnet"}
WINDOW_PHRASES: dict[str, str] = {
    WINDOW_FIVE_HOUR: "the 5-hour allowance",
    WINDOW_SEVEN_DAY: "the weekly allowance",
    "seven_day_opus": "the weekly Opus allowance",
    "seven_day_sonnet": "the weekly Sonnet allowance",
}

STATUS_ALLOWED = "allowed"
STATUS_WARNING = "allowed_warning"
STATUS_REJECTED = "rejected"

SOURCE_STREAM = "stream"
SOURCE_ENDPOINT = "endpoint"
SOURCE_PROBE = "probe"
SOURCE_LIMIT = "limit"

# What admission decided for one session. `inert` is a session funded by an API key, which
# has no subscription windows to hold on.
ADMITTED = "admitted"
WARNED = "warned"
UNKNOWN = "unknown"
INERT = "inert"
HELD = "held"
ADMISSIONS: tuple[str, ...] = (ADMITTED, WARNED, UNKNOWN, INERT, HELD)

# Which moment a hold answered: the one before a session started, or a limit a session met.
BEFORE_SESSION = "admission"
AFTER_LIMIT = "limit"

# A reading older than this is unknown rather than stale-but-trusted.
READING_TTL = 600.0
# How long after a reported reset the step measures again. A window reported to reopen at T
# is measured after T, not at it, so the measurement is not of the window still closing.
RESET_SETTLE = 60.0
# The waits between measurements of a window still closed after the moment it was reported
# to reopen, capped at the last.
RECHECK_BACKOFF: tuple[float, ...] = (300.0, 600.0, 1200.0)

READING_FILE = "reading.json"
READING_LOCK = "reading.lock"
# Held by whichever step is refreshing a stale reading, so concurrent steps that all find it
# stale cause one probe between them; the rest re-read what it wrote.
REFRESH_LOCK = "refresh.lock"
READING_LOCK_WAIT = 10.0

PROBE_TIMEOUT = 120.0
# Below this much budget a probe is not worth starting: it would be stopped before the event.
PROBE_MINIMUM = 15.0

# The endpoint answers 429 to a poller, so it is asked at most this often, and backed off
# along the second figure after each refusal.
ENDPOINT_CACHE = 180.0
ENDPOINT_BACKOFF: tuple[float, ...] = (180.0, 360.0, 720.0, 900.0)

REFRESH_LOCK_WAIT = PROBE_TIMEOUT + USAGE_TIMEOUT + 30.0


class Window(TypedDict):
    """What one measurement said about one window, and when and how it was taken."""

    used: float | None
    status: str | None
    resets_at: int | None
    source: str
    read_at: float


class Reading(TypedDict):
    """The shared reading: every window last measured, and how the account is funded."""

    windows: dict[str, Window]
    api_key_source: str | None
    funding_read_at: float | None
    endpoint_called_at: float | None
    endpoint_failures: int


def empty_reading() -> Reading:
    return Reading(
        windows={},
        api_key_source=None,
        funding_read_at=None,
        endpoint_called_at=None,
        endpoint_failures=0,
    )


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _window_from(raw: object) -> Window | None:
    if not isinstance(raw, dict):
        return None
    entry = cast(dict[str, Any], raw)
    read_at = _number(entry.get("read_at"))
    source = _text(entry.get("source"))
    if read_at is None or source is None:
        return None
    resets: object = entry.get("resets_at")
    return Window(
        used=_number(entry.get("used")),
        status=_text(entry.get("status")),
        resets_at=resets if isinstance(resets, int) and not isinstance(resets, bool) else None,
        source=source,
        read_at=read_at,
    )


def reading_from(raw: object) -> Reading:
    """A stored reading, with anything unreadable dropped rather than trusted."""
    reading = empty_reading()
    if not isinstance(raw, dict):
        return reading
    stored = cast(dict[str, Any], raw)
    windows: object = stored.get("windows")
    if isinstance(windows, dict):
        for name, entry in cast(dict[str, Any], windows).items():
            window = _window_from(entry)
            if window is not None:
                reading["windows"][name] = window
    reading["api_key_source"] = _text(stored.get("api_key_source"))
    reading["funding_read_at"] = _number(stored.get("funding_read_at"))
    reading["endpoint_called_at"] = _number(stored.get("endpoint_called_at"))
    failures = stored.get("endpoint_failures")
    reading["endpoint_failures"] = (
        failures if isinstance(failures, int) and not isinstance(failures, bool) else 0
    )
    return reading


def window_of(
    event: dict[str, Any], *, source: str, read_at: float
) -> tuple[str, Window] | None:
    """The window one `rate_limit_event` speaks for, as a measurement.

    Measured on the provider's CLI 2.1.220: one window per event, named by `rateLimitType`, with
    `utilization` a 0–1 fraction where the event carries one at all.
    """
    if event.get("type") != "rate_limit_event":
        return None
    info: object = event.get("rate_limit_info")
    if not isinstance(info, dict):
        return None
    fields = cast(dict[str, Any], info)
    name = _text(fields.get("rateLimitType"))
    if name is None:
        return None
    used = _number(fields.get("utilization"))
    return name, Window(
        used=used if used is not None and 0.0 <= used <= 1.0 else None,
        status=_text(fields.get("status")),
        resets_at=reset_epoch(event),
        source=source,
        read_at=read_at,
    )


def _merged(previous: Window | None, new: Window) -> Window:
    """A new measurement of a window, keeping the last known `used` within the same window.

    An `allowed` event usually carries no utilization. Usage inside one window only rises, so
    the figure an earlier measurement of the *same* window took is still a floor under it —
    dropping it would forget that the window was nearly full the moment a quieter event
    arrived.
    """
    if (
        new["used"] is None
        and previous is not None
        and previous["used"] is not None
        and previous["resets_at"] == new["resets_at"]
    ):
        carried = Window(**new)
        carried["used"] = previous["used"]
        return carried
    return new


class Gauge:
    """The shared reading under one runs root, read and replaced whole under a lock."""

    def __init__(self, directory: Path, *, clock: Callable[[], float] = time.time) -> None:
        self.directory = directory
        self.clock = clock

    @property
    def path(self) -> Path:
        return self.directory / READING_FILE

    def read(self) -> Reading:
        try:
            raw: Any = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return empty_reading()
        return reading_from(raw)

    @contextmanager
    def _updating(self) -> Generator[Reading]:
        with exclusive_lock(
            self.directory / READING_LOCK,
            wait_seconds=READING_LOCK_WAIT,
            cause="headroom_unavailable",
            subject="the subscription reading",
        ):
            reading = self.read()
            yield reading
            write_json(self.path, cast(dict[str, Any], reading))

    def observe(self, message: dict[str, Any], *, source: str = SOURCE_STREAM) -> None:
        """Fold one stream message into the reading: a window's event, or how it is funded."""
        now = self.clock()
        if message.get("type") == "system":
            funding = _text(message.get("apiKeySource"))
            if funding is None:
                return
            with self._updating() as reading:
                reading["api_key_source"] = funding
                reading["funding_read_at"] = now
            return
        found = window_of(message, source=source, read_at=now)
        if found is None:
            return
        name, window = found
        with self._updating() as reading:
            reading["windows"][name] = _merged(reading["windows"].get(name), window)

    def record(self, windows: dict[str, Window]) -> None:
        with self._updating() as reading:
            for name, window in windows.items():
                reading["windows"][name] = _merged(reading["windows"].get(name), window)

    def endpoint_answered(self, *, at: float, refused: bool) -> None:
        with self._updating() as reading:
            reading["endpoint_called_at"] = at
            reading["endpoint_failures"] = reading["endpoint_failures"] + 1 if refused else 0

    def endpoint_unanswered(self, *, at: float) -> None:
        """An ask that failed for any reason but a 429: not asked again inside the cache,
        and the 429 back-off it may be in is kept rather than reset."""
        with self._updating() as reading:
            reading["endpoint_called_at"] = at


def _applies(name: str, model: str | None) -> bool:
    family = MODEL_WINDOWS.get(name)
    return family is None or (model is not None and family in model.lower())


def fresh_windows(
    reading: Reading, *, now: float, model: str | None, newer_than: float | None = None
) -> dict[str, Window]:
    """The windows a decision may rest on: measured recently, and not already reopened.

    A window whose reset has passed says nothing about the window now open, so it is void —
    except a rejection, which is the one state that may outlive its own reported reset and
    must then be measured again rather than assumed over.
    """
    found: dict[str, Window] = {}
    for name, window in reading["windows"].items():
        if not _applies(name, model):
            continue
        if window["read_at"] < now - READING_TTL:
            continue
        if newer_than is not None and window["read_at"] < newer_than:
            continue
        reopened = window["resets_at"] is not None and window["resets_at"] <= now
        if reopened and window["status"] != STATUS_REJECTED:
            continue
        found[name] = window
    return found


def funded_by_key(reading: Reading, *, now: float) -> bool:
    funding = reading["api_key_source"]
    read_at = reading["funding_read_at"]
    return (
        funding is not None
        and funding != "none"
        and read_at is not None
        and read_at >= now - READING_TTL
    )


class Decision(NamedTuple):
    """Whether a session may start, and where it may not, which window and until when."""

    admission: str
    reason: str | None
    window: str | None
    until: float | None
    used: float | None


def _percent(used: float) -> str:
    return f"{used * 100:.0f}%"


def window_phrase(name: str | None) -> str:
    if name is None:
        return "the allowance"
    return WINDOW_PHRASES.get(name, f"the {name} allowance")


def decide(
    reading: Reading,
    *,
    now: float,
    model: str | None,
    newer_than: float | None = None,
    thresholds: dict[str, float] = HOLD_THRESHOLDS,
) -> Decision:
    """Admission, read off one reading — never a reset the clock alone implies."""
    if funded_by_key(reading, now=now):
        return Decision(
            INERT,
            f"the session is funded by an API key ({reading['api_key_source']}), which has "
            "no subscription windows",
            None,
            None,
            None,
        )
    windows = fresh_windows(reading, now=now, model=model, newer_than=newer_than)
    if not windows:
        return Decision(UNKNOWN, "no window was measured recently enough to rest on", None, None, None)
    blocking: list[Decision] = []
    for name, window in windows.items():
        threshold = thresholds.get(name, max(thresholds.values()))
        used = window["used"]
        if window["status"] == STATUS_REJECTED:
            why = f"{window_phrase(name)} is spent"
        elif used is not None and used >= threshold:
            why = f"{window_phrase(name)} is at {_percent(used)}, past the {_percent(threshold)} hold threshold"
        else:
            continue
        resets = window["resets_at"]
        blocking.append(
            Decision(HELD, why, name, float(resets) if resets is not None and resets > now else None, used)
        )
    if blocking:
        # Every closed window has to reopen, so the hold lasts until the last of them.
        return max(blocking, key=lambda held: held.until if held.until is not None else now)
    warned = sorted(name for name, window in windows.items() if window["status"] == STATUS_WARNING)
    if warned:
        return Decision(
            WARNED,
            " and ".join(window_phrase(name) for name in warned) + " warned it is running low",
            warned[0],
            None,
            windows[warned[0]]["used"],
        )
    return Decision(ADMITTED, None, None, None, None)


# --- the feeders that are asked rather than overheard ---------------------------------


Prober = Callable[[float], list[dict[str, Any]]]
EndpointReader = Callable[[float], dict[str, Window]]


class Instruments(NamedTuple):
    probe: Prober
    endpoint: EndpointReader | None


def _epoch(moment: object) -> int | None:
    if not isinstance(moment, str) or not moment:
        return None
    try:
        return int(datetime.fromisoformat(moment.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def usage_windows(body: object, *, read_at: float) -> dict[str, Window]:
    """The endpoint's answer as measurements: `utilization` is 0–100 there, a fraction here."""
    if not isinstance(body, dict):
        raise Unmeasured("the usage endpoint answered something other than an object")
    windows: dict[str, Window] = {}
    for name, entry in cast(dict[str, Any], body).items():
        if not isinstance(entry, dict) or name == "extra_usage":
            continue
        fields = cast(dict[str, Any], entry)
        utilization = _number(fields.get("utilization"))
        if utilization is None:
            continue
        windows[name] = Window(
            used=max(0.0, utilization) / 100,
            status=None,
            resets_at=_epoch(fields.get("resets_at")),
            source=SOURCE_ENDPOINT,
            read_at=read_at,
        )
    return windows


def read_usage_endpoint(now: float) -> dict[str, Window]:
    """Both windows' usage, from the endpoint the provider's own `/usage` reads."""
    return usage_windows(read_usage(now), read_at=now)


def endpoint_enabled(environment: Mapping[str, str] | None = None) -> bool:
    values = os.environ if environment is None else environment
    return values.get(HEADROOM_ENDPOINT_ENV) == "1"


def default_instruments() -> Instruments:
    return Instruments(
        probe=run_probe, endpoint=read_usage_endpoint if endpoint_enabled() else None
    )


def _endpoint_due(reading: Reading, *, now: float) -> bool:
    called = reading["endpoint_called_at"]
    if called is None:
        return True
    failures = reading["endpoint_failures"]
    wait = (
        ENDPOINT_BACKOFF[min(failures - 1, len(ENDPOINT_BACKOFF) - 1)]
        if failures
        else ENDPOINT_CACHE
    )
    return now >= called + wait


def _settled(gauge: Gauge, *, model: str | None, newer_than: float | None) -> bool:
    reading = gauge.read()
    now = gauge.clock()
    return funded_by_key(reading, now=now) or bool(
        fresh_windows(reading, now=now, model=model, newer_than=newer_than)
    )


def measure(
    gauge: Gauge,
    *,
    model: str | None,
    newer_than: float | None,
    budget_until: float,
    instruments: Instruments,
) -> list[str]:
    """Bring the shared reading up to date if nothing fresh is known, and say what failed.

    One refresher at a time: a step finding the reading stale takes the refresh lock, and
    re-reads under it before measuring, so concurrent steps that all woke to a stale reading
    cause one measurement between them.
    """
    if _settled(gauge, model=model, newer_than=newer_than):
        return []
    notes: list[str] = []
    try:
        with exclusive_lock(
            gauge.directory / REFRESH_LOCK,
            wait_seconds=REFRESH_LOCK_WAIT,
            cause="headroom_unavailable",
            subject="the subscription reading's refresh",
        ):
            if _settled(gauge, model=model, newer_than=newer_than):
                return []
            now = gauge.clock()
            if instruments.endpoint is None:
                notes.append(f"the usage endpoint is off ({HEADROOM_ENDPOINT_ENV} is not 1)")
            elif not _endpoint_due(gauge.read(), now=now):
                notes.append("the usage endpoint was asked too recently to ask again")
            else:
                try:
                    windows = instruments.endpoint(now)
                except EndpointRefused as refused:
                    gauge.endpoint_answered(at=now, refused=True)
                    notes.append(str(refused))
                except Unmeasured as unmeasured:
                    gauge.endpoint_unanswered(at=now)
                    notes.append(str(unmeasured))
                else:
                    gauge.endpoint_answered(at=now, refused=False)
                    gauge.record(windows)
            if _settled(gauge, model=model, newer_than=newer_than):
                return []
            timeout = min(PROBE_TIMEOUT, budget_until - gauge.clock())
            if timeout < PROBE_MINIMUM:
                notes.append("too little time was left to probe")
                return notes
            try:
                messages = instruments.probe(timeout)
            except Unmeasured as unmeasured:
                notes.append(str(unmeasured))
                return notes
            for message in messages:
                gauge.observe(message, source=SOURCE_PROBE)
            if not any(message.get("type") == "rate_limit_event" for message in messages):
                notes.append("the probe's stream carried no rate_limit_event")
    # A reading that could not be locked or written is an unknown one, and unknown admits: a
    # fault in the instrument never becomes a failed step.
    except (CairnError, OSError) as busy:
        notes.append(str(busy))
    return notes


# --- the step's own account, and the hold ---------------------------------------------


class Hold(TypedDict):
    """One stretch a step spent waiting at the allowance."""

    window: str | None
    started: str
    until: str
    why: str
    after: str


class Resume(TypedDict):
    """One resume of a session the limit stopped, and what it came to."""

    session_id: str
    at: str
    outcome: str


Session = Callable[[float], CommandResult]
Resumer = Callable[[str, float], CommandResult]
Announcer = Callable[[Hold | None], None]


def announcer(context: RuntimeContext) -> Announcer:
    """Say, beside the run's reports, that this step is holding — and take it back after.

    A report is written when a step ends, so a step waiting hours for a window would read as
    stalled to anyone looking at the run. Best effort: the announcement is for a reader, and
    a step that could not write it holds just the same.
    """
    path = holds_directory(context.runs_root, context.run_id) / f"{context.step_id}.json"

    def announce(hold: Hold | None) -> None:
        try:
            if hold is None:
                path.unlink(missing_ok=True)
            else:
                write_json(path, {"run_id": context.run_id, **hold})
        except OSError:
            pass

    return announce


def local_moment(epoch: float) -> str:
    """A moment as a person reads it on the machine the step ran on."""
    return datetime.fromtimestamp(epoch).astimezone().strftime("%a %d %b %H:%M %Z")


def _snapshot(reading: Reading, *, now: float, model: str | None) -> dict[str, Any]:
    """The reading admission rested on, with each window's age rather than its timestamp."""
    return {
        name: {
            "used": window["used"],
            "status": window["status"],
            "resets_at": None if window["resets_at"] is None else iso_moment(window["resets_at"]),
            "source": window["source"],
            "read_at": iso_moment(window["read_at"]),
            "age_seconds": round(max(0.0, now - window["read_at"]), 1),
        }
        for name, window in sorted(reading["windows"].items())
        if _applies(name, model)
    }


def _limit_window(result: CommandResult) -> tuple[str, int | None]:
    """Which window a session's limit was, and when it reopens, from its own events."""
    events: object = result.detail.get("rate_limits")
    found: list[dict[str, Any]] = [
        cast(dict[str, Any], event)
        for event in (cast(list[Any], events) if isinstance(events, list) else [])
        if isinstance(event, dict)
    ]
    rejected = [
        event
        for event in found
        if isinstance(event.get("rate_limit_info"), dict)
        and cast(dict[str, Any], event["rate_limit_info"]).get("status") == STATUS_REJECTED
    ]
    chosen = (rejected or found)[-1] if (rejected or found) else None
    if chosen is None:
        return WINDOW_FIVE_HOUR, None
    info = cast(dict[str, Any], chosen.get("rate_limit_info") or {})
    return _text(info.get("rateLimitType")) or WINDOW_FIVE_HOUR, reset_epoch(chosen)


def _joined(earlier: CommandResult, later: CommandResult) -> CommandResult:
    """One step's account across a session and its resumes: the later word, every turn."""
    turns = sum(
        int(_number(result.detail.get("turn_count")) or 0) for result in (earlier, later)
    )
    limits: list[Any] = [
        *cast(list[Any], earlier.detail.get("rate_limits") or []),
        *cast(list[Any], later.detail.get("rate_limits") or []),
    ]
    return later._replace(detail={**later.detail, "turn_count": turns, "rate_limits": limits})


class Steward:
    """One agent step's dealings with the allowance: admission, holds, resumes, account.

    Two budgets bound it, and together they are what the engine's bound on the step is
    built from. Work — time inside a session — never exceeds the hang guard across every
    session the step opens. Holding never exceeds the hold budget — `QUOTA_WAIT` for a plan
    step, nothing for a merge slot: a hold may end no later than the step's start plus that
    budget plus the work already done, so whatever is left of the guard still fits before
    the engine's kill.
    """

    def __init__(
        self,
        gauge: Gauge,
        *,
        model: str | None,
        instruments: Instruments,
        announce: Announcer,
        sleep: Callable[[float], None] = time.sleep,
        hold_budget: float = QUOTA_WAIT,
    ) -> None:
        self.gauge = gauge
        self.hold_budget = hold_budget
        self.model = model
        self.instruments = instruments
        self.announce = announce
        self.sleep = sleep
        self.started = gauge.clock()
        self.worked = 0.0
        self.admission: Decision | None = None
        self.reading: dict[str, Any] = {}
        self.notes: list[str] = []
        self.holds: list[Hold] = []
        self.resumes: list[Resume] = []

    def hold_ceiling(self) -> float:
        return self.started + self.hold_budget + self.worked

    def work_left(self) -> float:
        now = self.gauge.clock()
        return min(
            HANG_GUARD - self.worked, self.started + HANG_GUARD + self.hold_budget - now
        )

    def run(self, session: Session) -> CommandResult:
        began = self.gauge.clock()
        try:
            return session(self.work_left())
        finally:
            self.worked += self.gauge.clock() - began

    def _measured(self, *, newer_than: float | None, admitting: bool) -> Decision:
        """Measure if needed, and decide. Before the session, the decision is the step's
        admission: the last one taken is the one the session started on, or the one that
        stopped the step, and that is what its report says it was admitted on."""
        notes = measure(
            self.gauge,
            model=self.model,
            newer_than=newer_than,
            budget_until=self.hold_ceiling(),
            instruments=self.instruments,
        )
        now = self.gauge.clock()
        reading = self.gauge.read()
        decision = decide(reading, now=now, model=self.model, newer_than=newer_than)
        if admitting:
            self.admission = decision
            self.reading = _snapshot(reading, now=now, model=self.model)
            self.notes = notes
        return decision

    def wait_until_clear(self, after: str, *, closed: Decision | None = None) -> Decision | None:
        """Hold until a measurement admits. The decision that stopped the step, if it must.

        `closed` is a window already known shut — a limit the session just met — which is
        held on without measuring first. Every later pass measures, and only a reading taken
        after the hold's own end is believed.
        """
        decision = closed
        newer_than: float | None = None
        rechecks = 0
        while True:
            if decision is None:
                decision = self._measured(
                    newer_than=newer_than, admitting=after == BEFORE_SESSION
                )
            if decision.admission != HELD:
                return None
            now = self.gauge.clock()
            if decision.until is not None:
                until = decision.until + RESET_SETTLE
            else:
                until = now + RECHECK_BACKOFF[min(rechecks, len(RECHECK_BACKOFF) - 1)]
                rechecks += 1
            if until > self.hold_ceiling():
                return decision
            hold = Hold(
                window=decision.window,
                started=iso_moment(now),
                until=iso_moment(until),
                why=decision.reason or "",
                after=after,
            )
            self.holds.append(hold)
            self.announce(hold)
            try:
                self.sleep(max(0.0, until - self.gauge.clock()))
            finally:
                self.announce(None)
            newer_than = decision.until if decision.until is not None else until
            decision = None

    def limit_met(self, result: CommandResult) -> Decision:
        """The window a session's limit closed, written into the shared reading.

        Written even though the stream normally carries the rejection already, because a
        session that ended on `blocking_limit` without one would otherwise leave every other
        step admitting into the same closed window.
        """
        name, resets = _limit_window(result)
        now = self.gauge.clock()
        try:
            self.gauge.record(
                {
                    name: Window(
                        used=None,
                        status=STATUS_REJECTED,
                        resets_at=resets,
                        source=SOURCE_LIMIT,
                        read_at=now,
                    )
                }
            )
        except (CairnError, OSError):
            pass
        return Decision(
            HELD,
            f"the session met the limit of {window_phrase(name)}",
            name,
            float(resets) if resets is not None and resets > now else None,
            None,
        )

    def account(self, stopped: Decision | None = None) -> dict[str, Any]:
        admission = self.admission
        return {
            "admission": None if admission is None else admission.admission,
            "reason": "; ".join(
                part
                for part in (None if admission is None else admission.reason, *self.notes)
                if part
            )
            or None,
            "reading": self.reading,
            "holds": list(self.holds),
            "resumes": list(self.resumes),
            "held_window": None if stopped is None else stopped.window,
            "held_until": (
                None if stopped is None or stopped.until is None else iso_moment(stopped.until)
            ),
            "held_used": None if stopped is None else stopped.used,
        }

    def held(
        self,
        stopped: Decision,
        *,
        prior: CommandResult | None,
        why: str | None = None,
    ) -> CommandResult:
        """The step ends `quota_held`, naming the window and the moment it reopens."""
        allowance = window_phrase(stopped.window)
        state = (
            f"{allowance} at {_percent(stopped.used)}"
            if stopped.used is not None
            else f"{allowance} spent"
        )
        if why is not None:
            summary = f"{why} — {state}"
        elif stopped.until is not None:
            summary = f"held until {local_moment(stopped.until)} — {state}"
        else:
            summary = f"held at {allowance}, which had not reopened when the step could wait no longer"
        detail = {} if prior is None else dict(prior.detail)
        detail["headroom"] = self.account(stopped)
        return CommandResult(EXIT_RATE_LIMITED, "failed", summary, [], False, QUOTA_HELD, detail)


def within_allowance(
    first: Session,
    again: Resumer,
    *,
    gauge: Gauge,
    model: str | None,
    instruments: Instruments,
    announce: Announcer,
    sleep: Callable[[float], None] = time.sleep,
    hold_budget: float = QUOTA_WAIT,
) -> CommandResult:
    """Run one agent step's session inside the allowance, holding and resuming as it must.

    A session that met the limit is resumed by id once its window reopens. A resume that
    could not continue the session — refused, or failing before it reported — ends the step
    `quota_held` with the session's id, which is the last resort: the marker means a re-run
    skips everything that landed, and the record names the moment to run it.
    """
    steward = Steward(
        gauge,
        model=model,
        instruments=instruments,
        announce=announce,
        sleep=sleep,
        hold_budget=hold_budget,
    )
    stopped = steward.wait_until_clear(BEFORE_SESSION)
    if stopped is not None:
        return steward.held(stopped, prior=None)
    try:
        result = steward.run(first)
    except CairnError as failed:
        failed.detail = {**failed.detail, "headroom": steward.account()}
        raise
    # The limit is read off the latest session alone: the joined account carries every
    # earlier session's events, and a stale rejection would name the wrong window and moment.
    latest = result
    while latest.cause == RATE_LIMITED:
        closed = steward.limit_met(latest)
        session_id = _text(latest.detail.get("session_id"))
        stopped = steward.wait_until_clear(AFTER_LIMIT, closed=closed)
        if stopped is not None:
            return steward.held(stopped, prior=result)
        if session_id is None:
            return steward.held(closed, prior=result, why="the limited session left no id to resume")
        if steward.work_left() <= 0:
            return steward.held(closed, prior=result, why="the step's work bound was spent before the limit cleared")
        at = iso_moment(gauge.clock())
        try:
            resumed = steward.run(partial(again, session_id))
        except CairnError as refused:
            steward.resumes.append(Resume(session_id=session_id, at=at, outcome=refused.cause))
            return steward.held(closed, prior=result, why=f"the session could not be resumed: {refused}")
        steward.resumes.append(
            Resume(session_id=session_id, at=at, outcome=resumed.cause or resumed.status)
        )
        if resumed.cause == "provider_failed":
            return steward.held(closed, prior=_joined(result, resumed), why="the session could not be resumed")
        latest = resumed
        result = _joined(result, resumed)
    return result._replace(detail={**result.detail, "headroom": steward.account()})


def session_within_allowance(
    context: RuntimeContext,
    *,
    provider: str,
    prompt: str,
    working_directory: Path,
    model: str | None,
    tools: list[str],
    resume_session: str | None = None,
    call: Callable[..., CommandResult] | None = None,
    instruments: Instruments | None = None,
    hold_budget: float = QUOTA_WAIT,
) -> CommandResult:
    """One step's session — a plan step's, a remedy's or a merge resolution's — admitted,
    held and resumed at the allowance, with every session it opens feeding the shared
    reading. `hold_budget` is how long it may wait; a step whose engine bound carries no hold
    passes nothing."""
    call = run_provider if call is None else call
    if provider not in METERED_PROVIDERS:
        return call(
            provider,
            prompt,
            working_directory,
            "auto",
            model,
            tools,
            resume_session=resume_session,
        )
    gauge = Gauge(headroom_directory(context.runs_root))

    def first(deadline: float) -> CommandResult:
        return call(
            provider,
            prompt,
            working_directory,
            "auto",
            model,
            tools,
            deadline_seconds=deadline,
            resume_session=resume_session,
            observe=gauge.observe,
        )

    def again(session_id: str, deadline: float) -> CommandResult:
        return call(
            provider,
            RESUME_AFTER_LIMIT,
            working_directory,
            "auto",
            model,
            tools,
            deadline_seconds=deadline,
            resume_session=session_id,
            observe=gauge.observe,
        )

    return within_allowance(
        first,
        again,
        gauge=gauge,
        model=model,
        instruments=default_instruments() if instruments is None else instruments,
        announce=announcer(context),
        hold_budget=hold_budget,
    )


__all__ = [
    "ADMISSIONS",
    "AFTER_LIMIT",
    "BEFORE_SESSION",
    "Decision",
    "Gauge",
    "Hold",
    "Instruments",
    "Reading",
    "Resume",
    "Steward",
    "Unmeasured",
    "Window",
    "announcer",
    "decide",
    "default_instruments",
    "endpoint_enabled",
    "fresh_windows",
    "local_moment",
    "measure",
    "reading_from",
    "run_probe",
    "session_within_allowance",
    "usage_windows",
    "window_of",
    "within_allowance",
]
