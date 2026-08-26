"""Doc 15: the skill, and the one thing about it a unit test can actually prove.

The skill is prose a model reads, so the interesting claim — that a person's sentence reaches
the right capability — is not settled here and this suite does not pretend it is. What is
settled here is everything the prose rests on:

- **the rules**, disjoint over their whole domain, total over it, and never resolving a
  costly ambiguity to the likelier reading;
- **the document**, checked cell by cell against the rules, so what a model reads and what is
  proved here cannot drift apart;
- **the gate**, which does not depend on the classification being right at all: nothing in
  Cairn's own code can start a run except through an offer that was priced and an
  authorisation that is spent exactly once.

That last one is the shape of the whole answer to doc 15 task 11. A corpus can be gamed by
omission and a classifier can be wrong; a chokepoint that has never been handed the material
to start a run cannot start one either way.

What is **not** proved here, stated so nobody reads a green suite as more than it is: that a
model reads an English sentence into the right verb class and object shape. The corpus
carries the phrasings so a model-in-the-loop harness has an input, and that measurement is
doc 17's.
"""

import ast
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Callable, Sequence
from contextlib import redirect_stderr, redirect_stdout
from itertools import combinations
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from cairn.core import CairnError
from cairn.enginehome import ENGINE_BINARY
from cairn.gitio import runs_root
from cairn.layout import check_run_id
from cairn.parameters import refuse_misfiled_records
from cairn.parameters import repository as declared_repository
from cairn.record.vocabulary import VERDICT_PRECEDENCE
from cairn.report import phrases
from cairn.skill import consent, explain, resolve, surface, trigger
from cairn.skill.cli import explain_main, run_main
from cairn.skill.dispatch import (
    ASK_REASONS,
    DISPATCH_RULES,
    FAMILY_BY_TABLED_ASK,
    QUESTION_BY_ASK,
    READINGS_BY_TABLED_ASK,
    Asked,
    Invocation,
    Selected,
    dispatch,
)
from cairn.skill.resolve import OccasionSignal, Resolved, Unresolved
from cairn.skill.trigger import EngineUnavailable
from cairn.skill.vocabulary import (
    ASK_FAMILIES,
    BINDINGS,
    CAPABILITY_EXPLAIN,
    CAPABILITY_ORDER,
    CAPABILITY_RUN,
    CAPABILITY_SCHEDULE,
    CONSENT_GATED,
    CONSENT_OUTCOMES,
    COST_BY_READING,
    COST_BY_ROLE,
    COST_SENTENCES,
    DOCUMENT_BY_CAPABILITY,
    FAMILY_HARMLESS_CHOICE,
    FAMILY_NOTHING_APPLIES,
    FAMILY_OBJECT_UNCLEAR,
    FAMILY_VERB_UNCLEAR,
    HEADLINE_COST,
    HEADLINE_DAEMON_COST,
    OCCASION_READINGS,
    QUALIFIER_SHAPES,
    READING_BY_TRIGGER,
    RUN_COST_FACTS,
    SUBJECT_SHAPES,
    TRIGGER_SCHEDULED,
    TRIGGER_SHAPES,
    VERB_CLASSES,
    VERB_INTERROGATING,
    VERB_RECOUNTING,
    WRITES_NOTHING,
)
from cairn.topology import ROLES, worktrees_parent
from cairn.verify import EXCLUSION_CAUSES
from cairn.workflow.schema import PARENT_BRANCH_PARAM, REPOSITORY_PARAM
from cairn.workflow.stamp import workflow_path
from scripts.measure_surface import block as measure_surface_block

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
SKILL = PACKAGE_ROOT / "SKILL.md"
CAPABILITIES = PACKAGE_ROOT / "capabilities"
README = PACKAGE_ROOT / "README.md"
CORPUS = json.loads(
    (PACKAGE_ROOT / "fixtures" / "invocations" / "cases.json").read_text(encoding="utf-8")
)
CASES = cast(list[dict[str, Any]], CORPUS["cases"])
# One well-formed run id, for the tests whose subject is the ledger rather than the run.
RUN_ID = "20260101T000000Z-aaaabbbb"
WORKFLOWS = PACKAGE_ROOT / "fixtures" / "workflows"
GOLDEN_WORKFLOW = WORKFLOWS / "mixed-kinds.yaml"

CAPABILITY_DOCUMENTS = tuple(sorted(set(DOCUMENT_BY_CAPABILITY.values())))

# Every file the skill's own claims could be restated in, which is what a "stated in exactly
# one place" test has to search to mean anything.
def sources() -> list[Path]:
    return [
        *sorted((PACKAGE_ROOT / "cairn").rglob("*.py")),
        *sorted((PACKAGE_ROOT / "docs").glob("*.md")),
        *sorted(CAPABILITIES.glob("*.md")),
        SKILL,
        README,
    ]


