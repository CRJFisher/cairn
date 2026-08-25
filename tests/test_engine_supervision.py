"""Doc 09 against the real engine: kill it, contend for it, and refuse its retry scanner.

These tests kill for real. Nothing here simulates a crash, because the whole class of
defect they exist to catch is the difference between what a supervisor is documented to do
and what it does when the process disappears mid-write.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

from cairn.baseconfig import ensure_dag_retry_disabled
from cairn.gitio import git, resolve_ref
from cairn.locks import RUN_LOCK_REF, git_write_mutex, read_run_lock
from cairn.supervise import (
    STATUS_FAILED,
    STATUS_RUNNING,
    find_status_files,
    last_record,
    reconcile,
)
from cairn.topology import worktrees_root_for

PACKAGE_ROOT = Path(__file__).parents[1]
ENGINE = shutil.which("dagu")
# Long enough that no test outruns it, and distinctive enough that a process listing can
# prove the sleep an engine step started is gone.
SENTINEL_SECONDS = 987654
START_TIMEOUT_SECONDS = 60


def steps_hold(marker: str, seconds: int) -> list[dict[str, Any]]:
    """One step that holds for `seconds`, carrying `marker` in the leaf process's own argv.

    A shell comment would put the marker only on the shell the engine starts, so a listing
    could call the tree reaped while the process that does the waiting was still alive.
    """
    return [
        {
            "name": "hold",
            "run": f"{sys.executable} -c 'import time; time.sleep({seconds})' {marker}",
            "depends": ["lock_acquire"],
        }
    ]


@unittest.skipUnless(ENGINE, "the engine is not installed")
class EngineCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name).resolve()
        self.addCleanup(self._temporary.cleanup)
        self.home = self.root / "engine-home"
        self.home.mkdir()
        ensure_dag_retry_disabled(self.home / "base.yaml")
        self.dags = self.root / "dags"
        self.dags.mkdir()
        self.repository = self.make_repository("repo")

    def make_repository(self, name: str) -> Path:
        repository = self.root / name
        repository.mkdir(parents=True)
        git(repository, ("init", "--initial-branch=main", "--quiet", "."))
        git(repository, ("config", "user.email", "cairn@test"))
        git(repository, ("config", "user.name", "Cairn Test"))
        (repository / "README.md").write_text("start\n", encoding="utf-8")
        git(repository, ("add", "--all"))
        git(repository, ("commit", "--quiet", "-m", "init"))
        return repository

    def environment(self) -> dict[str, str]:
        return {
            **os.environ,
            "DAGU_HOME": str(self.home),
            "PYTHONPATH": str(PACKAGE_ROOT),
        }

    def write_dag(
        self,
        name: str,
        plan: str,
        repository: Path,
        steps: list[dict[str, Any]],
        *,
        run_timeout: int = 600,
        release: bool = True,
    ) -> Path:
        """Write one workflow, spelled as JSON — which the engine reads as the YAML it is."""
        cairn = f"{sys.executable} -m cairn"
        body: list[dict[str, Any]] = [
            {
                "name": "lock_acquire",
                "run": f"{cairn} lock acquire --plan {plan} --run-timeout {run_timeout}",
                "working_dir": str(repository),
                "timeout_sec": 120,
                "retry_policy": {"limit": 0, "interval_sec": 1},
            },
            *[
                {
                    "working_dir": str(repository),
                    "timeout_sec": 300,
                    "retry_policy": {"limit": 0, "interval_sec": 1},
                    **step,
                }
                for step in steps
            ],
        ]
        path = self.dags / f"{name}.yaml"
        path.write_text(
            json.dumps(
                {
                    "type": "graph",
                    "retry_policy": {"limit": 0, "interval_sec": 1},
                    # The release is a lifecycle handler, not a node: a node whose
                    # dependency failed is never dispatched, so a failed run would keep
                    # its repository for the whole reclaim window.
                    **(
                        {
                            "handler_on": {
                                "exit": {
                                    "run": f"{cairn} lock release",
                                    "working_dir": str(repository),
                                    "timeout_sec": 120,
                                }
                            }
                        }
                        if release
                        else {}
                    ),
                    # The engine does not pass its own environment through to a step, so
                    # the package these tests exercise is named in the workflow. Release
                    # resolves the installed command instead ([16]).
                    "env": [
                        {"PYTHONPATH": str(PACKAGE_ROOT)},
                        {"DAGU_HOME": str(self.home)},
                        {"CAIRN_PARENT_BRANCH": "main"},
                        # Judged at the run's first act, so a workflow that omitted it
                        # would be refused before its first spend ([triggers.md]).
                        {"CAIRN_REPOSITORY": str(repository)},
                        # Where every step of this run writes its own account. There is no
                        # fallback, so a workflow that omitted it would have every step
                        # fail to resolve its identity before doing any work.
                        {"CAIRN_RUNS_DIR": str(self.root / "runs")},
                    ],
                    "steps": body,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return path

    def start(self, dag: Path, run_id: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(ENGINE), "start", "--run-id", run_id, str(dag)],
            capture_output=True,
            text=True,
            env=self.environment(),
            timeout=600,
            check=False,
        )

    def start_detached(self, dag: Path, run_id: str) -> subprocess.Popen[str]:
        # Its own session, so a tree kill reaches the engine and its steps and stops there
        # rather than at whatever started this test.
        child = subprocess.Popen(
            [str(ENGINE), "start", "--run-id", run_id, str(dag)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=self.environment(),
            start_new_session=True,
        )
        self.addCleanup(self._reap, child)
        return child

    def _reap(self, child: subprocess.Popen[str]) -> None:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=30)

    def wait_for(self, predicate: Any, what: str, seconds: int = START_TIMEOUT_SECONDS) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.2)
        self.fail(f"timed out waiting for {what}")

    def matching_processes(self, marker: str) -> list[str]:
        found = subprocess.run(
            ["pgrep", "-f", marker], capture_output=True, text=True, check=False
        )
        return [line for line in found.stdout.split() if line]

    def status_files(self) -> list[Path]:
        return list(find_status_files(self.home / "data" / "dag-runs"))


class Concurrency(EngineCase):
    def test_a_second_run_against_one_repository_is_refused_and_names_the_holder(
        self,
    ) -> None:
        marker = f"cairn-hold-{os.getpid()}"
        first = self.write_dag("first", "plan-a", self.repository, steps_hold(marker, 20))
        second = self.write_dag("second", "plan-a", self.repository, steps_hold(marker, 1))
        running = self.start_detached(first, "run_first")
        self.wait_for(
            lambda: resolve_ref(self.repository, RUN_LOCK_REF) is not None,
            "the first run to take the lock",
        )
        refused = self.start(second, "run_second")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("run_first", refused.stdout + refused.stderr)
        running.wait(timeout=300)
        self.assertIsNone(resolve_ref(self.repository, RUN_LOCK_REF))

    def test_a_different_plan_against_one_repository_is_refused_too(self) -> None:
        # The engine's own serialisation is per DAG name, so this is exactly the case it
        # would let through.
        marker = f"cairn-hold-{os.getpid()}"
        first = self.write_dag("first", "plan-a", self.repository, steps_hold(marker, 20))
        other = self.write_dag("other", "plan-b", self.repository, steps_hold(marker, 1))
        running = self.start_detached(first, "run_first")
        self.wait_for(
            lambda: resolve_ref(self.repository, RUN_LOCK_REF) is not None,
            "the first run to take the lock",
        )
        refused = self.start(other, "run_other")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("plan-a", refused.stdout + refused.stderr)
        running.wait(timeout=300)

    def test_two_repositories_run_at_the_same_time(self) -> None:
        other = self.make_repository("other-repo")
        marker = f"cairn-hold-{os.getpid()}"
        first = self.write_dag("first", "plan-a", self.repository, steps_hold(marker, 20))
        second = self.write_dag("second", "plan-a", other, steps_hold(marker, 1))
        running = self.start_detached(first, "run_first")
        self.wait_for(
            lambda: resolve_ref(self.repository, RUN_LOCK_REF) is not None,
            "the first run to take the lock",
        )
        concurrent = self.start(second, "run_second")
        self.assertEqual(concurrent.returncode, 0, concurrent.stdout)
        # Exit zero alone would pass on a machine slow enough that the first run had
        # already finished, so the first repository is asserted still held.
        held = read_run_lock(self.repository)
        self.assertIsNotNone(held)
        assert held is not None
        self.assertEqual(held[0]["run_id"], "run_first")
        running.wait(timeout=300)

    def test_a_failed_run_gives_its_repository_back(self) -> None:
        failing = self.write_dag(
            "failing",
            "plan-a",
            self.repository,
            [{"name": "boom", "run": "exit 3", "depends": ["lock_acquire"]}],
        )
        outcome = self.start(failing, "run_failing")
        self.assertNotEqual(outcome.returncode, 0)
        self.assertIsNone(
            resolve_ref(self.repository, RUN_LOCK_REF),
            "a failed run must not hold the repository for its whole reclaim window",
        )
        # And the repository is immediately usable again, which is the point.
        recovery = self.write_dag(
            "recovery", "plan-a", self.repository, steps_hold("cairn-ok", 1)
        )
        self.assertEqual(self.start(recovery, "run_recovery").returncode, 0)


class Adversarial(EngineCase):
    def killed_run(self, marker: str, *, run_timeout: int = 600) -> subprocess.Popen[str]:
        dag = self.write_dag(
            "victim",
            "plan-a",
            self.repository,
            steps_hold(marker, SENTINEL_SECONDS),
            run_timeout=run_timeout,
        )
        running = self.start_detached(dag, "run_victim")
        self.wait_for(
            lambda: bool(self.matching_processes(marker)), "the step to start working"
        )
        return running

    def test_killing_the_orchestrator_leaves_no_step_process_alive(self) -> None:
        marker = f"cairn-victim-{os.getpid()}"
        running = self.killed_run(marker)
        os.kill(running.pid, signal.SIGKILL)
        running.wait(timeout=30)
        self.wait_for(
            lambda: not self.matching_processes(marker),
            "every step process to be reaped",
            seconds=30,
        )
        self.assertEqual(self.matching_processes(marker), [])

    def test_killing_the_whole_tree_leaves_no_step_process_alive(self) -> None:
        marker = f"cairn-tree-{os.getpid()}"
        running = self.killed_run(marker)
        os.killpg(os.getpgid(running.pid), signal.SIGKILL)
        running.wait(timeout=30)
        self.wait_for(
            lambda: not self.matching_processes(marker),
            "every step process to be reaped",
            seconds=30,
        )

    def test_a_killed_runs_record_reconciles_without_a_scheduler(self) -> None:
        marker = f"cairn-record-{os.getpid()}"
        running = self.killed_run(marker)
        os.kill(running.pid, signal.SIGKILL)
        running.wait(timeout=30)

        frozen = [
            path
            for path in self.status_files()
            if (last_record(path) or {}).get("status") == STATUS_RUNNING
        ]
        self.assertTrue(frozen, "the engine should leave the killed run frozen at running")

        results = reconcile(self.home / "data" / "dag-runs")
        self.assertTrue(any(result.changed for result in results))
        for path in frozen:
            record = last_record(path)
            assert record is not None
            self.assertEqual(record["status"], STATUS_FAILED)
            self.assertTrue(record["finishedAt"])
            for node in record.get("nodes", []):
                self.assertNotEqual(node["status"], STATUS_RUNNING)

    def test_a_killed_runs_lock_is_reclaimed_with_no_operator_procedure(self) -> None:
        # A four-second run declares a five-second window, so the recovery this asserts is
        # the real one: no edit, no operator step, just the arithmetic the plan itself gave.
        marker = f"cairn-lock-{os.getpid()}"
        running = self.killed_run(marker, run_timeout=4)
        self.wait_for(
            lambda: resolve_ref(self.repository, RUN_LOCK_REF) is not None,
            "the run to take the lock",
        )
        os.kill(running.pid, signal.SIGKILL)
        running.wait(timeout=30)
        self.wait_for(
            lambda: not self.matching_processes(marker), "the step to be reaped", seconds=30
        )

        held = read_run_lock(self.repository)
        self.assertIsNotNone(held, "the crash leaves the lock held, which is the hazard")
        assert held is not None

        recovery = self.write_dag(
            "recovery",
            "plan-a",
            self.repository,
            steps_hold(marker + "-ok", 1),
            run_timeout=4,
        )
        self.wait_for(
            lambda: time.time() >= held[0]["reclaim_after"],
            "the window the killed run itself declared",
            seconds=120,
        )
        converged = self.start(recovery, "run_recovery")
        self.assertEqual(converged.returncode, 0, converged.stdout)
        self.assertIsNone(resolve_ref(self.repository, RUN_LOCK_REF))

    def test_killing_a_step_mid_edit_leaves_a_worktree_that_converges_on_re_run(
        self,
    ) -> None:
        """07's exit criterion: proven by killing a step, not by staging the aftermath."""
        marker = f"cairn-edit-{os.getpid()}"
        plan = "plan-a"
        worktree = worktrees_root_for(self.repository, plan) / "alpha"
        cairn = f"{sys.executable} -m cairn"
        body: list[dict[str, Any]] = [
            {
                "name": "setup_alpha",
                "run": (
                    f"{cairn} worktree setup --plan {plan} --step alpha "
                    f"--branch step/alpha"
                ),
                "depends": ["lock_acquire"],
            },
            {
                "name": "work_alpha",
                "run": (
                    f"{sys.executable} -c 'import pathlib,time; "
                    f'pathlib.Path("half-written.txt").write_text("agent output"); '
                    f"time.sleep({SENTINEL_SECONDS})' {marker}"
                ),
                "working_dir": str(worktree),
                "depends": ["setup_alpha"],
            },
        ]
        dag = self.write_dag("edit", "plan-a", self.repository, body, run_timeout=4)
        running = self.start_detached(dag, "run_edit")
        self.wait_for(
            lambda: (worktree / "half-written.txt").exists(), "the step to start editing"
        )

        os.kill(running.pid, signal.SIGKILL)
        running.wait(timeout=30)
        self.wait_for(
            lambda: not self.matching_processes(marker), "the step to be reaped", seconds=30
        )
        self.assertTrue(
            (worktree / "half-written.txt").exists(),
            "the killed step's output must survive the kill",
        )

        # Re-running is the whole recovery procedure. It converges the worktree and keeps
        # what the killed agent had written.
        held = read_run_lock(self.repository)
        assert held is not None
        self.wait_for(
            lambda: time.time() >= held[0]["reclaim_after"],
            "the window the killed run declared",
            seconds=120,
        )
        again = self.write_dag(
            "edit_again",
            "plan-a",
            self.repository,
            [
                {
                    "name": "setup_alpha",
                    "run": (
                        f"{cairn} worktree setup --plan plan-a --step alpha "
                        f"--branch step/alpha"
                    ),
                    "depends": ["lock_acquire"],
                }
            ],
            run_timeout=4,
        )
        converged = self.start(again, "run_edit_again")
        self.assertEqual(converged.returncode, 0, converged.stdout)
        self.assertTrue((worktree / "half-written.txt").exists())

    def test_killing_a_single_step_ends_the_run_and_frees_the_repository_at_once(
        self,
    ) -> None:
        # The orchestrator survives a step's death, so it reaches its own exit handler:
        # the lock comes back immediately and the next run waits for nothing.
        marker = f"cairn-step-{os.getpid()}"
        running = self.killed_run(marker, run_timeout=4)
        victims = self.matching_processes(marker)
        self.assertTrue(victims)
        for pid in victims:
            os.kill(int(pid), signal.SIGKILL)
        self.assertNotEqual(running.wait(timeout=60), 0)
        self.wait_for(
            lambda: not self.matching_processes(marker), "the step to be gone", seconds=30
        )
        self.assertIsNone(resolve_ref(self.repository, RUN_LOCK_REF))
        recovery = self.write_dag(
            "recovery", "plan-a", self.repository, steps_hold(marker + "-ok", 1), run_timeout=4
        )
        self.assertEqual(self.start(recovery, "run_recovery").returncode, 0)

    def test_a_killed_step_leaves_a_stale_index_lock_that_the_next_write_clears(
        self,
    ) -> None:
        lock = self.repository / ".git" / "index.lock"
        lock.write_text("", encoding="utf-8")
        os.utime(lock, (time.time() - 3600, time.time() - 3600))
        with git_write_mutex(self.repository):
            self.assertFalse(lock.exists())
        git(self.repository, ("status", "--porcelain"))


