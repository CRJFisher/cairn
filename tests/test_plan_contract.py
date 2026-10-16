import ast
import copy
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, cast

from cairn.core import CairnError
from cairn.gitio import runs_root
from cairn.layout import record_path
from cairn.plan.assertions import answer
from cairn.plan.cli import main
from cairn.plan.home import plans_directory
from cairn.plan.ids import (
    assign_ids,
    derive_plan_slug,
    is_engine_id,
    is_plan_slug,
    plan_slug_collisions,
    sanitise_id,
)
from cairn.plan.questions import ResolutionError, resolve
from cairn.plan.report import render, waves
from cairn.plan.schema import (
    ENGINE_NAME_MAX_BYTES,
    GRAPH_VERSION,
    PREPARE_TIMEOUT,
    SchemaError,
    normalise,
)
from cairn.plan.validate import Finding, validate, validate_for_publication
from cairn.record.harvest import harvest
from cairn.record.store import write_record
from cairn.record.vocabulary import EXIT_NO_RECORD, EXIT_UNFINISHED, VERDICT_EXIT_CODES
from tests.test_run_record import record_of

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(os.path.dirname(HERE), "fixtures", "plans")
NAMES = sorted(os.listdir(FIXTURES))
REAL = ("worktree-hydration", "pattern-lifecycle")
# One recorded run, as the shape every record a harvest reads has to have. The steps are
# replaced per case; everything else is a real extraction's output rather than a sketch.
RECORD_OF_GREEN: dict[str, Any] = cast(dict[str, Any], record_of("green"))


def load(name: str, filename: str) -> Any:
    with open(os.path.join(FIXTURES, name, filename), encoding="utf-8") as handle:
        return json.load(handle)


def minimal(**plan: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "cairn_graph_version": GRAPH_VERSION,
        "plan": {
            "slug": "p",
            "title": "P",
            "source": "README.md",
            "sources": [{"path": "README.md", "sha256": "0" * 64}],
        },
        "steps": [
            {"id": "a", "slug": "a", "title": "A", "task": "Bring a to its end state.",
             "verify": None}
        ],
    }
    base["plan"].update(plan)
    return base


class GoldenGraphs(unittest.TestCase):
    def test_corpus_covers_every_shape_the_contract_names(self) -> None:
        self.assertEqual(
            set(NAMES),
            {
                "linear-chain",
                "fan-out",
                "multi-wave",
                "single-step",
                "task-381",
                "cycle",
                "no-verify",
                "mixed-kinds",
                "no-declared-deps",
                "all-roots",
                "already-done",
                "unjustified-edge",
                "dangling-dependency",
                "non-convergent",
                "worktree-hydration",
                "pattern-lifecycle",
                "stated-in-the-request",
            },
        )

    def test_golden_graphs_are_stored_normalised(self) -> None:
        for name in NAMES:
            with self.subTest(name):
                golden = load(name, "graph.json")
                self.assertEqual(normalise(golden), golden)

    def test_validator_verdict_matches_the_golden_expectation(self) -> None:
        for name in NAMES:
            with self.subTest(name):
                expect = load(name, "expect.json")
                result = validate(load(name, "graph.json"))
                self.assertEqual(result.ok, expect["ok"])
                self.assertEqual(
                    sorted(f.code for f in result.errors), sorted(expect["errors"])
                )
                self.assertEqual(
                    sorted(f.code for f in result.warnings), sorted(expect["warnings"])
                )

    def test_every_quotation_survives_a_recheck_against_the_documents(self) -> None:
        """The verdict is unchanged when the validator can read the sources themselves."""
        for name in NAMES:
            with self.subTest(name):
                expect = load(name, "expect.json")
                result = validate(
                    load(name, "graph.json"), source_root=os.path.join(FIXTURES, name)
                )
                self.assertEqual(
                    sorted(f.code for f in result.errors), sorted(expect["errors"])
                )

    def test_plan_slug_derives_from_the_document_location(self) -> None:
        for name in NAMES:
            with self.subTest(name):
                graph = load(name, "graph.json")
                source = os.path.join(FIXTURES, name, graph["plan"]["source"])
                self.assertEqual(derive_plan_slug(source), graph["plan"]["slug"])
                self.assertEqual(derive_plan_slug(source), name)

    def test_every_graph_pins_every_document_it_was_derived_from(self) -> None:
        for name in NAMES:
            with self.subTest(name):
                graph = load(name, "graph.json")
                on_disk = sorted(
                    entry
                    for entry in os.listdir(os.path.join(FIXTURES, name))
                    if entry.endswith(".md")
                )
                self.assertEqual(sorted(s["path"] for s in graph["plan"]["sources"]), on_disk)
                for source in graph["plan"]["sources"]:
                    path = os.path.join(FIXTURES, name, source["path"])
                    with open(path, "rb") as handle:
                        digest = hashlib.sha256(handle.read()).hexdigest()
                    self.assertEqual(source["sha256"], digest, source["path"])

    def test_every_step_id_is_the_sanitised_form_of_the_plans_own_slug(self) -> None:
        for name in NAMES:
            with self.subTest(name):
                graph = load(name, "graph.json")
                for step in graph["steps"]:
                    self.assertTrue(is_engine_id(step["id"]), step["id"])
                    base = sanitise_id(step["slug"])
                    self.assertRegex(step["id"], rf"^{re.escape(base)}(_\d+)?$")

    def test_the_real_plans_parse_and_are_multi_document(self) -> None:
        for name in REAL:
            with self.subTest(name):
                result = validate(
                    load(name, "graph.json"), source_root=os.path.join(FIXTURES, name)
                )
                self.assertTrue(result.ok, [str(f) for f in result.errors])
                assert result.graph is not None
                self.assertTrue(result.graph["steps"])
                self.assertGreater(len(result.graph["plan"]["sources"]), 1)


class SourceRecheck(unittest.TestCase):
    """What the validator can only check when it can read the documents themselves."""

    def _wh(self) -> tuple[dict[str, Any], str]:
        return load("worktree-hydration", "graph.json"), os.path.join(
            FIXTURES, "worktree-hydration"
        )

    def test_a_document_that_moved_on_since_derivation_is_named(self) -> None:
        graph, root = self._wh()
        graph["plan"]["sources"][2]["sha256"] = "1" * 64
        result = validate(graph, source_root=root)
        self.assertIn("stale_source", [f.code for f in result.errors])
        self.assertIn("02-hydration-hook.md", str(result.errors[0]))

    def test_a_pinned_document_that_is_not_there_is_named(self) -> None:
        graph, root = self._wh()
        graph["plan"]["sources"].append({"path": "06-invented.md", "sha256": "0" * 64})
        result = validate(graph, source_root=root)
        self.assertIn("missing_source", [f.code for f in result.errors])

    def test_a_model_quoting_words_no_document_contains_is_rejected(self) -> None:
        """A model is the one bound that carries its own quotation, so it is rechecked like
        an edge's: the person's words are what the value rests on ([30 B])."""
        graph = load("stated-in-the-request", "graph.json")
        root = os.path.join(FIXTURES, "stated-in-the-request")
        graph["plan"]["model_evidence"] = "use sonnet 5.5, obviously"
        result = validate(graph, source_root=root)
        self.assertIn("evidence_not_in_source", [f.code for f in result.errors])

    def test_a_model_the_plan_states_and_quotes_nothing_for_is_rejected(self) -> None:
        graph = load("stated-in-the-request", "graph.json")
        graph["plan"]["model_evidence"] = None
        self.assertIn("unquoted_model", [f.code for f in validate(graph).errors])

    def test_a_plan_naming_no_model_is_rejected(self) -> None:
        graph = load("stated-in-the-request", "graph.json")
        graph["plan"]["default_model"] = "  "
        self.assertIn("plan_model", [f.code for f in validate(graph).errors])

    def test_a_blank_prepare_is_rejected(self) -> None:
        graph = load("stated-in-the-request", "graph.json")
        graph["plan"]["prepare"] = "   "
        self.assertIn("plan_prepare", [f.code for f in validate(graph).errors])

    def test_a_prepare_bound_that_is_not_positive_is_a_schema_error(self) -> None:
        graph = load("stated-in-the-request", "graph.json")
        graph["plan"]["prepare"] = "uv sync --frozen"
        graph["plan"]["prepare_timeout"] = 0
        self.assertIn("schema", [f.code for f in validate(graph).errors])

    def test_a_plan_states_no_prepare_unless_it_says_so(self) -> None:
        graph = validate(load("stated-in-the-request", "graph.json")).graph
        assert graph is not None
        self.assertIsNone(graph["plan"]["prepare"])
        self.assertEqual(graph["plan"]["prepare_timeout"], PREPARE_TIMEOUT)

    def test_an_edge_quoting_words_no_document_contains_is_rejected(self) -> None:
        graph, root = self._wh()
        graph["steps"][1]["deps"][0]["evidence"] = "Task 01, obviously."
        result = validate(graph, source_root=root)
        self.assertIn("evidence_not_in_source", [f.code for f in result.errors])

    def test_an_edge_quotation_survives_the_documents_own_line_wrapping(self) -> None:
        graph, root = self._wh()
        graph["steps"][4]["deps"][0]["evidence"] = (
            "Tasks 02, 03, 04.\n   This is the last step  —  it removes a "
            "currently-working hook."
        )
        result = validate(graph, source_root=root)
        self.assertNotIn("evidence_not_in_source", [f.code for f in result.errors])

    def test_an_omission_quoting_words_no_document_contains_is_rejected(self) -> None:
        graph, root = self._wh()
        graph["omissions"][0]["evidence"] = "We decided to skip this one."
        result = validate(graph, source_root=root)
        self.assertIn("evidence_not_in_source", [f.code for f in result.errors])

    def test_a_synthesised_verify_command_is_rejected(self) -> None:
        graph, root = self._wh()
        graph["steps"][0]["verify"] = "test -f .claude/worktree.config.json"
        result = validate(graph, source_root=root)
        self.assertIn("invented_verify", [f.code for f in result.errors])

    def test_a_graph_pinning_no_document_cannot_be_rechecked(self) -> None:
        graph = minimal()
        graph["plan"]["sources"] = []
        self.assertIn("no_sources", [f.code for f in validate(graph).errors])

    def test_the_index_document_must_be_among_the_pinned_sources(self) -> None:
        graph = minimal(source="OTHER.md")
        self.assertIn("source_not_pinned", [f.code for f in validate(graph).errors])

    def test_the_same_document_is_never_pinned_twice(self) -> None:
        graph = minimal()
        graph["plan"]["sources"].append({"path": "README.md", "sha256": "0" * 64})
        self.assertIn("duplicate_source", [f.code for f in validate(graph).errors])


