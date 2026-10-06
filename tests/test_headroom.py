"""Working within the subscription: one shared reading, admission, the hold and the resume.

Organised by [38]'s acceptance: the reset time read from the shape the CLI really emits, a
reading below the threshold admitting with no probe, concurrent steps sharing one probe, an
unknown reading admitting and saying why, a hold that measures again after the reset, a
weekly hold longer than a step may wait ending `quota_held` in a sentence, a limit met
mid-session held and resumed rather than failed, and every one of those facts reaching the
record and the report.

The clock is a fake one throughout. A hold is hours long by design, and a test that slept
would be measuring the machine rather than the decision.
"""

from __future__ import annotations

import io
import json
import tempfile
import threading
import time
import unittest
from collections.abc import Callable
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any
from unittest.mock import patch

from cairn.core import (
    EXIT_OK,
    EXIT_RATE_LIMITED,
    CairnError,
    CommandResult,
    RuntimeContext,
)
from cairn.headroom import (
    ADMITTED,
    AFTER_LIMIT,
    BEFORE_SESSION,
    HELD,
    INERT,
    READING_TTL,
    RESET_SETTLE,
    UNKNOWN,
    WARNED,
    Gauge,
    Hold,
    Instruments,
    decide,
    endpoint_enabled,
    measure,
    session_within_allowance,
    usage_windows,
    within_allowance,
)
from cairn.layout import HEADROOM_ENDPOINT_ENV, headroom_directory, holds_directory
from cairn.plan.schema import HANG_GUARD, QUOTA_WAIT
from cairn.providers import (
    RATE_LIMITED,
    EndpointRefused,
    Unmeasured,
    access_token,
    run_claude,
)
from cairn.record.extract import extract, read_holds
from cairn.record.facts import as_mapping
from cairn.record.vocabulary import (
    NEXT_AWAIT_ALLOWANCE,
    OUTCOME_EXCLUDED,
    OUTCOME_RUNNING,
)
from cairn.report.compose import document
from cairn.report.phrases import SENTENCE_BY_ACTION
from cairn.report.terminal import render
from cairn.verify import QUOTA_HELD, REPORTED_HELD, divergence_line, judge
from tests.test_step_kinds import FakeOutput, FakeProcess

# The measured event, from Claude Code 2.1.220 ([38] § What was measured).
NOW = 1_791_140_000.0
FIVE_HOUR_RESET = 1_791_151_800


def event(
    window: str = "five_hour",
    status: str = "allowed",
    resets_at: int | None = FIVE_HOUR_RESET,
    utilization: float | None = None,
) -> dict[str, Any]:
    info: dict[str, Any] = {
        "status": status,
        "rateLimitType": window,
        "overageStatus": "rejected",
        "isUsingOverage": False,
    }
    if resets_at is not None:
        info["resetsAt"] = resets_at
    if utilization is not None:
        info["utilization"] = utilization
    return {"type": "rate_limit_event", "rate_limit_info": info}


class Clock:
    """Wall time that moves only when the code under test sleeps or a test says so."""

    def __init__(self, now: float = NOW) -> None:
        self.now = now
        self.slept: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class Probe:
    """A probe whose answer is decided by the test, counted so sharing can be asserted."""

    def __init__(self, answer: Callable[[], list[dict[str, Any]]]) -> None:
        self.answer = answer
        self.calls = 0

    def __call__(self, timeout: float) -> list[dict[str, Any]]:
        del timeout
        self.calls += 1
        return self.answer()


def silent_probe() -> Probe:
    return Probe(list)


def done(turns: int = 2, session: str = "session-1") -> CommandResult:
    return CommandResult(
        EXIT_OK, "done", "finished", [], False, None, {"session_id": session, "turn_count": turns}
    )


