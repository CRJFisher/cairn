import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from itertools import pairwise
from pathlib import Path
from typing import Any, ClassVar, cast

from cairn.core import ENDED_WITHOUT_REPORTING
from cairn.emitters import (
    emit_commit,
    emit_marker,
    emit_step,
    emit_verify,
    verify_gate,
)
from cairn.layout import reports_directory
from cairn.plan.assertions import AnswerError, answer, propose, render, tally
from cairn.plan.cli import main as plan_main
from cairn.plan.report import render as render_report
from cairn.plan.schema import (
    ENGINE_NAME_MAX_BYTES,
    Assertion,
    Graph,
    has_assertion,
    is_unasserted,
    is_unverified,
    normalise,
)
from cairn.plan.validate import validate
from cairn.topology import Node
from cairn.verify import (
    BRANCH,
    CHAIN,
    EXCLUSION_CAUSES,
    GATE_EXCLUDE_IT,
    GATE_RECORD_IT,
    REPORTED_NOTHING,
    REPORTED_UNREADABLE,
    Divergence,
    divergence_line,
    exit_status_reference,
    judge,
    mark_name,
    verify_handle,
    verify_name,
    work_name,
)
from cairn.workflow.build import envelope

# The engine mints a run id when none is given, and a run's records are keyed by it.
ENGINE_RUN_ID = "gate-engine-run"

def reports_of(root: Path, run_id: str = "run-1") -> Path:
    """Where a run's accounts land, composed the way a step composes it for itself."""
    return reports_directory(root / "runs", run_id)


from tests.test_step_protocol import run_cli, runtime_env, work_report

CAIRN_ROOT = Path(__file__).resolve().parent.parent
GATE_DOC = CAIRN_ROOT / "docs" / "verify-gate.md"
FIXTURES = CAIRN_ROOT / "fixtures" / "plans"
NODE_STATUS = re.compile(r"^[├└│─ ]+([a-z_0-9]+)(?: \([^)]*\))? \[(\w+)\]", re.MULTILINE)

# The two real plan documents in the corpus. Every step either contains no assertion
# and no answer, which is what the authoring conversation exists for.
REAL_PLANS = ("worktree-hydration", "pattern-lifecycle")


def report(
    status: str = "done",
    summary: str = "did it",
    blocked: bool = False,
    cause: str | None = None,
) -> dict[str, Any]:
    return {
        "step_id": "a",
        "run_id": "run-1",
        "status": status,
        "summary": summary,
        "needs_user_decision": blocked,
        "cause": cause,
    }


def plan_graph(
    verify: str | None = "test -f built.txt",
    assertion: Assertion | None = None,
    command: str = "printf built > built.txt",
    step_id: str = "a",
    deps: list[str] | None = None,
    kind: str = "command",
) -> Any:
    step: dict[str, Any] = (
        {"kind": kind}
        if kind.startswith("agent.")
        else {
            "kind": "command",
            "command": command,
            "command_type": "wait_until" if kind == "wait_until" else "exec",
        }
    )
    raw: dict[str, Any] = {
        "plan": {"slug": "p", "title": "P", "source": "README.md"},
        "steps": [
            {
                "id": step_id,
                "slug": step_id,
                "title": step_id.upper(),
                "task": "Bring the tree to a state where the artefact is there.",
                **step,
                "verify": verify,
                "assertion": assertion,
                "deps": [
                    {"id": dep, "origin": "declared", "evidence": "stated"}
                    for dep in deps or []
                ],
            }
        ],
    }
    return raw


def one_step(**kwargs: Any) -> Any:
    return normalise(plan_graph(**kwargs))["steps"][0]


def verification(step: Any, working_directory: str, position: str) -> list[dict[str, Any]]:
    """The engine steps a step's verification becomes, in the order the topology derives.

    The topology decides which of these nodes exist and what each depends on; this reads
    the same predicate so a test can exercise a body without deriving a whole graph.
    """
    marker = emit_marker(step, working_directory, position)
    if not has_assertion(step):
        return [marker]
    return [emit_verify(step, working_directory), marker]


def run_plan(arguments: list[str]) -> int:
    """Drive the derivation-time command line without its output reaching the suite's."""
    with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
        return plan_main(arguments)


def fixture(name: str) -> Graph:
    with open(FIXTURES / name / "graph.json", encoding="utf-8") as handle:
        return normalise(json.load(handle))


class TheAssertionRunsBare(unittest.TestCase):
    """Nothing of Cairn's stands between the plan's command and the engine."""

    def test_the_plans_command_is_the_step_body_byte_for_byte(self) -> None:
        for command in ("test -f a", "pytest -q && ruff check .", "grep -q 'x: y' a.yml"):
            with self.subTest(command=command):
                emitted = emit_verify(one_step(verify=command), "/repo")
                self.assertEqual(emitted["run"], command)

    def test_no_cairn_invocation_appears_in_the_assertion(self) -> None:
        emitted = emit_verify(one_step(), "/repo")
        self.assertNotIn("cairn", emitted["run"])

    def test_an_assertion_is_bounded_and_never_retried(self) -> None:
        """A fact check retried is a different question, so it is asked exactly once."""
        emitted = emit_verify(one_step(), "/repo")
        self.assertEqual(emitted["retry_policy"], {"limit": 0, "interval_sec": 1})
        self.assertGreater(emitted["timeout_sec"], 0)

    def test_the_assertion_stands_where_the_step_it_asserts_stood(self) -> None:
        emitted = emit_verify(one_step(), "/worktrees/a")
        self.assertEqual(emitted["working_dir"], "/worktrees/a")

    def test_the_assertion_survives_its_own_failure_so_the_run_reaches_the_join(self) -> None:
        self.assertEqual(emit_verify(one_step(), "/repo")["continue_on"], {"failure": True})

    def test_the_assertion_carries_no_marker_gate(self) -> None:
        """A no-op run still asserts: the gate belongs to the work, not to the check."""
        self.assertNotIn("preconditions", emit_verify(one_step(), "/repo"))

    def test_an_assertion_that_cannot_fail_is_refused(self) -> None:
        for command in ("true", ":", "exit 0", "  "):
            with self.subTest(command=command), self.assertRaises(ValueError):
                emit_step(one_step(verify=command), "/repo")


