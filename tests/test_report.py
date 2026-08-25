"""The report: one spine, three renderings, and the oracle that keeps them saying one thing.

Organised by what it proves rather than by which module it touches, because doc 14's exit
criteria are claims about the three renderings together — that they share one order, that
none of them computes a fact or decides a verdict, that an exclusion is unmissable in every
one of them, and that all three agree with the canonical-facts projection.

The corpus loaders come from the run-record suite rather than being written again here: two
readers of one fixture set drift, which is the failure this whole document exists to prevent.
"""

from __future__ import annotations

import ast
import itertools
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, ClassVar, cast
from unittest.mock import patch
from xml.etree import ElementTree

from cairn.__main__ import main as cairn_main
from cairn.record.extract import extract
from cairn.record.facts import ABSENT, NONE, as_mapping, canonical_facts
from cairn.record.model import RunRecord
from cairn.record.vocabulary import (
    EXIT_NO_RECORD,
    NEXT_ACTIONS,
    VERDICT_EXIT_CODES,
    VERDICT_PRECEDENCE,
)
from cairn.report import html, markdown, phrases, terminal
from cairn.report.compose import document
from cairn.report.sinks import Rendering, for_markdown_code, raw
from cairn.report.spine import (
    BLOCK_KINDS,
    RULES,
    SECTION_ATTENTION,
    SECTION_NEXT,
    SECTION_ORDER,
    SECTION_RECEIPTS,
    SECTION_SHAPE,
    SECTION_STEPS,
    SECTION_VERDICT,
    SECTIONS,
    SINKS,
)

from tests.test_run_record import PACKAGE_ROOT, SHAPES, alive_copy, load, record_of

DOCUMENT = PACKAGE_ROOT / "docs" / "report.md"

RENDERERS = {
    "terminal": terminal.render,
    "markdown": markdown.render,
    "html": html.render,
}

# A rendering escapes for its own sink, so a fact's text is not a substring of the document
# byte for byte. These are the characters an escape may add or replace; removing them from
# both sides is what lets one containment check serve three sinks without any of them being
# let off. It is deliberately not an un-escape: markdown has no inverse and HTML's is
# ambiguous, and a test that guessed at one would be asserting its own guess.
ENTITY = re.compile(r"&#?\w+;")
ESCAPABLE = re.compile(r"[\\&;<>|\[\]`'\"\s]+")

# Keys no rendering states as text, each for a stated reason. Every other fact that has a
# value reaches every rendering — there is no per-sink escape hatch, because all three render
# the same blocks.
UNSTATED = {
    # The group heading each item sits under *is* this value, phrased.
    "kind": "the attention section's own headings phrase it",
    # Conditions rather than statements: they decide which sentences the verdict section
    # carries, and those sentences say more than the flag would.
    "run.engine_contradicted": "it decides the contradiction sentence, which says more",
    "run.owner_alive": "it decides the crash sentence, which says more",
}


def poke(shape: str, **fields: Any) -> RunRecord:
    """One recorded run with fields set by hand, which is what a renderer really reads.

    A renderer's input is a file on disk. A record someone edited, or one an older extraction
    wrote, has been through no normalisation at all — so a defence that only holds for values
    the extraction produced is not a defence.
    """
    record = record_of(shape)
    record.update(cast(Any, fields))
    return record


def rendered(record: RunRecord, sink: str) -> Rendering:
    return RENDERERS[sink](document(record), as_mapping(record))


def loose(text: str) -> str:
    """Both sides stripped of what an escape may add, so one check serves three sinks.

    Not an un-escape: markdown has no inverse and HTML's is ambiguous, and a test that
    guessed at one would be asserting its own guess.
    """
    return ESCAPABLE.sub(" ", ENTITY.sub(" ", text))


class TheSpineIsFrozen(unittest.TestCase):
    """Task 1: the order is structural, so a new renderer cannot invent one."""

    def test_the_six_questions_come_in_the_one_order(self) -> None:
        self.assertEqual(
            SECTION_ORDER,
            (
                SECTION_VERDICT,
                SECTION_NEXT,
                SECTION_ATTENTION,
                SECTION_STEPS,
                SECTION_SHAPE,
                SECTION_RECEIPTS,
            ),
        )

    def test_the_first_question_is_did_it_work_and_the_topology_is_the_fifth(self) -> None:
        """The order is the design: a reader's first question is never 'what was the shape'."""
        self.assertEqual(SECTIONS[0].question, "did it work")
        self.assertEqual(SECTIONS[4].question, "what shape was the run")

    def test_every_section_says_something_when_it_has_nothing(self) -> None:
        for section in SECTIONS:
            with self.subTest(section=section.key):
                self.assertTrue(section.nothing.endswith("."))

    def test_the_document_states_the_whole_spine(self) -> None:
        text = DOCUMENT.read_text(encoding="utf-8")
        for section in SECTIONS:
            with self.subTest(section=section.key):
                self.assertIn(f"`{section.key}`", text)
                self.assertIn(section.question, text)
        for group in (BLOCK_KINDS, RULES, SINKS):
            for value in group:
                with self.subTest(value=value):
                    self.assertIn(f"`{value}`", text)

    def test_the_document_states_the_sections_in_the_order_the_code_holds(self) -> None:
        """Containment is not enough when the whole claim is the order."""
        text = DOCUMENT.read_text(encoding="utf-8")
        found = [
            key
            for key in re.findall(r"`([a-z_]+)`", text)
            if key in SECTION_ORDER
        ]
        first: list[str] = []
        for key in found:
            if key not in first:
                first.append(key)
        self.assertEqual(tuple(first), SECTION_ORDER)

    def test_the_report_mints_its_vocabulary_in_one_module_only(self) -> None:
        pattern = re.compile(
            r"^(SECTION|BLOCK|RULE|SINK|TONE)_[A-Z0-9_]+ = ", re.MULTILINE
        )
        holders = {
            path.relative_to(PACKAGE_ROOT).as_posix()
            for path in (PACKAGE_ROOT / "cairn").rglob("*.py")
            if pattern.search(path.read_text(encoding="utf-8"))
        }
        self.assertEqual(holders, {"cairn/report/spine.py"})

    def test_every_phrase_map_is_total_over_the_vocabulary_it_is_keyed_on(self) -> None:
        """A word the record can hold and no map can phrase raises rather than defaulting."""
        for mapping, vocabulary in phrases.TOTAL_MAPS:
            with self.subTest(vocabulary=vocabulary[0]):
                self.assertEqual(set(mapping), set(vocabulary))


