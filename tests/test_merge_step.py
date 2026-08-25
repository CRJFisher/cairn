"""Doc 10's merge step: ordered, one at a time, halting rather than guessing.

The proof of a merge is git, so most of this suite drives real repositories. What is pure —
the prediction's three answers, the marker rule, the exclusion table, the ordering bound and
the proof itself — is unit tested, because those are the cases that otherwise only appear in
a workflow that reports success.
"""

import json
import os
import re
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, ClassVar
from unittest.mock import patch

from cairn.core import EXIT_FAILED, EXIT_OK, CairnError, CommandResult, RuntimeContext
from cairn.emitters import emit_node
from cairn.gitio import git, is_ancestor, resolve_ref
from cairn.layout import reports_directory
from cairn.locks import unresolved_merge
from cairn.merge import (
    BY_AGENT,
    BY_COMMAND,
    CLEAN,
    CONFLICT_MARKERS,
    CONFLICTED,
    EXCLUDED,
    MERGE_TASK,
    MERGEABLE,
    NOTHING_TO_MERGE,
    UNAVAILABLE,
    MergeFacts,
    Prediction,
    changed_paths,
    classify_candidate,
    classify_prediction,
    committed_markers,
    judge_merge,
    landing_order,
    merge_prompt,
    owned_paths,
    predict,
    run_merge,
    scan_markers,
    unowned_conflicts,
    verify_landed,
)
from cairn.plan.schema import (
    MERGE_TIMEOUT,
    RESERVED_ID_PREFIXES,
    SUPPORT_TIMEOUT,
    normalise,
)
from cairn.topology import derive, merge_provider
from cairn.verify import EXCLUSION_CAUSES, mark_name
from cairn.workflow.build import envelope

# The engine mints a run id when none is given, and a run's records are keyed by it.
ENGINE_RUN_ID = "merge-engine-run"

def reports_of(root: Path, run_id: str = "run-1") -> Path:
    """Where a run's accounts land, composed the way a step composes it for itself."""
    return reports_directory(root / "runs", run_id)


CAIRN_ROOT = Path(__file__).resolve().parent.parent
MERGE_DOC = CAIRN_ROOT / "docs" / "merge-step.md"
FIXTURES = CAIRN_ROOT / "fixtures" / "plans"
REPOSITORY = Path("/repo")
PARENT = "main"

OID = "0" * 40
NODE_STATUS = re.compile(r"^[├└│─ ]+([a-z_0-9]+)(?: \([^)]*\))? \[(\w+)\]", re.MULTILINE)


def fixture(name: str) -> Any:
    with open(FIXTURES / name / "graph.json", encoding="utf-8") as handle:
        return normalise(json.load(handle))


def topology(name: str) -> Any:
    return derive(fixture(name), repository_root=REPOSITORY, parent_branch=PARENT)


def by_name(derived: Any, name: str) -> Any:
    return next(node for node in derived["nodes"] if node["name"] == name)


def emitted(derived: Any, name: str, graph: Any) -> Any:
    return emit_node(
        by_name(derived, name),
        steps={step["id"]: step for step in graph["steps"]},
        run_timeout_seconds=derived["max_seconds"],
    )


def prediction(left: str, right: str, *paths: str) -> Prediction:
    outcome = CONFLICTED if paths else CLEAN
    return Prediction(left, right, outcome, paths, "")


# ---------------------------------------------------------------------------
# Task 1 — the merge prompt
# ---------------------------------------------------------------------------


class TheMergePrompt(unittest.TestCase):
    def test_the_prompt_names_the_branch_it_lands_and_the_branch_it_lands_on(self) -> None:
        text = merge_prompt("step/theme", "main", ["a.txt"])
        self.assertIn("step/theme", text)
        self.assertIn("main", text)
        self.assertIn("a.txt", text)

    def test_the_prompt_asks_for_both_sides_intent_rather_than_a_winner(self) -> None:
        text = merge_prompt("step/a", "main", ["a.txt"])
        self.assertIn("both sides", text)
        self.assertIn("Never resolve a conflict by taking one side wholesale", text)

    def test_the_prompt_asks_for_every_marker_stripped(self) -> None:
        self.assertIn("Leave no conflict marker anywhere", merge_prompt("step/a", "main", []))

    def test_the_prompt_forbids_the_abort_that_would_not_converge(self) -> None:
        """An abort leaves the branch unmerged, so the next run stops in the same place."""
        text = merge_prompt("step/a", "main", [])
        self.assertIn("git merge --abort", text)
        self.assertIn("the next run stops in the same place", text)

    def test_the_prompt_is_reproduced_in_its_document(self) -> None:
        quoted = MERGE_DOC.read_text(encoding="utf-8").replace("\n> ", "\n").replace("\n>", "\n")
        flowed = " ".join(quoted.split())
        for sentence in (
            "Resolve them so that the intended change from both sides survives.",
            "Leave no conflict marker anywhere, in any file",
            "Leave the merge exactly as you found it.",
            "Never resolve a conflict by taking one side wholesale to make the merge pass.",
        ):
            self.assertIn(sentence, flowed)
            self.assertIn(sentence, " ".join(MERGE_TASK.split()))

    def test_the_prompt_is_stated_in_one_place_and_reproduced_in_one_other(self) -> None:
        needle = "Never resolve a conflict by taking one side wholesale"
        holders = {
            path.name
            for pattern in ("cairn/**/*.py", "docs/*.md", "README.md")
            for path in CAIRN_ROOT.glob(pattern)
            if needle in path.read_text(encoding="utf-8")
        }
        self.assertEqual(holders, {"merge.py", "merge-step.md"})

    def test_the_prediction_is_never_handed_to_the_agent(self) -> None:
        """It would invite trusting the prediction over the tree, which the proof catches."""
        text = merge_prompt("step/a", "main", ["a.txt"])
        for word in ("merge-tree", "predict", "predicted"):
            self.assertNotIn(word, text)


# ---------------------------------------------------------------------------
# Task 2 — the chain, and task 10's ordering bound in the emission
# ---------------------------------------------------------------------------


