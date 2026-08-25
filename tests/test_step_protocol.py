import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import chdir, redirect_stderr, redirect_stdout
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import patch

from cairn.__main__ import asks_for_help, main
from cairn.core import CairnError, CommandResult
from cairn.emitters import emit_step, marker_gate
from cairn.layout import occasion_path, reports_directory
from cairn.marker import (
    MARKER_DIRECTORY,
    OCCASION_ENV,
    OCCASION_PATTERN,
    Marker,
    current_key,
    is_fresh,
    marker_path,
    mint_occasion,
    occasion_moment,
    read_marker,
    write_marker,
)
from cairn.plan.schema import SCOPES, normalise
from cairn.protocol import PREAMBLE, STEP_REPORT_SCHEMA, compose_prompt
from cairn.providers import PROVIDER_RUNNERS
from cairn.topology import node_name

CAIRN_ROOT = Path(__file__).resolve().parent.parent
PROTOCOL_DOC = CAIRN_ROOT / "docs" / "step-protocol.md"
# Pinned to a year the suite can never be run in, so a key read from the wall clock
# instead of from the occasion cannot coincide with the expected one and pass by luck.
OCCASION = "20190412T215838Z-a6beaee2"
LATER_OCCASION = "20190413T010203Z-00ff00ff"
EARLY_WEEK_OCCASION = "20190103T090000Z-0000beef"

def reports_of(root: Path, run_id: str = "run-1") -> Path:
    """Where a run's accounts land, composed the way a step composes it for itself."""
    return reports_directory(root / "runs", run_id)



def runtime_env(root: Path, step_id: str = "step_a", **extra: str) -> dict[str, str]:
    return {
        "DAG_RUN_ID": "run-1",
        "DAG_RUN_STEP_NAME": step_id,
        "DAG_RUN_WORK_DIR": str(root),
        "CAIRN_RUNS_DIR": str(root / "runs"),
        **extra,
    }


def plan_step(
    scope: str = "once",
    task: str = "Bring the config schema to a state where it carries a sentinel field.",
    kind: str = "agent.claude",
    reads: list[str] | None = None,
) -> Any:
    raw: dict[str, Any] = {
        "plan": {"slug": "p", "title": "P", "source": "README.md"},
        "steps": [
            {
                "id": "a",
                "slug": "a",
                "title": "A",
                "task": task,
                "verify": "test -f result",
                "kind": kind,
                "scope": scope,
                "reads": reads or [],
            }
        ],
    }
    if kind == "command":
        raw["steps"][0]["command"] = "printf '%s' hello"
        raw["steps"][0]["command_type"] = "exec"
    return normalise(raw)["steps"][0]


def run_cli(
    arguments: list[str], env: dict[str, str], root: Path | None = None
) -> tuple[int, str, str]:
    """Invoke the command line the way an emitted step does: its identity, its directory."""
    out, err = StringIO(), StringIO()
    directory = Path.cwd() if root is None else root
    with (
        patch.dict(os.environ, env, clear=True),
        chdir(directory),
        redirect_stdout(out),
        redirect_stderr(err),
    ):
        code = main(arguments)
    return code, out.getvalue(), err.getvalue()


def report_for(root: Path, step_id: str = "step_a") -> dict[str, Any]:
    payload: Any = json.loads((reports_of(root) / f"{step_id}.json").read_text())
    return payload


def work_report(
    root: Path,
    step_id: str,
    status: str = "done",
    summary: str = "did the work",
    run_id: str = "run-1",
    needs_user_decision: bool = False,
    runs_root: Path | None = None,
) -> Path:
    """The account the work step left, which the marker write quotes and the gate reads."""
    name = node_name("work", step_id)
    directory = (
        reports_of(root, run_id)
        if runs_root is None
        else reports_directory(runs_root, run_id)
    )
    path = directory / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "step_id": name,
                "run_id": run_id,
                "status": status,
                "duration": 0.1,
                "working_directory": str(root),
                "summary": summary,
                "follow_up_work": [],
                "needs_user_decision": needs_user_decision,
                "cause": None,
                "detail": {},
            }
        )
    )
    return path


class TheProtocolIsStatedOnce(unittest.TestCase):
    """The preamble and the report schema live in one place, provably."""

    def test_preamble_is_reproduced_verbatim_in_the_document(self) -> None:
        self.assertIn(PREAMBLE.strip(), PROTOCOL_DOC.read_text(encoding="utf-8"))

    def test_report_schema_is_reproduced_in_the_document(self) -> None:
        blocks = re.findall(r"```json\n(.*?)```", PROTOCOL_DOC.read_text(), re.DOTALL)
        parsed = [json.loads(block) for block in blocks]
        self.assertIn(STEP_REPORT_SCHEMA, parsed)

    def test_prompt_is_the_protocol_then_the_task(self) -> None:
        composed = compose_prompt("Do the thing.")
        self.assertTrue(composed.startswith(PREAMBLE))
        self.assertTrue(composed.rstrip().endswith("Do the thing."))

    def test_preamble_never_asks_the_agent_to_record_completion(self) -> None:
        self.assertIn("Completion is recorded by the verification", PREAMBLE)
        self.assertNotIn(MARKER_DIRECTORY, PREAMBLE)

    def sources(self) -> list[Path]:
        return [
            path
            for pattern in ("cairn/**/*.py", "docs/*.md", "README.md")
            for path in CAIRN_ROOT.glob(pattern)
        ]

    def test_the_preamble_and_the_schema_are_stated_nowhere_else(self) -> None:
        """`only place` is the exit criterion; containment alone would not notice a copy."""
        # `additionalProperties` appears only where the schema is stated; the field names
        # appear wherever a report is built or read, which is use rather than statement.
        for label, needle in (
            ("preamble", PREAMBLE.splitlines()[0]),
            ("schema", "additionalProperties"),
        ):
            holders = {
                path.name
                for path in self.sources()
                if needle in path.read_text(encoding="utf-8")
            }
            with self.subTest(statement=label):
                self.assertEqual(holders, {"protocol.py", "step-protocol.md"})

    def test_no_document_reaches_outside_the_package(self) -> None:
        """The directory is extracted by a move, so a link that escapes it dies there."""
        escaping = {
            path.name
            for path in CAIRN_ROOT.glob("**/*.md")
            if "](../../" in path.read_text(encoding="utf-8")
        }
        self.assertEqual(escaping, set())


