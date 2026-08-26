"""Doc 11: the emitter, and the preflight that refuses what the engine would run anyway.

The class that matters most here is the pairing: for each hole in the engine's own
validation, one test shows Cairn refusing the file and its neighbour shows `dagu validate`
accepting the very same bytes. That pairing is the preflight's whole reason to exist, so it
is written to be read as one argument rather than as two unrelated assertions.

Every engine-driving class **fails** without `dagu` rather than skipping, because the defects
they cover are the ones that otherwise report success. Set `CAIRN_SKIP_ENGINE_TESTS=1` to
record deliberately that a run did not check them.
"""

import copy
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, ClassVar, cast
from unittest.mock import patch

from cairn.core import CairnError, RuntimeContext
from cairn.parameters import parameter
from cairn.plan.schema import ENGINE_NAME_MAX_BYTES
from cairn.topology import ROLES, parse_node_name, worktrees_root_for
from cairn.wave import run_join
from cairn.workflow.build import (
    disabled_retry,
    envelope,
    graph_digest,
    step_concurrency,
)
from cairn.workflow.cli import main as workflow_main
from cairn.workflow.cli import workflow_verbs
from cairn.workflow.gate import (
    EngineUnavailable,
    assert_pinned,
    engine_reason,
    gate,
    rehearse_start,
)
from cairn.workflow.preflight import RULES, Fault, check, rehearse_gate
from cairn.workflow.schema import (
    ENGINE_VERSION,
    GENERATOR_VERSION,
    GRAPH_TYPE,
    LABEL_BODY_DIGEST,
    LABEL_GENERATOR,
    LABEL_GRAPH_DIGEST,
    LABEL_PLAN,
    OCCASION_PARAM,
    PARAMETERS,
    PARENT_BRANCH_PARAM,
    REPOSITORY_PARAM,
    WORKFLOW_SUFFIX,
    Workflow,
    body_digest,
    is_agent_body,
    read,
    serialise,
)
from cairn.workflow.stamp import (
    ANOTHER_GENERATOR,
    ANOTHER_PLAN,
    HAND_EDITED,
    PLAN_CHANGED,
    REPLACED_WHOLESALE,
    UNCHANGED,
    UNSTAMPED,
    describe,
    file_digest,
    stamp_path,
    workflow_path,
    write_stamp,
)
from scripts.regenerate_workflows import (
    GOLDENS,
    OCCASION,
    PARENT_BRANCH,
    PLANS,
    PYTHON_PATH,
    REPOSITORY,
    RUNS_ROOT,
    SHAPES,
    build_shape,
    emitted,
    golden_path,
    inputs_of,
    plan_graph,
    refusal,
    regenerate,
)
from scripts.regenerate_workflows import main as regenerate_main

CAIRN_ROOT = Path(__file__).resolve().parents[1]


def document(name: str = "multi-wave") -> Workflow:
    """A shape built to run on this machine rather than to be recorded.

    The goldens pin a package root that is on no machine, which is what makes them the
    same file everywhere; the classes below that rehearse the gate or drive the engine
    need one whose steps can really import Cairn, and that path is this checkout's.
    """
    return build_shape(name, python_path=str(CAIRN_ROOT))


def machine_paths_in(text: str) -> list[str]:
    """This checkout and this home, which are the two a golden could come to carry.

    A temporary directory is not among them on purpose: nothing on the emitter's path reads
    one, and on a checkout where it is `/tmp` the probe would fire on a plan whose own task
    text mentions that directory.
    """
    return [path for path in (str(CAIRN_ROOT), str(Path.home())) if path in text]


def reread(built: Workflow) -> Any:
    """The document as the engine will parse it, which is what every rule reads."""
    return json.loads(serialise(built))


def rules(faults: list[Any]) -> set[str]:
    return {fault.rule for fault in faults}


class TheDocumentIsWrittenAsJson(unittest.TestCase):
    def test_the_emitted_bytes_parse_back_to_the_document_that_was_built(self) -> None:
        built = document()
        self.assertEqual(json.loads(serialise(built)), built)

    def test_a_command_that_reads_as_a_boolean_survives_as_a_string(self) -> None:
        """`run: false` is rejected at load; a JSON string can never render as a bare false."""
        built = document()
        built["steps"][0]["run"] = "false"
        self.assertIsInstance(json.loads(serialise(built))["steps"][0]["run"], str)

    def test_a_body_carrying_quotes_a_hash_and_a_newline_survives_the_file(self) -> None:
        built = document()
        built["steps"][0]["run"] = "printf 'a: b' # x\nyes\ton\t~"
        self.assertEqual(
            json.loads(serialise(built))["steps"][0]["run"], built["steps"][0]["run"]
        )

    def test_the_file_declares_no_top_level_name(self) -> None:
        """The validator rejects a file that names itself while a run would accept it."""
        self.assertNotIn("name", reread(document()))


class TheMachineLevelDefaultsAreStated(unittest.TestCase):
    def test_the_graph_type_is_stated_rather_than_inherited(self) -> None:
        self.assertEqual(document()["type"], GRAPH_TYPE)

    def test_step_concurrency_is_a_number_no_wave_can_exceed(self) -> None:
        """Zero is inheritance wearing the shape of an override: it reads as unset."""
        built = document()
        self.assertEqual(built["max_active_steps"], len(built["steps"]))
        self.assertGreater(built["max_active_steps"], 0)
        self.assertEqual(step_concurrency(0), 1)

    def test_the_dag_level_retry_is_disabled_with_the_interval_the_schema_demands(
        self,
    ) -> None:
        self.assertEqual(disabled_retry(), {"limit": 0, "interval_sec": 1})
        self.assertEqual(document()["retry_policy"], {"limit": 0, "interval_sec": 1})

    def test_every_step_carries_a_working_directory_a_timeout_and_a_retry_bound(
        self,
    ) -> None:
        for name in SHAPES:
            with self.subTest(shape=name):
                for step in document(name)["steps"]:
                    self.assertTrue(step["working_dir"])
                    self.assertIsInstance(step["timeout_sec"], int)
                    self.assertIn("interval_sec", step["retry_policy"])

    def test_the_release_runs_on_the_way_out_rather_than_as_a_node(self) -> None:
        built = document()
        self.assertIn("exit", built["handler_on"])
        self.assertNotIn("lock_release", [step["name"] for step in built["steps"]])


class ThePerTargetValuesAreParameters(unittest.TestCase):
    def test_the_repository_the_parent_branch_and_the_occasion_are_the_parameters(
        self,
    ) -> None:
        declared = [key for entry in document()["params"] for key in entry]
        self.assertEqual(tuple(declared), PARAMETERS)

    def test_every_working_directory_is_written_against_the_repository_parameter(
        self,
    ) -> None:
        for name in SHAPES:
            with self.subTest(shape=name):
                for step in document(name)["steps"]:
                    self.assertTrue(
                        str(step["working_dir"]).startswith(f"${{{REPOSITORY_PARAM}}}"),
                        step["working_dir"],
                    )

    def test_no_body_names_a_repository_a_worktree_or_the_parent_branch(self) -> None:
        """A body that named one target could not be pointed at another."""
        for name in SHAPES:
            built = document(name)
            for step in [*built["steps"], built["handler_on"]["exit"]]:
                with self.subTest(shape=name, step=step["name"]):
                    body = str(step["run"])
                    self.assertNotIn(str(REPOSITORY), body)
                    self.assertNotIn(".cairn-worktrees", body)
                    self.assertNotIn(f"--into {PARENT_BRANCH}", body)
                    self.assertNotIn(f"--base {PARENT_BRANCH}", body)

    def test_no_body_carries_a_parameter_reference(self) -> None:
        """Measured: quoting decides whether a reference is inert, split, or executed.

        `shlex.quote` single-quotes any token holding `$`, which suppresses substitution
        outright; a bare token splits on whitespace; and a double-quoted one executes
        whatever the value holds, which a trigger-time field must never be able to do.
        """
        for name in SHAPES:
            built = document(name)
            for step in [*built["steps"], built["handler_on"]["exit"]]:
                with self.subTest(shape=name, step=step["name"]):
                    self.assertNotIn("${", str(step["run"]))

    def test_a_worktree_directory_follows_the_repository_it_is_derived_from(self) -> None:
        built = document("fan-out")
        worktrees = [
            step["working_dir"]
            for step in built["steps"]
            if ".cairn-worktrees" in str(step["working_dir"])
        ]
        self.assertTrue(worktrees)
        for directory in worktrees:
            self.assertTrue(str(directory).startswith(f"${{{REPOSITORY_PARAM}}}."))