class TheChainTheTopologyEmits(unittest.TestCase):
    def test_a_slot_names_every_candidate_because_it_chooses_between_them(self) -> None:
        graph = fixture("fan-out")
        body = emitted(topology("fan-out"), "merge_w2_1", graph)["run"]
        self.assertIn("merge land --slot 1", body)
        self.assertIn("--branch step/keymap_reader", body)
        self.assertIn("--branch step/theme_reader", body)
        # The branch it lands into is a parameter, so it reaches the slot through the
        # environment rather than through a body that would name one repository.
        self.assertNotIn("--into", body)

    def test_every_slot_is_followed_by_its_own_proof(self) -> None:
        fan = topology("fan-out")
        roles = [
            (node["name"], node["after"])
            for node in fan["nodes"]
            if node["role"] in ("merge", "verify") and node["step"] is None
        ]
        self.assertEqual(
            roles,
            [
                ("merge_w2_1", ["join_w2"]),
                ("verify_merge_w2_1", ["merge_w2_1"]),
                ("merge_w2_2", ["verify_merge_w2_1"]),
                ("verify_merge_w2_2", ["merge_w2_2"]),
            ],
        )

    def test_the_proof_reads_the_slot_it_proves_by_name(self) -> None:
        graph = fixture("fan-out")
        body = emitted(topology("fan-out"), "verify_merge_w2_1", graph)["run"]
        self.assertIn("merge verify --merge merge_w2_1", body)

    def test_a_slot_is_bounded_by_the_session_it_may_have_to_pay_for(self) -> None:
        """At a support step's bound the engine would kill a resolution mid-merge."""
        graph = fixture("fan-out")
        fan = topology("fan-out")
        self.assertEqual(emitted(fan, "merge_w2_1", graph)["timeout_sec"], MERGE_TIMEOUT)
        self.assertGreater(MERGE_TIMEOUT, SUPPORT_TIMEOUT)
        self.assertEqual(emitted(fan, "verify_merge_w2_1", graph)["timeout_sec"], SUPPORT_TIMEOUT)

    def test_the_topology_prices_a_slot_as_the_session_too(self) -> None:
        """The run's own maximum and the lock lease derive from the node, not the body."""
        fan = topology("fan-out")
        self.assertEqual(by_name(fan, "merge_w2_1")["max_seconds"], MERGE_TIMEOUT)
        self.assertEqual(by_name(fan, "verify_merge_w2_1")["max_seconds"], SUPPORT_TIMEOUT)

    def test_a_wave_of_merges_reaches_the_runs_maximum_duration(self) -> None:
        """A lease derived from a merge priced as git would come free mid-resolution."""
        self.assertGreaterEqual(topology("fan-out")["max_seconds"], MERGE_TIMEOUT * 2)

    def test_a_slot_is_never_retried(self) -> None:
        graph = fixture("fan-out")
        policy = emitted(topology("fan-out"), "merge_w2_1", graph)["retry_policy"]
        self.assertEqual(policy, {"limit": 0, "interval_sec": 1})

    def test_no_node_in_the_merge_chain_can_be_skipped_over(self) -> None:
        """A halt has to stop the slots behind it and the prune after them."""
        graph = fixture("fan-out")
        fan = topology("fan-out")
        for name in ("merge_w2_1", "verify_merge_w2_1", "merge_w2_2", "verify_merge_w2_2"):
            self.assertNotIn("continue_on", emitted(fan, name, graph), name)

    def test_a_plan_of_commands_still_names_an_agent_to_resolve_a_conflict(self) -> None:
        """A conflict is a question about intent whatever the steps that caused it were."""
        self.assertEqual(merge_provider("command"), "claude")
        self.assertEqual(merge_provider("agent.echo"), "echo")

    def test_a_step_id_in_the_namespace_the_slots_use_is_refused(self) -> None:
        """Otherwise a step called `w2_1` and a slot's proof are one node in the workflow."""
        self.assertIn("merge_", RESERVED_ID_PREFIXES)


# ---------------------------------------------------------------------------
# Task 3 and 11 — the prediction advises and never gates
# ---------------------------------------------------------------------------


class ThePredictionHasThreeAnswers(unittest.TestCase):
    """Measured against git 2.42.1, where exit status alone does not carry the answer."""

    def test_a_clean_merge_predicts_clean(self) -> None:
        self.assertEqual(classify_prediction("a", "b", 0, OID, "").outcome, CLEAN)

    def test_a_conflict_predicts_the_files_it_names(self) -> None:
        stdout = f"{OID}\nf.txt\ng.txt\n\nAuto-merging f.txt\nCONFLICT (content): f.txt"
        predicted = classify_prediction("a", "b", 1, stdout, "")
        self.assertEqual(predicted.outcome, CONFLICTED)
        self.assertEqual(predicted.paths, ("f.txt", "g.txt"))

    def test_a_ref_that_does_not_resolve_is_not_a_conflict_in_every_file(self) -> None:
        """It exits 1 exactly as a conflict does; the object id is the only discriminator."""
        predicted = classify_prediction("a", "b", 1, "", "not something we can merge")
        self.assertEqual(predicted.outcome, UNAVAILABLE)
        self.assertEqual(predicted.paths, ())

    def test_unrelated_histories_are_unavailable_rather_than_clean(self) -> None:
        self.assertEqual(
            classify_prediction("a", "b", 128, "", "refusing to merge").outcome, UNAVAILABLE
        )

    def test_a_conflict_with_no_named_file_is_still_a_conflict(self) -> None:
        """git's own note: a merge can conflict without any individual file conflicting."""
        predicted = classify_prediction("a", "b", 1, f"{OID}\n\nCONFLICT (rename/rename)", "")
        self.assertEqual(predicted.outcome, CONFLICTED)
        self.assertEqual(predicted.paths, ())


class ThePredictionOrdersTheChain(unittest.TestCase):
    def test_the_heaviest_overlap_lands_last(self) -> None:
        order = landing_order(
            ["step/a", "step/b", "step/c"],
            [
                prediction("step/a", "step/b", "f.txt", "g.txt"),
                prediction("step/a", "step/c"),
                prediction("step/b", "step/c"),
            ],
        )
        self.assertEqual(order[-1], "step/b")
        self.assertEqual(order[0], "step/c")

    def test_a_conflict_git_named_no_file_for_still_outweighs_a_clean_pair(self) -> None:
        """An empty conflicted-file list is not a clean merge, so it cannot sort as one."""
        nameless = Prediction("step/a", "step/b", CONFLICTED, (), "")
        order = landing_order(["step/a", "step/c"], [nameless, prediction("step/a", "step/c")])
        self.assertEqual(order, ["step/c", "step/a"])

    def test_an_unadvised_wave_still_lands_in_a_settled_order(self) -> None:
        """Two runs of one plan must be comparable, so no order is ever arbitrary."""
        self.assertEqual(landing_order(["step/b", "step/a"], []), ["step/a", "step/b"])

    def test_a_predicted_conflict_no_step_of_the_wave_changes_is_a_plan_defect(self) -> None:
        unowned = unowned_conflicts(
            [prediction("step/a", "step/b", "f.txt", "vendor/lib.js")], ["f.txt"]
        )
        self.assertEqual(unowned, ["vendor/lib.js"])

    def test_an_unavailable_prediction_names_nothing_as_unowned(self) -> None:
        """Advice git could not give never refuses a merge."""
        absent = Prediction("step/a", "step/b", UNAVAILABLE, (), "boom")
        self.assertEqual(unowned_conflicts([absent], []), [])