def limited(resets_at: int, window: str = "five_hour", session: str = "session-1") -> CommandResult:
    return CommandResult(
        EXIT_RATE_LIMITED,
        "failed",
        "agent process ended with rate_limited",
        [],
        False,
        RATE_LIMITED,
        {
            "session_id": session,
            "turn_count": 3,
            "rate_limits": [event(window, "rejected", resets_at)],
        },
    )


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.clock = Clock()
        self.gauge = Gauge(Path(self.directory.name) / ".headroom", clock=self.clock.time)
        self.announced: list[Hold | None] = []

    def instruments(self, probe: Probe | None = None) -> Instruments:
        return Instruments(probe=probe or silent_probe(), endpoint=None)

    def run_step(
        self,
        first: Callable[[float], CommandResult],
        again: Callable[[str, float], CommandResult] | None = None,
        *,
        probe: Probe | None = None,
        model: str = "sonnet",
    ) -> CommandResult:
        def refuse(_session: str, _deadline: float) -> CommandResult:
            raise AssertionError("no resume was expected")

        return within_allowance(
            first,
            again or refuse,
            gauge=self.gauge,
            model=model,
            instruments=self.instruments(probe),
            announce=self.announced.append,
            sleep=self.clock.sleep,
        )


class TheResetTimeIsReadFromTheShapeTheCliEmits(unittest.TestCase):
    """[38 D]: the reader required a top-level string and every real event nests an integer,
    so the reported moment was always null."""

    def test_a_real_event_names_its_moment_in_the_report(self) -> None:
        def limited_session() -> type[FakeProcess]:
            class Limited(FakeProcess):
                def __init__(self, command: list[str], **kwargs: object) -> None:
                    super().__init__(command, **kwargs)
                    self.returncode = 1
                    self.stdout = FakeOutput(
                        json.dumps(event(status="rejected")) + "\n" + json.dumps(self.output()) + "\n"
                    )

                def output(self) -> dict[str, Any]:
                    record = super().output()
                    record["terminal_reason"] = "blocking_limit"
                    del record["structured_output"]
                    return record

            return Limited

        with redirect_stdout(io.StringIO()):
            result = run_claude("x", Path.cwd(), "auto", "sonnet", [], limited_session())
        self.assertEqual(result.cause, RATE_LIMITED)
        self.assertEqual(result.detail["resets_at"], "2026-10-04T22:10:00Z")

    def test_every_session_feeds_the_shared_reading_as_its_events_arrive(self) -> None:
        seen: list[dict[str, Any]] = []

        class Warned(FakeProcess):
            def __init__(self, command: list[str], **kwargs: object) -> None:
                super().__init__(command, **kwargs)
                system = {"type": "system", "subtype": "init", "apiKeySource": "none"}
                self.stdout = FakeOutput(
                    "\n".join(
                        json.dumps(line)
                        for line in (system, event(status="allowed_warning", utilization=0.9), self.output())
                    )
                    + "\n"
                )

        with redirect_stdout(io.StringIO()):
            run_claude("x", Path.cwd(), "auto", "sonnet", [], Warned, observe=seen.append)
        self.assertEqual([message["type"] for message in seen], ["system", "rate_limit_event"])

    def test_an_observer_that_fails_never_costs_the_session(self) -> None:
        def broken(_message: dict[str, Any]) -> None:
            raise OSError("disk full")

        class Eventful(FakeProcess):
            def __init__(self, command: list[str], **kwargs: object) -> None:
                super().__init__(command, **kwargs)
                self.stdout = FakeOutput(json.dumps(event()) + "\n" + json.dumps(self.output()) + "\n")

        with redirect_stdout(io.StringIO()):
            result = run_claude("x", Path.cwd(), "auto", "sonnet", [], Eventful, observe=broken)
        self.assertEqual(result.status, "done")