class TheJoinIsTheWavesOneCensus(unittest.TestCase):
    def test_the_join_emits_a_body_rather_than_naming_a_later_document(self) -> None:
        joins = [s for s in document("fan-out")["steps"] if s["name"].startswith("join_")]
        self.assertEqual(len(joins), 1)
        self.assertIn("wave join", str(joins[0]["run"]))

    def test_the_join_waits_for_every_commit_in_its_wave(self) -> None:
        built = document("fan-out")
        join = next(s for s in built["steps"] if s["name"].startswith("join_"))
        self.assertTrue(all(d.startswith("commit_") for d in join["depends"]))

    def test_the_join_absorbs_nothing_so_a_real_failure_stops_the_slots(self) -> None:
        join = next(s for s in document("fan-out")["steps"] if s["name"].startswith("join_"))
        self.assertNotIn("continue_on", join)


class ThePreflightRefusesWhatTheEngineWouldRun(unittest.TestCase):
    """Each mutation takes a clean document and breaks exactly one thing."""

    def mutated(self, change: Callable[[dict[str, Any]], object]) -> set[str]:
        broken = copy.deepcopy(reread(document()))
        change(broken)
        return rules(check(broken))

    def test_mark_success_is_refused_anywhere_it_appears(self) -> None:
        self.assertIn(
            "mark_success",
            self.mutated(
                lambda d: d["steps"][0].update(continue_on={"failure": True, "mark_success": True})
            ),
        )

    def test_routing_on_output_is_refused(self) -> None:
        self.assertIn(
            "continue_on_output",
            self.mutated(lambda d: d["steps"][0].update(continue_on={"output": "x"})),
        )

    def test_a_step_assertion_that_absorbs_no_failure_is_refused(self) -> None:
        def drop(d: Any) -> None:
            for step in d["steps"]:
                if "id" in step:
                    step["continue_on"] = {}
                    return

        self.assertIn("assertion_absorbs_no_failure", self.mutated(drop))

    def test_a_marker_gated_step_that_cannot_survive_its_no_op_is_refused(self) -> None:
        def drop(d: Any) -> None:
            for step in d["steps"]:
                if any("marker absent" in c["condition"] for c in step.get("preconditions", [])):
                    step["continue_on"] = {"failure": True}
                    return

        self.assertIn("gate_without_skipped", self.mutated(drop))

    def test_a_verify_gated_step_that_lets_a_closed_gate_commit_is_refused(self) -> None:
        def add(d: Any) -> None:
            for step in d["steps"]:
                if any("verify gate" in c["condition"] for c in step.get("preconditions", [])):
                    step["continue_on"] = {"skipped": True}
                    return

        self.assertIn("marker_with_skipped", self.mutated(add))

    def test_a_missing_timeout_is_refused(self) -> None:
        self.assertIn("missing_timeout", self.mutated(lambda d: d["steps"][0].pop("timeout_sec")))

    def test_an_agent_body_stripped_of_its_bounds_is_refused(self) -> None:
        """A paid session with no written price or model is the one thing an offer
        cannot price, so the file never reaches a run."""

        def strip(flag: str) -> Callable[[dict[str, Any]], object]:
            def change(d: dict[str, Any]) -> None:
                for step in d["steps"]:
                    body = str(step.get("run", ""))
                    if is_agent_body(body):
                        words = body.split()
                        index = words.index(flag)
                        del words[index : index + 2]
                        step["run"] = " ".join(words)
                        return
                raise AssertionError("no agent body in the document")

            return change

        for flag in ("--max-budget-usd", "--model"):
            with self.subTest(flag=flag):
                self.assertIn("unbounded_session", self.mutated(strip(flag)))

    def test_a_missing_working_directory_is_refused(self) -> None:
        self.assertIn(
            "missing_working_dir", self.mutated(lambda d: d["steps"][0].pop("working_dir"))
        )

    def test_a_graph_type_other_than_graph_is_refused(self) -> None:
        for wrong in ("chain", "controller"):
            with self.subTest(type=wrong):
                self.assertIn(
                    "wrong_graph_type",
                    self.mutated(lambda d, value=wrong: d.update(type=value)),
                )

    def test_a_body_that_is_not_one_invocation_is_refused(self) -> None:
        self.assertIn(
            "body_not_one_invocation",
            self.mutated(lambda d: d["steps"][0].update(run="echo a && echo b")),
        )

    def test_a_list_valued_body_is_refused_because_it_runs_several_commands(self) -> None:
        """Measured: a list is a sequence of shell commands, not an argv vector."""
        self.assertIn(
            "body_not_one_invocation",
            self.mutated(lambda d: d["steps"][0].update(run=["echo a", "echo b"])),
        )

    def test_a_parameter_reference_in_a_body_is_refused(self) -> None:
        """The engine substitutes a declared name into a body; a caller varies it at trigger."""
        self.assertIn(
            "reference_out_of_position",
            self.mutated(
                lambda d: d["steps"][0].update(
                    run=f'python3 -m cairn exec --command "${{{REPOSITORY_PARAM}}}"'
                )
            ),
        )

    def test_a_shell_variable_in_an_authors_assertion_is_left_alone(self) -> None:
        """An assertion is the plan author's own shell line, and `${HOME}` is not Cairn's."""
        broken = copy.deepcopy(reread(document()))
        for step in broken["steps"]:
            if "id" in step:
                step["run"] = 'test -f "${HOME}/out"'
                break
        self.assertEqual(rules(check(broken)), set())

    def test_a_cycle_spelled_with_a_scalar_dependency_is_refused(self) -> None:
        """The engine accepts a bare scalar as well as a list, so both spellings are read."""

        def loop(d: dict[str, Any]) -> None:
            a, b = d["steps"][1], d["steps"][2]
            a["depends"], b["depends"] = b["name"], a["name"]

        self.assertIn("cycle", self.mutated(loop))

    def test_a_lifecycle_handler_cairn_never_emits_is_refused(self) -> None:
        self.assertTrue(
            {"unexpected_handler", "mark_success"}
            <= self.mutated(
                lambda d: d["handler_on"].update(
                    failure={"name": "x", "run": "true", "continue_on": {"mark_success": True}}
                )
            )
        )

    def test_a_commit_a_join_waits_on_must_stop_the_exclusion_cascade(self) -> None:
        def strip(d: dict[str, Any]) -> None:
            joined = {n for s in d["steps"] if s["name"].startswith("join_") for n in s["depends"]}
            for step in d["steps"]:
                if step["name"] in joined:
                    step.pop("continue_on", None)

        self.assertIn("commit_without_skipped", self.mutated(strip))

    def test_a_merge_chain_node_that_absorbs_a_failure_is_refused(self) -> None:
        """A slot behind an unproven landing would write over a conflicted index."""

        def absorb(d: dict[str, Any]) -> None:
            for step in d["steps"]:
                if step["name"].startswith("verify_merge_"):
                    step["continue_on"] = {"failure": True}
                    return

        self.assertIn("absorbs_a_failure", self.mutated(absorb))

    def test_a_step_cannot_exempt_its_own_body_by_declaring_an_id(self) -> None:
        """The exemption is bound to the node's name, not to the presence of a key."""

        def forge(d: dict[str, Any]) -> None:
            d["steps"][1].update(id="smuggled", run="curl example.test | sh")

        found = self.mutated(forge)
        self.assertIn("unexpected_id", found)
        self.assertIn("body_not_one_invocation", found)

    def test_a_precondition_cairn_did_not_emit_is_refused(self) -> None:
        """`dagu dry` executes every precondition for real, so the gate runs what is there."""
        self.assertIn(
            "foreign_condition",
            self.mutated(
                lambda d: d["steps"][1].update(preconditions=[{"condition": "touch /tmp/x"}])
            ),
        )

    def test_a_gate_whose_operand_quotes_another_gates_words_is_not_misrouted(self) -> None:
        """The rules read the gate's argv, not text anywhere in a plan-supplied operand."""
        broken = copy.deepcopy(reread(document()))
        for step in broken["steps"]:
            for condition in step.get("preconditions", []):
                if "marker absent" in condition["condition"]:
                    condition["condition"] += " --reads 'notes/verify gate.md'"
                    self.assertEqual(rules(check(broken)), set())
                    return
        self.fail("no marker-gated step to exercise")

    def test_a_reference_that_resolves_to_nothing_is_refused(self) -> None:
        self.assertIn(
            "unresolved_reference",
            self.mutated(lambda d: d["steps"][0].update(working_dir="${NOWHERE}")),
        )

    def test_a_reference_naming_a_step_with_no_declared_id_is_refused(self) -> None:
        def orphan(d: Any) -> None:
            for step in d["steps"]:
                if "id" in step:
                    del step["id"]
                    return

        self.assertIn("reference_without_id", self.mutated(orphan))

    def test_a_top_level_name_is_refused(self) -> None:
        self.assertIn("top_level_name", self.mutated(lambda d: d.update(name="plan")))

    def test_an_inherited_step_concurrency_is_refused(self) -> None:
        self.assertIn(
            "inherited_concurrency", self.mutated(lambda d: d.update(max_active_steps=0))
        )

    def test_a_node_name_the_run_model_could_not_parse_is_refused(self) -> None:
        self.assertIn("node_name", self.mutated(lambda d: d["steps"][0].update(name="not-a-role")))

    def test_an_unbounded_retry_is_refused(self) -> None:
        self.assertIn(
            "unbounded_retry", self.mutated(lambda d: d["steps"][0].update(retry_policy={}))
        )

    def test_a_with_block_is_refused_because_yaml_retypes_its_values(self) -> None:
        self.assertIn(
            "with_block", self.mutated(lambda d: d["steps"][0].update({"with": {"a": "true"}}))
        )

    def test_a_scope_keyed_on_the_occasion_with_nothing_declaring_it_is_refused(
        self,
    ) -> None:
        def strip(d: Any) -> None:
            d["params"] = [e for e in d["params"] if OCCASION_PARAM not in e]
            for step in d["steps"]:
                for condition in step.get("preconditions", []):
                    condition["condition"] = condition["condition"].replace(
                        "--scope once", "--scope weekly"
                    )

        self.assertIn("scope_without_occasion", self.mutated(strip))

    def test_a_cycle_among_the_emitted_steps_is_refused(self) -> None:
        def loop(d: Any) -> None:
            template = dict(d["steps"][0])
            d["steps"].append({**template, "name": "work_x", "depends": ["work_y"]})
            d["steps"].append({**template, "name": "work_y", "depends": ["work_x"]})
            d["max_active_steps"] = len(d["steps"])

        self.assertIn("cycle", self.mutated(loop))

    def test_a_generated_workflow_earns_no_refusal(self) -> None:
        for name in SHAPES:
            with self.subTest(shape=name):
                self.assertEqual(check(reread(document(name))), [])

    def test_every_rule_is_named_once(self) -> None:
        names = [rule.name for rule in RULES]
        self.assertEqual(len(names), len(set(names)))

    def test_every_rule_says_what_it_prevents(self) -> None:
        for rule in RULES:
            with self.subTest(rule=rule.name):
                self.assertTrue(rule.consequence.strip())