# ---------------------------------------------------------------------------
# Task 4 and 9 — the proof, and the merges that lie to it
# ---------------------------------------------------------------------------


def facts(**overrides: Any) -> MergeFacts:
    base: dict[str, Any] = {
        "into": "main",
        "landed": "step/a",
        "before": "aaa",
        "after": "bbb",
        "ancestor": True,
        "pending": None,
        "dirty": (),
        "changed": ("f.txt",),
        "marked": (),
    }
    return MergeFacts(**{**base, **overrides})


class TheProof(unittest.TestCase):
    def test_a_merge_that_landed_is_proven(self) -> None:
        verdict = judge_merge(facts())
        self.assertTrue(verdict["proven"])
        self.assertIsNone(verdict["cause"])

    def test_a_branch_that_is_not_an_ancestor_was_never_landed(self) -> None:
        """The lying merge: an agent that reports done without merging."""
        verdict = judge_merge(facts(ancestor=False))
        self.assertFalse(verdict["proven"])
        self.assertEqual(verdict["cause"], "merge_not_landed")

    def test_a_merge_still_in_progress_is_not_a_merge(self) -> None:
        verdict = judge_merge(facts(pending="a merge"))
        self.assertFalse(verdict["proven"])
        self.assertEqual(verdict["cause"], "merge_conflict")

    def test_an_unclean_tree_after_the_merge_is_not_proven(self) -> None:
        verdict = judge_merge(facts(dirty=("f.txt",)))
        self.assertFalse(verdict["proven"])
        self.assertEqual(verdict["cause"], "repository_dirty")

    def test_a_committed_conflict_marker_is_not_proven(self) -> None:
        verdict = judge_merge(facts(marked=("f.txt",)))
        self.assertFalse(verdict["proven"])
        self.assertEqual(verdict["cause"], "conflict_markers_committed")

    def test_every_check_is_reported_whichever_one_failed(self) -> None:
        for broken in (
            facts(),
            facts(ancestor=False),
            facts(pending="a merge"),
            facts(dirty=("f.txt",)),
            facts(marked=("f.txt",)),
        ):
            self.assertEqual(
                set(judge_merge(broken)["checks"]),
                {"ancestry", "settled", "clean_tree", "no_conflict_markers"},
            )

    def test_a_check_git_would_not_answer_closes_the_proof_rather_than_passing_it(self) -> None:
        """Absence of evidence is scored as absence of proof, as it is at the verify gate."""
        for unanswerable in (facts(dirty=None), facts(marked=None), facts(changed=None)):
            verdict = judge_merge(unanswerable)
            self.assertFalse(verdict["proven"])
            self.assertEqual(verdict["cause"], "merge_indeterminate")

    def test_a_check_that_was_never_made_is_not_recorded_as_one_that_failed(self) -> None:
        """`no_conflict_markers: False` would say markers were found by a scan that never ran."""
        self.assertIsNone(judge_merge(facts(marked=None))["checks"]["no_conflict_markers"])
        self.assertIsNone(judge_merge(facts(dirty=None))["checks"]["clean_tree"])
        # The checks that were made still read as they were made.
        self.assertTrue(judge_merge(facts(dirty=None))["checks"]["settled"])
        self.assertIs(judge_merge(facts(marked=("f.txt",)))["checks"]["no_conflict_markers"], False)


class TheMarkerScanReadsMarkersAndNotProse(unittest.TestCase):
    def test_a_file_carrying_both_markers_is_flagged(self) -> None:
        content = "<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> step/a\n"
        self.assertEqual(scan_markers([("f.txt", content)]), ["f.txt"])

    def test_a_setext_underline_is_not_a_conflict_marker(self) -> None:
        """`=======` on its own line is a Markdown heading, and git writes no label after it."""
        self.assertEqual(scan_markers([("doc.md", "Title\n=======\n\nbody\n")]), [])

    def test_prose_about_conflicts_without_the_labels_git_writes_is_not_flagged(self) -> None:
        text = "A conflict looks like <<<<<<< and ======= and >>>>>>> in the file.\n"
        self.assertEqual(scan_markers([("doc.md", text)]), [])

    def test_an_opening_marker_alone_is_not_evidence(self) -> None:
        self.assertEqual(scan_markers([("f.txt", "<<<<<<< HEAD\nours\n")]), [])

    def test_a_marker_with_no_label_after_it_is_documentation_and_not_a_conflict(self) -> None:
        """git always writes a label; a doc showing the syntax bare is not a conflict."""
        shown = "A conflict looks like:\n\n<<<<<<<\nours\n=======\ntheirs\n>>>>>>>\n"
        self.assertEqual(scan_markers([("guide.md", shown)]), [])

    def test_the_labels_git_writes_are_what_the_scan_keys_on(self) -> None:
        for marker in CONFLICT_MARKERS:
            self.assertTrue(marker.endswith(" "), marker)


# ---------------------------------------------------------------------------
# Task 5 — exclusions carry the gate's own cause
# ---------------------------------------------------------------------------


class AnExcludedBranchIsNamed(unittest.TestCase):
    def test_a_branch_with_work_is_available_to_land(self) -> None:
        candidate = classify_candidate("step/a", "a", True, True, None, "")
        self.assertEqual(candidate.disposition, MERGEABLE)

    def test_a_branch_that_was_never_created_never_ran(self) -> None:
        candidate = classify_candidate("step/a", "a", False, False, None, "")
        self.assertEqual(candidate.disposition, EXCLUDED)
        self.assertEqual(candidate.cause, "not_reached")

    def test_a_branch_whose_gate_closed_carries_the_gates_own_cause(self) -> None:
        candidate = classify_candidate("step/a", "a", True, False, "verify_failed", "it failed")
        self.assertEqual(candidate.disposition, EXCLUDED)
        self.assertEqual(candidate.cause, "verify_failed")

    def test_a_branch_already_contained_in_the_parent_is_not_an_exclusion(self) -> None:
        """Work that landed on an earlier run is a no-op, not a step that contributed nothing."""
        candidate = classify_candidate("step/a", "a", True, False, None, "")
        self.assertEqual(candidate.disposition, NOTHING_TO_MERGE)
        self.assertIsNone(candidate.cause)

    def test_the_merge_mints_no_exclusion_cause_of_its_own(self) -> None:
        for cause in ("not_reached", "gate_indeterminate", "verify_failed"):
            self.assertIn(cause, EXCLUSION_CAUSES)