class NoRendererComputesAFactOrDecidesAVerdict(unittest.TestCase):
    """The rule, and exit criterion 5, as a property of what a renderer was handed."""

    def _imports(self, module: str) -> set[str]:
        tree = ast.parse((PACKAGE_ROOT / "cairn" / "report" / module).read_text("utf-8"))
        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module)
            elif isinstance(node, ast.Import):
                found.update(alias.name for alias in node.names)
        return found

    def test_no_renderer_imports_the_record_at_all(self) -> None:
        """A function that was never given the record cannot compute a fact from it."""
        for module in ("terminal.py", "markdown.py", "html.py", "sinks.py"):
            with self.subTest(module=module):
                reaching = {
                    name
                    for name in self._imports(module)
                    if name.startswith("cairn.record")
                }
                self.assertEqual(reaching, set())

    def test_only_one_module_reads_the_record(self) -> None:
        readers = {
            path.name
            for path in (PACKAGE_ROOT / "cairn" / "report").glob("*.py")
            if "cairn.record.model" in path.read_text(encoding="utf-8")
        }
        self.assertEqual(readers, {"compose.py", "graph.py"})

    def test_no_renderer_spells_a_verdict_of_its_own(self) -> None:
        for module in ("terminal.py", "markdown.py", "html.py"):
            source = (PACKAGE_ROOT / "cairn" / "report" / module).read_text("utf-8")
            for verdict in VERDICT_PRECEDENCE:
                with self.subTest(module=module, verdict=verdict):
                    self.assertNotIn(f'"{verdict}"', source)

    def test_a_rendering_repeats_the_verdict_the_record_carries(self) -> None:
        """Hand it a wrong verdict and every rendering says the wrong thing, loyally."""
        record = poke("green-with-exclusions", verdict="green", exit_code=0)
        for sink in SINKS:
            with self.subTest(sink=sink):
                self.assertIn("This run worked.", loose(rendered(record, sink).text))

    def test_every_digit_a_rendering_prints_came_from_a_fact_or_from_its_own_chrome(
        self,
    ) -> None:
        """The mechanical form of 'no renderer computes a fact'.

        A count a renderer worked out for itself — "3 steps skipped" — appears here as a
        digit run belonging to no stated value, which is why every such count is a key in the
        projection instead.
        """
        for shape in SHAPES:
            record = record_of(shape)
            for sink in ("terminal", "markdown"):
                result = rendered(record, sink)
                allowed = " ".join(item.shown for item in result.stated)
                body = result.text
                for section in SECTIONS:
                    body = body.replace(section.heading, " ").replace(
                        section.heading.upper(), " "
                    )
                # A figure carries its own numbering — the layer ordinals are the drawing's,
                # like the SVG's coordinates, and the diagram is checked structurally rather
                # than against the projection.
                body = re.sub(r"^\s*\d+\. ", " ", body, flags=re.MULTILINE)
                for run in set(re.findall(r"\d+", body)):
                    with self.subTest(shape=shape, sink=sink, run=run):
                        self.assertIn(run, allowed)


