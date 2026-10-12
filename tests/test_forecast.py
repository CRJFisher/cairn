"""Spending the allowance knowingly: what a step like this one has cost here before.

Organised by [45 B]'s acceptance — `run start` and `explain workflow` state a forecast
against the current reading, naming the number of past records it rests on, or say that
there are none — and by the honesty rule the figures rest on: an estimate is built from
recorded differences between two measurements and from nothing else.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from cairn.forecast import (
    NOTHING_TO_ESTIMATE_FROM,
    QueueStep,
    forecast,
    history,
    lines,
    observations,
    pace,
    queue_of,
    step_estimate,
)
from cairn.headroom import Gauge, Window, empty_reading
from cairn.layout import RECORD_FILE

NOW = 1_791_140_000.0
WEEKLY_RESET = 1_791_400_000


def consumption(
    *, kind: str = "agent.claude", model: str = "opus", **deltas: float | None
) -> dict[str, Any]:
    return {
        "kind": kind,
        "model": model,
        "cost_usd": 1.5,
        "tokens": {"input": 10, "output": 20},
        "by_model": {},
        "windows": [
            {
                "window": window,
                "before": 0.1,
                "before_read_at": "2026-10-10T14:51:00Z",
                "after": None if delta is None else 0.1 + delta,
                "after_read_at": None if delta is None else "2026-10-10T18:00:00Z",
                "resets_at": "2026-10-13T05:00:00Z",
                "delta": delta,
            }
            for window, delta in deltas.items()
        ],
    }


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / "runs"
        self.root.mkdir(parents=True)

    def record(self, run_id: str, *steps: dict[str, Any] | None) -> None:
        directory = self.root / run_id
        directory.mkdir(parents=True, exist_ok=True)
        (directory / RECORD_FILE).write_text(
            json.dumps({"run_id": run_id, "steps": [{"consumption": step} for step in steps]}),
            encoding="utf-8",
        )

    def reading(self, used: float | None, *, window: str = "seven_day") -> Any:
        found = empty_reading()
        found["windows"][window] = Window(
            used=used,
            status="allowed",
            resets_at=WEEKLY_RESET,
            source="endpoint",
            read_at=NOW,
        )
        return found


class AnEstimateRestsOnMeasuredDifferences(Fixture):
    def test_a_window_with_no_delta_contributes_nothing(self) -> None:
        """A window measured once is not a window that moved by nothing ([45 A])."""
        self.record("run-1", consumption(seven_day=None))
        self.assertEqual(observations(self.root), [])
        self.assertEqual(history(self.root), {})

    def test_a_window_corrected_downward_survives_as_the_measurement_it_is(self) -> None:
        """A second feeder reading the window lower is a difference between two measurements
        like any other, so it reaches the history rather than being read as never measured
        twice ([45 A])."""
        self.record("run-1", consumption(seven_day=-0.05))
        self.assertEqual(
            history(self.root)[QueueStep("agent.claude", "opus")], {"seven_day": [-0.05]}
        )

    def test_every_measured_delta_is_grouped_by_the_kind_and_model_that_spent_it(self) -> None:
        self.record("run-1", consumption(seven_day=0.2), consumption(model="sonnet", seven_day=0.01))
        self.record("run-2", consumption(seven_day=0.1, five_hour=0.4))
        grouped = history(self.root)
        self.assertEqual(
            grouped[QueueStep("agent.claude", "opus")],
            {"seven_day": [0.2, 0.1], "five_hour": [0.4]},
        )
        self.assertEqual(
            grouped[QueueStep("agent.claude", "sonnet")], {"seven_day": [0.01]}
        )

    def test_the_median_is_taken_so_one_runaway_session_does_not_decide(self) -> None:
        self.record("run-1", consumption(seven_day=0.1))
        self.record("run-2", consumption(seven_day=0.2))
        self.record("run-3", consumption(seven_day=0.9))
        found = step_estimate(history(self.root), QueueStep("agent.claude", "opus"))
        self.assertAlmostEqual(found["seven_day"].fraction, 0.2)
        self.assertEqual(found["seven_day"].records, 3)

    def test_a_record_that_cannot_be_read_costs_its_own_rows_and_nothing_else(self) -> None:
        self.record("run-1", consumption(seven_day=0.2))
        damaged = self.root / "run-2"
        damaged.mkdir()
        (damaged / RECORD_FILE).write_text("{not json", encoding="utf-8")
        (self.root / ".headroom").mkdir()
        self.assertEqual(len(observations(self.root)), 1)

    def test_a_runs_root_that_does_not_exist_estimates_from_nothing(self) -> None:
        self.assertEqual(observations(self.root.parent / "absent"), [])


class TheQueueIsReadOffTheDefinition(Fixture):
    def test_only_agent_bodies_naming_a_provider_and_a_model_are_counted(self) -> None:
        self.assertEqual(
            queue_of(
                [
                    ("agent", "run", "--provider", "claude", "--model", "opus"),
                    ("agent", "run", "--provider", "claude", "--model", "sonnet"),
                    ("exec", "--command", "pytest"),
                    ("agent", "run", "--provider", "claude"),
                    (),
                ]
            ),
            (
                QueueStep("agent.claude", "opus"),
                QueueStep("agent.claude", "sonnet"),
            ),
        )


class TheForecastIsASentenceAgainstTheCurrentReading(Fixture):
    def test_the_queues_steps_are_summed_and_the_records_counted(self) -> None:
        self.record("run-1", consumption(seven_day=0.2))
        self.record("run-2", consumption(seven_day=0.1))
        found = forecast(
            self.root, [QueueStep("agent.claude", "opus"), QueueStep("agent.claude", "opus")]
        )
        self.assertEqual(found.steps, 2)
        self.assertAlmostEqual(found.windows[0].fraction, 0.3)
        self.assertEqual(found.windows[0].steps, 2)
        self.assertEqual(found.records, 4)

    def test_the_sentence_names_the_records_and_what_is_left_of_the_window(self) -> None:
        self.record("run-1", consumption(seven_day=0.31))
        said = lines(
            forecast(self.root, [QueueStep("agent.claude", "opus")]),
            self.reading(0.77),
            now=NOW,
        )
        self.assertEqual(len(said), 1)
        self.assertIn("1 agent step(s) have cost about 31% of the weekly allowance", said[0])
        self.assertIn("from 1 record(s)", said[0])
        self.assertIn("23% remains, reopening", said[0])

    def test_a_queue_no_record_speaks_for_says_so_and_says_nothing_else(self) -> None:
        said = lines(
            forecast(self.root, [QueueStep("agent.claude", "opus")]),
            self.reading(0.77),
            now=NOW,
        )
        self.assertEqual(said, [NOTHING_TO_ESTIMATE_FROM])

    def test_a_queue_only_half_covered_says_how_many_steps_it_speaks_for(self) -> None:
        self.record("run-1", consumption(seven_day=0.2))
        said = lines(
            forecast(
                self.root,
                [QueueStep("agent.claude", "opus"), QueueStep("agent.claude", "haiku")],
            ),
            self.reading(0.77),
            now=NOW,
        )
        self.assertIn("1 of this queue's 2 agent step(s)", said[0])

    def test_a_window_the_reading_carries_no_figure_for_is_said_as_that(self) -> None:
        """The stream speaks only from 90% up, so most of a window's life has no figure and
        a forecast that implied one would be inventing the half it exists to supply."""
        self.record("run-1", consumption(seven_day=0.2))
        queue = [QueueStep("agent.claude", "opus")]
        self.assertIn(
            "no figure for it",
            lines(forecast(self.root, queue), self.reading(None), now=NOW)[0],
        )
        self.assertIn(
            "no measurement of it",
            lines(forecast(self.root, queue), self.reading(0.5, window="five_hour"), now=NOW)[0],
        )


class AdmissionIsPacedByTheSameFigures(Fixture):
    def test_the_estimate_handed_to_admission_is_a_share_of_each_window(self) -> None:
        self.record("run-1", consumption(seven_day=0.2, five_hour=0.4))
        self.assertEqual(
            pace(self.root, kind="agent.claude", model="opus"),
            {"seven_day": 0.2, "five_hour": 0.4},
        )

    def test_a_step_with_no_model_and_a_kind_nothing_recorded_estimate_nothing(self) -> None:
        self.record("run-1", consumption(seven_day=0.2))
        self.assertEqual(pace(self.root, kind="agent.claude", model=None), {})
        self.assertEqual(pace(self.root, kind="agent.claude", model="haiku"), {})
        self.assertEqual(pace(self.root, kind="command", model="opus"), {})

    def test_the_forecast_reads_the_records_and_never_the_shared_reading(self) -> None:
        """The module that decides holds is not the one that reads the records, so the
        estimate reaches admission as a figure rather than as a second instrument."""
        self.record("run-1", consumption(seven_day=0.2))
        gauge = Gauge(self.root / ".headroom", clock=lambda: NOW, account_reader=lambda: None)
        self.assertFalse(gauge.path.exists())
        pace(self.root, kind="agent.claude", model="opus")
        self.assertFalse(gauge.path.exists())