class ValidatorMessages(unittest.TestCase):
    """The failures the contract promises a specific, actionable message for."""

    def _first(self, name: str, code: str) -> Finding:
        result = validate(load(name, "graph.json"))
        for finding in result.errors + result.warnings:
            if finding.code == code:
                return finding
        self.fail(f"{name} produced no {code} finding")

    def test_a_cycle_names_its_steps(self) -> None:
        finding = self._first("cycle", "cycle")
        self.assertIn("checker -> emitter -> parser -> checker", finding.message)

    def test_an_assertion_bound_the_document_did_not_state_is_a_warning_too(self) -> None:
        graph = minimal()
        graph["steps"][0]["verify_timeout"] = 1800
        result = validate(graph)
        self.assertTrue(result.ok)
        messages = [f.message for f in result.warnings if f.code == "derived_timeout"]
        self.assertTrue(any("assertion at 1800 s" in message for message in messages), messages)

    def test_an_unresolved_dependency_names_both_ends(self) -> None:
        finding = self._first("dangling-dependency", "unresolved_dependency")
        self.assertIn("write_the_renderer", finding.message)
        self.assertIn("theme_compiler", finding.message)

    def test_an_unjustified_edge_names_the_edge(self) -> None:
        finding = self._first("unjustified-edge", "unjustified_edge")
        self.assertIn("fix_the_date_parser -> fix_the_csv_writer", finding.message)
        self.assertEqual(finding.step, "fix_the_csv_writer")

    def test_a_non_convergent_task_is_a_question_the_derivation_declared(self) -> None:
        """The duplication is a reading of the plan, so the derivation declares it —
        naming the step and quoting the sentence it read — and no code re-reads the task."""
        graph = load("non-convergent", "graph.json")
        declared = [
            question
            for question in graph["questions"]
            if question["kind"] == "non_convergent_task"
        ]
        self.assertEqual(
            [question["step"] for question in declared],
            ["append_a_section_to_changelog_md", "create_a_new_audit_log_file"],
        )
        for question in declared:
            self.assertTrue(question["evidence"].strip(), question["step"])

    def test_a_plan_whose_every_step_is_already_done_derives_no_work(self) -> None:
        finding = self._first("already-done", "empty_graph")
        self.assertIn("no steps", finding.message)

    def test_a_declared_edge_needs_evidence_exactly_as_a_derived_one_does(self) -> None:
        """The origin changes what the report says, never whether a quote is required."""
        graph = load("linear-chain", "graph.json")
        graph["steps"][1]["deps"][0]["evidence"] = None
        result = validate(graph)
        self.assertIn("unjustified_edge", [f.code for f in result.errors])
        self.assertIn("declared edge", str(result.errors[0]))


class UncoveredCodes(unittest.TestCase):
    """Every remaining code the validator can emit, exercised once."""

    def test_a_graph_from_another_version_is_refused(self) -> None:
        for version in (1, 3):
            with self.subTest(version=version):
                graph = minimal()
                graph["cairn_graph_version"] = version
                self.assertIn("graph_version", [f.code for f in validate(graph).errors])

    def test_a_command_no_source_document_gives_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = "Run `bin/reindex --full` to rebuild.\n"
            (root / "README.md").write_text(document)
            digest = hashlib.sha256(document.encode()).hexdigest()

            def graph_with(command: str) -> dict[str, Any]:
                graph = minimal(sources=[{"path": "README.md", "sha256": digest}])
                graph["steps"][0].update(
                    {"kind": "command", "command": command, "command_type": "exec"}
                )
                return graph

            quoted = validate(
                graph_with("bin/reindex --full"), source_root=str(root)
            )
            self.assertNotIn(
                "invented_command", [finding.code for finding in quoted.errors]
            )
            invented = validate(
                graph_with("bin/reindex --force"), source_root=str(root)
            )
            self.assertIn(
                "invented_command", [finding.code for finding in invented.errors]
            )

    def test_a_command_step_may_not_declare_a_tool_policy(self) -> None:
        graph = minimal()
        graph["steps"][0].update(
            {
                "kind": "command",
                "command": "true",
                "command_type": "exec",
                "tools": ["Bash(rm:*)"],
            }
        )
        self.assertIn(
            "tools", " ".join(finding.message for finding in validate(graph).errors)
        )

    def test_a_plan_slug_outside_the_grammar_is_refused(self) -> None:
        self.assertIn("plan_slug", [f.code for f in validate(minimal(slug="My Plan")).errors])

    def test_a_slug_over_the_engines_name_bound_is_refused_at_derivation(self) -> None:
        """Refused here rather than at the gate, where a person has already been charged."""
        found = validate(minimal(slug="a" * (ENGINE_NAME_MAX_BYTES + 1))).errors
        self.assertIn("plan_slug", [f.code for f in found])
        said = " ".join(f.message for f in found)
        self.assertIn(str(ENGINE_NAME_MAX_BYTES), said)
        self.assertIn("DAG name", said)

    def test_a_slug_at_the_engines_name_bound_passes(self) -> None:
        """Measured against Dagu 2.18.2: 40 loads, 41 is refused."""
        self.assertTrue(validate(minimal(slug="a" * ENGINE_NAME_MAX_BYTES)).ok)

    def test_an_id_the_engine_would_reject_is_refused(self) -> None:
        graph = minimal()
        graph["steps"][0]["id"] = "config-schema"
        self.assertIn("step_id", [f.code for f in validate(graph).errors])

    def test_a_trailing_newline_does_not_smuggle_an_id_past_the_grammar(self) -> None:
        self.assertFalse(is_engine_id("config_schema\n"))

    def test_two_steps_sharing_an_id_are_refused(self) -> None:
        graph = minimal()
        graph["steps"].append(dict(graph["steps"][0]))
        result = validate(graph)
        self.assertIn("duplicate_id", [f.code for f in result.errors])
        self.assertIn("duplicate_slug", [f.code for f in result.errors])

    def test_the_same_edge_declared_twice_is_refused(self) -> None:
        graph = load("linear-chain", "graph.json")
        graph["steps"][1]["deps"].append(dict(graph["steps"][1]["deps"][0]))
        self.assertIn("duplicate_dependency", [f.code for f in validate(graph).errors])

    def test_a_step_with_no_task_is_refused(self) -> None:
        graph = minimal()
        graph["steps"][0]["task"] = "   "
        self.assertIn("empty_task", [f.code for f in validate(graph).errors])

    def test_a_task_led_by_a_command_that_is_not_a_step_is_refused(self) -> None:
        """Each does nothing a step could be verified for, and none has a file whose
        frontmatter could say so, which is why Cairn keeps the list."""
        for command in ("/clear", "/compact", "/help", "/login", "/loop 5m /code-review"):
            with self.subTest(command=command):
                graph = minimal()
                graph["steps"][0]["task"] = f"{command}\nThen carry on."
                refused = [f for f in validate(graph).errors if f.code == "not_a_step"]
                self.assertEqual(len(refused), 1)
                self.assertIn(command, refused[0].message)

    def test_a_task_led_by_a_command_that_runs_as_a_step_is_admitted(self) -> None:
        for task in ("/code-review high --fix\nFix what it finds.", "Clear the cache: /clear"):
            with self.subTest(task=task):
                graph = minimal()
                graph["steps"][0]["task"] = task
                self.assertNotIn("not_a_step", [f.code for f in validate(graph).errors])

    def test_a_non_positive_verify_timeout_and_a_negative_retry_count_are_refused(self) -> None:
        graph = minimal()
        graph["steps"][0]["verify_timeout"] = 0
        graph["steps"][0]["retries"] = -1
        messages = [f.message for f in validate(graph).errors if f.code == "schema"]
        self.assertTrue(any("steps[0].verify_timeout" in message for message in messages), messages)
        self.assertTrue(any("steps[0].retries" in message for message in messages), messages)

    def test_a_remedy_needs_an_agent_session_to_resume_and_an_assertion_to_rerun(self) -> None:
        graph = minimal()
        graph["steps"][0]["remediate"] = True
        graph["steps"][0]["kind"] = "command"
        graph["steps"][0]["command"] = "true"
        graph["steps"][0]["command_type"] = "exec"
        self.assertIn("unremediable_step", [f.code for f in validate(graph).errors])

    def test_a_non_positive_assertion_bound_is_refused(self) -> None:
        graph = minimal()
        graph["steps"][0]["verify_timeout"] = 0
        messages = [f.message for f in validate(graph).errors if f.code == "schema"]
        self.assertTrue(any("steps[0].verify_timeout" in message for message in messages), messages)

    def test_a_name_that_is_both_a_step_and_an_omission_is_refused(self) -> None:
        graph = minimal()
        graph["omissions"] = [
            {"slug": "a", "title": "A", "reason": "deferred", "evidence": "later"}
        ]
        self.assertIn("omitted_and_included", [f.code for f in validate(graph).errors])

    def test_a_question_naming_no_step_in_the_graph_is_refused(self) -> None:
        graph = minimal()
        graph["questions"] = [
            {"kind": "missing_verify", "step": "ghost", "question": "What asserts it?"}
        ]
        self.assertIn("unknown_question_step", [f.code for f in validate(graph).errors])

    def test_reads_under_a_scope_that_never_hashes_them_is_a_warning(self) -> None:
        graph = minimal()
        graph["steps"][0]["reads"] = ["src/**"]
        result = validate(graph)
        self.assertTrue(result.ok)
        self.assertIn("unused_reads", [f.code for f in result.warnings])

    def test_a_self_dependency_is_named_as_such(self) -> None:
        graph = minimal()
        graph["steps"][0]["deps"] = [{"id": "a", "origin": "declared", "evidence": "x"}]
        self.assertIn("self_dependency", [f.code for f in validate(graph).errors])

    def test_a_malformed_graph_fails_before_any_topology_check(self) -> None:
        result = validate({"plan": {"slug": "p"}, "steps": []})
        self.assertFalse(result.ok)
        self.assertEqual({f.code for f in result.errors}, {"schema"})
        self.assertIsNone(result.graph)