# ---------------------------------------------------------------------------
# The rest drives real repositories.
# ---------------------------------------------------------------------------


class RepositoryCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.repository = self.root / "repo"
        self.repository.mkdir(parents=True)
        git(self.repository, ("init", "--initial-branch=main", "--quiet", "."))
        git(self.repository, ("config", "user.email", "cairn@test"))
        git(self.repository, ("config", "user.name", "Cairn Test"))
        self.write("shared.txt", "one\ntwo\nthree\n")
        self.commit("root")
        self.reports = reports_of(self.root)
        self.reports.mkdir(parents=True)
        self.context = RuntimeContext(
            run_id="run-1",
            step_id="merge_w1_1",
            working_directory=self.repository,
            report_path=self.reports / "merge_w1_1.json",
            runs_root=self.reports.parent.parent,
        )
        self.calls: list[str] = []
        self.passed: tuple[Any, ...] = ()

    def write(self, name: str, body: str) -> None:
        (self.repository / name).write_text(body, encoding="utf-8")

    def commit(self, message: str) -> str:
        git(self.repository, ("add", "--all"))
        git(self.repository, ("commit", "--quiet", "-m", message))
        return git(self.repository, ("rev-parse", "HEAD")).stdout

    def branch(self, name: str, filename: str, body: str) -> None:
        """A step's branch, and the gate report a run that verified it always leaves."""
        git(self.repository, ("checkout", "--quiet", "-b", name, "main"))
        self.write(filename, body)
        self.commit(f"work on {name}")
        git(self.repository, ("checkout", "--quiet", "main"))
        self.verified(name.split("/", 1)[-1])

    def verified(self, step: str) -> None:
        """The report `mark_<step>` leaves when the gate opened and recorded the work."""
        (self.reports / f"{mark_name(step)}.json").write_text(
            json.dumps(
                {"step_id": step, "run_id": "run-1", "status": "done", "cause": None,
                 "summary": "recorded", "needs_user_decision": False}
            ),
            encoding="utf-8",
        )

    def gate_report(self, step: str, cause: str, summary: str = "the assertion failed") -> None:
        (self.reports / f"{mark_name(step)}.json").write_text(
            json.dumps(
                {"step_id": step, "run_id": "run-1", "status": "failed", "cause": cause,
                 "summary": summary, "needs_user_decision": False}
            ),
            encoding="utf-8",
        )

    def subjects(self) -> list[str]:
        return git(self.repository, ("log", "--format=%s")).stdout.splitlines()

    def land(self, candidates: list[str], **kwargs: Any) -> CommandResult:
        return run_merge(
            self.repository,
            slot=kwargs.pop("slot", 1),
            into="main",
            candidates=candidates,
            provider="stub",
            model=kwargs.pop("model", None),
            max_budget_usd=kwargs.pop("max_budget_usd", None),
            context=self.context,
            run_agent=kwargs.pop("run_agent", self.refusing_agent),
        )

    def refusing_agent(self, *args: Any, **kwargs: Any) -> CommandResult:
        self.calls.append(str(args[1]) if len(args) > 1 else "")
        return CommandResult(
            EXIT_FAILED, "failed", "the two intentions contradict", [], False,
            "reported_failure", {},
        )

    def recording_agent(self, *args: Any, **kwargs: Any) -> CommandResult:
        """A resolver that keeps the argv slots the model and the ceiling travel in."""
        self.passed = args
        return self.resolving_agent(*args, **kwargs)

    def resolving_agent(self, *args: Any, **kwargs: Any) -> CommandResult:
        """A genuine resolution: keep both sides, then complete the merge."""
        self.calls.append(str(args[1]) if len(args) > 1 else "")
        self.write("shared.txt", "one\nfrom-a\nfrom-b\nthree\n")
        git(self.repository, ("add", "--all"))
        git(self.repository, ("commit", "--quiet", "--no-edit"))
        return CommandResult(EXIT_OK, "done", "resolved", [], False, None, {})


class TheCallersCeilingReachesTheResolvingSession(RepositoryCase):
    """`merge land` takes a model and a ceiling, and they are the only bound on that session.

    Until this existed, `run_merge` could accept both and pass `None, None` through to the
    provider with the whole free suite still green — so every production merge session ran
    unbounded on whatever the CLI defaulted to, which is what the flags were added to stop.
    """

    def test_the_model_and_the_ceiling_arrive_in_the_provider_call(self) -> None:
        self.branch("step/a", "shared.txt", "one\nfrom-a\nthree\n")
        self.branch("step/b", "shared.txt", "one\nfrom-b\nthree\n")
        self.land(["step/a", "step/b"], slot=1, run_agent=self.recording_agent)
        self.land(
            ["step/a", "step/b"],
            slot=2,
            model="claude-haiku-4-5-20251001",
            max_budget_usd=0.25,
            run_agent=self.recording_agent,
        )
        self.assertEqual(self.passed[4], "claude-haiku-4-5-20251001")
        self.assertEqual(self.passed[5], 0.25)

    def test_a_caller_naming_neither_leaves_the_provider_to_its_own_defaults(self) -> None:
        self.branch("step/a", "shared.txt", "one\nfrom-a\nthree\n")
        self.branch("step/b", "shared.txt", "one\nfrom-b\nthree\n")
        self.land(["step/a", "step/b"], slot=1, run_agent=self.recording_agent)
        self.land(["step/a", "step/b"], slot=2, run_agent=self.recording_agent)
        self.assertIsNone(self.passed[4])
        self.assertIsNone(self.passed[5])


class ARealConflictLandsAndIsProven(RepositoryCase):
    def setUp(self) -> None:
        super().setUp()
        self.branch("step/a", "shared.txt", "one\nfrom-a\nthree\n")
        self.branch("step/b", "shared.txt", "one\nfrom-b\nthree\n")

    def test_two_steps_editing_one_region_land_and_are_proven(self) -> None:
        first = self.land(["step/a", "step/b"])
        self.assertEqual(first.exit_code, EXIT_OK, first.summary)
        second = self.land(["step/a", "step/b"], slot=2, run_agent=self.resolving_agent)
        self.assertEqual(second.exit_code, EXIT_OK, second.summary)
        self.assertTrue(is_ancestor(self.repository, "step/a", "main"))
        self.assertTrue(is_ancestor(self.repository, "step/b", "main"))
        self.assertIsNone(unresolved_merge(self.repository))

    def test_the_landed_file_carries_what_both_sides_intended(self) -> None:
        self.land(["step/a", "step/b"])
        self.land(["step/a", "step/b"], slot=2, run_agent=self.resolving_agent)
        body = (self.repository / "shared.txt").read_text(encoding="utf-8")
        self.assertIn("from-a", body)
        self.assertIn("from-b", body)

    def test_a_clean_first_landing_never_reaches_an_agent(self) -> None:
        """The deterministic layer bounds the agent to exactly the conflicted case."""
        result = self.land(["step/a", "step/b"])
        self.assertEqual(result.detail["resolved_by"], BY_COMMAND)
        self.assertEqual(self.calls, [])

    def test_the_conflicted_landing_is_the_one_that_pays_for_a_session(self) -> None:
        self.land(["step/a", "step/b"])
        result = self.land(["step/a", "step/b"], slot=2, run_agent=self.resolving_agent)
        self.assertEqual(result.detail["resolved_by"], BY_AGENT)
        self.assertEqual(len(self.calls), 1)
        self.assertIn("shared.txt", self.calls[0])


