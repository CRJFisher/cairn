"""Doc 13: triggers, schedules, and the view a run is linked to rather than rebuilt.

Two classes of test carry most of the weight here, and both are pairings.

The first is the house pattern: for each construct Cairn refuses, its neighbour shows the
engine accepting the very same bytes. A rule whose engine half is missing is a rule nobody
can tell from a style preference.

The second is new, and it is what task 12 and task 13 are about: for each defect, one test
**measures the defect** and its neighbour shows the guard closing it. A refusal test with no
measurement beside it proves only that a command can fail.

Every engine-driving class **fails** without `dagu` rather than skipping, because the
defects they cover are the ones that otherwise report success. Set `CAIRN_SKIP_ENGINE_TESTS=1`
to record deliberately that a run did not check them.
"""

import copy
import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar, cast
from unittest.mock import patch

from cairn.__main__ import main as cairn_main
from cairn.__main__ import record_the_run
from cairn.baseconfig import (
    assert_catchup_disabled,
    ensure_dag_retry_disabled,
    read_base_retry_policy,
    read_base_scalar,
)
from cairn.core import CairnError, RuntimeContext
from cairn.enginehome import (
    EnginePaths,
    engine_paths,
    forget_engine_paths,
    run_records_path,
)
from cairn.layout import VIEW_BASE_DEFAULT, occasion_path, view_base, view_url
from cairn.marker import OCCASION_PATTERN, mint_occasion, resolve_occasion
from cairn.parameters import parameter, parent_branch, repository
from cairn.record.extract import extract
from cairn.schedule import (
    NAMED_LIMIT,
    RETRY_SCANNER_HOURS,
    assert_safe_to_start,
    failed_runs_since,
    install,
    installed,
    published_path,
    queued_runs,
    remove,
    scheduler_command,
)
from cairn.topology import WORKTREES_SUFFIX, worktrees_parent
from cairn.workflow.build import envelope
from cairn.workflow.preflight import RULES, check
from cairn.workflow.schema import (
    CATCHUP_DISABLED,
    OCCASION_PARAM,
    OVERLAP_SKIP,
    PARENT_BRANCH_PARAM,
    REPOSITORY_PARAM,
    ROOT_KEYS,
    Workflow,
    serialise,
)

CAIRN_ROOT = Path(__file__).resolve().parent.parent
TRIGGERS_DOC = CAIRN_ROOT / "docs" / "triggers.md"
SKIP_ENV = "CAIRN_SKIP_ENGINE_TESTS"


# The engine refuses a definition with no steps at all, so every document here carries one.
# It is the smallest body a rule can be measured against without a second thing going wrong.
ONE_STEP: dict[str, Any] = {
    "name": "work_a",
    "run": "true",
    "working_dir": "/srv/work/product",
    "timeout_sec": 60,
    "retry_policy": {"limit": 0, "interval_sec": 1},
}


def document(**extra: Any) -> Workflow:
    """A minimal well-formed definition, so a rule is measured against one thing at a time."""
    built = envelope(
        [dict(ONE_STEP)],
        repository="/srv/work/product",
        parent_branch="main",
        occasion="",
        python_path="/opt/cairn",
        runs_root="/srv/work/product/.git/cairn/runs",
    )
    return cast(Workflow, {**cast(dict[str, Any], built), **extra})


def rules(document: Any) -> set[str]:
    return {fault.rule for fault in check(document)}


def git(directory: Path, *arguments: str) -> None:
    subprocess.run(("git", *arguments), cwd=directory, capture_output=True, check=True)


def repository_at(root: Path, name: str = "product") -> Path:
    made = root / name
    made.mkdir(parents=True)
    git(made, "init", "-b", "main")
    git(made, "config", "user.email", "probe@example.invalid")
    git(made, "config", "user.name", "probe")
    (made / "README.md").write_text("start\n", encoding="utf-8")
    git(made, "add", "-A")
    git(made, "commit", "-m", "init")
    return made


# ---------------------------------------------------------------------------
# Task 13 — what a caller may vary
# ---------------------------------------------------------------------------


class TheTwoDerivationsOfAWorktreeRootMustAgree(unittest.TestCase):
    """The measurement first, then the guard. Without the measurement the refusal below
    would prove only that a function can raise."""

    def test_a_canonical_repository_makes_the_two_derivations_agree(self) -> None:
        value = "/srv/work/product"
        self.assertEqual(Path(value + WORKTREES_SUFFIX), worktrees_parent(Path(value)))

    def test_a_trailing_slash_makes_them_disagree(self) -> None:
        """This is the defect: the emitter splices text and the runtime derives through
        `Path`, which normalises. The spliced directory is *inside* the working tree, and
        the engine creates a missing working directory rather than failing."""
        value = "/srv/work/product/"
        spliced = Path(value + WORKTREES_SUFFIX)
        self.assertNotEqual(spliced, worktrees_parent(Path(value)))
        self.assertEqual(spliced, Path("/srv/work/product/.cairn-worktrees"))
        self.assertIn("product", spliced.parent.name)

    def test_the_check_is_the_two_derivations_rather_than_a_rule_about_slashes(self) -> None:
        """So a spelling nobody has thought of is caught by the same comparison."""
        for spelling in ("/srv/work/product/", "/srv/work/product/."):
            with self.subTest(value=spelling):
                self.assertNotEqual(
                    Path(spelling + WORKTREES_SUFFIX), worktrees_parent(Path(spelling))
                )

    def test_a_doubled_separator_normalises_identically_on_both_sides(self) -> None:
        """Not every unusual spelling diverges, and one that does not is not a defect."""
        self.assertEqual(
            Path("/srv/work//product" + WORKTREES_SUFFIX),
            worktrees_parent(Path("/srv/work//product")),
        )


