import ast
import copy
import hashlib
import io
import json
import os
import re
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

from cairn.plan.cli import main
from cairn.plan.ids import (
    assign_ids,
    derive_plan_slug,
    is_engine_id,
    plan_slug_collisions,
    sanitise_id,
)
from cairn.plan.report import render, waves
from cairn.plan.schema import SchemaError, normalise
from cairn.plan.validate import Finding, validate

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(os.path.dirname(HERE), "fixtures", "plans")
NAMES = sorted(os.listdir(FIXTURES))
REAL = ("worktree-hydration", "pattern-lifecycle")


def load(name: str, filename: str) -> Any:
    with open(os.path.join(FIXTURES, name, filename), encoding="utf-8") as handle:
        return json.load(handle)


def minimal(**plan: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
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

    def test_a_non_positive_timeout_and_a_negative_retry_count_are_refused(self) -> None:
        graph = minimal()
        graph["steps"][0]["timeout"] = 0
        graph["steps"][0]["retries"] = -1
        codes = [f.code for f in validate(graph).errors]
        self.assertIn("timeout", codes)
        self.assertIn("retries", codes)

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
        self.assertEqual(step["timeout"], 3600)

    def test_retry_and_timeout_defaults_follow_the_kind(self) -> None:
        graph = minimal()
        graph["steps"][0]["kind"] = "command"
        graph["steps"][0]["command"] = "make build"
        graph["steps"][0]["command_type"] = "exec"
        step = normalise(graph)["steps"][0]
        self.assertEqual(step["timeout"], 600)
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
        self.assertEqual(graph["steps"][0]["timeout"], 600)

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

    def test_any_agent_provider_gets_the_agent_timeout_not_the_command_one(self) -> None:
        """Adding a provider must not silently cut its steps to the command bound."""
        graph = minimal(default_kind="agent.codex")
        self.assertEqual(normalise(graph)["steps"][0]["timeout"], 3600)
        self.assertTrue(validate(graph).ok)

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

    def test_an_agent_step_resolves_its_own_ceiling_and_model(self) -> None:
        """A plan that says nothing is still bounded: the session an agent step opens
        cannot be priced without a ceiling, or attributed without a model."""
        step = normalise(minimal())["steps"][0]
        self.assertEqual(step["max_budget_usd"], 5.0)
        self.assertEqual(step["model"], "sonnet")

    def test_a_command_step_carries_no_session_bounds(self) -> None:
        raw = minimal(default_kind="command")
        raw["steps"][0]["command"] = "printf done"
        raw["steps"][0]["command_type"] = "exec"
        step = normalise(raw)["steps"][0]
        self.assertIsNone(step["max_budget_usd"])
        self.assertIsNone(step["model"])
        for bound, value in (("max_budget_usd", 2.0), ("model", "sonnet")):
            with self.subTest(bound):
                declared = copy.deepcopy(raw)
                declared["steps"][0][bound] = value
                with self.assertRaisesRegex(SchemaError, "command kind must not carry"):
                    normalise(declared)

    def test_a_whole_dollar_ceiling_is_a_float_not_a_type_error(self) -> None:
        raw = minimal()
        raw["steps"][0]["max_budget_usd"] = 8
        self.assertEqual(normalise(raw)["steps"][0]["max_budget_usd"], 8.0)
        raw["steps"][0]["max_budget_usd"] = True
        with self.assertRaisesRegex(SchemaError, "found bool"):
            normalise(raw)

    def test_an_unpriceable_agent_step_is_a_validation_error(self) -> None:
        graph = minimal()
        graph["steps"][0]["max_budget_usd"] = 0
        self.assertIn("budget", [f.code for f in validate(graph).errors])
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
        tools = reads = timeouts = ceilings = models = 0
        for name in NAMES:
            for step in load(name, "graph.json")["steps"]:
                kinds.add(step["kind"])
                scopes.add(step["scope"])
                tools += bool(step["tools"])
                reads += bool(step["reads"])
                timeouts += step["timeout"] not in (600, 3600)
                ceilings += step.get("max_budget_usd") not in (None, 5.0)
                models += step.get("model") not in (None, "sonnet")
        self.assertEqual(kinds, {"agent.claude", "command"})
        self.assertTrue({"once", "run", "weekly", "inputs"} <= scopes)
        self.assertTrue(tools and reads and timeouts and ceilings and models)


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


if __name__ == "__main__":
    unittest.main()