class TheReadingIsSharedAndAged(Fixture):
    def test_a_stream_event_becomes_a_measurement_of_its_window(self) -> None:
        self.gauge.observe(event(utilization=0.42))
        window = self.gauge.read()["windows"]["five_hour"]
        self.assertEqual(window["used"], 0.42)
        self.assertEqual(window["resets_at"], FIVE_HOUR_RESET)
        self.assertEqual(window["source"], "stream")
        self.assertEqual(window["read_at"], NOW)

    def test_a_quieter_event_keeps_the_figure_the_same_window_already_reached(self) -> None:
        self.gauge.observe(event(status="allowed_warning", utilization=0.93))
        self.clock.now += 60
        self.gauge.observe(event(status="allowed"))
        self.assertEqual(self.gauge.read()["windows"]["five_hour"]["used"], 0.93)

    def test_a_new_window_forgets_the_last_windows_figure(self) -> None:
        self.gauge.observe(event(utilization=0.93))
        self.gauge.observe(event(resets_at=FIVE_HOUR_RESET + 18000))
        self.assertIsNone(self.gauge.read()["windows"]["five_hour"]["used"])

    def test_a_damaged_reading_is_an_empty_one_rather_than_a_crash(self) -> None:
        self.gauge.directory.mkdir(parents=True)
        self.gauge.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(self.gauge.read()["windows"], {})


class AdmissionReadsTheReadingAndNeverTheClock(Fixture):
    def decided(self, model: str = "sonnet") -> str:
        return decide(self.gauge.read(), now=self.clock.now, model=model).admission

    def test_the_table(self) -> None:
        cases: list[tuple[str, list[dict[str, Any]], str]] = [
            ("a fresh allowed window admits", [event()], ADMITTED),
            ("a rejected window holds", [event(status="rejected")], HELD),
            ("a window at the threshold holds", [event(utilization=0.95)], HELD),
            ("a warning below it admits and says so", [event(status="allowed_warning", utilization=0.8)], WARNED),
            ("a warning with no figure admits and says so", [event(status="allowed_warning")], WARNED),
            ("nothing measured admits as unknown", [], UNKNOWN),
        ]
        for name, events, expected in cases:
            with self.subTest(name):
                self.gauge.path.unlink(missing_ok=True)
                for message in events:
                    self.gauge.observe(message)
                self.assertEqual(self.decided(), expected)

    def test_a_hold_lasts_until_the_reported_reset_of_the_last_closed_window(self) -> None:
        self.gauge.observe(event(status="rejected"))
        self.gauge.observe(event("seven_day", utilization=0.99, resets_at=FIVE_HOUR_RESET + 86400))
        decision = decide(self.gauge.read(), now=self.clock.now, model="sonnet")
        self.assertEqual(decision.window, "seven_day")
        self.assertEqual(decision.until, FIVE_HOUR_RESET + 86400)

    def test_a_reading_older_than_its_ttl_is_unknown_not_trusted(self) -> None:
        self.gauge.observe(event(status="rejected"))
        self.clock.now += READING_TTL + 1
        self.assertEqual(self.decided(), UNKNOWN)

    def test_a_window_whose_reset_has_passed_says_nothing_about_the_window_now_open(self) -> None:
        self.gauge.observe(event(utilization=0.99, resets_at=int(NOW) + 30))
        self.clock.now += 60
        self.assertEqual(self.decided(), UNKNOWN)

    def test_a_rejection_outlives_its_own_reported_reset_until_measured_again(self) -> None:
        self.gauge.observe(event(status="rejected", resets_at=int(NOW) + 30))
        self.clock.now += 60
        decision = decide(self.gauge.read(), now=self.clock.now, model="sonnet")
        self.assertEqual((decision.admission, decision.until), (HELD, None))

    def test_a_per_model_weekly_window_holds_only_its_own_family(self) -> None:
        self.gauge.observe(event("seven_day_opus", status="rejected", resets_at=FIVE_HOUR_RESET))
        self.assertEqual(self.decided("opus"), HELD)
        self.assertEqual(self.decided("sonnet"), UNKNOWN)

    def test_a_session_funded_by_an_api_key_has_no_window_to_hold_on(self) -> None:
        self.gauge.observe({"type": "system", "subtype": "init", "apiKeySource": "ANTHROPIC_API_KEY"})
        self.gauge.observe(event(status="rejected"))
        self.assertEqual(self.decided(), INERT)