class EveryRenderingAgreesWithTheProjection(unittest.TestCase):
    """Task 11 and exit criterion 1, as three assertions over the scribe's own log."""

    def test_every_fact_a_rendering_states_is_the_fact_the_projection_holds(self) -> None:
        """Fidelity: the text shown is what the declared rule makes of the projected value."""
        for shape in SHAPES:
            record = record_of(shape)
            facts = as_mapping(record)
            for sink in SINKS:
                for item in rendered(record, sink).stated:
                    with self.subTest(shape=shape, sink=sink, keys=item.keys):
                        self.assertEqual(
                            item.shown,
                            phrases.apply(
                                item.rule, tuple(facts[key] for key in item.keys)
                            ),
                        )

    def test_every_fact_a_rendering_logs_actually_appears_in_it(self) -> None:
        """Realisation: without this the log would be a story the rendering tells itself."""
        for shape in SHAPES:
            record = record_of(shape)
            for sink in SINKS:
                result = rendered(record, sink)
                body = loose(result.text)
                for item in result.stated:
                    with self.subTest(shape=shape, sink=sink, keys=item.keys):
                        self.assertIn(loose(item.shown), body)

    def test_every_fact_that_has_a_value_reaches_every_rendering(self) -> None:
        """Coverage: nothing the record established is silently dropped by a surface.

        A fact whose value is an absence, nothing, or zero may go unstated — those are the
        nothings, and a report of them is a report of nothing. Everything else is either
        stated or in `UNSTATED` with its reason.
        """
        for shape in SHAPES:
            record = record_of(shape)
            for sink in SINKS:
                stated = {key for item in rendered(record, sink).stated for key in item.keys}
                for key, value in canonical_facts(record):
                    if value in (ABSENT, NONE, "0"):
                        continue
                    if key in UNSTATED or key.rsplit(".", 1)[-1] in UNSTATED:
                        continue
                    with self.subTest(shape=shape, sink=sink, key=key):
                        self.assertIn(key, stated)

    def test_the_three_renderings_never_disagree_about_a_fact(self) -> None:
        """Doc 14's rule: a disagreement is a defect in one of them, caught against the model."""
        for shape in SHAPES:
            record = record_of(shape)
            said: dict[str, dict[tuple[str, ...], str]] = {}
            for sink in SINKS:
                said[sink] = {
                    item.keys: item.shown for item in rendered(record, sink).stated
                }
            first = said[SINKS[0]]
            for sink in SINKS[1:]:
                with self.subTest(shape=shape, sink=sink):
                    self.assertEqual(said[sink], first)

    def test_every_fact_answers_the_same_label_in_every_rendering(self) -> None:
        """A value against the wrong label states a fact and answers a different question.

        A rendering that shifted every receipt one row — the cost against the turns, the
        session against the model — states every fact correctly and is wrong about all of
        them. Only the binding catches that, so the binding is what the log carries.
        """
        for shape in SHAPES:
            record = record_of(shape)
            bindings = [
                [(item.section, item.label, item.keys) for item in rendered(record, sink).stated]
                for sink in SINKS
            ]
            for sink, found in zip(SINKS[1:], bindings[1:], strict=True):
                with self.subTest(shape=shape, sink=sink):
                    self.assertEqual(found, bindings[0])

    def test_a_rendering_that_shifts_its_values_against_its_labels_is_caught(self) -> None:
        """The control: the binding assertion has to be able to fail."""
        record = record_of("agent")
        stated = list(rendered(record, "terminal").stated)
        shifted = [
            item._replace(label=other.label)
            for item, other in itertools.pairwise(stated)
        ]
        self.assertNotEqual(
            [(item.section, item.label, item.keys) for item in shifted],
            [(item.section, item.label, item.keys) for item in stated[: len(shifted)]],
        )

    def test_a_rendering_that_states_a_wrong_value_is_caught(self) -> None:
        """The control: an oracle that cannot fail records nothing."""
        record = record_of("red")
        facts = dict(as_mapping(record))
        facts["run.verdict"] = "green"
        wrong = terminal.render(document(record), facts)
        item = next(entry for entry in wrong.stated if entry.keys == ("run.verdict",))
        self.assertNotEqual(
            item.shown,
            phrases.apply(item.rule, (as_mapping(record)["run.verdict"],)),
        )

    def test_an_absent_fact_is_never_rendered_as_a_zero(self) -> None:
        for shape in SHAPES:
            record = record_of(shape)
            facts = as_mapping(record)
            for sink in SINKS:
                for item in rendered(record, sink).stated:
                    if any(facts[key] != ABSENT for key in item.keys):
                        continue
                    with self.subTest(shape=shape, sink=sink, keys=item.keys):
                        self.assertNotIn(
                            item.shown, {"0", "0.0", "0.00", "$0", "$0.00", "", "-"}
                        )
                        self.assertEqual(item.shown, phrases.NOT_RECORDED)


class EveryRenderingWalksTheSameSpine(unittest.TestCase):
    """A renderer is handed a sequence, so it has nothing to order."""

    def _keys_in_order(self, text: str, sink: str) -> list[str]:
        if sink == "markdown":
            return re.findall(r"<!-- cairn:section:([a-z_]+) -->", text)
        if sink == "html":
            return re.findall(r'<section id="cairn-([a-z_]+)">', text)
        headings = {section.heading.upper(): section.key for section in SECTIONS}
        return [
            headings[found]
            for found in re.findall(r"^== (.+) ==$", text, re.MULTILINE)
        ]

    def test_every_rendering_of_every_run_carries_all_six_in_order(self) -> None:
        for shape in SHAPES:
            record = record_of(shape)
            for sink in SINKS:
                with self.subTest(shape=shape, sink=sink):
                    self.assertEqual(
                        self._keys_in_order(rendered(record, sink).text, sink),
                        list(SECTION_ORDER),
                    )

    def test_a_run_with_nothing_in_a_section_still_carries_the_section(self) -> None:
        record = record_of("green")
        for sink in SINKS:
            with self.subTest(sink=sink):
                self.assertIn(
                    "Nothing needs your attention", loose(rendered(record, sink).text)
                )