class TheHaltPathConverges(RepositoryCase):
    def setUp(self) -> None:
        super().setUp()
        self.branch("step/a", "shared.txt", "one\nfrom-a\nthree\n")
        self.branch("step/b", "shared.txt", "one\nfrom-b\nthree\n")
        self.land(["step/a", "step/b"])

    def test_an_unresolvable_conflict_is_left_exactly_as_git_left_it(self) -> None:
        result = self.land(["step/a", "step/b"], slot=2)
        self.assertNotEqual(result.exit_code, EXIT_OK)
        self.assertIsNotNone(unresolved_merge(self.repository))
        self.assertIn("<<<<<<< ", (self.repository / "shared.txt").read_text(encoding="utf-8"))

    def test_the_halt_names_what_a_person_has_to_settle(self) -> None:
        result = self.land(["step/a", "step/b"], slot=2)
        self.assertIn("shared.txt", result.detail["conflicted"])
        self.assertTrue(any("re-run" in line for line in result.follow_up_work))

    def test_a_second_attempt_over_the_preserved_merge_refuses_before_paying(self) -> None:
        self.land(["step/a", "step/b"], slot=2)
        self.calls.clear()
        with self.assertRaises(CairnError) as raised:
            self.land(["step/a", "step/b"], slot=2)
        self.assertEqual(raised.exception.cause, "merge_in_progress")
        self.assertEqual(self.calls, [])

    def test_a_human_who_resolves_the_conflict_converges_on_the_next_run(self) -> None:
        self.land(["step/a", "step/b"], slot=2)
        self.write("shared.txt", "one\nfrom-a\nfrom-b\nthree\n")
        git(self.repository, ("add", "--all"))
        git(self.repository, ("commit", "--quiet", "--no-edit"))
        landed = self.subjects()
        result = self.land(["step/a", "step/b"], slot=2)
        self.assertEqual(result.status, "noop")
        self.assertEqual(self.subjects(), landed, "the re-run landed something twice")

    def test_a_human_who_discards_the_branchs_side_converges_on_the_next_run(self) -> None:
        """The branch still lands; that it contributed nothing is the honest record."""
        self.land(["step/a", "step/b"], slot=2)
        git(self.repository, ("checkout", "--ours", "--", "shared.txt"))
        git(self.repository, ("add", "--all"))
        git(self.repository, ("commit", "--quiet", "--no-edit"))
        result = self.land(["step/a", "step/b"], slot=2)
        self.assertEqual(result.status, "noop")
        self.assertNotIn("from-b", (self.repository / "shared.txt").read_text(encoding="utf-8"))

    def test_every_slot_before_the_halt_no_ops_rather_than_landing_again(self) -> None:
        self.land(["step/a", "step/b"], slot=2)
        self.write("shared.txt", "one\nfrom-a\nfrom-b\nthree\n")
        git(self.repository, ("add", "--all"))
        git(self.repository, ("commit", "--quiet", "--no-edit"))
        settled = self.subjects()
        first = self.land(["step/a", "step/b"])
        second = self.land(["step/a", "step/b"], slot=2)
        self.assertEqual([first.status, second.status], ["noop", "noop"])
        self.assertEqual(self.subjects(), settled)


class ALyingMergeReddens(RepositoryCase):
    def setUp(self) -> None:
        super().setUp()
        self.branch("step/a", "shared.txt", "one\nfrom-a\nthree\n")
        self.branch("step/b", "shared.txt", "one\nfrom-b\nthree\n")
        self.land(["step/a", "step/b"])

    def idle_agent(self, *args: Any, **kwargs: Any) -> CommandResult:
        """Reports done and merges nothing."""
        return CommandResult(EXIT_OK, "done", "merged it", [], False, None, {})

    def marker_agent(self, *args: Any, **kwargs: Any) -> CommandResult:
        """Commits the conflicted file exactly as git left it."""
        git(self.repository, ("add", "--all"))
        git(self.repository, ("commit", "--quiet", "--no-edit"))
        return CommandResult(EXIT_OK, "done", "merged it", [], False, None, {})

    def test_an_agent_that_reports_done_without_merging_fails_the_proof(self) -> None:
        result = self.land(["step/a", "step/b"], slot=2, run_agent=self.idle_agent)
        self.assertNotEqual(result.exit_code, EXIT_OK)
        self.assertEqual(result.cause, "merge_conflict")

    def test_an_agent_that_commits_leftover_markers_fails_the_proof(self) -> None:
        result = self.land(["step/a", "step/b"], slot=2, run_agent=self.marker_agent)
        self.assertNotEqual(result.exit_code, EXIT_OK)
        self.assertEqual(result.cause, "conflict_markers_committed")
        self.assertIn("shared.txt", result.summary)

    def test_the_scan_reads_what_was_committed_and_not_the_working_tree(self) -> None:
        """A marker an agent tidies out of the file after committing it still reached history."""
        self.write("marked.txt", "<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> them\n")
        committed = self.commit("commit the markers")
        self.write("marked.txt", "tidied\n")
        self.assertEqual(
            committed_markers(self.repository, committed, ["marked.txt"]), ("marked.txt",)
        )