class OneMeasurementServesEveryStep(Fixture):
    def test_a_fresh_stream_reading_admits_with_no_probe(self) -> None:
        self.gauge.observe(event(utilization=0.4))
        probe = silent_probe()
        notes = measure(
            self.gauge, model="sonnet", newer_than=None, budget_until=NOW + 600,
            instruments=self.instruments(probe),
        )
        self.assertEqual((probe.calls, notes), (0, []))

    def test_a_stale_reading_is_probed_and_the_probe_is_the_reading(self) -> None:
        probe = Probe(lambda: [event(utilization=0.5)])
        measure(
            self.gauge, model="sonnet", newer_than=None, budget_until=NOW + 600,
            instruments=self.instruments(probe),
        )
        self.assertEqual(probe.calls, 1)
        self.assertEqual(self.gauge.read()["windows"]["five_hour"]["source"], "probe")

    def test_eight_concurrent_steps_cause_at_most_one_probe(self) -> None:
        gauge = Gauge(self.gauge.directory)
        lock = threading.Lock()
        calls: list[int] = []

        def slow_probe(_timeout: float) -> list[dict[str, Any]]:
            with lock:
                calls.append(1)
            time.sleep(0.2)
            return [event(utilization=0.5, resets_at=int(time.time()) + 3600)]

        instruments = Instruments(probe=slow_probe, endpoint=None)
        threads = [
            threading.Thread(
                target=measure,
                args=(gauge,),
                kwargs={
                    "model": "sonnet",
                    "newer_than": None,
                    "budget_until": time.time() + 600,
                    "instruments": instruments,
                },
            )
            for _ in range(8)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        self.assertEqual(len(calls), 1)

    def test_the_endpoint_answers_first_where_the_owner_turned_it_on(self) -> None:
        probe = silent_probe()
        asked: list[float] = []

        def endpoint(now: float) -> dict[str, Any]:
            asked.append(now)
            return usage_windows(
                {"five_hour": {"utilization": 97, "resets_at": "2026-10-04T22:10:00+00:00"}},
                read_at=now,
            )

        measure(
            self.gauge, model="sonnet", newer_than=None, budget_until=NOW + 600,
            instruments=Instruments(probe=probe, endpoint=endpoint),
        )
        self.assertEqual((len(asked), probe.calls), (1, 0))
        decision = decide(self.gauge.read(), now=self.clock.now, model="sonnet")
        self.assertEqual((decision.admission, decision.used), (HELD, 0.97))

    def test_a_refusing_endpoint_is_backed_off_and_the_probe_answers_instead(self) -> None:
        def refusing(_now: float) -> dict[str, Any]:
            raise EndpointRefused("the usage endpoint answered 429")

        probe = Probe(lambda: [event()])
        notes = measure(
            self.gauge, model="sonnet", newer_than=None, budget_until=NOW + 600,
            instruments=Instruments(probe=probe, endpoint=refusing),
        )
        self.assertIn("the usage endpoint answered 429", notes)
        self.assertEqual(probe.calls, 1)
        self.assertEqual(self.gauge.read()["endpoint_failures"], 1)

    def test_a_failing_endpoint_is_not_asked_again_inside_its_cache(self) -> None:
        asked: list[float] = []

        def failing(now: float) -> dict[str, Any]:
            asked.append(now)
            raise Unmeasured("the usage endpoint answered 500")

        instruments = Instruments(probe=silent_probe(), endpoint=failing)
        for _ in range(2):
            measure(
                self.gauge, model="sonnet", newer_than=None, budget_until=NOW + 600,
                instruments=instruments,
            )
        self.assertEqual(len(asked), 1)
        self.assertEqual(self.gauge.read()["endpoint_failures"], 0)

    def test_a_reading_that_cannot_be_written_is_unknown_not_a_failed_step(self) -> None:
        def endpoint(now: float) -> dict[str, Any]:
            return usage_windows({"five_hour": {"utilization": 40}}, read_at=now)

        with patch.object(self.gauge, "record", side_effect=OSError("no space left on device")):
            notes = measure(
                self.gauge, model="sonnet", newer_than=None, budget_until=NOW + 600,
                instruments=Instruments(probe=silent_probe(), endpoint=endpoint),
            )
        self.assertIn("no space left on device", notes)

    def test_the_endpoint_is_off_unless_the_owner_says_so(self) -> None:
        self.assertFalse(endpoint_enabled({}))
        self.assertFalse(endpoint_enabled({HEADROOM_ENDPOINT_ENV: "yes"}))
        self.assertTrue(endpoint_enabled({HEADROOM_ENDPOINT_ENV: "1"}))

    def test_an_expired_credential_is_unknown_and_never_refreshed(self) -> None:
        stored = json.dumps(
            {"claudeAiOauth": {"accessToken": "token", "expiresAt": (NOW + 30) * 1000}}
        )
        self.assertIsNone(access_token(NOW, stored))
        self.assertEqual(access_token(NOW - 3600, stored), "token")

    def test_the_endpoints_percent_is_the_readings_fraction(self) -> None:
        windows = usage_windows(
            {
                "five_hour": {"utilization": 42.0, "resets_at": "2026-10-04T22:10:00+00:00"},
                "seven_day": None,
                "extra_usage": {"utilization": 10},
            },
            read_at=NOW,
        )
        self.assertEqual(list(windows), ["five_hour"])
        self.assertEqual(windows["five_hour"]["used"], 0.42)
        self.assertEqual(windows["five_hour"]["resets_at"], FIVE_HOUR_RESET)


class AStepAdmittedOnUnknownSaysWhy(Fixture):
    def test_the_run_does_not_stall_on_a_blind_instrument(self) -> None:
        sessions: list[float] = []

        def first(deadline: float) -> CommandResult:
            sessions.append(deadline)
            return done()

        def failing(_timeout: float) -> list[dict[str, Any]]:
            raise Unmeasured("the probe could not start: no such file")

        result = within_allowance(
            first,
            lambda _s, _d: done(),
            gauge=self.gauge,
            model="sonnet",
            instruments=Instruments(probe=failing, endpoint=None),
            announce=self.announced.append,
            sleep=self.clock.sleep,
        )
        self.assertEqual(sessions, [HANG_GUARD])
        headroom = result.detail["headroom"]
        self.assertEqual(headroom["admission"], UNKNOWN)
        self.assertIn("the probe could not start", headroom["reason"])
        self.assertIn(HEADROOM_ENDPOINT_ENV, headroom["reason"])


class AHoldMeasuresAgainAfterTheReset(Fixture):
    def test_a_closed_five_hour_window_is_waited_out_then_measured_again(self) -> None:
        self.gauge.observe(event(status="rejected"))
        reopened = Probe(lambda: [event(resets_at=FIVE_HOUR_RESET + 18000)])
        started: list[float] = []

        def first(deadline: float) -> CommandResult:
            started.append(self.clock.now)
            return done()

        result = self.run_step(first, probe=reopened)
        self.assertEqual(result.status, "done")
        self.assertEqual(started, [FIVE_HOUR_RESET + RESET_SETTLE])
        self.assertEqual(reopened.calls, 1, "the step measured again rather than trust the clock")
        [hold] = result.detail["headroom"]["holds"]
        self.assertEqual((hold["window"], hold["after"]), ("five_hour", BEFORE_SESSION))
        self.assertEqual(self.announced, [hold, None])
        self.assertEqual(
            result.detail["headroom"]["admission"], ADMITTED,
            "the report names the decision the session started on, not the hold before it",
        )

    def test_a_window_still_closed_after_its_reset_is_backed_off_not_entered(self) -> None:
        self.gauge.observe(event(status="rejected"))
        answers = iter([[event(status="rejected", resets_at=None)], [event()]])
        probe = Probe(lambda: next(answers))
        result = self.run_step(lambda _deadline: done(), probe=probe)
        holds = result.detail["headroom"]["holds"]
        self.assertEqual(len(holds), 2)
        self.assertEqual(self.clock.slept[1], 300.0)
        self.assertEqual(result.status, "done")

    def test_held_time_is_never_charged_as_work(self) -> None:
        self.gauge.observe(event(status="rejected"))
        given: list[float] = []

        def first(deadline: float) -> CommandResult:
            given.append(deadline)
            return done()

        self.run_step(first, probe=Probe(lambda: [event(resets_at=FIVE_HOUR_RESET + 18000)]))
        self.assertEqual(given, [HANG_GUARD])


class AWeeklyHoldEndsTheStepInASentence(Fixture):
    def test_no_worker_sleeps_for_days(self) -> None:
        thursday = int(NOW) + 3 * 86400
        self.gauge.observe(event("seven_day", utilization=0.97, resets_at=thursday))

        def first(_deadline: float) -> CommandResult:
            raise AssertionError("a session was started into a closed window")

        result = self.run_step(first)
        self.assertEqual(self.clock.slept, [])
        self.assertEqual(result.exit_code, EXIT_RATE_LIMITED)
        self.assertEqual(result.cause, QUOTA_HELD)
        self.assertRegex(result.summary, r"^held until .+ — the weekly allowance at 97%$")
        headroom = result.detail["headroom"]
        self.assertEqual(headroom["held_window"], "seven_day")
        self.assertEqual(headroom["held_until"], "2026-10-07T18:53:20Z")
        self.assertEqual(headroom["admission"], HELD)


class ALimitMetMidSessionIsAPause(Fixture):
    def test_the_session_is_held_then_resumed_and_lands_as_if_uninterrupted(self) -> None:
        self.gauge.observe(event())
        resumed: list[tuple[str, float]] = []

        def first(_deadline: float) -> CommandResult:
            self.clock.now += 1200
            return limited(FIVE_HOUR_RESET)

        def again(session: str, deadline: float) -> CommandResult:
            resumed.append((session, deadline))
            self.clock.now += 600
            return done(turns=4)

        result = self.run_step(
            first, again, probe=Probe(lambda: [event(resets_at=FIVE_HOUR_RESET + 18000)])
        )
        self.assertEqual(result.exit_code, EXIT_OK)
        self.assertEqual(result.status, "done")
        self.assertEqual(resumed, [("session-1", HANG_GUARD - 1200)])
        self.assertEqual(result.detail["turn_count"], 7)
        headroom = result.detail["headroom"]
        [hold] = headroom["holds"]
        self.assertEqual((hold["window"], hold["after"]), ("five_hour", AFTER_LIMIT))
        self.assertEqual([entry["outcome"] for entry in headroom["resumes"]], ["done"])

    def test_the_limit_closes_the_window_for_every_other_step_at_once(self) -> None:
        self.gauge.observe(event())

        def again(_session: str, _deadline: float) -> CommandResult:
            return done()

        seen_closed: list[str] = []

        def first(_deadline: float) -> CommandResult:
            return limited(FIVE_HOUR_RESET)

        original = self.gauge.record

        def spy(windows: dict[str, Any]) -> None:
            original(windows)
            seen_closed.append(decide(self.gauge.read(), now=self.clock.now, model="sonnet").admission)

        with patch.object(self.gauge, "record", spy):
            self.run_step(
                first, again, probe=Probe(lambda: [event(resets_at=FIVE_HOUR_RESET + 18000)])
            )
        self.assertEqual(seen_closed[0], HELD)

    def test_a_resume_that_cannot_continue_ends_the_step_held_with_its_session(self) -> None:
        self.gauge.observe(event())

        def again(_session: str, _deadline: float) -> CommandResult:
            raise CairnError("provider_protocol", "No conversation found with session ID")

        result = self.run_step(
            lambda _deadline: limited(FIVE_HOUR_RESET),
            again,
            probe=Probe(lambda: [event(resets_at=FIVE_HOUR_RESET + 18000)]),
        )
        self.assertEqual(result.cause, QUOTA_HELD)
        self.assertEqual(result.detail["session_id"], "session-1")
        self.assertIn("could not be resumed", result.summary)

    def test_a_second_limit_is_named_by_the_session_that_met_it(self) -> None:
        self.gauge.observe(event())
        far = int(NOW) + QUOTA_WAIT + 7200

        def again(_session: str, _deadline: float) -> CommandResult:
            return limited(far, window="seven_day")._replace(
                detail={
                    "session_id": "session-1",
                    "turn_count": 1,
                    "rate_limits": [event("seven_day", "allowed_warning", far)],
                }
            )

        result = self.run_step(
            lambda _deadline: limited(FIVE_HOUR_RESET),
            again,
            probe=Probe(lambda: [event(resets_at=FIVE_HOUR_RESET + 18000)]),
        )
        self.assertEqual(result.cause, QUOTA_HELD)
        self.assertEqual(result.detail["headroom"]["held_window"], "seven_day")

    def test_a_session_that_raises_still_carries_what_it_was_admitted_on(self) -> None:
        self.gauge.observe(event())

        def first(_deadline: float) -> CommandResult:
            raise CairnError("provider_protocol", "stream ended without a result message")

        with self.assertRaises(CairnError) as raised:
            self.run_step(first)
        self.assertEqual(raised.exception.detail["headroom"]["admission"], ADMITTED)

    def test_a_limit_too_far_away_to_wait_for_ends_the_step_held(self) -> None:
        self.gauge.observe(event())
        far = int(NOW) + QUOTA_WAIT + 3600
        result = self.run_step(lambda _deadline: limited(far, window="seven_day"))
        self.assertEqual(result.cause, QUOTA_HELD)
        self.assertEqual(result.detail["headroom"]["held_window"], "seven_day")
        self.assertIn("weekly allowance spent", result.summary)


class AnUnmeteredProviderStartsAsItAlwaysDid(unittest.TestCase):
    def test_no_reading_no_hold_no_account(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            context = RuntimeContext("run-1", "work_a", root, root / "runs/run-1/reports/work_a.json", root / "runs")
            calls: list[dict[str, Any]] = []

            def call(*args: Any, **kwargs: Any) -> CommandResult:
                calls.append(kwargs)
                return done()

            def unwanted(_timeout: float) -> list[dict[str, Any]]:
                raise AssertionError("an unmetered provider was measured")

            result = session_within_allowance(
                context, provider="echo", prompt="x", working_directory=root, model=None,
                tools=[], call=call, instruments=Instruments(probe=unwanted, endpoint=None),
            )
            self.assertNotIn("headroom", result.detail)
            self.assertFalse(headroom_directory(root / "runs").exists())


class TheGateReadsAHeldStepAsHeld(unittest.TestCase):
    def test_a_held_step_is_never_read_as_a_veto(self) -> None:
        held = {"status": "failed", "cause": QUOTA_HELD, "needs_user_decision": False, "summary": "x"}
        verdict = judge(0, held)
        self.assertEqual(verdict["cause"], QUOTA_HELD)
        self.assertEqual(verdict["divergence"], {"reported": REPORTED_HELD, "asserted": True})
        self.assertFalse(verdict["record"])
        self.assertIsNone(judge(1, held)["divergence"])
        divergence = verdict["divergence"]
        assert divergence is not None
        self.assertIn("held at the subscription's allowance", divergence_line(divergence))


def held_run(*, running: bool = False) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """One agent step that ended `quota_held`, or one holding right now."""
    state: dict[str, Any] = {
        "dagRunId": "run-held",
        "name": "plan",
        "status": 1 if running else 2,
        "startedAt": "2026-10-05T01:00:00+01:00",
        "paramsList": ["CAIRN_REPOSITORY=/srv/work/product"],
        "nodes": [
            {"step": {"name": "work_alpha"}, "status": 1 if running else 2},
            {"step": {"name": "verify_alpha", "depends": ["work_alpha"]}, "status": 0 if running else 2},
            {"step": {"name": "mark_alpha", "depends": ["verify_alpha"]}, "status": 0 if running else 5},
            {"step": {"name": "commit_alpha", "depends": ["mark_alpha"]}, "status": 0 if running else 5},
        ],
    }
    if running:
        return state, {}
    headroom: dict[str, Any] = {
        "admission": "held",
        "reason": "the weekly allowance is at 97%, past the 95% hold threshold",
        "reading": {
            "seven_day": {
                "used": 0.97, "status": "allowed_warning", "resets_at": "2026-10-08T04:13:20Z",
                "source": "stream", "read_at": "2026-10-05T01:02:00Z", "age_seconds": 12.0,
            }
        },
        "holds": [],
        "resumes": [],
        "held_window": "seven_day",
        "held_until": "2026-10-08T04:13:20Z",
        "held_used": 0.97,
    }
    common: dict[str, Any] = {"run_id": "run-held", "needs_user_decision": False, "follow_up_work": []}
    return state, {
        "work_alpha": {
            **common, "step_id": "work_alpha", "status": "failed", "cause": QUOTA_HELD,
            "summary": "held until Thu 08 Oct 05:13 BST — the weekly allowance at 97%",
            "detail": {"headroom": headroom},
        },
        "mark_alpha": {
            **common, "step_id": "mark_alpha", "status": "failed", "cause": QUOTA_HELD,
            "summary": "the step was held at the subscription's allowance before it reported",
            "detail": {"position": "chain"},
        },
    }


class TheRecordAndTheReportSayItInASentence(unittest.TestCase):
    def test_a_held_step_names_the_window_and_the_moment_and_the_next_action_waits(self) -> None:
        state, reports = held_run()
        record = extract(state, reports, run_id="run-held")
        [step] = record["steps"]
        self.assertEqual((step["outcome"], step["cause"]), (OUTCOME_EXCLUDED, QUOTA_HELD))
        headroom = step["headroom"]
        assert headroom is not None
        self.assertEqual(headroom["held_until"], "2026-10-08T04:13:20Z")
        self.assertEqual(record["next_action"]["action"], NEXT_AWAIT_ALLOWANCE)
        self.assertEqual(record["allowance"][0]["window"], "seven_day")
        text = " ".join(render(document(record), as_mapping(record)).text.split())
        self.assertIn(SENTENCE_BY_ACTION[NEXT_AWAIT_ALLOWANCE], text)
        self.assertIn("2026-10-08T04:13:20Z", text)
        self.assertIn("seven_day 97% used", text)

    def test_a_step_holding_now_reads_as_waiting_not_stalled(self) -> None:
        state, reports = held_run(running=True)
        with tempfile.TemporaryDirectory() as temporary:
            directory = holds_directory(Path(temporary), "run-held")
            directory.mkdir(parents=True)
            (directory / "work_alpha.json").write_text(
                json.dumps(
                    {
                        "run_id": "run-held", "window": "five_hour",
                        "started": "2026-10-05T02:00:00Z", "until": "2026-10-05T07:31:00Z",
                        "why": "the 5-hour allowance is spent", "after": "admission",
                    }
                ),
                encoding="utf-8",
            )
            holds = read_holds(directory, "run-held")
        with patch("cairn.record.extract.owner_liveness", return_value=True):
            record = extract(state, reports, run_id="run-held", holds=holds)
        [step] = record["steps"]
        self.assertEqual(step["outcome"], OUTCOME_RUNNING)
        assert step["headroom"] is not None and step["headroom"]["holding"] is not None
        text = " ".join(render(document(record), as_mapping(record)).text.split())
        self.assertIn("waiting for the subscription's allowance, not stalled", text)
        self.assertIn("2026-10-05T07:31:00Z", text)

    def test_a_remedy_holding_now_reads_as_waiting_not_stalled(self) -> None:
        state, reports = held_run(running=True)
        state["nodes"] = [
            {"step": {"name": "work_alpha"}, "status": 4},
            {"step": {"name": "verify_alpha", "depends": ["work_alpha"]}, "status": 2},
            {"step": {"name": "remedy_alpha", "depends": ["verify_alpha"]}, "status": 1},
            {"step": {"name": "mark_alpha", "depends": ["remedy_alpha"]}, "status": 0},
        ]
        holding = {
            "run_id": "run-held", "window": "five_hour",
            "started": "2026-10-05T02:00:00Z", "until": "2026-10-05T07:31:00Z",
            "why": "the 5-hour allowance is spent", "after": "limit",
        }
        with patch("cairn.record.extract.owner_liveness", return_value=True):
            record = extract(state, reports, run_id="run-held", holds={"remedy_alpha": holding})
        [step] = record["steps"]
        assert step["headroom"] is not None and step["headroom"]["holding"] is not None
        self.assertEqual(step["headroom"]["holding"]["until"], "2026-10-05T07:31:00Z")


if __name__ == "__main__":
    unittest.main()