class Robustness(unittest.TestCase):
    """A verdict, always. A crash and a rejection are indistinguishable to a caller."""

    def test_a_value_of_the_wrong_shape_is_a_verdict_not_an_exception(self) -> None:
        cases: dict[str, Any] = {
            "omissions holding a number": {"omissions": [1]},
            "omissions holding a string": {"omissions": ["xy"]},
            "questions holding a number": {"questions": [7]},
            "sources that are not a list": {"plan": {"sources": 5}},
            "collisions that are not a list": {"plan": {"id_collisions": 5}},
        }
        for label, patch in cases.items():
            with self.subTest(label):
                graph = minimal()
                graph.update({k: v for k, v in patch.items() if k != "plan"})
                if "plan" in patch:
                    graph["plan"].update(patch["plan"])
                result = validate(graph)
                self.assertFalse(result.ok)
                self.assertEqual({f.code for f in result.errors}, {"schema"})

    def test_a_step_whose_deps_are_not_a_list_is_a_verdict(self) -> None:
        graph = minimal()
        graph["steps"][0]["deps"] = 5
        self.assertEqual({f.code for f in validate(graph).errors}, {"schema"})

    def test_a_kind_that_is_not_a_string_never_reaches_the_timeout_default(self) -> None:
        graph = minimal()
        graph["steps"][0]["kind"] = ["agent.claude"]
        self.assertEqual({f.code for f in validate(graph).errors}, {"schema"})

    def test_a_plan_longer_than_the_interpreters_stack_still_gets_a_verdict(self) -> None:
        """Recursion would make the same graph valid on one machine and a crash on another."""
        graph = minimal()
        graph["steps"] = [
            {
                "id": f"s{i}",
                "slug": f"s{i}",
                "title": "T",
                "task": "Bring it to its end state.",
                "verify": None,
                "deps": (
                    [{"id": f"s{i - 1}", "origin": "declared", "evidence": "x"}] if i else []
                ),
            }
            for i in range(5000)
        ]
        result = validate(graph)
        self.assertEqual([f.code for f in result.errors], [])

    def test_a_cycle_deeper_than_the_stack_is_still_named(self) -> None:
        graph = minimal()
        graph["steps"] = [
            {
                "id": f"s{i}",
                "slug": f"s{i}",
                "title": "T",
                "task": "Bring it to its end state.",
                "verify": None,
                "deps": [{"id": f"s{(i - 1) % 3000}", "origin": "declared", "evidence": "x"}],
            }
            for i in range(3000)
        ]
        self.assertEqual([f.code for f in validate(graph).errors], ["cycle"])

    def test_a_line_break_in_a_field_cannot_end_the_table(self) -> None:
        graph = minimal()
        graph["steps"][0]["slug"] = "cr\rslug"
        graph["steps"].append(
            {"id": "b", "slug": "b", "title": "B", "task": "Bring b along.", "verify": "true"}
        )
        text = render(graph, None)
        self.assertEqual(len([line for line in text.splitlines() if line.startswith("| `")]), 2)
        self.assertNotIn("\r", text)

    def test_a_backtick_in_a_verify_command_keeps_its_code_span(self) -> None:
        graph = minimal()
        graph["steps"][0]["verify"] = "echo `date`"
        self.assertIn("`` echo `date` ``", render(graph, None))

    def test_the_report_says_how_a_worktree_is_made_ready(self) -> None:
        prepared = render(minimal(prepare="uv sync --frozen", prepare_timeout=900), None)
        self.assertIn("## Worktrees", prepared)
        self.assertIn(
            "every worktree is prepared with `uv sync --frozen`, bounded at 900s", prepared
        )
        self.assertIn("no prepare: a worktree holds only what git tracks", render(minimal(), None))

    def test_a_graph_that_cannot_be_read_exits_differently_from_one_that_fails(self) -> None:
        def run(*graph: str) -> int:
            with (
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()),
            ):
                return main(["validate", os.path.join(FIXTURES, *graph)])

        self.assertEqual(run("no-such.json"), 2)
        self.assertEqual(run("cycle", "graph.json"), 1)
        self.assertEqual(run("linear-chain", "graph.json"), 0)


class Topology(unittest.TestCase):
    def test_a_cycle_short_circuits_the_rest_of_the_topology_checks(self) -> None:
        result = validate(load("cycle", "graph.json"))
        self.assertEqual([f.code for f in result.errors], ["cycle"])

    def test_an_edge_already_implied_transitively_is_a_warning(self) -> None:
        result = validate(load("worktree-hydration", "graph.json"))
        redundant = [f for f in result.warnings if f.code == "redundant_edge"]
        self.assertEqual(len(redundant), 2)
        self.assertTrue(all(f.step == "ariadne_migration" for f in redundant))

    def test_a_diamond_reports_no_redundant_edge(self) -> None:
        result = validate(load("multi-wave", "graph.json"))
        self.assertEqual([f.code for f in result.warnings], [])

    def test_waves_expose_the_concurrency_the_plan_allows(self) -> None:
        self.assertEqual(
            waves(normalise(load("multi-wave", "graph.json"))),
            [["export_schema"], ["writer"], ["reader", "zip_packer"], ["reconcile"]],
        )

    def test_a_graph_with_no_topology_has_no_waves_to_show(self) -> None:
        self.assertIsNone(waves(normalise(load("cycle", "graph.json"))))
        self.assertIsNone(waves(normalise(load("dangling-dependency", "graph.json"))))

    def test_a_plan_that_connects_nothing_is_all_roots(self) -> None:
        self.assertEqual(
            waves(normalise(load("all-roots", "graph.json"))),
            [["drop_the_legacy_exporter", "tighten_the_log_format"]],
        )