class TheGateIsEmittedWithTheStepItGates(unittest.TestCase):
    def test_the_marker_write_is_a_step_of_its_own_gated_on_the_assertion(self) -> None:
        nodes = verification(one_step(), "/repo", BRANCH)
        self.assertEqual([node["name"] for node in nodes], ["verify_a", "mark_a"])
        self.assertIn("marker write", nodes[1]["run"])
        self.assertNotIn("marker write", nodes[0]["run"])

    def test_the_gate_reads_the_assertions_exit_status_by_the_engines_own_name(self) -> None:
        condition = emit_marker(one_step(), "/repo", BRANCH)["preconditions"][0]["condition"]
        self.assertIn("verify gate", condition)
        self.assertIn("${verify_a.exit_code}", condition)

    def test_the_gate_is_one_quoted_invocation_that_survives_the_reference(self) -> None:
        """The engine substitutes into the raw condition before any shell sees it."""
        gate = verify_gate(one_step(), BRANCH)
        self.assertEqual(gate, shlex.join(shlex.split(gate)))

    def test_the_marker_never_carries_the_flag_that_would_commit_over_a_closed_gate(
        self,
    ) -> None:
        """Measured: the flag here lets the commit run when the gate refused to record."""
        for position in (BRANCH, CHAIN):
            with self.subTest(position=position):
                self.assertNotIn("continue_on", emit_marker(one_step(), "/repo", position))

    def test_the_two_positions_differ_only_in_the_position_each_records(self) -> None:
        branch = emit_marker(one_step(), "/repo", BRANCH)
        chain = emit_marker(one_step(), "/repo", CHAIN)
        self.assertEqual(
            {key: value for key, value in branch.items() if key != "preconditions"},
            {key: value for key, value in chain.items() if key != "preconditions"},
        )
        self.assertEqual(
            branch["preconditions"][0]["condition"].replace(BRANCH, CHAIN),
            chain["preconditions"][0]["condition"],
        )

    def test_an_unknown_position_is_refused_rather_than_guessed(self) -> None:
        with self.assertRaises(ValueError):
            emit_marker(one_step(), "/repo", "somewhere")

    def test_the_work_step_survives_its_own_failure_so_its_assertion_still_runs(self) -> None:
        """A step that reports failure over work that is there must still be asked."""
        emitted = emit_step(one_step(), "/repo")
        self.assertEqual(emitted["continue_on"], {"failure": True, "skipped": True})

    def test_the_engine_id_the_reference_names_stays_inside_the_engines_bound(self) -> None:
        long_id = "bring_the_architecture_page_into_line_with_the_current_module_layout"
        handle = verify_handle(long_id)
        self.assertLessEqual(len(handle), ENGINE_NAME_MAX_BYTES)
        self.assertRegex(handle, r"^[a-z][a-z0-9_]*$")
        self.assertIn(handle, exit_status_reference(long_id))
        self.assertNotEqual(handle, verify_handle(long_id + "_x"))

    def test_every_corpus_step_yields_an_id_the_engine_accepts(self) -> None:
        for name in sorted(path.name for path in FIXTURES.iterdir()):
            for step in fixture(name)["steps"]:
                with self.subTest(fixture=name, step=step["id"]):
                    self.assertLessEqual(len(verify_handle(step["id"])), ENGINE_NAME_MAX_BYTES)


class NothingEmittedCanHideAFailure(unittest.TestCase):
    """The two constructs that would let a failed assertion read as a success."""

    def nodes(self) -> list[dict[str, Any]]:
        """Every node every emitter can produce, not only the shape one kind takes.

        The construct forbidden on a step's own output is forbidden because for an agent
        step that output is self-report, so an agent node the scan never sees is the one
        node it most needed to cover.
        """
        emitted: list[dict[str, Any]] = []
        for position in (BRANCH, CHAIN):
            for verify, assertion in (
                ("test -f built.txt", None),
                (None, Assertion(outcome="declined", proposed="test -e x", reason="prose")),
            ):
                for kind in ("command", "wait_until", "agent.claude", "agent.echo"):
                    step = one_step(verify=verify, assertion=assertion, kind=kind)
                    emitted.append(emit_step(step, "/repo"))
                    emitted.extend(verification(step, "/repo", position))
        return emitted

    def keys(self, node: object, prefix: str = "") -> list[str]:
        if not isinstance(node, dict):
            return []
        found: list[str] = []
        for key, value in cast(dict[str, object], node).items():
            name = f"{prefix}.{key}" if prefix else key
            found.append(name)
            found.extend(self.keys(value, name))
        return found

    def test_no_emitted_node_carries_mark_success(self) -> None:
        for node in self.nodes():
            self.assertNotIn("mark_success", self.keys(node), node["name"])

    def test_no_emitted_node_routes_on_a_steps_own_output(self) -> None:
        """For an agent step stdout is self-report, so routing on it is routing on a claim."""
        for node in self.nodes():
            self.assertNotIn("continue_on.output", self.keys(node), node["name"])

    def test_the_only_routing_keys_emitted_are_failure_and_skipped(self) -> None:
        for node in self.nodes():
            self.assertLessEqual(
                set(node.get("continue_on", {})), {"failure", "skipped"}, node["name"]
            )

    def test_every_emitted_node_is_bounded_and_stands_somewhere(self) -> None:
        """A built-in action is the substrate's own node and carries neither."""
        for node in self.nodes():
            if "action" in node:
                continue
            self.assertGreater(node["timeout_sec"], 0, node["name"])
            self.assertTrue(node["working_dir"], node["name"])


