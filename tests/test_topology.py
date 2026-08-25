"""Doc 07's derivation: waves, branches, node names, and the duration arithmetic."""

import json
import unittest
from pathlib import Path
from typing import Any, cast

from cairn.emitters import emit_node, emit_step, emit_verify, retry_policy
from cairn.plan.schema import SUPPORT_TIMEOUT, WAIT_REPORT_GRACE, Graph, Step, normalise
from cairn.topology import (
    ENGINE_NAME_MAX_BYTES,
    RESERVED_NAMES,
    ROLES,
    RUN_CEILING_SECONDS,
    Node,
    Topology,
    TopologyError,
    check_name,
    critical_path_seconds,
    derive,
    node_name,
    parse_node_name,
    total_seconds,
    worktrees_root_for,
)
from cairn.verify import BRANCH, CHAIN

FIXTURES = Path(__file__).parents[1] / "fixtures" / "plans"

_NODE: Node = {
    "name": "",
    "role": "work",
    "step": None,
    "wave": 1,
    "working_directory": "/repo",
    "after": [],
    "max_seconds": 0,
    "detail": {},
}
REPOSITORY = Path("/srv/work/product")
PARENT = "main"


def fixture(name: str) -> Graph:
    with open(FIXTURES / name / "graph.json", encoding="utf-8") as handle:
        return normalise(json.load(handle))


def topology(name: str) -> Topology:
    return derive(fixture(name), repository_root=REPOSITORY, parent_branch=PARENT)


def names(topology_: Topology, role: str) -> list[str]:
    return [node["name"] for node in topology_["nodes"] if node["role"] == role]


def by_name(topology_: Topology, name: str) -> Node:
    for node in topology_["nodes"]:
        if node["name"] == name:
            return node
    raise AssertionError(f"no node named {name!r}")


def one_step_graph(**overrides: Any) -> Graph:
    step: dict[str, Any] = {
        "id": "only",
        "slug": "only",
        "title": "Only",
        "task": "Do the only thing.",
        "verify": "test -f out",
    }
    step.update(overrides)
    return normalise(
        {
            "plan": {"slug": "solo", "title": "Solo", "source": "solo.md"},
            "steps": [step],
        }
    )


class Naming(unittest.TestCase):
    def test_a_name_is_its_role_then_its_subject(self) -> None:
        self.assertEqual(node_name("verify", "config"), "verify_config")
        self.assertEqual(parse_node_name("verify_config"), ("verify", "config"))

    def test_a_step_id_that_starts_with_a_role_still_round_trips(self) -> None:
        name = node_name("work", "work_config")
        self.assertEqual(parse_node_name(name), ("work", "work_config"))

    def test_a_reserved_name_is_refused(self) -> None:
        for reserved in sorted(RESERVED_NAMES):
            with self.assertRaisesRegex(TopologyError, "reserved"):
                check_name(reserved)

    def test_a_hyphen_is_refused_because_the_engine_rejects_it(self) -> None:
        with self.assertRaisesRegex(TopologyError, "hyphen"):
            check_name("work_a-b")

    def test_an_over_long_name_is_refused_rather_than_truncated(self) -> None:
        with self.assertRaisesRegex(TopologyError, "over the 40-byte"):
            check_name("verify_" + "a" * ENGINE_NAME_MAX_BYTES)

    def test_derivation_refuses_a_step_whose_longest_node_name_will_not_fit(self) -> None:
        graph = one_step_graph(id="a" * 36, slug="a" * 36)
        with self.assertRaisesRegex(TopologyError, "over the 40-byte"):
            derive(graph, repository_root=REPOSITORY, parent_branch=PARENT)


class Paths(unittest.TestCase):
    def test_the_worktrees_root_sits_beside_the_repository_and_is_named_by_plan(
        self,
    ) -> None:
        root = worktrees_root_for(Path("/srv/work/product"), "fan-out")
        self.assertEqual(root, Path("/srv/work/product.cairn-worktrees/fan-out"))

    def test_two_plans_never_share_a_worktree_directory(self) -> None:
        self.assertNotEqual(
            worktrees_root_for(REPOSITORY, "one"), worktrees_root_for(REPOSITORY, "two")
        )

    def test_no_derived_path_carries_a_home_directory(self) -> None:
        rendered = json.dumps(topology("multi-wave"))
        self.assertNotIn(str(Path.home()), rendered)
        self.assertNotIn("~", rendered)