class StepRecord(unittest.TestCase):
    def test_defaults_come_from_the_contract_not_from_the_plan(self) -> None:
        step = normalise(minimal())["steps"][0]
        self.assertEqual(step["kind"], "agent.claude")
        self.assertEqual(step["scope"], "once")
        self.assertEqual(step["deps"], [])
        self.assertIsNone(step["tools"])
        self.assertEqual(step["retries"], 0)

    def test_retry_defaults_follow_the_kind(self) -> None:
        graph = minimal()
        graph["steps"][0]["kind"] = "command"
        graph["steps"][0]["command"] = "make build"
        graph["steps"][0]["command_type"] = "exec"
        step = normalise(graph)["steps"][0]
        self.assertEqual(step["retries"], 0)

    def test_a_defaulted_list_is_never_shared_between_steps(self) -> None:
        graph = minimal()
        graph["steps"].append(
            {"id": "b", "slug": "b", "title": "B", "task": "Bring b along.", "verify": None}
        )
        normalised = normalise(graph)
        normalised["steps"][0]["reads"].append("src/**")
        self.assertEqual(normalised["steps"][1]["reads"], [])

    def test_normalising_leaves_the_callers_own_graph_alone(self) -> None:
        raw = minimal()
        before = copy.deepcopy(raw)
        normalise(raw)
        self.assertEqual(raw, before)

    def test_the_run_level_default_kind_reaches_every_step(self) -> None:
        raw = minimal(default_kind="command")
        raw["steps"][0]["command"] = "printf done"
        raw["steps"][0]["command_type"] = "exec"
        graph = normalise(raw)
        self.assertEqual(graph["steps"][0]["kind"], "command")

    def test_command_text_is_explicit_and_command_only(self) -> None:
        raw = minimal(default_kind="command")
        with self.assertRaisesRegex(SchemaError, "requires a non-empty 'command'"):
            normalise(raw)
        raw["steps"][0]["command"] = "printf done"
        raw["steps"][0]["command_type"] = "exec"
        self.assertEqual(normalise(raw)["steps"][0].get("command"), "printf done")

        agent = minimal()
        agent["steps"][0]["command"] = "printf done"
        with self.assertRaisesRegex(SchemaError, "agent kind must not carry"):
            normalise(agent)

    def test_a_missing_verify_key_is_rejected_while_a_null_one_is_recorded(self) -> None:
        base = minimal()
        del base["steps"][0]["verify"]
        with self.assertRaises(SchemaError):
            normalise(base)
        base["steps"][0]["verify"] = None
        self.assertIsNone(normalise(base)["steps"][0]["verify"])

    def test_an_unknown_field_is_rejected_rather_than_ignored(self) -> None:
        graph = minimal()
        graph["steps"][0]["retry"] = 3
        with self.assertRaises(SchemaError):
            normalise(graph)

    def test_a_kind_outside_the_plan_authorable_vocabulary_is_rejected(self) -> None:
        for kind in ("worktree", "verify", "merge", "wait", "agent", "agent.Claude"):
            with self.subTest(kind):
                graph = minimal()
                graph["steps"][0]["kind"] = kind
                with self.assertRaises(SchemaError):
                    normalise(graph)

    def test_an_agent_step_resolves_its_own_model(self) -> None:
        """A plan that says nothing still names who does the work: the session an agent
        step opens cannot be attributed without a model."""
        step = normalise(minimal())["steps"][0]
        self.assertEqual(step["model"], "sonnet")

    def test_a_command_step_carries_no_model(self) -> None:
        raw = minimal(default_kind="command")
        raw["steps"][0]["command"] = "printf done"
        raw["steps"][0]["command_type"] = "exec"
        step = normalise(raw)["steps"][0]
        self.assertIsNone(step["model"])
        declared = copy.deepcopy(raw)
        declared["steps"][0]["model"] = "sonnet"
        with self.assertRaisesRegex(SchemaError, "command kind must not carry"):
            normalise(declared)

    def test_a_blank_model_is_a_validation_error(self) -> None:
        graph = minimal()
        graph["steps"][0]["model"] = "   "
        self.assertIn("model", [f.code for f in validate(graph).errors])

    def test_an_inputs_scope_without_declared_reads_fails(self) -> None:
        graph = load("mixed-kinds", "graph.json")
        graph["steps"][0]["scope"] = "inputs"
        self.assertIn("scope_inputs", [f.code for f in validate(graph).errors])

    def test_the_corpus_exercises_every_field_the_record_carries(self) -> None:
        kinds: set[str] = set()
        scopes: set[str] = set()
        tools = reads = models = 0
        for name in NAMES:
            for step in load(name, "graph.json")["steps"]:
                kinds.add(step["kind"])
                scopes.add(step["scope"])
                tools += bool(step["tools"])
                reads += bool(step["reads"])
                models += step.get("model") not in (None, "sonnet")
        self.assertEqual(kinds, {"agent.claude", "command"})
        self.assertTrue({"once", "run", "weekly", "inputs"} <= scopes)
        self.assertTrue(tools and reads and models)


class Identifiers(unittest.TestCase):
    def test_the_leading_ordinal_is_display_not_identity(self) -> None:
        self.assertEqual(sanitise_id("01-config-schema"), "config_schema")
        self.assertEqual(sanitise_id("1. Config schema"), "config_schema")
        self.assertEqual(
            sanitise_id("08.9-native-dependency-provisioning"),
            "native_dependency_provisioning",
        )

    def test_a_bare_space_is_not_an_ordinal_separator(self) -> None:
        self.assertEqual(sanitise_id("3 things"), "s_3_things")

    def test_an_id_that_cannot_start_with_a_letter_is_prefixed(self) -> None:
        self.assertTrue(is_engine_id(sanitise_id("2024 audit")))
        self.assertTrue(is_engine_id(sanitise_id("!!!")))
        self.assertTrue(is_engine_id(sanitise_id("")))

    def test_collisions_are_broken_positionally_and_recorded(self) -> None:
        assignments, collisions = assign_ids(["01-foo", "02-foo", "foo"])
        self.assertEqual([a["id"] for a in assignments], ["foo", "foo_2", "foo_3"])
        self.assertEqual([c["slug"] for c in collisions], ["02-foo", "foo"])
        self.assertEqual(collisions[0]["clashed_with"], "01-foo")

    def test_collision_breaking_is_stable_across_derivations(self) -> None:
        source = ["01-foo", "02-foo", "foo"]
        self.assertEqual(assign_ids(source)[0], assign_ids(source)[0])

    def test_an_index_document_is_named_by_its_folder(self) -> None:
        self.assertEqual(derive_plan_slug("/tmp/p/offline-export/README.md"), "offline-export")
        self.assertEqual(derive_plan_slug("/tmp/p/offline-export/WORKLIST.md"), "offline-export")
        self.assertEqual(derive_plan_slug("/tmp/p/offline-export/04-thing.md"), "04-thing")

    def test_a_task_shaped_name_takes_its_own_id_as_the_slug(self) -> None:
        """A backlog document's file name is its whole title; the id is what names the plan."""
        long_name = (
            "/p/task-381 - Report entry points for a repository of vscode's scale.md"
        )
        self.assertEqual(derive_plan_slug(long_name), "task-381")
        self.assertEqual(derive_plan_slug("/p/TASK-381.4 Something else.md"), "task-381-4")

    def test_a_name_that_merely_opens_with_a_task_id_keeps_all_of_itself(self) -> None:
        """The whitespace is what separates an id from a title, so a hyphenated name is whole."""
        self.assertEqual(
            derive_plan_slug("/p/task-381-migration-plan.md"), "task-381-migration-plan"
        )

    def test_a_long_name_is_cut_at_a_hyphen_and_carries_a_digest(self) -> None:
        slug = derive_plan_slug(
            "/p/a-very-long-plan-document-name-that-goes-on-and-on-past-the-bound.md"
        )
        self.assertLessEqual(len(slug.encode("utf-8")), ENGINE_NAME_MAX_BYTES)
        self.assertTrue(is_plan_slug(slug))
        self.assertFalse(slug.endswith("-"))
        # Cut at a hyphen, so the readable half is whole words rather than a mid-word chop.
        self.assertTrue("a-very-long-plan-document-name".startswith(slug.rsplit("-", 1)[0]))

    def test_two_long_names_that_cut_alike_still_derive_two_slugs(self) -> None:
        """The digest is of the whole name, so a plan cannot adopt another plan's worktrees."""
        first = derive_plan_slug("/p/a-very-long-plan-document-name-past-the-bound.md")
        second = derive_plan_slug("/p/a-very-long-plan-document-name-past-the-bounds.md")
        self.assertNotEqual(first, second)

    def test_the_derivation_is_a_function_of_the_document_alone(self) -> None:
        """Called twice, twice the same — it reads no filesystem state but the path itself.

        A slug that varied with what is already on disk would re-point a plan at a different
        worktree parent and a different run record the second time it was derived.
        """
        name = "/p/a-very-long-plan-document-name-that-goes-on-and-on-past-the-bound.md"
        self.assertEqual(derive_plan_slug(name), derive_plan_slug(name))

    def test_every_corpus_plan_derives_a_slug_the_engine_could_load(self) -> None:
        for name in sorted(os.listdir(FIXTURES)):
            graph = json.loads(
                Path(FIXTURES, name, "graph.json").read_text(encoding="utf-8")
            )
            with self.subTest(plan=name):
                self.assertTrue(is_plan_slug(graph["plan"]["slug"]))

    def test_the_corpus_holds_a_plan_whose_document_name_exceeds_the_bound(self) -> None:
        """The fixture is load-bearing rather than decorative, and this is what says so."""
        over = [
            name
            for name in sorted(os.listdir(FIXTURES))
            if len(
                json.loads(
                    Path(FIXTURES, name, "graph.json").read_text(encoding="utf-8")
                )["plan"]["source"].encode("utf-8")
            )
            > ENGINE_NAME_MAX_BYTES
        ]
        self.assertTrue(over, "no corpus plan exercises the bound the engine enforces")

    def test_a_slug_collides_with_nothing_that_exists(self) -> None:
        self.assertEqual(plan_slug_collisions("no-such-plan", [FIXTURES]), [])
        self.assertEqual(
            plan_slug_collisions("linear-chain", [FIXTURES]),
            [os.path.join(FIXTURES, "linear-chain")],
        )

    def test_a_recorded_collision_reaches_the_report(self) -> None:
        graph = minimal()
        graph["plan"]["id_collisions"] = [
            {
                "slug": "02-foo",
                "sanitised_to": "foo",
                "assigned": "foo_2",
                "clashed_with": "01-foo",
            }
        ]
        text = render(graph, None)
        self.assertIn("Renamed to fit the engine", text)
        self.assertIn("`foo_2`", text)