class TheGateJudges(unittest.TestCase):
    """Verify owns the green light; self-report owns the veto; neither is resolved away."""

    def test_a_passing_assertion_over_a_step_that_says_it_worked_records_it(self) -> None:
        verdict = judge(0, report())
        self.assertTrue(verdict["record"])
        self.assertIsNone(verdict["cause"])
        self.assertIsNone(verdict["divergence"])

    def test_a_no_op_is_recorded_like_any_other_verified_step(self) -> None:
        self.assertTrue(judge(0, report(status="noop"))["record"])

    def test_a_failed_assertion_over_a_claimed_success_is_a_divergence(self) -> None:
        verdict = judge(1, report())
        self.assertFalse(verdict["record"])
        self.assertEqual(verdict["cause"], "verify_failed")
        self.assertEqual(verdict["divergence"], Divergence(reported="done", asserted=False))

    def test_a_passing_assertion_over_a_claimed_failure_is_a_divergence(self) -> None:
        verdict = judge(0, report(status="failed"))
        self.assertFalse(verdict["record"], "self-report vetoes what verify would allow")
        self.assertEqual(verdict["cause"], "reported_failure")
        self.assertEqual(verdict["divergence"], Divergence(reported="failed", asserted=True))

    def test_a_divergence_names_no_winner(self) -> None:
        for verify_exit, status in ((1, "done"), (0, "failed")):
            divergence = judge(verify_exit, report(status=status))["divergence"]
            assert divergence is not None
            self.assertEqual(set(divergence), {"reported", "asserted"})

    def test_agreement_on_failure_is_not_a_divergence(self) -> None:
        self.assertIsNone(judge(1, report(status="failed"))["divergence"])

    def test_a_step_blocked_on_a_person_is_neither_recorded_nor_failed(self) -> None:
        verdict = judge(0, report(blocked=True))
        self.assertFalse(verdict["record"])
        self.assertEqual(verdict["cause"], "user_decision_required")

    def test_a_step_that_left_no_report_of_this_run_never_ran(self) -> None:
        """A cascade-skipped step still runs its assertion, which can pass over nothing."""
        verdict = judge(0, None)
        self.assertFalse(verdict["record"])
        self.assertEqual(verdict["cause"], "not_reached")

    def test_a_step_with_no_checkable_effect_routes_on_its_report_alone(self) -> None:
        self.assertTrue(judge(None, report())["record"])
        self.assertEqual(judge(None, report(status="failed"))["cause"], "reported_failure")
        self.assertIsNone(judge(None, report(status="failed"))["divergence"])

    def test_every_cause_the_gate_reaches_is_in_the_frozen_set(self) -> None:
        for verify_exit in (None, 0, 1):
            for status in ("done", "noop", "failed"):
                for blocked in (False, True):
                    cause = judge(verify_exit, report(status=status, blocked=blocked))["cause"]
                    self.assertIn(cause, (None, *EXCLUSION_CAUSES))
        self.assertIn(judge(0, None)["cause"], EXCLUSION_CAUSES)


class TheGateFailsClosed(unittest.TestCase):
    """The exact inverse of the marker gate, and the asymmetry is the design."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.addCleanup(self.temporary.cleanup)

    def gate(self, *arguments: str) -> tuple[int, str, str]:
        return run_cli(["verify", *arguments], runtime_env(self.root), self.root)

    def test_a_verified_step_opens_the_gate(self) -> None:
        work_report(self.root, "a")
        code, _, _ = self.gate("gate", "--step", "a", "--position", BRANCH, "--verify-exit", "0")
        self.assertEqual(code, GATE_RECORD_IT)

    def test_the_gate_writes_nothing_on_the_path_where_a_step_will_run(self) -> None:
        """A report there would outlive a step that was then killed."""
        work_report(self.root, "a")
        self.gate("gate", "--step", "a", "--position", BRANCH, "--verify-exit", "0")
        self.assertFalse((reports_of(self.root) / "step_a.json").exists())

    def test_a_closed_gate_records_where_no_step_will_run_to_record(self) -> None:
        work_report(self.root, "a")
        code, _, _ = self.gate("gate", "--step", "a", "--position", CHAIN, "--verify-exit", "1")
        self.assertEqual(code, GATE_EXCLUDE_IT)
        written: Any = json.loads((reports_of(self.root) / "step_a.json").read_text())
        self.assertEqual(written["cause"], "verify_failed")
        self.assertEqual(written["detail"]["position"], CHAIN)
        self.assertEqual(written["detail"]["divergence"], {"reported": "done", "asserted": False})

    def test_every_fault_closes_the_gate(self) -> None:
        cases = {
            "no report at all": ("--step", "a", "--position", BRANCH, "--verify-exit", "0"),
            "an exit status that is not a number": (
                "--step", "a", "--position", BRANCH, "--verify-exit", "${verify_a.exit_code}",
            ),
            "an unknown position": ("--step", "a", "--position", "sideways", "--verify-exit", "0"),
            "a missing step": ("--position", BRANCH, "--verify-exit", "0"),
            "a mistyped verb": ("gates", "--step", "a", "--position", BRANCH),
        }
        for label, arguments in cases.items():
            with self.subTest(fault=label):
                verb = () if arguments[0] == "gates" else ("gate",)
                code, _, _ = self.gate(*verb, *arguments)
                self.assertEqual(code, GATE_EXCLUDE_IT)

    def test_a_fault_the_gate_did_not_anticipate_still_closes_it(self) -> None:
        """The catch-all is the branch a fail-open mutation hides in, so it is driven."""
        work_report(self.root, "a")
        env = runtime_env(self.root)
        env.pop("CAIRN_RUNS_DIR")
        code, _, _ = run_cli(
            ["verify", "gate", "--step", "a", "--position", BRANCH, "--verify-exit", "0"],
            env,
            self.root,
        )
        self.assertEqual(code, GATE_EXCLUDE_IT)

    def test_every_closed_gate_leaves_an_account_of_itself(self) -> None:
        """An exclusion no report explains is an exclusion the run record cannot name."""
        work_report(self.root, "a")
        faults = {
            "an exit status that is not a number": (
                "--step", "a", "--position", BRANCH, "--verify-exit", "${verify_a.exit_code}",
            ),
            "an unknown position": ("--step", "a", "--position", "sideways", "--verify-exit", "0"),
            "arguments it cannot read": ("--step", "a", "--elsewhere", "0"),
        }
        for label, arguments in faults.items():
            with self.subTest(fault=label):
                path = reports_of(self.root) / "step_a.json"
                path.unlink(missing_ok=True)
                code, _, stderr = self.gate("gate", *arguments)
                self.assertEqual(code, GATE_EXCLUDE_IT)
                self.assertIn("verify gate [", stderr, "an exclusion is never silent")
                written: Any = json.loads(path.read_text())
                self.assertIn(written["cause"], EXCLUSION_CAUSES)

    def test_a_status_the_report_shape_does_not_promise_closes_the_gate(self) -> None:
        """Every reader of a report asks one question, and reads anything but `failed`
        as a green light."""
        path = work_report(self.root, "a")
        payload: Any = json.loads(path.read_text())
        payload["status"] = "succeeded"
        path.write_text(json.dumps(payload))
        code, _, _ = self.gate("gate", "--step", "a", "--position", BRANCH, "--verify-exit", "0")
        self.assertEqual(code, GATE_EXCLUDE_IT)
        written: Any = json.loads((reports_of(self.root) / "step_a.json").read_text())
        self.assertEqual(written["cause"], "gate_indeterminate")

    def test_a_report_from_another_run_cannot_open_the_gate(self) -> None:
        work_report(self.root, "a", run_id="an-earlier-run")
        code, _, _ = self.gate("gate", "--step", "a", "--position", BRANCH, "--verify-exit", "0")
        self.assertEqual(code, GATE_EXCLUDE_IT)
        written: Any = json.loads((reports_of(self.root) / "step_a.json").read_text())
        self.assertEqual(written["cause"], "not_reached")

    def test_a_damaged_report_is_not_recorded_as_a_step_that_never_ran(self) -> None:
        path = work_report(self.root, "a")
        path.write_text("{not json")
        self.gate("gate", "--step", "a", "--position", BRANCH, "--verify-exit", "0")
        written: Any = json.loads((reports_of(self.root) / "step_a.json").read_text())
        self.assertEqual(written["cause"], "gate_indeterminate")

    def test_the_marker_gate_opens_on_the_faults_this_one_closes_on(self) -> None:
        """Redoing convergent work is cheap; a marker over unverified work is not."""
        marker_gate_code, _, _ = run_cli(
            ["marker", "absent", "--step", "a", "--scope", "nonsense"],
            runtime_env(self.root),
            self.root,
        )
        verify_gate_code, _, _ = self.gate("gate", "--step", "a", "--position", "nonsense")
        self.assertEqual(marker_gate_code, 0, "the marker gate runs the work again")
        self.assertEqual(verify_gate_code, 1, "the verify gate records nothing")


class AStepWithNothingToAssert(unittest.TestCase):
    def declined(self) -> Any:
        return one_step(
            verify=None,
            assertion=Assertion(
                outcome="declined",
                proposed="test -e docs/getting-started.md",
                reason="the end state is prose a command cannot read",
            ),
        )

    def test_a_declared_unverified_step_emits_no_assertion(self) -> None:
        nodes = verification(self.declined(), "/repo", BRANCH)
        self.assertEqual([node["name"] for node in nodes], ["mark_a"])

    def test_an_unverified_step_is_gated_on_its_own_report_alone(self) -> None:
        condition = verification(self.declined(), "/repo", BRANCH)[0]["preconditions"][0][
            "condition"
        ]
        self.assertIn("verify gate", condition)
        self.assertNotIn("--verify-exit", condition)

    def test_a_step_nobody_was_asked_about_never_reaches_the_engine(self) -> None:
        """Authoring warns; emission refuses. Otherwise `unverified` means nothing.

        Every step becomes a work node, so refusing there refuses the whole step group.
        """
        with self.assertRaisesRegex(ValueError, "nobody has been asked"):
            emit_step(one_step(verify=None), "/repo")

    def test_a_step_id_in_the_namespace_the_gate_derives_names_from_is_refused(self) -> None:
        """Otherwise it and another step's derived node are one node in the workflow."""
        graph = plan_graph(step_id="verify_a")
        graph["plan"]["sources"] = [{"path": "README.md", "sha256": "x" * 64}]
        self.assertIn("reserved_id", [f.code for f in validate(graph).errors])
        graph = plan_graph(step_id="marker_a")
        graph["plan"]["sources"] = [{"path": "README.md", "sha256": "x" * 64}]
        self.assertNotIn("reserved_id", [f.code for f in validate(graph).errors])

    def test_a_declined_step_and_an_unasked_one_are_different_states(self) -> None:
        self.assertTrue(is_unverified(self.declined()))
        self.assertFalse(is_unasserted(self.declined()))
        self.assertTrue(is_unasserted(one_step(verify=None)))
        self.assertFalse(is_unverified(one_step(verify=None)))

    def test_the_report_names_every_unverified_step_beside_the_proposal_it_declined(self) -> None:
        graph = normalise(
            plan_graph(
                verify=None,
                assertion=Assertion(
                    outcome="declined",
                    proposed="test -e docs/getting-started.md",
                    reason="the end state is prose a command cannot read",
                ),
            )
        )
        text = render_report(graph)
        self.assertIn("**unverified**", text)
        self.assertIn("test -e docs/getting-started.md", text)
        self.assertIn("the end state is prose a command cannot read", text)

    def test_a_declared_unverified_step_is_a_warning_on_every_report(self) -> None:
        graph = plan_graph(
            verify=None,
            assertion=Assertion(outcome="declined", proposed=None, reason="prose only"),
        )
        graph["plan"]["sources"] = [{"path": "README.md", "sha256": "x" * 64}]
        result = validate(graph)
        self.assertIn("unverified_step", [finding.code for finding in result.warnings])
        self.assertNotIn("missing_verify", [finding.code for finding in result.warnings])