class ChainShape(unittest.TestCase):
    def test_a_chain_runs_on_the_parent_branch_with_no_worktrees(self) -> None:
        chain = topology("linear-chain")
        self.assertEqual(chain["branches"], [])
        self.assertEqual(names(chain, "setup"), [])
        self.assertEqual(names(chain, "join"), [])
        self.assertEqual(names(chain, "prune"), [])
        self.assertEqual([wave["isolated"] for wave in chain["waves"]], [False] * 3)

    def test_every_chain_step_is_work_then_verify_then_mark_then_commit(self) -> None:
        chain = topology("linear-chain")
        self.assertEqual(
            [node["name"] for node in chain["nodes"] if node["step"] == "middleware"],
            ["work_middleware", "verify_middleware", "mark_middleware", "commit_middleware"],
        )

    def test_a_chain_marker_omits_the_flag_that_would_let_the_chain_carry_on(self) -> None:
        chain = topology("linear-chain")
        self.assertEqual(by_name(chain, "mark_middleware")["detail"]["position"], CHAIN)

    def test_the_marker_waits_on_the_assertion_that_decides_whether_it_writes(self) -> None:
        chain = topology("linear-chain")
        self.assertEqual(by_name(chain, "mark_middleware")["after"], ["verify_middleware"])
        self.assertEqual(by_name(chain, "commit_middleware")["after"], ["mark_middleware"])

    def test_a_chain_commit_omits_the_flag_so_the_cascade_reaches_what_depended_on_it(
        self,
    ) -> None:
        chain = topology("linear-chain")
        graph = fixture("linear-chain")
        commit = emit_node(
            by_name(chain, "commit_middleware"),
            steps={step["id"]: step for step in graph["steps"]},
            run_timeout_seconds=chain["max_seconds"],
        )
        self.assertNotIn("continue_on", commit)

    def test_a_single_step_plan_is_the_degenerate_chain(self) -> None:
        solo = derive(one_step_graph(), repository_root=REPOSITORY, parent_branch=PARENT)
        self.assertEqual(solo["branches"], [])
        self.assertEqual(solo["merge_order"], [])
        self.assertEqual(
            [node["name"] for node in solo["nodes"]],
            ["lock_acquire", "work_only", "verify_only", "mark_only", "commit_only"],
        )
        self.assertEqual(solo["on_exit"]["name"], "lock_release")

    def test_a_step_with_no_verify_command_gets_no_verify_node(self) -> None:
        solo = derive(
            one_step_graph(verify=None), repository_root=REPOSITORY, parent_branch=PARENT
        )
        self.assertEqual(names(solo, "verify"), [])
        self.assertEqual(by_name(solo, "mark_only")["after"], ["work_only"])
        self.assertIs(by_name(solo, "mark_only")["detail"]["verified"], False)