class TheGateCommandIsProvenToResolve(unittest.TestCase):
    def test_a_workflow_whose_interpreter_cannot_import_cairn_is_refused(self) -> None:
        """A gate that cannot launch skips every step into a clean, empty success."""
        broken = copy.deepcopy(reread(document()))
        broken["env"] = [{"PYTHONPATH": "/nowhere/at/all"}]
        self.assertEqual(rules(rehearse_gate(broken)), {"gate_unresolvable"})

    def test_a_workflow_declaring_no_interpreter_path_is_refused(self) -> None:
        broken = copy.deepcopy(reread(document()))
        broken["env"] = []
        self.assertEqual(rules(rehearse_gate(broken)), {"gate_unresolvable"})

    def test_a_generated_workflow_resolves(self) -> None:
        self.assertEqual(rehearse_gate(reread(document())), [])


class Provenance(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "multi-wave.yaml"
        self.built = document()
        self.path.write_text(serialise(self.built), encoding="utf-8")
        write_stamp(self.path, self.built, graph_digest(plan_graph("multi-wave")))

    def test_the_stamp_records_the_plan_and_the_bytes_it_was_written_from(self) -> None:
        recorded = json.loads(stamp_path(self.path).read_text(encoding="utf-8"))
        self.assertEqual(recorded["plan"], "multi-wave")
        self.assertEqual(recorded["engine"], ENGINE_VERSION)
        self.assertEqual(recorded["body_sha256"], body_digest(reread(self.built)))

    def test_an_untouched_workflow_reports_no_divergence(self) -> None:
        self.assertEqual(describe(self.path, "multi-wave").state, UNCHANGED)

    def test_rewriting_identical_bytes_reports_no_divergence(self) -> None:
        """The detector reads content, not metadata."""
        self.path.write_text(self.path.read_text(encoding="utf-8"), encoding="utf-8")
        os.utime(self.path, (0, 0))
        self.assertEqual(describe(self.path, "multi-wave").state, UNCHANGED)

    def test_a_hand_edited_workflow_is_named_with_what_is_being_replaced(self) -> None:
        edited = json.loads(self.path.read_text(encoding="utf-8"))
        edited["steps"][1]["timeout_sec"] = 99999
        self.path.write_text(json.dumps(edited, indent=2), encoding="utf-8")
        divergence = describe(self.path, "multi-wave")
        self.assertEqual(divergence.state, HAND_EDITED)
        self.assertIn("replaced, not merged", divergence.summary)

    def test_a_workflow_cairn_never_wrote_is_named_as_such(self) -> None:
        """The delete-and-recreate case: it arrives carrying no provenance at all."""
        self.path.write_text(json.dumps({"type": "graph", "steps": []}), encoding="utf-8")
        self.assertEqual(describe(self.path, "multi-wave").state, UNSTAMPED)

    def test_a_workflow_that_no_longer_parses_is_still_described(self) -> None:
        self.path.write_text("steps: [not json", encoding="utf-8")
        self.assertEqual(describe(self.path, "multi-wave").state, HAND_EDITED)

    def test_a_plan_edited_since_authoring_is_a_divergence_of_its_own(self) -> None:
        """The workflow's own bytes cannot show this, so nothing else would say it."""
        divergence = describe(self.path, "multi-wave", "a-different-digest-entirely")
        self.assertEqual(divergence.state, PLAN_CHANGED)
        self.assertIn("the plan changed", divergence.summary)

    def test_an_unedited_plan_reports_no_divergence(self) -> None:
        self.assertEqual(
            describe(self.path, "multi-wave", graph_digest(plan_graph("multi-wave"))).state,
            UNCHANGED,
        )

    def test_a_reader_with_no_plan_in_hand_never_claims_agreement(self) -> None:
        """`check` has no graph, and absence of one is not evidence the plan is unchanged."""
        self.assertEqual(describe(self.path, "multi-wave").state, UNCHANGED)

    def test_a_workflow_generated_from_another_plan_is_named(self) -> None:
        self.assertEqual(describe(self.path, "fan-out").state, ANOTHER_PLAN)

    def test_a_workflow_that_has_gone_missing_is_named(self) -> None:
        self.path.unlink()
        self.assertEqual(describe(self.path, "multi-wave").state, REPLACED_WHOLESALE)

    def rewritten(self, edit: Callable[[Any], object]) -> Any:
        """The file on disk, edited, with its stamp still describing the bytes there."""
        document_ = json.loads(self.path.read_text(encoding="utf-8"))
        edit(document_)
        self.path.write_text(json.dumps(document_, indent=2), encoding="utf-8")
        write_stamp(self.path, cast(Workflow, document_), graph_digest(plan_graph("multi-wave")))
        return document_

    def test_a_workflow_an_earlier_generator_wrote_is_named_as_such(self) -> None:
        """`body_digest` strips every `cairn_` label, so a generation that moved leaves the
        body hash agreeing with itself and reads as unmodified unless this is asked."""
        self.rewritten(lambda d: d["labels"].update({LABEL_GENERATOR: "0"}))
        divergence = describe(self.path, "multi-wave")
        self.assertEqual(divergence.state, ANOTHER_GENERATOR)
        self.assertIn(f"this one is {GENERATOR_VERSION}", divergence.summary)
        self.assertIn("generator 0", divergence.summary)

    def test_an_edit_outranks_the_generation_that_wrote_the_file(self) -> None:
        """Both are true of this file; that someone edited it is the one worth saying."""
        self.rewritten(
            lambda d: (
                d["labels"].update({LABEL_GENERATOR: "0"}),
                d["steps"][1].update(timeout_sec=99999),
            )
        )
        self.assertEqual(describe(self.path, "multi-wave").state, HAND_EDITED)

    def test_an_edit_too_small_to_move_the_body_outranks_it_as_well(self) -> None:
        """Found by the byte record rather than by the body hash, and just as much an edit."""
        self.rewritten(lambda d: d["labels"].update({LABEL_GENERATOR: "0"}))
        self.path.write_text(
            self.path.read_text(encoding="utf-8") + "\n", encoding="utf-8"
        )
        self.assertEqual(describe(self.path, "multi-wave").state, HAND_EDITED)

    def test_an_earlier_generator_is_named_even_where_cairns_own_record_is_gone(self) -> None:
        """The case a record could never speak for: the generation rides in the file, so
        deleting the record beside it must not turn the answer into `unmodified`."""
        self.rewritten(lambda d: d["labels"].update({LABEL_GENERATOR: "0"}))
        stamp_path(self.path).unlink()
        self.assertEqual(describe(self.path, "multi-wave").state, ANOTHER_GENERATOR)

    def test_the_stamp_in_the_file_and_the_body_it_describes_agree(self) -> None:
        self.assertEqual(
            self.built["labels"][LABEL_BODY_DIGEST], body_digest(reread(self.built))
        )
        self.assertEqual(self.built["labels"][LABEL_PLAN], "multi-wave")


class Authoring(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repository = self.root / "repo"
        self.repository.mkdir()
        for command in (
            ("init", "--initial-branch=main", "--quiet", "."),
            ("config", "user.email", "cairn@test"),
            ("config", "user.name", "Cairn Test"),
        ):
            subprocess.run(("git", *command), cwd=self.repository, check=True)
        (self.repository / "README.md").write_text("start\n", encoding="utf-8")
        subprocess.run(("git", "add", "--all"), cwd=self.repository, check=True)
        subprocess.run(("git", "commit", "--quiet", "-m", "init"), cwd=self.repository, check=True)

    def author(self, plan: str = "linear-chain") -> int:
        return workflow_main(
            [
                "author",
                str(PLANS / plan / "graph.json"),
                "--repository",
                str(self.repository),
            ]
        )

    def tree_is_clean(self) -> bool:
        completed = subprocess.run(
            ("git", "status", "--porcelain", "--untracked-files=all"),
            cwd=self.repository,
            capture_output=True,
            text=True,
            check=True,
        )
        return completed.stdout.strip() == ""

    def test_the_definition_is_written_to_a_file_and_handed_over_by_path(self) -> None:
        self.assertEqual(self.author(), 0)
        self.assertTrue(workflow_path(self.repository, "linear-chain").exists())

    def test_nothing_generated_reaches_the_repositorys_working_tree(self) -> None:
        self.assertTrue(self.tree_is_clean())
        self.assertEqual(self.author(), 0)
        self.assertTrue(self.tree_is_clean())

    def test_the_definition_lives_in_cairns_own_state_directory(self) -> None:
        self.assertEqual(self.author(), 0)
        path = workflow_path(self.repository, "linear-chain")
        self.assertIn(".git", path.parts)

    def test_a_refused_definition_never_reaches_the_path_a_run_would_start_from(
        self,
    ) -> None:
        """Gated where it cannot be run from, and moved into place only once it passes."""
        self.assertEqual(
            workflow_main(
                [
                    "author",
                    str(PLANS / "linear-chain" / "graph.json"),
                    "--repository",
                    str(self.repository),
                    "--python-path",
                    "/nowhere/at/all",
                ]
            ),
            1,
        )
        self.assertFalse(workflow_path(self.repository, "linear-chain").exists())

    def test_re_authoring_replaces_a_hand_edited_workflow_rather_than_merging_it(
        self,
    ) -> None:
        self.assertEqual(self.author(), 0)
        path = workflow_path(self.repository, "linear-chain")
        edited = json.loads(path.read_text(encoding="utf-8"))
        edited["steps"][1]["timeout_sec"] = 99999
        path.write_text(json.dumps(edited, indent=2), encoding="utf-8")
        self.assertEqual(self.author(), 0)
        self.assertNotEqual(
            json.loads(path.read_text(encoding="utf-8"))["steps"][1]["timeout_sec"], 99999
        )

    def test_the_command_line_offers_only_authoring_and_checking(self) -> None:
        """The generator is the only thing that writes one; nothing accepts workflow text."""
        with self.assertRaises(SystemExit):
            workflow_main(["install", "somewhere.yaml"])
        self.assertEqual(workflow_verbs(), {"author", "check"})


class TheEngineIsWhatDecidesTheShape(unittest.TestCase):
    """The pairing: what Cairn refuses, and what the engine says about the same bytes."""

    SKIP_ENV: ClassVar[str] = "CAIRN_SKIP_ENGINE_TESTS"

    def setUp(self) -> None:
        self.dagu = shutil.which("dagu")
        if not self.dagu and not os.environ.get(self.SKIP_ENV):
            self.fail(
                "dagu is not installed, so the preflight's whole reason to exist is "
                f"unverified. Install it, or set {self.SKIP_ENV}=1 to record that this run "
                "did not check it."
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
        path.write_text(
            serialise(cast(Workflow, built)) if isinstance(built, dict) else str(built),
            encoding="utf-8",
        )
        return subprocess.run(
            (str(self.dagu), "validate", "--dagu-home", str(self.home), str(path)),
            capture_output=True,
            text=True,
            check=False,
        ).returncode

    def cyclic(self) -> Any:
        broken = copy.deepcopy(reread(document()))
        template = dict(broken["steps"][0])
        broken["steps"].append({**template, "name": "work_x", "depends": ["work_y"]})
        broken["steps"].append({**template, "name": "work_y", "depends": ["work_x"]})
        broken["max_active_steps"] = len(broken["steps"])
        return broken

    def test_a_cyclic_emission_is_refused_by_cairn(self) -> None:
        self.assertIn("cycle", rules(check(self.cyclic())))

    def test_the_engines_validator_exits_zero_on_that_same_cyclic_file(self) -> None:
        """The preflight's reason to exist, stated as one measurement."""
        self.assertEqual(self.validate(self.cyclic(), "cycle"), 0)

    def test_the_engines_validator_exits_zero_on_an_unresolved_reference(self) -> None:
        broken = copy.deepcopy(reread(document()))
        broken["steps"][0]["working_dir"] = "${NOWHERE}"
        self.assertIn("unresolved_reference", rules(check(broken)))
        self.assertEqual(self.validate(broken, "unresolved"), 0)

    def test_the_engines_validator_exits_zero_on_mark_success(self) -> None:
        broken = copy.deepcopy(reread(document()))
        broken["steps"][0]["continue_on"] = {"failure": True, "mark_success": True}
        self.assertIn("mark_success", rules(check(broken)))
        self.assertEqual(self.validate(broken, "marksuccess"), 0)

    def test_the_engines_validator_exits_zero_on_a_step_with_no_bounds(self) -> None:
        broken = copy.deepcopy(reread(document()))
        broken["steps"][0].pop("timeout_sec")
        broken["steps"][0].pop("working_dir")
        self.assertTrue({"missing_timeout", "missing_working_dir"} <= rules(check(broken)))
        self.assertEqual(self.validate(broken, "unbounded"), 0)

    def test_the_engine_refuses_a_file_that_names_itself(self) -> None:
        """Cairn's type cannot spell it; this pins why the field is absent."""
        named = copy.deepcopy(reread(document()))
        named["name"] = "plan"
        self.assertNotEqual(self.validate(named, "named"), 0)

    def test_every_generated_workflow_passes_both_engine_checks(self) -> None:
        for name in SHAPES:
            with self.subTest(shape=name):
                path = self.root / f"{name}.yaml"
                path.write_text(serialise(document(name)), encoding="utf-8")
                self.assertEqual(gate(path), [])

    def test_a_recorded_file_loads_in_the_engine_as_it_is_committed(self) -> None:
        """The pinned package root is a fiction, and the engine reads a file that declares
        one exactly as it reads any other: a precondition that cannot launch is a skip."""
        self.assertEqual(gate(golden_path("multi-wave")), [])

    def test_the_gate_writes_nothing_into_the_engine_home_a_run_would_use(self) -> None:
        """`dagu dry` writes, so the gate is given a data directory of its own.

        The home under test is the one the engine would choose for itself — pointed at by
        `DAGU_HOME` — rather than one the gate is handed. A gate that stopped isolating
        itself would write here, which is what makes this assertion able to fail.
        """
        path = self.root / "linear-chain.yaml"
        path.write_text(serialise(document("linear-chain")), encoding="utf-8")
        run_home = self.root / "the-runs-own-home"
        run_home.mkdir()
        previous = os.environ.get("DAGU_HOME")
        os.environ["DAGU_HOME"] = str(run_home)
        try:
            self.assertEqual(gate(path), [])
        finally:
            if previous is None:
                del os.environ["DAGU_HOME"]
            else:
                os.environ["DAGU_HOME"] = previous
        self.assertEqual(list(run_home.rglob("*")), [])


class TheEngineVersionIsPinned(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def stub(self, body: str) -> str:
        path = self.root / "dagu"
        path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        path.chmod(0o755)
        return str(path)

    def test_a_shell_that_cannot_start_a_run_is_refused_in_the_engines_own_words(self) -> None:
        """The fault `dagu validate` and `dagu dry` structurally cannot find, because
        neither binds the unix socket every run opens before any step runs ([19 C])."""
        bind_refused = (
            "echo 'time=t level=WARN msg=noise' >&2\n"
            "echo 'Error: failed to start the unix socket server: listen unix "
            "/tmp/@dagu__x.sock: bind: operation not permitted' >&2\n"
            "exit 1"
        )
        with self.assertRaises(EngineUnavailable) as caught:
            rehearse_start(binary=self.stub(bind_refused))
        said = str(caught.exception)
        self.assertIn("bind: operation not permitted", said)
        self.assertNotIn("level=WARN", said)
        # It has to say what clears it, or a person cannot act on it.
        self.assertIn("unix socket", said)

    def test_a_rehearsal_that_never_answers_is_refused_rather_than_waited_on(self) -> None:
        """The other spelling: in the machine's own home the bind was observed to sit
        silent for two minutes rather than fail, which a bound turns into a refusal."""
        with patch("cairn.workflow.gate.GATE_TIMEOUT", 1), self.assertRaises(
            EngineUnavailable
        ) as caught:
            rehearse_start(binary=self.stub("sleep 30"))
        self.assertIn("did not take", str(caught.exception))

    def test_a_rehearsal_never_names_the_machines_own_engine_home(self) -> None:
        """An engine home the binary has never seen is created carrying an active retry
        policy that re-executes paid work ([09]), so reading whether the engine works must
        never be the thing that arms it."""
        recorder = self.root / "argv"
        with self.assertRaises(EngineUnavailable):
            rehearse_start(
                binary=self.stub(f'printf "%s\\n" "$@" > {recorder}\nexit 1')
            )
        argv = recorder.read_text(encoding="utf-8").splitlines()
        self.assertIn("--dagu-home", argv)
        home = Path(argv[argv.index("--dagu-home") + 1])
        self.assertFalse(home.exists(), "the scratch home outlived the rehearsal")
        self.assertNotEqual(home, Path(os.environ.get("DAGU_HOME", "")))

    def test_the_pin_is_the_version_the_generator_was_measured_against(self) -> None:
        self.assertEqual(ENGINE_VERSION, "2.11.0")

    def test_an_engine_of_another_version_halts_naming_both(self) -> None:
        with self.assertRaises(EngineUnavailable) as caught:
            assert_pinned(self.stub("echo 2.12.1"))
        self.assertIn("2.12.1", str(caught.exception))
        self.assertIn(ENGINE_VERSION, str(caught.exception))

    def test_an_engine_whose_version_cannot_be_read_halts(self) -> None:
        with self.assertRaises(EngineUnavailable):
            assert_pinned(self.stub("echo not-a-version"))

    def test_an_engine_that_will_not_answer_halts_rather_than_skipping_the_gate(
        self,
    ) -> None:
        with self.assertRaises(EngineUnavailable):
            assert_pinned(self.stub("exit 3"))

    def test_an_engine_that_is_not_there_halts(self) -> None:
        with self.assertRaises(EngineUnavailable):
            assert_pinned(str(self.root / "absent"))


class TheEnvelopeIsTheOnlyStatementOfTheFormat(unittest.TestCase):
    def test_the_envelope_and_a_built_workflow_agree_on_every_machine_level_default(
        self,
    ) -> None:
        built = document()
        bare = envelope(
            [],
            repository=str(REPOSITORY),
            parent_branch=PARENT_BRANCH,
            occasion=OCCASION,
            python_path=str(CAIRN_ROOT),
            runs_root=RUNS_ROOT,
        )
        for field in ("type", "retry_policy", "params", "env"):
            with self.subTest(field=field):
                self.assertEqual(bare[field], built[field])
        self.assertEqual(
            [key for entry in bare["params"] for key in entry],
            [REPOSITORY_PARAM, PARENT_BRANCH_PARAM, OCCASION_PARAM],
        )



class TheWavesCensus(unittest.TestCase):
    """The join's body: it answers, and it never refuses.

    A refusal here would abort the merge slots behind it and strand the branches that did
    verify — the same failure `continue_on: {failure: true}` prevents one node earlier.
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repository = self.root / "repo"
        self.repository.mkdir()
        self.run_git("init", "--initial-branch=main", "--quiet", ".")
        self.run_git("config", "user.email", "cairn@test")
        self.run_git("config", "user.name", "Cairn Test")
        (self.repository / "README.md").write_text("start\n", encoding="utf-8")
        self.run_git("add", "--all")
        self.run_git("commit", "--quiet", "-m", "init")
        self.reports = self.root / "runs" / "run-1" / "reports"
        self.reports.mkdir(parents=True)

    def run_git(self, *arguments: str) -> None:
        subprocess.run(("git", *arguments), cwd=self.repository, check=True)

    def branch_with_work(self, name: str) -> None:
        self.run_git("checkout", "--quiet", "-b", name)
        (self.repository / f"{name.split('/')[-1]}.txt").write_text("x\n", encoding="utf-8")
        self.run_git("add", "--all")
        self.run_git("commit", "--quiet", "-m", name)
        self.run_git("checkout", "--quiet", "main")

    def context(self) -> Any:
        return RuntimeContext(
            run_id="run-1",
            step_id="join_w1",
            working_directory=self.repository,
            report_path=self.reports / "join_w1.json",
            runs_root=self.reports.parent.parent,
        )

    def record_gate(self, step: str, cause: str | None) -> None:
        (self.reports / f"mark_{step}.json").write_text(
            json.dumps(
                {
                    "step_id": f"mark_{step}",
                    "run_id": "run-1",
                    "status": "failed" if cause else "done",
                    "summary": "recorded",
                    "cause": cause,
                    "needs_user_decision": False,
                }
            ),
            encoding="utf-8",
        )

    def join(self, *branches: str) -> Any:
        return run_join(1, list(branches), "main", self.context())

    def test_the_census_names_which_branches_carry_work_to_land(self) -> None:
        self.branch_with_work("step/alpha")
        self.record_gate("alpha", None)
        result = self.join("step/alpha")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.detail["arrived"], ["step/alpha"])
        self.assertEqual(result.detail["excluded"], {})

    def test_an_excluded_branch_is_named_with_the_cause_the_gate_recorded(self) -> None:
        self.branch_with_work("step/alpha")
        self.record_gate("alpha", "verify_failed")
        result = self.join("step/alpha")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(
            result.detail["excluded"]["step/alpha"]["cause"], "verify_failed"
        )

    def test_a_branch_already_in_the_parent_is_not_reported_as_an_exclusion(self) -> None:
        """It carries no cause, and inventing one would put a lie in an unrepeatable census."""
        self.run_git("branch", "step/settled")
        result = self.join("step/settled")
        self.assertEqual(result.detail["excluded"], {})
        self.assertEqual(result.detail["settled"], ["step/settled"])

    def test_a_wave_whose_every_branch_was_excluded_still_answers(self) -> None:
        """It reports a no-op rather than refusing, so the slots and the prune still run."""
        result = self.join("step/alpha", "step/beta")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.status, "noop")
        self.assertEqual(result.detail["arrived"], [])


class TheParametersReachTheSubcommands(unittest.TestCase):
    def test_a_missing_parent_branch_fails_the_step_rather_than_guessing(self) -> None:
        """A parameter reaches a step through its environment; an unset one is not a default."""
        previous = os.environ.pop(PARENT_BRANCH_PARAM, None)
        try:
            with self.assertRaises(CairnError) as caught:
                parameter(PARENT_BRANCH_PARAM)
            self.assertEqual(caught.exception.cause, "invalid_arguments")
        finally:
            if previous is not None:
                os.environ[PARENT_BRANCH_PARAM] = previous


class TheEmittedPathsAgreeWithTheRuntimes(unittest.TestCase):
    """The one place two statements of the same derivation could drift apart.

    A step's working directory is written by the emitter as text against the repository
    parameter; the worktree it names is created at run time by `cairn worktree setup` from
    the repository it stands in. Nothing but this makes the two agree, and a disagreement is
    silent: the engine creates a missing working directory and runs the step in it.
    """

    def test_every_worktree_directory_is_the_one_the_runtime_would_create(self) -> None:
        for name in ("fan-out", "multi-wave"):
            graph = plan_graph(name)
            built = document(name)
            root = worktrees_root_for(REPOSITORY, graph["plan"]["slug"])
            for step in built["steps"]:
                directory = str(step["working_dir"])
                if ".cairn-worktrees" not in directory:
                    continue
                step_id = directory.rsplit("/", 1)[-1]
                expected = str(root / step_id).replace(
                    str(REPOSITORY), f"${{{REPOSITORY_PARAM}}}", 1
                )
                with self.subTest(shape=name, step=step["name"]):
                    self.assertEqual(directory, expected)


class TheEngineGateRefusesAsWellAsPasses(unittest.TestCase):
    """The gate is only a gate if it is ever seen to close."""

    SKIP_ENV: ClassVar[str] = "CAIRN_SKIP_ENGINE_TESTS"

    def setUp(self) -> None:
        self.dagu = shutil.which("dagu")
        if not self.dagu and not os.environ.get(self.SKIP_ENV):
            self.fail(
                "dagu is not installed, so the mandatory gate is unverified. Install it, or "
                f"set {self.SKIP_ENV}=1 to record that this run did not check it."
            )
        if not self.dagu:
            self.skipTest("recorded deliberately as unchecked")
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def written(self, document_: Any, name: str) -> Path:
        path = self.root / f"{name}.yaml"
        path.write_text(json.dumps(document_, indent=2) + "\n", encoding="utf-8")
        return path

    def test_the_validator_closes_the_gate_on_a_file_it_will_not_load(self) -> None:
        broken = copy.deepcopy(reread(document("linear-chain")))
        broken["steps"][1]["bogus_key"] = "x"
        self.assertEqual(
            [f.rule for f in gate(self.written(broken, "unknown"))], ["engine_validate"]
        )

    def test_a_name_at_the_bound_loads_and_one_over_it_is_refused(self) -> None:
        """The measurement the slug bound rests on, asked of the engine rather than assumed."""
        good = reread(document("linear-chain"))
        self.assertEqual(gate(self.written(good, "a" * ENGINE_NAME_MAX_BYTES)), [])
        faults = gate(self.written(good, "a" * (ENGINE_NAME_MAX_BYTES + 1)))
        self.assertEqual([f.rule for f in faults], ["engine_validate"])
        self.assertIn("name must be less than 40 characters", faults[0].detail)

    def test_a_refusal_leads_with_the_file_that_would_be_published(self) -> None:
        """The authoring path gates a scratch copy; a person can only look at the target.

        The engine's own message still quotes the file it was handed, which is honest — what
        this fixes is Cairn naming a temporary as though it were the definition.
        """
        broken = copy.deepcopy(reread(document("linear-chain")))
        broken["steps"][1]["bogus_key"] = "x"
        published = self.root / "the-plan.yaml"
        faults = gate(self.written(broken, "pending"), named=published)
        self.assertTrue(faults[0].detail.startswith(f"{published}: "))
        self.assertIn("bogus_key", faults[0].detail)

    def test_the_dry_run_closes_the_gate_on_a_plan_the_engine_cannot_build(self) -> None:
        """`dagu validate` exits 0 on a cycle, so only the dry run catches it here."""
        broken = copy.deepcopy(reread(document("linear-chain")))
        a, b = broken["steps"][1], broken["steps"][2]
        a["depends"], b["depends"] = [b["name"]], [a["name"]]
        self.assertEqual(
            [f.rule for f in gate(self.written(broken, "cyclic"))], ["engine_dry"]
        )


# Captured verbatim from `dagu validate` 2.11.0 refusing a 41-character DAG name. The
# engine's own log lines come first and its finding comes last, which is why a reader that
# kept the head of this stream kept the logging and dropped the cause.
MEASURED_REFUSAL = (
    'time=2026-08-26T11:51:10.723+01:00 level=WARN msg="No auth.mode configured — '
    "defaulting to 'builtin'.\"\n"
    "Error: Validation failed for /a/very/long/path/aaaaaaaaaaa.yaml\n"
    "- field 'name': name must be less than 40 characters (value: aaaaaaaaaaa)\n"
)


class TheEnginesOwnReasonSurvivesTheRefusal(unittest.TestCase):
    """A refusal that hides its cause is a refusal the person has to reproduce to read."""

    def reason(self, stream: str) -> str:
        return engine_reason(
            subprocess.CompletedProcess(args=(), returncode=1, stdout="", stderr=stream)
        )

    def test_the_engines_logging_is_dropped_and_its_finding_is_kept(self) -> None:
        said = self.reason(MEASURED_REFUSAL)
        self.assertIn("name must be less than 40 characters", said)
        self.assertNotIn("level=WARN", said)

    def test_a_reason_further_in_than_the_old_cut_still_arrives(self) -> None:
        """The old reader kept the first 600 characters, and the reason is last."""
        padded = MEASURED_REFUSAL.replace("/a/very/long/path/", "/" + "d/" * 400)
        self.assertGreater(len(padded), 600)
        self.assertIn("name must be less than 40 characters", self.reason(padded))

    def test_a_refusal_with_nothing_but_logging_is_not_silently_empty(self) -> None:
        self.assertEqual(self.reason("time=x level=WARN msg=\"noise\"\n"), "")


REFUSALS_HEADING = "## What the preflight refuses"
RECORDED_HEADING = "## The recorded shape"


class TheDocumentAndTheCodeStateOneRuleSet(unittest.TestCase):
    """The refusal vocabulary is written twice; nothing but this keeps the two the same."""

    def test_every_pin_the_goldens_are_built_from_appears_in_the_document(self) -> None:
        """The pins are written twice as well, and rot the same way a rule name would."""
        text = (CAIRN_ROOT / "docs" / "workflow.md").read_text(encoding="utf-8")
        section = text.split(RECORDED_HEADING)[1].split("\n## ")[0]
        # The occasion is deliberately absent: the emitter declares it empty, so asserting
        # its presence in the document would assert that an empty string appears in prose,
        # which any fenced block satisfies.
        for pin in (str(REPOSITORY), PARENT_BRANCH, PYTHON_PATH):
            with self.subTest(pin=pin):
                self.assertIn(f"`{pin}`", section)

    def test_every_rule_the_code_can_raise_appears_in_the_document(self) -> None:
        """Read out of the one section that states them, and tolerant of cell padding.

        Both matter: another section's table names emitted fields in the same spelling, and
        the padding belongs to the formatter rather than to anyone who edits the document.
        """
        text = (CAIRN_ROOT / "docs" / "workflow.md").read_text(encoding="utf-8")
        section = text.split(REFUSALS_HEADING)[1].split("\n## ")[0]
        documented = set(re.findall(r"^\| *`([a-z_]+)` *\|", section, re.MULTILINE))
        self.assertEqual({rule.name for rule in RULES}, documented)

    def test_every_refusal_says_what_it_prevented(self) -> None:
        """A refusal that cannot say what it stopped reads as pedantry."""
        for rule in RULES:
            with self.subTest(rule=rule.name):
                self.assertIn(rule.consequence, str(Fault(rule.name, "a_step", "found it")))


REBUILD_ELSEWHERE = (
    "import hashlib, json;"
    "from scripts.regenerate_workflows import SHAPES, emitted;"
    "print(json.dumps({s: hashlib.sha256(emitted(s).encode('utf-8')).hexdigest()"
    " for s in SHAPES}))"
)


class TheEmittedFileIsRecordedShapeByShape(unittest.TestCase):
    """One whole file per topology shape, compared byte for byte.

    Everything above this holds for any document that has it, which is exactly what a
    property cannot do for a generated file: an unintended change somewhere else in it still
    passes. These hold for one file and no other. When a change here is deliberate,
    `python3 -m scripts.regenerate_workflows` rewrites them — and refuses to when the shape
    moved under a generator version that already described another one.
    """

    def test_every_topology_shape_has_a_golden_and_every_golden_a_shape(self) -> None:
        self.assertEqual(
            {path.name for path in GOLDENS.glob(f"*{WORKFLOW_SUFFIX}")},
            {f"{shape}{WORKFLOW_SUFFIX}" for shape in SHAPES},
        )

    def test_the_recorded_corpus_holds_nothing_but_recorded_workflows(self) -> None:
        """A `.orig` from a merge or a stray record would otherwise sit there unread."""
        strays = sorted(
            path.name
            for path in GOLDENS.iterdir()
            if path.suffix != WORKFLOW_SUFFIX and not path.name.startswith(".")
        )
        self.assertEqual(strays, [])

    def test_each_golden_is_the_file_the_emitter_writes(self) -> None:
        for shape in SHAPES:
            with self.subTest(shape=shape):
                self.assertEqual(
                    golden_path(shape).read_bytes(),
                    emitted(shape).encode("utf-8"),
                    "the emitted file moved: read the diff, then regenerate with "
                    "python3 -m scripts.regenerate_workflows, run from this package",
                )

    def test_one_added_key_anywhere_in_a_document_leaves_the_golden_behind(self) -> None:
        """The control: a comparison that cannot fail records nothing."""
        moved = build_shape("linear-chain")
        moved["steps"][0]["output"] = "CAPTURED"
        self.assertNotEqual(
            golden_path("linear-chain").read_bytes(), serialise(moved).encode("utf-8")
        )

    def test_no_golden_names_the_machine_it_was_generated_on(self) -> None:
        for shape in SHAPES:
            with self.subTest(shape=shape):
                self.assertEqual(
                    machine_paths_in(golden_path(shape).read_text(encoding="utf-8")), []
                )

    def test_the_scan_for_a_machine_path_finds_one_when_it_is_there(self) -> None:
        """The control: the declared package root is one keyword away from being this checkout's."""
        self.assertIn(
            str(CAIRN_ROOT),
            machine_paths_in(serialise(build_shape("linear-chain", python_path=str(CAIRN_ROOT)))),
        )

    def test_a_golden_rebuilt_under_another_environment_is_the_same_file(self) -> None:
        """The pins are the whole input: no clock, no locale, no working directory, no seed."""
        with tempfile.TemporaryDirectory() as elsewhere:
            completed = subprocess.run(
                (sys.executable, "-c", REBUILD_ELSEWHERE),
                env={
                    "PATH": os.environ.get("PATH", ""),
                    "PYTHONPATH": str(CAIRN_ROOT),
                    "PYTHONHASHSEED": "random",
                    "HOME": elsewhere,
                    "LC_ALL": "C",
                    "TZ": "Pacific/Kiritimati",
                },
                cwd=elsewhere,
                capture_output=True,
                text=True,
                check=True,
            )
        self.assertEqual(
            json.loads(completed.stdout),
            {shape: file_digest(golden_path(shape)) for shape in SHAPES},
        )

    def test_the_goldens_between_them_hold_every_topology_role(self) -> None:
        """A snapshot claims nothing about a shape the corpus does not hold."""
        found: set[str] = set()
        for shape in SHAPES:
            recorded = read(golden_path(shape))
            for node in [*recorded["steps"], *recorded["handler_on"].values()]:
                found.add(parse_node_name(node["name"]).role)
        self.assertEqual(found, set(ROLES))

    def test_the_goldens_hold_a_wave_that_opens_a_run_and_one_that_follows_another(
        self,
    ) -> None:
        """A worktree hangs off the lock where its wave opens the run and off the previous
        wave's commit everywhere else, and no one shape shows both arrangements."""
        edges = {
            tuple(step.get("depends", []))
            for shape in SHAPES
            for step in read(golden_path(shape))["steps"]
            if step["name"].startswith("setup_")
        }
        self.assertIn(("lock_acquire",), edges)
        self.assertTrue(any(edge and edge[0].startswith("commit_") for edge in edges), edges)

    def test_the_bytes_on_disk_earn_no_refusal_from_the_rules(self) -> None:
        """Judged as committed rather than as rebuilt, so the artefact itself is read."""
        for shape in SHAPES:
            with self.subTest(shape=shape):
                self.assertEqual(check(read(golden_path(shape))), [])


class TheEmittedShapeCannotMoveTwiceUnderOneGeneratorVersion(unittest.TestCase):
    """The reader the constant claims: the emitted shape cannot move under it twice.

    The whole emitted text is the shape, and everything the emitter was handed — the plan's
    digest and the four pins the file carries back — is the input, so
    output-moved-while-input-did-not is the generator having moved and nothing else.
    """

    def moved(self, shape: str = "fan-out") -> Workflow:
        rebuilt = build_shape(shape)
        rebuilt["steps"][0]["timeout_sec"] += 1
        return rebuilt

    def test_a_change_confined_to_the_stamp_is_the_shape_moving_too(self) -> None:
        """The labels are the region this constant is most about, so a digest that strips
        them cannot be what the refusal reads."""
        relabelled = build_shape("fan-out")
        relabelled["labels"]["cairn_something_new"] = "x"
        self.assertIsNotNone(refusal("fan-out", read(golden_path("fan-out")), relabelled))

    def test_a_reordering_that_changes_no_value_is_the_shape_moving_too(self) -> None:
        """Declaration order is the file's reading order, so it is part of what moved."""
        reordered = build_shape("fan-out")
        step = reordered["steps"][1]
        reordered["steps"][1] = dict(reversed(list(step.items())))
        self.assertIsNotNone(refusal("fan-out", read(golden_path("fan-out")), reordered))

    def test_a_shape_that_did_not_move_is_rewritten_without_a_word(self) -> None:
        self.assertIsNone(
            refusal("fan-out", read(golden_path("fan-out")), build_shape("fan-out"))
        )

    def test_a_moved_shape_is_refused_under_the_version_that_recorded_the_old_one(
        self,
    ) -> None:
        message = refusal("fan-out", read(golden_path("fan-out")), self.moved())
        self.assertIsNotNone(message)
        self.assertIn("GENERATOR_VERSION", str(message))
        self.assertIn("cairn/workflow/schema.py", str(message))

    def test_a_moved_shape_is_recorded_once_the_version_rises_above_it(self) -> None:
        """The refusal is a fork in the road rather than a wall."""
        self.assertIsNone(
            refusal(
                "fan-out",
                read(golden_path("fan-out")),
                self.moved(),
                generator=GENERATOR_VERSION + 1,
            )
        )

    def test_a_version_below_the_one_the_golden_records_is_refused_as_well(self) -> None:
        """A file written by generator 2 and one written by generator 1 must stay tellable."""
        self.assertIsNotNone(
            refusal(
                "fan-out",
                read(golden_path("fan-out")),
                self.moved(),
                generator=GENERATOR_VERSION - 1,
            )
        )

    def test_a_plan_that_moved_is_rewritten_without_raising_the_version(self) -> None:
        """A corpus edit moves the file legitimately, and a rule that cried wolf here would
        be worked around rather than obeyed."""
        rebuilt = self.moved()
        rebuilt["labels"][LABEL_GRAPH_DIGEST] = "0" * 64
        self.assertIsNone(refusal("fan-out", read(golden_path("fan-out")), rebuilt))

    def test_a_pin_that_moved_is_rewritten_without_raising_the_version(self) -> None:
        """Re-pinning is an input moving, and the file carries every pin back to say so."""
        rebuilt = build_shape("fan-out", python_path="/opt/elsewhere")
        self.assertIsNone(refusal("fan-out", read(golden_path("fan-out")), rebuilt))

    def test_a_plan_digest_the_emitter_stopped_recording_is_the_shape_moving(self) -> None:
        """Two digests that disagree are the plan moving; one that vanished is the labels
        moving, and the labels are the shape."""
        dropped = self.moved()
        del dropped["labels"][LABEL_GRAPH_DIGEST]
        self.assertIsNotNone(refusal("fan-out", read(golden_path("fan-out")), dropped))

    def test_a_parameter_the_emitter_stopped_declaring_is_the_shape_moving(self) -> None:
        """Which values a caller may vary is the shape, so dropping one is not a re-pin —
        and reading it as one would let the whole parameter block move unremarked."""
        dropped = build_shape("fan-out")
        dropped["params"] = [e for e in dropped["params"] if OCCASION_PARAM not in e]
        self.assertIsNotNone(refusal("fan-out", read(golden_path("fan-out")), dropped))

    def test_the_inputs_a_golden_reports_are_the_pins_it_was_built_from(self) -> None:
        """The control for the two tests above: read back, the pins are the pinned values.

        The occasion is not among them. The emitter declares it empty on every file, so a
        change to it is the shape moving rather than a re-pin, and reading it as an input
        would let the parameter's default change without anything asking about the version.
        """
        self.assertEqual(
            inputs_of(read(golden_path("fan-out")))[1],
            {
                REPOSITORY_PARAM: str(REPOSITORY),
                PARENT_BRANCH_PARAM: PARENT_BRANCH,
                "PYTHONPATH": PYTHON_PATH,
            },
        )

    def test_a_golden_carrying_no_generator_at_all_is_repaired_rather_than_refused(
        self,
    ) -> None:
        """Deliberate: a file with no claim about any version is not a version colliding."""
        unstamped = read(golden_path("fan-out"))
        del unstamped["labels"][LABEL_GENERATOR]
        self.assertIsNone(refusal("fan-out", unstamped, self.moved()))


class TheRegenerationCommandJudgesEveryShapeBeforeWritingAny(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def recorded(self) -> dict[str, bytes]:
        return {
            shape: golden_path(shape, into=self.root).read_bytes() for shape in SHAPES
        }

    def test_the_command_writes_every_shape_into_an_empty_directory(self) -> None:
        """The command the failure message names produces what the suite demands."""
        outcome = regenerate(into=self.root)
        self.assertEqual(sorted(outcome.written), sorted(SHAPES))
        self.assertEqual(
            self.recorded(), {shape: golden_path(shape).read_bytes() for shape in SHAPES}
        )

    def test_the_command_writes_nothing_when_nothing_moved(self) -> None:
        regenerate(into=self.root)
        outcome = regenerate(into=self.root)
        self.assertEqual((outcome.written, sorted(outcome.unchanged)), ([], sorted(SHAPES)))

    def test_one_moved_shape_stops_every_shape_from_being_written(self) -> None:
        """A corpus half in one shape and half in another is a state nobody chose.

        A second shape is left in a state the command would otherwise repair, so the claim
        is observable in the tree: were the refusal per-shape, that one would be rewritten.
        """
        regenerate(into=self.root)
        before = self.recorded()
        moved = read(golden_path("fan-out", into=self.root))
        moved["steps"][0]["timeout_sec"] += 1
        golden_path("fan-out", into=self.root).write_text(
            json.dumps(moved, indent=2), encoding="utf-8"
        )
        golden_path("multi-wave", into=self.root).write_text("[]", encoding="utf-8")
        after_edit = self.recorded()

        outcome = regenerate(into=self.root)
        self.assertEqual(list(outcome.refused), ["fan-out"])
        self.assertEqual((outcome.written, outcome.unchanged), ([], []))
        self.assertEqual(self.recorded(), after_edit)
        self.assertNotEqual(after_edit, before)

    def test_a_recorded_file_that_is_no_longer_a_document_is_repaired(self) -> None:
        """It carries no claim about any generator version, so it is not one moving."""
        regenerate(into=self.root)
        golden_path("fan-out", into=self.root).write_text("[]", encoding="utf-8")
        outcome = regenerate(into=self.root)
        self.assertEqual((outcome.written, outcome.refused), (["fan-out"], {}))
        self.assertEqual(
            golden_path("fan-out", into=self.root).read_bytes(),
            golden_path("fan-out").read_bytes(),
        )

    def test_the_command_reports_a_refusal_as_a_nonzero_exit_naming_the_shape(self) -> None:
        regenerate(into=self.root)
        edited = read(golden_path("multi-wave", into=self.root))
        edited["steps"][0]["timeout_sec"] += 1
        golden_path("multi-wave", into=self.root).write_text(
            json.dumps(edited, indent=2), encoding="utf-8"
        )
        said = io.StringIO()
        with redirect_stderr(said):
            code = regenerate_main(["--into", str(self.root)])
        self.assertEqual(code, 1)
        self.assertIn("multi-wave", said.getvalue())

    def test_the_command_names_every_file_it_wrote_and_exits_zero(self) -> None:
        said = io.StringIO()
        with redirect_stdout(said):
            code = regenerate_main(["--into", str(self.root)])
        self.assertEqual(code, 0)
        for shape in SHAPES:
            with self.subTest(shape=shape):
                self.assertIn(str(golden_path(shape, into=self.root)), said.getvalue())

    def test_a_destination_that_is_not_a_directory_is_a_line_rather_than_a_traceback(
        self,
    ) -> None:
        """A caller cannot tell a crash from a rejection, so the command never crashes."""
        occupied = self.root / "not-a-directory"
        occupied.write_text("", encoding="utf-8")
        said = io.StringIO()
        with redirect_stderr(said):
            self.assertEqual(regenerate_main(["--into", str(occupied)]), 2)
        self.assertIn(str(occupied), said.getvalue())

    def test_a_destination_that_is_not_there_yet_is_created(self) -> None:
        """The corpus can be deleted and rebuilt; only an occupied path is a usage error."""
        fresh = self.root / "made" / "here"
        with redirect_stdout(io.StringIO()):
            self.assertEqual(regenerate_main(["--into", str(fresh)]), 0)
        self.assertEqual(
            golden_path("fan-out", into=fresh).read_bytes(),
            golden_path("fan-out").read_bytes(),
        )


if __name__ == "__main__":
    unittest.main()