class FanOut(EngineCase):
    def test_independent_steps_run_together_with_real_worktrees_and_the_mutex(
        self,
    ) -> None:
        """The mutex sits in the middle of this and the fan-out still happens."""
        steps, seconds = 4, 4
        plan = "fan-out"
        body: list[dict[str, Any]] = []
        for index in range(steps):
            worktree = worktrees_root_for(self.repository, plan) / f"s{index}"
            body.extend(
                [
                    {
                        "name": f"setup_s{index}",
                        "run": (
                            f"{sys.executable} -m cairn worktree setup "
                            f"--plan {plan} --step s{index} --branch step/s{index}"
                        ),
                        "depends": ["lock_acquire"],
                    },
                    {
                        "name": f"work_s{index}",
                        "run": (
                            f"{sys.executable} -c 'import pathlib,time; "
                            f'pathlib.Path("out.txt").write_text("x"); '
                            f"time.sleep({seconds})'"
                        ),
                        "working_dir": str(worktree),
                        "depends": [f"setup_s{index}"],
                    },
                    {
                        "name": f"commit_s{index}",
                        "run": f"{sys.executable} -m cairn commit --message 'cairn(s{index}): work'",
                        "working_dir": str(worktree),
                        "depends": [f"work_s{index}"],
                    },
                ]
            )
        dag = self.write_dag("fanout", plan, self.repository, body)
        started = time.monotonic()
        outcome = self.start(dag, "run_fanout")
        elapsed = time.monotonic() - started
        self.assertEqual(outcome.returncode, 0, outcome.stdout)
        for index in range(steps):
            self.assertTrue(
                (worktrees_root_for(self.repository, plan) / f"s{index}" / "out.txt").exists()
            )

        # "Faster than fully serial" would still pass on a run that managed only two at a
        # time, so the bar is the criterion's own: approximately the longest step, not the
        # sum. The overhead allowance is measured rather than guessed.
        control = self.write_dag(
            "control",
            "plan-a",
            self.repository,
            [
                {
                    "name": "work_only",
                    "run": f"{sys.executable} -c 'import time; time.sleep({seconds})'",
                    "depends": ["lock_acquire"],
                }
            ],
        )
        base = time.monotonic()
        self.assertEqual(self.start(control, "run_control").returncode, 0)
        overhead = max(0.0, (time.monotonic() - base) - seconds)
        self.assertLess(elapsed, seconds + overhead * 3 + steps)