class AnExclusionIsUnmissable(unittest.TestCase):
    """Task 5 and exit criterion 2: I5's whole point, on the first screen of all three."""

    def _first_section(self, record: RunRecord, sink: str) -> str:
        text = rendered(record, sink).text
        if sink == "markdown":
            return text.split("<!-- cairn:section:next -->")[0]
        if sink == "html":
            return text.split('<section id="cairn-next">')[0]
        return text.split("== WHAT TO DO NEXT ==")[0]

    def test_the_first_screen_contradicts_the_engines_own_clean_success(self) -> None:
        """The fixture where the engine reports `Succeeded` with exit 0 over a dropped step."""
        record = record_of("green-with-exclusions")
        self.assertEqual(record["engine_run_status_name"], "succeeded")
        for sink in SINKS:
            opening = loose(self._first_section(record, sink))
            with self.subTest(sink=sink):
                self.assertIn("not a clean success", opening)
                self.assertIn("succeeded", opening)
                self.assertIn("and Cairn does not", opening)
                self.assertIn("green with exclusions".replace(" ", "_"), opening)

    def test_a_clean_run_opens_differently_from_one_with_exclusions(self) -> None:
        clean = record_of("green")
        excluded = record_of("green-with-exclusions")
        for sink in SINKS:
            with self.subTest(sink=sink):
                opening = loose(self._first_section(clean, sink))
                self.assertIn("This run worked.", opening)
                self.assertNotIn("not a clean success", opening)
                self.assertNotEqual(
                    self._first_section(clean, sink),
                    self._first_section(excluded, sink),
                )

    def test_a_census_exclusion_is_on_the_first_screen_too(self) -> None:
        """The run whose steps all verified and whose join still declined a branch."""
        state, reports, run_id = load("green")
        reports["join_w1"] = {
            "run_id": run_id,
            "status": "done",
            "detail": {
                "wave": 1,
                "into": "main",
                "arrived": ["step/alpha"],
                "excluded": {
                    "step/beta": {"cause": "gate_indeterminate", "summary": "unreadable"}
                },
                "settled": [],
            },
        }
        record = extract(state, reports, run_id=run_id)
        for sink in SINKS:
            opening = loose(self._first_section(record, sink))
            with self.subTest(sink=sink):
                self.assertIn("not a clean success", opening)
                self.assertIn("step/beta", opening)

    def test_a_blocked_run_opens_on_the_block(self) -> None:
        record = record_of("blocked")
        for sink in SINKS:
            with self.subTest(sink=sink):
                self.assertIn(
                    "blocked on a decision", loose(self._first_section(record, sink))
                )

    def test_the_first_screen_of_a_terminal_rendering_fits_one(self) -> None:
        for shape in SHAPES:
            with self.subTest(shape=shape):
                lines = self._first_section(record_of(shape), "terminal").splitlines()
                self.assertLessEqual(len(lines), 24)


class ANoOpRunReadsAsANoOp(unittest.TestCase):
    """Task 6: a screen of grey with no account of who did the work is useless."""

    def test_the_count_and_the_earlier_run_are_on_the_first_screen(self) -> None:
        record = record_of("all-no-op")
        for sink in SINKS:
            with self.subTest(sink=sink):
                text = loose(rendered(record, sink).text)
                self.assertIn("2 steps skipped: already complete", text)
                self.assertIn("fixture-earlierrun", text)

    def test_each_no_op_names_the_occasion_its_key_matched(self) -> None:
        """`once` and `daily` is the difference between correct caching and stale research."""
        record = record_of("all-no-op")
        for sink in SINKS:
            text = loose(rendered(record, sink).text)
            for step in record["steps"]:
                with self.subTest(sink=sink, step=step["step_id"]):
                    freshness = step["freshness"]
                    assert freshness is not None
                    self.assertIn(freshness["recorded_scope"], text)
                    self.assertIn(freshness["recorded_key"], text)

    def test_a_run_with_no_no_ops_states_no_skip_count(self) -> None:
        for sink in SINKS:
            with self.subTest(sink=sink):
                self.assertNotIn(
                    "steps skipped", loose(rendered(record_of("green"), sink).text)
                )


