"""The run record: the frozen vocabulary, the extraction, and the corpus that drives both.

The suite is organised by what it proves rather than by which module it touches, because
doc 12's exit criteria are claims about the model as a whole — that no synonym exists, that
the verdict is never read off the engine, that an absence is never a plausible default.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from cairn.__main__ import main as cairn_main
from cairn.core import CairnError, RuntimeContext
from cairn.layout import (
    ENGINE_HOST_ENV,
    ENGINE_PORT_ENV,
    RUNS_ROOT_ENV,
    VIEW_BASE_DEFAULT,
    VIEW_BASE_ENV,
    record_path,
    reports_directory,
    view_base,
    view_url,
)
from cairn.liveness import self_start_time
from cairn.record import engine
from cairn.record.engine import Attempt, began
from cairn.record.extract import classify_step, extract, read_reports, step_order
from cairn.record.facts import ABSENT, as_mapping, canonical_facts
from cairn.record.model import RunRecord
from cairn.record.store import read_record, write_record
from cairn.record.vocabulary import (
    ATTENTION_ORDER,
    EDGE_KINDS,
    EXIT_GREEN,
    EXIT_NO_RECORD,
    NEXT_ACTIONS,
    NEXT_RERUN,
    NEXT_SETTLE_MERGE,
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
    OVERLAYS,
    PROVENANCE_ABSENT,
    PROVENANCES,
    STEP_OUTCOMES,
    VERDICT_ALL_NO_OP,
    VERDICT_BLOCKED,
    VERDICT_EXIT_CODES,
    VERDICT_FAILED,
    VERDICT_GREEN,
    VERDICT_GREEN_WITH_EXCLUSIONS,
    VERDICT_PRECEDENCE,
    VERDICT_RUNNING,
)
from cairn.supervise import STATUS_RUNNING, last_record
from cairn.text import (
    LINE_LIMIT,
    TEXT_LIMIT,
    as_count,
    as_money,
    flatten,
    normalise,
    normalise_all,
)
from cairn.verify import (
    EXCLUSION_CAUSES,
    NOT_REACHED,
    ORCHESTRATOR_DIED,
    REPORTED_FAILURE,
    REPORTED_KILLED,
    TIMED_OUT,
    VERIFY_FAILED,
)
from cairn.workflow.schema import ENGINE_VERSION

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
CORPUS = PACKAGE_ROOT / "fixtures" / "runs"
DOCUMENT = PACKAGE_ROOT / "docs" / "run-model.md"

SHAPES = (
    "green",
    "red",
    "blocked",
    "green-with-exclusions",
    "all-no-op",
    "mid-run",
    "crashed",
    "timed-out",
    "agent",
)


def load(shape: str) -> tuple[dict[str, Any], dict[str, dict[str, Any]], str]:
    """One recorded run: the engine's last snapshot, this run's reports, and its identity."""
    directory = CORPUS / shape
    recording: Any = json.loads((directory / "recording.json").read_text(encoding="utf-8"))
    run_id = str(cast(dict[str, Any], recording)["run_id"])
    state = last_record(directory / "status.jsonl")
    assert state is not None, f"{shape} has no readable engine record"
    return state, read_reports(directory / "reports", run_id), run_id


def record_of(shape: str) -> RunRecord:
    """One recorded run, as the corpus recorded it."""
    state, reports, run_id = load(shape)
    return extract(state, reports, run_id=run_id)


def alive_copy(state: dict[str, Any]) -> dict[str, Any]:
    """The same snapshot with this process named as its owner.

    A recorded live run's process is gone by the time anything reads the fixture, so the
    only way to read those bytes as a live run is to point them at a process that is. That
    is not a workaround — it is the assertion: the status field is identical either way.
    """
    return {
        **state,
        "pid": os.getpid(),
        "pidStartedAt": int((self_start_time() or 0) * 1000),
    }


class TheVocabularyIsFrozen(unittest.TestCase):
    """Exit criterion: one document enumerates every enum value, with no synonym elsewhere."""

    def test_the_verdicts_are_exactly_these_six_in_this_precedence(self) -> None:
        self.assertEqual(
            VERDICT_PRECEDENCE,
            (
                "failed",
                "blocked",
                "running",
                "green_with_exclusions",
                "all_no_op",
                "green",
            ),
        )

    def test_the_step_outcomes_are_exactly_these_seven(self) -> None:
        self.assertEqual(
            STEP_OUTCOMES,
            (
                "verified",
                "failed",
                "excluded",
                "no_op",
                "not_reached",
                "running",
                "pending",
            ),
        )

    def test_the_attention_order_is_exactly_this(self) -> None:
        self.assertEqual(
            ATTENTION_ORDER,
            (
                "blocked",
                "failure",
                "excluded",
                "budget",
                "housekeeping_failure",
                "divergence",
                "follow_up",
            ),
        )

    def test_the_attention_order_is_not_the_verdict_order(self) -> None:
        """A block outranks a failure for a reader and not for a verdict, and both are frozen."""
        self.assertNotEqual(ATTENTION_ORDER[:2], VERDICT_PRECEDENCE[:2])

    def test_every_verdict_has_exactly_one_exit_code(self) -> None:
        self.assertEqual(set(VERDICT_EXIT_CODES), set(VERDICT_PRECEDENCE))

    def test_no_verdict_takes_the_code_argparse_spends_on_usage(self) -> None:
        """A caller reading 2 as a verdict would be reading a typo."""
        self.assertNotIn(2, VERDICT_EXIT_CODES.values())

    def test_the_record_quotes_the_gates_exclusion_causes_rather_than_re_spelling_them(
        self,
    ) -> None:
        for shape in SHAPES:
            for step in record_of(shape)["steps"]:
                with self.subTest(shape=shape, step=step["step_id"]):
                    self.assertIn(step["cause"], (None, *EXCLUSION_CAUSES))


class TheDocumentStatesTheWholeVocabulary(unittest.TestCase):
    """Exit criterion: one document enumerates every enum value and every field."""

    def setUp(self) -> None:
        self.text = DOCUMENT.read_text(encoding="utf-8")

    def test_every_frozen_value_is_spelled_in_the_document(self) -> None:
        for group in (
            VERDICT_PRECEDENCE,
            STEP_OUTCOMES,
            OVERLAYS,
            ATTENTION_ORDER,
            EDGE_KINDS,
            PROVENANCES,
            NEXT_ACTIONS,
        ):
            for value in group:
                with self.subTest(value=value):
                    self.assertIn(f"`{value}`", self.text)

    def test_every_record_field_is_named_in_the_document(self) -> None:
        for name in RunRecord.__annotations__:
            with self.subTest(field=name):
                self.assertIn(f"`{name}`", self.text)

    def test_the_causes_are_referred_to_rather_than_enumerated(self) -> None:
        """The gate's document owns that vocabulary, and two enumerations would drift.

        `not_reached` appears here as well, because it is deliberately one word for one
        event seen from two sides — a step outcome and a cause. What must not appear is the
        list.
        """
        self.assertIn("verify-gate.md", self.text)
        spelled = [cause for cause in EXCLUSION_CAUSES if f"`{cause}`" in self.text]
        self.assertNotIn("verify_failed", spelled)
        self.assertLess(len(spelled), 3, f"the record's document enumerates {spelled}")

    def test_the_document_states_the_engine_version_it_was_measured_against(self) -> None:
        self.assertIn(ENGINE_VERSION, self.text)

    def test_the_document_names_the_rest_apis_alternative_to_integer_archaeology(
        self,
    ) -> None:
        """Task 14 wants both mitigations in the table's own documentation, not just the pin."""
        self.assertIn("statusLabel", self.text)

    def test_the_vocabulary_is_minted_in_one_module_only(self) -> None:
        """A second module naming a verdict is the synonym this document exists to forbid."""
        pattern = re.compile(
            r"^(VERDICT|OUTCOME|OVERLAY|ATTENTION|PROVENANCE|NEXT|EDGE)_[A-Z0-9_]+ = ",
            re.MULTILINE,
        )
        holders = {
            path.relative_to(PACKAGE_ROOT).as_posix()
            for path in (PACKAGE_ROOT / "cairn").rglob("*.py")
            if pattern.search(path.read_text(encoding="utf-8"))
        }
        self.assertEqual(holders, {"cairn/record/vocabulary.py"})


class TheEngineStatusMappingIsPinned(unittest.TestCase):
    """Task 14: a table indexed by which vocabulary is read, and a hard error otherwise."""

    def test_five_is_a_skipped_node_a_queued_run_and_a_retry_trigger(self) -> None:
        """Three vocabularies overlap numerically, which is why one table would not do."""
        self.assertEqual(engine.node_status_name(5), "skipped")
        self.assertEqual(engine.run_status_name(5), "queued")
        self.assertEqual(engine.trigger_name(5), "retry")

    def test_an_unmapped_node_status_is_a_hard_error_naming_its_vocabulary(self) -> None:
        with self.assertRaises(CairnError) as caught:
            engine.node_status_name(9)
        self.assertEqual(caught.exception.cause, "engine_status_unmapped")
        self.assertEqual(caught.exception.detail["vocabulary"], "node")
        self.assertEqual(caught.exception.detail["engine"], ENGINE_VERSION)

    def test_an_unmapped_run_status_is_a_hard_error(self) -> None:
        with self.assertRaises(CairnError) as caught:
            engine.run_status_name(3)
        self.assertEqual(caught.exception.detail["vocabulary"], "run")

    def test_an_unmapped_trigger_is_a_hard_error(self) -> None:
        with self.assertRaises(CairnError) as caught:
            engine.trigger_name(99)
        self.assertEqual(caught.exception.detail["vocabulary"], "trigger")

    def test_a_status_that_is_not_a_number_is_refused_rather_than_defaulted(self) -> None:
        for value in ("4", None, True, 1.5):
            with self.subTest(value=value), self.assertRaises(CairnError):
                engine.node_status_name(value)

    def test_the_kill_survives_only_inside_the_error_sentence(self) -> None:
        """Measured against Dagu 2.11.0: the node is a plain `failed`; the bound and the
        elapsed time are in the prose, in Go's own duration spelling ([22 A])."""
        found = engine.parse_timeout(
            "step timed out after 2h30m0.041s (timeout: 2h30m0s): context deadline exceeded"
        )
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.bound_seconds, 9000)
        self.assertAlmostEqual(found.elapsed_seconds, 9000.041)
        short = engine.parse_timeout(
            "step timed out after 2.002s (timeout: 2s): context deadline exceeded"
        )
        self.assertEqual(short, engine.Timeout(bound_seconds=2, elapsed_seconds=2.002))
        for other in ("exit status 3", "step timed out", "", None, 7):
            with self.subTest(error=other):
                self.assertIsNone(engine.parse_timeout(other))

    def test_the_exit_code_survives_only_inside_the_error_string(self) -> None:
        self.assertEqual(engine.parse_exit_code("exit status 7"), 7)
        self.assertIsNone(engine.parse_exit_code("upstream failed"))
        self.assertIsNone(engine.parse_exit_code(None))

    def test_an_empty_timestamp_is_absence_rather_than_a_moment(self) -> None:
        """The engine spells "not yet" as an empty string, which is not a time."""
        self.assertIsNone(engine.moment(""))
        self.assertEqual(engine.moment("2026-08-11T13:39:21+01:00"), "2026-08-11T13:39:21+01:00")