class FanOutShape(unittest.TestCase):
    def test_an_independent_set_gets_one_branch_and_worktree_each(self) -> None:
        fan = topology("fan-out")
        self.assertEqual(
            [branch["name"] for branch in fan["branches"]],
            ["step/keymap_reader", "step/theme_reader"],
        )
        self.assertEqual(
            by_name(fan, "setup_theme_reader")["detail"]["worktree"],
            str(worktrees_root_for(REPOSITORY, "fan-out") / "theme_reader"),
        )

    def test_an_isolated_step_is_setup_work_verify_mark_commit(self) -> None:
        fan = topology("fan-out")
        self.assertEqual(
            [node["name"] for node in fan["nodes"] if node["step"] == "theme_reader"],
            [
                "setup_theme_reader",
                "work_theme_reader",
                "verify_theme_reader",
                "mark_theme_reader",
                "commit_theme_reader",
            ],
        )

    def test_an_isolated_marker_carries_the_position_that_excludes_one_branch(self) -> None:
        fan = topology("fan-out")
        self.assertEqual(by_name(fan, "mark_theme_reader")["detail"]["position"], BRANCH)

    def test_only_the_commit_routes_and_it_routes_by_the_position_it_carries(self) -> None:
        """The last node before the join is the one node in a step's group that routes."""
        fan = topology("fan-out")
        graph = fixture("fan-out")
        steps = {step["id"]: step for step in graph["steps"]}
        emitted = {
            node["name"]: emit_node(node, steps=steps, run_timeout_seconds=fan["max_seconds"])
            for node in fan["nodes"]
            if node["step"] == "theme_reader" and node["role"] in ("mark", "commit")
        }
        self.assertEqual(emitted["commit_theme_reader"]["continue_on"], {"skipped": True})
        self.assertNotIn("continue_on", emitted["mark_theme_reader"])

    def test_the_join_waits_for_every_commit_in_the_wave(self) -> None:
        fan = topology("fan-out")
        self.assertEqual(
            sorted(by_name(fan, "join_w2")["after"]),
            ["commit_keymap_reader", "commit_theme_reader"],
        )

    def test_isolated_work_runs_in_its_own_worktree_and_setup_in_the_repository(
        self,
    ) -> None:
        fan = topology("fan-out")
        self.assertEqual(by_name(fan, "setup_theme_reader")["working_directory"], str(REPOSITORY))
        self.assertEqual(
            by_name(fan, "work_theme_reader")["working_directory"],
            str(worktrees_root_for(REPOSITORY, "fan-out") / "theme_reader"),
        )

    def test_merges_are_slots_in_a_chain_not_a_fixed_order_of_branches(self) -> None:
        fan = topology("fan-out")
        merges = names(fan, "merge")
        self.assertEqual(merges, ["merge_w2_1", "merge_w2_2"])
        for merge in merges:
            self.assertEqual(
                by_name(fan, merge)["detail"]["candidates"],
                ["step/keymap_reader", "step/theme_reader"],
            )
        # A slot waits on the proof of the slot before it, never on that slot's own account
        # of itself, so nothing lands over a merge that was never established.
        self.assertEqual(by_name(fan, "merge_w2_2")["after"], ["verify_merge_w2_1"])

    def test_the_merge_order_is_a_bound_per_wave_and_not_a_sequence(self) -> None:
        self.assertEqual(
            topology("fan-out")["merge_order"],
            [["step/keymap_reader", "step/theme_reader"]],
        )

    def test_the_prune_follows_the_last_merge_and_names_what_it_removes(self) -> None:
        fan = topology("fan-out")
        prune = by_name(fan, "prune_w2")
        self.assertEqual(prune["after"], ["verify_merge_w2_2"])
        self.assertEqual(prune["detail"]["branches"], ["step/keymap_reader", "step/theme_reader"])


class MultiWaveShape(unittest.TestCase):
    def test_a_chain_feeding_a_fan_out_feeding_a_chain_composes(self) -> None:
        multi = topology("multi-wave")
        self.assertEqual(
            [(wave["index"], wave["steps"], wave["isolated"]) for wave in multi["waves"]],
            [
                (1, ["export_schema"], False),
                (2, ["writer"], False),
                (3, ["reader", "zip_packer"], True),
                (4, ["reconcile"], False),
            ],
        )

    def test_a_waves_prune_feeds_the_next_segments_first_step(self) -> None:
        multi = topology("multi-wave")
        self.assertEqual(by_name(multi, "work_reconcile")["after"], ["prune_w3"])

    def test_the_first_wave_follows_the_lock(self) -> None:
        multi = topology("multi-wave")
        self.assertEqual(by_name(multi, "work_export_schema")["after"], ["lock_acquire"])

    def test_the_release_depends_on_nothing_so_a_failed_run_still_runs_it(self) -> None:
        # A node whose dependency failed is never dispatched, so a release wired into the
        # graph would run only on the success path and a failed run would hold its
        # repository for the whole reclaim window.
        multi = topology("multi-wave")
        self.assertEqual(multi["on_exit"]["after"], [])
        self.assertNotIn("lock_release", [node["name"] for node in multi["nodes"]])

    def test_every_node_name_is_unique_engine_legal_and_parseable(self) -> None:
        multi = topology("multi-wave")
        seen = [node["name"] for node in multi["nodes"]]
        self.assertEqual(len(seen), len(set(seen)))
        for name in seen:
            check_name(name)
            self.assertIn(parse_node_name(name).role, ROLES)

    def test_a_name_outside_the_closed_role_set_is_refused_both_ways(self) -> None:
        with self.assertRaises(TopologyError):
            node_name("scribble", "thing")
        with self.assertRaises(TopologyError):
            parse_node_name("scribble_thing")

    def test_a_step_id_the_engine_would_reject_is_refused(self) -> None:
        for bad in ("9work", "work.a", "work a", "work/a", ""):
            with self.subTest(bad=bad), self.assertRaises(TopologyError):
                check_name(bad)

    def test_every_node_carries_a_working_directory(self) -> None:
        for fixture_name in ("linear-chain", "fan-out", "multi-wave"):
            for node in topology(fixture_name)["nodes"]:
                self.assertTrue(node["working_directory"], node["name"])