def _imports(module: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(module.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return found


def _sets(members: tuple[str, ...]) -> list[frozenset[str]]:
    """Absent, each on its own, and every pair — the arities the resolution turns on."""
    found: list[frozenset[str]] = [frozenset()]
    found += [frozenset({member}) for member in members]
    found += [frozenset(pair) for pair in combinations(members, 2)]
    return found


# The whole finite input space, enumerated once. Disjointness and totality are properties of
# a product space, so they are asserted over every point of it rather than over the points
# somebody thought of.
DOMAIN: tuple[Invocation, ...] = tuple(
    Invocation(verbs, subjects)
    for verbs in _sets(VERB_CLASSES)
    for subjects in _sets(SUBJECT_SHAPES)
)
ASKS: tuple[Asked, ...] = tuple(
    decision for point in DOMAIN if isinstance(decision := dispatch(point), Asked)
)


def family(name: str) -> list[dict[str, Any]]:
    return [case for case in CASES if case["family"] == name]


def reading_of(case: dict[str, Any]) -> Invocation:
    read = cast(dict[str, list[str]], case["reading"])
    return Invocation(
        verbs=frozenset(read["verbs"]),
        subjects=frozenset(read["subjects"]),
        qualifiers=frozenset(read["qualifiers"]),
    )


def dispatch_cases() -> list[dict[str, Any]]:
    return [case for case in CASES if "reading" in case]


class TheRulesAreDisjoint(unittest.TestCase):
    """Not by review — by there being nowhere for a second answer to come from."""

    def test_every_verb_and_object_pair_has_exactly_one_entry(self) -> None:
        self.assertEqual(
            set(DISPATCH_RULES),
            {(verb, subject) for verb in VERB_CLASSES for subject in SUBJECT_SHAPES},
        )
        self.assertEqual(len(DISPATCH_RULES), len(VERB_CLASSES) * len(SUBJECT_SHAPES))

    def test_every_outcome_is_a_capability_or_a_reason_from_the_ask_list(self) -> None:
        for pair, outcome in DISPATCH_RULES.items():
            with self.subTest(pair=pair):
                self.assertIn(outcome, set(CAPABILITY_ORDER) | set(ASK_REASONS))

    def test_the_table_carries_no_priority_to_break_a_tie_with(self) -> None:
        """A first-match rule list would resolve ambiguity by ordering. A mapping cannot,
        and there is nowhere here to rank one reading above another."""
        self.assertIsInstance(DISPATCH_RULES, dict)
        source = (PACKAGE_ROOT / "cairn" / "skill" / "dispatch.py").read_text("utf-8")
        for smell in (
            "sorted(DISPATCH_RULES",
            "max(DISPATCH_RULES",
            "min(DISPATCH_RULES",
            "priority",
            "weight",
            "confidence",
        ):
            with self.subTest(smell=smell):
                self.assertNotIn(smell, source)
        self.assertEqual(Selected._fields, ("capability", "rule"))

    def test_an_object_shape_is_never_also_a_qualifier(self) -> None:
        self.assertEqual(set(SUBJECT_SHAPES) & set(QUALIFIER_SHAPES), set())

    def test_the_two_capability_subsets_are_contiguous_slices_of_the_order(self) -> None:
        """The order means what it claims: worst-to-dispatch-here-wrongly, first."""
        self.assertEqual(CAPABILITY_ORDER[: len(CONSENT_GATED)], CONSENT_GATED)
        self.assertEqual(CAPABILITY_ORDER[-len(WRITES_NOTHING) :], WRITES_NOTHING)


class TheRulesAreTotal(unittest.TestCase):
    """Over the whole finite input space, not over the cases someone thought of."""

    def test_every_point_in_the_domain_answers_and_none_raises(self) -> None:
        for invocation in DOMAIN:
            with self.subTest(invocation=invocation):
                decision = dispatch(invocation)
                if isinstance(decision, Selected):
                    self.assertIn(decision.capability, CAPABILITY_ORDER)
                else:
                    self.assertIn(decision.ask.reason, ASK_REASONS)
                    self.assertIn(decision.ask.family, ASK_FAMILIES)

    def test_a_capability_is_selected_only_where_there_is_one_reading(self) -> None:
        """Arity other than one on either axis is a question, never a resolution — except
        where every reading of it only reads, which is the one stated exception."""
        for invocation in DOMAIN:
            decision = dispatch(invocation)
            if not isinstance(decision, Selected):
                continue
            with self.subTest(invocation=invocation):
                if decision.rule.startswith("safe:"):
                    self.assertIn(decision.capability, WRITES_NOTHING)
                else:
                    self.assertEqual(len(invocation.verbs), 1)
                    self.assertEqual(len(invocation.subjects), 1)

    def test_no_costly_ambiguity_is_ever_resolved(self) -> None:
        """The rule doc 15 exists for: a reading that spends money is asked about."""
        for invocation in DOMAIN:
            decision = dispatch(invocation)
            if isinstance(decision, Selected) and decision.rule.startswith("safe:"):
                with self.subTest(invocation=invocation):
                    self.assertNotIn(decision.capability, CONSENT_GATED)

    def test_every_capability_is_reachable(self) -> None:
        reached = {
            decision.capability
            for decision in map(dispatch, DOMAIN)
            if isinstance(decision, Selected)
        }
        self.assertEqual(reached, set(CAPABILITY_ORDER))

    def test_every_reason_in_the_ask_list_is_reachable(self) -> None:
        reached = {
            decision.ask.reason
            for decision in map(dispatch, DOMAIN)
            if isinstance(decision, Asked)
        }
        self.assertEqual(reached, set(ASK_REASONS))


class EveryQuestionIsWorthAsking(unittest.TestCase):
    """The invariants that keep the ask list from becoming friction or a hiding place."""

    def test_every_family_means_what_it_says_about_its_readings(self) -> None:
        for asked in ASKS:
            with self.subTest(reason=asked.ask.reason, rule=asked.rule):
                readings = set(asked.ask.readings)
                if asked.ask.family == FAMILY_NOTHING_APPLIES:
                    self.assertEqual(asked.ask.readings, ())
                elif asked.ask.family == FAMILY_VERB_UNCLEAR:
                    # At least one reading, at least one of them costly. A single reading
                    # still earns the question when the alternative is "nothing applies":
                    # what is unclear is whether anything was asked for at all.
                    self.assertGreaterEqual(len(readings), 1)
                    self.assertFalse(readings <= set(WRITES_NOTHING))
                elif asked.ask.family == FAMILY_HARMLESS_CHOICE:
                    # Asked for the order or the object, not for permission: no branch of
                    # this question costs anything.
                    self.assertGreaterEqual(len(readings), 1)
                    self.assertTrue(readings <= set(WRITES_NOTHING))
                else:
                    self.assertEqual(asked.ask.family, FAMILY_OBJECT_UNCLEAR)
                    self.assertGreaterEqual(len(readings), 1)

    def test_a_question_offering_a_costly_reading_names_what_it_costs(self) -> None:
        """Task 5's 'wherever it is made', reaching the point where a run or a daemon is one
        branch of a question rather than the subject of an offer."""
        for asked in ASKS:
            with self.subTest(reason=asked.ask.reason, readings=asked.ask.readings):
                if CAPABILITY_RUN in asked.ask.readings:
                    self.assertIn(HEADLINE_COST, asked.ask.question)
                if CAPABILITY_SCHEDULE in asked.ask.readings:
                    self.assertIn(HEADLINE_DAEMON_COST, asked.ask.question)

    def test_a_question_that_offers_a_run_in_prose_states_the_cost_in_its_own_text(
        self,
    ) -> None:
        """The appender covers questions computed from the table. A tabled question that
        offers a run in words has to carry the cost itself, or stripping it from the text
        would be repaired invisibly."""
        for reason, question in QUESTION_BY_ASK.items():
            if reason not in READINGS_BY_TABLED_ASK:
                continue
            offers_a_run = CAPABILITY_RUN in READINGS_BY_TABLED_ASK[reason]
            with self.subTest(reason=reason):
                self.assertEqual(offers_a_run, HEADLINE_COST in question)

    def test_a_question_that_applies_to_nothing_offers_no_capability(self) -> None:
        """`nothing_applies` means there is no reading, so a question of that family must
        not present one as a choice."""
        for asked in ASKS:
            if asked.ask.family != FAMILY_NOTHING_APPLIES:
                continue
            with self.subTest(reason=asked.ask.reason):
                self.assertEqual(asked.ask.readings, ())

    def test_every_reason_has_a_question_and_the_map_is_total(self) -> None:
        self.assertEqual(set(QUESTION_BY_ASK), set(ASK_REASONS))
        for reason, question in QUESTION_BY_ASK.items():
            with self.subTest(reason=reason):
                self.assertIn("?", question)

    def test_the_tabled_asks_are_exactly_the_reasons_the_table_holds(self) -> None:
        tabled = {
            outcome for outcome in DISPATCH_RULES.values() if outcome not in CAPABILITY_ORDER
        }
        self.assertEqual(set(FAMILY_BY_TABLED_ASK), tabled)
        self.assertEqual(set(READINGS_BY_TABLED_ASK), tabled)


class TheDocumentAndTheTableAreOneRuleSet(unittest.TestCase):
    """The drift oracle. What a model reads is what this suite proved, cell for cell."""

    def setUp(self) -> None:
        self.text = SKILL.read_text(encoding="utf-8")

    def _table(self) -> dict[tuple[str, str], str]:
        found: dict[tuple[str, str], str] = {}
        header: list[str] = []
        for line in self.text.splitlines():
            if not line.startswith("|"):
                continue
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if cells[0].startswith("verb class"):
                header = [cell.strip("`") for cell in cells[1:]]
                continue
            if not header or not cells[0].startswith("`"):
                continue
            verb = cells[0].strip("`")
            if verb not in VERB_CLASSES:
                continue
            for shape, cell in zip(header, cells[1:], strict=True):
                found[(verb, shape)] = cell.strip("*").removeprefix("ask ").strip("`")
        return found

    def test_the_documents_table_is_the_rules_exactly(self) -> None:
        self.assertEqual(self._table(), DISPATCH_RULES)

    def test_every_reason_in_the_ask_list_is_stated_in_the_document(self) -> None:
        for reason in ASK_REASONS:
            with self.subTest(reason=reason):
                self.assertIn(reason, self.text)

    def test_every_capability_is_named_and_owned(self) -> None:
        for capability in CAPABILITY_ORDER:
            with self.subTest(capability=capability):
                self.assertIn(f"**{capability.capitalize()}**", self.text)
        for document in CAPABILITY_DOCUMENTS:
            with self.subTest(document=document):
                self.assertIn(f"capabilities/{document}", self.text)
                self.assertTrue((CAPABILITIES / document).exists())

    def test_it_carries_no_procedure(self) -> None:
        """A procedure needs a fence, a numbered list or a command, and there are none."""
        self.assertNotIn("```", self.text)
        self.assertIsNone(re.search(r"^\s*\d+\.\s", self.text, re.MULTILINE))
        for command in ("python3 -m cairn plan", "python3 -m cairn workflow", "dagu start"):
            with self.subTest(command=command):
                self.assertNotIn(command, self.text)

    def test_the_procedure_it_does_not_carry_is_carried_by_the_capability_documents(
        self,
    ) -> None:
        """The reciprocal, without which the test above passes on a skill with no procedure
        anywhere at all."""
        for document in CAPABILITY_DOCUMENTS:
            text = (CAPABILITIES / document).read_text(encoding="utf-8")
            with self.subTest(document=document):
                self.assertIsNotNone(re.search(r"^\s*\d+\.\s", text, re.MULTILINE))

    def test_it_declares_a_name_and_a_description_and_nothing_that_loads_more(self) -> None:
        lines = self.text.splitlines()
        self.assertEqual(lines[0].strip(), "---")
        keys = {
            line.partition(":")[0].strip()
            for line in lines[1 : lines.index("---", 1)]
            if ":" in line
        }
        self.assertEqual(keys, {"name", "description", "disable-model-invocation"})

    def test_nothing_but_a_person_naming_it_can_open_this_skill(self) -> None:
        """A run takes the repository lock, spends on sessions and commits. Being reached
        from a sentence that never named Cairn is the wrong default for that, and a bundled
        skill answering the same sentence is a contest no description is guaranteed to win —
        so there is no contest: the invocation is the first consent."""
        self.assertIn("disable-model-invocation: true", self.text)

    def test_a_person_can_find_out_the_command_exists_without_reading_the_source(
        self,
    ) -> None:
        """The whole price of user invocation is discoverability, and this is what pays it.

        Two places, because they are the two doors: the README a person opens, and the
        internal command line somebody finds by going looking and mistakes for the surface.
        """
        self.assertIn("/cairn", (PACKAGE_ROOT / "README.md").read_text(encoding="utf-8"))
        helped = subprocess.run(
            (sys.executable, "-m", "cairn", "--help"),
            cwd=PACKAGE_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn("/cairn", helped.stdout)

    def test_the_view_is_named_where_it_is_the_better_answer(self) -> None:
        """Exit criterion 1's other half: no capability requires it, and the skill says so
        wherever it is better rather than pretending it is not there."""
        for document in ("running.md", "reading.md"):
            text = (CAPABILITIES / document).read_text(encoding="utf-8")
            with self.subTest(document=document):
                self.assertIn("Where the engine's view is better", text)
                self.assertIn("cost", text)
                self.assertIn("divergence", text)
                self.assertIn("verdict", text)

    def test_a_run_is_started_from_exactly_one_place(self) -> None:
        """Across the surface a model reads. The README names the command too, for a
        maintainer tracing how a run is authorised — but no capability the skill can select
        may reach a start except the one that owns it."""
        holders = {
            path.name
            for path in [SKILL, *sorted(CAPABILITIES.glob("*.md"))]
            if "cairn run start" in path.read_text("utf-8")
        }
        self.assertEqual(holders, {"running.md"})

    def test_reading_a_run_can_start_nothing(self) -> None:
        text = (CAPABILITIES / "reading.md").read_text(encoding="utf-8")
        for starter in ("cairn run start", "cairn run offer", "dagu start"):
            with self.subTest(starter=starter):
                self.assertNotIn(starter, text)


class EveryCapabilityDocumentDeclaresItsContract(unittest.TestCase):
    """Task 2: each names its entry preconditions and what it is bound to on entry."""

    ROWS = (
        "Capability",
        "Entered when",
        "Preconditions",
        "Bound on entry",
        "Owns",
        "Defers to",
        "Triggers",
    )

    def _contract(self, document: str) -> dict[str, str]:
        found: dict[str, str] = {}
        for line in (CAPABILITIES / document).read_text(encoding="utf-8").splitlines():
            if not line.startswith("|"):
                continue
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) == 2 and cells[0] in self.ROWS:
                found[cells[0]] = cells[1]
        return found

    def test_every_document_declares_every_row(self) -> None:
        for document in CAPABILITY_DOCUMENTS:
            with self.subTest(document=document):
                self.assertEqual(set(self._contract(document)), set(self.ROWS))

    def test_every_capability_has_exactly_one_owning_document(self) -> None:
        owned: dict[str, str] = {}
        for document in CAPABILITY_DOCUMENTS:
            for capability in re.findall(r"`(\w+)`", self._contract(document)["Capability"]):
                owned[capability] = document
        self.assertEqual(owned, DOCUMENT_BY_CAPABILITY)

    def test_every_binding_a_document_claims_is_one_the_dispatcher_can_supply(self) -> None:
        for document in CAPABILITY_DOCUMENTS:
            bound = re.findall(r"`([\w_]+)`", self._contract(document)["Bound on entry"])
            with self.subTest(document=document):
                self.assertTrue(bound)
                self.assertTrue(set(bound) <= set(BINDINGS), set(bound) - set(BINDINGS))

    def test_a_document_that_triggers_nothing_names_no_way_to_start_anything(self) -> None:
        for document in CAPABILITY_DOCUMENTS:
            if self._contract(document)["Triggers"] != "nothing":
                continue
            text = (CAPABILITIES / document).read_text(encoding="utf-8")
            with self.subTest(document=document):
                self.assertNotIn("dagu start", text)
                self.assertNotIn("cairn run", text)

    def test_every_document_it_defers_to_exists(self) -> None:
        for document in CAPABILITY_DOCUMENTS:
            for target in re.findall(r"\]\(([^)]+)\)", self._contract(document)["Defers to"]):
                with self.subTest(document=document, target=target):
                    self.assertTrue((CAPABILITIES / target).resolve().exists())

    def test_no_document_reaches_outside_the_package(self) -> None:
        """Doc 16 extracts this directory by moving it, so a link out of it would break."""
        for document in CAPABILITY_DOCUMENTS:
            text = (CAPABILITIES / document).read_text(encoding="utf-8")
            with self.subTest(document=document):
                self.assertNotIn("](../../", text)


class TheConsentRuleIsStatedOnce(unittest.TestCase):
    """Exit criterion 3, as a search over every file that could restate it."""

    CLAUSES = (
        "A qualifying yes",
        "A bare acknowledgement is not one",
        "A yes that predates the offer is not one",
        "One acceptance authorises exactly one execution",
    )

    def test_each_clause_is_stated_in_the_skill_file_and_nowhere_else(self) -> None:
        for clause in self.CLAUSES:
            holders = {
                path.name for path in sources() if clause in path.read_text("utf-8")
            }
            with self.subTest(clause=clause):
                self.assertEqual(holders, {"SKILL.md"})

    def test_every_document_that_can_offer_a_run_points_at_it(self) -> None:
        for document in ("authoring.md", "running.md"):
            text = (CAPABILITIES / document).read_text(encoding="utf-8")
            with self.subTest(document=document):
                self.assertIn("../SKILL.md", text)

    def test_the_run_cost_is_composed_from_the_definition_rather_than_retyped(self) -> None:
        """Task 5. A cost typed into prose is a cost that goes stale silently, so the only
        statement of it is built from the file that is about to run."""
        self.assertEqual(set(COST_SENTENCES), set(RUN_COST_FACTS))
        self.assertLessEqual(set(COST_BY_ROLE), set(RUN_COST_FACTS))
        self.assertLessEqual(set(COST_BY_ROLE.values()), set(ROLES))
        stated = consent.disclosure(GOLDEN_WORKFLOW)
        for sentence in stated:
            with self.subTest(sentence=sentence):
                self.assertNotIn("{", sentence)
        joined = " ".join(stated)
        self.assertIn("/srv/work/product", joined)
        self.assertIn("run lock", joined)
        self.assertIn("commits", joined)

    def test_a_chain_is_priced_for_no_worktrees_and_no_merge(self) -> None:
        """A wave holding one step runs in the repository itself ([07]), so a chain creates
        no worktree and lands no merge — and a price for what a definition cannot do is a
        price nobody agreed to."""
        chain = " ".join(consent.disclosure(WORKFLOWS / "mixed-kinds.yaml"))
        self.assertNotIn(str(worktrees_parent(Path("/srv/work/product"))), chain)
        self.assertNotIn("it merges", chain)
        # The facts that are every run's are still all there, the branch among them.
        self.assertIn("run lock", chain)
        self.assertIn("lands on main", chain)
        self.assertIn("unix socket", chain)

    def test_a_fan_out_is_priced_for_both(self) -> None:
        """Paired with the chain, this is what proves the price reads the file."""
        wide = consent.disclosure(WORKFLOWS / "fan-out.yaml")
        self.assertEqual(len(wide), len(RUN_COST_FACTS))
        joined = " ".join(wide)
        self.assertIn(str(worktrees_parent(Path("/srv/work/product"))), joined)
        self.assertIn("it merges", joined)

    def test_every_priced_fact_is_the_vocabularys_and_keeps_its_order(self) -> None:
        for name in ("linear-chain", "mixed-kinds", "single-step", "fan-out", "multi-wave"):
            with self.subTest(workflow=name):
                stated = consent.disclosure(WORKFLOWS / f"{name}.yaml")
                every = [COST_SENTENCES[fact] for fact in RUN_COST_FACTS]
                position = -1
                for sentence in stated:
                    template = next(
                        line for line in every if line.split("{")[0] in sentence
                    )
                    self.assertGreater(every.index(template), position)
                    position = every.index(template)

    def test_the_price_names_the_socket_every_run_opens(self) -> None:
        """A cause a person can clear before saying yes, and one that costs the yes after."""
        joined = " ".join(consent.disclosure(GOLDEN_WORKFLOW))
        self.assertIn("unix socket", joined)
        self.assertIn("bind", joined)

    def test_the_disclosure_states_the_ceiling_the_model_and_the_timeout(self) -> None:
        """17.3 task 4: the three bounds the definition writes are three of the facts a
        person agrees to, read from the file rather than retyped."""
        joined = " ".join(consent.disclosure(GOLDEN_WORKFLOW))
        self.assertIn("US$ 8.00", joined)
        self.assertIn("opus", joined)
        self.assertIn("7200s", joined)

    def test_a_definition_with_an_unbounded_session_cannot_be_offered(self) -> None:
        """An agent body with no written ceiling is the one thing a price cannot cover,
        so the offer refuses rather than pricing the run as though the session were free."""
        with tempfile.TemporaryDirectory() as root:
            unbounded = Path(root) / "unbounded.yaml"
            document = cast(dict[str, Any], json.loads(GOLDEN_WORKFLOW.read_text("utf-8")))
            for step in cast(list[dict[str, Any]], document["steps"]):
                body = str(step.get("run", ""))
                if "--max-budget-usd" in body:
                    words = body.split()
                    index = words.index("--max-budget-usd")
                    del words[index : index + 2]
                    step["run"] = " ".join(words)
            unbounded.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(CairnError, "cannot be stated"):
                consent.disclosure(unbounded)

    def test_the_money_fact_leads(self) -> None:
        self.assertEqual(RUN_COST_FACTS[0], "spend")
        self.assertIn("paid agent session", consent.disclosure(GOLDEN_WORKFLOW)[0])

    def test_a_cost_cannot_be_quoted_for_a_definition_nobody_has(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            stripped = Path(root) / "stripped.yaml"
            document = cast(dict[str, Any], json.loads(GOLDEN_WORKFLOW.read_text("utf-8")))
            document.pop("params")
            stripped.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(CairnError):
                consent.disclosure(stripped)


class WhatAcceptsAnOfferAndWhatDoesNot(unittest.TestCase):
    """The consent clauses, as properties of the filesystem rather than of prose."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.repository = self.root / "product"
        self.repository.mkdir()
        subprocess.run(("git", "init", "-q"), cwd=self.repository, check=True)
        self.workflow = self.root / "offline-export.yaml"
        shutil.copy(GOLDEN_WORKFLOW, self.workflow)

    def _offer(self) -> consent.Offer:
        made, _ = consent.make_offer(
            self.repository,
            plan="offline-export",
            workflow=self.workflow,
            parent_branch="main",
            occasion_reading="new_occasion",
            occasion=None,
        )
        return made

    def test_a_qualifying_yes_authorises_one_execution(self) -> None:
        made = self._offer()
        granted = consent.spend(self.repository, made.offer_id, reply="yes, go ahead", run_id=RUN_ID)
        self.assertIsInstance(granted, consent.Authorisation)

    def test_the_same_acceptance_cannot_authorise_a_second(self) -> None:
        made = self._offer()
        consent.spend(self.repository, made.offer_id, reply="yes, go ahead", run_id=RUN_ID)
        again = consent.spend(self.repository, made.offer_id, reply="yes, go ahead", run_id=RUN_ID)
        self.assertIsInstance(again, consent.Refused)
        self.assertEqual(cast(consent.Refused, again).outcome, "already_spent")

    def test_a_spent_offer_records_the_words_it_was_accepted_with(self) -> None:
        # A run spends money and commits on someone's say-so. Recording only *that* it was
        # accepted leaves nothing afterwards able to answer which words did it.
        made = self._offer()
        consent.spend(self.repository, made.offer_id, reply="yes, run it", run_id=RUN_ID)
        accepted = consent.acceptance_of(self.repository, made.offer_id)
        self.assertIsNotNone(accepted)
        self.assertEqual(cast(consent.Acceptance, accepted).reply, "yes, run it")
        self.assertTrue(cast(consent.Acceptance, accepted).spent_at)

    def test_an_offer_that_was_never_spent_records_no_acceptance(self) -> None:
        made = self._offer()
        self.assertIsNone(consent.acceptance_of(self.repository, made.offer_id))

    def test_the_second_acceptance_still_names_when_the_first_was_spent(self) -> None:
        made = self._offer()
        consent.spend(self.repository, made.offer_id, reply="yes, go ahead", run_id=RUN_ID)
        again = consent.spend(self.repository, made.offer_id, reply="yes, go ahead", run_id=RUN_ID)
        moment = cast(consent.Acceptance, consent.acceptance_of(self.repository, made.offer_id))
        self.assertIn(moment.spent_at, cast(consent.Refused, again).why)

    def test_a_yes_predating_the_offer_names_no_offer(self) -> None:
        """It cannot quote an id that did not exist when it was given — the clause holds by
        the shape of the token rather than by comparing clocks."""
        refused = consent.spend(
            self.repository,
            "20200101T000000Z-deadbeef",
            reply="yes, go ahead",
            run_id=RUN_ID,
        )
        self.assertIsInstance(refused, consent.Refused)
        self.assertEqual(cast(consent.Refused, refused).outcome, "no_such_offer")

    def _staged(self, case: dict[str, Any]) -> str:
        """The offer id this case's reply should be answered against.

        A case may declare the ledger state it is about — no offer, one already spent, one
        damaged, one whose definition moved — and the outcome it declares is an outcome of
        that state, so it has to be set up rather than skipped.
        """
        # Every case starts from the same ledger and the same definition, because one that
        # stages a damaged or replaced one must not decide what the next case sees.
        shutil.copy(GOLDEN_WORKFLOW, self.workflow)
        standing = case.get("offer")
        if standing == "absent":
            return "20200101T000000Z-deadbeef"
        made = self._offer()
        if standing == "damaged":
            consent.offer_path(self.repository, made.offer_id).write_text(
                "{", encoding="utf-8"
            )
        elif standing == "spent":
            consent.spend(self.repository, made.offer_id, reply="yes, go ahead", run_id=RUN_ID)
        elif standing == "moved":
            self.workflow.write_text("{}", encoding="utf-8")
        return made.offer_id

    def test_every_reply_in_the_corpus_is_answered_the_way_it_says(self) -> None:
        for case in family("consent"):
            with self.subTest(case=case["id"]):
                answered = consent.spend(
                    self.repository,
                    self._staged(case),
                    reply=case["reply"],
                    run_id=RUN_ID,
                )
                if case["expect"]["outcome"] == "accepted":
                    self.assertIsInstance(answered, consent.Authorisation)
                    continue
                self.assertIsInstance(answered, consent.Refused)
                self.assertEqual(
                    cast(consent.Refused, answered).outcome, case["expect"]["outcome"]
                )

    def test_no_reply_is_read_for_meaning_and_the_corpus_says_which_ones(self) -> None:
        """Doc 15's fourth clause, held where it can be kept rather than where it reads well.

        Every one of these replies must never reach a start — "ok" accepts nothing, "no" is a
        refusal — and every one of them spends the offer if it arrives. That is not a hole in
        the gate: the string arriving is the session's own `--reply` argument, so a comparison
        made here would run after the judgement it claimed to make and could fire only where a
        session misread the words and then quoted them faithfully. A list that cannot see the
        case it exists for is reasoned about as protection and is not any. The rule binds the
        session, `SKILL.md` states it, and the paid suite measures whether it was kept.
        """
        judged = [case for case in family("consent") if case.get("judged_by") == "session"]
        self.assertTrue(judged)
        for case in judged:
            with self.subTest(case=case["id"]):
                self.assertEqual(case["expect"]["outcome"], "accepted")
                answered = consent.spend(
                    self.repository,
                    self._staged(case),
                    reply=case["reply"],
                    run_id=RUN_ID,
                )
                self.assertIsInstance(answered, consent.Authorisation)

    def test_the_ledger_holds_no_list_of_words_a_person_might_say(self) -> None:
        """The reciprocal, so a deny list cannot creep back in beside the artifact clauses:
        no module under `cairn/skill/` may hold a set of English phrases at all."""
        english = re.compile(r"\"(?:ok|okay|sure|thanks|no|nope|stop|wait)\"")
        for path in sorted((PACKAGE_ROOT / "cairn" / "skill").rglob("*.py")):
            with self.subTest(module=path.name):
                self.assertIsNone(english.search(path.read_text(encoding="utf-8")))

    def test_a_reply_with_no_words_in_it_authorises_nothing(self) -> None:
        """Not a judgement about meaning — a run authorised by an empty argument leaves the
        ledger unable to say afterwards what authorised it."""
        made = self._offer()
        for reply in ("", "   ", "..."):
            with self.subTest(reply=reply):
                answered = consent.spend(self.repository, made.offer_id, reply=reply, run_id=RUN_ID)
                self.assertIsInstance(answered, consent.Refused)
                self.assertEqual(cast(consent.Refused, answered).outcome, "no_words")

    def test_an_offer_that_is_damaged_is_not_reported_as_one_that_never_existed(
        self,
    ) -> None:
        """Folding a corrupt ledger into 'no such offer' would tell a person their yes
        predated an offer that in fact exists — a claim about their conversation drawn from
        a filesystem fault."""
        made = self._offer()
        consent.offer_path(self.repository, made.offer_id).write_text("{", encoding="utf-8")
        answered = consent.spend(self.repository, made.offer_id, reply="yes, go ahead", run_id=RUN_ID)
        self.assertIsInstance(answered, consent.Refused)
        self.assertEqual(cast(consent.Refused, answered).outcome, "offer_unreadable")

    def test_an_offer_id_that_could_name_another_file_is_refused(self) -> None:
        with self.assertRaises(CairnError):
            consent.offer_path(self.repository, "../../config")

    def test_reading_an_offer_writes_nothing(self) -> None:
        """A read path that creates a directory fails on a repository whose admin directory
        is not writable, and it is a read."""
        self.assertIsNone(consent.read_offer(self.repository, "20200101T000000Z-deadbeef"))
        self.assertFalse(consent.offers_directory(self.repository).exists())

    def test_a_refused_acceptance_leaves_the_offer_spendable(self) -> None:
        """A refusal is not a consumption: the person's yes still stands once the cause is
        cleared, and asking again would be asking twice for one decision."""
        made = self._offer()
        consent.spend(self.repository, made.offer_id, reply="", run_id=RUN_ID)
        granted = consent.spend(self.repository, made.offer_id, reply="yes, run it", run_id=RUN_ID)
        self.assertIsInstance(granted, consent.Authorisation)

    def test_an_offer_is_void_once_the_definition_it_priced_has_moved(self) -> None:
        made = self._offer()
        self.workflow.write_text(
            self.workflow.read_text("utf-8").replace("mixed-kinds", "something-else"),
            encoding="utf-8",
        )
        refused = consent.spend(self.repository, made.offer_id, reply="yes, go ahead", run_id=RUN_ID)
        self.assertIsInstance(refused, consent.Refused)
        self.assertEqual(cast(consent.Refused, refused).outcome, "workflow_moved")

    def test_the_offer_records_when_it_was_made_and_when_it_was_spent(self) -> None:
        """What no test can prove is that a person was asked. The ledger records both
        moments so a zero gap is visible rather than claimed against."""
        made = self._offer()
        self.assertTrue(made.offered_at)
        granted = consent.spend(self.repository, made.offer_id, reply="yes", run_id=RUN_ID)
        self.assertTrue(cast(consent.Authorisation, granted).granted_at)

    def test_the_ledger_lives_where_no_commit_or_worktree_removal_can_reach_it(self) -> None:
        made = self._offer()
        path = consent.offer_path(self.repository, made.offer_id)
        self.assertIn(".git", path.parts)

    def test_every_refusal_the_corpus_names_is_in_the_frozen_set(self) -> None:
        for case in family("consent"):
            with self.subTest(case=case["id"]):
                self.assertIn(case["expect"]["outcome"], CONSENT_OUTCOMES)


def _engine_that_exited(code: int | None) -> Callable[..., "FakeEngine"]:
    """A launcher standing in for one whose child is already in a known state."""

    def launched(*_arguments: object) -> FakeEngine:
        return FakeEngine(exit_code=code)

    return launched


def _answers(replies: list[bool]) -> Callable[..., bool]:
    """`engine_holds` answering a scripted sequence, so the poll loop can be driven."""

    def asked(*_arguments: object) -> bool:
        return replies.pop(0)

    return asked


class FakeEngine:
    """A launched engine that never exits, which is what a detached start expects.

    `poll` answering `None` is the ordinary case: `start` returns once the engine's own
    history says it has the run, and the process outlives the command that made it.
    """

    def __init__(self, exit_code: int | None = None) -> None:
        self.returncode = exit_code
        self.waited = False
        self.stdin = None
        self.stdout = None

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self.waited = True
        return 0 if self.returncode is None else self.returncode

    def terminate(self) -> None:
        raise AssertionError("a detached engine is never terminated by the start")

    def kill(self) -> None:
        raise AssertionError("a detached engine is never killed by the start")


class NoInvocationStartsARunWithoutAQualifyingYes(unittest.TestCase):
    """Doc 15 task 11, as a hard gate: every assertion here is an equality over every case,
    and the last test in the class is what keeps anything softer from creeping in."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.repository = self.root / "product"
        self.repository.mkdir()
        subprocess.run(("git", "init", "-q"), cwd=self.repository, check=True)
        self.workflow = workflow_path(self.repository, "offline-export")
        self.workflow.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(GOLDEN_WORKFLOW, self.workflow)
        self.launched: list[Sequence[str]] = []

    def _factory(self, command: Sequence[str], **_options: Any) -> FakeEngine:
        self.launched.append(command)
        return FakeEngine()

    def _offer(self) -> consent.Offer:
        made, _ = consent.make_offer(
            self.repository,
            plan="offline-export",
            workflow=self.workflow,
            occasion_reading="new_occasion",
            occasion=None,
        )
        return made

    def _start(self, offer_id: str, reply: str) -> None:
        """Everything a run passes through, with the engine replaced by a recorder.

        Nothing here is stubbed but the launch itself: the offer is real, the spend is real,
        and every other process is forbidden — so the count is over processes started rather
        than over the one seam the test injected.
        """
        run_id = "20260101T000000Z-aaaabbbb"
        granted = consent.spend(self.repository, offer_id, reply=reply, run_id=run_id)
        if isinstance(granted, consent.Refused):
            return
        with (
            patch("cairn.skill.trigger.assert_pinned"),
            patch("cairn.skill.trigger.rehearse_start"),
            patch("subprocess.run", side_effect=AssertionError("started a process")),
            patch("subprocess.Popen", side_effect=AssertionError("started a process")),
        ):
            trigger.start(
                granted,
                run_id,
                runs_root=self.repository / "runs",
                records=self.repository / "records",
                popen_factory=self._factory,
                registered=lambda _identity: True,
            )

    def test_no_classification_of_any_case_reaches_the_ledger(self) -> None:
        """Dispatch selecting Run is an offer, never an execution — and reading a request
        leaves no trace in the repository at all. The launch half is
        `test_no_run_phrasing_in_the_corpus_starts_anything_on_its_own`, which drives one."""
        for case in dispatch_cases():
            with self.subTest(case=case["id"]):
                dispatch(reading_of(case))
        self.assertFalse(consent.offers_directory(self.repository).exists())

    def test_the_classifier_cannot_reach_the_thing_that_authorises(self) -> None:
        """The gate does not rest on the classification being right: a module that was never
        handed the consent machinery cannot mint an authorisation however wrong it is."""
        self.assertEqual(
            _imports(PACKAGE_ROOT / "cairn" / "skill" / "dispatch.py"),
            {"cairn.skill.vocabulary", "typing", "__future__"},
        )

    def test_an_authorisation_is_constructed_in_one_module_only(self) -> None:
        holders: set[str] = set()
        for path in (PACKAGE_ROOT / "cairn").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "Authorisation"
                ):
                    holders.add(path.name)
        self.assertEqual(holders, {"consent.py"})

    def test_every_qualifying_yes_starts_exactly_one_run_and_nothing_else_starts_any(
        self,
    ) -> None:
        """Over the replies the ledger is answerable for, which is every one whose outcome is
        a fact about a file. The nine a session judges are excluded and asserted elsewhere,
        with the reasoning attached — carrying them here would put "'no' launched a run" in a
        class whose whole claim is the opposite, stated in a line that never says why.
        """
        for case in family("consent"):
            if case.get("judged_by") == "session":
                continue
            with self.subTest(case=case["id"]):
                self.launched.clear()
                standing = case.get("offer")
                if standing == "absent":
                    self._start("20200101T000000Z-deadbeef", case["reply"])
                elif standing == "damaged":
                    made = self._offer()
                    consent.offer_path(self.repository, made.offer_id).write_text(
                        "{", encoding="utf-8"
                    )
                    self._start(made.offer_id, case["reply"])
                elif standing == "spent":
                    made = self._offer()
                    self._start(made.offer_id, case["reply"])
                    self.launched.clear()
                    self._start(made.offer_id, case["reply"])
                elif standing == "moved":
                    made = self._offer()
                    self.workflow.write_text("{}", encoding="utf-8")
                    self._start(made.offer_id, case["reply"])
                    shutil.copy(GOLDEN_WORKFLOW, self.workflow)
                else:
                    made = self._offer()
                    self._start(made.offer_id, case["reply"])
                expected = 1 if case["expect"]["outcome"] == "accepted" else 0
                self.assertEqual(len(self.launched), expected)

    def test_no_run_phrasing_in_the_corpus_starts_anything_on_its_own(self) -> None:
        """Every case the corpus resolves to Run, carried through the whole path with no
        offer standing. Each is as unambiguous an instruction to run as the corpus holds, and
        none of them starts anything: what authorises a run is an offer minted before the
        words, so an invocation can never carry its own acceptance however it is phrased."""
        for case in dispatch_cases():
            decision = dispatch(reading_of(case))
            if not isinstance(decision, Selected) or decision.capability != CAPABILITY_RUN:
                continue
            with self.subTest(case=case["id"]):
                self.launched.clear()
                self._start("20200101T000000Z-deadbeef", case["utterance"])
                self.assertEqual(self.launched, [])

    def test_two_targets_need_two_offers(self) -> None:
        """One acceptance authorises exactly one execution, so a sentence naming two runs
        cannot ride one yes even though the table selects Run once."""
        first = self._offer()
        self._start(first.offer_id, "yes, go ahead")
        self._start(first.offer_id, "yes, go ahead")
        self.assertEqual(len(self.launched), 1)
        second = self._offer()
        self._start(second.offer_id, "yes, go ahead")
        self.assertEqual(len(self.launched), 2)

    def test_the_pre_spend_check_rehearses_a_run_as_well_as_reading_the_version(self) -> None:
        """`dagu validate` and `dagu dry` never bind a socket, so a workflow authors cleanly
        in a shell that cannot run it. Only actually starting a run finds that out, and it
        has to be found out before the offer is spent."""
        order: list[str] = []

        def note(what: str) -> Callable[..., None]:
            def recorded(*_args: object, **_keywords: object) -> None:
                order.append(what)

            return recorded

        with (
            patch("cairn.skill.trigger.assert_pinned", side_effect=note("pinned")),
            patch("cairn.skill.trigger.rehearse_start", side_effect=note("rehearsed")),
        ):
            trigger.refuse_unusable_engine()
        # The cheaper question first, so a machine with no engine at all is refused by it
        # and the rehearsal is never reached with nothing to rehearse against.
        self.assertEqual(order, ["pinned", "rehearsed"])

    def test_a_shell_that_cannot_bind_the_run_socket_costs_nobody_their_yes(self) -> None:
        """The refusal that used to arrive inside the run, where it had already cost the
        acceptance — now it arrives before the offer is spent ([19 C])."""
        made = self._offer()
        with patch(
            "cairn.skill.cli.refuse_unusable_engine",
            side_effect=EngineUnavailable(
                "failed to start the unix socket server: listen unix "
                "/tmp/@dagu__x.sock: bind: operation not permitted"
            ),
        ):
            refused = run_main(
                [
                    "start",
                    "--repository",
                    str(self.repository),
                    "--offer",
                    made.offer_id,
                    "--reply",
                    "yes, go ahead",
                ]
            )
        self.assertEqual(refused, 1)
        self.assertIsNone(consent.acceptance_of(self.repository, made.offer_id))
        # And the same offer still buys exactly one run once the cause is cleared.
        self._start(made.offer_id, "yes, go ahead")
        self.assertEqual(len(self.launched), 1)

    def test_a_refusal_that_started_nothing_leaves_the_acceptance_standing(self) -> None:
        """An engine the run could not have used is a cause a person can clear, so it must
        not cost them their yes — the offer is checked before it is spent."""
        made = self._offer()
        with patch(
            "cairn.skill.cli.refuse_unusable_engine",
            side_effect=EngineUnavailable("wrong engine"),
        ):
            refused = run_main(
                [
                    "start",
                    "--repository",
                    str(self.repository),
                    "--offer",
                    made.offer_id,
                    "--reply",
                    "yes, go ahead",
                ]
            )
        self.assertEqual(refused, 1)
        self._start(made.offer_id, "yes, go ahead")
        self.assertEqual(len(self.launched), 1)

    def test_a_start_never_retargets_the_repository(self) -> None:
        """Retargeting writes the run's whole record into the authoring repository, so the
        skill's own trigger varies the occasion and the branch and never the target."""
        made = self._offer()
        self._start(made.offer_id, "yes, go ahead")
        self.assertEqual(len(self.launched), 1)
        self.assertNotIn(REPOSITORY_PARAM, " ".join(self.launched[0]))

    def test_every_parameter_a_start_composes_comes_from_the_authorisation(self) -> None:
        """A term settled after the offer is a term nobody agreed to — the branch most of
        all, since it is what verified work is merged into."""
        made = self._offer()
        self._start(made.offer_id, "yes, go ahead")
        composed = " ".join(self.launched[0])
        self.assertIn(f"{PARENT_BRANCH_PARAM}={made.parent_branch}", composed)
        self.assertNotIn("--parent-branch", composed)

    def test_the_engine_is_never_asked_to_retry(self) -> None:
        made = self._offer()
        self._start(made.offer_id, "yes, go ahead")
        self.assertNotIn("retry", self.launched[0])

    def test_this_gate_holds_no_threshold(self) -> None:
        """A gate that grew a tolerance would have to say so out loud, and here is where it
        would be caught. Every assertion above is an equality over every case."""
        source = Path(__file__).read_text(encoding="utf-8")
        body = source[source.index("class NoInvocationStartsARunWithoutAQualifyingYes") :]
        body = body[: body.index("\n    def test_this_gate_holds_no_threshold")]
        for smell in (
            "assertGreater",
            "assertLess",
            "threshold",
            "tolerance",
            "percent",
            "0.9",
        ):
            with self.subTest(smell=smell):
                self.assertNotIn(smell, body)


class TheCorpusIsWhatItClaimsToBe(unittest.TestCase):
    """A corpus can be weakened by omission, so its shape is asserted as well as its answers."""

    def test_every_case_resolves_the_way_the_corpus_says(self) -> None:
        for case in dispatch_cases():
            with self.subTest(case=case["id"]):
                decision = dispatch(reading_of(case))
                expect = cast(dict[str, Any], case["expect"])
                if expect["capability"] is not None:
                    self.assertIsInstance(decision, Selected)
                    self.assertEqual(cast(Selected, decision).capability, expect["capability"])
                else:
                    self.assertIsInstance(decision, Asked)
                    assert isinstance(decision, Asked)
                    self.assertEqual(decision.ask.reason, expect["ask"])
                    self.assertEqual(list(decision.ask.readings), expect["readings"])

    def test_every_capability_has_at_least_four_canonical_phrasings(self) -> None:
        reached: dict[str, int] = {name: 0 for name in CAPABILITY_ORDER}
        for case in family("canonical"):
            decision = dispatch(reading_of(case))
            self.assertIsInstance(decision, Selected)
            assert isinstance(decision, Selected)
            reached[decision.capability] += 1
        for capability, count in reached.items():
            with self.subTest(capability=capability):
                self.assertGreaterEqual(count, 4)

    def test_every_reason_in_the_ask_list_appears_in_the_corpus(self) -> None:
        reached = {
            decision.ask.reason
            for case in dispatch_cases()
            if isinstance(decision := dispatch(reading_of(case)), Asked)
        }
        self.assertEqual(reached, set(ASK_REASONS))

    def test_the_three_shapes_doc_fifteen_names_are_each_covered(self) -> None:
        adversarial = family("adversarial")
        reasons = [
            decision.ask.reason
            for case in adversarial
            if isinstance(decision := dispatch(reading_of(case)), Asked)
        ]
        self.assertGreaterEqual(reasons.count("many_verbs"), 2)
        self.assertGreaterEqual(reasons.count("executing_a_past_run"), 2)
        self.assertGreaterEqual(reasons.count("no_verb"), 2)

    def test_every_value_in_every_case_is_a_frozen_word(self) -> None:
        for case in CASES:
            with self.subTest(case=case["id"]):
                if "reading" in case:
                    read = cast(dict[str, list[str]], case["reading"])
                    self.assertTrue(set(read["verbs"]) <= set(VERB_CLASSES))
                    self.assertTrue(set(read["subjects"]) <= set(SUBJECT_SHAPES))
                    self.assertTrue(set(read["qualifiers"]) <= set(QUALIFIER_SHAPES))
                if "signal" in case:
                    self.assertIn(case["signal"]["trigger"], TRIGGER_SHAPES)

    def test_every_case_is_distinct_and_says_what_it_is_for(self) -> None:
        identifiers = [case["id"] for case in CASES]
        self.assertEqual(len(identifiers), len(set(identifiers)))
        for case in CASES:
            with self.subTest(case=case["id"]):
                self.assertTrue(case["why"].strip())
                self.assertIn("family", case)


class ANewOccasionAndARecoveryAreDecidedAtTheTrigger(unittest.TestCase):
    """Task 6. A wrong reading either re-pays for work or acts on stale work, and both are
    the operator's to decide."""

    def test_the_reading_is_total_over_the_trigger_shapes(self) -> None:
        self.assertEqual(set(READING_BY_TRIGGER), set(TRIGGER_SHAPES))
        self.assertEqual(set(COST_BY_READING), set(OCCASION_READINGS))

    def test_every_corpus_case_takes_the_reading_it_declares(self) -> None:
        for case in family("occasion"):
            signal = cast(dict[str, Any], case["signal"])
            recovering = signal["trigger"] == "recovery"
            record = (
                cast(Any, {"lineage": {"occasion": "20260810T031500Z-a1b2c3d4"}})
                if recovering
                else None
            )
            decided = resolve.decide_occasion(
                OccasionSignal(
                    trigger=signal["trigger"],
                    named_run="20260810T031500Z-a1b2c3d4" if recovering else None,
                    pinned=(
                        "20260810T031500Z-a1b2c3d4"
                        if signal["trigger"] == "pinned"
                        else None
                    ),
                    prior_runs=signal["prior_runs"],
                ),
                record,
            )
            with self.subTest(case=case["id"]):
                self.assertEqual(decided.reading, case["expect"]["reading"])
                self.assertEqual(decided.disclose, case["expect"]["disclose"])

    def test_a_disclosure_states_the_cost_of_the_reading_not_taken(self) -> None:
        decided = resolve.decide_occasion(OccasionSignal(trigger="fresh", prior_runs=3))
        self.assertTrue(decided.disclose)
        self.assertIn("paid for again", decided.taken)
        self.assertIn("skipped", decided.forgone)

    def test_a_scheduled_trigger_always_mints_and_refuses_a_pin(self) -> None:
        """Measured over three firings: an occasion fixed at authoring time makes every
        firing after the first a clean success that did nothing."""
        self.assertEqual(READING_BY_TRIGGER[TRIGGER_SCHEDULED], "new_occasion")
        with self.assertRaises(CairnError):
            resolve.decide_occasion(
                OccasionSignal(trigger=TRIGGER_SCHEDULED, pinned="20260810T031500Z-a1b2c3d4")
            )

    def test_a_recovery_reads_the_occasion_rather_than_inventing_one(self) -> None:
        record = cast(Any, {"lineage": {"occasion": "20260810T031500Z-a1b2c3d4"}})
        decided = resolve.decide_occasion(
            OccasionSignal(trigger="recovery", named_run="r"), record
        )
        self.assertEqual(decided.occasion, "20260810T031500Z-a1b2c3d4")

    def test_a_recovery_of_a_run_that_recorded_no_occasion_refuses(self) -> None:
        """Minting here would present as a recovery while silently re-paying for every
        scoped step — the more expensive wrong answer and the one nobody would see."""
        record = cast(Any, {"lineage": {"occasion": None}})
        with self.assertRaises(CairnError):
            resolve.decide_occasion(
                OccasionSignal(trigger="recovery", named_run="r"), record
            )


class TheTargetRepositoryComesFromTheRequest(unittest.TestCase):
    """Task 8. Never inferred from the workflow, never defaulted to the session's directory."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.repository = self.root / "product"
        self.repository.mkdir()
        subprocess.run(("git", "init", "-q"), cwd=self.repository, check=True)
        self.workflow = self.root / "offline-export.yaml"
        document = cast(dict[str, Any], json.loads(GOLDEN_WORKFLOW.read_text("utf-8")))
        document["params"] = [
            {REPOSITORY_PARAM: str(self.repository)},
            {"CAIRN_PARENT_BRANCH": "main"},
            {"CAIRN_OCCASION": ""},
        ]
        self.workflow.write_text(json.dumps(document), encoding="utf-8")

    def test_nothing_in_the_skill_reads_the_session_directory(self) -> None:
        """The mechanical form of 'never defaulted to the session's directory': the value is
        not reachable rather than merely not used."""
        for path in sorted((PACKAGE_ROOT / "cairn" / "skill").glob("*.py")):
            text = path.read_text(encoding="utf-8")
            with self.subTest(module=path.name):
                for reach in ("Path.cwd", "os.getcwd", "os.curdir", 'default="."'):
                    self.assertNotIn(reach, text)

    def test_a_request_naming_no_repository_asks(self) -> None:
        resolution = resolve.resolve_repository(None, self.workflow)
        self.assertIsInstance(resolution, Unresolved)
        self.assertEqual(cast(Unresolved, resolution).outcome, "absent")

    def test_the_repository_is_never_read_out_of_the_workflow(self) -> None:
        """It encodes one, and that is still not an answer to 'which repository'."""
        resolution = resolve.resolve_repository(None, self.workflow)
        self.assertIsInstance(resolution, Unresolved)
        self.assertNotIn(str(self.repository), cast(Unresolved, resolution).question)

    def test_a_matching_repository_proceeds(self) -> None:
        resolution = resolve.resolve_repository(str(self.repository), self.workflow)
        self.assertIsInstance(resolution, Resolved)
        self.assertEqual(cast(Resolved, resolution).repository, self.repository.resolve())

    def test_a_mismatch_names_both_and_reconciles_neither(self) -> None:
        other = self.root / "other"
        other.mkdir()
        subprocess.run(("git", "init", "-q"), cwd=other, check=True)
        resolution = resolve.resolve_repository(str(other), self.workflow)
        self.assertIsInstance(resolution, Unresolved)
        unresolved = cast(Unresolved, resolution)
        self.assertEqual(unresolved.outcome, "mismatch")
        self.assertIn(str(other), unresolved.question)
        self.assertIn(str(self.repository), unresolved.question)
        self.assertIn("re-authored", unresolved.question)

    def test_the_spelling_that_lands_nothing_is_refused_in_the_conversation(self) -> None:
        with self.assertRaises(CairnError):
            resolve.resolve_repository(f"{self.repository}/", self.workflow)

    def test_a_relative_path_is_refused(self) -> None:
        with self.assertRaises(CairnError):
            resolve.resolve_repository("product", self.workflow)

    def test_a_plan_this_repository_has_no_definition_for_is_answered_in_words(
        self,
    ) -> None:
        """The ordinary mistake — asking to run a plan against a repository it was never
        authored for — is a sentence a person will say, so it gets an answer rather than a
        traceback."""
        absent = self.root / "nowhere" / "offline-export.yaml"
        with self.assertRaises(CairnError) as raised:
            resolve.refuse_missing_definition(absent, "offline-export", str(self.repository))
        self.assertIn("author this plan for this repository", str(raised.exception))

    def test_a_definition_that_no_longer_parses_is_refused_rather_than_read_as_absent(
        self,
    ) -> None:
        """Reading a hand edit as "encodes no repository" would agree with whatever the
        caller named, over a file nobody reviewed."""
        self.workflow.write_text("not json at all", encoding="utf-8")
        with self.assertRaises(CairnError):
            resolve.encoded_repository(self.workflow)

    def test_every_corpus_repository_case_resolves_the_way_it_says(self) -> None:
        other = self.root / "other"
        other.mkdir()
        subprocess.run(("git", "init", "-q"), cwd=other, check=True)
        stated = {
            "repository": str(self.repository),
            "elsewhere": str(other),
            "trailing_separator": f"{self.repository}/",
            None: None,
        }
        for case in family("repository"):
            value = stated[case["stated"]]
            with self.subTest(case=case["id"]):
                if case["expect"]["outcome"] == "refused":
                    with self.assertRaises(CairnError):
                        resolve.resolve_repository(value, self.workflow)
                    continue
                resolution = resolve.resolve_repository(value, self.workflow)
                if case["expect"]["outcome"] == "resolved":
                    self.assertIsInstance(resolution, Resolved)
                else:
                    self.assertIsInstance(resolution, Unresolved)
                    self.assertEqual(
                        cast(Unresolved, resolution).outcome, case["expect"]["outcome"]
                    )


class ARetargetedRunWouldFileItsRecordSomewhereElse(unittest.TestCase):
    """The defect behind task 8, measured, then the guard that closes it.

    The runs root is resolved at authoring time and emitted into `env:`; the repository is a
    parameter every trigger surface can edit. Both derivations in `parameters.repository`
    follow that parameter, so a retarget satisfies them both and the run works in one
    repository while filing everything in the other.
    """

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.authored = self.root / "authored"
        self.other = self.root / "other"
        for path in (self.authored, self.other):
            path.mkdir()
            subprocess.run(("git", "init", "-q"), cwd=path, check=True)

    def test_the_two_existing_derivations_do_not_notice_a_retarget(self) -> None:
        """The measurement. Both agree, because both follow the parameter."""
        environment = {REPOSITORY_PARAM: str(self.other)}
        self.assertEqual(declared_repository(self.other, environment), self.other)

    def test_the_runs_root_check_refuses_it(self) -> None:
        environment = {"CAIRN_RUNS_DIR": str(runs_root(self.authored))}
        with self.assertRaises(Exception) as raised:
            refuse_misfiled_records(self.other, environment)
        self.assertIn(str(self.authored), str(raised.exception))

    def test_a_run_against_the_repository_it_was_authored_for_passes(self) -> None:
        environment = {"CAIRN_RUNS_DIR": str(runs_root(self.authored))}
        refuse_misfiled_records(self.authored, environment)

    def test_the_runs_root_is_judged_at_the_run_s_first_act(self) -> None:
        """The guard is called from `lock acquire`, not merely defined.

        A guard nothing invokes passes its own unit tests for ever, and the engine-driven
        lock tests all point their runs root at a scratch directory — a relocation this
        deliberately allows — so none of them would notice the call site going away.
        """
        called: set[str] = set()
        for node in ast.walk(ast.parse((PACKAGE_ROOT / "cairn" / "__main__.py").read_text("utf-8"))):
            if not (isinstance(node, ast.FunctionDef) and node.name == "_lock"):
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name):
                    called.add(inner.func.id)
        self.assertIn("refuse_misfiled_records", called)

    def test_a_relative_runs_root_is_refused(self) -> None:
        """Resolved against each step's own working directory, so a relative value means a
        different place per step and the run's reports scatter."""
        with self.assertRaises(CairnError):
            refuse_misfiled_records(self.authored, {"CAIRN_RUNS_DIR": "runs"})

    def test_a_repository_whose_admin_directory_is_not_called_git_is_still_covered(
        self,
    ) -> None:
        """A clone made with `--separate-git-dir` has no `.git` component at all, and its
        retarget is the same defect."""
        elsewhere = self.root / "admin" / "product.git" / "cairn" / "runs"
        with self.assertRaises(CairnError):
            refuse_misfiled_records(self.other, {"CAIRN_RUNS_DIR": str(elsewhere)})

    def test_a_runs_root_belonging_to_no_repository_is_a_relocation_and_not_a_retarget(
        self,
    ) -> None:
        """What is refused is a runs root that is *another repository's*. A scratch root is
        somewhere a person deliberately put their records, and refusing it would break every
        harness that isolates them without closing the defect."""
        refuse_misfiled_records(self.other, {"CAIRN_RUNS_DIR": str(self.root / "runs")})


class ExplainAnswersItsThreeQuestions(unittest.TestCase):
    """Task 7, and exit criterion 5: three answers, three sources, nothing started."""

    def test_it_says_what_a_workflow_would_do_without_running_it(self) -> None:
        for recorded in sorted((PACKAGE_ROOT / "fixtures" / "workflows").glob("*.yaml")):
            account = explain.would_do(recorded)
            with self.subTest(workflow=recorded.name):
                self.assertTrue(account.steps)
                self.assertEqual(account.repository, "/srv/work/product")
                self.assertEqual(account.parent_branch, "main")
                for step in account.steps:
                    self.assertTrue(step.node)

    def test_the_account_is_not_the_definition(self) -> None:
        """Tens of kilobytes re-emitted through a conversation is a copy nobody can
        reproduce faithfully, so an account is what a reader gets."""
        account = explain.would_do(GOLDEN_WORKFLOW)
        self.assertNotIn("retry_policy", str(account))

    def test_it_names_a_definition_cairn_did_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            edited = Path(root) / "mixed-kinds.yaml"
            document = cast(dict[str, Any], json.loads(GOLDEN_WORKFLOW.read_text("utf-8")))
            document["steps"][0]["timeout_sec"] = 99
            edited.write_text(json.dumps(document), encoding="utf-8")
            self.assertEqual(
                explain.would_do(edited, "mixed-kinds").provenance.state, "hand_edited"
            )

    def test_every_word_the_record_can_hold_has_a_meaning(self) -> None:
        for word in VERDICT_PRECEDENCE:
            with self.subTest(word=word):
                found = explain.meaning(word)
                self.assertEqual(found.sentences[0], phrases.HEADLINE_BY_VERDICT[word])
                self.assertIsNotNone(found.exit_code)
        for cause in EXCLUSION_CAUSES:
            with self.subTest(cause=cause):
                self.assertTrue(explain.meaning(cause).sentences)

    def test_a_word_in_two_vocabularies_is_answered_for_both(self) -> None:
        found = explain.meaning("not_reached")
        self.assertEqual(len(found.families), 2)

    def test_a_word_no_vocabulary_holds_is_refused_rather_than_guessed_at(self) -> None:
        with self.assertRaises(CairnError):
            explain.meaning("mostly_fine")

    def test_the_exclusion_causes_are_phrased_where_every_other_frozen_word_is(self) -> None:
        self.assertEqual(set(phrases.SENTENCE_BY_CAUSE), set(EXCLUSION_CAUSES))
        self.assertIn(
            (phrases.SENTENCE_BY_CAUSE, EXCLUSION_CAUSES),
            [(mapping, vocabulary) for mapping, vocabulary in phrases.TOTAL_MAPS],
        )

    def test_a_step_that_was_not_excluded_is_said_not_to_have_been(self) -> None:
        record = cast(
            Any,
            {
                "run_id": "r",
                "steps": [
                    {
                        "step_id": "alpha",
                        "outcome": "verified",
                        "overlays": [],
                        "cause": None,
                        "divergence": None,
                        "branch": "step/alpha",
                    }
                ],
            },
        )
        found = explain.why_excluded(record, "alpha")
        self.assertIsNone(found.cause)
        self.assertEqual(found.meaning, explain.NOT_EXCLUDED)

    def test_an_exclusion_carries_the_cause_the_record_holds(self) -> None:
        record = cast(
            Any,
            {
                "run_id": "r",
                "steps": [
                    {
                        "step_id": "alpha",
                        "outcome": "excluded",
                        "overlays": ["divergence"],
                        "cause": "verify_failed",
                        "divergence": {"reported": "done", "asserted": False},
                        "branch": "step/alpha",
                    }
                ],
            },
        )
        found = explain.why_excluded(record, "alpha")
        self.assertEqual(found.cause, "verify_failed")
        self.assertEqual(found.meaning, phrases.SENTENCE_BY_CAUSE["verify_failed"])
        self.assertIn("reported", cast(str, found.divergence))
        self.assertEqual(found.consequence, explain.CONSEQUENCE)

    def test_a_step_the_record_does_not_hold_is_refused(self) -> None:
        record = cast(Any, {"run_id": "r", "steps": []})
        with self.assertRaises(CairnError):
            explain.why_excluded(record, "alpha")

    def test_it_was_never_handed_anything_that_could_start_or_lock(self) -> None:
        imported = _imports(PACKAGE_ROOT / "cairn" / "skill" / "explain.py")
        forbidden = {
            "subprocess",
            "cairn.locks",
            "cairn.providers",
            "cairn.commands",
            "cairn.merge",
            "cairn.worktrees",
            "cairn.skill.trigger",
            "cairn.skill.consent",
        }
        self.assertEqual(imported & forbidden, set())

    def test_all_three_answers_run_no_process_at_all(self) -> None:
        record = cast(Any, {"run_id": "r", "steps": []})
        started = AssertionError("explain started a process")
        with (
            patch("subprocess.run", side_effect=started),
            patch("subprocess.Popen", side_effect=started),
        ):
            explain.would_do(GOLDEN_WORKFLOW)
            explain.meaning("green")
            with self.assertRaises(CairnError):
                explain.why_excluded(record, "alpha")

    def test_explain_is_never_where_an_unmatched_request_lands(self) -> None:
        """A fallback would make the ask list decorative, so no point that asks may be
        answered by Explain instead — and Explain is reached only where a verb asked for
        it."""
        for point in DOMAIN:
            decision = dispatch(point)
            if not isinstance(decision, Selected):
                continue
            if decision.capability != CAPABILITY_EXPLAIN:
                continue
            with self.subTest(point=point):
                self.assertTrue(
                    point.verbs & {VERB_INTERROGATING, VERB_RECOUNTING}
                    or decision.rule.startswith("safe:"),
                    "Explain was selected for a request that asked for neither",
                )


class TheEngineIsLaunchedToOutliveTheCommand(unittest.TestCase):
    """Detachment is four flags, and each one is a way the run dies without it."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.options: dict[str, Any] = {}

    def _factory(self, command: Sequence[str], **options: Any) -> FakeEngine:
        self.options = options
        return FakeEngine()

    def test_the_engine_leaves_the_commands_session_and_process_group(self) -> None:
        """Measured 2026-08-25: a detached start outlived the session that launched it by
        1h18m and the release still gave the repository back. Without this a harness
        killing its process tree, and a hangup on a closing terminal, both reach the run."""
        trigger.launch_detached(("true",), self.root / "engine.log", self._factory)
        self.assertIs(self.options["start_new_session"], True)

    def test_the_engine_holds_no_pipe_the_command_would_have_to_drain(self) -> None:
        """An inherited pipe held open by an orphan is exactly how a parent waits on an EOF
        that never comes, which is the blocking this whole change exists to remove."""
        log = self.root / "engine.log"
        trigger.launch_detached(("true",), log, self._factory)
        self.assertIs(self.options["stdin"], subprocess.DEVNULL)
        self.assertIs(self.options["stderr"], subprocess.STDOUT)
        # A real file rather than a pipe, and the run's own log rather than anywhere else.
        self.assertEqual(Path(self.options["stdout"].name), log)

    def test_the_log_is_appended_so_a_recovery_keeps_what_it_recovers(self) -> None:
        """A run directory is per run and not per attempt, so a recovery against the same
        run id must not delete the evidence of the attempt it continues."""
        log = self.root / "engine.log"
        log.write_text("the first attempt\n", encoding="utf-8")
        trigger.launch_detached(("true",), log, self._factory)
        self.assertIn("the first attempt", log.read_text(encoding="utf-8"))

    def test_the_log_lands_beside_the_run_it_belongs_to(self) -> None:
        where = trigger.address(
            consent.Authorisation(
                offer_id="20260101T000000Z-aaaabbbb",
                plan="offline-export",
                workflow=str(GOLDEN_WORKFLOW),
                repository="/srv/work/product",
                parent_branch="main",
                occasion=None,
                run_id=RUN_ID,
                granted_at="2026-01-01T00:00:00+00:00",
            ),
            RUN_ID,
            self.root,
        )
        self.assertEqual(where.log, self.root / RUN_ID / "engine.log")


class TheCommandLineIsWhatTheSkillActuallyInvokes(unittest.TestCase):
    """The wiring, driven as the skill drives it.

    Every other class here exercises one function. This one runs the argv a capability
    document tells a model to run, because that is the only place the offer, the occasion,
    the repository resolution, the spend and the start are assembled into a sequence — and
    an assembly nothing drives is an assembly nothing checks.
    """

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.repository = self.root / "product"
        self.repository.mkdir()
        subprocess.run(("git", "init", "-q"), cwd=self.repository, check=True)
        self.workflow = workflow_path(self.repository, "offline-export")
        self.workflow.parent.mkdir(parents=True, exist_ok=True)
        document = cast(dict[str, Any], json.loads(GOLDEN_WORKFLOW.read_text("utf-8")))
        document["params"] = [
            {REPOSITORY_PARAM: str(self.repository)},
            {PARENT_BRANCH_PARAM: "main"},
            {"CAIRN_OCCASION": ""},
        ]
        self.workflow.write_text(json.dumps(document), encoding="utf-8")
        self.launched: list[Sequence[str]] = []

    def _said(self, argv: list[str]) -> tuple[int, str]:
        # Kept on the instance as well as returned, so a test can read what has been said
        # *so far* from inside a seam — which is how print order is asserted at all.
        self._spoken = io.StringIO()
        with redirect_stdout(self._spoken), redirect_stderr(self._spoken):
            code = run_main(argv)
        return code, self._spoken.getvalue()

    def _offer(self, *extra: str) -> tuple[int, str]:
        return self._said(
            [
                "offer",
                "--plan",
                "offline-export",
                "--repository",
                str(self.repository),
                "--trigger",
                "fresh",
                *extra,
            ]
        )

    def _start(self, offer_id: str, reply: str) -> tuple[int, str]:
        with (
            patch("cairn.skill.trigger.assert_pinned"),
            patch("cairn.skill.trigger.rehearse_start"),
            patch("cairn.skill.trigger.launch_detached", side_effect=self._record),
            patch("cairn.skill.trigger.engine_holds", return_value=True),
        ):
            return self._said(
                [
                    "start",
                    "--repository",
                    str(self.repository),
                    "--offer",
                    offer_id,
                    "--reply",
                    reply,
                ]
            )

    def _record(self, command: Sequence[str], *_rest: Any) -> FakeEngine:
        self.launched.append(command)
        return FakeEngine()

    def _minted(self, spoken: str) -> str:
        found = re.search(r"^offer\s+(\S+)$", spoken, re.MULTILINE)
        self.assertIsNotNone(found, f"no offer was minted:\n{spoken}")
        return cast(re.Match[str], found).group(1)

    def test_an_offer_states_every_cost_and_hands_back_one_id(self) -> None:
        code, spoken = self._offer()
        self.assertEqual(code, 0)
        for line in consent.disclosure(self.workflow):
            self.assertIn(line, spoken)
        self.assertIn("only if you say so", spoken)
        self.assertTrue(consent.read_offer(self.repository, self._minted(spoken)))

    def test_an_offer_prices_the_branch_the_run_will_land_on(self) -> None:
        _, spoken = self._offer("--parent-branch", "release")
        self.assertIn("lands on release", spoken)
        offered = consent.read_offer(self.repository, self._minted(spoken))
        self.assertIsNotNone(offered)
        self.assertEqual(cast(consent.Offer, offered).parent_branch, "release")

    def test_a_qualifying_yes_starts_the_run_the_offer_priced(self) -> None:
        _, spoken = self._offer("--parent-branch", "release")
        offer_id = self._minted(spoken)
        code, started = self._start(offer_id, "yes, go ahead")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.launched), 1)
        self.assertIn(f"{PARENT_BRANCH_PARAM}=release", " ".join(self.launched[0]))
        self.assertIn("verified work lands on release", started)

    def test_an_engine_that_refuses_to_launch_is_not_reported_as_a_started_run(self) -> None:
        """A run the engine never took on leaves no record, so this is the one engine status
        the command cannot pass over."""
        _, spoken = self._offer()
        with (
            patch("cairn.skill.trigger.assert_pinned"),
            patch("cairn.skill.trigger.rehearse_start"),
            patch(
                "cairn.skill.trigger.launch_detached",
                side_effect=_engine_that_exited(1),
            ),
            patch("cairn.skill.trigger.engine_holds", return_value=False),
        ):
            code, said = self._said(
                [
                    "start",
                    "--repository",
                    str(self.repository),
                    "--offer",
                    self._minted(spoken),
                    "--reply",
                    "yes, go ahead",
                ]
            )
        self.assertEqual(code, 1)
        self.assertIn("without taking the run on", said)
        # The address **is** printed now, and that is the point: a person whose start
        # failed after the offer was spent has the run id and somewhere to look ([19 B]).
        self.assertIn("watch", said)
        self.assertIn("engine.log", said)

    def test_the_identity_is_printed_before_the_engine_is_invoked(self) -> None:
        """The whole of B. A start that blocks for the run is killed by any caller with its
        own timeout, and everything the person needs to name the run died with it."""
        _, spoken = self._offer()
        seen: list[str] = []

        def snapshot(command: Sequence[str], *_rest: Any) -> FakeEngine:
            # Read what has already been printed at the moment the engine is launched.
            seen.append(self._spoken.getvalue())
            self.launched.append(command)
            return FakeEngine()

        with (
            patch("cairn.skill.trigger.assert_pinned"),
            patch("cairn.skill.trigger.rehearse_start"),
            patch("cairn.skill.trigger.launch_detached", side_effect=snapshot),
            patch("cairn.skill.trigger.engine_holds", return_value=True),
        ):
            code, _ = self._said(
                [
                    "start",
                    "--repository",
                    str(self.repository),
                    "--offer",
                    self._minted(spoken),
                    "--reply",
                    "yes, go ahead",
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(len(seen), 1)
        for line in ("started", "branch", "watch", "read"):
            with self.subTest(line=line):
                self.assertIn(line, seen[0])

    def test_the_spent_marker_names_the_run_and_the_invocation_it_bought(self) -> None:
        """A killed start is a spent yes, so the marker has to carry a name a recovery can
        quote — the run id died with the process before ([19 B])."""
        _, spoken = self._offer()
        offer_id = self._minted(spoken)
        self._start(offer_id, "yes, go ahead")
        spent = consent.acceptance_of(self.repository, offer_id)
        self.assertIsNotNone(spent)
        held = cast(consent.Acceptance, spent)
        self.assertTrue(held.run_id)
        self.assertEqual(tuple(held.command), tuple(self.launched[0]))
        # And the claim is exactly as exclusive as it was.
        again = consent.spend(
            self.repository, offer_id, reply="yes, go ahead", run_id=RUN_ID
        )
        self.assertIsInstance(again, consent.Refused)
        self.assertEqual(cast(consent.Refused, again).outcome, "already_spent")

    def test_a_second_acceptance_is_told_which_run_the_first_one_bought(self) -> None:
        _, spoken = self._offer()
        offer_id = self._minted(spoken)
        self._start(offer_id, "yes, go ahead")
        refused = cast(
            consent.Refused,
            consent.spend(self.repository, offer_id, reply="yes", run_id=RUN_ID),
        )
        spent = cast(consent.Acceptance, consent.acceptance_of(self.repository, offer_id))
        self.assertIn(spent.run_id, refused.why)

    def test_a_start_returns_once_the_engine_has_the_run_without_waiting_for_it(
        self,
    ) -> None:
        """Detached is the default: what is waited for is the engine taking the run on."""
        engines: list[FakeEngine] = []

        def launched(command: Sequence[str], *_rest: Any) -> FakeEngine:
            self.launched.append(command)
            engines.append(FakeEngine())
            return engines[-1]

        _, spoken = self._offer()
        held = [False, True]
        with (
            patch("cairn.skill.trigger.assert_pinned"),
            patch("cairn.skill.trigger.rehearse_start"),
            patch("cairn.skill.trigger.launch_detached", side_effect=launched),
            patch("cairn.skill.trigger.engine_holds", side_effect=_answers(held)),
            patch("cairn.skill.trigger.time.sleep"),
        ):
            code, said = self._said(
                [
                    "start",
                    "--repository",
                    str(self.repository),
                    "--offer",
                    self._minted(spoken),
                    "--reply",
                    "yes, go ahead",
                ]
            )
        self.assertEqual(code, 0)
        self.assertFalse(engines[0].waited, "a detached start waited for the whole run")
        self.assertNotIn("engine   exited", said)

    def test_wait_returns_the_engines_status_and_says_that_it_blocked(self) -> None:
        engines: list[FakeEngine] = []

        def launched(command: Sequence[str], *_rest: Any) -> FakeEngine:
            self.launched.append(command)
            engines.append(FakeEngine())
            return engines[-1]

        _, spoken = self._offer()
        with (
            patch("cairn.skill.trigger.assert_pinned"),
            patch("cairn.skill.trigger.rehearse_start"),
            patch("cairn.skill.trigger.launch_detached", side_effect=launched),
            patch("cairn.skill.trigger.engine_holds", return_value=True),
        ):
            code, said = self._said(
                [
                    "start",
                    "--repository",
                    str(self.repository),
                    "--offer",
                    self._minted(spoken),
                    "--reply",
                    "yes, go ahead",
                    "--wait",
                ]
            )
        self.assertEqual(code, 0)
        self.assertTrue(engines[0].waited)
        self.assertIn("engine   exited", said)

    def test_an_engine_still_starting_is_a_caution_and_not_a_refusal(self) -> None:
        """Neither registered nor exited. Killing a run the offer has already paid for, on
        a timer, is the one destructive move available here — so it is not made."""
        _, spoken = self._offer()
        with (
            patch("cairn.skill.trigger.assert_pinned"),
            patch("cairn.skill.trigger.rehearse_start"),
            patch(
                "cairn.skill.trigger.launch_detached",
                side_effect=_engine_that_exited(None),
            ),
            patch("cairn.skill.trigger.engine_holds", return_value=False),
            patch("cairn.skill.trigger.TAKEN_ON_TIMEOUT", 0.0),
        ):
            code, said = self._said(
                [
                    "start",
                    "--repository",
                    str(self.repository),
                    "--offer",
                    self._minted(spoken),
                    "--reply",
                    "yes, go ahead",
                ]
            )
        self.assertEqual(code, 0)
        self.assertIn("has not registered", said)
        self.assertNotIn("refused", said)

    def test_a_branch_the_engine_could_not_carry_is_refused_when_the_offer_is_made(
        self,
    ) -> None:
        """Refused at offer time, not after the acceptance is spent: a person must not lose
        their yes to a value they gave before it."""
        code, said = self._offer("--parent-branch", "my branch")
        self.assertEqual(code, 1)
        self.assertIn("whitespace", said)
        self.assertFalse(consent.offers_directory(self.repository).exists())

    def test_a_start_mints_its_own_run_id_rather_than_asking_for_one(self) -> None:
        _, spoken = self._offer()
        self._start(self._minted(spoken), "yes, go ahead")
        check_run_id(self.launched[0][self.launched[0].index("--run-id") + 1])

    def test_every_refusal_exits_nonzero_and_says_which_clause_stopped_it(self) -> None:
        _, spoken = self._offer()
        offer_id = self._minted(spoken)
        for offer, reply, outcome in (
            (offer_id, "", "no_words"),
            ("20200101T000000Z-deadbeef", "yes, run it", "no_such_offer"),
        ):
            with self.subTest(outcome=outcome):
                code, said = self._start(offer, reply)
                self.assertEqual(code, 1)
                self.assertIn(outcome, said)
                self.assertEqual(self.launched, [])

    def test_a_plan_with_no_definition_here_is_answered_rather_than_crashed_on(
        self,
    ) -> None:
        code, said = self._said(
            [
                "offer",
                "--plan",
                "never-authored",
                "--repository",
                str(self.repository),
                "--trigger",
                "fresh",
            ]
        )
        self.assertEqual(code, 1)
        self.assertIn("author this plan for this repository", said)

    def test_a_recovery_with_no_run_named_says_which_flag_is_missing(self) -> None:
        code, said = self._said(
            [
                "offer",
                "--plan",
                "offline-export",
                "--repository",
                str(self.repository),
                "--trigger",
                "recovery",
            ]
        )
        self.assertEqual(code, 1)
        self.assertIn("--recovering", said)

    def test_an_occasion_given_to_a_trigger_that_continues_none_is_refused(self) -> None:
        code, said = self._said(
            [
                "offer",
                "--plan",
                "offline-export",
                "--repository",
                str(self.repository),
                "--trigger",
                "fresh",
                "--occasion",
                "20260810T031500Z-a1b2c3d4",
            ]
        )
        self.assertEqual(code, 1)
        self.assertIn("--trigger pinned", said)

    def test_explain_answers_from_the_command_line_and_starts_no_run(self) -> None:
        """Reading a repository asks git where its admin directory is, which is a read. What
        Explain may never launch is the engine."""
        spoken = io.StringIO()
        launched: list[Sequence[str]] = []
        real = subprocess.run

        def watched(command: Sequence[str], *arguments: Any, **options: Any) -> Any:
            launched.append(list(command))
            return cast(Any, real)(command, *arguments, **options)

        with redirect_stdout(spoken), patch("subprocess.run", watched):
            self.assertEqual(
                explain_main(
                    [
                        "workflow",
                        "--plan",
                        "offline-export",
                        "--repository",
                        str(self.repository),
                    ]
                ),
                0,
            )
            self.assertEqual(explain_main(["word", "green_with_exclusions"]), 0)
        said = spoken.getvalue()
        self.assertIn("lock_acquire", said)
        self.assertIn("not a clean success", said)
        self.assertEqual([command for command in launched if command[0] == ENGINE_BINARY], [])


class TheInstalledSurfaceIsMeasuredAndPublished(unittest.TestCase):
    """Task 10 and D6. The figure is recomputed every run, so it cannot go stale quietly."""

    def test_the_readme_publishes_the_figure_the_measurement_returns(self) -> None:
        measured = surface.published(surface.measure(PACKAGE_ROOT))
        self.assertIn(
            measured,
            README.read_text(encoding="utf-8"),
            "README.md does not carry the measured surface cost. Run "
            f"`python3 -m scripts.measure_surface` and paste:\n\n{measured}",
        )

    def test_a_line_added_to_the_surface_moves_the_measurement(self) -> None:
        """Proves `measure` reads the artifact rather than returning a constant."""
        with tempfile.TemporaryDirectory() as root:
            copy = Path(root) / "cairn"
            shutil.copytree(PACKAGE_ROOT, copy, ignore=shutil.ignore_patterns("*cache*"))
            before = surface.measure(copy)
            with (copy / surface.SKILL_FILE).open("a", encoding="utf-8") as handle:
                handle.write("one more line\n")
            self.assertNotEqual(surface.measure(copy).on_trigger, before.on_trigger)

    def test_the_published_figure_says_how_it_was_counted(self) -> None:
        block = surface.published(surface.measure(PACKAGE_ROOT))
        self.assertIn("estimate", block)
        self.assertIn(str(surface.CHARACTERS_PER_TOKEN), block)

    def test_the_surface_stays_inside_its_declared_budget(self) -> None:
        measured = surface.measure(PACKAGE_ROOT)
        self.assertLessEqual(
            measured.described.characters, surface.DESCRIPTION_CHARACTER_BUDGET
        )
        self.assertLessEqual(
            measured.on_trigger.characters, surface.ON_TRIGGER_CHARACTER_BUDGET
        )

    def test_the_description_names_every_capability_it_claims_to_reach(self) -> None:
        """A capability the description never mentions is one nobody reaches by asking."""
        described = surface.description(SKILL).casefold()
        for capability in CAPABILITY_ORDER:
            with self.subTest(capability=capability):
                self.assertIn(capability, described)

    def test_the_script_and_the_test_compute_the_same_block(self) -> None:
        self.assertEqual(measure_surface_block(), surface.published(surface.measure(PACKAGE_ROOT)))


class TheSkillMintsItsVocabularyInOneModuleOnly(unittest.TestCase):
    """The third of these, beside the record's and the report's, for the same reason."""

    def test_no_second_module_names_a_capability_or_a_shape(self) -> None:
        pattern = re.compile(
            r"^(CAPABILITY|VERB|SHAPE|FAMILY|TRIGGER|OCCASION|CONSENT|BINDING|COST)_[A-Z0-9_]+ = ",
            re.MULTILINE,
        )
        holders = {
            path.relative_to(PACKAGE_ROOT).as_posix()
            for path in (PACKAGE_ROOT / "cairn" / "skill").rglob("*.py")
            if pattern.search(path.read_text(encoding="utf-8"))
        }
        self.assertEqual(holders, {"cairn/skill/vocabulary.py"})

    def test_the_ask_reasons_are_minted_beside_the_rules_they_complement(self) -> None:
        pattern = re.compile(r"^ASK_[A-Z0-9_]+ = ", re.MULTILINE)
        holders = {
            path.relative_to(PACKAGE_ROOT).as_posix()
            for path in (PACKAGE_ROOT / "cairn").rglob("*.py")
            if pattern.search(path.read_text(encoding="utf-8"))
        }
        self.assertEqual(holders, {"cairn/skill/dispatch.py"})


if __name__ == "__main__":
    unittest.main()