class TheCorpusCoversTheStateSpace(unittest.TestCase):
    """Exit criterion: the fixture corpus covers every verdict and every step outcome."""

    def test_the_corpus_is_exactly_the_runs_the_document_names(self) -> None:
        recorded = {path.name for path in CORPUS.iterdir() if path.is_dir()}
        self.assertEqual(recorded, set(SHAPES))
        document = DOCUMENT.read_text(encoding="utf-8")
        for shape in SHAPES:
            with self.subTest(shape=shape):
                self.assertIn(f"`{shape}`", document)

    def test_every_recording_names_the_pinned_engine_version(self) -> None:
        """A pin bumped without re-recording leaves a corpus describing another engine."""
        for shape in SHAPES:
            with self.subTest(shape=shape):
                recording: Any = json.loads(
                    (CORPUS / shape / "recording.json").read_text(encoding="utf-8")
                )
                self.assertEqual(cast(dict[str, Any], recording)["engine"], ENGINE_VERSION)

    def test_every_verdict_is_covered_by_a_recorded_run(self) -> None:
        reached = {record_of(shape)["verdict"] for shape in SHAPES}
        state, reports, run_id = load("mid-run")
        reached.add(extract(alive_copy(state), reports, run_id=run_id)["verdict"])
        self.assertEqual(reached, set(VERDICT_PRECEDENCE))

    def test_every_step_outcome_is_covered_by_a_recorded_run(self) -> None:
        reached = {
            step["outcome"] for shape in SHAPES for step in record_of(shape)["steps"]
        }
        state, reports, run_id = load("mid-run")
        reached.update(
            step["outcome"]
            for step in extract(alive_copy(state), reports, run_id=run_id)["steps"]
        )
        self.assertEqual(reached, set(STEP_OUTCOMES))

    def test_every_engine_node_reaches_the_record(self) -> None:
        """A node dropped for being unrecognisable is one whose failure nothing reports."""
        for shape in SHAPES:
            with self.subTest(shape=shape):
                state, _, _ = load(shape)
                self.assertEqual(
                    len(record_of(shape)["nodes"]), len(engine.nodes_of(state))
                )


class TheEnginesOwnVerdictDecidesNothing(unittest.TestCase):
    """Exit criterion: a run the engine calls Succeeded over an exclusion is not a success."""

    def test_a_clean_engine_success_over_an_excluded_step_is_green_with_exclusions(
        self,
    ) -> None:
        """I5's regression test: the engine's verdict and Cairn's, contradicting, in one place."""
        record = record_of("green-with-exclusions")
        self.assertEqual(record["engine_run_status_name"], engine.RUN_SUCCEEDED)
        self.assertEqual(record["verdict"], VERDICT_GREEN_WITH_EXCLUSIONS)
        self.assertTrue(record["engine_contradicted"])
        self.assertNotEqual(record["exit_code"], EXIT_GREEN)

    def test_the_recorded_exclusion_really_is_a_clean_success_with_no_failed_node(
        self,
    ) -> None:
        """The premise, asserted against the engine's own file rather than against Cairn."""
        state, _, _ = load("green-with-exclusions")
        self.assertEqual(state["status"], 4)
        self.assertFalse(
            any(node.get("status") == 2 for node in engine.nodes_of(state)),
            "the fixture stopped being the pathological shape it was recorded for",
        )

    def test_three_runs_the_engine_spells_identically_reach_three_verdicts(self) -> None:
        """`green`, `all-no-op` and `blocked` are all engine status 4 and none is the same run."""
        verdicts: dict[str, str] = {}
        for shape in ("green", "all-no-op", "blocked"):
            record = record_of(shape)
            self.assertEqual(record["engine_run_status"], 4)
            verdicts[shape] = record["verdict"]
        self.assertEqual(
            verdicts,
            {
                "green": VERDICT_GREEN,
                "all-no-op": VERDICT_ALL_NO_OP,
                "blocked": VERDICT_BLOCKED,
            },
        )

    def test_the_verdict_is_unmoved_by_the_engines_run_status(self) -> None:
        """Rewrite the engine's own verdict and the walk over the nodes reaches the same one."""
        state, reports, run_id = load("green-with-exclusions")
        for status in (2, 4, 6):
            with self.subTest(engine_status=status):
                record = extract({**state, "status": status}, reports, run_id=run_id)
                self.assertEqual(record["verdict"], VERDICT_GREEN_WITH_EXCLUSIONS)

    def test_a_queued_run_is_pending_work_and_never_an_outcome(self) -> None:
        """Everything triggered externally arrives this way, with every node at not-started."""
        state, reports, run_id = load("green")
        queued = {
            **state,
            "status": 5,
            "finishedAt": "",
            "nodes": [
                {**node, "status": 0, "startedAt": "", "finishedAt": ""}
                for node in engine.nodes_of(state)
            ],
        }
        record = extract(queued, reports, run_id=run_id)
        self.assertEqual(record["engine_run_status_name"], engine.RUN_QUEUED)
        self.assertEqual(record["verdict"], VERDICT_RUNNING)
        self.assertEqual(record["next_action"]["action"], "start_scheduler")


class NothingIsLostAndNothingIsAssumed(unittest.TestCase):
    """A node the record cannot name, and a run that produced no step at all."""

    def test_a_node_the_engine_named_nothing_still_reaches_the_record(self) -> None:
        """A node dropped for being unnameable is one whose failure nothing can report."""
        state, reports, run_id = load("green")
        damaged: dict[str, Any] = {
            **state,
            "nodes": [
                *engine.nodes_of(state),
                {"step": {}, "status": 2, "error": "exit status 5"},
            ],
        }
        record = extract(damaged, reports, run_id=run_id)
        self.assertEqual(len(record["nodes"]), len(engine.nodes_of(damaged)))
        self.assertIn(2, [node["status"] for node in record["nodes"]])
        self.assertEqual(record["verdict"], VERDICT_FAILED)

    def test_a_run_that_produced_no_step_is_never_green(self) -> None:
        """Every verdict is a statement about steps, so a run with none has not succeeded."""
        state, _, run_id = load("green")
        empty = {**state, "nodes": [{"step": {"name": "wat"}, "status": 4}]}
        record = extract(empty, {}, run_id=run_id)
        self.assertEqual(record["steps"], [])
        self.assertEqual(record["verdict"], VERDICT_FAILED)
        self.assertNotEqual(record["exit_code"], EXIT_GREEN)

    def test_the_latest_attempt_is_chosen_by_moment_rather_than_by_text(self) -> None:
        """Two attempts either side of an offset change sort by text in the wrong order."""
        # 23:30Z on the 31st, and 05:00Z on the 1st. The second really is later.
        earlier = Attempt(Path("earlier"), {}, "2026-01-01T00:30:00+01:00")
        later = Attempt(Path("later"), {}, "2026-01-01T00:00:00-05:00")
        self.assertGreater(
            earlier.started_at,
            later.started_at,
            "the pair is pointless unless their text sorts the wrong way round",
        )
        self.assertEqual(
            [attempt.path.name for attempt in sorted([later, earlier], key=began)],
            ["earlier", "later"],
        )


class ARunIsReadableWithNothingRunning(unittest.TestCase):
    """Exit criterion: live and compacted read the same way, including after a kill."""

    def test_one_extraction_reads_a_live_multi_line_file_and_a_compacted_one(self) -> None:
        live = (CORPUS / "mid-run" / "status.jsonl").read_text(encoding="utf-8")
        finished = (CORPUS / "green" / "status.jsonl").read_text(encoding="utf-8")
        self.assertGreater(len(live.strip().splitlines()), 1, "the live fixture compacted")
        self.assertEqual(len(finished.strip().splitlines()), 1)
        for shape in ("mid-run", "green"):
            with self.subTest(shape=shape):
                self.assertIn(record_of(shape)["verdict"], VERDICT_PRECEDENCE)

    def test_the_last_line_that_parses_is_the_record(self) -> None:
        """A kill can cut a line mid-character; the previous whole snapshot still stands."""
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "status.jsonl"
            original = (CORPUS / "green" / "status.jsonl").read_text(encoding="utf-8")
            path.write_text(original + '{"dagRunId": "trunc', encoding="utf-8")
            state = last_record(path)
            self.assertIsNotNone(state)
            self.assertEqual(cast(dict[str, Any], state)["status"], 4)

    def test_a_killed_runs_record_does_not_say_the_run_is_running(self) -> None:
        """Task 13: the engine reports the killed run as running, and Cairn's record does not."""
        state, _, _ = load("crashed")
        self.assertEqual(state["status"], STATUS_RUNNING)
        self.assertFalse(state.get("finishedAt"))

        record = record_of("crashed")
        self.assertEqual(record["verdict"], VERDICT_FAILED)
        self.assertIs(record["owner_alive"], False)
        self.assertNotIn(
            OUTCOME_RUNNING, {step["outcome"] for step in record["steps"]}
        )

    def test_a_killed_step_states_what_was_lost_rather_than_defaulting_it(self) -> None:
        record = record_of("crashed")
        killed = record["steps"][0]
        self.assertEqual(killed["cause"], ORCHESTRATOR_DIED)
        for field in ("finished_at", "exit_code", "cost_usd", "turns", "session_id"):
            with self.subTest(field=field):
                self.assertIsNone(killed[field])
                self.assertEqual(killed["provenance"][field], PROVENANCE_ABSENT)

    def test_liveness_comes_from_the_process_and_never_from_the_status_field(self) -> None:
        """The same bytes, two verdicts, decided only by whether the recorded owner lives."""
        state, reports, run_id = load("mid-run")
        dead = extract(state, reports, run_id=run_id)
        live = extract(alive_copy(state), reports, run_id=run_id)
        self.assertEqual(state["status"], STATUS_RUNNING)
        self.assertEqual(dead["verdict"], VERDICT_FAILED)
        self.assertEqual(live["verdict"], VERDICT_RUNNING)

    def test_a_reader_that_cannot_look_reports_a_live_run_as_running(self) -> None:
        """The dogfood fault ([23 A]): a sandboxed shell answered failed, orchestrator_died,
        rerun — over a run that was mid-step and went on to verify and commit."""
        state, reports, run_id = load("mid-run")
        with patch("cairn.liveness.process_start_time", return_value=None):
            record = extract(alive_copy(state), reports, run_id=run_id)
        self.assertIsNone(record["owner_alive"])
        self.assertEqual(record["provenance"]["owner_alive"], PROVENANCE_ABSENT)
        self.assertEqual(record["verdict"], VERDICT_RUNNING)
        self.assertEqual(record["next_action"]["action"], NEXT_WAIT)
        self.assertNotIn(ORCHESTRATOR_DIED, {step["cause"] for step in record["steps"]})
        self.assertNotIn(OUTCOME_FAILED, {step["outcome"] for step in record["steps"]})

    def test_a_sibling_not_yet_started_is_pending_and_a_step_behind_a_halt_is_not_reached(
        self,
    ) -> None:
        state, reports, run_id = load("mid-run")
        outcomes = {
            step["step_id"]: step["outcome"]
            for step in extract(alive_copy(state), reports, run_id=run_id)["steps"]
        }
        self.assertEqual(outcomes["alpha"], OUTCOME_RUNNING)
        self.assertEqual(outcomes["gamma"], OUTCOME_PENDING)
        halted = {
            step["step_id"]: step["outcome"] for step in record_of("red")["steps"]
        }
        self.assertEqual(halted["beta"], OUTCOME_NOT_REACHED)