class AnAnswerAndItsCommandSayTheSameThing(unittest.TestCase):
    def verdict(self, **kwargs: Any) -> list[str]:
        graph = plan_graph(**kwargs)
        graph["plan"]["sources"] = [{"path": "README.md", "sha256": "x" * 64}]
        return [finding.code for finding in validate(graph).errors]

    def test_a_declined_step_cannot_also_carry_a_command(self) -> None:
        self.assertIn(
            "schema",
            self.verdict(
                verify="test -f a",
                assertion=Assertion(outcome="declined", proposed=None, reason="why"),
            ),
        )

    def test_a_declined_step_must_say_why(self) -> None:
        self.assertIn(
            "schema",
            self.verdict(
                verify=None, assertion=Assertion(outcome="declined", proposed="x", reason=None)
            ),
        )

    def test_an_accepted_proposal_is_the_command_that_was_proposed(self) -> None:
        self.assertIn(
            "schema",
            self.verdict(
                verify="test -f a",
                assertion=Assertion(outcome="accepted", proposed="test -f b", reason=None),
            ),
        )
        self.assertEqual(
            self.verdict(
                verify="test -f a",
                assertion=Assertion(outcome="accepted", proposed="test -f a", reason=None),
            ),
            [],
        )

    def test_an_edited_proposal_differs_from_the_one_that_was_proposed(self) -> None:
        self.assertIn(
            "schema",
            self.verdict(
                verify="test -f a",
                assertion=Assertion(outcome="edited", proposed="test -f a", reason=None),
            ),
        )

    def test_a_command_a_human_authored_is_not_an_invented_one(self) -> None:
        """`invented_verify` stops a derivation fabricating a command, not a person writing one."""
        source = FIXTURES / "linear-chain"
        graph = fixture("linear-chain")
        graph["steps"][0]["verify"] = "test -e a-command-no-document-gives"
        graph["steps"][0]["assertion"] = Assertion(
            outcome="edited", proposed="test -e something-else", reason=None
        )
        codes = [f.code for f in validate(graph, source_root=str(source)).errors]
        self.assertNotIn("invented_verify", codes)

        graph["steps"][0]["assertion"] = None
        codes = [f.code for f in validate(graph, source_root=str(source)).errors]
        self.assertIn("invented_verify", codes)