class TheExclusionsReachTheRecord(RepositoryCase):
    def test_a_branch_whose_gate_closed_is_named_rather_than_silently_no_ops(self) -> None:
        self.branch("step/a", "a.txt", "a\n")
        git(self.repository, ("branch", "step/b", "main"))
        self.gate_report("b", "verify_failed")
        result = self.land(["step/a", "step/b"])
        excluded = result.detail["excluded"]
        self.assertEqual([e["branch"] for e in excluded], ["step/b"])
        self.assertEqual(excluded[0]["cause"], "verify_failed")
        self.assertTrue(any("step/b is excluded" in line for line in result.follow_up_work))

    def test_a_branch_that_was_never_created_is_not_reached(self) -> None:
        self.branch("step/a", "a.txt", "a\n")
        result = self.land(["step/a", "step/b"])
        self.assertEqual(result.detail["excluded"][0]["cause"], "not_reached")

    def test_a_branch_an_earlier_slot_landed_is_not_reported_as_an_exclusion(self) -> None:
        """It contributed its work; saying otherwise puts a fabricated cause in the record."""
        self.branch("step/a", "a.txt", "a\n")
        self.branch("step/b", "b.txt", "b\n")
        self.land(["step/a", "step/b"])
        second = self.land(["step/a", "step/b"], slot=2)
        self.assertEqual(second.detail["excluded"], [])
        self.assertEqual(second.follow_up_work, [])

    def test_a_closed_gate_excludes_a_branch_even_when_its_step_committed(self) -> None:
        """A session can commit in its own worktree; the gate decides, not the commit count."""
        self.branch("step/a", "a.txt", "a\n")
        self.gate_report("a", "verify_failed")
        result = self.land(["step/a"])
        self.assertEqual(result.status, "noop")
        self.assertEqual(result.detail["excluded"][0]["cause"], "verify_failed")
        self.assertFalse(is_ancestor(self.repository, "step/a", "main"))

    def test_a_branch_with_work_and_no_gate_report_of_this_run_does_not_land(self) -> None:
        """Nothing durable shows the step verified, so landing it lands unverified work."""
        git(self.repository, ("checkout", "--quiet", "-b", "step/a", "main"))
        self.write("a.txt", "a\n")
        self.commit("work on step/a")
        git(self.repository, ("checkout", "--quiet", "main"))
        result = self.land(["step/a"])
        self.assertEqual(result.status, "noop")
        self.assertEqual(result.detail["excluded"][0]["cause"], "not_reached")

    def test_a_wave_with_nothing_left_reports_a_no_op_and_moves_no_ref(self) -> None:
        git(self.repository, ("branch", "step/a", "main"))
        self.gate_report("a", "verify_failed")
        before = resolve_ref(self.repository, "main")
        result = self.land(["step/a"])
        self.assertEqual(result.status, "noop")
        self.assertEqual(resolve_ref(self.repository, "main"), before)

    def test_every_cause_the_gate_can_record_travels_into_the_merges_report(self) -> None:
        """The branch carries commits, so the gate's answer is what decides its fate."""
        self.branch("step/z", "z.txt", "z\n")
        for cause in EXCLUSION_CAUSES:
            with self.subTest(cause=cause):
                self.gate_report("z", cause)
                result = self.land(["step/z"])
                self.assertEqual(result.detail["excluded"][0]["cause"], cause)


class TheProofRunsInAProcessOfItsOwn(RepositoryCase):
    def test_the_proof_confirms_what_the_slot_landed(self) -> None:
        self.branch("step/a", "a.txt", "a\n")
        landed = self.land(["step/a"])
        self.context.report_path.write_text(
            json.dumps({"step_id": "merge_w1_1", "run_id": "run-1", "status": "done",
                        "summary": "landed", "needs_user_decision": False,
                        "detail": landed.detail}),
            encoding="utf-8",
        )
        proof = verify_landed(
            self.repository, merge="merge_w1_1", into="main",
            candidates=["step/a"], context=self.context,
        )
        self.assertEqual(proof.exit_code, EXIT_OK)

    def test_the_proof_reddens_a_slot_that_claimed_a_branch_it_never_landed(self) -> None:
        self.branch("step/a", "a.txt", "a\n")
        self.context.report_path.write_text(
            json.dumps({"step_id": "merge_w1_1", "run_id": "run-1", "status": "done",
                        "summary": "landed", "needs_user_decision": False,
                        "detail": {"landed": "step/a",
                                   "before": resolve_ref(self.repository, "main")}}),
            encoding="utf-8",
        )
        proof = verify_landed(
            self.repository, merge="merge_w1_1", into="main",
            candidates=["step/a"], context=self.context,
        )
        self.assertNotEqual(proof.exit_code, EXIT_OK)
        self.assertEqual(proof.cause, "merge_not_landed")


class ThePredictionNeverGatesTheProof(RepositoryCase):
    def test_a_pair_predicted_clean_that_conflicts_anyway_still_halts(self) -> None:
        """The case that matters: prediction advises the order and decides nothing.

        The two branches touch different files, so against each other they merge cleanly.
        What they conflict with is the parent, which moved for a reason neither tip can
        see — so a prediction between the tips says clean and the merge says otherwise.
        """
        self.branch("step/a", "a.txt", "a\n")
        self.branch("step/b", "shared.txt", "one\nfrom-b\nthree\n")
        self.write("shared.txt", "one\nfrom-main\nthree\n")
        self.commit("main moved under both branches")

        self.assertEqual(predict(self.repository, "step/a", "step/b").outcome, CLEAN)
        self.land(["step/a", "step/b"])
        result = self.land(["step/a", "step/b"], slot=2)
        self.assertNotEqual(result.exit_code, EXIT_OK, "the merge trusted the prediction")
        self.assertEqual(result.cause, "reported_failure")
        self.assertIsNotNone(unresolved_merge(self.repository))

    def test_a_stale_prediction_between_two_tips_never_gates_the_second_landing(self) -> None:
        self.branch("step/a", "shared.txt", "one\nfrom-a\nthree\n")
        self.branch("step/b", "shared.txt", "one\nfrom-b\nthree\n")
        self.land(["step/a", "step/b"])
        self.assertEqual(predict(self.repository, "step/a", "step/b").outcome, CONFLICTED)
        result = self.land(["step/a", "step/b"], slot=2)
        self.assertNotEqual(result.exit_code, EXIT_OK)

    def test_a_predicted_conflict_that_resolves_lands_anyway(self) -> None:
        self.branch("step/a", "shared.txt", "one\nfrom-a\nthree\n")
        self.branch("step/b", "shared.txt", "one\nfrom-b\nthree\n")
        self.assertEqual(predict(self.repository, "step/a", "step/b").outcome, CONFLICTED)
        self.land(["step/a", "step/b"])
        result = self.land(["step/a", "step/b"], slot=2, run_agent=self.resolving_agent)
        self.assertEqual(result.exit_code, EXIT_OK)

    def test_a_prediction_touches_no_ref_and_no_working_tree(self) -> None:
        self.branch("step/a", "shared.txt", "one\nfrom-a\nthree\n")
        self.branch("step/b", "shared.txt", "one\nfrom-b\nthree\n")
        before = git(self.repository, ("show-ref",), check=False).stdout
        status = git(self.repository, ("status", "--porcelain")).stdout
        predict(self.repository, "step/a", "step/b")
        self.assertEqual(git(self.repository, ("show-ref",), check=False).stdout, before)
        self.assertEqual(git(self.repository, ("status", "--porcelain")).stdout, status)