class WhatACallerMayVary(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.repository = repository_at(self.root)

    def test_an_unset_parameter_is_named_rather_than_defaulted(self) -> None:
        with self.assertRaises(CairnError) as caught:
            parameter(REPOSITORY_PARAM, {})
        self.assertEqual(caught.exception.cause, "invalid_arguments")

    def test_the_canonical_repository_is_accepted(self) -> None:
        self.assertEqual(
            repository(self.repository, {REPOSITORY_PARAM: str(self.repository)}),
            self.repository,
        )

    def test_a_trailing_slash_is_refused_before_anything_is_created(self) -> None:
        with self.assertRaises(CairnError) as caught:
            repository(self.repository, {REPOSITORY_PARAM: f"{self.repository}/"})
        self.assertEqual(caught.exception.cause, "invalid_arguments")
        self.assertIn("worktree setup", str(caught.exception))
        self.assertFalse((self.repository / WORKTREES_SUFFIX).exists())

    def test_a_relative_repository_is_refused(self) -> None:
        with self.assertRaises(CairnError) as caught:
            repository(self.repository, {REPOSITORY_PARAM: "product"})
        self.assertIn("absolute", str(caught.exception))

    def test_a_repository_naming_somewhere_else_is_refused(self) -> None:
        other = repository_at(self.root, "other")
        with self.assertRaises(CairnError) as caught:
            repository(self.repository, {REPOSITORY_PARAM: str(other)})
        self.assertEqual(caught.exception.cause, "invalid_arguments")

    def test_a_repository_reached_through_a_symlink_is_refused(self) -> None:
        """The two derivations agree on the text and name different real directories, so a
        check comparing the value against itself would pass this and land nothing."""
        link = self.root / "link"
        link.symlink_to(self.repository)
        with self.assertRaises(CairnError) as caught:
            repository(self.repository, {REPOSITORY_PARAM: str(link)})
        self.assertIn("worktree setup", str(caught.exception))

    def test_a_parent_branch_that_is_an_option_is_refused_before_it_reaches_git(self) -> None:
        with self.assertRaises(CairnError) as caught:
            parent_branch(self.repository, {PARENT_BRANCH_PARAM: "--upload-pack=x"})
        self.assertIn("option", str(caught.exception))

    def test_a_parent_branch_git_rejects_is_refused(self) -> None:
        with self.assertRaises(CairnError):
            parent_branch(self.repository, {PARENT_BRANCH_PARAM: "a branch"})

    def test_an_ordinary_parent_branch_is_accepted(self) -> None:
        self.assertEqual(
            parent_branch(self.repository, {PARENT_BRANCH_PARAM: "main"}), "main"
        )

    def test_the_shape_of_a_branch_is_checked_without_a_repository_in_hand(self) -> None:
        """Every later reader pays no subprocess, and argv injection is closed anyway."""
        self.assertEqual(parent_branch(None, {PARENT_BRANCH_PARAM: "main"}), "main")
        with self.assertRaises(CairnError):
            parent_branch(None, {PARENT_BRANCH_PARAM: "-x"})



# ---------------------------------------------------------------------------
# Task 12 — where a run's occasion comes from
# ---------------------------------------------------------------------------


class TheRunMintsItsOwnOccasion(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.runs = Path(self.temporary.name).resolve() / "runs"

    def test_a_caller_who_supplies_none_gets_one_minted_and_recorded(self) -> None:
        minted = resolve_occasion(self.runs, "run-1", {})
        self.assertIsNotNone(OCCASION_PATTERN.match(minted))
        self.assertEqual(occasion_path(self.runs, "run-1").read_text().strip(), minted)

    def test_every_reader_in_one_run_gets_the_same_value(self) -> None:
        """The gate fails open and the marker write fails closed, so a disagreement
        between them would either re-pay for ever or skip for ever."""
        first = resolve_occasion(self.runs, "run-1", {})
        self.assertEqual(resolve_occasion(self.runs, "run-1", {}), first)

    def test_a_second_run_gets_a_second_occasion(self) -> None:
        """Which is what makes a scheduled plan do work on every firing."""
        self.assertNotEqual(
            resolve_occasion(self.runs, "run-1", {}),
            resolve_occasion(self.runs, "run-2", {}),
        )

    def test_a_recovery_under_the_same_identity_continues_the_occasion(self) -> None:
        """`dagu retry` reuses the run id, and the occasion is keyed on it."""
        first = resolve_occasion(self.runs, "run-1", {})
        self.assertEqual(resolve_occasion(self.runs, "run-1", {}), first)

    def test_a_supplied_occasion_outranks_the_recorded_one(self) -> None:
        pinned = mint_occasion()
        resolve_occasion(self.runs, "run-1", {})
        self.assertEqual(resolve_occasion(self.runs, "run-1", {OCCASION_PARAM: pinned}), pinned)

    def test_a_supplied_occasion_that_is_not_one_is_refused_rather_than_keyed_on(self) -> None:
        with self.assertRaises(CairnError):
            resolve_occasion(self.runs, "run-1", {OCCASION_PARAM: "not-an-occasion"})

    def test_a_recorded_occasion_that_is_not_one_is_refused(self) -> None:
        path = occasion_path(self.runs, "run-1")
        path.parent.mkdir(parents=True)
        path.write_text("rubbish\n", encoding="utf-8")
        with self.assertRaises(CairnError):
            resolve_occasion(self.runs, "run-1", {})


# ---------------------------------------------------------------------------
# Task 1 and 2 — the view is a link
# ---------------------------------------------------------------------------


class TheLiveViewIsALink(unittest.TestCase):
    def test_the_url_is_composed_from_the_engines_own_name_for_the_workflow(self) -> None:
        self.assertEqual(
            view_url("nightly", "run-1", "http://127.0.0.1:8080"),
            "http://127.0.0.1:8080/dag-runs/nightly/run-1",
        )

    def test_a_name_carrying_a_separator_cannot_reach_outside_its_own_segment(self) -> None:
        self.assertEqual(
            view_url("../admin", "run/1", "http://h:1"),
            "http://h:1/dag-runs/..%2Fadmin/run%2F1",
        )

    def test_the_base_is_the_engines_documented_default_unless_the_machine_says_otherwise(
        self,
    ) -> None:
        self.assertEqual(view_base({}), VIEW_BASE_DEFAULT)
        self.assertEqual(view_base({"CAIRN_VIEW_BASE": "https://dagu.example/"}),
                         "https://dagu.example")

    def test_a_trailing_slash_on_the_base_never_doubles(self) -> None:
        self.assertEqual(
            view_url("n", "r", "http://h:1/"), "http://h:1/dag-runs/n/r"
        )


# ---------------------------------------------------------------------------
# Tasks 4 and 6 — what every emitted file states, and what is refused
# ---------------------------------------------------------------------------


class EveryFileStatesWhenItRunsAndWhatIsReplayed(unittest.TestCase):
    def test_replay_is_off_on_every_file_whether_scheduled_or_not(self) -> None:
        self.assertEqual(document()["catchup_window"], CATCHUP_DISABLED)
        self.assertEqual(document()["overlap_policy"], OVERLAP_SKIP)

    def test_an_unscheduled_file_declares_no_schedule_at_all(self) -> None:
        self.assertNotIn("schedule", document())

    def test_a_schedule_is_stated_before_anything_else_a_person_reads(self) -> None:
        built = envelope(
            [],
            repository="/r",
            parent_branch="main",
            occasion="",
            python_path="/p",
            runs_root="/x",
            schedule="0 3 * * *",
        )
        self.assertEqual(list(built)[:2], ["type", "schedule"])

    def test_the_emitted_root_keys_are_the_allowlist_the_preflight_holds_to(self) -> None:
        """A key the emitter starts writing and the allowlist does not know is refused by
        Cairn's own preflight, which would otherwise be found by a run rather than a test."""
        self.assertLessEqual(set(document()), ROOT_KEYS)


class TheTriggerRulesRefuseWhatTheEngineWouldRun(unittest.TestCase):
    def test_a_catchup_window_is_refused(self) -> None:
        self.assertIn("catchup_replay", rules(document(catchup_window="6h")))

    def test_an_absent_catchup_window_is_refused_because_omission_is_inheritance(self) -> None:
        broken = cast(dict[str, Any], copy.deepcopy(dict(document())))
        del broken["catchup_window"]
        self.assertIn("catchup_replay", rules(broken))

    def test_an_overlap_policy_the_machine_would_decide_is_refused(self) -> None:
        self.assertIn("inherited_overlap", rules(document(overlap_policy="all")))
        broken = cast(dict[str, Any], copy.deepcopy(dict(document())))
        del broken["overlap_policy"]
        self.assertIn("inherited_overlap", rules(broken))

    def test_a_scheduled_file_carrying_a_fixed_occasion_is_refused(self) -> None:
        """Cron has no override point, so a pinned occasion is reused by every firing."""
        broken = cast(dict[str, Any], copy.deepcopy(dict(document(schedule="0 3 * * *"))))
        broken["params"] = [
            {REPOSITORY_PARAM: "/r"},
            {PARENT_BRANCH_PARAM: "main"},
            {OCCASION_PARAM: mint_occasion()},
        ]
        self.assertIn("schedule_with_fixed_occasion", rules(broken))

    def test_a_scheduled_file_leaving_the_occasion_empty_earns_no_refusal(self) -> None:
        self.assertNotIn(
            "schedule_with_fixed_occasion", rules(document(schedule="0 3 * * *"))
        )

    def test_a_root_key_cairn_does_not_emit_is_refused(self) -> None:
        for key, value in (
            ("skip_if_successful", True),
            ("mail_on", {"failure": True}),
            ("smtp", {"host": "h"}),
            ("queue", "q"),
            ("webhook", {"forward_headers": ["X-Target"]}),
        ):
            with self.subTest(key=key):
                self.assertIn("foreign_root_key", rules(document(**{key: value})))

    def test_a_well_formed_file_earns_none_of_them(self) -> None:
        self.assertEqual(
            rules(document()) & {
                "catchup_replay",
                "inherited_overlap",
                "schedule_with_fixed_occasion",
                "foreign_root_key",
            },
            set(),
        )


class TheDocumentAndTheCodeStateOneTriggerRuleSet(unittest.TestCase):
    def test_every_new_rule_appears_in_the_workflow_document(self) -> None:
        text = (CAIRN_ROOT / "docs" / "workflow.md").read_text(encoding="utf-8")
        for rule in RULES:
            with self.subTest(rule=rule.name):
                self.assertIn(f"`{rule.name}`", text)

    def test_the_surface_document_names_the_two_the_view_will_never_answer(self) -> None:
        text = TRIGGERS_DOC.read_text(encoding="utf-8")
        self.assertIn("**Cost**", text)
        self.assertIn("**Divergence**", text)

    def test_the_surface_document_states_both_human_gate_constraints(self) -> None:
        text = TRIGGERS_DOC.read_text(encoding="utf-8")
        self.assertIn("never a substitute for the deterministic verify gate", text)
        self.assertIn("holds the repository's run lock", text)

    def test_the_surface_document_carries_a_row_for_every_trigger_path(self) -> None:
        text = TRIGGERS_DOC.read_text(encoding="utf-8")
        for path in ("Skill-started run", "Manual trigger from UI", "Cron schedule",
                     "External webhook"):
            with self.subTest(path=path):
                self.assertIn(path, text)


# ---------------------------------------------------------------------------
# Tasks 5 and 6 — the scheduler is an explicit installation
# ---------------------------------------------------------------------------


class TheCatchupHazardIsReadFromTheMachinesOwnFile(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "base.yaml"

    def write(self, text: str) -> Path:
        self.path.write_text(text, encoding="utf-8")
        return self.path

    def test_an_absent_file_carries_no_window(self) -> None:
        assert_catchup_disabled(self.path)

    def test_a_file_declaring_none_carries_no_window(self) -> None:
        assert_catchup_disabled(self.write("retry_policy:\n  limit: 0\n"))

    def test_the_empty_string_is_the_engines_own_spelling_for_off(self) -> None:
        assert_catchup_disabled(self.write('catchup_window: ""\n'))

    def test_a_positive_window_is_refused_naming_the_line(self) -> None:
        with self.assertRaises(CairnError) as caught:
            assert_catchup_disabled(self.write('# a comment\ncatchup_window: "6h"\n'))
        self.assertEqual(caught.exception.cause, "base_catchup_enabled")
        self.assertEqual(caught.exception.detail["catchup_window"], "6h")

    def test_a_quoted_key_is_the_same_key(self) -> None:
        with self.assertRaises(CairnError):
            assert_catchup_disabled(self.write('"catchup_window": "6h"\n'))

    def test_an_indented_namesake_is_not_a_top_level_window(self) -> None:
        """It belongs to whatever declared it, and reading it as this one would let a
        nested empty value shadow a real top-level window."""
        assert_catchup_disabled(self.write('defaults:\n  catchup_window: "6h"\n'))

    def test_two_declarations_are_refused_rather_than_resolved(self) -> None:
        with self.assertRaises(CairnError):
            assert_catchup_disabled(self.write('catchup_window: ""\ncatchup_window: "6h"\n'))

    def test_a_window_hidden_behind_a_trailing_comment_is_still_a_window(self) -> None:
        with self.assertRaises(CairnError):
            assert_catchup_disabled(self.write("catchup_window: 2d  # a while\n"))


class StartingTheSchedulerIsTheOnePlaceTheHazardFires(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.records = self.root / "dag-runs"
        self.base = self.root / "base.yaml"
        self.base.write_text("retry_policy:\n  limit: 0\n  interval_sec: 1\n", encoding="utf-8")

    def record_run(self, run_id: str, status: int, finished: str | None) -> None:
        directory = self.records / run_id
        directory.mkdir(parents=True)
        payload: dict[str, Any] = {
            "dagRunId": run_id,
            "name": "nightly",
            "status": status,
            "nodes": [],
        }
        if finished:
            payload["finishedAt"] = finished
        (directory / "status.jsonl").write_text(json.dumps(payload) + "\n", encoding="utf-8")

    def recent(self, hours: float) -> str:
        """A finish time relative to now, so no test pins a date that later falls outside
        the window and turns a passing suite red on a calendar boundary."""
        return (datetime.now(UTC) - timedelta(hours=hours)).isoformat()

    def test_an_armed_machine_is_refused_and_the_refusal_names_what_it_would_re_execute(
        self,
    ) -> None:
        self.base.write_text("retry_policy:\n  limit: 3\n  interval_sec: 5\n", encoding="utf-8")
        self.record_run("failed-1", 2, self.recent(1))
        self.record_run("failed-2", 2, None)
        self.record_run("fine", 4, self.recent(1))
        with self.assertRaises(CairnError) as caught:
            assert_safe_to_start(base_config=self.base, records=self.records)
        message = str(caught.exception)
        self.assertIn("failed-1", message)
        self.assertIn("failed-2", message)
        self.assertNotIn("fine", message)
        self.assertEqual(caught.exception.cause, "base_retry_enabled")

    def test_a_run_that_failed_before_the_window_is_not_named(self) -> None:
        self.record_run("ancient", 2, self.recent(RETRY_SCANNER_HOURS + 1))
        self.assertEqual(failed_runs_since(self.records), [])

    def test_the_window_is_asserted_from_both_sides_of_its_own_boundary(self) -> None:
        self.record_run("inside", 2, self.recent(RETRY_SCANNER_HOURS - 0.5))
        self.record_run("outside", 2, self.recent(RETRY_SCANNER_HOURS + 0.5))
        self.assertEqual(
            [run.run_id for run in failed_runs_since(self.records)], ["inside"]
        )

    def test_a_refusal_that_stops_short_says_how_many_it_did_not_name(self) -> None:
        self.base.write_text("retry_policy:\n  limit: 3\n  interval_sec: 5\n", encoding="utf-8")
        for index in range(NAMED_LIMIT + 3):
            self.record_run(f"failed-{index}", 2, self.recent(1))
        with self.assertRaises(CairnError) as caught:
            assert_safe_to_start(base_config=self.base, records=self.records)
        self.assertIn("and 3 more", str(caught.exception))

    def test_a_run_with_no_finish_time_is_named_before_the_elision_can_drop_it(self) -> None:
        """It is the one this list refuses to under-name, so it must not sort last."""
        self.base.write_text("retry_policy:\n  limit: 3\n  interval_sec: 5\n", encoding="utf-8")
        self.record_run("unknown", 2, None)
        for index in range(NAMED_LIMIT + 3):
            self.record_run(f"failed-{index}", 2, self.recent(1))
        with self.assertRaises(CairnError) as caught:
            assert_safe_to_start(base_config=self.base, records=self.records)
        self.assertIn("unknown", str(caught.exception))

    def test_a_failed_run_with_no_finish_time_counts_as_inside_the_window(self) -> None:
        """Under-naming a run about to be re-executed is the one error this must not make."""
        self.record_run("unknown", 2, None)
        self.assertEqual([run.run_id for run in failed_runs_since(self.records)], ["unknown"])

    def test_a_catchup_window_is_refused_even_where_retry_is_already_off(self) -> None:
        self.base.write_text(
            'retry_policy:\n  limit: 0\n  interval_sec: 1\ncatchup_window: "6h"\n',
            encoding="utf-8",
        )
        with self.assertRaises(CairnError) as caught:
            assert_safe_to_start(base_config=self.base, records=self.records)
        self.assertEqual(caught.exception.cause, "base_catchup_enabled")

    def test_a_safe_machine_reports_the_queued_work_the_scheduler_would_drain(self) -> None:
        self.record_run("waiting", 5, None)
        waiting = assert_safe_to_start(base_config=self.base, records=self.records)
        self.assertEqual([run.run_id for run in waiting], ["waiting"])

    def test_a_queued_run_is_what_an_external_trigger_leaves_with_nothing_draining_it(
        self,
    ) -> None:
        self.record_run("enqueued", 5, None)
        self.assertEqual([run.run_id for run in queued_runs(self.records)], ["enqueued"])

    def test_a_machine_that_has_never_run_a_dag_is_safe_rather_than_unreadable(self) -> None:
        """An absent history is the honest answer to "what would be re-executed", and
        refusing would make a fresh machine the one place the check cannot run."""
        absent = self.root / "never-run"
        self.assertEqual(failed_runs_since(absent), [])
        self.assertEqual(assert_safe_to_start(base_config=self.base, records=absent), [])


class InstallingASchedulePublishesIntoTheWatchedDirectory(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.repository = repository_at(self.root)
        self.dags = self.root / "dags"
        self.workflow = self.repository / ".git" / "cairn" / "workflows" / "nightly.yaml"
        self.workflow.parent.mkdir(parents=True)
        self.workflow.write_text(serialise(document(schedule="0 3 * * *")), encoding="utf-8")

    def test_a_definition_cairn_never_wrote_cannot_be_scheduled(self) -> None:
        with self.assertRaises(CairnError):
            install(self.repository, "absent", dags=self.dags)

    def test_installing_links_rather_than_copies_so_re_authoring_is_picked_up(self) -> None:
        published = install(self.repository, "nightly", dags=self.dags)
        self.assertTrue(published.is_symlink())
        self.assertEqual(published.readlink(), self.workflow)

    def test_installing_twice_is_the_same_installation(self) -> None:
        first = install(self.repository, "nightly", dags=self.dags)
        self.assertEqual(install(self.repository, "nightly", dags=self.dags), first)

    def test_a_name_another_plan_holds_is_refused_rather_than_retargeted(self) -> None:
        """Two repositories whose plans share a slug would fork one DAG history."""
        published_path("nightly", dags=self.dags).parent.mkdir(parents=True)
        published_path("nightly", dags=self.dags).write_text("someone else's", encoding="utf-8")
        with self.assertRaises(CairnError) as caught:
            install(self.repository, "nightly", dags=self.dags)
        self.assertIn("fork one DAG history", str(caught.exception))

    def test_removing_leaves_the_definition_alone(self) -> None:
        install(self.repository, "nightly", dags=self.dags)
        remove(self.repository, "nightly", dags=self.dags)
        self.assertEqual(installed(dags=self.dags), [])
        self.assertTrue(self.workflow.exists())

    def test_removing_what_was_never_installed_is_an_outcome_not_a_failure(self) -> None:
        self.assertIsNone(remove(self.repository, "nightly", dags=self.dags))

    def test_removing_never_deletes_a_definition_cairn_did_not_publish(self) -> None:
        """`install` refuses to write over a foreign entry, and points a person at this
        command — so this command deleting one would destroy what that refusal protects."""
        foreign = published_path("nightly", dags=self.dags)
        foreign.parent.mkdir(parents=True, exist_ok=True)
        foreign.write_text("someone else's", encoding="utf-8")
        with self.assertRaises(CairnError):
            remove(self.repository, "nightly", dags=self.dags)
        self.assertTrue(foreign.exists())

    def test_a_plan_that_is_not_one_segment_cannot_name_a_file_outside_the_directory(
        self,
    ) -> None:
        for escaping in ("../../escape", "a/b", ".hidden", "Nightly"):
            with self.subTest(plan=escaping), self.assertRaises(CairnError):
                published_path(escaping, dags=self.dags)

    def test_every_slug_the_plan_corpus_actually_uses_is_installable(self) -> None:
        """A plan slug carries hyphens, which the engine's node-name grammar forbids — so
        holding a filename to that grammar would refuse every real plan there is."""
        for slug in ("fan-out", "multi-wave", "single-step", "worktree-hydration"):
            with self.subTest(plan=slug):
                self.assertEqual(
                    published_path(slug, dags=self.dags).name, f"{slug}.yaml"
                )

    def test_the_scheduler_is_pointed_at_the_directory_the_definition_was_published_to(
        self,
    ) -> None:
        """A schedule emitted into a directory nothing watches fires never, silently."""
        self.assertEqual(scheduler_command(dags=self.dags)[-2:], ["--dags", str(self.dags)])


# ---------------------------------------------------------------------------
# The engine's own paths
# ---------------------------------------------------------------------------


class TheEnginesPathsAreAskedOfTheEngine(unittest.TestCase):
    def setUp(self) -> None:
        forget_engine_paths()
        self.addCleanup(forget_engine_paths)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def test_an_explicit_home_is_arithmetic_and_costs_no_subprocess(self) -> None:
        found = engine_paths({"DAGU_HOME": "/opt/dagu"})
        self.assertEqual(
            found,
            EnginePaths(
                dags_directory=Path("/opt/dagu/dags"),
                dag_runs=Path("/opt/dagu/data/dag-runs"),
            ),
        )
        self.assertEqual(run_records_path({"DAGU_HOME": "/opt/dagu"}),
                         Path("/opt/dagu/data/dag-runs"))

    def stub(self, body: str) -> None:
        """Stand a `dagu` on PATH that prints what the test wants it to."""
        binary = self.root / "dagu"
        binary.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        binary.chmod(0o755)
        self.addCleanup(os.environ.update, {"PATH": os.environ["PATH"]})
        os.environ["PATH"] = f"{self.root}:{os.environ['PATH']}"

    def test_the_path_cairn_reads_is_the_path_the_engine_reports(self) -> None:
        """Arithmetic off the configuration directory answers a directory that does not
        exist on this platform, and every reader of it then reports a machine with no runs
        on it."""
        self.stub('echo "DAG runs:  /nowhere/x"; echo "DAGs directory: /nowhere/dags"')
        self.assertEqual(run_records_path({}), Path("/nowhere/x"))

    def test_a_label_the_engine_stops_printing_is_a_hard_error(self) -> None:
        self.stub('echo "DAGs directory: /nowhere/dags"')
        with self.assertRaises(CairnError) as caught:
            engine_paths({})
        self.assertEqual(caught.exception.cause, "engine_paths_unreadable")
        self.assertIn("dag_runs", str(caught.exception))

    def test_a_path_holding_a_colon_survives_the_parse(self) -> None:
        self.stub('echo "DAGs directory: /a:b/dags"; echo "DAG runs: /a:b/data/dag-runs"')
        self.assertEqual(run_records_path({}), Path("/a:b/data/dag-runs"))

    def test_a_label_printed_twice_is_refused_rather_than_resolved_last_wins(self) -> None:
        self.stub('echo "DAGs directory: /a/dags"; echo "DAG runs: /a/x"; '
                  'echo "DAG runs: /b/x"')
        with self.assertRaises(CairnError):
            engine_paths({})

    def test_an_engine_that_cannot_be_asked_is_refused_rather_than_guessed_at(self) -> None:
        self.stub("exit 3")
        with self.assertRaises(CairnError) as caught:
            engine_paths({})
        self.assertEqual(caught.exception.cause, "engine_paths_unreadable")


# ---------------------------------------------------------------------------
# The pairing: the engine accepts every one of these
# ---------------------------------------------------------------------------


class TheEngineAcceptsWhatCairnRefuses(unittest.TestCase):
    """For each trigger rule, Cairn refuses and `dagu validate` exits 0 on the same bytes."""

    SKIP_ENV: ClassVar[str] = SKIP_ENV

    def setUp(self) -> None:
        self.dagu = shutil.which("dagu")
        if not self.dagu and not os.environ.get(self.SKIP_ENV):
            self.fail(
                "dagu is not installed, so these refusals are unverified against the engine "
                f"that would run the same bytes. Install it, or set {self.SKIP_ENV}=1 to "
                "record that this run did not check them."
            )
        if not self.dagu:
            self.skipTest("recorded deliberately as unchecked")
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()
        (self.home / "base.yaml").write_text(
            "retry_policy:\n  limit: 0\n  interval_sec: 1\n", encoding="utf-8"
        )

    def validate(self, built: Any, name: str = "probe") -> int:
        path = self.root / f"{name}.yaml"
        path.write_text(serialise(cast(Workflow, built)), encoding="utf-8")
        return subprocess.run(
            (str(self.dagu), "validate", "--dagu-home", str(self.home), str(path)),
            capture_output=True,
            text=True,
            check=False,
        ).returncode

    def test_the_engine_loads_a_file_that_would_replay_missed_slots(self) -> None:
        broken = document(catchup_window="6h")
        self.assertIn("catchup_replay", rules(broken))
        self.assertEqual(self.validate(broken, "catchup"), 0)

    def test_the_engine_loads_a_file_that_suppresses_a_firing_on_its_own_verdict(self) -> None:
        broken = document(skip_if_successful=True)
        self.assertIn("foreign_root_key", rules(broken))
        self.assertEqual(self.validate(broken, "suppress"), 0)

    def test_the_engine_loads_a_file_that_mails_from_its_own_verdict(self) -> None:
        broken = document(mail_on={"failure": True})
        self.assertIn("foreign_root_key", rules(broken))
        self.assertEqual(self.validate(broken, "mail"), 0)

    def test_the_engine_loads_a_scheduled_file_carrying_a_fixed_occasion(self) -> None:
        """Neither engine check can see a schedule that will no-op for ever."""
        broken = cast(dict[str, Any], copy.deepcopy(dict(document(schedule="0 3 * * *"))))
        broken["params"] = [
            {REPOSITORY_PARAM: "/r"},
            {PARENT_BRANCH_PARAM: "main"},
            {OCCASION_PARAM: mint_occasion()},
        ]
        self.assertIn("schedule_with_fixed_occasion", rules(broken))
        self.assertEqual(self.validate(broken, "fixed"), 0)

    def test_the_engine_loads_a_file_that_lets_the_machine_decide_an_overlap(self) -> None:
        broken = document(overlap_policy="all")
        self.assertIn("inherited_overlap", rules(broken))
        self.assertEqual(self.validate(broken, "overlap"), 0)

    def test_the_engine_loads_every_root_key_cairn_refuses(self) -> None:
        for key, value in (
            ("smtp", {"host": "h"}),
            ("queue", "q"),
            ("webhook", {"forward_headers": ["X-Target"]}),
        ):
            with self.subTest(key=key):
                broken = document(**{key: value})
                self.assertIn("foreign_root_key", rules(broken))
                self.assertEqual(self.validate(broken, f"root-{key}"), 0)

    def test_the_engine_judges_the_cron_expression_so_cairn_does_not(self) -> None:
        """One of the few places its own validator is not blind."""
        self.assertEqual(self.validate(document(schedule="0 3 * * *"), "good-cron"), 0)
        self.assertNotEqual(self.validate(document(schedule="not a cron"), "bad-cron"), 0)

    def test_the_shape_cairn_emits_survives_both_engine_checks(self) -> None:
        self.assertEqual(self.validate(document(schedule="0 3 * * *"), "emitted"), 0)



class ASecondFiringDoesItsWork(unittest.TestCase):
    """Task 12's regression, driven end to end against a real engine.

    The defect it closes was measured before it was fixed: three firings of one workflow
    whose step is `run`-scoped, and from the second onward the engine reported `succeeded`
    with the step skipped and nothing written. The negative control below is what makes this
    test sensitive to the occasion rather than to something incidental — pin the occasion and
    pin the occasion and the second firing no-ops instead.
    """

    SKIP_ENV: ClassVar[str] = SKIP_ENV

    def setUp(self) -> None:
        self.dagu = shutil.which("dagu")
        if not self.dagu and not os.environ.get(self.SKIP_ENV):
            self.fail(
                "dagu is not installed, so the defect this closes is unverified. Install "
                f"it, or set {self.SKIP_ENV}=1 to record that this run did not check it."
            )
        if not self.dagu:
            self.skipTest("recorded deliberately as unchecked")
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.home = self.root / "home"
        (self.home / "dags").mkdir(parents=True)
        (self.home / "base.yaml").write_text(
            "retry_policy:\n  limit: 0\n  interval_sec: 1\n", encoding="utf-8"
        )
        self.repository = repository_at(self.root)
        self.environment = {
            **os.environ,
            "DAGU_HOME": str(self.home),
            "PYTHONPATH": str(CAIRN_ROOT),
        }
        self.workflow = self.author()

    def graph(self) -> dict[str, Any]:
        digest = hashlib.sha256(b"start\n").hexdigest()
        return {
            "cairn_graph_version": 2,
            "plan": {
                "slug": "ticker",
                "title": "Ticker",
                "source": "README.md",
                "sources": [{"path": "README.md", "sha256": digest}],
                "default_kind": "command",
                "id_collisions": [],
            },
            "steps": [
                {
                    "id": "tick",
                    "slug": "1. Tick",
                    "title": "Tick",
                    "task": "Bring the tree to a state where tick.txt holds one more line.",
                    "deps": [],
                    "verify": "test -f tick.txt",
                    "assertion": None,
                    "tools": None,
                    # `run` scope is the whole point: its freshness key *is* the occasion.
                    "scope": "run",
                    "reads": [],
                    "retries": 0,
                    "kind": "command",
                    "command": "date +%s%N >> tick.txt",
                    "command_type": "exec",
                    "timeout": 120,
                }
            ],
            "omissions": [],
            "questions": [],
        }

    def author(self) -> Path:
        graph = self.root / "graph.json"
        graph.write_text(json.dumps(self.graph(), indent=2), encoding="utf-8")
        out = self.root / "ticker.yaml"
        outcome = subprocess.run(
            (
                "python3", "-m", "cairn", "workflow", "author", str(graph),
                "--repository", str(self.repository), "--out", str(out),
            ),
            capture_output=True, text=True, env=self.environment, check=False,
        )
        self.assertEqual(outcome.returncode, 0, outcome.stderr)
        return out

    def fire(self, run_id: str, *extra: str) -> int:
        return subprocess.run(
            (str(self.dagu), "start", "--run-id", run_id, *extra, str(self.workflow)),
            capture_output=True, text=True, env=self.environment, check=False,
        ).returncode

    def lines(self) -> int:
        path = self.repository / "tick.txt"
        return len(path.read_text(encoding="utf-8").splitlines()) if path.exists() else 0

    def test_the_authored_file_leaves_the_occasion_for_the_run_to_mint(self) -> None:
        declared = json.loads(self.workflow.read_text(encoding="utf-8"))["params"]
        self.assertIn({OCCASION_PARAM: ""}, declared)

    def test_authoring_with_a_schedule_puts_one_in_the_file(self) -> None:
        """The flag is the only way the emitted schedule is reachable at all."""
        graph = self.root / "graph.json"
        out = self.root / "scheduled.yaml"
        outcome = subprocess.run(
            (
                "python3", "-m", "cairn", "workflow", "author", str(graph),
                "--repository", str(self.repository), "--out", str(out),
                "--schedule", "0 3 * * *",
            ),
            capture_output=True, text=True, env=self.environment, check=False,
        )
        self.assertEqual(outcome.returncode, 0, outcome.stderr)
        emitted = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(emitted["schedule"], "0 3 * * *")
        self.assertEqual(emitted["catchup_window"], "")
        self.assertEqual(emitted["overlap_policy"], "skip")

    def test_authoring_with_a_cron_the_engine_refuses_is_refused(self) -> None:
        """Cairn parses no cron: the mandatory gate is the authority."""
        outcome = subprocess.run(
            (
                "python3", "-m", "cairn", "workflow", "author", str(self.root / "graph.json"),
                "--repository", str(self.repository), "--out", str(self.root / "bad.yaml"),
                "--schedule", "not a cron",
            ),
            capture_output=True, text=True, env=self.environment, check=False,
        )
        self.assertNotEqual(outcome.returncode, 0)
        self.assertFalse((self.root / "bad.yaml").exists())

    def test_a_second_firing_does_its_work_rather_than_reporting_a_clean_success(self) -> None:
        self.assertEqual(self.fire("firing-1"), 0)
        self.assertEqual(self.lines(), 1)
        self.assertEqual(self.fire("firing-2"), 0)
        self.assertEqual(
            self.lines(), 2, "the second firing reported success having done nothing"
        )

    def test_each_firing_records_an_occasion_of_its_own(self) -> None:
        self.fire("firing-1")
        self.fire("firing-2")
        runs = self.repository / ".git" / "cairn" / "runs"
        first = (runs / "firing-1" / "occasion").read_text(encoding="utf-8").strip()
        second = (runs / "firing-2" / "occasion").read_text(encoding="utf-8").strip()
        self.assertIsNotNone(OCCASION_PATTERN.match(first))
        self.assertNotEqual(first, second)

    def test_pinning_the_occasion_makes_the_second_firing_a_no_op(self) -> None:
        """The negative control. Without it, the test above could pass for a reason that
        has nothing to do with the occasion."""
        pinned = mint_occasion()
        self.assertEqual(self.fire("pinned-1", "--params", f"{OCCASION_PARAM}={pinned}"), 0)
        self.assertEqual(self.lines(), 1)
        self.assertEqual(self.fire("pinned-2", "--params", f"{OCCASION_PARAM}={pinned}"), 0)
        self.assertEqual(self.lines(), 1)

    def test_every_run_leaves_its_own_record_without_anyone_asking(self) -> None:
        """Task 7: a run nobody watched still leaves something a person can read."""
        self.fire("firing-1")
        record = self.repository / ".git" / "cairn" / "runs" / "firing-1" / "record.json"
        self.assertTrue(record.exists(), "the release wrote no record")
        payload = json.loads(record.read_text(encoding="utf-8"))
        self.assertEqual(payload["verdict"], "green")
        self.assertTrue(payload["view_url"].endswith("/dag-runs/ticker/firing-1"))

    def test_the_release_never_judges_its_own_node_and_so_never_reports_itself_failed(
        self,
    ) -> None:
        """The engine records its lifecycle handler before dispatching it, and a run whose
        steps are all finished reads any not-started node as one that will never run — so a
        release judging its own node would write `failed` over a green run, on the one path
        nobody is watching."""
        self.fire("firing-1")
        runs = self.repository / ".git" / "cairn" / "runs" / "firing-1"
        written = json.loads((runs / "record.json").read_text(encoding="utf-8"))
        self.assertEqual(
            [i["outcome"] for i in written["infrastructure"] if i["name"] == "onExit"],
            ["running"],
        )
        rebuilt = subprocess.run(
            ("python3", "-m", "cairn", "record", "build", "--run", "firing-1",
             "--repository", str(self.repository)),
            capture_output=True, text=True, env=self.environment, check=False,
        )
        self.assertEqual(rebuilt.returncode, 0, rebuilt.stderr)
        after = json.loads((runs / "record.json").read_text(encoding="utf-8"))
        self.assertEqual(after["verdict"], written["verdict"])
        self.assertEqual(
            [i["outcome"] for i in after["infrastructure"] if i["name"] == "onExit"],
            ["verified"],
        )

    def test_a_repository_parameter_that_would_land_nothing_is_refused_at_the_first_act(
        self,
    ) -> None:
        """Task 13, end to end: the run halts before any worktree and lands nothing wrong."""
        code = self.fire(
            "slashed", "--params", f"{REPOSITORY_PARAM}={self.repository}/"
        )
        self.assertNotEqual(code, 0)
        self.assertEqual(self.lines(), 0)
        self.assertFalse((self.repository / ".cairn-worktrees").exists())



class TheScheduleSurfaceStatesWhatItCosts(unittest.TestCase):
    """The escalation gate, driven through the command line rather than the library.

    `--accept-daemon` is the one thing standing between wanting a recurring plan and
    acquiring a daemon whose retry scanner re-executes paid work, so it is exercised where
    a person meets it.
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.repository = repository_at(self.root)
        self.dags = self.root / "dags"
        self.records = self.root / "dag-runs"
        self.base = self.root / "base.yaml"
        self.base.write_text(
            "retry_policy:\n  limit: 0\n  interval_sec: 1\n", encoding="utf-8"
        )
        self.workflow = self.repository / ".git" / "cairn" / "workflows" / "nightly.yaml"
        self.workflow.parent.mkdir(parents=True)
        self.workflow.write_text(
            serialise(document(schedule="0 3 * * *")), encoding="utf-8"
        )

    def run_cli(self, *arguments: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cairn_main(["schedule", *arguments])
        return code, out.getvalue(), err.getvalue()

    def install(self, *extra: str) -> tuple[int, str, str]:
        return self.run_cli(
            "install", "--plan", "nightly", "--repository", str(self.repository),
            "--dags", str(self.dags), *extra,
        )

    def test_installing_without_accepting_the_daemon_is_refused_with_its_cost(self) -> None:
        code, _, err = self.install()
        self.assertEqual(code, 1)
        self.assertIn("persistent process", err)
        self.assertIn("re-executes every failed run", err)
        self.assertEqual(installed(dags=self.dags), [], "it published anyway")

    def test_accepting_the_daemon_links_the_definition_and_says_what_will_fire_it(
        self,
    ) -> None:
        code, out, _ = self.install("--accept-daemon")
        self.assertEqual(code, 0)
        self.assertIn("cron '0 3 * * *'", out)
        self.assertEqual([p.name for p in installed(dags=self.dags)], ["nightly.yaml"])

    def test_installing_a_definition_with_no_schedule_says_nothing_will_fire_it(self) -> None:
        """The trigger that silently does nothing, in the direction the watched directory
        cannot show."""
        self.workflow.write_text(serialise(document()), encoding="utf-8")
        code, out, _ = self.install("--accept-daemon")
        self.assertEqual(code, 0)
        self.assertIn("declares no schedule", out)

    def test_the_record_holds_where_a_token_goes_and_never_a_token(self) -> None:
        self.install("--accept-daemon", "--webhook-token-sink", "1password: cairn/hooks")
        sidecar = self.workflow.with_name("nightly.triggers.json")
        recorded = json.loads(sidecar.read_text(encoding="utf-8"))
        self.assertEqual(recorded["webhook"], {"token_sink": "1password: cairn/hooks"})
        self.assertNotIn("dagu_wh_", sidecar.read_text(encoding="utf-8"))

    def test_removing_takes_the_record_with_the_link(self) -> None:
        self.install("--accept-daemon", "--webhook-token-sink", "somewhere")
        code, _, _ = self.run_cli(
            "remove", "--plan", "nightly", "--repository", str(self.repository),
            "--dags", str(self.dags),
        )
        self.assertEqual(code, 0)
        self.assertEqual(installed(dags=self.dags), [])
        self.assertFalse(self.workflow.with_name("nightly.triggers.json").exists())

    def test_status_reports_an_unsafe_machine_rather_than_refusing(self) -> None:
        """A report never refuses, and the machine whose state is bad is the only one the
        question is interesting on."""
        self.base.write_text(
            "retry_policy:\n  limit: 3\n  interval_sec: 5\n", encoding="utf-8"
        )
        code, out, _ = self.run_cli(
            "status", "--dags", str(self.dags), "--base-config", str(self.base),
            "--engine-records", str(self.records),
        )
        self.assertEqual(code, 0)
        self.assertIn("unsafe", out)

    def test_starting_without_accepting_the_daemon_is_refused(self) -> None:
        code, _, err = self.run_cli("start", "--dry-run")
        self.assertEqual(code, 1)
        self.assertIn("persistent process", err)

    def test_starting_asserts_the_machine_before_it_prints_the_invocation(self) -> None:
        self.base.write_text(
            "retry_policy:\n  limit: 3\n  interval_sec: 5\n", encoding="utf-8"
        )
        code, out, err = self.run_cli(
            "start", "--accept-daemon", "--dry-run", "--base-config", str(self.base),
            "--engine-records", str(self.records), "--dags", str(self.dags),
        )
        self.assertEqual(code, 1)
        self.assertIn("re-execute", err)
        self.assertNotIn("starting", out)

    def test_a_safe_machine_prints_the_invocation_it_would_become(self) -> None:
        code, out, _ = self.run_cli(
            "start", "--accept-daemon", "--dry-run", "--base-config", str(self.base),
            "--engine-records", str(self.records), "--dags", str(self.dags),
        )
        self.assertEqual(code, 0)
        self.assertIn(f"--dags {self.dags}", out)


class TheReleaseCannotBeMadeToFailByWhatItRecords(unittest.TestCase):
    """The absorption `_close_out`'s docstring calls the whole design constraint.

    A lifecycle handler exiting nonzero records the whole run as failed — measured — and
    this node is load-bearing infrastructure in Cairn's own record, so a fault in writing
    the record would be reported as the run having failed.
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.context = RuntimeContext(
            run_id="run-1",
            step_id="onExit",
            working_directory=self.root,
            report_path=self.root / "runs" / "run-1" / "reports" / "onExit.json",
            runs_root=self.root / "runs",
        )

    def test_a_record_that_cannot_be_built_leaves_a_note_rather_than_an_exception(
        self,
    ) -> None:
        with patch(
            "cairn.__main__.build_run_record", side_effect=OSError("no such directory")
        ):
            notes = record_the_run(self.context)
        self.assertTrue(any("could not be written" in note for note in notes))

    def test_an_engine_that_cannot_be_asked_where_it_keeps_things_is_absorbed_too(
        self,
    ) -> None:
        with patch(
            "cairn.__main__.run_records_path",
            side_effect=CairnError("engine_paths_unreadable", "no dagu"),
        ):
            notes = record_the_run(self.context)
        self.assertTrue(any("could not be written" in note for note in notes))

    def test_a_run_neither_source_holds_is_reported_rather_than_crashed(self) -> None:
        with patch("cairn.__main__.run_records_path", return_value=self.root / "absent"):
            notes = record_the_run(self.context)
        self.assertTrue(any("no run record" in note for note in notes))



class TheRecordCarriesWhatOnlyTheRunKnows(unittest.TestCase):
    """The three branches this change added to the record, unit-tested so a run without an
    engine still proves them."""

    def state(self, **extra: Any) -> dict[str, Any]:
        return {"dagRunId": "r1", "status": 4, "nodes": [], **extra}

    def test_the_link_is_composed_from_the_engines_own_name_for_the_workflow(self) -> None:
        record = extract(self.state(name="nightly"), {}, run_id="r1")
        link = record["view_url"]
        assert link is not None
        self.assertTrue(link.endswith("/dag-runs/nightly/r1"))
        self.assertEqual(record["provenance"]["view_url"], "derived")

    def test_a_run_whose_state_names_no_workflow_carries_no_link(self) -> None:
        record = extract(self.state(), {}, run_id="r1")
        self.assertIsNone(record["view_url"])
        self.assertEqual(record["provenance"]["view_url"], "absent")

    def test_the_node_building_the_record_is_running_rather_than_never_reached(self) -> None:
        """A settled run reads a not-started node as one that will never run, so the release
        judging its own node would write `failed` over a green run."""
        state = self.state(onExit={"step": {"name": "lock_release"}, "status": 0})
        judged = extract(state, {}, run_id="r1")
        self.assertEqual(
            [i["outcome"] for i in judged["infrastructure"]], ["not_reached"]
        )
        spared = extract(state, {}, run_id="r1", in_flight_node="lock_release")
        self.assertEqual([i["outcome"] for i in spared["infrastructure"]], ["running"])

    def test_a_release_that_already_failed_says_so_rather_than_reading_as_running(
        self,
    ) -> None:
        state = self.state(onExit={"step": {"name": "lock_release"}, "status": 0})
        record = extract(
            state, {}, run_id="r1",
            in_flight_node="lock_release", in_flight_cause="lock_not_held",
        )
        self.assertEqual([i["outcome"] for i in record["infrastructure"]], ["failed"])
        self.assertEqual([i["cause"] for i in record["infrastructure"]], ["lock_not_held"])
        self.assertEqual(record["verdict"], "failed")

    def test_the_occasion_comes_from_the_lock_where_the_parameter_is_empty(self) -> None:
        """Which is every run that minted its own, i.e. every run nobody pinned."""
        minted = mint_occasion()
        reports: dict[str, dict[str, Any]] = {
            "lock_acquire": {
                "step_id": "lock_acquire", "run_id": "r1", "status": "done",
                "summary": "acquired", "follow_up_work": [], "needs_user_decision": False,
                "cause": None, "detail": {"occasion": minted},
            }
        }
        record = extract(self.state(), reports, run_id="r1")
        self.assertEqual(record["lineage"]["occasion"], minted)

    def test_a_supplied_occasion_still_outranks_the_lock_report(self) -> None:
        pinned = mint_occasion()
        state = self.state(paramsList=[f"{OCCASION_PARAM}={pinned}"])
        record = extract(state, {}, run_id="r1")
        self.assertEqual(record["lineage"]["occasion"], pinned)



class OneCommandDisarmsBothHazardsWithoutLosingTheUsersFile(unittest.TestCase):
    """`base.yaml` is the user's, every DAG on the machine inherits it, and Cairn is the
    only thing that edits it unattended — so the two edits must compose without dropping a
    setting between them."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "base.yaml"

    def written(self, text: str) -> str:
        self.path.write_text(text, encoding="utf-8")
        ensure_dag_retry_disabled(self.path)
        return self.path.read_text(encoding="utf-8")

    def test_both_hazards_are_disarmed_in_one_pass(self) -> None:
        self.written(
            'catchup_window: "6h"\nretry_policy:\n  limit: 3\n  interval_sec: 5\n'
        )
        self.assertEqual(read_base_scalar(self.path, "catchup_window"), "")
        self.assertEqual(read_base_retry_policy(self.path).limit, 0)

    def test_a_setting_between_the_two_survives_both_edits(self) -> None:
        """The catchup edit moves the lines the retry splice indexes, so a reader taken
        before it would cut the wrong ones."""
        after = self.written(
            'catchup_window: "6h"\n'
            "max_active_steps: 10\n"
            "retry_policy:\n  limit: 3\n  interval_sec: 5\n"
        )
        self.assertIn("max_active_steps: 10", after)
        self.assertEqual(read_base_scalar(self.path, "catchup_window"), "")
        self.assertEqual(read_base_retry_policy(self.path).limit, 0)
        self.assertEqual(after.count("interval_sec"), 1)

    def test_a_file_that_needs_neither_edit_is_left_alone(self) -> None:
        original = 'catchup_window: ""\nretry_policy:\n  limit: 0\n  interval_sec: 1\n'
        self.path.write_text(original, encoding="utf-8")
        self.assertFalse(ensure_dag_retry_disabled(self.path))
        self.assertEqual(self.path.read_text(encoding="utf-8"), original)

    def test_a_last_line_with_no_newline_is_not_welded_to_the_new_key(self) -> None:
        after = self.written('catchup_window: "6h"\nlog_level: debug')
        self.assertIn("log_level: debug", after)
        self.assertEqual(read_base_scalar(self.path, "log_level"), "debug")
        self.assertEqual(read_base_scalar(self.path, "catchup_window"), "")

    def test_the_key_is_disarmed_wherever_in_the_file_it_sits(self) -> None:
        for text in (
            'retry_policy:\n  limit: 3\n  interval_sec: 5\ncatchup_window: "24h"\n',
            'catchup_window: "6h"\nretry_policy:\n  limit: 3\n  interval_sec: 5\n',
        ):
            with self.subTest(text=text):
                self.path.write_text(text, encoding="utf-8")
                ensure_dag_retry_disabled(self.path)
                self.assertEqual(read_base_scalar(self.path, "catchup_window"), "")
                self.assertEqual(read_base_retry_policy(self.path).limit, 0)

    def test_a_key_declared_twice_is_repaired_rather_than_a_dead_end(self) -> None:
        """The refusal names this command as its remedy, so the remedy has to be able to
        fix what the refusal refuses."""
        self.written('catchup_window: ""\ncatchup_window: "6h"\n')
        self.assertEqual(read_base_scalar(self.path, "catchup_window"), "")

    def test_a_file_that_does_not_exist_is_created_with_both_closed(self) -> None:
        self.assertTrue(ensure_dag_retry_disabled(self.path))
        assert_catchup_disabled(self.path)
        self.assertEqual(read_base_retry_policy(self.path).limit, 0)



if __name__ == "__main__":
    unittest.main()