class TheMissingVerifyConversation(unittest.TestCase):
    def test_the_offer_is_the_derivations_own_declaration_on_the_graph(self) -> None:
        """`propose` carries the reading the derivation recorded; it composes nothing.

        The graph's `missing_verify` question is the only source of an offer, so what the
        worksheet shows and what `answer` records cannot disagree about what was offered.
        """
        for name in REAL_PLANS:
            graph = fixture(name)
            declared = {
                question["step"]: (question["proposed"], question["evidence"])
                for question in graph["questions"]
                if question["kind"] == "missing_verify"
            }
            proposals = propose(graph)
            self.assertEqual({p["step"] for p in proposals}, set(declared))
            for proposal in proposals:
                with self.subTest(fixture=name, step=proposal["step"]):
                    offered, acceptance = declared[proposal["step"]]
                    self.assertEqual(proposal["proposed"], offered)
                    self.assertEqual(proposal["acceptance"], acceptance)

    def test_a_step_the_derivation_offered_nothing_for_gets_no_offer(self) -> None:
        for proposal in propose(fixture("no-verify")):
            self.assertIsNone(proposal["proposed"])

    def test_proposing_never_writes_a_command_into_a_graph(self) -> None:
        """A proposal is an offer. Only an answer is a decision, and only `answer` writes."""
        graph = fixture("worktree-hydration")
        before = json.dumps(graph, sort_keys=True)
        propose(graph)
        self.assertEqual(json.dumps(graph, sort_keys=True), before)

    def test_a_proposal_shows_the_step_its_own_words(self) -> None:
        graph = fixture("worktree-hydration")
        text = render(propose(graph))
        for step in graph["steps"]:
            self.assertIn(step["id"], text)
            self.assertIn(step["task"][:40], text)

    def test_a_step_with_nothing_to_offer_says_so_and_still_asks(self) -> None:
        text = render(propose(fixture("no-verify")))
        self.assertIn("The derivation offered nothing", text)
        self.assertIn("--decline", text)

    def test_accepting_editing_and_declining_are_counted_apart(self) -> None:
        graph = fixture("worktree-hydration")
        offers = {p["step"]: p["proposed"] for p in propose(graph)}
        accepted = next(step for step, offer in offers.items() if offer)
        declined = next(step for step in offers if step != accepted)
        answer(graph, accepted, command=cast(str, offers[accepted]), reason=None)
        answer(graph, declined, command=None, reason="prose only")
        counts = tally(graph)
        self.assertEqual(counts["accepted"], 1)
        self.assertEqual(counts["declined"], 1)
        self.assertEqual(counts["edited"], 0)
        self.assertEqual(counts["unasserted"], 3)

    def test_a_command_written_where_nothing_was_offered_edited_no_proposal(self) -> None:
        """Counting it as an edit would say the proposals are carrying weight they are not."""
        graph = fixture("no-verify")
        step_id = graph["steps"][0]["id"]
        answer(graph, step_id, command="test -e a", reason=None)
        assertion = graph["steps"][0]["assertion"]
        assert assertion is not None
        self.assertEqual(assertion["outcome"], "authored")
        self.assertEqual(tally(graph)["edited"], 0)
        self.assertEqual(tally(graph)["authored"], 1)

    def test_an_answer_clears_the_question_it_answers(self) -> None:
        """Otherwise the report asks the author for the command they just supplied."""
        graph = fixture("no-verify")
        step_id = graph["steps"][0]["id"]
        answer(graph, step_id, command="test -e a", reason=None)
        remaining = [
            question["step"]
            for question in graph["questions"]
            if question["kind"] == "missing_verify"
        ]
        self.assertNotIn(step_id, remaining)
        self.assertNotIn(
            "missing_verify", [f.code for f in validate(graph).warnings if f.step == step_id]
        )

    def test_an_assertion_that_cannot_fail_is_refused_while_its_author_is_here(self) -> None:
        graph = fixture("no-verify")
        step_id = graph["steps"][0]["id"]
        for command in ("true", " ", "exit 0"):
            with self.subTest(command=command), self.assertRaises(AnswerError):
                answer(graph, step_id, command=command, reason=None)

    def test_the_worksheet_prints_an_invocation_that_records_the_answer(self) -> None:
        """A worksheet whose instruction has to be corrected before it works records nothing.

        No line restates the offer: it lives on the graph's own question, and `answer`
        records it from there, so no printed invocation can drop or misquote it.
        """
        text = render(propose(fixture("worktree-hydration")), "graph.json")
        self.assertIn("--out graph.json", text)
        self.assertNotIn("--proposed", text)
        offers = [p for p in propose(fixture("worktree-hydration")) if p["proposed"]]
        self.assertTrue(offers, "no step in this plan carries an offer to answer")
        for proposal in offers:
            answers = [
                line
                for line in render([proposal], "graph.json").splitlines()
                if "plan answer" in line
            ]
            self.assertEqual(len(answers), 2, proposal["step"])

    def test_an_answer_that_differs_from_the_offer_is_an_edit(self) -> None:
        graph = fixture("worktree-hydration")
        offered = next(p for p in propose(graph) if p["proposed"])
        answer(graph, offered["step"], command="test -e something-else", reason=None)
        assertion = next(
            step for step in graph["steps"] if step["id"] == offered["step"]
        )["assertion"]
        assert assertion is not None
        self.assertEqual(assertion["outcome"], "edited")
        self.assertEqual(assertion["proposed"], offered["proposed"])

    def test_an_answer_for_a_step_that_is_not_there_is_refused(self) -> None:
        with self.assertRaises(AnswerError):
            answer(fixture("no-verify"), "nowhere", command="test -e a", reason=None)

    def test_an_answer_is_a_command_or_a_decline_and_never_both_or_neither(self) -> None:
        graph = fixture("no-verify")
        step_id = graph["steps"][0]["id"]
        with self.assertRaises(AnswerError):
            answer(graph, step_id, command=None, reason=None)
        with self.assertRaises(AnswerError):
            answer(graph, step_id, command="test -e a", reason="why")

    def test_the_invocation_the_worksheet_prints_records_the_answer(self) -> None:
        """The worksheet's instruction is the operator's whole path, so it is run as printed
        — both forms, in a directory whose name a shell would split."""
        directory = Path(tempfile.mkdtemp()) / "two words"
        directory.mkdir()
        self.addCleanup(shutil.rmtree, directory.parent)
        offers = {p["step"]: p["proposed"] for p in propose(fixture("worktree-hydration"))}

        for form in ("--command", "--decline"):
            graph_path = directory / f"{form.strip('-')}.json"
            shutil.copy(FIXTURES / "worktree-hydration" / "graph.json", graph_path)
            self.assertEqual(run_plan(["propose", str(graph_path)]), 1, "steps are unanswered")
            printed = render(propose(fixture("worktree-hydration")), str(graph_path))
            for line in printed.splitlines():
                stripped = line.strip()
                if not stripped.startswith("python3 -m cairn plan answer"):
                    continue
                arguments = shlex.split(stripped)[4:]
                if (form == "--decline") != ("--decline" in arguments):
                    continue
                self.assertEqual(run_plan(arguments), 0, stripped)

            self.assertEqual(run_plan(["propose", str(graph_path)]), 0, "nothing is left to ask")
            answered = normalise(json.loads(graph_path.read_text(encoding="utf-8")))
            for step in answered["steps"]:
                assertion = step["assertion"]
                assert assertion is not None
                # The offer is recomputed from the unanswered graph, so a printed line
                # that dropped it would be caught rather than agreed with.
                self.assertEqual(assertion["proposed"], offers[step["id"]], step["id"])
                if form == "--decline":
                    self.assertTrue(is_unverified(step), step["id"])
                else:
                    self.assertEqual(
                        assertion["outcome"],
                        "accepted" if offers[step["id"]] else "authored",
                        step["id"],
                    )

    def test_a_decline_with_no_reason_is_refused(self) -> None:
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory)
        graph_path = directory / "graph.json"
        shutil.copy(FIXTURES / "no-verify" / "graph.json", graph_path)
        step_id = fixture("no-verify")["steps"][0]["id"]
        self.assertEqual(
            run_plan(["answer", str(graph_path), "--step", step_id, "--decline"]), 2
        )

    def test_both_real_plans_reach_a_runnable_state_through_their_answers(self) -> None:
        """The exit criterion: a plan arriving with no assertions can be run after the
        conversation, and every step is either asserted or a recorded decline."""
        for name in REAL_PLANS:
            with self.subTest(plan=name):
                graph = normalise(
                    json.loads(
                        (FIXTURES / name / "answered.json").read_text(encoding="utf-8")
                    )
                )
                self.assertTrue(validate(graph).ok, [str(f) for f in validate(graph).errors])
                for step in graph["steps"]:
                    self.assertFalse(is_unasserted(step), step["id"])
                    emit_step(step, "/repo")
                    verification(step, "/repo", BRANCH)

    def test_the_answers_the_two_real_plans_received_are_recorded(self) -> None:
        counts = {"accepted": 0, "edited": 0, "authored": 0, "declined": 0}
        offered = 0
        for name in REAL_PLANS:
            graph = normalise(
                json.loads((FIXTURES / name / "answered.json").read_text(encoding="utf-8"))
            )
            recorded = tally(graph)
            for key in counts:
                counts[key] += recorded[cast(Any, key)]
            # What the extraction rule actually offered, recomputed from the unanswered
            # graph, so the recorded answers cannot drift from the rule that drew them.
            proposals = {p["step"]: p["proposed"] for p in propose(fixture(name))}
            offered += sum(1 for value in proposals.values() if value is not None)
            for step in graph["steps"]:
                assertion = step["assertion"]
                assert assertion is not None
                self.assertEqual(assertion["proposed"], proposals[step["id"]], step["id"])
        self.assertEqual(sum(counts.values()), 8, "every step of both plans was answered")
        self.assertEqual(counts["accepted"] + counts["edited"], offered)
        document = GATE_DOC.read_text(encoding="utf-8")
        self.assertIn(f"{offered} proposals offered", document)
        for outcome, count in counts.items():
            self.assertIn(f"{outcome} {count}", document)