class TheReceiptsArePasteable(unittest.TestCase):
    """Task 7 and exit criterion 3."""

    def _recovered(self, record: RunRecord, sink: str, command: str) -> str:
        text = rendered(record, sink).text
        for line in text.splitlines():
            stripped = line.strip().strip("`")
            if stripped.startswith("cd ") and "--resume" in stripped:
                return stripped
        raise AssertionError(f"{sink} lost the resume command for {command}")

    def test_the_resume_command_survives_every_sink_as_the_same_argument_list(self) -> None:
        record = record_of("agent")
        expected = record["steps"][0]["resume_command"]
        assert expected is not None
        for sink in ("terminal", "markdown"):
            with self.subTest(sink=sink):
                self.assertEqual(
                    shlex.split(self._recovered(record, sink, expected)),
                    shlex.split(expected),
                )

    def test_the_resume_command_runs_when_it_is_pasted(self) -> None:
        """The exit criterion itself, with a stub provider so nothing is spent."""
        record = record_of("agent")
        command = self._recovered(record, "terminal", "")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "somewhere"
            home.mkdir()
            stub = root / "bin"
            stub.mkdir()
            (stub / "claude").write_text(
                '#!/bin/sh\necho "$@" > "$STUB_OUT"\npwd >> "$STUB_OUT"\n',
                encoding="utf-8",
            )
            (stub / "claude").chmod(0o755)
            recorded = root / "argv"
            # The recorded working directory is gone by now — a pruned worktree, which is
            # what a green run leaves. The command is still the right command, so it is
            # pointed at a directory that exists and its argv is what is under test.
            pasted = re.sub(r"^cd [^&]+", f"cd {shlex.quote(str(home))} ", command)
            completed = subprocess.run(
                ["/bin/sh", "-c", pasted],
                env={**os.environ, "PATH": f"{stub}:{os.environ['PATH']}",
                     "STUB_OUT": str(recorded)},
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            written = recorded.read_text(encoding="utf-8").splitlines()
            session = record["steps"][0]["session_id"]
            self.assertEqual(written[0], f"--resume {session}")
            self.assertEqual(Path(written[1]).resolve(), home.resolve())

    def test_a_priced_step_carries_its_whole_receipt(self) -> None:
        record = record_of("agent")
        step = record["steps"][0]
        for sink in SINKS:
            text = loose(rendered(record, sink).text)
            for field in ("session_id", "transcript", "stderr_log", "commit"):
                with self.subTest(sink=sink, field=field):
                    value = step[cast(Any, field)]
                    assert value is not None
                    self.assertIn(loose(str(value)), text)

    def test_a_notional_cost_never_reads_as_money_spent(self) -> None:
        record = record_of("agent")
        for sink in SINKS:
            with self.subTest(sink=sink):
                text = loose(rendered(record, sink).text)
                self.assertIn("an API-equivalent price, not money spent", text)

    def test_an_unpriced_run_states_no_cost_rather_than_a_zero(self) -> None:
        for sink in SINKS:
            with self.subTest(sink=sink):
                text = rendered(record_of("green"), sink).text
                self.assertNotIn("$0", text)

    def test_the_engines_own_view_of_the_run_is_rendered(self) -> None:
        record = record_of("green")
        link = record["view_url"]
        assert link is not None
        for sink in SINKS:
            with self.subTest(sink=sink):
                self.assertIn(loose(link), loose(rendered(record, sink).text))

    def test_the_engines_view_is_a_link_only_in_the_page_that_can_follow_one(self) -> None:
        record = record_of("green")
        self.assertIn('<a href="http://127.0.0.1:8080/dag-runs/', rendered(record, "html").text)

    def test_a_view_of_a_scheme_a_document_may_not_follow_is_never_a_link(self) -> None:
        record = poke("green", view_url="javascript:alert(1)")
        page = rendered(record, "html").text
        self.assertNotIn('href="javascript', page)
        self.assertIn("javascript:alert(1)", page)


class TheDivergencesStandSideBySide(unittest.TestCase):
    """Task 8: neither account presented as the truth."""

    def _diverging(self) -> RunRecord:
        record = record_of("green")
        step = record["steps"][0]
        step["overlays"] = ["divergence"]
        step["divergence"] = {"reported": "done", "asserted": False}
        return record

    def test_both_accounts_reach_every_rendering(self) -> None:
        record = self._diverging()
        for sink in SINKS:
            with self.subTest(sink=sink):
                text = loose(rendered(record, sink).text)
                self.assertIn(loose("the step's own account"), text)
                self.assertIn(loose("what verification found"), text)

    def test_neither_side_is_labelled_the_truth(self) -> None:
        record = self._diverging()
        for sink in SINKS:
            with self.subTest(sink=sink):
                text = loose(rendered(record, sink).text).lower()
                self.assertIn("two accounts that do not agree", text)
                self.assertNotIn("actually", text)
                self.assertNotIn("really", text)

    def test_a_step_with_no_divergence_renders_no_empty_comparison(self) -> None:
        for sink in SINKS:
            with self.subTest(sink=sink):
                self.assertNotIn(
                    "two accounts that do not agree",
                    loose(rendered(record_of("green"), sink).text),
                )


class TheNextActionIsRendered(unittest.TestCase):
    """Task 9: derived from the record, never composed as prose."""

    def test_every_action_the_vocabulary_holds_has_a_sentence(self) -> None:
        self.assertEqual(set(phrases.SENTENCE_BY_ACTION), set(NEXT_ACTIONS))

    def test_the_action_and_its_sentence_reach_every_rendering(self) -> None:
        for shape in SHAPES:
            record = record_of(shape)
            action = record["next_action"]["action"]
            for sink in SINKS:
                with self.subTest(shape=shape, sink=sink):
                    text = loose(rendered(record, sink).text)
                    self.assertIn(loose(phrases.SENTENCE_BY_ACTION[action]), text)
                    self.assertIn(action, text)

    def test_the_command_is_rendered_where_the_record_carries_one(self) -> None:
        record = record_of("red")
        command = record["next_action"]["command"]
        assert command is not None
        for sink in ("terminal", "markdown"):
            with self.subTest(sink=sink):
                self.assertIn(loose(command), loose(rendered(record, sink).text))

    def test_no_rendering_invents_a_command_where_the_record_has_none(self) -> None:
        record = record_of("blocked")
        self.assertIsNone(record["next_action"]["command"])
        for sink in SINKS:
            with self.subTest(sink=sink):
                self.assertNotIn("dagu retry", rendered(record, sink).text)


class UntrustedTextIsEscapedAtEachSink(unittest.TestCase):
    """Task 10: one normalisation, then a different escape at every final sink."""

    PAYLOAD = (
        "</text><script>alert(1)</script> | b [x](javascript:alert(1)) ]]> &amp; "
        '"q" \x1b[31mRED\x07 \u202e #heading `code`'
    )

    def _poisoned(self) -> RunRecord:
        record = record_of("green")
        record["steps"][0]["said"] = self.PAYLOAD
        record["nodes"][0]["name"] = self.PAYLOAD
        record["attention"] = [
            {
                "kind": "follow_up",
                "subject": self.PAYLOAD,
                "summary": self.PAYLOAD,
                "cause": None,
            }
        ]
        return record

    def test_a_terminal_rendering_carries_no_control_sequence_at_all(self) -> None:
        text = rendered(self._poisoned(), "terminal").text
        self.assertNotIn("\x1b", text)
        self.assertNotIn("\x07", text)
        self.assertNotIn("\u202e", text)
        self.assertIsNone(re.search(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]", text))

    def test_no_rendering_of_any_fixture_carries_a_control_sequence(self) -> None:
        for shape in SHAPES:
            for sink in SINKS:
                with self.subTest(shape=shape, sink=sink):
                    self.assertIsNone(
                        re.search(
                            r"[\x00-\x08\x0b-\x1f\x7f-\x9f]",
                            rendered(record_of(shape), sink).text,
                        )
                    )

    def test_a_markdown_table_keeps_its_shape_and_its_links_inert(self) -> None:
        text = rendered(self._poisoned(), "markdown").text
        rows = [line for line in text.splitlines() if line.startswith("|")]
        for row in rows:
            with self.subTest(row=row[:40]):
                self.assertIn(row.count("|") - row.count("\\|"), (3, 4, 6))
        self.assertNotIn("<script>", text)
        self.assertIsNone(re.search(r"(?<!\\)\]\(", text))

    def test_a_verbatim_span_cannot_be_broken_out_of_by_its_own_backticks(self) -> None:
        """The fence outruns any run inside, so the text stays literal however it is written.

        This is what makes leaving a verbatim span unescaped safe: link syntax inside a code
        span is inert, and nothing in the span can end it early.
        """
        for payload in ("`", "``", "a ``` b", "[x](javascript:alert(1))", "`` `` ``"):
            with self.subTest(payload=payload):
                span = str(for_markdown_code(raw(payload)))
                fence = re.match(r"`+", span)
                assert fence is not None
                inside = span[len(fence.group(0)) : -len(fence.group(0))]
                self.assertNotIn(fence.group(0), inside)
                self.assertIn(payload.strip("`").strip(), inside)

    def test_the_html_shows_the_payload_as_text_and_never_as_markup(self) -> None:
        page = rendered(self._poisoned(), "html").text
        self.assertNotIn("<script>", page)
        section = re.search(r"<section id=\"cairn-steps\">.*?</section>", page, re.DOTALL)
        assert section is not None
        parsed = ElementTree.fromstring(section.group(0))
        said = [node.text or "" for node in parsed.iter()]
        self.assertTrue(any("<script>" in text for text in said), "payload is not character data")

    def test_the_drawn_graph_still_parses_as_xml_with_a_hostile_node_name(self) -> None:
        """`</text>` in a node name would close the element and make the rest markup."""
        page = rendered(self._poisoned(), "html").text
        drawing = re.search(r"<svg.*?</svg>", page, re.DOTALL)
        assert drawing is not None
        tree = ElementTree.fromstring(drawing.group(0))
        texts = tree.findall(".//{http://www.w3.org/2000/svg}text")
        self.assertTrue(any(self.PAYLOAD[:20] in (node.text or "") for node in texts))

    def test_the_unescaped_payload_would_break_the_document(self) -> None:
        """The control: a safety test that cannot fail records nothing."""
        broken = f'<svg xmlns="http://www.w3.org/2000/svg"><text>{self.PAYLOAD}</text></svg>'
        with self.assertRaises(ElementTree.ParseError):
            ElementTree.fromstring(broken)


class TheHtmlIsSelfContainedAndOffline(unittest.TestCase):
    """Task 4 and exit criterion 4."""

    def test_no_rendering_reaches_the_network_for_anything(self) -> None:
        for shape in SHAPES:
            page = rendered(record_of(shape), "html").text
            body = page.replace('xmlns="http://www.w3.org/2000/svg"', "")
            for forbidden in ("<script", "src=", "@import", "<link", "<img", "<iframe"):
                with self.subTest(shape=shape, forbidden=forbidden):
                    self.assertNotIn(forbidden, body)

    def test_the_only_link_is_the_engines_own_view(self) -> None:
        for shape in SHAPES:
            page = rendered(record_of(shape), "html").text
            for link in re.findall(r'href="([^"]+)"', page):
                with self.subTest(shape=shape, link=link):
                    self.assertIn("/dag-runs/", link)

    def test_every_node_the_record_holds_is_drawn(self) -> None:
        """A node dropped for being unrecognisable is a node whose failure nothing draws."""
        for shape in SHAPES:
            record = record_of(shape)
            page = rendered(record, "html").text
            with self.subTest(shape=shape):
                self.assertEqual(page.count("<rect"), len(record["nodes"]))

    def test_a_node_the_name_grammar_does_not_cover_is_still_drawn(self) -> None:
        record = record_of("green")
        record["nodes"].append(
            {
                **record["nodes"][0],
                "name": "wat",
                "role": None,
                "subject": None,
                "step_id": None,
            }
        )
        page = rendered(record, "html").text
        self.assertIn(">wat<", page)

    def test_the_page_needs_nothing_the_document_does_not_carry(self) -> None:
        """Every url(), font and reference a stylesheet could reach out through."""
        page = rendered(record_of("agent"), "html").text
        for forbidden in ("url(", "@font-face", "<use", "xlink:href", "srcset", "<base"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, page)

    def test_a_graph_too_big_to_read_states_its_size_instead_of_drawing_it(self) -> None:
        record = record_of("green")
        first = record["nodes"][0]
        record["nodes"] = [{**first, "name": f"work_s{index}"} for index in range(200)]
        record["edges"] = []
        page = rendered(record, "html").text
        self.assertNotIn("<svg", page)
        self.assertIn("past the point a drawn graph reads as one", page)

    def test_a_cycle_in_a_hand_edited_record_still_terminates(self) -> None:
        record = record_of("green")
        names = [node["name"] for node in record["nodes"][:3]]
        record["edges"] = [
            {"upstream": names[0], "downstream": names[1], "kind": "dependency"},
            {"upstream": names[1], "downstream": names[2], "kind": "dependency"},
            {"upstream": names[2], "downstream": names[0], "kind": "dependency"},
        ]
        page = rendered(record, "html").text
        self.assertEqual(page.count("<rect"), len(record["nodes"]))


class TheTerminalRenderingIsTheDefault(unittest.TestCase):
    """Task 2: readable in a scrolling terminal with no colour support."""

    def test_no_rendering_carries_a_colour_escape(self) -> None:
        for shape in SHAPES:
            with self.subTest(shape=shape):
                self.assertNotIn("\x1b[", rendered(record_of(shape), "terminal").text)

    def test_no_line_runs_past_the_column_bound_but_a_receipt(self) -> None:
        """A verbatim line is never wrapped: it is the text a person copies."""
        for shape in SHAPES:
            record = record_of(shape)
            verbatim = {
                str(step["resume_command"]) for step in record["steps"]
            } | {str(step["asked"]) for step in record["steps"]}
            for line in rendered(record, "terminal").text.splitlines():
                if any(part and part in line for part in verbatim):
                    continue
                if line.strip().startswith(("transcript", "standard error", "repository")):
                    continue
                with self.subTest(shape=shape, line=line[:40]):
                    self.assertLessEqual(len(line), 140)


class TheMarkdownRecordIsDurable(unittest.TestCase):
    """Task 3: it reads in a repository and in a pull request."""

    def test_every_table_row_carries_the_columns_its_header_declares(self) -> None:
        for shape in SHAPES:
            text = rendered(record_of(shape), "markdown").text
            columns = 0
            for line in text.splitlines():
                if not line.startswith("|"):
                    columns = 0
                    continue
                width = line.count("|") - line.count("\\|")
                if columns == 0:
                    columns = width
                with self.subTest(shape=shape, line=line[:40]):
                    self.assertEqual(width, columns)

    def test_no_heading_level_is_skipped(self) -> None:
        for shape in SHAPES:
            levels = [
                len(found)
                for found in re.findall(
                    r"^(#+) ", rendered(record_of(shape), "markdown").text, re.MULTILINE
                )
            ]
            with self.subTest(shape=shape):
                for before, after in itertools.pairwise(levels):
                    self.assertLessEqual(after - before, 1)


class AReaderCanAnswerAllSixQuestions(unittest.TestCase):
    """Task 12: the trust claim, as far as a suite can carry it.

    The human half — hand a reader a report from a run they did not watch — is a study rather
    than a test, and it is recorded as owed rather than simulated. What is mechanical is that
    every question has its section, in every rendering of every shape, and that the section
    carries the facts that answer it rather than only its heading.
    """

    ANSWERS: ClassVar[dict[str, tuple[str, ...]]] = {
        SECTION_VERDICT: ("run.verdict", "run.exit_code", "run.id"),
        SECTION_NEXT: ("run.next_action",),
        SECTION_STEPS: ("run.steps.verified",),
        SECTION_SHAPE: ("run.node_count", "run.step_count"),
        SECTION_RECEIPTS: ("run.view_url", "run.engine_version"),
    }

    def test_every_question_is_answered_in_its_own_section(self) -> None:
        for shape in SHAPES:
            record = record_of(shape)
            for sink in SINKS:
                stated: dict[str, set[str]] = {}
                for item in rendered(record, sink).stated:
                    stated.setdefault(item.section, set()).update(item.keys)
                for section, keys in self.ANSWERS.items():
                    for key in keys:
                        with self.subTest(shape=shape, sink=sink, key=key):
                            self.assertIn(key, stated.get(section, set()))

    def test_the_attention_section_answers_with_every_item_the_record_holds(self) -> None:
        for shape in SHAPES:
            record = record_of(shape)
            if not record["attention"]:
                continue
            for sink in SINKS:
                text = loose(rendered(record, sink).text)
                for item in record["attention"][:1]:
                    with self.subTest(shape=shape, sink=sink):
                        self.assertIn(loose(item["subject"]), text)

    def test_no_section_is_answered_by_its_heading_alone(self) -> None:
        for shape in SHAPES:
            record = record_of(shape)
            for sink in SINKS:
                sections = {item.section for item in rendered(record, sink).stated}
                with self.subTest(shape=shape, sink=sink):
                    self.assertGreaterEqual(len(sections), 5)


class EveryRenderingIsReadableWithNothingRunning(unittest.TestCase):
    """Exit criterion 4, and the crash case doc 12 measured."""

    def test_a_rendering_issues_no_subprocess_and_no_git_command(self) -> None:
        for shape in SHAPES:
            record = record_of(shape)
            for sink in SINKS:
                with self.subTest(shape=shape, sink=sink), patch(
                    "subprocess.run", side_effect=AssertionError("a process was started")
                ):
                    rendered(record, sink)

    def test_a_run_read_while_it_lives_and_after_it_died_both_render(self) -> None:
        state, reports, run_id = load("mid-run")
        living = extract(alive_copy(state), reports, run_id=run_id)
        dead = extract(state, reports, run_id=run_id)
        self.assertEqual(living["verdict"], "running")
        self.assertEqual(dead["verdict"], "failed")
        for sink in SINKS:
            with self.subTest(sink=sink):
                self.assertIn("has not finished", loose(rendered(living, sink).text))
                self.assertIn("This run failed.", loose(rendered(dead, sink).text))


class TheCommandRendersAndExitsOnTheRunsVerdict(unittest.TestCase):
    """The exit status is the run's verdict, never the command's own health."""

    def _run(self, shape: str, *arguments: str) -> subprocess.CompletedProcess[str]:
        """One recorded shape, laid out the way a real runs root is, and read through it."""
        directory = PACKAGE_ROOT / "fixtures" / "runs" / shape
        recording = json.loads((directory / "recording.json").read_text(encoding="utf-8"))
        run_id = str(recording["run_id"])
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        reports = directory / "reports"
        if reports.is_dir():
            shutil.copytree(reports, root / run_id / "reports")
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "cairn",
                "report",
                "--run",
                run_id,
                "--reports",
                str(root),
                "--engine-records",
                str(directory),
                *arguments,
            ],
            cwd=PACKAGE_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_the_command_exits_with_the_verdicts_frozen_code(self) -> None:
        for shape in SHAPES:
            with self.subTest(shape=shape):
                record = record_of(shape)
                completed = self._run(shape)
                self.assertEqual(
                    completed.returncode, VERDICT_EXIT_CODES[record["verdict"]]
                )

    def test_the_default_format_is_the_terminal_rendering(self) -> None:
        self.assertIn("== DID IT WORK ==", self._run("green").stdout)

    def test_each_format_renders_its_own_shape(self) -> None:
        self.assertIn("<!-- cairn:section:verdict -->", self._run("green", "--format", "markdown").stdout)
        self.assertIn("<!DOCTYPE html>", self._run("green", "--format", "html").stdout)

    def test_a_format_that_does_not_exist_is_a_usage_error_no_verdict_owns(self) -> None:
        completed = self._run("green", "--format", "pdf")
        self.assertEqual(completed.returncode, 2)
        self.assertNotIn(2, VERDICT_EXIT_CODES.values())

    def test_a_run_nothing_knows_exits_on_the_code_no_verdict_owns(self) -> None:
        completed = subprocess.run(
            [
                sys.executable, "-m", "cairn", "report",
                "--run", "nobody-ran-this",
                "--repository", str(PACKAGE_ROOT),
            ],
            cwd=PACKAGE_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, EXIT_NO_RECORD)
        self.assertIn("neither Cairn nor the engine holds a record", completed.stderr)

    def test_a_report_asked_for_without_a_repository_is_refused_rather_than_guessed(
        self,
    ) -> None:
        # The repository comes from the request for every capability, always: a report
        # resolved from the working directory answers about whatever tree the terminal was
        # sitting in, and says nothing to mark it as the wrong run's receipts.
        completed = subprocess.run(
            [sys.executable, "-m", "cairn", "report", "--run", "nobody-ran-this"],
            cwd=PACKAGE_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, EXIT_NO_RECORD)
        self.assertIn("--repository", completed.stderr)
        self.assertNotIn("neither Cairn nor the engine holds a record", completed.stderr)

    def test_a_recorded_corpus_is_read_without_naming_any_repository(self) -> None:
        # `--reports` names the runs root outright, so there is nothing to derive and
        # nothing to guess. Requiring a repository beside it would be ceremony.
        self.assertIn("== DID IT WORK ==", self._run("green").stdout)

    def test_the_rendering_can_be_written_to_a_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / "report.html"
            completed = self._run("green", "--format", "html", "--out", str(out))
            self.assertEqual(completed.returncode, 0)
            self.assertIn("<!DOCTYPE html>", out.read_text(encoding="utf-8"))

    def test_the_dispatch_reaches_the_report_before_any_step_identity(self) -> None:
        """A person reading a run is not standing inside a step."""
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(SystemExit) as caught:
            cairn_main(["report", "--help"])
        self.assertEqual(caught.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