class Duration(unittest.TestCase):
    def test_a_step_costs_every_attempt_and_every_wait_between_them(self) -> None:
        # The engine applies `timeout_sec` per attempt, so one retry doubles the bound and
        # adds the interval; a bound counted once would understate a run by hours.
        solo = derive(
            one_step_graph(timeout=100, retries=0),
            repository_root=REPOSITORY,
            parent_branch=PARENT,
        )
        retried = derive(
            one_step_graph(timeout=100, retries=1),
            repository_root=REPOSITORY,
            parent_branch=PARENT,
        )
        self.assertEqual(by_name(solo, "work_only")["max_seconds"], 100)
        self.assertEqual(by_name(retried, "work_only")["max_seconds"], 100 * 2 + 1)

    def test_the_run_maximum_holds_whatever_the_engines_concurrency_cap_is(self) -> None:
        # The slowest path would be tighter and wrong: the engine caps concurrent steps,
        # so a wave wider than the cap outruns its own longest path, and a reclaim window
        # derived from that path would come free mid-run. Computed by hand here, because a
        # comparison against the function that produced it could not fail.
        fan = topology("fan-out")
        work = sum(
            by_name(fan, f"work_{step}")["max_seconds"]
            for step in ("extract_the_loader_interface", "theme_reader", "keymap_reader")
        )
        support = sum(
            node["max_seconds"] for node in fan["nodes"] if node["role"] != "work"
        )
        self.assertEqual(
            fan["max_seconds"], work + support + fan["on_exit"]["max_seconds"]
        )

    def test_the_maximum_adds_parallel_branches_rather_than_taking_the_slower(
        self,
    ) -> None:
        # A diamond is where a longest path and a sum give different answers, so it is
        # where the choice is actually visible.
        diamond: list[Node] = [
            {**_NODE, "name": "a", "after": [], "max_seconds": 10},
            {**_NODE, "name": "b", "after": ["a"], "max_seconds": 100},
            {**_NODE, "name": "c", "after": ["a"], "max_seconds": 7},
            {**_NODE, "name": "d", "after": ["b", "c"], "max_seconds": 1},
        ]
        self.assertEqual(total_seconds(diamond), 118)

    def test_a_declared_wait_counts_in_full_toward_the_run_maximum(self) -> None:
        waiting = derive(
            one_step_graph(
                kind="command",
                command="test -f ready",
                command_type="wait_until",
                timeout=7200,
                verify=None,
            ),
            repository_root=REPOSITORY,
            parent_branch=PARENT,
        )
        self.assertGreaterEqual(waiting["max_seconds"], 7200 + WAIT_REPORT_GRACE)
        self.assertEqual(
            by_name(waiting, "work_only")["max_seconds"], 7200 + WAIT_REPORT_GRACE
        )

    def test_a_plan_of_ordinary_agent_steps_is_not_refused_for_being_wide(self) -> None:
        # Gating admission on the sum would make the ceiling a plan-size cap: thirty
        # perfectly ordinary parallel steps would be refused for existing.
        steps = [
            {
                "id": f"s{index}",
                "slug": f"s{index}",
                "title": f"S{index}",
                "task": "Do the thing.",
                "verify": "test -f out",
            }
            for index in range(30)
        ]
        graph = normalise(
            {"plan": {"slug": "wide", "title": "W", "source": "w.md"}, "steps": steps}
        )
        derived = derive(graph, repository_root=REPOSITORY, parent_branch=PARENT)
        self.assertGreater(derived["max_seconds"], RUN_CEILING_SECONDS)
        self.assertLess(derived["critical_path_seconds"], RUN_CEILING_SECONDS)

    def test_the_lease_is_the_sum_and_the_ceiling_is_the_chain(self) -> None:
        fan = topology("fan-out")
        self.assertEqual(
            fan["max_seconds"], total_seconds([*fan["nodes"], fan["on_exit"]])
        )
        self.assertEqual(
            fan["critical_path_seconds"],
            critical_path_seconds([*fan["nodes"], fan["on_exit"]]),
        )
        self.assertGreater(fan["max_seconds"], fan["critical_path_seconds"])

    def test_a_plan_whose_waits_run_past_the_ceiling_is_refused_with_the_arithmetic(
        self,
    ) -> None:
        with self.assertRaises(TopologyError) as caught:
            derive(
                one_step_graph(
                    kind="command",
                    command="test -f ready",
                    command_type="wait_until",
                    timeout=RUN_CEILING_SECONDS + 1,
                    verify=None,
                ),
                repository_root=REPOSITORY,
                parent_branch=PARENT,
            )
        message = str(caught.exception)
        self.assertIn("worst-case duration", message)
        self.assertIn("slowest chain", message)
        self.assertIn("once per attempt", message)
        self.assertIn("48-hour ceiling", message)