class WhatTheCorpusStates(unittest.TestCase):
    """The figures the documents quote are the corpus that ships, or the test says so."""

    def counts(self) -> tuple[int, int, int]:
        steps = unasserted = real = 0
        for path in sorted(FIXTURES.iterdir()):
            graph = fixture(path.name)
            steps += len(graph["steps"])
            missing = sum(1 for step in graph["steps"] if is_unasserted(step))
            unasserted += missing
            if path.name in REAL_PLANS:
                real += missing
        return steps, unasserted, real

    def test_the_corpus_is_the_one_the_documents_describe(self) -> None:
        steps, unasserted, real = self.counts()
        self.assertEqual((steps, unasserted, real), (41, 10, 8))

    def test_the_document_quotes_the_corpus_it_ships(self) -> None:
        steps, unasserted, real = self.counts()
        text = GATE_DOC.read_text(encoding="utf-8")
        self.assertIn(f"{unasserted} of the corpus's {steps}", text)
        self.assertIn(f"{real} of those {unasserted}", text)


class AStepThatReportedNothingIsNotAStepThatReportedFailure(unittest.TestCase):
    """The runtime writes `failed` for a session that ended without reporting, because that
    is the only status a report can carry when there is nothing to carry. Reading it back as
    a veto tells a person their session claimed a failure it never claimed ([19 D])."""

    def test_the_cause_is_the_protocol_failure_rather_than_a_veto(self) -> None:
        found = judge(0, report(status="failed", cause="provider_protocol"))
        self.assertEqual(found["cause"], "provider_protocol")
        self.assertNotIn("reported failure", found["summary"])

    def test_the_divergence_is_kept_and_says_the_step_said_nothing(self) -> None:
        """It is the only channel the fact has: a mark report contributes its cause, its
        position and its divergence to the record, and neither the gate's summary nor the
        assertion's exit status reaches it. Without this, a step whose work is sitting
        verified in the tree is indistinguishable from one that did nothing."""
        silent = report(status="failed", cause="provider_protocol")
        silent["detail"] = {ENDED_WITHOUT_REPORTING: True}
        found = judge(0, silent)
        self.assertIsNotNone(found["divergence"])
        divergence = cast(Divergence, found["divergence"])
        self.assertEqual(divergence["reported"], REPORTED_NOTHING)
        self.assertTrue(divergence["asserted"])
        self.assertIn("ended without reporting", divergence_line(divergence))
        self.assertNotIn("'failed'", divergence_line(divergence))

    def test_a_step_whose_account_was_unreadable_says_that_instead(self) -> None:
        """`provider_protocol` covers every unreadable-protocol fault and only the
        commonest is a silence. The divergence must agree with the gate's own summary
        beside it, or the record contradicts itself about one step."""
        garbled = judge(0, report(status="failed", cause="provider_protocol"))
        divergence = cast(Divergence, garbled["divergence"])
        self.assertEqual(divergence["reported"], REPORTED_UNREADABLE)
        self.assertIn("no readable account", divergence_line(divergence))
        self.assertIn("no readable account", garbled["summary"])
        self.assertNotIn("ended without reporting", divergence_line(divergence))

    def test_a_failing_assertion_leaves_nothing_to_diverge_from(self) -> None:
        found = judge(1, report(status="failed", cause="provider_protocol"))
        self.assertEqual(found["cause"], "provider_protocol")
        self.assertIsNone(found["divergence"])

    def test_a_step_that_really_reported_failure_is_unchanged(self) -> None:
        """This is what keeps the seam narrow. Without it `judge` would drift into carrying
        any cause through, which would need every runtime cause in the frozen set."""
        found = judge(0, report(status="failed", cause="command_failed"))
        self.assertEqual(found["cause"], "reported_failure")
        self.assertIsNotNone(found["divergence"])
        self.assertEqual(cast(Divergence, found["divergence"])["reported"], "failed")

    def test_a_report_carrying_no_cause_is_read_as_the_steps_own_veto(self) -> None:
        found = judge(0, report(status="failed"))
        self.assertEqual(found["cause"], "reported_failure")

    def test_the_gate_still_closes_over_a_step_that_said_nothing(self) -> None:
        """What must not change: a step that did not say what it did is not recorded done."""
        for verify_exit in (None, 0, 1):
            with self.subTest(verify_exit=verify_exit):
                found = judge(
                    verify_exit, report(status="failed", cause="provider_protocol")
                )
                self.assertFalse(found["record"])