class TheCommandLineDescribesItself(unittest.TestCase):
    def test_a_step_whose_own_argument_is_a_help_flag_still_runs(self) -> None:
        """A help request is a leading run of subcommand names, never an option's value.

        Reading it any looser lets a task whose text happens to be `--help` print usage
        and exit zero over work that never happened, which the engine reads as success.
        """
        self.assertTrue(asks_for_help(["--help"]))
        self.assertTrue(asks_for_help(["agent", "run", "--help"]))
        self.assertFalse(asks_for_help(["agent", "run", "--prompt", "--help"]))
        self.assertFalse(asks_for_help(["exec", "--command", "grep --help x"]))
        self.assertFalse(asks_for_help(["exec", "--command", "true"]))

    def test_a_help_flag_the_dispatch_reaches_is_skew_not_a_usage_message(self) -> None:
        """The dispatch parser has no help, so nothing can exit zero over unrun work."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            code, _, _ = run_cli(
                ["exec", "--command", "printf ran > out.txt", "--help"],
                runtime_env(root),
                root,
            )
            self.assertNotEqual(code, 0, "a step that did nothing must not report success")
            self.assertFalse((root / "out.txt").exists())
            self.assertEqual(report_for(root)["cause"], "invalid_arguments")


class TheOccasion(unittest.TestCase):
    def test_minted_occasion_carries_its_own_moment(self) -> None:
        moment = datetime(2026, 8, 9, 21, 58, 38, tzinfo=UTC)
        occasion = mint_occasion(moment)
        self.assertIsNotNone(OCCASION_PATTERN.match(occasion))
        self.assertEqual(occasion_moment(occasion), moment)

    def test_two_mints_are_distinct_occasions(self) -> None:
        moment = datetime(2026, 8, 9, 21, 58, 38, tzinfo=UTC)
        self.assertNotEqual(mint_occasion(moment), mint_occasion(moment))

    def test_a_value_cairn_did_not_mint_is_refused(self) -> None:
        with self.assertRaisesRegex(CairnError, "occasion"):
            occasion_moment("yesterday")

    def test_occasion_new_prints_a_usable_occasion(self) -> None:
        code, out, _ = run_cli(["occasion", "new"], {})
        self.assertEqual(code, 0)
        self.assertIsNotNone(OCCASION_PATTERN.match(out.strip()))


class FreshnessKeys(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.runs_root = self.root / "runs"
        self.addCleanup(self.temporary.cleanup)

    def key(self, scope: str, occasion: str = OCCASION, **kwargs: Any) -> str:
        return current_key(
            scope,
            root=self.root,
            environment={OCCASION_ENV: occasion},
            runs_root=self.runs_root,
            run_id="run-1",
            **kwargs,
        )

    def unsupplied(self, scope: str, run_id: str = "run-1", **kwargs: Any) -> str:
        """The key for a caller who supplied no occasion, which is every scheduled firing."""
        return current_key(
            scope,
            root=self.root,
            environment={},
            runs_root=self.runs_root,
            run_id=run_id,
            **kwargs,
        )

    def test_once_needs_no_occasion_at_all(self) -> None:
        self.assertEqual(self.unsupplied("once"), "once")
        self.assertFalse(occasion_path(self.runs_root, "run-1").exists())

    def test_run_scope_is_the_occasion(self) -> None:
        self.assertEqual(self.key("run"), OCCASION)
        self.assertNotEqual(self.key("run", LATER_OCCASION), self.key("run"))

    def test_periods_bucket_the_occasion_moment(self) -> None:
        self.assertEqual(self.key("hourly"), "2019-04-12T21")
        self.assertEqual(self.key("daily"), "2019-04-12")
        self.assertEqual(self.key("weekly"), "2019-W15")
        self.assertEqual(self.key("monthly"), "2019-04")

    def test_an_iso_week_is_zero_padded(self) -> None:
        """The key is compared against markers already committed, so its format is frozen."""
        self.assertEqual(self.key("weekly", EARLY_WEEK_OCCASION), "2019-W01")

    def test_a_period_key_ignores_the_wall_clock(self) -> None:
        """A run that crosses midnight must not bucket its own steps into two days."""
        for scope in ("hourly", "daily", "weekly", "monthly"):
            with self.subTest(scope=scope):
                self.assertNotIn(
                    datetime.now(UTC).strftime("%Y"), self.key(scope)
                )

    def test_a_later_occasion_moves_the_period(self) -> None:
        self.assertNotEqual(self.key("daily", LATER_OCCASION), self.key("daily"))
        self.assertEqual(self.key("monthly", LATER_OCCASION), self.key("monthly"))

    def test_every_scope_but_once_and_inputs_mints_an_occasion_when_none_was_supplied(
        self,
    ) -> None:
        """A caller who supplies none is the ordinary case, not an error.

        A cron firing has no override point at all, so a scope that refused without a
        supplied occasion would refuse every scheduled run. The run mints one instead, and
        records it, so every gate in that run keys on one value.
        """
        for scope in ("run", "hourly", "daily", "weekly", "monthly"):
            with self.subTest(scope=scope):
                self.assertTrue(self.unsupplied(scope))
                recorded = occasion_path(self.runs_root, "run-1").read_text().strip()
                self.assertIsNotNone(OCCASION_PATTERN.match(recorded))

    def test_every_scope_in_one_run_keys_on_the_same_minted_occasion(self) -> None:
        """The gate fails open and the marker write fails closed, so a disagreement between
        them would either re-pay for ever or skip for ever."""
        self.assertEqual(self.unsupplied("run"), self.unsupplied("run"))
        minted = occasion_path(self.runs_root, "run-1").read_text().strip()
        self.assertEqual(self.unsupplied("run"), minted)

    def test_a_second_run_mints_a_second_occasion(self) -> None:
        """Which is what makes a `run`-scoped step do its work on every firing."""
        self.assertNotEqual(self.unsupplied("run"), self.unsupplied("run", run_id="run-2"))

    def test_a_supplied_occasion_outranks_the_recorded_one(self) -> None:
        """The parameter is the override a recovery uses to continue an earlier occasion."""
        self.unsupplied("run")
        self.assertEqual(self.key("run"), OCCASION)

    def test_an_occasion_that_is_not_one_is_refused_rather_than_keyed_on(self) -> None:
        with self.assertRaises(CairnError):
            current_key(
                "run",
                root=self.root,
                environment={OCCASION_ENV: "not-an-occasion"},
                runs_root=self.runs_root,
                run_id="run-1",
            )

    def test_inputs_key_follows_the_declared_files(self) -> None:
        (self.root / "a.txt").write_text("one")
        (self.root / "b.txt").write_text("two")
        before = self.key("inputs", reads=["a.txt", "b.txt"])
        self.assertEqual(before, self.key("inputs", reads=["b.txt", "a.txt"]))
        (self.root / "a.txt").write_text("changed")
        self.assertNotEqual(before, self.key("inputs", reads=["a.txt", "b.txt"]))

    def test_inputs_key_follows_a_rename(self) -> None:
        """Derived work is stale when its inputs move, and a rename is a move."""
        (self.root / "before.txt").write_text("same bytes")
        before = self.key("inputs", reads=["."])
        (self.root / "before.txt").rename(self.root / "after.txt")
        self.assertNotEqual(before, self.key("inputs", reads=["."]))

    def test_a_declared_input_that_is_not_there_yet_is_a_state(self) -> None:
        absent = self.key("inputs", reads=["late.txt"])
        (self.root / "late.txt").write_text("arrived")
        self.assertNotEqual(absent, self.key("inputs", reads=["late.txt"]))

    def test_a_declared_directory_is_hashed_file_by_file(self) -> None:
        corpus = self.root / "data" / "corpus"
        corpus.mkdir(parents=True)
        (corpus / "one.txt").write_text("one")
        before = self.key("inputs", reads=["data/corpus/"])
        (corpus / "two.txt").write_text("two")
        self.assertNotEqual(before, self.key("inputs", reads=["data/corpus/"]))

    def test_naming_metadata_as_an_input_is_refused(self) -> None:
        write_marker(self.root, "b", "run-1", "once", "once", "done")
        (self.root / ".git").mkdir()
        for declared in (MARKER_DIRECTORY, ".git"):
            with (
                self.subTest(declared=declared),
                self.assertRaisesRegex(CairnError, "metadata"),
            ):
                self.key("inputs", reads=[declared])

    def test_a_whole_tree_read_passes_over_metadata_and_stays_stable(self) -> None:
        """Otherwise the step keys itself on its own first marker and every commit."""
        (self.root / "kept.txt").write_text("kept")
        before = self.key("inputs", reads=["."])
        write_marker(self.root, "b", "run-1", "once", "once", "done")
        (self.root / ".git").mkdir()
        (self.root / ".git" / "HEAD").write_text("ref: refs/heads/main")
        self.assertEqual(before, self.key("inputs", reads=["."]))
        (self.root / "kept.txt").write_text("moved")
        self.assertNotEqual(before, self.key("inputs", reads=["."]))

    def test_a_directory_a_later_step_will_fill_is_a_state_not_a_refusal(self) -> None:
        (self.root / "out").mkdir()
        empty = self.key("inputs", reads=["out"])
        (self.root / "out" / "built.txt").write_text("built")
        self.assertNotEqual(empty, self.key("inputs", reads=["out"]))

    def test_a_walk_passes_over_what_it_may_not_key_on_rather_than_refusing(self) -> None:
        """One stray entry must not make a whole declaration permanently unkeyable."""
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, outside)
        (outside / "shared.txt").write_text("elsewhere")
        (self.root / "src").mkdir()
        (self.root / "src" / "a.txt").write_text("real input")
        before = self.key("inputs", reads=["src"])
        (self.root / "src" / "escape").symlink_to(outside / "shared.txt")
        self.assertEqual(before, self.key("inputs", reads=["src"]))

    def test_a_plan_directory_named_like_the_marker_directory_is_still_an_input(self) -> None:
        """`.steps` is Cairn's only at the worktree root; a nested one is someone's data."""
        (self.root / "plans" / MARKER_DIRECTORY).mkdir(parents=True)
        (self.root / "plans" / MARKER_DIRECTORY / "real.md").write_text("one")
        before = self.key("inputs", reads=["plans"])
        (self.root / "plans" / MARKER_DIRECTORY / "real.md").write_text("edited")
        self.assertNotEqual(before, self.key("inputs", reads=["plans"]))

    def test_inputs_refuses_a_path_outside_the_working_directory(self) -> None:
        with self.assertRaisesRegex(CairnError, "outside"):
            self.key("inputs", reads=["../escape.txt"])

    def test_inputs_refuses_an_empty_declaration(self) -> None:
        with self.assertRaisesRegex(CairnError, "at least one path"):
            self.key("inputs", reads=[])

    def test_every_declarable_scope_has_a_key(self) -> None:
        (self.root / "a.txt").write_text("one")
        for scope in SCOPES:
            with self.subTest(scope=scope):
                reads = ["a.txt"] if scope == "inputs" else []
                self.assertTrue(self.key(scope, reads=reads))


class MarkerFile(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.addCleanup(self.temporary.cleanup)

    def test_marker_lives_beside_the_work_it_describes(self) -> None:
        path = write_marker(self.root, "config_schema", "run-1", "once", "once", "did it")
        self.assertEqual(path, self.root / ".steps" / "config_schema.done")
        self.assertEqual(read_marker(self.root, "config_schema"), {
            "step_id": "config_schema",
            "run_id": "run-1",
            "scope": "once",
            "key": "once",
            "summary": "did it",
        })

    def test_absent_marker_is_absence_not_an_error(self) -> None:
        self.assertIsNone(read_marker(self.root, "never_ran"))

    def test_a_step_id_the_engine_would_reject_never_becomes_a_path(self) -> None:
        for hostile in ("../escape", "a/b", "Step", ""):
            with self.subTest(hostile=hostile), self.assertRaises(CairnError):
                marker_path(self.root, hostile)

    def test_a_damaged_marker_is_loud(self) -> None:
        path = marker_path(self.root, "a")
        path.parent.mkdir(parents=True)
        path.write_text("{not json")
        with self.assertRaisesRegex(CairnError, "invalid|Expecting"):
            read_marker(self.root, "a")

    def test_a_marker_missing_a_field_is_loud(self) -> None:
        path = marker_path(self.root, "a")
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"step_id": "a", "scope": "once"}))
        with self.assertRaisesRegex(CairnError, "missing"):
            read_marker(self.root, "a")

    def test_once_accepts_any_recorded_key(self) -> None:
        marker: Marker = {
            "step_id": "a",
            "run_id": "run-1",
            "scope": "daily",
            "key": "2026-01-01",
            "summary": "",
        }
        self.assertTrue(is_fresh(marker, "once", "once"))

    def test_a_key_from_another_scope_is_stale(self) -> None:
        marker: Marker = {
            "step_id": "a",
            "run_id": "run-1",
            "scope": "daily",
            "key": "2026-08-09",
            "summary": "",
        }
        self.assertTrue(is_fresh(marker, "daily", "2026-08-09"))
        self.assertFalse(is_fresh(marker, "daily", "2026-08-10"))
        self.assertFalse(is_fresh(marker, "weekly", "2026-08-09"))


class TheGate(unittest.TestCase):
    """`marker absent` decides whether a step starts at all."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.addCleanup(self.temporary.cleanup)

    def gate(self, *arguments: str, **env: str) -> tuple[int, str]:
        code, _, err = run_cli(
            ["marker", "absent", *arguments], runtime_env(self.root, **env), self.root
        )
        return code, err

    def reports(self) -> list[Path]:
        directory = reports_of(self.root)
        return sorted(directory.glob("*.json")) if directory.is_dir() else []

    def test_a_fresh_step_runs_and_leaves_no_report(self) -> None:
        code, _ = self.gate("--step", "a", "--scope", "once")
        self.assertEqual(code, 0)
        self.assertEqual(self.reports(), [])

    def test_a_completed_step_is_skipped_and_recorded_as_a_no_op(self) -> None:
        write_marker(self.root, "a", "run-1", "once", "once", "already done")
        code, _ = self.gate("--step", "a", "--scope", "once")
        self.assertEqual(code, 1)
        report = report_for(self.root)
        self.assertEqual(report["status"], "noop")
        self.assertEqual(report["summary"], "already done")
        self.assertEqual(report["detail"]["scope"], "once")
        self.assertEqual(report["detail"]["recorded_key"], "once")

    def test_the_no_op_report_names_the_current_and_the_recorded_key(self) -> None:
        """`once` matches whatever is recorded, so the report is the only place the two
        can be told apart — and the run record is what tells the operator why."""
        write_marker(self.root, "a", "run-1", "weekly", "2019-W15", "researched last week")
        self.assertEqual(self.gate("--step", "a", "--scope", "once")[0], 1)
        self.assertEqual(
            report_for(self.root)["detail"],
            {
                "scope": "once",
                "key": "once",
                "recorded_scope": "weekly",
                "recorded_key": "2019-W15",
                "recorded_run": "run-1",
            },
        )

    def test_a_stale_key_reopens_the_work(self) -> None:
        write_marker(self.root, "a", "run-1", "run", OCCASION, "done last time")
        skipped, _ = self.gate(
            "--step", "a", "--scope", "run", **{OCCASION_ENV: OCCASION}
        )
        self.assertEqual(skipped, 1)
        reopened, _ = self.gate(
            "--step", "a", "--scope", "run", **{OCCASION_ENV: LATER_OCCASION}
        )
        self.assertEqual(reopened, 0)

    def test_a_period_holds_across_occasions_and_expires_with_the_period(self) -> None:
        write_marker(self.root, "a", "run-1", "daily", "2019-04-12", "researched")
        same_day, _ = self.gate(
            "--step", "a", "--scope", "daily", **{OCCASION_ENV: OCCASION}
        )
        self.assertEqual(same_day, 1)
        tomorrow, _ = self.gate(
            "--step", "a", "--scope", "daily", **{OCCASION_ENV: LATER_OCCASION}
        )
        self.assertEqual(tomorrow, 0)

    def test_recovering_the_same_occasion_no_ops_and_says_which_one(self) -> None:
        """The pair that matters: a new occasion redoes the work, a recovery does not."""
        write_marker(self.root, "a", "run-1", "run", OCCASION, "researched")
        recovered, _ = self.gate(
            "--step", "a", "--scope", "run", **{OCCASION_ENV: OCCASION}
        )
        self.assertEqual(recovered, 1, "recovering an occasion must not redo the work")
        self.assertEqual(
            report_for(self.root)["detail"],
            {
                "scope": "run",
                "key": OCCASION,
                "recorded_scope": "run",
                "recorded_key": OCCASION,
                "recorded_run": "run-1",
            },
        )
        fresh_occasion, _ = self.gate(
            "--step", "a", "--scope", "run", **{OCCASION_ENV: LATER_OCCASION}
        )
        self.assertEqual(fresh_occasion, 0, "a new occasion must redo the work")

    def test_a_fault_the_gate_cannot_classify_still_lets_the_work_happen(self) -> None:
        """The gate cannot enumerate what it survives.

        An exception escaping the gate leaves Python to exit nonzero, which the engine
        reads as a fresh marker and answers by skipping the step — so a fault the gate
        did not anticipate would silently drop the work out of a run that still reports
        success.
        """
        write_marker(self.root, "a", "run-1", "once", "once", "done")
        faults: list[BaseException] = [
            RuntimeError("something nobody predicted"),
            MemoryError(),
            UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
        ]
        for fault in faults:
            with self.subTest(fault=type(fault).__name__):
                with patch("cairn.__main__.read_marker", side_effect=fault):
                    code, err = self.gate("--step", "a", "--scope", "once")
                self.assertEqual(code, 0)
                self.assertIn("running the step", err)
        self.assertEqual(self.reports(), [])

    def test_a_marker_recorded_for_another_step_is_not_honoured(self) -> None:
        write_marker(self.root, "a", "run-1", "once", "once", "done")
        marker_path(self.root, "b").write_text(
            json.dumps({"step_id": "a", "run_id": "run-1", "scope": "once", "key": "once",
                        "summary": "s"})
        )
        code, err = self.gate("--step", "b", "--scope", "once")
        self.assertEqual(code, 0)
        self.assertIn("records step", err)

    def test_a_marker_whose_fields_are_not_strings_is_damaged(self) -> None:
        path = marker_path(self.root, "a")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"step_id": "a", "scope": 7, "key": None, "summary": {"x": 1}})
        )
        code, err = self.gate("--step", "a", "--scope", "once")
        self.assertEqual(code, 0)
        self.assertIn("not a string", err)

    def test_a_marker_that_is_not_utf8_is_damaged_rather_than_fatal(self) -> None:
        path = marker_path(self.root, "a")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'{"step_id":"a","scope":"once","key":"once","summary":"\xff"}')
        self.assertEqual(self.gate("--step", "a", "--scope", "once")[0], 0)

    def test_an_occasion_the_calendar_rejects_is_an_occasion_error(self) -> None:
        """The pattern admits a thirteenth month; only the parse can refuse one."""
        write_marker(self.root, "a", "run-1", "daily", "2026-08-09", "done")
        code, err = self.gate(
            "--step", "a", "--scope", "daily", **{OCCASION_ENV: "20261345T996060Z-aaaaaaaa"}
        )
        self.assertEqual(code, 0)
        self.assertIn("occasion", err)

    def test_a_mistyped_verb_never_reports_on_a_step_that_did_not_run(self) -> None:
        code, _, _ = run_cli(
            ["marker", "absnet", "--step", "a", "--scope", "once"],
            runtime_env(self.root),
            self.root,
        )
        self.assertEqual(code, 0)
        self.assertEqual(self.reports(), [])

    def test_every_error_lets_the_work_happen_and_says_so(self) -> None:
        write_marker(self.root, "a", "run-1", "run", OCCASION, "done")
        for arguments, env in (
            (["--step", "a", "--scope", "run"], {OCCASION_ENV: "not-an-occasion"}),
            (["--step", "a", "--scope", "inputs"], {}),
            (["--step", "a"], {}),
            (["--nonsense"], {}),
        ):
            with self.subTest(arguments=arguments):
                code, err = self.gate(*arguments, **env)
                self.assertEqual(code, 0)
                self.assertTrue(err.strip())
        self.assertEqual(self.reports(), [])

    def test_missing_runtime_identity_lets_the_work_happen(self) -> None:
        code, _, err = run_cli(["marker", "absent", "--step", "a", "--scope", "once"], {}, self.root)
        self.assertEqual(code, 0)
        self.assertIn("DAG_RUN_ID", err)

    def test_a_damaged_marker_lets_the_work_happen(self) -> None:
        path = marker_path(self.root, "a")
        path.parent.mkdir(parents=True)
        path.write_text("{not json")
        code, err = self.gate("--step", "a", "--scope", "once")
        self.assertEqual(code, 0)
        self.assertIn("marker gate", err)