class SchedulerRetry(EngineCase):
    def write_foreign_dag(self, name: str) -> Path:
        """A workflow Cairn did not write: no retry policy of its own, so only the
        machine-wide base configuration can stop the scanner re-executing it."""
        path = self.dags / f"{name}.yaml"
        path.write_text(
            json.dumps(
                {
                    "type": "graph",
                    "steps": [
                        {
                            "name": "fails",
                            "run": "exit 3",
                            "working_dir": str(self.repository),
                            "timeout_sec": 60,
                        }
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return path

    def test_starting_a_scheduler_re_executes_no_failed_cairn_run(self) -> None:
        """The scanner reaches runs the scheduler never watched, so this is the hazard."""
        failing = self.write_dag(
            "failing",
            "plan-a",
            self.repository,
            [{"name": "fails", "run": "exit 3", "depends": ["lock_acquire"]}],
            release=False,
        )
        outcome = self.start(failing, "run_failing")
        self.assertNotEqual(outcome.returncode, 0)
        # The half that only base.yaml can close: a plan Cairn never wrote, carrying no
        # policy of its own. Without the base configuration this one is re-executed.
        foreign = self.write_foreign_dag("foreign")
        self.assertNotEqual(self.start(foreign, "run_foreign").returncode, 0)

        before = self._attempt_count()
        scheduler = subprocess.Popen(
            [str(ENGINE), "scheduler", "--dags", str(self.dags)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=self.environment(),
        )
        self.addCleanup(self._reap, scheduler)
        # The scanner's own cadence is 45 seconds and it retries three times; a minute of
        # quiet with the disabling policy in place is the assertion.
        time.sleep(75)
        scheduler.terminate()
        scheduler.wait(timeout=30)
        self.assertEqual(self._attempt_count(), before)

    def _attempt_count(self) -> int:
        return len(self.status_files())


if __name__ == "__main__":
    unittest.main()