class TheScanIsScopedToWhatTheMergeChanged(RepositoryCase):
    def test_a_file_that_legitimately_carries_marker_lines_does_not_redden_a_merge(self) -> None:
        """The stated false-positive risk: committed content that discusses conflicts."""
        self.write("guide.md", "<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> them\n")
        self.commit("a guide about conflicts")
        self.branch("step/a", "a.txt", "a\n")
        result = self.land(["step/a"])
        self.assertEqual(result.exit_code, EXIT_OK, result.summary)

    def test_a_commit_the_scan_cannot_read_is_indeterminate_and_not_clean(self) -> None:
        """A scan that never ran must not report the absence of what it did not look for."""
        self.assertIsNone(committed_markers(self.repository, "0" * 40, ["shared.txt"]))

    def test_a_path_the_merge_deleted_carries_nothing_rather_than_reading_as_a_failure(
        self,
    ) -> None:
        git(self.repository, ("rm", "--quiet", "shared.txt"))
        committed = self.commit("delete the file")
        self.assertEqual(committed_markers(self.repository, committed, ["shared.txt"]), ())

    def test_a_binary_file_the_merge_touched_does_not_crash_the_scan(self) -> None:
        """A repository holds whatever bytes it holds; a scan a PNG can kill is not a scan."""
        (self.repository / "logo.png").write_bytes(bytes([0x89, 0x50, 0x4E, 0x47, 0xFF, 0xFE]))
        committed = self.commit("a binary file")
        self.assertEqual(committed_markers(self.repository, committed, ["logo.png"]), ())

    def test_a_marker_in_a_file_whose_name_is_not_ascii_is_still_found(self) -> None:
        """git escapes such a path in its own output, and an escaped path opens nothing."""
        self.write("café.md", "<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> them\n")
        committed = self.commit("markers under a non-ascii name")
        changed = changed_paths(self.repository, f"{committed}~1", committed)
        self.assertEqual(changed, ("café.md",))
        self.assertEqual(
            committed_markers(self.repository, committed, list(changed or ())), ("café.md",)
        )

    def test_the_same_content_reddens_once_the_merge_itself_changed_that_file(self) -> None:
        self.branch("step/a", "guide.md", "<<<<<<< HEAD\nours\n=======\nt\n>>>>>>> them\n")
        head = resolve_ref(self.repository, "main") or ""
        git(self.repository, ("merge", "--no-ff", "--no-edit", "-m", "m", "step/a"))
        after = resolve_ref(self.repository, "main") or ""
        self.assertEqual(committed_markers(self.repository, after, ["guide.md"]), ("guide.md",))
        self.assertNotEqual(head, after)


class TheOrderingBoundIsNeverViolated(RepositoryCase):
    def test_a_slot_lands_one_branch_at_a_time(self) -> None:
        self.branch("step/a", "a.txt", "a\n")
        self.branch("step/b", "b.txt", "b\n")
        first = self.land(["step/a", "step/b"])
        self.assertEqual(first.detail["landed"], "step/a")
        self.assertFalse(is_ancestor(self.repository, "step/b", "main"))
        second = self.land(["step/a", "step/b"], slot=2)
        self.assertEqual(second.detail["landed"], "step/b")

    def test_the_slot_lands_the_order_the_prediction_advised_and_not_the_alphabet(self) -> None:
        """Two branches conflict and a third does not, so the alphabet is the wrong answer."""
        self.branch("step/a", "shared.txt", "one\nfrom-a\nthree\n")
        self.branch("step/b", "shared.txt", "one\nfrom-b\nthree\n")
        self.branch("step/c", "c.txt", "c\n")
        result = self.land(["step/a", "step/b", "step/c"])
        self.assertEqual(result.detail["order"][0], "step/c", result.detail["order"])
        self.assertEqual(result.detail["landed"], "step/c")

    def test_a_branch_already_landed_is_not_landed_again(self) -> None:
        self.branch("step/a", "a.txt", "a\n")
        self.land(["step/a"])
        result = self.land(["step/a"], slot=2)
        self.assertEqual(result.status, "noop")

    def test_across_waves_the_emitted_chain_never_reorders(self) -> None:
        multi = topology("multi-wave")
        order = [node["name"] for node in multi["nodes"]]
        for wave in {node["wave"] for node in multi["nodes"] if node["role"] == "merge"}:
            merges = [n for n in multi["nodes"] if n["role"] == "merge" and n["wave"] == wave]
            prune = f"prune_w{wave}"
            for merge in merges:
                self.assertLess(order.index(merge["name"]), order.index(prune))


class TheMergeRefusesWhatItCannotLandHonestly(RepositoryCase):
    def test_a_repository_on_another_branch_is_refused(self) -> None:
        self.branch("step/a", "a.txt", "a\n")
        git(self.repository, ("checkout", "--quiet", "step/a"))
        with self.assertRaises(CairnError) as raised:
            self.land(["step/a"])
        self.assertEqual(raised.exception.cause, "merge_wrong_branch")

    def test_a_conflict_the_wave_does_claim_is_a_merge_problem_and_not_a_plan_defect(
        self,
    ) -> None:
        """Both branches change the file they conflict in, so the resolution is the answer."""
        self.branch("step/a", "shared.txt", "one\nfrom-a\nthree\n")
        self.branch("step/b", "shared.txt", "one\nfrom-b\nthree\n")
        result = self.land(["step/a", "step/b"])
        self.assertEqual(result.exit_code, EXIT_OK)

    def test_a_conflict_in_a_file_the_branch_did_change_goes_to_a_resolution(self) -> None:
        """The parent moved under the branch, but in a file the step itself claims."""
        self.branch("step/a", "a.txt", "a\n")
        self.write("shared.txt", "one\nfrom-main\nthree\n")
        self.commit("a change on the parent")
        git(self.repository, ("checkout", "--quiet", "step/a"))
        self.write("shared.txt", "one\nfrom-the-step\nthree\n")
        self.commit("the same file, on the branch")
        git(self.repository, ("checkout", "--quiet", "main"))
        result = self.land(["step/a"])
        self.assertEqual(result.cause, "reported_failure", "it was refused, not resolved")
        self.assertEqual(len(self.calls), 1, "the resolution was offered to a session")

    def test_a_branch_that_renames_a_file_claims_both_of_its_names(self) -> None:
        """git reports only a rename's destination, and the source would look unowned."""
        git(self.repository, ("checkout", "--quiet", "-b", "step/a", "main"))
        git(self.repository, ("mv", "shared.txt", "moved.txt"))
        self.commit("rename on the branch")
        git(self.repository, ("checkout", "--quiet", "main"))
        owned = owned_paths(self.repository, ["step/a"], "main")
        self.assertEqual(owned, ["moved.txt", "shared.txt"])

    def test_the_environment_that_would_send_the_commits_elsewhere_refuses_first(self) -> None:
        """One ref read against a session that would land its work in another repository."""
        self.branch("step/a", "a.txt", "a\n")
        with (
            patch.dict(os.environ, {"GIT_DIR": str(self.root / "elsewhere")}),
            self.assertRaises(CairnError) as raised,
        ):
            self.land(["step/a"])
        self.assertEqual(raised.exception.cause, "merge_environment_redirected")
        self.assertEqual(self.calls, [])