class DeclaredReadings(unittest.TestCase):
    """The two readings the derivation declares — convergence and a proposed assertion —
    each rest on quoted words, and the validator checks the quote, never the sentence."""

    def _sourced(self, name: str, graph: dict[str, Any]) -> list[str]:
        result = validate(graph, source_root=os.path.join(FIXTURES, name))
        return [f.code for f in result.errors]

    def test_a_declared_duplication_is_carried_as_a_question(self) -> None:
        """`add the hook to settings.json` duplicates on a re-run; the derivation read
        that and declared it, and the graph is where the reading lives."""
        graph = normalise(load("worktree-hydration", "graph.json"))
        self.assertTrue(
            any(
                q["kind"] == "non_convergent_task" and q["step"] == "global_wiring"
                for q in graph["questions"]
            )
        )

    def test_a_declaration_quoting_nothing_is_refused(self) -> None:
        for name, patch in (
            ("non-convergent", {"kind": "non_convergent_task"}),
            ("no-verify", {"kind": "missing_verify", "proposed": "test -e guide.md"}),
        ):
            with self.subTest(name):
                graph = load(name, "graph.json")
                graph["questions"][0].update(patch)
                graph["questions"][0]["evidence"] = "  "
                self.assertIn("unquoted_reading", [f.code for f in validate(graph).errors])

    def test_a_declaration_quoting_words_no_document_contains_is_refused(self) -> None:
        graph = load("non-convergent", "graph.json")
        graph["questions"][0]["evidence"] = "Append a fabricated sentence."
        self.assertIn("evidence_not_in_source", self._sourced("non-convergent", graph))

    def test_a_proposal_survives_the_recheck_on_the_words_it_rests_on(self) -> None:
        for name in ("worktree-hydration", "pattern-lifecycle"):
            with self.subTest(name):
                graph = load(name, "graph.json")
                self.assertTrue(
                    any(
                        q["kind"] == "missing_verify" and q["proposed"]
                        for q in graph["questions"]
                    )
                )
                self.assertEqual(self._sourced(name, graph), [])

    def test_a_misquoted_proposal_is_refused(self) -> None:
        graph = load("worktree-hydration", "graph.json")
        proposing = next(
            q for q in graph["questions"] if q["kind"] == "missing_verify" and q["proposed"]
        )
        proposing["evidence"] = "One authoritative description, roughly."
        self.assertIn("evidence_not_in_source", self._sourced("worktree-hydration", graph))

    def test_a_proposal_that_cannot_fail_is_refused(self) -> None:
        graph = load("no-verify", "graph.json")
        graph["questions"][0]["proposed"] = "true"
        graph["questions"][0]["evidence"] = "Rewrite the getting-started guide"
        self.assertIn("unassertable_proposal", [f.code for f in validate(graph).errors])

    def test_a_proposal_that_is_the_steps_own_command_is_refused(self) -> None:
        """An offer that asserts nothing is refused at the offer: an author shown it has
        already been asked to adopt it."""
        graph = load("mixed-kinds", "graph.json")
        step = next(s for s in graph["steps"] if s["id"] == "wait_for_the_index_to_settle")
        step["verify"] = None
        graph["questions"].append(
            {
                "kind": "missing_verify",
                "step": step["id"],
                "dep": None,
                "question": "What asserts this step's end state?",
                "evidence": "Wait for the index to settle",
                "proposed": step["command"],
                "resolution": None,
            }
        )
        self.assertIn("self_asserting_step", [f.code for f in validate(graph).errors])

    def test_a_step_asserting_itself_with_its_own_command_is_refused(self) -> None:
        """A `command` step that ran is not a `command` step that worked, and the corpus's
        own poll is the case that produced the rule: a `wait_until` that already succeeded
        succeeds again over a tree nothing looked at."""
        graph = load("mixed-kinds", "graph.json")
        step = next(s for s in graph["steps"] if s["id"] == "wait_for_the_index_to_settle")
        step["verify"] = step["command"]
        found = [f for f in validate(graph).errors if f.code == "self_asserting_step"]
        self.assertEqual([f.step for f in found], [step["id"]])

    def test_a_proposal_on_any_other_question_kind_is_refused(self) -> None:
        graph = load("dangling-dependency", "graph.json")
        graph["questions"][0]["proposed"] = "test -e renderer.md"
        self.assertEqual({f.code for f in validate(graph).errors} & {"schema"}, {"schema"})