TIMEOUT_SENTENCE = (
    "step timed out after 2h30m0.041s (timeout: 2h30m0s): context deadline exceeded"
)


def killed_chain(
    *, mark_cause: str | None = NOT_REACHED, assertion_status: int = 4
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """The measured shape ([22]): a work node the engine killed, its assertion run after it,
    and the gate closing over the absent report."""
    state: dict[str, Any] = {
        "dagRunId": "run-killed",
        "name": "plan",
        "status": 6,
        "startedAt": "2026-08-28T15:24:43+01:00",
        "finishedAt": "2026-08-29T06:05:00+01:00",
        "paramsList": ["CAIRN_REPOSITORY=/srv/work/product"],
        "nodes": [
            {
                "step": {"name": "work_alpha"},
                "status": 2,
                "error": TIMEOUT_SENTENCE,
                "startedAt": "2026-08-29T03:31:14+01:00",
                "finishedAt": "2026-08-29T06:01:14+01:00",
            },
            {"step": {"name": "verify_alpha", "depends": ["work_alpha"]}, "status": assertion_status},
            {"step": {"name": "mark_alpha", "depends": ["verify_alpha"]}, "status": 5},
            {"step": {"name": "commit_alpha", "depends": ["mark_alpha"]}, "status": 5},
        ],
    }
    reports: dict[str, dict[str, Any]] = {}
    if mark_cause is not None:
        reports["mark_alpha"] = {
            "step_id": "mark_alpha",
            "run_id": "run-killed",
            "status": "failed",
            "cause": mark_cause,
            "summary": "the gate closed",
            "needs_user_decision": False,
            "detail": {"position": "chain", "verify_exit": 0, "reported": None},
        }
    return state, reports


class AStepTheEngineKilledIsTimedOut(unittest.TestCase):
    """[22 A]: a step that landed four commits and passed its assertion was recorded as one
    that never ran, beside an engine node saying it timed out after 2h30m."""

    def test_the_engines_kill_outranks_the_gates_absent_report_reading(self) -> None:
        outcome, overlays, cause = classify_step(
            work_status=engine.NODE_STATUS_FAILED,
            mark_status=engine.NODE_STATUS_SKIPPED,
            work_report=None,
            mark_report={"cause": NOT_REACHED},
            has_assertion=True,
            run_settled=True,
            orchestrator_gone=False,
            engine_killed=True,
        )
        self.assertEqual((outcome, cause), (OUTCOME_FAILED, TIMED_OUT))
        self.assertNotIn(OVERLAY_DIVERGENCE, overlays)

    def test_a_cause_the_gate_established_on_its_own_evidence_is_never_overridden(self) -> None:
        for own_cause in (VERIFY_FAILED, REPORTED_FAILURE):
            with self.subTest(cause=own_cause):
                _, _, cause = classify_step(
                    work_status=engine.NODE_STATUS_FAILED,
                    mark_status=engine.NODE_STATUS_SKIPPED,
                    work_report=None,
                    mark_report={"cause": own_cause},
                    has_assertion=True,
                    run_settled=True,
                    orchestrator_gone=False,
                    engine_killed=True,
                )
                self.assertEqual(cause, own_cause)

    def test_a_step_that_reported_is_read_from_its_report_however_the_engine_ended_it(
        self,
    ) -> None:
        _, _, cause = classify_step(
            work_status=engine.NODE_STATUS_FAILED,
            mark_status=engine.NODE_STATUS_SKIPPED,
            work_report={"status": "failed", "needs_user_decision": False},
            mark_report={"cause": REPORTED_FAILURE},
            has_assertion=True,
            run_settled=True,
            orchestrator_gone=False,
            engine_killed=True,
        )
        self.assertEqual(cause, REPORTED_FAILURE)

    def test_the_record_carries_the_bound_the_elapsed_time_and_the_assertions_verdict(
        self,
    ) -> None:
        """The assertion of a killed step runs only where its own gate faulted open, which
        is the one way an emitted workflow produces this. Where it did, its verdict is
        weighed against the session nobody heard from."""
        state, reports = killed_chain()
        record = extract(state, reports, run_id="run-killed")
        step = record["steps"][0]
        self.assertEqual(step["outcome"], OUTCOME_FAILED)
        self.assertEqual(step["cause"], TIMED_OUT)
        self.assertEqual(step["timeout_seconds"], 9000)
        self.assertAlmostEqual(cast(float, step["elapsed_seconds"]), 9000.041)
        self.assertEqual(step["provenance"]["timeout_seconds"], "derived")
        self.assertEqual(step["divergence"], {"reported": REPORTED_KILLED, "asserted": True})
        self.assertIn(OVERLAY_DIVERGENCE, step["overlays"])
        self.assertEqual(step["provenance"]["divergence"], "derived")
        self.assertEqual(record["verdict"], VERDICT_FAILED)
        summaries = {item["kind"]: item["summary"] for item in record["attention"]}
        self.assertIn("9000 s bound", summaries["failure"])
        self.assertIn("stopped at its bound", summaries["divergence"])
        self.assertIn("passed over the work it left", summaries["divergence"])

    def test_an_assertion_that_never_ran_leaves_nothing_to_weigh(self) -> None:
        """The ordinary shape of a killed step: its assertion's gate declined for want of
        the report the kill prevented, so there is no second account to weigh."""
        state, reports = killed_chain(assertion_status=5)
        step = extract(state, reports, run_id="run-killed")["steps"][0]
        self.assertEqual(step["cause"], TIMED_OUT)
        self.assertIsNone(step["divergence"])
        self.assertNotIn(OVERLAY_DIVERGENCE, step["overlays"])

    def test_a_gate_that_never_ran_still_reads_the_kill(self) -> None:
        state, reports = killed_chain(mark_cause=None)
        step = extract(state, reports, run_id="run-killed")["steps"][0]
        self.assertEqual((step["outcome"], step["cause"]), (OUTCOME_FAILED, TIMED_OUT))

    def test_the_recorded_kill_reads_as_the_measured_fault_repaired(self) -> None:
        """The `timed-out` corpus shape, recorded from the engine with the gates the
        emitter writes: the killed step is `timed_out` carrying the bound that fired and
        how long it ran, the step behind it is `not_reached`, and the subject of what to do
        next is the killed step. It carries no assertion verdict, because the gate that
        decides whether an assertion runs turns on the same absent report the kill caused
        ([24 B]) — a shape, not a gap."""
        record = record_of("timed-out")
        by_id = {step["step_id"]: step for step in record["steps"]}
        alpha, beta = by_id["alpha"], by_id["beta"]
        self.assertEqual((alpha["outcome"], alpha["cause"]), (OUTCOME_FAILED, TIMED_OUT))
        self.assertEqual(alpha["timeout_seconds"], 2)
        self.assertGreaterEqual(cast(float, alpha["elapsed_seconds"]), 2.0)
        self.assertIsNone(alpha["divergence"])
        self.assertIsNone(alpha["assertion_exit"])
        self.assertEqual((beta["outcome"], beta["cause"]), (OUTCOME_NOT_REACHED, NOT_REACHED))
        self.assertEqual(record["verdict"], VERDICT_FAILED)
        self.assertEqual(record["next_action"]["action"], NEXT_RERUN)
        self.assertEqual(record["next_action"]["subject"], "alpha")
        self.assertIsNotNone(record["next_action"]["command"])
        failures = [item for item in record["attention"] if item["kind"] == "failure"]
        self.assertEqual([item["subject"] for item in failures], ["alpha", "beta"])
        self.assertIn("2 s bound", failures[0]["summary"])
        self.assertEqual(failures[1]["summary"], "never reached behind alpha")

    def test_a_run_that_halted_in_two_places_names_neither_as_the_others_cause(self) -> None:
        """One collapsed line for the steps a halt left behind is the right shape for one
        halt. Two independent halts leave two sets behind, and a line naming the first
        would tell a person the other branch stopped for a reason it did not."""
        state, reports = halted_chain(("alpha", "beta"), fails_at="alpha")
        other, more = halted_chain(("mike", "zulu"), fails_at="mike")
        state["nodes"].extend(other["nodes"])
        reports.update(more)
        record = extract(state, reports, run_id="run-halt")
        unreached = [
            item
            for item in record["attention"]
            if item["kind"] == "failure" and item["cause"] == NOT_REACHED
        ]
        self.assertEqual(len(unreached), 1)
        self.assertNotIn("behind", unreached[0]["summary"])
        self.assertIn("never reached", unreached[0]["summary"])

    def test_a_step_left_uncommitted_work_is_named_in_its_own_record(self) -> None:
        """[21]'s second half: the excluded path is in the step's record and raises a
        follow-up, so a commit reads as scoped without diffing it against a transcript."""
        state, reports = halted_chain(("alpha",), fails_at="alpha")
        reports["commit_alpha"] = {
            "step_id": "commit_alpha", "run_id": "run-halt", "status": "done",
            "summary": "committed abc", "needs_user_decision": False,
            "follow_up_work": ["left 1 path(s) uncommitted: eslint.config.js"],
            "detail": {"left_uncommitted": ["eslint.config.js"], "commit": "abc"},
        }
        step = extract(state, reports, run_id="run-halt")["steps"][0]
        self.assertEqual(step["left_uncommitted"], ["eslint.config.js"])
        self.assertIn(
            "left 1 path(s) uncommitted: eslint.config.js", step["follow_up_work"]
        )

    def test_a_waves_steps_are_ordered_by_id_and_never_by_the_node_that_named_them(
        self,
    ) -> None:
        """One level of the node graph holds several steps' nodes, and those nodes sort by
        role before subject: `commit_zulu` precedes `work_alpha`. The order a reader is
        given is the steps', so it is taken after the collapse, not before."""
        nodes = {
            "work_alpha": {"step": {"name": "work_alpha", "depends": []}},
            "work_zulu": {"step": {"name": "work_zulu", "depends": []}},
            "commit_zulu": {"step": {"name": "commit_zulu", "depends": ["work_zulu"]}},
            "work_mike": {"step": {"name": "work_mike", "depends": ["work_alpha"]}},
        }
        self.assertEqual(
            step_order(cast(Any, nodes), ["zulu", "alpha", "mike"]),
            ["alpha", "zulu", "mike"],
        )

    def test_the_record_and_the_engine_node_never_disagree_about_whether_a_step_ran(
        self,
    ) -> None:
        """I-B over the whole corpus: `not_reached` only over a node the engine never
        started or skipped, never over one it recorded as having run and failed."""
        for shape in SHAPES:
            record = record_of(shape)
            statuses = {node["name"]: node["status"] for node in record["nodes"]}
            for step in record["steps"]:
                work = statuses.get(f"work_{step['step_id']}")
                with self.subTest(shape=shape, step=step["step_id"]):
                    if step["outcome"] == OUTCOME_NOT_REACHED:
                        # The node has to be there to be judged: a step read as never
                        # reached over a node the record cannot find is the same claim
                        # made without evidence.
                        self.assertIsNotNone(work)
                        self.assertIn(
                            work,
                            (
                                engine.NODE_STATUS_NOT_STARTED,
                                engine.NODE_STATUS_ABORTED,
                                engine.NODE_STATUS_SKIPPED,
                            ),
                        )


class EveryAbsentFieldCarriesProvenance(unittest.TestCase):
    """Exit criterion: every absent field carries provenance rather than a plausible default."""

    def _sections(self, record: RunRecord) -> list[tuple[str, dict[str, Any]]]:
        found: list[tuple[str, dict[str, Any]]] = [("run", cast(dict[str, Any], record))]
        for step in record["steps"]:
            found.append((f"step {step['step_id']}", cast(dict[str, Any], step)))
        for node in record["nodes"]:
            found.append((f"node {node['name']}", cast(dict[str, Any], node)))
        for item in record["infrastructure"]:
            found.append((f"infra {item['name']}", cast(dict[str, Any], item)))
        found.append(("budget", cast(dict[str, Any], record["budget"])))
        found.append(("git", cast(dict[str, Any], record["git"])))
        found.append(("trigger", cast(dict[str, Any], record["trigger"])))
        found.append(("lineage", cast(dict[str, Any], record["lineage"])))
        return found

    def test_a_value_is_none_exactly_where_its_provenance_says_absent(self) -> None:
        for shape in SHAPES:
            for label, section in self._sections(record_of(shape)):
                marks = cast(dict[str, str], section["provenance"])
                for field, mark in marks.items():
                    if mark != PROVENANCE_ABSENT:
                        continue
                    with self.subTest(shape=shape, section=label, field=field):
                        self.assertIsNone(section[field])

    def test_every_none_value_is_listed_as_absent(self) -> None:
        """The converse, and the half that actually protects the criterion.

        A field silently missing from its own provenance map reads to a renderer as a value
        that was measured and happened to be nothing, which is exactly the plausible default
        the criterion forbids.
        """
        for shape in SHAPES:
            for label, section in self._sections(record_of(shape)):
                marks = cast(dict[str, str], section["provenance"])
                for field, value in section.items():
                    if field == "provenance" or value is not None:
                        continue
                    with self.subTest(shape=shape, section=label, field=field):
                        self.assertEqual(marks.get(field), PROVENANCE_ABSENT)

    def test_a_reported_string_cannot_reach_the_record_unbounded(self) -> None:
        """A report's detail is an agent's own output, and the session id is pasted into a
        command a person runs."""
        state, reports, run_id = load("green")
        loud = {name: dict(report) for name, report in reports.items()}
        loud["work_alpha"] = {
            **loud["work_alpha"],
            "detail": {"session_id": "z" * 100_000, "model": "m" * 9_000},
        }
        step = extract(state, loud, run_id=run_id)["steps"][0]
        self.assertLessEqual(len(cast(str, step["session_id"])), LINE_LIMIT)
        self.assertLessEqual(len(cast(str, step["model"])), LINE_LIMIT)

    def test_every_provenance_entry_names_a_real_field(self) -> None:
        for shape in SHAPES:
            for label, section in self._sections(record_of(shape)):
                for field in cast(dict[str, str], section["provenance"]):
                    with self.subTest(shape=shape, section=label, field=field):
                        self.assertIn(field, section)

    def test_every_provenance_value_is_one_of_the_three(self) -> None:
        for shape in SHAPES:
            for _, section in self._sections(record_of(shape)):
                for mark in cast(dict[str, str], section["provenance"]).values():
                    self.assertIn(mark, PROVENANCES)

    def test_the_exit_code_is_never_recorded_because_the_engine_never_records_one(
        self,
    ) -> None:
        for shape in SHAPES:
            for node in record_of(shape)["nodes"]:
                with self.subTest(shape=shape, node=node["name"]):
                    self.assertIn(
                        node["provenance"].get("exit_code"),
                        ("derived", PROVENANCE_ABSENT),
                    )

    def test_an_absent_actor_means_cairn_started_it_and_is_never_unknown(self) -> None:
        trigger = record_of("green")["trigger"]
        self.assertIsNone(trigger["actor"])
        self.assertTrue(trigger["started_by_cairn"])
        self.assertEqual(trigger["provenance"]["actor"], PROVENANCE_ABSENT)
        self.assertNotEqual(trigger["kind"], "unknown")


class TheExitCodeIsItsOwnContract(unittest.TestCase):
    """Task 6: specified independently of the display verdict."""

    def test_every_record_carries_the_frozen_code_for_its_verdict(self) -> None:
        for shape in SHAPES:
            with self.subTest(shape=shape):
                record = record_of(shape)
                self.assertEqual(
                    record["exit_code"], VERDICT_EXIT_CODES[record["verdict"]]
                )

    def test_a_run_with_exclusions_and_a_clean_run_exit_differently(self) -> None:
        """The automation half of I5: the engine exits 0 for both and Cairn does not."""
        self.assertEqual(record_of("green")["exit_code"], EXIT_GREEN)
        self.assertNotEqual(
            record_of("green-with-exclusions")["exit_code"], EXIT_GREEN
        )

    def test_a_no_op_run_and_a_green_run_are_the_same_to_automation(self) -> None:
        """Different verdicts, one code: no automation can act on the difference."""
        self.assertEqual(record_of("all-no-op")["exit_code"], EXIT_GREEN)


class TheNextCommandWorksWhenItIsPasted(unittest.TestCase):
    """A command that fails when pasted is worse than a report that carries none.

    The command is the skill's own recovery offer and never `dagu retry`, which SKILL.md
    refuses outright: re-running a plan is the whole recovery story, and a continued
    occasion is what makes it cheap.
    """

    def test_the_rerun_carries_the_recovery_offer_for_this_run(self) -> None:
        record = record_of("red")
        command = record["next_action"]["command"]
        assert command is not None
        self.assertEqual(
            shlex.split(command),
            [
                "python3",
                "-m",
                "cairn",
                "run",
                "offer",
                "--plan",
                record["plan"],
                "--repository",
                record["git"]["repository"],
                "--trigger",
                "recovery",
                "--recovering",
                record["run_id"],
            ],
        )

    def test_a_run_that_does_not_name_its_plan_carries_no_command_at_all(self) -> None:
        state, reports, run_id = load("red")
        record = extract({**state, "name": ""}, reports, run_id=run_id)
        self.assertEqual(record["next_action"]["action"], NEXT_RERUN)
        self.assertIsNone(record["next_action"]["command"])

    def test_a_run_that_does_not_name_its_repository_carries_no_command_at_all(self) -> None:
        state, reports, run_id = load("red")
        record = extract({**state, "paramsList": []}, reports, run_id=run_id)
        self.assertEqual(record["next_action"]["action"], NEXT_RERUN)
        self.assertIsNone(record["next_action"]["command"])

    def test_a_killed_run_still_carries_its_recovery_offer(self) -> None:
        """`dagu retry` refused a run the engine still called running; the recovery offer
        is a fresh start that reclaims the dead run's lock, so it is carried."""
        record = record_of("crashed")
        self.assertEqual(record["next_action"]["action"], NEXT_RERUN)
        self.assertIsNotNone(record["next_action"]["command"])

    def test_a_run_and_plan_carrying_shell_metacharacters_stay_one_argument_each(
        self,
    ) -> None:
        state, reports, _ = load("red")
        record = extract({**state, "name": "a b; rm -rf /"}, reports, run_id="r;m")
        command = record["next_action"]["command"]
        assert command is not None
        words = shlex.split(command)
        self.assertEqual(words[words.index("--plan") + 1], "a b; rm -rf /")
        self.assertEqual(words[words.index("--recovering") + 1], "r;m")

    def test_no_record_ever_carries_the_engines_own_retry(self) -> None:
        for shape in SHAPES:
            with self.subTest(shape=shape):
                command = record_of(shape)["next_action"]["command"]
                self.assertNotIn("dagu retry", command or "")


def halted_chain(
    step_ids: tuple[str, ...], *, fails_at: str, assertion_log: str | None = None
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """A chain the emitter's own pattern leaves behind a failed assertion: the halted step
    is excluded `verify_failed`, and every step behind it has its work node skipped with
    its gate closed `not_reached` ([23 B]'s reproduction)."""
    nodes: list[dict[str, Any]] = []
    reports: dict[str, dict[str, Any]] = {}
    previous = None
    halted = False
    for step_id in step_ids:
        after = [] if previous is None else [previous]
        if not halted and step_id != fails_at:
            statuses = {"work": 4, "verify": 4, "mark": 4, "commit": 4}
        elif not halted:
            statuses = {"work": 4, "verify": 2, "mark": 5, "commit": 5}
            reports[f"mark_{step_id}"] = {
                "step_id": f"mark_{step_id}", "run_id": "run-halt", "status": "failed",
                "cause": VERIFY_FAILED, "summary": "the assertion exited 1",
                "needs_user_decision": False,
                "detail": {"position": "chain", "verify_exit": 1, "reported": "done",
                           "divergence": {"reported": "done", "asserted": False}},
            }
            halted = True
        else:
            statuses = {"work": 5, "verify": 5, "mark": 5, "commit": 5}
            reports[f"mark_{step_id}"] = {
                "step_id": f"mark_{step_id}", "run_id": "run-halt", "status": "failed",
                "cause": NOT_REACHED, "summary": "the step left no report of this run",
                "needs_user_decision": False,
                "detail": {"position": "chain", "verify_exit": None, "reported": None},
            }
        if statuses["work"] == 4:
            reports[f"work_{step_id}"] = {
                "step_id": f"work_{step_id}", "run_id": "run-halt", "status": "done",
                "summary": f"did {step_id}", "needs_user_decision": False, "detail": {},
            }
        for role in ("work", "verify", "mark", "commit"):
            node: dict[str, Any] = {
                "step": {"name": f"{role}_{step_id}", "depends": after},
                "status": statuses[role],
            }
            if role == "verify" and statuses[role] == 2:
                node["error"] = "exit status 1"
                if assertion_log is not None:
                    node["stdout"] = assertion_log
            nodes.append(node)
            after = [f"{role}_{step_id}"]
        previous = f"commit_{step_id}"
    state: dict[str, Any] = {
        "dagRunId": "run-halt",
        "name": "plan",
        "status": 6,
        "paramsList": ["CAIRN_REPOSITORY=/srv/work/product"],
        "nodes": [{"step": {"name": "lock_acquire"}, "status": 4}, *nodes],
    }
    state["nodes"][1]["step"]["depends"] = ["lock_acquire"]
    return state, reports


class TheHeadlineNamesTheFault(unittest.TestCase):
    """[23 B]: after one gate closed, the record named a bystander eleven steps downstream,
    prescribed settling a merge the chain does not have, and buried the one line that
    differed under fourteen identical ones."""

    def test_a_chain_of_three_whose_middle_assertion_fails_names_the_middle_step(self) -> None:
        state, reports = halted_chain(("a", "b", "c"), fails_at="b")
        record = extract(state, reports, run_id="run-halt")
        outcomes = {step["step_id"]: step["outcome"] for step in record["steps"]}
        self.assertEqual(outcomes, {"a": OUTCOME_VERIFIED, "b": OUTCOME_EXCLUDED, "c": OUTCOME_NOT_REACHED})
        self.assertEqual(record["verdict"], VERDICT_FAILED)
        self.assertEqual(record["exit_code"], VERDICT_EXIT_CODES[VERDICT_FAILED])
        self.assertEqual(record["next_action"]["action"], NEXT_RERUN)
        self.assertEqual(record["next_action"]["subject"], "b")
        self.assertIsNotNone(record["next_action"]["command"])

    def test_the_subject_is_first_in_dependency_order_and_never_first_by_name(self) -> None:
        state, reports = halted_chain(("zulu", "alpha", "mike"), fails_at="zulu")
        record = extract(state, reports, run_id="run-halt")
        self.assertEqual(record["next_action"]["subject"], "zulu")
        self.assertEqual([step["step_id"] for step in record["steps"]], ["alpha", "mike", "zulu"])

    def test_the_steps_a_halt_left_behind_are_one_attention_item_naming_the_halt(self) -> None:
        state, reports = halted_chain(tuple(f"s{n:02d}" for n in range(1, 16)), fails_at="s02")
        record = extract(state, reports, run_id="run-halt")
        failures = [item for item in record["attention"] if item["kind"] == "failure"]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["subject"], "s03")
        self.assertEqual(failures[0]["cause"], NOT_REACHED)
        self.assertIn("behind s02", failures[0]["summary"])
        self.assertIn("with 12 more", failures[0]["summary"])
        excluded = [item for item in record["attention"] if item["kind"] == "excluded"]
        self.assertEqual([item["subject"] for item in excluded], ["s02"])
        self.assertEqual(
            sum(1 for step in record["steps"] if step["outcome"] == OUTCOME_NOT_REACHED), 13
        )

    def test_a_merge_less_run_with_an_excluded_last_step_is_rerun_not_settled(self) -> None:
        state, reports = halted_chain(("a", "b"), fails_at="b")
        record = extract(state, reports, run_id="run-halt")
        self.assertEqual(record["verdict"], VERDICT_GREEN_WITH_EXCLUSIONS)
        self.assertEqual(record["next_action"]["action"], NEXT_RERUN)
        self.assertEqual(record["next_action"]["subject"], "b")

    def test_a_run_whose_topology_holds_a_merge_still_settles_it(self) -> None:
        state, reports = halted_chain(("a", "b"), fails_at="b")
        state["nodes"].append({"step": {"name": "merge_w1_1", "depends": ["commit_b"]}, "status": 4})
        record = extract(state, reports, run_id="run-halt")
        self.assertEqual(record["next_action"]["action"], NEXT_SETTLE_MERGE)

    def test_a_record_with_a_cycle_in_its_edges_still_builds_in_a_stable_order(self) -> None:
        state, reports = halted_chain(("a", "b", "c"), fails_at="b")
        state["nodes"][1]["step"]["depends"] = ["commit_c"]
        first = extract(state, reports, run_id="run-halt")
        second = extract(state, reports, run_id="run-halt")
        self.assertEqual(first["next_action"], second["next_action"])
        self.assertEqual(first["next_action"]["subject"], "b")

    def test_a_failed_assertions_last_lines_are_carried_for_the_report_to_quote(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "verify_b.out"
            log.write_text("x" * 5000 + "\nFAIL  src/thing.test.ts > renders\n", encoding="utf-8")
            state, reports = halted_chain(("a", "b", "c"), fails_at="b", assertion_log=str(log))
            record = extract(state, reports, run_id="run-halt")
        step = next(step for step in record["steps"] if step["step_id"] == "b")
        assert step["assertion_tail"] is not None
        self.assertTrue(step["assertion_tail"].endswith("FAIL  src/thing.test.ts > renders"))
        self.assertLessEqual(len(step["assertion_tail"]), TEXT_LIMIT)
        self.assertNotIn("assertion_tail", step["provenance"])
        untouched = next(step for step in record["steps"] if step["step_id"] == "a")
        self.assertIsNone(untouched["assertion_tail"])
        self.assertEqual(untouched["provenance"]["assertion_tail"], PROVENANCE_ABSENT)

    def test_the_record_names_which_execution_backed_each_gate(self) -> None:
        """[24 A]: provenance says which execution backed a step's assertion."""
        state, reports = halted_chain(("a", "b"), fails_at="c")
        reports["verify_a"] = {
            "step_id": "verify_a", "run_id": "run-halt", "status": "done", "summary": "exited 0",
            "needs_user_decision": False, "cause": None,
            "detail": {"decision": "run", "exit": 0, "source": "executed", "backed_by": "a"},
        }
        reports["verify_b"] = {
            "step_id": "verify_b", "run_id": "run-halt", "status": "noop", "summary": "shared",
            "needs_user_decision": False, "cause": None,
            "detail": {"decision": "shared", "exit": 0, "source": "shared", "backed_by": "a"},
        }
        record = extract(state, reports, run_id="run-halt")
        by_id = {step["step_id"]: step for step in record["steps"]}
        self.assertEqual((by_id["a"]["assertion_exit"], by_id["a"]["assertion_source"]), (0, "executed"))
        self.assertEqual((by_id["b"]["assertion_source"], by_id["b"]["assertion_backed_by"]), ("shared", "a"))
        facts = as_mapping(record)
        self.assertEqual(facts["step.b.assertion_backed_by"], "a")
        self.assertEqual(facts["step.b.assertion_exit"], "0")
        for name in ("assertion_exit", "assertion_source", "assertion_backed_by"):
            self.assertNotIn(name, by_id["a"]["provenance"])

    def test_an_assertion_account_outside_the_vocabulary_is_read_as_absent(self) -> None:
        state, reports = halted_chain(("a",), fails_at="c")
        reports["verify_a"] = {
            "step_id": "verify_a", "run_id": "run-halt", "status": "done", "summary": "x",
            "needs_user_decision": False, "cause": None,
            "detail": {"decision": "run", "exit": True, "source": "guessed", "backed_by": 7},
        }
        step = extract(state, reports, run_id="run-halt")["steps"][0]
        self.assertIsNone(step["assertion_exit"])
        self.assertIsNone(step["assertion_source"])
        self.assertIsNone(step["assertion_backed_by"])
        self.assertEqual(step["provenance"]["assertion_source"], PROVENANCE_ABSENT)

    def test_a_log_that_is_gone_leaves_the_tail_absent_rather_than_failing_the_record(self) -> None:
        state, reports = halted_chain(("a", "b"), fails_at="b", assertion_log="/nowhere/verify_b.out")
        record = extract(state, reports, run_id="run-halt")
        self.assertIsNone(next(step for step in record["steps"] if step["step_id"] == "b")["assertion_tail"])


class TheViewBaseIsSomewhereAReaderCanGo(unittest.TestCase):
    """The link is composed once, in the record, so no surface has to work it out."""

    def test_cairns_own_variable_outranks_the_engines_two(self) -> None:
        self.assertEqual(
            view_base(
                {
                    VIEW_BASE_ENV: "https://dagu.example/",
                    ENGINE_HOST_ENV: "ignored",
                    ENGINE_PORT_ENV: "9999",
                }
            ),
            "https://dagu.example",
        )

    def test_the_engines_own_host_and_port_are_honoured_where_cairn_says_nothing(
        self,
    ) -> None:
        self.assertEqual(view_base({ENGINE_PORT_ENV: "9000"}), "http://127.0.0.1:9000")

    def test_a_base_without_a_scheme_is_refused_rather_than_made_relative(self) -> None:
        """A link that looks like an answer and goes nowhere is worse than the default."""
        self.assertEqual(view_base({VIEW_BASE_ENV: "dagu.example:8080"}), VIEW_BASE_DEFAULT)
        self.assertEqual(view_base({VIEW_BASE_ENV: "   "}), VIEW_BASE_DEFAULT)

    def test_a_bind_address_becomes_somewhere_a_browser_can_reach(self) -> None:
        """`DAGU_HOST` is where the server listens, which is not where a reader is."""
        self.assertEqual(view_base({ENGINE_HOST_ENV: "0.0.0.0"}), VIEW_BASE_DEFAULT)
        self.assertEqual(view_base({ENGINE_HOST_ENV: "::"}), VIEW_BASE_DEFAULT)
        self.assertEqual(view_base({ENGINE_HOST_ENV: "::1"}), "http://[::1]:8080")
        self.assertEqual(view_base({ENGINE_HOST_ENV: "host"}), "http://host:8080")

    def test_a_name_or_run_with_a_slash_cannot_reach_outside_its_own_path(self) -> None:
        self.assertEqual(
            view_url("../../admin", "a/b", VIEW_BASE_DEFAULT),
            f"{VIEW_BASE_DEFAULT}/dag-runs/..%2F..%2Fadmin/a%2Fb",
        )

    def test_an_absent_link_is_absent_in_the_provenance_rather_than_empty(self) -> None:
        state, reports, run_id = load("green")
        record = extract({**state, "name": ""}, reports, run_id=run_id)
        self.assertIsNone(record["view_url"])
        self.assertEqual(record["provenance"]["view_url"], PROVENANCE_ABSENT)


class AWavesExclusionsComeFromTheCensus(unittest.TestCase):
    """Task 16: the join is the only honest source, and git is never asked."""

    def test_the_extraction_issues_no_git_command(self) -> None:
        """After the first landing git cannot tell an excluded branch from an empty one."""
        for shape in SHAPES:
            with self.subTest(shape=shape):
                state, reports, run_id = load(shape)
                with patch(
                    "cairn.gitio.git", side_effect=AssertionError("git was consulted")
                ):
                    extract(state, reports, run_id=run_id)

    def test_a_census_is_read_verbatim_rather_than_re_derived(self) -> None:
        state, reports, run_id = load("green-with-exclusions")
        census = {
            "wave": 1,
            "into": "main",
            "arrived": ["step/alpha"],
            "excluded": {"step/beta": {"cause": "verify_failed", "summary": "nothing landed"}},
            "settled": ["step/gamma"],
        }
        reports["join_w1"] = {
            **reports.get("join_w1", {"run_id": run_id, "status": "done"}),
            "detail": census,
        }
        record = extract(state, reports, run_id=run_id)
        self.assertEqual(len(record["waves"]), 1)
        wave = record["waves"][0]
        self.assertEqual(wave["arrived"], ["step/alpha"])
        self.assertEqual(wave["settled"], ["step/gamma"])
        self.assertEqual(
            wave["excluded"],
            [{"branch": "step/beta", "cause": "verify_failed", "summary": "nothing landed"}],
        )
        self.assertEqual(record["git"]["excluded"], ["step/beta"])

    def test_a_branch_already_contained_in_the_parent_is_never_an_exclusion(self) -> None:
        """It landed earlier, or its step had nothing to commit; neither has a cause."""
        state, reports, run_id = load("green-with-exclusions")
        reports["join_w1"] = {
            "run_id": run_id,
            "status": "done",
            "detail": {"wave": 1, "into": "main", "arrived": [], "excluded": {},
                       "settled": ["step/beta"]},
        }
        record = extract(state, reports, run_id=run_id)
        self.assertEqual(record["waves"][0]["excluded"], [])
        self.assertEqual(record["git"]["excluded"], [])

    def _green_run_whose_census_declined_a_branch(self) -> RunRecord:
        """Every step verified, and the join still declined one of their branches.

        Reachable without anything hostile: the join reads a step's gate *report* while the
        verdict walks the gate's *node*, so one damaged report file declines a branch the
        walk reads as verified.
        """
        state, reports, run_id = load("green")
        reports["join_w1"] = {
            "run_id": run_id,
            "status": "done",
            "step_id": "join_w1",
            "summary": "wave 1: 1 of 2 branches carry work to land",
            "needs_user_decision": False,
            "follow_up_work": [],
            "cause": None,
            "detail": {
                "wave": 1,
                "into": "main",
                "arrived": ["step/alpha"],
                "excluded": {
                    "step/beta": {
                        "cause": "gate_indeterminate",
                        "summary": "beta left a gate report that cannot be read",
                    }
                },
                "settled": [],
            },
        }
        return extract(state, reports, run_id=run_id)

    def test_a_census_exclusion_is_never_a_clean_success(self) -> None:
        """I5 over the one exclusion no step outcome can speak for."""
        record = self._green_run_whose_census_declined_a_branch()
        self.assertEqual(record["verdict"], VERDICT_GREEN_WITH_EXCLUSIONS)
        self.assertEqual(record["exit_code"], VERDICT_EXIT_CODES[VERDICT_GREEN_WITH_EXCLUSIONS])
        self.assertTrue(record["engine_contradicted"])
        self.assertEqual([step["outcome"] for step in record["steps"]], ["verified"] * 2)

    def test_a_census_exclusion_reaches_attention_naming_its_branch_and_cause(self) -> None:
        record = self._green_run_whose_census_declined_a_branch()
        excluded = [item for item in record["attention"] if item["kind"] == "excluded"]
        self.assertEqual([item["subject"] for item in excluded], ["step/beta"])
        self.assertEqual(excluded[0]["cause"], "gate_indeterminate")

    def test_a_census_exclusion_leaves_a_merge_to_settle(self) -> None:
        record = self._green_run_whose_census_declined_a_branch()
        self.assertEqual(record["next_action"]["action"], NEXT_SETTLE_MERGE)
        self.assertEqual(record["next_action"]["subject"], "step/beta")


class ANoOpNamesWhoDidTheWork(unittest.TestCase):
    """Task 8: lineage, and task 3's scope-and-key on a no-op."""

    def test_a_no_op_carries_the_scope_whose_key_still_matched(self) -> None:
        for step in record_of("all-no-op")["steps"]:
            with self.subTest(step=step["step_id"]):
                self.assertEqual(step["outcome"], OUTCOME_NO_OP)
                freshness = step["freshness"]
                self.assertIsNotNone(freshness)
                self.assertEqual(cast(dict[str, str], freshness)["recorded_scope"], "once")

    def test_a_no_op_names_the_earlier_run_that_completed_it(self) -> None:
        record = record_of("all-no-op")
        self.assertEqual(
            record["lineage"]["completed_by"],
            {"alpha": "fixture-earlierrun", "beta": "fixture-earlierrun"},
        )
        self.assertEqual(record["lineage"]["previous_runs"], ["fixture-earlierrun"])

    def test_a_no_op_run_re_emits_no_follow_up_work(self) -> None:
        """Follow-ups are a per-run snapshot: no-op steps do not redo work, so they find none."""
        record = record_of("all-no-op")
        self.assertEqual([step["follow_up_work"] for step in record["steps"]], [[], []])
        self.assertNotIn(
            "follow_up", {item["kind"] for item in record["attention"]}
        )

    def test_a_run_with_no_lineage_still_extracts(self) -> None:
        """A missing or corrupt lineage never changes what a run does."""
        record = record_of("green")
        self.assertEqual(record["lineage"]["completed_by"], {})
        self.assertEqual(record["lineage"]["previous_runs"], [])


class AttentionComesInOneOrder(unittest.TestCase):
    """Task 5: every renderer conforms to one definition rather than inventing a subset."""

    def test_attention_items_come_in_the_frozen_order(self) -> None:
        for shape in SHAPES:
            with self.subTest(shape=shape):
                kinds = [item["kind"] for item in record_of(shape)["attention"]]
                self.assertEqual(kinds, sorted(kinds, key=ATTENTION_ORDER.index))

    def test_a_blocked_step_is_the_first_thing_a_reader_is_told(self) -> None:
        record = record_of("blocked")
        self.assertEqual(record["attention"][0]["kind"], "blocked")
        self.assertEqual(record["next_action"]["action"], "decide")
        blocked = record["steps"][0]
        self.assertEqual(blocked["outcome"], OUTCOME_EXCLUDED)
        self.assertIn(OVERLAY_BLOCKED, blocked["overlays"])

    def test_every_attention_kind_is_one_of_the_seven(self) -> None:
        for shape in SHAPES:
            for item in record_of(shape)["attention"]:
                self.assertIn(item["kind"], ATTENTION_ORDER)


class UntrustedTextIsNormalisedOnceAndEscapedNowhere(unittest.TestCase):
    """Task 10: one pass here, context-specific escaping at each sink."""

    def test_control_characters_never_reach_the_record(self) -> None:
        self.assertEqual(flatten("clean\x1b[31m red\x00"), "clean[31m red")

    def test_a_bidi_override_is_stripped(self) -> None:
        """A path that renders as its own opposite is a lie the reader cannot see."""
        self.assertEqual(normalise("gpj.\u202egnp.exe"), "gpj.gnp.exe")

    def test_a_line_separator_never_survives(self) -> None:
        """It ends a statement in a script context and splits `str.splitlines`."""
        self.assertNotIn("\u2028", normalise("before\u2028after"))

    def test_a_summary_is_one_line_and_capped(self) -> None:
        self.assertEqual(flatten("one\ntwo\tthree"), "one two three")
        self.assertEqual(len(flatten("x" * 500)), 200)

    def test_text_that_keeps_its_shape_keeps_only_newline_and_tab(self) -> None:
        self.assertEqual(normalise("a\nb\tc\x07d"), "a\nb\tcd")

    def test_the_record_stores_unescaped_text(self) -> None:
        """An escape is a property of where text is going, so it belongs at the sink."""
        self.assertEqual(normalise("<script>&amp;"), "<script>&amp;")

    def test_a_cost_that_is_not_a_finite_number_is_absent_rather_than_zero(self) -> None:
        """`NaN` is a legal float and is not legal JSON; one would cost every reader the record."""
        for value in (float("nan"), float("inf"), -1.0, "3.00", True, None):
            with self.subTest(value=value):
                self.assertIsNone(as_money(value))
        self.assertEqual(as_money(3), 3.0)

    def test_a_count_that_is_not_a_whole_number_is_absent(self) -> None:
        for value in (-1, 1.5, "2", True, None):
            with self.subTest(value=value):
                self.assertIsNone(as_count(value))
        self.assertEqual(as_count(4), 4)

    def test_a_list_an_agent_controls_is_bounded_in_both_directions(self) -> None:
        self.assertEqual(normalise_all("not a list"), [])
        self.assertEqual(len(normalise_all(["x"] * 500)), 50)


class ARealAgentStepsReceiptsAreCarried(unittest.TestCase):
    """The `agent` fixture is a real paid run, and it is the only one that can prove this.

    Every other recorded shape is a command step, so cost, turns, session identity and a
    resume command can only ever appear there as absent. Proving the populated half against
    a synthesised report would be proving Cairn's reading of a file Cairn wrote; this reads
    what a real provider actually returned.
    """

    def setUp(self) -> None:
        self.record = record_of("agent")
        self.step = self.record["steps"][0]

    def test_a_paid_step_carries_what_it_cost(self) -> None:
        cost = self.step["cost_usd"]
        self.assertIsNotNone(cost)
        self.assertGreater(cast(float, cost), 0)
        self.assertNotEqual(
            self.step["provenance"].get("cost_usd"), PROVENANCE_ABSENT
        )
        self.assertTrue(
            self.step["cost_is_notional"],
            "a subscription login prices an API equivalent, and the record must say so",
        )

    def test_a_paid_step_carries_the_session_a_person_can_reopen(self) -> None:
        session = cast(str, self.step["session_id"])
        self.assertTrue(session)
        resume = cast(str, self.step["resume_command"])
        self.assertIn(session, resume)
        self.assertIn("--resume", resume)
        self.assertIsNotNone(self.step["transcript"])

    def test_a_paid_step_carries_its_turns_and_its_own_account(self) -> None:
        self.assertIsNotNone(self.step["turns"])
        self.assertGreater(cast(int, self.step["turns"]), 0)
        self.assertTrue(self.step["said"])

    def test_the_run_totals_what_it_spent_and_flags_it_as_notional(self) -> None:
        budget = self.record["budget"]
        self.assertEqual(budget["cost_usd"], self.step["cost_usd"])
        self.assertTrue(budget["notional"])
        self.assertEqual(budget["priced_steps"], 1)
        self.assertIn("budget", [item["kind"] for item in self.record["attention"]])

    def test_the_step_carries_what_its_commit_changed(self) -> None:
        self.assertTrue(self.step["commit"])
        diffstat = cast(dict[str, int], self.step["diffstat"])
        self.assertGreater(diffstat["files"], 0)
        self.assertGreater(diffstat["insertions"], 0)

    def test_what_was_asked_is_carried_and_bounded(self) -> None:
        """A plan's task is a whole document, and it arrives here through a command line."""
        asked = cast(str, self.step["asked"])
        self.assertIn("note.txt", asked)
        self.assertLessEqual(len(asked), TEXT_LIMIT)

    def test_the_run_is_green_and_the_engine_agrees_for_once(self) -> None:
        self.assertEqual(self.record["verdict"], VERDICT_GREEN)
        self.assertFalse(self.record["engine_contradicted"])


class AnAgentStepsReceiptsReachTheRecord(unittest.TestCase):
    """What no cheap real run produces: a divergence, a branch, and reported follow-up work.

    The `agent` fixture supplies the receipts from a run that really paid for them. These
    three need a step to disagree with its own assertion, to stand on a branch, or to find
    work it declined to do — none of which a one-step green run does, and none of which is
    worth paying an agent to stage.
    """

    def setUp(self) -> None:
        state, reports, run_id = load("green")
        self.state, self.run_id = state, run_id
        self.reports = {name: dict(report) for name, report in reports.items()}
        self.reports["work_alpha"] = {
            **self.reports["work_alpha"],
            "summary": "added the sentinel field",
            "follow_up_work": ["the migration still needs a backfill"],
            "detail": {
                "session_id": "c44c6f6b-88da-4178-8d75-499c7c1d2f94",
                "total_cost_usd": 0.25,
                "cost_is_notional": True,
                "turn_count": 7,
                "model": "opus-4",
            },
        }
        self.reports["mark_alpha"] = {
            **self.reports["mark_alpha"],
            "cause": None,
            "detail": {
                "position": "branch",
                "divergence": {"reported": "done", "asserted": False},
            },
        }
        self.reports["commit_alpha"] = {
            **self.reports["commit_alpha"],
            "detail": {
                "commit": "dd839a803e68b0242993aef82bccfd32f1754ae8",
                "branch": "step/alpha",
                "diffstat": {"files": 2, "insertions": 8, "deletions": 1},
            },
        }
        self.step = extract(self.state, self.reports, run_id=self.run_id)["steps"][0]

    def test_the_receipts_a_person_needs_to_open_the_session_are_all_there(self) -> None:
        self.assertEqual(self.step["cost_usd"], 0.25)
        self.assertTrue(self.step["cost_is_notional"])
        self.assertEqual(self.step["turns"], 7)
        self.assertEqual(self.step["session_id"], "c44c6f6b-88da-4178-8d75-499c7c1d2f94")
        self.assertEqual(self.step["model"], "opus-4")
        self.assertIsNotNone(self.step["transcript"])
        resume = cast(str, self.step["resume_command"])
        self.assertIn("c44c6f6b-88da-4178-8d75-499c7c1d2f94", resume)
        self.assertIn("--resume", resume)

    def test_a_populated_field_is_never_marked_absent(self) -> None:
        for field in ("cost_usd", "turns", "session_id", "model", "resume_command"):
            with self.subTest(field=field):
                self.assertNotEqual(
                    self.step["provenance"].get(field), PROVENANCE_ABSENT
                )

    def test_what_the_step_said_and_what_the_commit_recorded_both_land(self) -> None:
        self.assertEqual(self.step["said"], "added the sentinel field")
        self.assertEqual(self.step["commit"], "dd839a803e68b0242993aef82bccfd32f1754ae8")
        self.assertEqual(self.step["branch"], "step/alpha")
        self.assertEqual(
            self.step["diffstat"], {"files": 2, "insertions": 8, "deletions": 1}
        )
        self.assertEqual(
            self.step["follow_up_work"], ["the migration still needs a backfill"]
        )

    def test_a_divergence_is_carried_beside_the_outcome_rather_than_folded_into_it(
        self,
    ) -> None:
        """Neither account is presented as the truth; both are kept."""
        self.assertEqual(
            self.step["divergence"], {"reported": "done", "asserted": False}
        )
        self.assertIn(OVERLAY_DIVERGENCE, self.step["overlays"])
        self.assertEqual(self.step["position"], "branch")

    def test_the_run_totals_the_spend_it_can_see_and_says_it_is_notional(self) -> None:
        record = extract(self.state, self.reports, run_id=self.run_id)
        self.assertEqual(record["budget"]["cost_usd"], 0.25)
        self.assertTrue(record["budget"]["notional"])
        self.assertEqual(record["budget"]["turns"], 7)
        self.assertEqual(record["budget"]["priced_steps"], 1)
        self.assertEqual(record["budget"]["unpriced_steps"], 1)
        self.assertIn("budget", [item["kind"] for item in record["attention"]])

    def test_the_follow_up_work_a_step_reported_becomes_an_attention_item(self) -> None:
        record = extract(self.state, self.reports, run_id=self.run_id)
        follow_ups = [
            item for item in record["attention"] if item["kind"] == "follow_up"
        ]
        self.assertEqual(len(follow_ups), 1)
        self.assertEqual(follow_ups[0]["subject"], "alpha")

    def test_every_receipt_reaches_the_projection(self) -> None:
        facts = as_mapping(extract(self.state, self.reports, run_id=self.run_id))
        self.assertEqual(facts["step.alpha.cost_usd"], "0.2500")
        self.assertEqual(facts["step.alpha.turns"], "7")
        self.assertEqual(facts["step.alpha.diffstat"], "2 files +8 -1")
        self.assertNotEqual(facts["step.alpha.resume_command"], ABSENT)


class TheProjectionIsTheDriftOracle(unittest.TestCase):
    """Task 11: stable keys and ordering, presentation-free, and total over the record."""

    def test_the_projection_is_deterministic(self) -> None:
        for shape in SHAPES:
            with self.subTest(shape=shape):
                record = record_of(shape)
                self.assertEqual(canonical_facts(record), canonical_facts(record))

    def test_the_projection_agrees_with_the_record_on_the_verdict_and_the_code(self) -> None:
        for shape in SHAPES:
            with self.subTest(shape=shape):
                record = record_of(shape)
                facts = as_mapping(record)
                self.assertEqual(facts["run.verdict"], record["verdict"])
                self.assertEqual(facts["run.exit_code"], str(record["exit_code"]))

    def test_every_step_reaches_the_projection_with_its_outcome(self) -> None:
        for shape in SHAPES:
            record = record_of(shape)
            facts = as_mapping(record)
            for step in record["steps"]:
                with self.subTest(shape=shape, step=step["step_id"]):
                    self.assertEqual(
                        facts[f"step.{step['step_id']}.outcome"], step["outcome"]
                    )

    def test_an_absent_fact_projects_as_absent_and_never_as_a_zero(self) -> None:
        facts = as_mapping(record_of("green"))
        self.assertEqual(facts["run.actor"], ABSENT)
        self.assertEqual(facts["budget.cost_usd"], ABSENT)

    def test_the_projection_carries_no_presentation(self) -> None:
        forbidden = re.compile(r"colou?r|icon|emoji|width|markdown|html|indent")
        for shape in SHAPES:
            for key, _ in canonical_facts(record_of(shape)):
                with self.subTest(shape=shape, key=key):
                    self.assertIsNone(forbidden.search(key))

    def test_the_run_level_key_set_is_the_same_for_every_shape(self) -> None:
        """A field added to the record without a projection key fails here."""
        shapes = [
            {key for key, _ in canonical_facts(record_of(shape)) if not key.startswith(
                ("step.", "attention.", "infrastructure.", "wave.")
            )}
            for shape in SHAPES
        ]
        for keys in shapes[1:]:
            self.assertEqual(keys, shapes[0])


class TheRecordLivesInCairnsOwnState(unittest.TestCase):
    """Tasks 9 and 15: keyed by run identity, outside every repository, replaced atomically."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_a_run_directory_is_keyed_by_run_identity(self) -> None:
        first = record_path(self.root, "run-one")
        second = record_path(self.root, "run-two")
        self.assertNotEqual(first, second)
        self.assertEqual(first.parent.parent, self.root)

    def test_a_run_id_that_would_leave_the_runs_root_is_refused(self) -> None:
        for run_id in ("../escape", "a/b", "", ".hidden", "x" * 200):
            with self.subTest(run_id=run_id), self.assertRaises(ValueError):
                record_path(self.root, run_id)

    def test_a_reports_directory_sits_beside_the_record_of_the_same_run(self) -> None:
        self.assertEqual(
            reports_directory(self.root, "run-one").parent,
            record_path(self.root, "run-one").parent,
        )

    def test_a_record_round_trips_through_its_own_store(self) -> None:
        record = record_of("green")
        written = write_record(self.root, record)
        self.assertTrue(written.exists())
        self.assertEqual(read_record(self.root, record["run_id"]), record)

    def test_a_missing_record_is_absence_rather_than_an_error(self) -> None:
        self.assertIsNone(read_record(self.root, "never-ran"))

    def test_a_record_an_older_extraction_wrote_is_rebuilt_rather_than_read_through(
        self,
    ) -> None:
        """A shape missing the fields this model requires is a lie about what was measured."""
        record = record_of("green")
        write_record(self.root, record)
        path = record_path(self.root, record["run_id"])
        older = {**json.loads(path.read_text(encoding="utf-8")), "record_version": 2}
        path.write_text(json.dumps(older), encoding="utf-8")
        with self.assertRaises(CairnError) as caught:
            read_record(self.root, record["run_id"])
        self.assertEqual(caught.exception.cause, "run_record_unreadable")
        self.assertIn("record build", str(caught.exception))

    def test_a_fragment_a_killed_writer_left_is_swept_before_the_next_write(self) -> None:
        record = record_of("green")
        path = record_path(self.root, record["run_id"])
        path.parent.mkdir(parents=True)
        fragment = path.parent / f".{path.name}.abc123.tmp"
        fragment.write_text("half a record", encoding="utf-8")
        write_record(self.root, record)
        self.assertFalse(fragment.exists())

    def test_a_step_composes_its_report_path_from_the_runs_root_and_the_run_id(self) -> None:
        environment = {
            "DAG_RUN_ID": "run-one",
            "DAG_RUN_STEP_NAME": "work_alpha",
            "DAG_RUN_WORK_DIR": str(self.root),
            RUNS_ROOT_ENV: str(self.root / "runs"),
        }
        context = RuntimeContext.from_env(environment)
        self.assertEqual(
            context.report_path,
            reports_directory(self.root / "runs", "run-one") / "work_alpha.json",
        )

    def test_a_step_with_no_runs_root_fails_loudly_rather_than_guessing(self) -> None:
        """There is no fallback: a report written where nothing looks is a report lost."""
        with self.assertRaisesRegex(CairnError, RUNS_ROOT_ENV):
            RuntimeContext.from_env(
                {
                    "DAG_RUN_ID": "run-one",
                    "DAG_RUN_STEP_NAME": "work_alpha",
                    "DAG_RUN_WORK_DIR": str(self.root),
                }
            )


class TheCommandReportsTheRunsVerdict(unittest.TestCase):
    """Exit criterion: the record is readable with nothing running, and automation can act."""

    def _run(self, *arguments: str) -> tuple[int, str]:
        completed = subprocess.run(
            [sys.executable, "-m", "cairn", "record", *arguments],
            cwd=PACKAGE_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        return completed.returncode, completed.stdout

    def test_a_run_neither_cairn_nor_the_engine_knows_exits_on_its_own_code(self) -> None:
        code, _ = self._run("build", "--run", "nobody-ran-this", "--repository", ".")
        self.assertEqual(code, EXIT_NO_RECORD)
        self.assertNotIn(EXIT_NO_RECORD, VERDICT_EXIT_CODES.values())

    def test_the_dispatch_reaches_the_record_command_before_any_step_identity(self) -> None:
        """Like `plan` and `supervise`, reading a run is not part of one.

        Reached with no Dagu environment at all: a person debugging a run is not standing
        inside a step, and a dispatch that resolved identity first would refuse them.
        """
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(SystemExit) as caught:
            cairn_main(["record", "--help"])
        self.assertEqual(caught.exception.code, 0)


class TheStepsOwnAccountIsTheRicherSource(unittest.TestCase):
    """Task 7: reports first, the engine's state as a supplement."""

    def test_a_verified_step_carries_what_only_its_own_report_holds(self) -> None:
        record = record_of("green")
        step = record["steps"][0]
        self.assertEqual(step["outcome"], OUTCOME_VERIFIED)
        self.assertIsNotNone(step["said"])

    def test_timings_and_log_paths_come_from_the_engine(self) -> None:
        step = record_of("green")["steps"][0]
        self.assertIsNotNone(step["started_at"])
        self.assertIsNotNone(step["transcript"])

    def test_a_report_from_another_run_is_never_read(self) -> None:
        """Reports outlive the run that wrote them, and one would speak for a step this run
        never started."""
        directory = CORPUS / "green" / "reports"
        self.assertEqual(read_reports(directory, "some-other-run"), {})

    def test_a_report_that_cannot_be_parsed_costs_its_own_step_and_nothing_more(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for source in (CORPUS / "green" / "reports").glob("*.json"):
                (directory / source.name).write_text(
                    source.read_text(encoding="utf-8"), encoding="utf-8"
                )
            (directory / "work_alpha.json").write_text("{ truncated", encoding="utf-8")
            _, _, run_id = load("green")
            reports = read_reports(directory, run_id)
            self.assertNotIn("work_alpha", reports)
            self.assertIn("work_beta", reports)

    def test_a_node_the_grammar_does_not_cover_is_carried_rather_than_dropped(self) -> None:
        state, reports, run_id = load("green")
        state = {
            **state,
            "nodes": [
                *engine.nodes_of(state),
                {"step": {"name": "wat"}, "status": 2, "error": "exit status 9"},
            ],
        }
        record = extract(state, reports, run_id=run_id)
        found = next(node for node in record["nodes"] if node["name"] == "wat")
        self.assertIsNone(found["role"])
        self.assertEqual(found["exit_code"], 9)
        self.assertEqual(record["verdict"], VERDICT_FAILED)


@unittest.skipUnless(
    os.environ.get("CAIRN_SKIP_ENGINE_TESTS") != "1" and shutil.which("dagu"),
    "the crash case is about what a real engine leaves behind",
)
class AKilledRunIsReadForReal(unittest.TestCase):
    """Task 13: kill -9 a live run and read the record, with no repair in between.

    The fixture corpus carries a recording of this, which is what lets every other test run
    without an engine. This one proves the recording is not a story: it kills a run that is
    actually going, and asserts the contradiction while the corpse is fresh.
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.runs = self.root / "runs"
        for directory in (self.home, self.runs):
            directory.mkdir(parents=True)
        (self.home / "base.yaml").write_text(
            "retry_policy:\n  limit: 0\n  interval_sec: 1\n", encoding="utf-8"
        )

    def test_the_engine_still_says_running_and_cairns_record_does_not(self) -> None:
        run_id = "killed-for-real"
        held = f"{sys.executable} -m cairn exec --command 'sleep 120'"
        definition = {
            "type": "graph",
            "retry_policy": {"limit": 0, "interval_sec": 1},
            "env": [
                {"PYTHONPATH": str(PACKAGE_ROOT)},
                {RUNS_ROOT_ENV: str(self.runs)},
            ],
            "steps": [
                {
                    "name": "work_alpha",
                    "run": held,
                    "working_dir": str(self.root),
                    "timeout_sec": 300,
                    "retry_policy": {"limit": 0, "interval_sec": 1},
                }
            ],
        }
        path = self.root / "killed.yaml"
        path.write_text(json.dumps(definition, indent=2), encoding="utf-8")

        child = subprocess.Popen(
            ["dagu", "start", "--run-id", run_id, str(path)],
            env={**os.environ, "DAGU_HOME": str(self.home)},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.addCleanup(self._reap, child)

        state_file = self._wait_for_state()
        os.kill(child.pid, 9)
        child.wait(timeout=60)

        state = last_record(state_file)
        assert state is not None
        # The premise, asserted rather than assumed: without it the rest is vacuous.
        self.assertEqual(state["status"], STATUS_RUNNING)
        self.assertFalse(state.get("finishedAt"))

        # No reconciliation first. The record must be honest without the repair.
        record = extract(state, read_reports(self.runs / run_id / "reports", run_id),
                         run_id=run_id)
        self.assertEqual(record["verdict"], VERDICT_FAILED)
        self.assertIs(record["owner_alive"], False)
        self.assertNotIn(
            OUTCOME_RUNNING, {step["outcome"] for step in record["steps"]}
        )
        self.assertEqual(record["steps"][0]["cause"], ORCHESTRATOR_DIED)

    def _wait_for_state(self) -> Path:
        for _ in range(300):
            found = sorted((self.home / "data").rglob("status.jsonl"))
            for candidate in found:
                state = last_record(candidate)
                if state is not None and state.get("status") == STATUS_RUNNING:
                    nodes = engine.nodes_of(state)
                    if any(node.get("status") == 1 for node in nodes):
                        return candidate
            time.sleep(0.1)
        self.fail("the engine never reported a running node")

    def _reap(self, child: subprocess.Popen[bytes]) -> None:
        try:
            os.killpg(os.getpgid(child.pid), 9)
        except (ProcessLookupError, PermissionError):
            pass


if __name__ == "__main__":
    unittest.main()