class MarkerWrite(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.addCleanup(self.temporary.cleanup)

    def write(self, *arguments: str, **env: str) -> int:
        code, _, _ = run_cli(
            ["marker", "write", *arguments], runtime_env(self.root, **env), self.root
        )
        return code

    def test_a_no_op_leaves_the_run_that_did_the_work_named(self) -> None:
        """The marker is the only durable answer to who did the work, and a recovery run
        that redid none of it must not claim it."""
        work_report(self.root, "a", summary="did the work")
        self.assertEqual(self.write("--step", "a", "--scope", "once"), 0)
        first = read_marker(self.root, "a")
        assert first is not None
        self.assertEqual(first["run_id"], "run-1")

        work_report(self.root, "a", status="noop", summary="did the work", run_id="run-2")
        self.assertEqual(
            self.write("--step", "a", "--scope", "once", DAG_RUN_ID="run-2"), 0
        )
        second = read_marker(self.root, "a")
        assert second is not None
        self.assertEqual(second["run_id"], "run-1", "a no-op claimed work it did not do")

    def test_verification_records_the_key_the_work_was_done_under(self) -> None:
        work_report(self.root, "a", summary="refreshed the summary")
        code = self.write(
            "--step", "a", "--scope", "daily",
            **{OCCASION_ENV: OCCASION},
        )
        self.assertEqual(code, 0)
        self.assertEqual(
            read_marker(self.root, "a"),
            {
                "step_id": "a",
                "run_id": "run-1",
                "scope": "daily",
                "key": "2019-04-12",
                "summary": "refreshed the summary",
            },
        )
        report = report_for(self.root)
        self.assertEqual(report["status"], "done")
        self.assertEqual(report["detail"]["key"], "2019-04-12")

    def test_writing_twice_converges(self) -> None:
        work_report(self.root, "a", summary="s")
        for _ in range(2):
            self.assertEqual(self.write("--step", "a", "--scope", "once"), 0)
        self.assertEqual(len(list((self.root / MARKER_DIRECTORY).iterdir())), 1)

    def test_the_marker_quotes_the_steps_own_account_of_what_it_did(self) -> None:
        """Only the step that did the work can say what it did, and the marker outlives it."""
        work_report(self.root, "a", summary="sentinel field added to the config schema")
        self.assertEqual(self.write("--step", "a", "--scope", "once"), 0)
        marker = read_marker(self.root, "a")
        assert marker is not None
        self.assertEqual(marker["summary"], "sentinel field added to the config schema")

    def test_a_step_that_left_no_report_of_this_run_is_never_marked(self) -> None:
        work_report(self.root, "a", run_id="an-earlier-run")
        self.assertEqual(self.write("--step", "a", "--scope", "once"), 1)
        self.assertIsNone(read_marker(self.root, "a"))
        self.assertEqual(report_for(self.root)["cause"], "missing_report")

    def test_a_marker_speaks_only_for_the_step_it_names(self) -> None:
        """One step's verification must never stand in for another's."""
        work_report(self.root, "first", summary="first done")
        self.write("--step", "first", "--scope", "once")
        self.assertEqual(read_marker(self.root, "first"), {
            "step_id": "first", "run_id": "run-1", "scope": "once", "key": "once",
            "summary": "first done",
        })
        self.assertIsNone(read_marker(self.root, "second"))
        code, _, _ = run_cli(
            ["marker", "absent", "--step", "second", "--scope", "once"],
            runtime_env(self.root),
            self.root,
        )
        self.assertEqual(code, 0, "an unverified step was gated by another step's marker")

    def test_a_failure_to_key_the_work_is_a_report_not_a_traceback(self) -> None:
        """The write fails closed, so an occasion it cannot key on leaves no marker.

        A caller who supplies nothing is the ordinary case and mints one; what cannot be
        keyed on is a value that is present and is not an occasion.
        """
        work_report(self.root, "a", summary="s")
        self.assertEqual(
            self.write("--step", "a", "--scope", "run", **{OCCASION_ENV: "not-an-occasion"}),
            1,
        )
        self.assertIsNone(read_marker(self.root, "a"))
        report = report_for(self.root)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["cause"], "invalid_occasion")

    def test_a_run_scoped_write_with_no_supplied_occasion_mints_and_records_one(self) -> None:
        """Which is every scheduled firing: cron has no override point at all."""
        work_report(self.root, "a", summary="s")
        self.assertEqual(self.write("--step", "a", "--scope", "run"), 0)
        marker = read_marker(self.root, "a")
        assert marker is not None
        self.assertIsNotNone(OCCASION_PATTERN.match(marker["key"]))

    def test_the_write_sweeps_its_own_fragments_from_the_directory_the_commit_stages(
        self,
    ) -> None:
        markers = self.root / MARKER_DIRECTORY
        markers.mkdir()
        for name in (".build.done.abcd1234.tmp", ".build.done.beef5678.tmp"):
            (markers / name).write_text("fragment")
        work_report(self.root, "build", summary="did it")
        self.write("--step", "build", "--scope", "once")
        self.assertEqual(
            sorted(path.name for path in markers.iterdir()), ["build.done"]
        )

    def test_the_sweep_leaves_a_fragment_another_step_is_still_writing(self) -> None:
        """Steps sharing a working directory write their markers at once, and a fragment
        is the file another writer is about to move into place."""
        markers = self.root / MARKER_DIRECTORY
        markers.mkdir()
        stale = markers / ".build.done.abcd1234.tmp"
        stale.write_text("a fragment this step's killed writer left")
        live = markers / ".other.done.beef5678.tmp"
        live.write_text("a fragment another step is writing now")

        work_report(self.root, "build", summary="did it")
        self.write("--step", "build", "--scope", "once")

        self.assertTrue(live.exists(), "another step's live fragment was collected")
        self.assertFalse(stale.exists(), "this step's own fragment was left behind")

    def test_the_marker_carries_one_line_however_long_the_step_answered(self) -> None:
        """The marker reaches git, and a step's account of itself has no fixed length."""
        work_report(self.root, "a", summary="first line\nsecond line " + "x" * 400)
        self.assertEqual(self.write("--step", "a", "--scope", "once"), 0)
        marker = read_marker(self.root, "a")
        assert marker is not None
        self.assertNotIn("\n", marker["summary"])
        self.assertLessEqual(len(marker["summary"]), 200)
        self.assertTrue(marker["summary"].startswith("first line second line"))
        self.assertTrue(marker["summary"].endswith("…"))

    def test_a_marker_the_repository_would_not_carry_is_refused(self) -> None:
        """An ignored marker is completion state no later run can see."""
        run = ("git", "-C", str(self.root))
        subprocess.run((*run, "init", "-q"), check=True)
        (self.root / ".gitignore").write_text(".*\n")
        work_report(self.root, "a", summary="did it")
        code = self.write("--step", "a", "--scope", "once")
        self.assertEqual(code, 1)
        self.assertIsNone(read_marker(self.root, "a"))
        self.assertEqual(report_for(self.root)["cause"], "marker_ignored")

    def test_the_marker_lands_where_a_commit_of_the_work_carries_it(self) -> None:
        run = ("git", "-C", str(self.root))
        subprocess.run((*run, "init", "-q"), check=True)
        subprocess.run((*run, "config", "user.email", "t@example.com"), check=True)
        subprocess.run((*run, "config", "user.name", "T"), check=True)
        (self.root / "work.txt").write_text("the work")
        work_report(self.root, "a", summary="did the work")
        self.write("--step", "a", "--scope", "once")
        subprocess.run((*run, "add", "-A"), check=True)
        subprocess.run((*run, "commit", "-qm", "work"), check=True)
        listing = subprocess.run(
            (*run, "show", "--name-only", "--format=", "HEAD"),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.split()
        self.assertIn("work.txt", listing)
        self.assertIn(".steps/a.done", listing)


class TheGateInTheEmittedWorkflow(unittest.TestCase):
    def test_every_plan_kind_is_gated_and_carries_the_skip_flag(self) -> None:
        for kind in ("agent.claude", "command"):
            with self.subTest(kind=kind):
                emitted = emit_step(plan_step(kind=kind), "/repo")
                condition = emitted["preconditions"][0]["condition"]
                self.assertIn("marker absent", condition)
                self.assertIn("--step a", condition)
                self.assertEqual(
                    emitted["continue_on"], {"failure": True, "skipped": True}
                )

    def test_the_gate_is_one_quoted_invocation(self) -> None:
        gate = marker_gate(plan_step(scope="daily"))
        self.assertEqual(gate, shlex.join(shlex.split(gate)))
        self.assertIn("--scope daily", gate)

    def test_declared_reads_reach_the_gate_only_where_they_are_hashed(self) -> None:
        self.assertIn(
            "--reads data.csv",
            marker_gate(plan_step(scope="inputs", reads=["data.csv"])),
        )
        self.assertNotIn(
            "--reads", marker_gate(plan_step(scope="once", reads=["data.csv"]))
        )

    def test_a_plan_declaring_no_scope_gates_on_a_plain_existence_check(self) -> None:
        gate = marker_gate(plan_step())
        self.assertTrue(gate.endswith("--scope once"))

    def test_the_preamble_stays_out_of_the_step_argv(self) -> None:
        emitted = emit_step(plan_step(), "/repo")
        self.assertNotIn(PREAMBLE.split("\n")[0], emitted["run"])


class TheTaskIsProseTheEmitterNeverReads(unittest.TestCase):
    """Emission acts on the graph's declared values, never on what a task's words mean.

    Whether a task will duplicate on a resumed run is a reading of the plan, and the
    derivation declares it as a `non_convergent_task` question the author answers
    ([plan-derivation.md]). Nothing at emission re-reads the sentence.
    """

    def test_a_task_is_emitted_verbatim_whatever_its_phrasing(self) -> None:
        task = "Append a row to the changelog."
        emitted = emit_step(plan_step(task=task), "/repo")
        self.assertIn(shlex.quote(task), emitted["run"])

    def test_an_inputs_step_that_could_never_be_keyed_is_refused(self) -> None:
        """Otherwise it does its work, fails its marker write, and repeats every run."""
        with self.assertRaisesRegex(ValueError, "reads nothing"):
            emit_step(plan_step(scope="inputs"), "/repo")
        with self.assertRaisesRegex(ValueError, "outside"):
            emit_step(plan_step(scope="inputs", reads=["/etc/hosts"]), "/repo")


class TheThreeStates(unittest.TestCase):
    """A fresh step works and is marked, a completed one no-ops, a killed one resumes."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.addCleanup(self.temporary.cleanup)

    def test_a_fresh_step_does_the_work_and_verification_marks_it(self) -> None:
        env = runtime_env(self.root)
        self.assertEqual(run_cli(["marker", "absent", "--step", "a", "--scope", "once"], env, self.root)[0], 0)
        run_cli(["exec", "--command", "printf built > built.txt"], env, self.root)
        self.assertEqual((self.root / "built.txt").read_text(), "built")
        work_report(self.root, "a", summary="built")
        run_cli(["marker", "write", "--step", "a", "--scope", "once"], env, self.root)
        self.assertEqual(run_cli(["marker", "absent", "--step", "a", "--scope", "once"], env, self.root)[0], 1)

    def test_a_completed_step_starts_no_agent_session(self) -> None:
        """The gate decides before any session is opened, so a no-op costs nothing."""
        sessions: list[str] = []

        def record(prompt: str, *_args: Any, **_kwargs: Any) -> CommandResult:
            sessions.append(prompt)
            return CommandResult(0, "done", "ran", [], False, None, {})

        env = runtime_env(self.root)
        gate = ["marker", "absent", "--step", "a", "--scope", "once"]
        agent = ["agent", "run", "--provider", "echo", "--prompt", "do the work"]

        def run_the_step() -> int:
            with patch.dict(PROVIDER_RUNNERS, {"echo": record}):
                if run_cli(gate, env, self.root)[0] != 0:
                    return 1
                run_cli(agent, env, self.root)
                return 0

        self.assertEqual(run_the_step(), 0)
        self.assertEqual(len(sessions), 1, "the first run must open a session")

        work_report(self.root, "a", summary="ran")
        run_cli(
            ["marker", "write", "--step", "a", "--scope", "once"],
            env,
            self.root,
        )
        self.assertEqual(run_the_step(), 1, "the gate must skip the second run")
        self.assertEqual(len(sessions), 1, "the completed step opened a second session")

    def test_a_step_killed_mid_edit_resumes_without_duplicating(self) -> None:
        """Kill for real: the half-done safety the whole protocol rests on.

        The work is append-shaped so duplication is observable, and convergent so it must
        not happen. A blind `>>` fails this; only the guarded append survives it.
        """
        converge = "grep -qxF MARK ledger.txt 2>/dev/null || printf 'MARK\\n' >> ledger.txt"
        script = f"{converge}; printf 'x' > {self.root / 'started'}; sleep 30"
        env = {**os.environ, **runtime_env(self.root), "PYTHONPATH": str(CAIRN_ROOT)}
        child = subprocess.Popen(
            [sys.executable, "-m", "cairn", "exec", "--command", script],
            cwd=self.root,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 15
        while not (self.root / "started").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue((self.root / "started").exists(), "the child never started")
        child.send_signal(signal.SIGTERM)
        self.assertNotEqual(child.wait(timeout=15), 0)

        self.assertEqual((self.root / "ledger.txt").read_text(), "MARK\n")
        self.assertIsNone(read_marker(self.root, "a"))
        self.assertEqual(report_for(self.root)["cause"], "cancelled")

        # Re-running the identical step absorbs the partial tree rather than repeating it.
        run_cli(
            ["exec", "--command", converge],
            runtime_env(self.root),
            self.root,
        )
        self.assertEqual((self.root / "ledger.txt").read_text().count("MARK"), 1)

    def test_a_blind_append_is_what_convergence_is_measured_against(self) -> None:
        """The control for the test above: without convergence, re-running duplicates."""
        blind = "printf 'MARK\\n' >> ledger.txt"
        for _ in range(2):
            run_cli(["exec", "--command", blind], runtime_env(self.root), self.root)
        self.assertEqual((self.root / "ledger.txt").read_text().count("MARK"), 2)

    def test_an_excluded_step_is_re_attempted_on_the_next_run(self) -> None:
        """Verification never ran, so there is no marker to make the next run skip it."""
        env = runtime_env(self.root)
        failed, _, _ = run_cli(["exec", "--command", "exit 3"], env, self.root)
        self.assertNotEqual(failed, 0)
        self.assertIsNone(read_marker(self.root, "a"))
        self.assertEqual(
            run_cli(["marker", "absent", "--step", "a", "--scope", "once"], env, self.root)[0], 0
        )

    def test_no_subcommand_but_verification_writes_a_marker(self) -> None:
        """A marker means verified, never claimed, whatever a step believes about itself."""
        env = runtime_env(self.root)

        def finished(*_args: Any, **_kwargs: Any) -> CommandResult:
            return CommandResult(0, "done", "finished", [], False, None, {})

        for arguments in (
            ["exec", "--command", "printf ok > out.txt"],
            ["wait", "--for", "0.01", "--timeout", "5"],
            ["agent", "run", "--provider", "echo", "--prompt", "do it"],
        ):
            with (
                self.subTest(subcommand=arguments[0]),
                patch.dict(PROVIDER_RUNNERS, {"echo": finished}),
            ):
                run_cli(arguments, env, self.root)
                self.assertFalse(
                    (self.root / MARKER_DIRECTORY).exists(),
                    f"{arguments[0]} recorded its own completion",
                )

    def test_a_marker_on_an_unmerged_branch_leaves_the_trunk_re_attempting(self) -> None:
        """Marker visibility follows the merge, which is the whole exclusion mechanism."""
        git = ("git", "-C", str(self.root))
        subprocess.run((*git, "init", "-q", "-b", "main"), check=True)
        subprocess.run((*git, "config", "user.email", "t@example.com"), check=True)
        subprocess.run((*git, "config", "user.name", "T"), check=True)
        (self.root / "seed.txt").write_text("seed")
        subprocess.run((*git, "add", "-A"), check=True)
        subprocess.run((*git, "commit", "-qm", "seed"), check=True)

        subprocess.run((*git, "checkout", "-q", "-b", "step/a"), check=True)
        (self.root / "work.txt").write_text("the work")
        marked = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, marked)
        work_report(self.root, "a", summary="the work", runs_root=marked)
        run_cli(
            ["marker", "write", "--step", "a", "--scope", "once"],
            runtime_env(self.root, CAIRN_RUNS_DIR=str(marked)),
            self.root,
        )
        subprocess.run((*git, "add", "-A"), check=True)
        subprocess.run((*git, "commit", "-qm", "work"), check=True)

        gate = ["marker", "absent", "--step", "a", "--scope", "once"]
        # The run directory is the engine's, outside the repository, so the gate's own
        # reports never appear as work in the tree it is deciding about.
        elsewhere = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, elsewhere)
        env = runtime_env(self.root, CAIRN_RUNS_DIR=str(elsewhere))
        self.assertEqual(run_cli(gate, env, self.root)[0], 1, "the branch has its marker")

        subprocess.run((*git, "checkout", "-q", "main"), check=True)
        self.assertEqual(
            run_cli(gate, env, self.root)[0],
            0,
            "an unmerged step must be re-attempted from the trunk",
        )

        subprocess.run((*git, "merge", "-q", "--no-edit", "step/a"), check=True)
        self.assertEqual(
            run_cli(gate, env, self.root)[0], 1, "a merged step must no-op on the trunk"
        )


class TheEngineHonoursTheLowering(unittest.TestCase):
    """Against real Dagu, because the failure mode here reports success."""

    SKIP_ENV = "CAIRN_SKIP_ENGINE_TESTS"
    dagu: ClassVar[str | None] = None

    @classmethod
    def setUpClass(cls) -> None:
        located = subprocess.run(
            ("which", "dagu"), capture_output=True, text=True, check=False
        )
        cls.dagu = located.stdout.strip() or None

    def setUp(self) -> None:
        if self.dagu is None:
            # These cover the one failure mode that reports success, so their absence is
            # declared rather than discovered: a machine without the engine has to say so.
            if os.environ.get(self.SKIP_ENV):
                self.skipTest(f"{self.SKIP_ENV} is set")
            self.fail(
                "dagu is not installed, so the lowering is unverified. Install it, or "
                f"set {self.SKIP_ENV}=1 to record that this run did not check it."
            )
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.addCleanup(self.temporary.cleanup)
        # Named rather than minted, because a run's records are keyed by its identity and
        # a test that let the engine choose could not say where to read them. Taken from
        # the temporary directory so it is unique per test: these runs share the machine's
        # own engine home, where a repeated run id is a conflict rather than a fresh run.
        self.run_id = f"cairn-{self.root.name}"

    def run_dag(self, body: str) -> subprocess.CompletedProcess[str]:
        path = self.root / "plan.yaml"
        path.write_text(body)
        return subprocess.run(
            (str(self.dagu), "start", "--run-id", self.run_id, str(path)),
            capture_output=True,
            text=True,
            check=False,
            cwd=self.root,
        )

    def gated_dag(self, python: str = "python3") -> str:
        """A workflow built from what `emit_step` emits, not from a hand-written likeness.

        The gate command is the emitter's own, so the thing under test is the one a real
        plan would run — including whether that invocation resolves at all.
        """
        emitted = emit_step(plan_step(kind="command"), str(self.root))
        gate = emitted["preconditions"][0]["condition"].replace("python3", python, 1)
        return f"""
type: graph
max_active_steps: 4
retry_policy: {{ limit: 0, interval_sec: 1 }}
env:
  - PYTHONPATH: {CAIRN_ROOT}
  - CAIRN_RUNS_DIR: {self.root / "runs"}
steps:
  - name: work_a
    working_dir: {emitted["working_dir"]}
    timeout_sec: {emitted["timeout_sec"]}
    run: sh -c 'printf ran >> {self.root / "ran.txt"}'
    preconditions:
      - condition: {shlex.quote(gate)}
    continue_on: {{ skipped: {str(emitted["continue_on"]["skipped"]).lower()} }}
  - name: verify_a
    depends: work_a
    working_dir: {self.root}
    run: sh -c 'printf verified >> {self.root / "verified.txt"}'
"""

    def test_a_gated_step_that_skips_still_lets_its_dependents_run(self) -> None:
        """Without `continue_on: {skipped: true}` the skip cascades to verify and commit."""
        write_marker(self.root, "a", "run-1", "once", "once", "already done")
        completed = self.run_dag(self.gated_dag())
        self.assertIn("[skipped]", completed.stdout)
        self.assertFalse((self.root / "ran.txt").exists())
        self.assertTrue((self.root / "verified.txt").exists())

    def test_the_gate_records_the_no_op_the_run_would_otherwise_report_as_clean(self) -> None:
        write_marker(self.root, "a", "run-1", "once", "once", "already done")
        completed = self.run_dag(self.gated_dag())
        self.assertEqual(completed.returncode, 0)
        report: Any = json.loads((reports_of(self.root, self.run_id) / "work_a.json").read_text())
        self.assertEqual(report["status"], "noop")
        self.assertEqual(report["detail"]["scope"], "once")
        self.assertEqual(report["detail"]["recorded_key"], "once")

    def test_an_unmarked_step_runs(self) -> None:
        self.run_dag(self.gated_dag())
        self.assertEqual((self.root / "ran.txt").read_text(), "ran")
        self.assertTrue((self.root / "verified.txt").exists())

    def test_a_gate_that_cannot_launch_skips_the_step_into_a_clean_run(self) -> None:
        """The one hazard the gate cannot answer, which the generator's preflight owns.

        Fail-open is a property of the gate running. A condition that never launches exits
        nonzero from outside Cairn, and the engine reads that as a skip — so a plan whose
        Cairn does not resolve evaporates into a green result with nothing done.
        """
        completed = self.run_dag(self.gated_dag(python="/nonexistent/python3"))
        self.assertEqual(completed.returncode, 0, "the run reports clean")
        self.assertIn("[skipped]", completed.stdout)
        self.assertFalse((self.root / "ran.txt").exists(), "no work happened")


if __name__ == "__main__":
    unittest.main()