class OnlyTheAgentReadsEnglish(unittest.TestCase):
    """No code in `cairn/` decides anything from the meaning of a sentence.

    In the register of the deny-list test over `cairn/skill/`: mechanical, over the
    source. A word-boundary token is the signature of a pattern matched against prose —
    every reader this tree has deleted carried one — while every regex that remains is
    over an identifier, a machine format, or whitespace being flattened for a verbatim
    quote check. A `\\b` added under `cairn/` is a prose reader until argued otherwise.
    """

    def test_no_regex_in_cairn_is_shaped_to_read_prose(self) -> None:
        package = Path(os.path.dirname(FIXTURES)).parent / "cairn"
        for path in sorted(package.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                target = node.func
                if not (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "re"
                ):
                    continue
                for argument in node.args:
                    if isinstance(argument, ast.Constant) and isinstance(
                        argument.value, str
                    ):
                        with self.subTest(module=path.name, pattern=argument.value):
                            self.assertNotIn("\\b", argument.value)


class ParseReport(unittest.TestCase):
    def test_every_fixture_renders_with_every_step_and_its_task(self) -> None:
        for name in NAMES:
            with self.subTest(name):
                graph = load(name, "graph.json")
                text = render(graph, validate(graph))
                for step in graph["steps"]:
                    self.assertIn(f"`{step['id']}`", text)
                    self.assertIn(step["task"].split(".")[0][:40], text)

    def test_a_derived_edge_is_marked_derived_with_its_evidence(self) -> None:
        text = render(load("no-declared-deps", "graph.json"), None)
        self.assertIn("**derived**, on the words: named for the version", text)

    def test_a_declared_edge_carries_its_evidence_too(self) -> None:
        text = render(load("worktree-hydration", "graph.json"), None)
        self.assertIn("declared, on the words: Tasks 02, 03.", text)

    def test_omissions_reach_the_report_with_their_cause(self) -> None:
        text = render(load("worktree-hydration", "graph.json"), None)
        self.assertIn("Lockfile fingerprinting", text)
        self.assertIn("deferred", text)
        self.assertIn("already_done", text)

    def test_questions_reach_the_report(self) -> None:
        text = render(load("pattern-lifecycle", "graph.json"), None)
        self.assertIn("plan_gated", text)
        self.assertIn("ambiguous_dependency", text)

    def test_a_verify_command_absence_is_shown_as_an_absence(self) -> None:
        text = render(load("no-verify", "graph.json"), None)
        self.assertIn("**never asked**", text)

    def test_an_untopological_graph_is_never_given_a_wave_list(self) -> None:
        text = render(load("cycle", "graph.json"), validate(load("cycle", "graph.json")))
        self.assertIn("The dependencies do not form a topology.", text)
        self.assertNotIn("1. `checker`", text)

    def test_a_multi_document_plan_says_how_many_it_read(self) -> None:
        text = render(load("worktree-hydration", "graph.json"), None)
        self.assertIn("Derived from 6 documents.", text)

    def test_every_report_names_the_model_each_session_opens_on(self) -> None:
        for name in NAMES:
            with self.subTest(name):
                graph = load(name, "graph.json")
                text = render(graph, None)
                self.assertIn(f"every session: `{graph['plan']['default_model']}`", text)
                self.assertIn(f"the merge resolver: `{graph['plan']['default_model']}`", text)

    def test_a_step_departing_from_the_plans_model_is_named_as_departing(self) -> None:
        text = render(load("mixed-kinds", "graph.json"), None)
        self.assertIn("instead opens on `opus`", text)

    def test_a_plan_stated_in_a_request_reports_its_waves_its_edge_and_its_models(self) -> None:
        """[30]: the report is the whole of what the person confirms — the waves their
        sentences described, the edge in their own words, and every session on the model
        they asked for, the resolver included."""
        graph = load("stated-in-the-request", "graph.json")
        text = render(graph, validate(graph))
        self.assertIn("7 step(s) in 3 wave(s); widest wave 4.", text)
        self.assertIn(
            "on the words: By then TASK-376.21 has landed, and these two touch different "
            "files.",
            text,
        )
        self.assertIn(
            "every session: `claude-sonnet-5-5` — on the words: All steps should use "
            "sonnet 5.5.",
            text,
        )
        self.assertIn("the merge resolver: `claude-sonnet-5-5`", text)
        self.assertIn("no step departs from it.", text)
        self.assertEqual(
            {step["model"] for step in graph["steps"]}, {"claude-sonnet-5-5"}
        )


if __name__ == "__main__":
    unittest.main()


def two_steps(**question: Any) -> dict[str, Any]:
    """A graph of two roots and one question about them, answered or not."""
    graph = minimal()
    graph["steps"].append(
        {"id": "b", "slug": "b", "title": "B", "task": "Bring b to its end state.",
         "verify": None}
    )
    graph["questions"] = [{"question": "Is it so?", **question}]
    return graph


def answered(graph: dict[str, Any], kind: str, **arguments: Any) -> Any:
    """The graph after one answer, through the one function every answer goes through."""
    return resolve(normalise(graph), kind, **arguments)


class AnsweringEveryQuestion(unittest.TestCase):
    """Every question kind closes through a recorded answer that the graph's facts carry."""

    def test_an_accepted_unjustified_edge_puts_the_edge_in_the_graph(self) -> None:
        graph = answered(
            two_steps(kind="unjustified_edge", step="b", dep="a"),
            "unjustified_edge", step="b", dep="a", outcome="accepted", reading=None,
            reason="b reads what a writes",
        )
        edge = graph["steps"][1]["deps"]
        self.assertEqual(
            edge, [{"id": "a", "origin": "answered", "evidence": "b reads what a writes"}]
        )
        self.assertEqual(graph["questions"][0]["resolution"]["outcome"], "accepted")
        self.assertTrue(validate(graph).ok, [str(f) for f in validate(graph).errors])

    def test_an_answered_edge_needs_no_quotation_from_the_documents(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            document = "Two unrelated things.\n"
            (Path(temporary) / "README.md").write_text(document)
            digest = hashlib.sha256(document.encode()).hexdigest()
            graph = two_steps(kind="unjustified_edge", step="b", dep="a")
            graph["plan"]["sources"] = [{"path": "README.md", "sha256": digest}]
            result = validate(
                answered(
                    graph, "unjustified_edge", step="b", dep="a", outcome="accepted",
                    reading=None, reason="the author says so",
                ),
                source_root=temporary,
            )
            self.assertNotIn("evidence_not_in_source", [f.code for f in result.errors])

    def test_a_declined_ambiguous_dependency_takes_the_edge_out(self) -> None:
        raw = two_steps(kind="ambiguous_dependency", step="b", dep="a")
        raw["steps"][1]["deps"] = [{"id": "a", "origin": "derived", "evidence": "then b"}]
        graph = answered(
            raw, "ambiguous_dependency", step="b", dep="a", outcome="declined",
            reading=None, reason="b stands alone",
        )
        self.assertEqual(graph["steps"][1]["deps"], [])
        self.assertTrue(validate(graph).ok, [str(f) for f in validate(graph).errors])

    def test_an_accepted_ambiguous_dependency_keeps_the_edge_it_doubted(self) -> None:
        raw = two_steps(kind="ambiguous_dependency", step="b", dep="a")
        raw["steps"][1]["deps"] = [{"id": "a", "origin": "derived", "evidence": "then b"}]
        graph = answered(
            raw, "ambiguous_dependency", step="b", dep="a", outcome="accepted",
            reading=None, reason="b does need a",
        )
        self.assertEqual(graph["steps"][1]["deps"][0]["origin"], "derived")
        self.assertTrue(validate(graph).ok, [str(f) for f in validate(graph).errors])

    def test_a_non_convergent_task_is_accepted_edited_or_waived(self) -> None:
        question = {
            "kind": "non_convergent_task", "step": "a", "evidence": "Append a line.",
            "proposed": "Bring the file to a state where it holds the line once.",
        }
        accepted = answered(
            two_steps(**question), "non_convergent_task", step="a", dep=None,
            outcome="accepted", reading=None, reason=None,
        )
        self.assertEqual(accepted["steps"][0]["task"], question["proposed"])
        edited = answered(
            two_steps(**question), "non_convergent_task", step="a", dep=None,
            outcome="edited", reading="Bring the file to hold exactly one line.", reason=None,
        )
        self.assertEqual(edited["steps"][0]["task"], "Bring the file to hold exactly one line.")
        waived = answered(
            two_steps(**question), "non_convergent_task", step="a", dep=None,
            outcome="declined", reading=None, reason="a duplicate line is harmless",
        )
        self.assertEqual(waived["steps"][0]["task"], "Bring a to its end state.")
        for graph in (accepted, edited, waived):
            self.assertTrue(validate(graph).ok, [str(f) for f in validate(graph).errors])

    def test_nothing_proposed_is_nothing_to_accept(self) -> None:
        with self.assertRaises(ResolutionError):
            answered(
                two_steps(kind="non_convergent_task", step="a", evidence="Append."),
                "non_convergent_task", step="a", dep=None, outcome="accepted",
                reading=None, reason=None,
            )

    def test_an_unresolved_reference_is_restated_or_declined_and_never_accepted(self) -> None:
        raw = two_steps(kind="unresolved_reference", step="a")
        edited = answered(
            raw, "unresolved_reference", step="a", dep=None, outcome="edited",
            reading="Bring the renderer to a state where it uses the theme compiler.",
            reason=None,
        )
        self.assertIn("theme compiler", edited["steps"][0]["task"])
        with self.assertRaises(ResolutionError):
            answered(
                raw, "unresolved_reference", step="a", dep=None, outcome="accepted",
                reading=None, reason="yes",
            )

    def test_a_gated_plan_is_only_ever_called_live_and_says_why(self) -> None:
        raw = two_steps(kind="plan_gated")
        live = answered(
            raw, "plan_gated", step=None, dep=None, outcome="accepted", reading=None,
            reason="the author green-lit it",
        )
        self.assertTrue(validate(live).ok, [str(f) for f in validate(live).errors])
        for outcome, reason in (("accepted", None), ("declined", "not yet")):
            with self.subTest(outcome=outcome), self.assertRaises(ResolutionError):
                answered(
                    raw, "plan_gated", step=None, dep=None, outcome=outcome,
                    reading=None, reason=reason,
                )

    def test_a_missing_verify_question_is_answered_on_its_steps_assertion(self) -> None:
        raw = two_steps(kind="missing_verify", step="a")
        with self.assertRaises(ResolutionError):
            answered(
                raw, "missing_verify", step="a", dep=None, outcome="accepted",
                reading=None, reason="yes",
            )
        raw["questions"][0]["resolution"] = {"outcome": "accepted", "reason": "yes"}
        self.assertIn("schema", [f.code for f in validate(raw).errors])

    def test_an_answer_cannot_be_given_twice(self) -> None:
        graph = answered(
            two_steps(kind="plan_gated"), "plan_gated", step=None, dep=None,
            outcome="accepted", reading=None, reason="live",
        )
        with self.assertRaises(ResolutionError):
            resolve(
                graph, "plan_gated", step=None, dep=None, outcome="accepted",
                reading=None, reason="live again",
            )

    def test_a_resolution_the_graph_does_not_carry_is_refused(self) -> None:
        raw = two_steps(kind="unjustified_edge", step="b", dep="a")
        raw["questions"][0]["resolution"] = {"outcome": "accepted", "reason": "b needs a"}
        self.assertIn("unapplied_answer", [f.code for f in validate(raw).errors])
        raw = two_steps(kind="unresolved_reference", step="a")
        raw["questions"][0]["resolution"] = {"outcome": "edited", "reading": "Restated."}
        self.assertIn("unapplied_answer", [f.code for f in validate(raw).errors])

    def test_an_answered_edge_no_question_gave_is_refused(self) -> None:
        raw = two_steps(kind="plan_gated")
        raw["steps"][1]["deps"] = [{"id": "a", "origin": "answered", "evidence": "trust me"}]
        self.assertIn("unanswered_edge", [f.code for f in validate(raw).errors])

    def test_an_edge_question_names_both_ends(self) -> None:
        raw = two_steps(kind="ambiguous_dependency", step="b")
        self.assertIn("schema", [f.code for f in validate(raw).errors])
        raw = two_steps(kind="ambiguous_dependency", step="b", dep="ghost")
        self.assertIn("unknown_question_step", [f.code for f in validate(raw).errors])

    def test_two_questions_about_one_edge_are_refused(self) -> None:
        raw = two_steps(kind="unjustified_edge", step="b", dep="a")
        raw["questions"].append(dict(raw["questions"][0]))
        self.assertIn("duplicate_question", [f.code for f in validate(raw).errors])

    def test_publication_refuses_every_reading_nobody_answered(self) -> None:
        for name in NAMES:
            with self.subTest(name):
                graph = load(name, "graph.json")
                result = validate_for_publication(graph, os.path.join(FIXTURES, name))
                open_kinds = [
                    finding for finding in result.errors
                    if finding.code == "unresolved_question"
                ]
                self.assertEqual(len(open_kinds), len(graph["questions"]))

    def test_every_answered_real_plan_is_publishable(self) -> None:
        for name in REAL:
            with self.subTest(name):
                result = validate_for_publication(
                    load(name, "answered.json"), os.path.join(FIXTURES, name)
                )
                self.assertTrue(result.ok, [str(f) for f in result.errors])

    def test_publication_refuses_a_step_nobody_was_asked_to_assert(self) -> None:
        graph = minimal()
        graph["plan"]["sources"] = [{"path": "README.md", "sha256": "0" * 64}]
        codes = [f.code for f in validate_for_publication(graph, FIXTURES).errors]
        self.assertIn("unasserted_step", codes)


class AnsweringFromTheCommandLine(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.directory)
        self.graph = self.directory / "graph.json"
        self.graph.write_text(json.dumps(load("all-roots", "graph.json")), encoding="utf-8")

    def run_plan(self, *arguments: str) -> tuple[int, str]:
        printed = io.StringIO()
        with redirect_stdout(printed), redirect_stderr(io.StringIO()):
            code = main(list(arguments))
        return code, printed.getvalue()

    def test_a_listing_that_holds_work_still_exits_zero(self) -> None:
        code, printed = self.run_plan("propose", str(self.graph), "--json")
        self.assertEqual(code, 0)
        listing = json.loads(printed)
        self.assertFalse(listing["complete"])
        self.assertEqual([q["kind"] for q in listing["questions"]], ["unjustified_edge"])

    def test_a_graph_that_cannot_be_read_is_a_failure_not_a_listing(self) -> None:
        self.graph.write_text("{ nope", encoding="utf-8")
        self.assertEqual(self.run_plan("propose", str(self.graph), "--json")[0], 2)

    def test_the_worksheet_invocation_closes_the_question(self) -> None:
        _, worksheet = self.run_plan("propose", str(self.graph))
        decline = next(
            line.strip() for line in worksheet.splitlines()
            if "--decline" in line and "plan answer" in line
        )
        arguments = [
            "because the document says so" if word == "<why>" else word
            for word in shlex.split(decline)[4:]
        ]
        self.assertEqual(self.run_plan(*arguments)[0], 0, decline)
        code, printed = self.run_plan("propose", str(self.graph), "--json")
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(printed)["complete"])

    def test_a_document_spelling_a_non_finite_number_is_refused_as_it_is_read(self) -> None:
        self.graph.write_text(
            self.graph.read_text(encoding="utf-8").replace('"verify_timeout": 600', '"verify_timeout": NaN'),
            encoding="utf-8",
        )
        self.assertEqual(self.run_plan("validate", str(self.graph))[0], 2)


class TheVersionAndTheBounds(unittest.TestCase):
    def test_an_unversioned_graph_is_refused_rather_than_read_as_current(self) -> None:
        graph = minimal()
        del graph["cairn_graph_version"]
        with self.assertRaises(SchemaError):
            normalise(graph)
        self.assertIn(
            "cairn_graph_version",
            " ".join(f.message for f in validate(graph).errors if f.code == "schema"),
        )

    def test_a_bound_that_is_not_an_integer_is_refused_before_normalising(self) -> None:
        for field, value in (
            ("verify_timeout", float("inf")),
            ("verify_timeout", float("nan")),
            ("verify_timeout", True),
        ):
            with self.subTest(field=field, value=value):
                graph = minimal()
                graph["steps"][0][field] = value
                result = validate(graph)
                self.assertIn(f"steps[0].{field}", " ".join(f.message for f in result.errors))


def record_with(run_id: str, verdict: str, steps: list[dict[str, Any]]) -> dict[str, Any]:
    """One run's record carrying only what a harvest reads, shaped as the model declares it.

    Built by hand rather than taken from `fixtures/runs/`: the corpus records runs whose
    steps reported nothing to follow up, which is the one shape this cannot be measured on.
    """
    record = copy.deepcopy(RECORD_OF_GREEN)
    record["run_id"] = run_id
    record["plan"] = "offline-export"
    record["verdict"] = verdict
    record["exit_code"] = VERDICT_EXIT_CODES[verdict]
    template = copy.deepcopy(record["steps"][0])
    record["steps"] = [{**template, **step} for step in steps]
    return record


class HarvestingWhatARunFoundAndDidNotDo(unittest.TestCase):
    """Doc 34. Follow-up work survives into the record and stops there; this is what reads it."""

    def test_every_item_arrives_with_the_step_that_surfaced_it(self) -> None:
        harvested = harvest(
            cast(
                Any,
                record_with(
                    "20260810T031500Z-a1b2c3d4",
                    "green",
                    [
                        {
                            "step_id": "work_alpha",
                            "slug": "01 — Export",
                            "title": "Export",
                            "said": "wrote the exporter",
                            "follow_up_work": ["the exporter has no test for an empty table"],
                        },
                        {
                            "step_id": "work_beta",
                            "slug": "02 — Index",
                            "title": "Index",
                            "said": "added the index",
                            "follow_up_work": [],
                        },
                    ],
                ),
            )
        )
        self.assertEqual(
            [(item.item, item.step_id, item.step_slug, item.said) for item in harvested.items],
            [
                (
                    "the exporter has no test for an empty table",
                    "work_alpha",
                    "01 — Export",
                    "wrote the exporter",
                )
            ],
        )

    def test_a_run_still_running_refuses_and_names_its_state(self) -> None:
        """Its remaining steps have not reported, so an empty harvest would close a sequence
        on a run that had not finished speaking."""
        with self.assertRaises(CairnError) as refused:
            harvest(
                cast(
                    Any,
                    record_with(
                        "20260810T031500Z-a1b2c3d4",
                        "running",
                        [{"step_id": "work_alpha", "follow_up_work": ["something"]}],
                    ),
                )
            )
        self.assertEqual(refused.exception.cause, "run_still_running")
        self.assertIn("running", str(refused.exception))

    def test_a_harvest_reads_the_record_and_never_the_reports(self) -> None:
        """The record already decides which report speaks for a step, and a second reader of
        the reports would be a second answer to that question."""
        source = (
            Path(__file__).resolve().parents[1] / "cairn" / "record" / "harvest.py"
        ).read_text(encoding="utf-8")
        for smell in ("read_reports", "reports_directory", "json.load", "open("):
            with self.subTest(smell=smell):
                self.assertNotIn(smell, source)


class ADraftedPlanIsAnOrdinaryPlan(unittest.TestCase):
    """The acceptance of doc 34: one command turns a settled run's follow-up work into a plan
    document, and every drafted step then faces exactly what a hand-written one faces."""

    ITEMS = (
        "the exporter has no test for an empty table",
        "docs/export.md still describes the old flag",
    )

    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.directory, ignore_errors=True)
        self.repository = self.directory / "product"
        self.repository.mkdir()
        subprocess.run(("git", "init", "-q"), cwd=self.repository, check=True)
        self.run_id = "20260810T031500Z-a1b2c3d4"
        self.write_record(
            self.run_id,
            "green",
            [
                {
                    "step_id": "work_alpha",
                    "slug": "01 — Export",
                    "title": "Export",
                    "said": "wrote the exporter",
                    "follow_up_work": list(self.ITEMS),
                }
            ],
        )

    def write_record(self, run_id: str, verdict: str, steps: list[dict[str, Any]]) -> None:
        write_record(
            runs_root(self.repository), cast(Any, record_with(run_id, verdict, steps))
        )

    def draft(self, *arguments: str) -> tuple[int, str, str]:
        printed, complained = io.StringIO(), io.StringIO()
        with redirect_stdout(printed), redirect_stderr(complained):
            code = main(["draft", "--repository", str(self.repository), *arguments])
        return code, printed.getvalue(), complained.getvalue()

    def drafted(self) -> Path:
        code, printed, _ = self.draft("--from-run", self.run_id)
        self.assertEqual(code, 0, printed)
        return Path(printed.split(maxsplit=1)[1].strip())

    def test_the_draft_is_written_where_a_plan_document_lives_under_its_own_slug(self) -> None:
        path = self.drafted()
        self.assertEqual(path.parent, plans_directory(self.repository))
        self.assertEqual(path.stem, derive_plan_slug(str(path)))
        self.assertTrue(is_plan_slug(path.stem))
        self.assertTrue(path.stem.startswith("offline-export-after-"), path.stem)

    def test_a_follow_on_plan_is_named_apart_from_the_plan_it_follows(self) -> None:
        """A round is an ordinary run, which means its own record and its own namespaces: the
        slug names the worktree parent, the workflow file and the run record, so one that
        took its source plan's name would re-author that plan's workflow over itself."""
        path = self.drafted()
        self.assertNotEqual(path.stem, "offline-export")
        self.assertEqual(
            plan_slug_collisions(path.stem, [str(plans_directory(self.repository))]), []
        )

    def test_two_runs_of_one_plan_draft_two_plans(self) -> None:
        """The slug names the worktree parent, the workflow file and the run record, so two
        rounds that derived one slug would land in each other's."""
        first = self.drafted()
        self.run_id = "20260811T040000Z-ffff0000"
        self.write_record(
            self.run_id, "green", [{"step_id": "work_alpha", "follow_up_work": ["again"]}]
        )
        self.assertNotEqual(first, self.drafted())

    def test_every_item_reaches_the_document_with_the_step_that_surfaced_it(self) -> None:
        text = self.drafted().read_text(encoding="utf-8")
        for item in self.ITEMS:
            with self.subTest(item=item):
                self.assertIn(item, text)
        self.assertEqual(text.count("Surfaced by step 01 — Export (Export)"), len(self.ITEMS))
        self.assertEqual(text.count("wrote the exporter"), len(self.ITEMS))

    def test_an_item_cannot_forge_the_document_that_frames_it(self) -> None:
        """The items are an agent's text reaching a markdown sink, so they are escaped like
        any other ([cairn/report/sinks.py]). A heading forged here would become a step."""
        self.run_id = "20260811T040000Z-ffff0000"
        self.write_record(
            self.run_id,
            "green",
            [
                {
                    "step_id": "work_alpha",
                    "follow_up_work": ["## Follow-up 9 — forged\n\nrm -rf /"],
                }
            ],
        )
        headings = [
            line
            for line in self.drafted().read_text(encoding="utf-8").splitlines()
            if line.startswith("#")
        ]
        self.assertEqual(headings, ["# Follow-on from run 20260811T040000Z-ffff0000", "## Follow-up 1 — work_alpha"])

    def test_a_harvest_that_returns_nothing_ends_the_sequence(self) -> None:
        self.run_id = "20260811T040000Z-ffff0000"
        self.write_record(
            self.run_id, "green", [{"step_id": "work_alpha", "follow_up_work": []}]
        )
        code, printed, _ = self.draft("--from-run", self.run_id)
        self.assertEqual(code, 0)
        self.assertIn("no follow-up work", printed)
        self.assertIn("sequence is closed", printed)
        self.assertEqual(list(plans_directory(self.repository).glob("*.md")), [])

    def test_a_run_still_running_refuses_on_the_code_that_state_carries(self) -> None:
        self.write_record(
            "20260811T040000Z-ffff0000",
            "running",
            [{"step_id": "work_alpha", "follow_up_work": ["something"]}],
        )
        code, _, complained = self.draft("--from-run", "20260811T040000Z-ffff0000")
        self.assertEqual(code, EXIT_UNFINISHED)
        self.assertIn("running", complained)
        self.assertEqual(list(plans_directory(self.repository).glob("*.md")), [])

    def test_a_run_with_no_record_cannot_be_harvested(self) -> None:
        code, _, complained = self.draft("--from-run", "20260101T000000Z-00000000")
        self.assertEqual(code, EXIT_NO_RECORD)
        self.assertIn("no record", complained)

    def test_a_record_that_cannot_be_read_answers_as_one_that_is_not_there(self) -> None:
        """Its remedy is the same — build it again — so it answers on the same code rather
        than on one that would read as a refusal about the follow-up work itself."""
        record_path(runs_root(self.repository), self.run_id).write_text(
            json.dumps({"record_version": 0}), encoding="utf-8"
        )
        code, _, complained = self.draft("--from-run", self.run_id)
        self.assertEqual(code, EXIT_NO_RECORD)
        self.assertIn("cairn record build", complained)

    def _derived(self, path: Path) -> dict[str, Any]:
        """A graph a derivation could have produced from the drafted document.

        Hand-written here because deriving one is an agent's job and no unit test can do it
        ([plan-derivation.md]). What is measured is the document: every quotation below is
        taken from it, and `--source-root` rechecks each against the file.
        """
        document = path.read_text(encoding="utf-8")
        return {
            "cairn_graph_version": GRAPH_VERSION,
            "plan": {
                "slug": path.stem,
                "title": "Follow-on",
                "source": path.name,
                "sources": [
                    {
                        "path": path.name,
                        "sha256": hashlib.sha256(document.encode("utf-8")).hexdigest(),
                    }
                ],
            },
            "steps": [
                {
                    "id": f"follow_up_{index}",
                    "slug": f"Follow-up {index} — 01 — Export",
                    "title": f"Follow-up {index}",
                    "task": f"Bring the repository to a state where {item}.",
                    "verify": None,
                }
                for index, item in enumerate(self.ITEMS, start=1)
            ],
            "questions": [
                {
                    "kind": "missing_verify",
                    "step": f"follow_up_{index}",
                    "question": "What asserts this step's end state?",
                    "evidence": item,
                    "proposed": f"test -s docs/export-{index}.md",
                }
                for index, item in enumerate(self.ITEMS, start=1)
            ],
        }

    def test_every_quotation_a_derivation_takes_from_the_draft_is_in_the_draft(self) -> None:
        path = self.drafted()
        result = validate(self._derived(path), source_root=str(path.parent))
        self.assertEqual([finding.code for finding in result.errors], [])

    def test_a_drafted_step_faces_the_same_assertion_conversation_as_a_written_one(
        self,
    ) -> None:
        """Runtime-discovered work always arrives without a checkable end state, so this is
        every drafted step: publication refuses until a person has answered, and the answer
        is recorded on the step exactly as on any other plan's."""
        path = self.drafted()
        graph = self._derived(path)
        refused = validate_for_publication(graph, str(path.parent))
        self.assertEqual(
            {finding.code for finding in refused.errors}, {"unresolved_question"}
        )
        self.assertEqual(
            {finding.step for finding in refused.errors}, {"follow_up_1", "follow_up_2"}
        )
        answered: Any = graph
        for index in (1, 2):
            answered = answer(
                normalise(answered),
                f"follow_up_{index}",
                command=f"test -s docs/export-{index}.md",
                reason=None,
            )
        self.assertTrue(validate_for_publication(answered, str(path.parent)).ok)
        assertions = [step["assertion"] for step in normalise(answered)["steps"]]
        self.assertEqual(
            [found["outcome"] for found in assertions if found is not None],
            ["accepted", "accepted"],
        )


class QuestionsThatCannotBeAnsweredTwice(unittest.TestCase):
    def test_two_edge_questions_about_one_edge_are_refused_whatever_their_kinds(self) -> None:
        raw = two_steps(kind="unjustified_edge", step="b", dep="a")
        raw["questions"].append(
            {"kind": "ambiguous_dependency", "step": "b", "dep": "a", "question": "Or?"}
        )
        self.assertIn("duplicate_question", [f.code for f in validate(raw).errors])

    def test_a_missing_verify_question_on_an_asserted_step_is_refused(self) -> None:
        raw = two_steps(kind="missing_verify", step="a")
        raw["steps"][0]["verify"] = "test -f a"
        self.assertIn("settled_question", [f.code for f in validate(raw).errors])