class TheEngineStopsAChainThatHalts(RepositoryCase):
    """Against real Dagu, because a run whose only failures are skips reports Succeeded."""

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
                "dagu is not installed, so the merge chain's routing is unverified. Install "
                f"it, or set {self.SKIP_ENV}=1 to record that this run did not check it."
            )
        super().setUp()
        self.engine_temporary = TemporaryDirectory()
        self.addCleanup(self.engine_temporary.cleanup)
        self.engine = Path(self.engine_temporary.name)

    def run_dag(self, steps: list[Any]) -> "subprocess.CompletedProcess[str]":
        workflow = envelope(
            steps,
            repository=str(self.repository),
            parent_branch=PARENT,
            occasion="20260810T000000Z-mergetest",
            python_path=str(CAIRN_ROOT),
            runs_root=str(self.root / "runs"),
        )
        path = self.engine / "plan.yaml"
        path.write_text(json.dumps(workflow, indent=2), encoding="utf-8")
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
            cwd=self.repository,
        )

    def record_the_gates(self) -> dict[str, Any]:
        """The gate reports the mark steps leave upstream, under the engine's own run id.

        A slot lands only what the gate recorded, so a chain without them has nothing to
        land and would prove nothing about merging.
        """
        writer = self.root / "record_gates.py"
        reports_of(self.root, ENGINE_RUN_ID).mkdir(parents=True, exist_ok=True)
        writer.write_text(
            "import json, os, sys\n"
            "for step in sys.argv[2:]:\n"
            "    path = os.path.join(sys.argv[1], 'mark_' + step + '.json')\n"
            "    with open(path, 'w', encoding='utf-8') as handle:\n"
            "        json.dump({'step_id': step, 'run_id': os.environ['DAG_RUN_ID'],\n"
            "                   'status': 'done', 'cause': None, 'summary': 'recorded',\n"
            "                   'needs_user_decision': False}, handle)\n",
            encoding="utf-8",
        )
        return {
            "name": "record_the_gates",
            "run": f"python3 {writer} {reports_of(self.root, ENGINE_RUN_ID)} a b",
            "working_dir": str(self.repository),
            "timeout_sec": 60,
            "retry_policy": {"limit": 0, "interval_sec": 1},
        }

    def chain(self, provider: str = "claude") -> list[Any]:
        """The emitted merge chain, with the candidates this repository actually has.

        No test here reaches a resolving agent. A conflict is handed to a provider that
        does not resolve, because the routing under test is what the engine does with a
        slot that failed — and a real session would make the assertion cost money and
        depend on what a model decided that day.
        """
        fan = topology("fan-out")
        graph = fixture("fan-out")
        steps = {step["id"]: step for step in graph["steps"]}
        emitted_nodes: list[Any] = [self.record_the_gates()]
        for node in fan["nodes"]:
            if node["role"] not in ("merge", "verify") or node["step"] is not None:
                continue
            body = emit_node(node, steps=steps, run_timeout_seconds=fan["max_seconds"])
            body["working_dir"] = str(self.repository)
            body["run"] = (
                str(body["run"])
                .replace("step/keymap_reader", "step/a")
                .replace("step/theme_reader", "step/b")
                .replace("--provider claude", f"--provider {provider}")
            )
            body.pop("depends", None)
            if emitted_nodes:
                body["depends"] = [emitted_nodes[-1]["name"]]
            emitted_nodes.append(body)
        return emitted_nodes

    def test_a_slot_that_cannot_resolve_reddens_the_run_rather_than_skipping_it(self) -> None:
        """A run whose only failures are skips reports Succeeded, so a halt must land failed."""
        self.branch("step/a", "shared.txt", "one\nfrom-a\nthree\n")
        self.branch("step/b", "shared.txt", "one\nfrom-b\nthree\n")
        completed = self.run_dag(self.chain(provider="nothing-resolves-this"))
        statuses = dict(NODE_STATUS.findall(completed.stdout))

        self.assertNotEqual(completed.returncode, 0, "a halted merge reported a clean run")
        self.assertNotIn("Result: Succeeded", completed.stdout)
        self.assertEqual(statuses.get("merge_w2_1"), "succeeded")
        self.assertEqual(statuses.get("merge_w2_2"), "failed", "the halt must land as failed")
        self.assertNotEqual(statuses.get("verify_merge_w2_2"), "succeeded")
        self.assertIsNotNone(unresolved_merge(self.repository), "the halt was not preserved")

    def test_a_halted_chain_lands_what_it_proved_and_nothing_after_it(self) -> None:
        """Nothing writes the parent branch over a conflicted index."""
        self.branch("step/a", "shared.txt", "one\nfrom-a\nthree\n")
        self.branch("step/b", "shared.txt", "one\nfrom-b\nthree\n")
        before = git(self.repository, ("rev-parse", "main")).stdout
        self.run_dag(self.chain(provider="nothing-resolves-this"))
        self.assertNotEqual(
            git(self.repository, ("rev-parse", "main")).stdout, before, "no slot landed"
        )
        self.assertTrue(is_ancestor(self.repository, "step/a", "main"))
        self.assertFalse(
            is_ancestor(self.repository, "step/b", "main"),
            "the halted branch landed anyway",
        )

    def test_a_clean_chain_lands_every_branch_and_leaves_the_run_green(self) -> None:
        self.branch("step/a", "a.txt", "a\n")
        self.branch("step/b", "b.txt", "b\n")
        completed = self.run_dag(self.chain())
        statuses = dict(NODE_STATUS.findall(completed.stdout))

        self.assertEqual(completed.returncode, 0, completed.stdout)
        self.assertEqual(statuses.get("verify_merge_w2_2"), "succeeded")
        self.assertTrue(is_ancestor(self.repository, "step/a", "main"))
        self.assertTrue(is_ancestor(self.repository, "step/b", "main"))


if __name__ == "__main__":
    unittest.main()