class TheGateIsStatedOnce(unittest.TestCase):
    def test_the_frozen_set_is_exactly_these_causes(self) -> None:
        """Iterating the tuple can only catch an addition; a literal catches a removal too."""
        self.assertEqual(
            EXCLUSION_CAUSES,
            (
                "verify_failed",
                "reported_failure",
                "provider_protocol",
                "user_decision_required",
                "not_reached",
                "gate_indeterminate",
                "timed_out",
                "retry_exhausted",
                "orchestrator_died",
            ),
        )

    def test_the_exclusion_causes_are_reproduced_verbatim_in_the_document(self) -> None:
        text = GATE_DOC.read_text(encoding="utf-8")
        for cause in EXCLUSION_CAUSES:
            self.assertIn(f"`{cause}`", text)

    def test_the_causes_are_named_in_one_document_only(self) -> None:
        needle = "gate_indeterminate"
        holders = {
            path.name
            for pattern in ("cairn/**/*.py", "docs/*.md", "README.md")
            for path in CAIRN_ROOT.glob(pattern)
            if needle in path.read_text(encoding="utf-8")
        }
        self.assertEqual(holders, {"verify.py", "verify-gate.md"})


class TheEngineRoutesFailureByPosition(unittest.TestCase):
    """Against real Dagu, because every failure mode here otherwise reports success."""

    SKIP_ENV = "CAIRN_SKIP_ENGINE_TESTS"
    dagu: ClassVar[str | None] = None

    @classmethod
    def setUpClass(cls) -> None:
        located = subprocess.run(("which", "dagu"), capture_output=True, text=True, check=False)
        cls.dagu = located.stdout.strip() or None

    def setUp(self) -> None:
        if self.dagu is None:
            if os.environ.get(self.SKIP_ENV):
                self.skipTest(f"{self.SKIP_ENV} is set")
            self.fail(
                "dagu is not installed, so the gate is unverified. Install it, or set "
                f"{self.SKIP_ENV}=1 to record that this run did not check it."
            )
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.engine_temporary = tempfile.TemporaryDirectory()
        self.engine = Path(self.engine_temporary.name)
        self.addCleanup(self.temporary.cleanup)
        self.addCleanup(self.engine_temporary.cleanup)
        # The commit node is part of the group under test, so the group runs against a
        # real repository and what did or did not land is read out of git itself.
        for arguments in (
            ("init", "-b", "main"),
            ("config", "user.email", "cairn@example.invalid"),
            ("config", "user.name", "cairn"),
            ("commit", "--allow-empty", "-m", "root"),
        ):
            subprocess.run(("git", *arguments), cwd=self.root, check=True, capture_output=True)

    def commit_node(self, step_id: str, position: str) -> Node:
        return {
            "name": f"commit_{step_id}",
            "role": "commit",
            "step": step_id,
            "wave": 1,
            "working_directory": str(self.root),
            "after": [],
            "max_seconds": 60,
            "detail": {"branch": None, "position": position},
        }

    def lower(self, steps: list[Any], position: str, join: list[str] | None = None) -> list[Any]:
        """Every node under test comes from the emitters, never a hand-written likeness.

        The whole group is lowered, commit included, because the commit is the node that
        routes: a group cut short at the marker cannot show what a closed gate does to the
        work that would otherwise land.

        The emitters build bodies and the topology chains them, so the chaining is done
        here in the order the topology derives.
        """
        nodes: list[Any] = []
        for step in steps:
            work = emit_step(step, str(self.root))
            # The topology names every node `<role>_<subject>`, and the gate reads the work
            # step's report by that name — so the lowering has to use it too.
            work["name"] = work_name(step["id"])
            group: list[Any] = [
                work,
                *verification(step, str(self.root), position),
                emit_commit(self.commit_node(step["id"], position), f"cairn({step['id']})"),
            ]
            for previous, node in pairwise(group):
                node["depends"] = [previous["name"]]
            nodes.extend(group)
        if join is not None:
            nodes.append(
                {
                    "name": "join",
                    "depends": join,
                    "run": "sh -c 'ls .steps > landed.txt'",
                    "working_dir": str(self.root),
                    "timeout_sec": 60,
                    "retry_policy": {"limit": 0, "interval_sec": 1},
                }
            )
        return nodes

    def run_dag(self, nodes: list[Any]) -> subprocess.CompletedProcess[str]:
        workflow = envelope(
            nodes,
            repository=str(self.root),
            parent_branch="main",
            occasion="20260810T000000Z-gatetest",
            python_path=str(CAIRN_ROOT),
            runs_root=str(self.root / "runs"),
        )
        path = self.engine / "plan.yaml"
        path.write_text(json.dumps(workflow, indent=2))
        return subprocess.run(
            (
                str(self.dagu),
                "start",
                "--run-id",
                ENGINE_RUN_ID,
                "--dagu-home",
                str(self.engine / "home"),
                str(path),
            ),
            capture_output=True,
            text=True,
            check=False,
            cwd=self.root,
        )

    def statuses(self, completed: subprocess.CompletedProcess[str]) -> dict[str, str]:
        return dict(NODE_STATUS.findall(completed.stdout))

    def gate_report(self, step_id: str) -> dict[str, Any]:
        payload: Any = json.loads(
            (reports_of(self.root, ENGINE_RUN_ID) / f"{mark_name(step_id)}.json").read_text()
        )
        return payload

    def marker(self, step_id: str) -> Path:
        return self.root / ".steps" / f"{step_id}.done"

    def subjects(self) -> list[str]:
        """What actually reached history, which is the only account no node can overstate."""
        log = subprocess.run(
            ("git", "log", "--format=%s"),
            cwd=self.root,
            check=True,
            capture_output=True,
            text=True,
        )
        return log.stdout.split()

    def test_a_branch_that_reports_success_and_writes_nothing_is_excluded(self) -> None:
        """The differentiating case: the claim is invisible on a happy path, so this is
        the fixture that fails."""
        steps = [
            one_step(step_id="a", command="printf a > a.txt", verify="test -f a.txt"),
            one_step(step_id="b", command="true", verify="test -f b.txt"),
            one_step(step_id="c", command="printf c > c.txt", verify="test -f c.txt"),
        ]
        nodes = self.lower(steps, BRANCH, join=[f"commit_{s['id']}" for s in steps])
        completed = self.run_dag(nodes)
        statuses = self.statuses(completed)

        self.assertNotEqual(completed.returncode, 0, "a run with an exclusion is never clean")
        self.assertNotIn("Result: Succeeded", completed.stdout)
        self.assertEqual(statuses[verify_name("b")], "failed")
        self.assertEqual(statuses[mark_name("b")], "skipped")
        self.assertEqual(statuses["commit_b"], "skipped", "nothing lands over a closed gate")
        self.assertEqual(statuses["commit_a"], "succeeded")
        self.assertEqual(statuses["join"], "succeeded", "the merge still runs")
        # The wave is concurrent, so which of the two verified steps committed first is
        # not the claim — that the excluded one committed at all would be.
        self.assertEqual(
            sorted(self.subjects()), ["cairn(a)", "cairn(c)", "root"], "b reached history"
        )

        work: Any = json.loads((reports_of(self.root, ENGINE_RUN_ID) / f'{work_name("b")}.json').read_text())
        self.assertEqual(work["status"], "done", "the step claimed success")
        self.assertFalse(self.marker("b").exists(), "unverified work was recorded")
        self.assertTrue(self.marker("a").exists())
        self.assertTrue(self.marker("c").exists())
        landed = (self.root / "landed.txt").read_text()
        self.assertIn("a.done", landed)
        self.assertIn("c.done", landed)
        self.assertNotIn("b.done", landed)

        recorded = self.gate_report("b")
        self.assertEqual(recorded["cause"], "verify_failed")
        self.assertEqual(recorded["detail"]["position"], BRANCH)
        self.assertEqual(recorded["detail"]["divergence"], {"reported": "done", "asserted": False})

    def test_a_failed_assertion_mid_chain_leaves_the_rest_not_reached(self) -> None:
        """`b`'s own assertion passes over a tree `b` never touched, and it is still
        not recorded — which is what tells a halt from an exclusion."""
        steps = [
            one_step(step_id="a", command="true", verify="test -f a.txt"),
            one_step(
                step_id="b",
                command="printf b > b.txt",
                verify="test -d .",
                deps=["a"],
            ),
        ]
        nodes = self.lower(steps, CHAIN)
        for node in nodes:
            if node["name"] == work_name("b"):
                node["depends"] = [mark_name("a")]
        completed = self.run_dag(nodes)
        statuses = self.statuses(completed)

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(statuses[verify_name("a")], "failed")
        self.assertEqual(statuses[mark_name("a")], "skipped")
        self.assertEqual(statuses[work_name("b")], "skipped", "the chain halted")
        self.assertEqual(statuses[verify_name("b")], "succeeded", "the assertion still ran")

        self.assertFalse((self.root / "b.txt").exists(), "the halted step never ran")
        self.assertFalse((reports_of(self.root, ENGINE_RUN_ID) / "b.json").exists())
        self.assertFalse(self.marker("a").exists())
        self.assertFalse(self.marker("b").exists(), "a passing assertion recorded a step that never ran")

        self.assertEqual(self.gate_report("a")["cause"], "verify_failed")
        self.assertEqual(self.gate_report("a")["detail"]["position"], CHAIN)
        self.assertEqual(self.gate_report("b")["cause"], "not_reached")

    def test_a_step_that_reports_failure_over_work_that_is_there_diverges(self) -> None:
        steps = [
            one_step(step_id="a", command="printf a > a.txt; exit 3", verify="test -f a.txt")
        ]
        completed = self.run_dag(self.lower(steps, BRANCH))
        statuses = self.statuses(completed)

        self.assertEqual(statuses[work_name("a")], "failed")
        self.assertEqual(statuses[verify_name("a")], "succeeded", "the assertion was still asked")
        self.assertEqual(statuses[mark_name("a")], "skipped")
        self.assertFalse(self.marker("a").exists(), "self-report vetoes what verify allows")
        recorded = self.gate_report("a")
        self.assertEqual(recorded["cause"], "reported_failure")
        self.assertEqual(recorded["detail"]["divergence"], {"reported": "failed", "asserted": True})

    def test_a_verified_step_is_recorded_and_the_run_is_clean(self) -> None:
        steps = [one_step(step_id="a", command="printf a > a.txt", verify="test -f a.txt")]
        completed = self.run_dag(self.lower(steps, BRANCH))
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(self.statuses(completed)[mark_name("a")], "succeeded")
        marker: Any = json.loads(self.marker("a").read_text())
        self.assertEqual(marker["step_id"], "a")
        self.assertEqual(marker["summary"], "command completed")

    def test_a_step_id_too_long_to_name_its_own_assertion_still_routes(self) -> None:
        """The digest handle is the path every long corpus step takes, so the engine
        has to be the thing that says it resolves."""
        long_id = "bring_the_architecture_page_into_line_with_the_current_module_layout"
        self.assertNotEqual(verify_handle(long_id), verify_name(long_id))
        steps = [
            one_step(step_id=long_id, command="printf a > a.txt", verify="exit 4")
        ]
        completed = self.run_dag(self.lower(steps, BRANCH))
        self.assertEqual(self.statuses(completed)[mark_name(long_id)], "skipped")
        self.assertEqual(self.gate_report(long_id)["detail"]["verify_exit"], 4)
        self.assertFalse(self.marker(long_id).exists())

    def test_the_gate_reads_the_status_the_engine_holds_for_the_assertion(self) -> None:
        """`${steps.<id>.exit_code}` resolves to nothing at this pin and `${<id>.exit_code}`
        resolves to the status, so a run is the only thing that can settle the spelling."""
        steps = [one_step(step_id="a", command="printf a > a.txt", verify="exit 10")]
        completed = self.run_dag(self.lower(steps, BRANCH))
        self.assertEqual(self.statuses(completed)[mark_name("a")], "skipped")
        self.assertEqual(self.gate_report("a")["detail"]["verify_exit"], 10)


if __name__ == "__main__":
    unittest.main()