class Emission(unittest.TestCase):
    def steps(self, graph: Graph) -> dict[str, Step]:
        return {step["id"]: step for step in graph["steps"]}

    def test_every_emitted_node_carries_a_timeout_and_a_retry_bound(self) -> None:
        graph = fixture("multi-wave")
        derived = derive(graph, repository_root=REPOSITORY, parent_branch=PARENT)
        emitted = 0
        for node in derived["nodes"]:
            try:
                step = emit_node(
                    node, steps=self.steps(graph), run_timeout_seconds=derived["max_seconds"]
                )
            except ValueError as refusal:
                # A role whose body a later document owns must refuse by name; it must
                # never quietly emit an unbounded step.
                self.assertIn("belongs to doc", str(refusal), node["name"])
                continue
            self.assertIn("timeout_sec", step, node["name"])
            self.assertIn("retry_policy", step, node["name"])
            self.assertIn("interval_sec", step["retry_policy"], node["name"])
            self.assertIn("working_dir", step, node["name"])
            emitted += 1
        self.assertGreater(emitted, 0)

    def test_a_verify_command_is_emitted_verbatim_however_it_is_written(self) -> None:
        # The one-quoted-invocation rule is about bodies Cairn builds. The plan author's
        # verify is a shell line — pipes, globs and quotes and all — and applying the rule
        # to it refuses every real plan whose assertion is anything but a bare command.
        assertion = 'grep -q "## Unreleased" CHANGELOG.md | wc -l'
        graph = one_step_graph(verify=assertion)
        derived = derive(graph, repository_root=REPOSITORY, parent_branch=PARENT)
        emitted = emit_node(
            by_name(derived, "verify_only"),
            steps={step["id"]: step for step in graph["steps"]},
            run_timeout_seconds=derived["max_seconds"],
        )
        self.assertEqual(emitted["run"], assertion)

    def test_every_verify_in_the_corpus_emits(self) -> None:
        for name in ("non-convergent", "multi-wave", "linear-chain"):
            graph = fixture(name)
            derived = derive(graph, repository_root=REPOSITORY, parent_branch=PARENT)
            steps = {step["id"]: step for step in graph["steps"]}
            for node in derived["nodes"]:
                # A `verify` node naming no step proves a merge rather than running an
                # assertion the plan's author wrote.
                if node["role"] != "verify" or node["step"] is None:
                    continue
                emitted = emit_node(
                    node, steps=steps, run_timeout_seconds=derived["max_seconds"]
                )
                self.assertEqual(emitted["run"], steps[node["step"]]["verify"])

    def test_a_verify_step_is_never_retried(self) -> None:
        verify = emit_verify(self.steps(one_step_graph())["only"], "/repo")
        self.assertEqual(verify["retry_policy"], {"limit": 0, "interval_sec": 1})
        self.assertEqual(verify["timeout_sec"], SUPPORT_TIMEOUT)

    def test_never_retrying_is_spelled_with_the_interval_the_engine_demands(self) -> None:
        self.assertEqual(retry_policy(0, 1), {"limit": 0, "interval_sec": 1})

    def test_an_agent_step_is_not_retried_at_all(self) -> None:
        # Every failure an agent can have is either a wrong task or a paid session that
        # already changed the repository. A rate limit is the one that is distinguishable,
        # and it is reported with the moment it clears rather than waited out blind: the
        # engine's policy is a static number and cannot read `resetsAt`, so any wait short
        # enough to be worth making usually meets the same limit and pays for a second
        # session to find out.
        step = emit_step(self.steps(one_step_graph())["only"], "/repo")
        self.assertEqual(step["retry_policy"], {"limit": 0, "interval_sec": 1})

    def test_no_emitted_policy_makes_a_retry_conditional_on_an_exit_code(self) -> None:
        # An `exit_code` list only ever means "retry on these", so with no retries left to
        # scope it would be a policy that reads as though something still retries.
        graph = fixture("multi-wave")
        for step in graph["steps"]:
            self.assertNotIn("exit_code", emit_step(step, "/repo")["retry_policy"])

    def test_a_plan_that_asks_for_a_retry_still_gets_one(self) -> None:
        step = emit_step(self.steps(one_step_graph(retries=2))["only"], "/repo")
        self.assertEqual(step["retry_policy"], {"limit": 2, "interval_sec": 1})

    def test_the_corpus_carries_the_kind_default_rather_than_contradicting_it(
        self,
    ) -> None:
        # An agent deriving a new plan reads these as worked examples, so a corpus that
        # pinned a retry would teach one back in.
        graph = fixture("multi-wave")
        agents = [step for step in graph["steps"] if step["kind"].startswith("agent.")]
        self.assertTrue(agents)
        for step in agents:
            self.assertEqual(step["retries"], 0, step["id"])

    def test_a_command_step_is_not_retried_at_all(self) -> None:
        graph = fixture("mixed-kinds")
        commands = [
            step for step in graph["steps"] if step["kind"] == "command"
        ]
        self.assertTrue(commands)
        for step in commands:
            emitted = emit_step(step, "/repo")
            self.assertEqual(emitted["retry_policy"]["limit"], 0)

    def test_every_topology_role_emits_a_body(self) -> None:
        graph = fixture("multi-wave")
        derived = derive(graph, repository_root=REPOSITORY, parent_branch=PARENT)
        emitted = {
            node["role"]: emit_node(
                node, steps=self.steps(graph), run_timeout_seconds=60
            )
            for node in derived["nodes"]
        }
        self.assertEqual(set(emitted), set(ROLES))
        for role, step in emitted.items():
            self.assertTrue(str(step["run"]), f"{role} emitted an empty body")

    def test_the_join_waits_for_every_commit_in_its_wave_and_never_absorbs_a_failure(
        self,
    ) -> None:
        graph = fixture("fan-out")
        derived = derive(graph, repository_root=REPOSITORY, parent_branch=PARENT)
        node = next(item for item in derived["nodes"] if item["role"] == "join")
        join = emit_node(node, steps=self.steps(graph), run_timeout_seconds=60)
        self.assertEqual(
            sorted(cast(list[str], join["depends"])),
            sorted(f"commit_{step}" for step in derived["waves"][1]["steps"]),
        )
        # An excluded branch reaches the join already, because that branch's commit stops
        # the skip cascade. A flag here would instead absorb a genuine failure and let the
        # slots land over a wave nobody could survey.
        self.assertNotIn("continue_on", join)

    def test_a_lock_node_carries_the_runs_own_timeout(self) -> None:
        graph = fixture("fan-out")
        derived = derive(graph, repository_root=REPOSITORY, parent_branch=PARENT)
        acquire = emit_node(
            by_name(derived, "lock_acquire"),
            steps=self.steps(graph),
            run_timeout_seconds=derived["max_seconds"],
        )
        self.assertIn(f"--run-timeout {derived['max_seconds']}", cast(str, acquire["run"]))
        self.assertIn("--plan fan-out", cast(str, acquire["run"]))

    def test_every_emitted_body_is_one_quoted_invocation(self) -> None:
        graph = fixture("multi-wave")
        derived = derive(graph, repository_root=REPOSITORY, parent_branch=PARENT)
        for node in derived["nodes"]:
            if node["role"] in ("merge", "join", "verify"):
                continue
            emitted = emit_node(
                node, steps=self.steps(graph), run_timeout_seconds=derived["max_seconds"]
            )
            self.assertTrue(str(emitted["run"]).startswith("python3 -m cairn "))


if __name__ == "__main__":
    unittest.main()
