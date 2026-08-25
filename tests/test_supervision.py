"""Doc 09's locks and repairs, and doc 07's worktree convergence, against real git."""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

from cairn.baseconfig import (
    assert_dag_retry_disabled,
    base_config_path,
    ensure_dag_retry_disabled,
    read_base_retry_policy,
)
from cairn.core import CairnError
from cairn.gitio import (
    REDIRECTING_VARIABLES,
    REF_LOCK_TIMEOUT_MILLISECONDS,
    GitOutcome,
    absolute_directory,
    common_directory,
    git,
    hash_object,
    refuse_unusable_repository,
    resolve_ref,
    update_ref,
)
from cairn.layout import reports_directory
from cairn.liveness import (
    parse_elapsed,
    process_is_alive,
    process_start_time,
    self_start_time,
)
from cairn.locks import (
    RECLAIM_FACTOR,
    RUN_LOCK_REF,
    LockRecord,
    acquire_run_lock,
    clear_stale_git_locks,
    git_write_mutex,
    holder_liveness,
    read_run_lock,
    reclaimability,
    refuse_dirty_repository,
    refuse_lost_repository,
    refuse_unresolved_merge,
    release_run_lock,
    stale_git_locks,
    taking_is_allowed,
    unresolved_merge,
)
from cairn.supervise import (
    ALREADY_TERMINAL,
    NO_RECORD,
    OWNER_UNKNOWN,
    RECONCILED,
    RECONCILED_ERROR,
    STATUS_FAILED,
    STATUS_RUNNING,
    STILL_RUNNING,
    UNRECOGNISED,
    WOULD_RECONCILE,
    last_record,
    reconcile,
    reconcile_status_file,
)
from cairn.topology import worktrees_root_for
from cairn.worktrees import (
    ABSENT,
    ANCESTOR_OF_PARENT,
    ELSEWHERE,
    FOREIGN,
    HEALTHY,
    INTERRUPTED,
    JUNK,
    LOCKED,
    MERGED_BEHIND,
    REPAIRABLE,
    SAME_AS_PARENT,
    STALE_REGISTRATION,
    STATES,
    UNCLASSIFIED,
    UNMERGED,
    UNREADABLE,
    WRONG_BRANCH,
    Facts,
    classify,
    commit_all,
    prune_worktrees,
    setup_worktree,
)

# Above the platform's maximum process identifier, so it can never name a live process.
IMPOSSIBLE_PID = 99_999_999
PACKAGE_ROOT = Path(__file__).parents[1]

def reports_of(root: Path, run_id: str) -> Path:
    """Where a run's accounts land, composed the way a step composes it for itself."""
    return reports_directory(root / "runs", run_id)



def make_repository(root: Path, name: str = "repo") -> Path:
    repository = root / name
    repository.mkdir(parents=True)
    git(repository, ("init", "--initial-branch=main", "--quiet", "."))
    git(repository, ("config", "user.email", "cairn@test"))
    git(repository, ("config", "user.name", "Cairn Test"))
    (repository / "README.md").write_text("start\n", encoding="utf-8")
    git(repository, ("add", "--all"))
    git(repository, ("commit", "--quiet", "-m", "init"))
    return repository


def advance(repository: Path, filename: str) -> str:
    (repository / filename).write_text(f"{filename}\n", encoding="utf-8")
    git(repository, ("add", "--all"))
    git(repository, ("commit", "--quiet", "-m", f"add {filename}"))
    return git(repository, ("rev-parse", "HEAD")).stdout


def stage_conflict(repository: Path) -> None:
    """Leave the repository mid-merge with a conflicted index, the way a kill would."""
    advance(repository, "base.txt")
    git(repository, ("checkout", "--quiet", "-b", "other"))
    (repository / "clash.txt").write_text("theirs\n", encoding="utf-8")
    git(repository, ("add", "--all"))
    git(repository, ("commit", "--quiet", "-m", "theirs"))
    git(repository, ("checkout", "--quiet", "main"))
    (repository / "clash.txt").write_text("ours\n", encoding="utf-8")
    git(repository, ("add", "--all"))
    git(repository, ("commit", "--quiet", "-m", "ours"))
    git(repository, ("merge", "other"), check=False)


def child_python(script: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run a helper in its own process, reaching the package the way an engine step does."""
    return subprocess.run(
        [sys.executable, "-c", script, *arguments],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "PYTHONPATH": str(PACKAGE_ROOT)},
        check=False,
    )


class RepositoryCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name).resolve()
        self.repository = make_repository(self.root)
        self.addCleanup(self._temporary.cleanup)


class Liveness(RepositoryCase):
    def test_this_process_is_alive_at_its_own_start_time(self) -> None:
        started = self_start_time()
        self.assertIsNotNone(started)
        self.assertTrue(process_is_alive(os.getpid(), started))

    def test_a_recycled_identifier_is_not_the_process_that_was_recorded(self) -> None:
        started = self_start_time()
        self.assertIsNotNone(started)
        self.assertFalse(process_is_alive(os.getpid(), (started or 0) - 3600))

    def test_an_identifier_naming_nothing_is_not_alive(self) -> None:
        self.assertIsNone(process_start_time(IMPOSSIBLE_PID))
        self.assertFalse(process_is_alive(IMPOSSIBLE_PID, None))

    def test_elapsed_time_is_parsed_in_every_form_ps_prints_it(self) -> None:
        # Read as elapsed rather than as a civil start date: `ps -o lstart=` shifts by an
        # hour across a daylight-saving boundary, and against a seconds-wide tolerance
        # that would call a live run dead and write a terminal status into its record.
        self.assertEqual(parse_elapsed("03"), 3)
        self.assertEqual(parse_elapsed("01:30"), 90)
        self.assertEqual(parse_elapsed("02:00:00"), 7200)
        self.assertEqual(parse_elapsed("2-04:00:00"), 2 * 86400 + 4 * 3600)
        self.assertIsNone(parse_elapsed("not a duration"))


class GitInvocation(RepositoryCase):
    def test_an_inherited_git_dir_never_redirects_a_write(self) -> None:
        # `GIT_DIR` overrides discovery from the working directory outright, so one left
        # in the environment by a shell, a hook or a parent process would send every
        # commit a plan makes into a repository nobody named.
        elsewhere = make_repository(self.root, "elsewhere")
        (self.repository / "work.txt").write_text("mine\n", encoding="utf-8")
        with patch.dict(
            os.environ,
            {
                "GIT_DIR": str(common_directory(elsewhere)),
                "GIT_WORK_TREE": str(elsewhere),
            },
        ):
            commit_all(self.repository, "cairn(a): work")
        self.assertIn("work.txt", git(self.repository, ("show", "--name-only", "HEAD")).stdout)
        self.assertEqual(git(elsewhere, ("rev-list", "--count", "HEAD")).stdout, "1")

    def test_no_redirecting_variable_survives_into_a_git_invocation(self) -> None:
        # Every name at once, each pointing somewhere unusable: any one of them still
        # honoured makes git fail or answer about the wrong repository.
        nowhere = str(self.root / "nowhere")
        with patch.dict(os.environ, {name: nowhere for name in REDIRECTING_VARIABLES}):
            self.assertEqual(git(self.repository, ("rev-list", "--count", "HEAD")).stdout, "1")
            self.assertEqual(
                git(self.repository, ("rev-parse", "--show-toplevel")).stdout,
                str(self.repository),
            )

    def test_ref_writes_wait_for_a_contended_lock_rather_than_failing(self) -> None:
        # An agent commits in its own worktree outside the write mutex, so a ref lock
        # collision is expected traffic rather than a fault. git gives up in 100ms by
        # default, which inside a paid step is a spurious failure.
        for setting in ("core.filesRefLockTimeout", "core.packedRefsTimeout"):
            self.assertEqual(
                git(self.repository, ("config", "--get", setting)).stdout,
                str(REF_LOCK_TIMEOUT_MILLISECONDS),
            )

    def test_a_path_the_engine_left_unresolved_is_refused_before_git_sees_it(self) -> None:
        # An unresolved engine reference expands to the empty string and the step still
        # runs, so git would fall back to wherever the step happened to start.
        for empty in ("", "   "):
            with self.assertRaisesRegex(CairnError, "is empty"):
                absolute_directory(empty, "worktree")
        with self.assertRaisesRegex(CairnError, "not an absolute path"):
            absolute_directory("relative/path", "worktree")
        self.assertEqual(absolute_directory(str(self.repository), "repository"), self.repository)

    def test_a_bare_repository_has_no_working_tree_to_run_a_step_in(self) -> None:
        bare = self.root / "bare.git"
        git(self.root, ("init", "--bare", "--quiet", str(bare)))
        with self.assertRaisesRegex(CairnError, "bare repository"):
            refuse_unusable_repository(bare)

    def test_a_submodule_would_lock_its_superproject_too(self) -> None:
        inner = make_repository(self.root, "inner")
        git(
            self.repository,
            ("-c", "protocol.file.allow=always", "submodule", "add", "--quiet", str(inner), "sub"),
        )
        git(self.repository, ("commit", "--quiet", "-m", "add submodule"))
        with self.assertRaisesRegex(CairnError, "is a submodule of"):
            refuse_unusable_repository(self.repository / "sub")
        refuse_unusable_repository(self.repository)


class StaleGitLocks(RepositoryCase):
    def test_a_young_lock_is_left_for_the_process_that_may_still_hold_it(self) -> None:
        lock = common_directory(self.repository) / "index.lock"
        lock.write_text("", encoding="utf-8")
        self.assertEqual(stale_git_locks(self.repository), [])
        self.assertEqual(clear_stale_git_locks(self.repository), [])
        self.assertTrue(lock.exists())

    def test_an_old_lock_is_debris_a_killed_step_left(self) -> None:
        lock = common_directory(self.repository) / "index.lock"
        lock.write_text("", encoding="utf-8")
        os.utime(lock, (time.time() - 3600, time.time() - 3600))
        self.assertEqual(clear_stale_git_locks(self.repository), [str(lock)])
        self.assertFalse(lock.exists())

    def test_a_stale_ref_lock_is_cleared_too(self) -> None:
        lock = common_directory(self.repository) / "refs" / "heads" / "main.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text("", encoding="utf-8")
        os.utime(lock, (time.time() - 3600, time.time() - 3600))
        self.assertEqual(clear_stale_git_locks(self.repository), [str(lock)])

    def test_a_linked_worktrees_own_index_lock_is_swept_too(self) -> None:
        # A killed isolated step leaves its lock in the worktree's admin directory, not in
        # the repository's — sweeping only the latter would miss every fan-out casualty.
        worktree = self.root / "trees" / "alpha"
        setup_worktree(self.repository, worktree, "step/alpha", "main")
        admin = Path(
            (worktree / ".git").read_text(encoding="utf-8").split("gitdir:")[1].strip()
        )
        lock = admin / "index.lock"
        lock.write_text("", encoding="utf-8")
        os.utime(lock, (time.time() - 3600, time.time() - 3600))
        self.assertIn(str(lock), clear_stale_git_locks(self.repository))
        self.assertFalse(lock.exists())

    def test_cairns_own_mutex_file_is_never_swept(self) -> None:
        with git_write_mutex(self.repository):
            pass
        mutex = common_directory(self.repository) / "cairn" / "git-write.lock"
        os.utime(mutex, (time.time() - 86400, time.time() - 86400))
        clear_stale_git_locks(self.repository, older_than_seconds=1)
        self.assertTrue(mutex.exists())

    def test_the_mutex_clears_debris_on_the_way_in(self) -> None:
        lock = common_directory(self.repository) / "index.lock"
        lock.write_text("", encoding="utf-8")
        os.utime(lock, (time.time() - 3600, time.time() - 3600))
        with git_write_mutex(self.repository):
            self.assertFalse(lock.exists())


class GitWriteMutex(RepositoryCase):
    HOLD = (
        "import sys, time\n"
        "from pathlib import Path\n"
        "from cairn.locks import git_write_mutex\n"
        "with git_write_mutex(Path(sys.argv[1]), wait_seconds=float(sys.argv[2])):\n"
        "    print('held', flush=True)\n"
        "    time.sleep(float(sys.argv[3]))\n"
    )

    def test_it_is_held_by_one_holder_at_a_time(self) -> None:
        order: list[str] = []
        contending = threading.Event()

        def second() -> None:
            contending.set()
            with git_write_mutex(self.repository, wait_seconds=30):
                order.append("second")

        with git_write_mutex(self.repository, wait_seconds=30):
            worker = threading.Thread(target=second)
            worker.start()
            self.assertTrue(contending.wait(timeout=10))
            time.sleep(0.2)
            order.append("first")
        worker.join(timeout=30)
        self.assertEqual(order, ["first", "second"])

    def test_it_excludes_another_process_and_not_only_another_thread(self) -> None:
        with git_write_mutex(self.repository):
            child = child_python(self.HOLD, str(self.repository), "0.2", "5")
        self.assertNotEqual(child.returncode, 0)
        self.assertIn("git write mutex", child.stderr)

    def test_a_holder_that_dies_leaves_the_mutex_free(self) -> None:
        child = subprocess.Popen(
            [sys.executable, "-c", self.HOLD, str(self.repository), "30", "60"],
            stdout=subprocess.PIPE,
            text=True,
            env={**os.environ, "PYTHONPATH": str(PACKAGE_ROOT)},
        )
        self.addCleanup(child.kill)
        assert child.stdout is not None
        self.assertEqual(child.stdout.readline().strip(), "held")
        child.kill()
        child.wait(timeout=10)
        with git_write_mutex(self.repository, wait_seconds=10):
            pass

    def test_the_mutex_file_names_only_its_current_holder(self) -> None:
        with git_write_mutex(self.repository):
            pass
        mutex = common_directory(self.repository) / "cairn" / "git-write.lock"
        self.assertEqual(mutex.read_text(encoding="utf-8").count("\n"), 1)


class RepositoryState(RepositoryCase):
    def conflict(self) -> None:
        stage_conflict(self.repository)

    def test_a_clean_repository_has_nothing_in_progress(self) -> None:
        self.assertIsNone(unresolved_merge(self.repository))
        refuse_dirty_repository(self.repository)

    def test_a_conflicted_merge_is_named_and_halts(self) -> None:
        self.conflict()
        self.assertEqual(unresolved_merge(self.repository), "a merge")
        with self.assertRaisesRegex(CairnError, "merge"):
            refuse_unresolved_merge(self.repository)

    def test_every_half_finished_operation_halts_a_write_not_only_a_merge(self) -> None:
        advance(self.repository, "base.txt")
        git(self.repository, ("checkout", "--quiet", "-b", "other"))
        (self.repository / "clash.txt").write_text("theirs\n", encoding="utf-8")
        git(self.repository, ("add", "--all"))
        git(self.repository, ("commit", "--quiet", "-m", "theirs"))
        git(self.repository, ("checkout", "--quiet", "main"))
        (self.repository / "clash.txt").write_text("ours\n", encoding="utf-8")
        git(self.repository, ("add", "--all"))
        git(self.repository, ("commit", "--quiet", "-m", "ours"))

        git(self.repository, ("cherry-pick", "other"), check=False)
        self.assertEqual(unresolved_merge(self.repository), "a cherry-pick")
        git(self.repository, ("cherry-pick", "--abort"), check=False)

        git(self.repository, ("rebase", "other"), check=False)
        self.assertEqual(unresolved_merge(self.repository), "a rebase")
        git(self.repository, ("rebase", "--abort"), check=False)

    def test_a_conflicted_index_alone_is_enough_to_halt(self) -> None:
        self.conflict()
        admin = common_directory(self.repository)
        (admin / "MERGE_HEAD").unlink(missing_ok=True)
        (admin / "MERGE_MSG").unlink(missing_ok=True)
        self.assertEqual(
            unresolved_merge(self.repository), "an unresolved conflict in the index"
        )

    def test_a_commit_refuses_to_write_over_an_unresolved_merge(self) -> None:
        self.conflict()
        with self.assertRaises(CairnError) as caught:
            commit_all(self.repository, "should not land")
        self.assertEqual(caught.exception.cause, "merge_in_progress")

    def test_a_run_refuses_to_start_over_the_users_own_uncommitted_work(self) -> None:
        # A chain step commits in the repository itself and stages everything there, so
        # work the user left behind would land as a step's output.
        (self.repository / "mine.txt").write_text("not the plan's\n", encoding="utf-8")
        with self.assertRaises(CairnError) as caught:
            refuse_dirty_repository(self.repository)
        self.assertEqual(caught.exception.cause, "repository_dirty")
        self.assertIn("mine.txt", str(caught.exception))


class SubcommandsTakeTheMutex(RepositoryCase):
    """The mutex is only worth anything if the write paths actually hold it."""

    def step_environment(self, directory: Path) -> dict[str, str]:
        """The identity the engine gives a step, so the subcommand leaves a report.

        A generated workflow declares its per-target values as parameters, which the engine
        exports into every step's environment ([workflow.md]), so a subcommand driven outside
        the engine is given the same.
        """
        return {
            **os.environ,
            "PYTHONPATH": str(PACKAGE_ROOT),
            "DAG_RUN_ID": "run_probe",
            "DAG_RUN_STEP_NAME": "probe",
            "DAG_RUN_WORK_DIR": str(directory),
            "CAIRN_RUNS_DIR": str(self.root / "runs"),
            "CAIRN_PARENT_BRANCH": "main",
            "CAIRN_REPOSITORY": str(self.repository),
        }

    def run_subcommand(self, directory: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        script = (
            "import sys\n"
            "from cairn.__main__ import main\n"
            "sys.exit(main(sys.argv[1:]))\n"
        )
        return subprocess.run(
            [sys.executable, "-c", script, *arguments],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=directory,
            env=self.step_environment(directory),
            check=False,
        )

    def start_subcommand(self, directory: Path, *arguments: str) -> subprocess.Popen[str]:
        script = (
            "import sys\n"
            "from cairn.__main__ import main\n"
            "sys.exit(main(sys.argv[1:]))\n"
        )
        child = subprocess.Popen(
            [sys.executable, "-c", script, *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=directory,
            env=self.step_environment(directory),
        )
        self.addCleanup(child.kill)
        return child

    HELD_OUT_SECONDS = 8.0

    def assert_held_out(self, child: subprocess.Popen[str]) -> None:
        """Give the child every chance to finish, and require that it does not.

        Without the mutex the subcommand takes well under a second, so waiting far longer
        than it needs is what separates "blocked" from "slow to start".
        """
        try:
            child.wait(timeout=self.HELD_OUT_SECONDS)
        except subprocess.TimeoutExpired:
            return
        self.fail("the subcommand wrote without waiting for the git write mutex")

    def test_commit_is_held_out_while_the_mutex_is_taken(self) -> None:
        head = git(self.repository, ("rev-parse", "HEAD")).stdout
        (self.repository / "new.txt").write_text("content\n", encoding="utf-8")
        with git_write_mutex(self.repository):
            child = self.start_subcommand(
                self.repository, "commit", "--message", "cairn(a): waits its turn"
            )
            self.assert_held_out(child)
            self.assertEqual(
                git(self.repository, ("rev-parse", "HEAD")).stdout,
                head,
                "nothing may be committed while another writer holds the mutex",
            )
        self.assertEqual(child.wait(timeout=120), 0)
        self.assertNotEqual(git(self.repository, ("rev-parse", "HEAD")).stdout, head)
        self.assertEqual(
            git(self.repository, ("log", "-1", "--pretty=%s")).stdout,
            "cairn(a): waits its turn",
        )

    def test_an_agent_step_runs_while_another_step_holds_the_mutex(self) -> None:
        """The whole fan-out rests on this: the slow part is outside the lock.

        The mutex serialises git writes, which take milliseconds. If it ever came to cover
        an agent step too — an easy tightening to make, and one nothing else here would
        notice — every agent would queue behind every other and a wave of five hour-long
        steps would take five hours instead of one. Nothing measures wall-clock in the
        suite, so this asserts the structure the wall-clock depends on instead.
        """
        with git_write_mutex(self.repository):
            child = self.start_subcommand(
                self.repository,
                "agent",
                "run",
                "--provider",
                "nonesuch",
                "--prompt",
                "make it so",
            )
            try:
                child.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.fail("an agent step waited on the git write mutex")
        # An unknown provider, because the assertion is about how far the step gets, not
        # about what a session does. Reaching provider dispatch at all is already past
        # every point at which the mutex could have held it.
        report = json.loads((reports_of(self.root, "run_probe") / "probe.json").read_text())
        self.assertEqual(report["cause"], "provider_unavailable")

    def test_a_worktree_path_is_derived_rather_than_carried_in_the_body(self) -> None:
        # The hazard this closes is designed out rather than refused: a path argument could
        # arrive empty from an unresolved engine reference, and `Path("")` is `Path(".")` —
        # a step's own directory is its worktree, so the step would converge the worktree it
        # was standing in, check another step's branch out over it, and report `done`. There
        # is no such argument now; the path comes from the repository the step stands in.
        worktree = self.root / "repo.cairn-worktrees" / "demo" / "alpha"
        setup_worktree(self.repository, worktree, "step/alpha", "main")
        outcome = self.run_subcommand(
            self.repository, "worktree", "setup", "--worktree", str(worktree),
            "--branch", "step/alpha", "--base", "main",
        )
        self.assertNotEqual(outcome.returncode, 0)
        report = json.loads((reports_of(self.root, "run_probe") / "probe.json").read_text())
        self.assertEqual(report["cause"], "invalid_arguments")
        self.assertEqual(
            git(worktree, ("rev-parse", "--abbrev-ref", "HEAD")).stdout, "step/alpha"
        )

    def test_worktree_setup_is_held_out_too(self) -> None:
        worktree = worktrees_root_for(self.repository, "demo") / "alpha"
        with git_write_mutex(self.repository):
            child = self.start_subcommand(
                self.repository,
                "worktree",
                "setup",
                "--plan",
                "demo",
                "--step",
                "alpha",
                "--branch",
                "step/alpha",
            )
            self.assert_held_out(child)
            self.assertFalse(
                worktree.exists(),
                "no worktree may be checked out while another writer holds the mutex",
            )
        self.assertEqual(child.wait(timeout=120), 0)
        self.assertTrue((worktree / "README.md").exists())

    def test_lock_acquire_refuses_an_engine_that_still_retries_whole_dags(self) -> None:
        base = self.root / "engine" / "base.yaml"
        base.parent.mkdir(parents=True, exist_ok=True)
        base.write_text(
            "retry_policy:\n  limit: 3\n  interval_sec: 5\n", encoding="utf-8"
        )
        outcome = self.run_subcommand(
            self.repository,
            "lock",
            "acquire",
            "--plan",
            "demo",
            "--run-timeout",
            "600",
            "--base-config",
            str(base),
        )
        self.assertNotEqual(outcome.returncode, 0)
        report = json.loads((reports_of(self.root, "run_probe") / "probe.json").read_text())
        self.assertEqual(report["cause"], "base_retry_enabled")

    def test_lock_acquire_refuses_a_repository_with_the_users_uncommitted_work(
        self,
    ) -> None:
        base = self.root / "engine" / "base.yaml"
        base.parent.mkdir(parents=True, exist_ok=True)
        base.write_text(
            "retry_policy:\n  limit: 0\n  interval_sec: 1\n", encoding="utf-8"
        )
        (self.repository / "mine.txt").write_text("not the plan's\n", encoding="utf-8")
        outcome = self.run_subcommand(
            self.repository,
            "lock",
            "acquire",
            "--plan",
            "demo",
            "--run-timeout",
            "600",
            "--base-config",
            str(base),
        )
        self.assertNotEqual(outcome.returncode, 0)
        report = json.loads((reports_of(self.root, "run_probe") / "probe.json").read_text())
        self.assertEqual(report["cause"], "repository_dirty")

    def test_lock_acquire_refuses_a_repository_with_a_half_finished_merge(self) -> None:
        base = self.root / "engine" / "base.yaml"
        base.parent.mkdir(parents=True, exist_ok=True)
        base.write_text(
            "retry_policy:\n  limit: 0\n  interval_sec: 1\n", encoding="utf-8"
        )
        stage_conflict(self.repository)
        outcome = self.run_subcommand(
            self.repository,
            "lock",
            "acquire",
            "--plan",
            "demo",
            "--run-timeout",
            "600",
            "--base-config",
            str(base),
        )
        self.assertNotEqual(outcome.returncode, 0)
        report = json.loads((reports_of(self.root, "run_probe") / "probe.json").read_text())
        self.assertEqual(report["cause"], "merge_in_progress")


class RunLock(RepositoryCase):
    def acquire(
        self,
        run_id: str = "run_1",
        timeout: float = 600.0,
        status_file: str | None = None,
    ) -> Any:
        return acquire_run_lock(
            self.repository,
            run_id=run_id,
            plan="demo",
            run_timeout_seconds=timeout,
            status_file=status_file,
        )

    def plant(self, **overrides: Any) -> str:
        record: dict[str, Any] = {
            "run_id": "ghost",
            "plan": "demo",
            "repository": str(common_directory(self.repository)),
            "host": os.uname().nodename,
            "pid": IMPOSSIBLE_PID,
            "pid_started_at": None,
            "acquired_at": time.time(),
            "run_timeout_seconds": 600.0,
            "reclaim_after": time.time() + 100000,
            "status_file": None,
        }
        record.update(overrides)
        object_id = hash_object(self.repository, json.dumps(record))
        self.assertTrue(update_ref(self.repository, f"create {RUN_LOCK_REF} {object_id}"))
        return object_id

    def engine_record(self, run_id: str, pid: int, started_at: float | None) -> Path:
        """A status file shaped the way the engine writes one for a run in progress."""
        path = self.root / "dag-runs" / run_id / "status.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        record: dict[str, Any] = {"dagRunId": run_id, "status": 1, "pid": pid}
        if started_at is not None:
            record["pidStartedAt"] = started_at * 1000.0
        path.write_text(json.dumps(record) + "\n", encoding="utf-8")
        return path

    def test_a_live_run_keeps_its_repository_past_its_own_estimate(self) -> None:
        # The estimate bounds what a plan may declare, not what a live run is allowed to
        # finish. Reclaiming here would take the repository from a run still writing to it.
        status = self.engine_record("run_1", os.getpid(), self_start_time())
        self.acquire("run_1", timeout=1.0, status_file=str(status))
        time.sleep(1.5)
        record, _ = read_run_lock(self.repository) or (None, None)
        assert record is not None
        self.assertTrue(reclaimability(record).reclaimable, "the window has passed")
        self.assertFalse(taking_is_allowed(record).reclaimable)
        with self.assertRaises(CairnError) as caught:
            self.acquire("run_2")
        self.assertEqual(caught.exception.cause, "repository_busy")

    def test_a_run_whose_orchestrator_is_gone_gives_the_repository_up_at_once(self) -> None:
        # No waiting out a window measured for a crash nobody can see: the record proves
        # the orchestrator is gone, so the next run may have the repository now.
        status = self.engine_record("run_1", IMPOSSIBLE_PID, None)
        self.acquire("run_1", timeout=100_000.0, status_file=str(status))
        record, _ = read_run_lock(self.repository) or (None, None)
        assert record is not None
        self.assertFalse(reclaimability(record).reclaimable, "the window is far off")
        self.assertTrue(taking_is_allowed(record).reclaimable)
        held = self.acquire("run_2")
        self.assertIsNotNone(held.reclaimed_from)

    def test_an_unfindable_record_leaves_the_window_to_decide(self) -> None:
        self.acquire("run_1", timeout=100_000.0, status_file=str(self.root / "gone.jsonl"))
        record, _ = read_run_lock(self.repository) or (None, None)
        assert record is not None
        self.assertIsNone(holder_liveness(record))
        self.assertFalse(taking_is_allowed(record).reclaimable)

    def test_a_repository_that_will_not_answer_is_not_read_as_an_absent_lock(self) -> None:
        # Failing open here would spend a whole agent budget in a repository another run
        # may well own. Only "there is no repository here" is silence.
        self.acquire("holder")
        with patch(
            "cairn.locks.read_run_lock",
            side_effect=CairnError("git_failed", "git cat-file blob failed"),
        ), self.assertRaises(CairnError) as caught:
            refuse_lost_repository(self.repository, "displaced")
        self.assertEqual(caught.exception.cause, "git_failed")

    def test_a_run_that_lost_the_repository_halts_before_it_spends(self) -> None:
        self.acquire("holder")
        with self.assertRaises(CairnError) as caught:
            refuse_lost_repository(self.repository, "displaced")
        self.assertEqual(caught.exception.cause, "lock_not_held")
        self.assertIn("holder", str(caught.exception))

    def test_the_guard_reads_silence_as_silence_rather_than_as_loss(self) -> None:
        # These subcommands are the step vocabulary and stand on their own; an absent lock
        # is not evidence that one was taken away, and neither is a plain directory.
        refuse_lost_repository(self.repository, "nobody")
        refuse_lost_repository(self.root, "nobody")
        self.acquire("mine")
        refuse_lost_repository(self.repository, "mine")

    def test_a_free_repository_is_taken_and_the_holder_is_recorded(self) -> None:
        held = self.acquire()
        self.assertEqual(held.record["run_id"], "run_1")
        self.assertIsNone(held.reclaimed_from)
        stored = read_run_lock(self.repository)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored[0]["run_id"], "run_1")

    def test_a_second_run_is_refused_and_the_holder_is_named(self) -> None:
        self.acquire("run_1")
        with self.assertRaises(CairnError) as caught:
            self.acquire("run_2")
        self.assertEqual(caught.exception.cause, "repository_busy")
        self.assertIn("run_1", str(caught.exception))
        self.assertIn("demo", str(caught.exception))

    def test_a_different_plan_against_one_repository_is_refused_too(self) -> None:
        self.acquire("run_1")
        with self.assertRaises(CairnError) as caught:
            acquire_run_lock(
                self.repository,
                run_id="run_2",
                plan="a-different-plan",
                run_timeout_seconds=600,
            )
        self.assertEqual(caught.exception.cause, "repository_busy")

    def test_two_repositories_do_not_contend(self) -> None:
        other = make_repository(self.root, "other")
        self.acquire("run_1")
        held = acquire_run_lock(
            other, run_id="run_2", plan="demo", run_timeout_seconds=600
        )
        self.assertEqual(held.record["run_id"], "run_2")

    def test_two_worktrees_of_one_repository_are_one_contender(self) -> None:
        worktree = self.root / "wt"
        git(self.repository, ("worktree", "add", "--quiet", str(worktree), "-b", "side"))
        self.acquire("run_1")
        with self.assertRaises(CairnError) as caught:
            acquire_run_lock(
                worktree, run_id="run_2", plan="demo", run_timeout_seconds=600
            )
        self.assertEqual(caught.exception.cause, "repository_busy")

    def test_the_holder_releases_and_the_repository_frees(self) -> None:
        self.acquire("run_1")
        release_run_lock(self.repository, run_id="run_1")
        self.assertIsNone(read_run_lock(self.repository))
        self.assertIsNone(resolve_ref(self.repository, RUN_LOCK_REF))

    def test_a_run_can_reacquire_its_own_lock_so_a_retry_is_not_blocked_by_itself(
        self,
    ) -> None:
        # `dagu retry` reuses the run identifier, so refusing here would make the
        # documented recovery impossible for the very run it recovers.
        first = self.acquire("run_1")
        again = self.acquire("run_1")
        self.assertEqual(again.record["run_id"], "run_1")
        self.assertIsNone(again.reclaimed_from)
        # And it renews rather than inheriting: an expired lease left expired would let a
        # third run reclaim the repository while the retry was writing to it.
        self.assertGreater(again.record["reclaim_after"], first.record["reclaim_after"])
        stored = read_run_lock(self.repository)
        assert stored is not None
        self.assertEqual(stored[0]["reclaim_after"], again.record["reclaim_after"])

    def test_retaking_an_expired_lease_does_not_leave_it_expired(self) -> None:
        self.plant(run_id="run_1", reclaim_after=1.0)
        again = self.acquire("run_1", timeout=600.0)
        self.assertGreater(again.record["reclaim_after"], time.time())
        # A third run must now be refused, where inheriting the expired record would have
        # let it take the repository out from under the retry.
        with self.assertRaises(CairnError) as caught:
            self.acquire("run_2")
        self.assertEqual(caught.exception.cause, "repository_busy")

    def test_a_ref_pointing_at_something_that_is_not_a_record_stays_reclaimable(
        self,
    ) -> None:
        # A permanent state, not a transient failure to read one: raising here would wedge
        # the repository with no way back that is not an operator procedure.
        head = git(self.repository, ("rev-parse", "HEAD")).stdout
        self.assertTrue(update_ref(self.repository, f"create {RUN_LOCK_REF} {head}"))
        self.assertIsNone(read_run_lock(self.repository))
        self.assertEqual(self.acquire("run_2").record["run_id"], "run_2")

    def test_a_run_that_lost_the_race_can_never_release_the_winner(self) -> None:
        self.acquire("run_1")
        with self.assertRaises(CairnError) as caught:
            release_run_lock(self.repository, run_id="run_2")
        self.assertEqual(caught.exception.cause, "lock_not_held")
        self.assertIsNotNone(read_run_lock(self.repository))

    def test_releasing_a_lock_that_was_never_taken_is_a_no_op(self) -> None:
        # The release runs however a run ends, including one refused before it took
        # anything, so nothing to give back is an outcome rather than a failure.
        self.assertIsNone(release_run_lock(self.repository, run_id="run_1"))

    def test_releasing_twice_is_a_no_op_the_second_time(self) -> None:
        self.acquire("run_1")
        self.assertIsNotNone(release_run_lock(self.repository, run_id="run_1"))
        self.assertIsNone(release_run_lock(self.repository, run_id="run_1"))

    def test_a_lock_past_its_window_is_reclaimed(self) -> None:
        self.plant(reclaim_after=1.0)
        held = self.acquire("run_2")
        self.assertIsNotNone(held.reclaimed_from)
        assert held.reclaimed_from is not None
        self.assertEqual(held.reclaimed_from["run_id"], "ghost")

    def test_a_dead_recorded_process_does_not_by_itself_free_the_lock(self) -> None:
        # The lock is taken by one short-lived step and returned by another, so the
        # process that recorded itself is already gone while the run is still going.
        # Reclaiming on that would take the lock off every live run on the machine.
        self.plant(pid=IMPOSSIBLE_PID)
        with self.assertRaises(CairnError) as caught:
            self.acquire("run_2")
        self.assertEqual(caught.exception.cause, "repository_busy")

    def test_a_refusal_says_what_would_have_to_change_to_free_the_repository(self) -> None:
        self.acquire("run_1", timeout=600.0)
        with self.assertRaises(CairnError) as caught:
            self.acquire("run_2")
        self.assertIn("run_1", str(caught.exception))
        self.assertIn("may run until", str(caught.exception))
        self.assertIn("seconds from now", str(caught.exception))

    def test_a_refusal_never_names_a_minute_at_which_nothing_will_differ(self) -> None:
        # A live holder past its window is refused on liveness, not on the clock, so the
        # window's own number would send someone back at the named minute to the identical
        # refusal for ever.
        status = self.engine_record("run_1", os.getpid(), self_start_time())
        self.acquire("run_1", timeout=1.0, status_file=str(status))
        time.sleep(1.5)
        with self.assertRaises(CairnError) as caught:
            self.acquire("run_2")
        message = str(caught.exception)
        self.assertIn("still running", message)
        self.assertNotIn("reclaimable in 0.0 minutes", message)
        self.assertNotIn("may run until", message)

    def test_the_reclaim_window_is_the_runs_own_timeout_scaled_and_nothing_else(
        self,
    ) -> None:
        held = self.acquire("run_1", timeout=1234.0)
        window = held.record["reclaim_after"] - held.record["acquired_at"]
        # Pinned to the number rather than to the constant, so changing the factor is a
        # decision someone has to make here too.
        self.assertAlmostEqual(window, 1234.0 * 1.25, places=3)
        self.assertEqual(RECLAIM_FACTOR, 1.25)

    def test_two_runs_racing_one_expired_lock_produce_exactly_one_winner(self) -> None:
        self.plant(reclaim_after=1.0)
        barrier = threading.Barrier(2)

        def contend(run_id: str) -> str | None:
            barrier.wait(timeout=10)
            try:
                return acquire_run_lock(
                    self.repository,
                    run_id=run_id,
                    plan="demo",
                    run_timeout_seconds=600,
                ).record["run_id"]
            except CairnError:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(contend, ["run_a", "run_b"]))
        self.assertEqual(len([item for item in results if item is not None]), 1)
        stored = read_run_lock(self.repository)
        assert stored is not None
        self.assertIn(stored[0]["run_id"], {"run_a", "run_b"})

    def test_an_unreadable_lock_is_not_a_repository_nobody_can_run_against(self) -> None:
        object_id = hash_object(self.repository, "not json at all")
        self.assertTrue(update_ref(self.repository, f"create {RUN_LOCK_REF} {object_id}"))
        self.assertIsNone(read_run_lock(self.repository))
        held = self.acquire("run_2")
        self.assertEqual(held.record["run_id"], "run_2")

    def test_a_half_written_record_is_reclaimable_rather_than_a_permanent_wedge(
        self,
    ) -> None:
        # Validating two fields and trusting the rest turned a version-skewed payload into
        # a KeyError on the reclaim path — every later run failing the same way, forever.
        object_id = hash_object(
            self.repository, json.dumps({"run_id": "ghost", "pid": IMPOSSIBLE_PID})
        )
        self.assertTrue(update_ref(self.repository, f"create {RUN_LOCK_REF} {object_id}"))
        self.assertIsNone(read_run_lock(self.repository))
        self.assertEqual(self.acquire("run_2").record["run_id"], "run_2")

    def test_a_record_missing_any_field_the_refusal_reads_is_not_a_record(self) -> None:
        for absent in ("plan", "host", "acquired_at", "reclaim_after", "run_timeout_seconds"):
            with self.subTest(absent=absent):
                git(self.repository, ("update-ref", "-d", RUN_LOCK_REF), check=False)
                record: dict[str, Any] = {
                    "run_id": "ghost",
                    "plan": "demo",
                    "repository": str(self.repository),
                    "host": os.uname().nodename,
                    "pid": IMPOSSIBLE_PID,
                    "pid_started_at": None,
                    "acquired_at": time.time(),
                    "run_timeout_seconds": 600.0,
                    "reclaim_after": time.time() + 10000,
                }
                del record[absent]
                object_id = hash_object(self.repository, json.dumps(record))
                update_ref(self.repository, f"create {RUN_LOCK_REF} {object_id}")
                self.assertIsNone(read_run_lock(self.repository))

    def test_an_unreadable_lock_is_never_released_by_a_run_that_cannot_prove_it_owns_it(
        self,
    ) -> None:
        object_id = hash_object(self.repository, "not json at all")
        update_ref(self.repository, f"create {RUN_LOCK_REF} {object_id}")
        with self.assertRaises(CairnError) as caught:
            release_run_lock(self.repository, run_id="run_1")
        self.assertEqual(caught.exception.cause, "lock_not_held")
        self.assertIsNotNone(resolve_ref(self.repository, RUN_LOCK_REF))

    def test_a_ref_lock_left_by_another_git_process_does_not_lose_the_swap(self) -> None:
        # `cannot lock ref` is git's wording for a refused compare-and-swap and for plain
        # lockfile contention alike, and those are opposite answers.
        self.acquire("run_1")
        contended = common_directory(self.repository) / "refs" / "cairn" / "run-lock.lock"
        contended.parent.mkdir(parents=True, exist_ok=True)
        contended.write_text("", encoding="utf-8")

        def release_late() -> None:
            time.sleep(0.25)
            contended.unlink(missing_ok=True)

        worker = threading.Thread(target=release_late)
        worker.start()
        try:
            self.assertIsNotNone(release_run_lock(self.repository, run_id="run_1"))
        finally:
            worker.join(timeout=10)

    def test_reclaimability_is_the_age_and_states_it(self) -> None:
        record: LockRecord = {
            "run_id": "ghost",
            "plan": "demo",
            "repository": str(self.repository),
            "host": os.uname().nodename,
            "pid": IMPOSSIBLE_PID,
            "pid_started_at": None,
            "status_file": None,
            "acquired_at": 0.0,
            "run_timeout_seconds": 60.0,
            "reclaim_after": time.time() + 3600,
        }
        self.assertFalse(reclaimability(record).reclaimable)
        self.assertTrue(reclaimability(record, now=record["reclaim_after"]).reclaimable)


class Reconciliation(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)

    def write(self, records: list[dict[str, Any]], name: str = "status.jsonl") -> Path:
        path = self.root / "attempt" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record) + "\n")
        return path

    def running(self, **overrides: Any) -> dict[str, Any]:
        record: dict[str, Any] = {
            "dagRunId": "run_1",
            "name": "demo",
            "status": STATUS_RUNNING,
            "startedAt": "2026-08-09T22:00:00+01:00",
            "pid": IMPOSSIBLE_PID,
            "pidStartedAt": 1_700_000_000_000,
            "nodes": [
                {"step": {"name": "a"}, "status": STATUS_RUNNING},
                {"step": {"name": "b"}, "status": 4},
            ],
        }
        record.update(overrides)
        return record

    def status_of(self, path: Path) -> Any:
        record = last_record(path)
        self.assertIsNotNone(record)
        assert record is not None
        return record

    def test_a_killed_runs_record_reconciles_to_a_terminal_status(self) -> None:
        path = self.write([self.running()])
        outcome = reconcile_status_file(path)
        self.assertTrue(outcome.changed)
        record = self.status_of(path)
        self.assertEqual(record["status"], STATUS_FAILED)
        self.assertTrue(record["finishedAt"])
        self.assertEqual(record["error"], RECONCILED_ERROR)
        # Appended, not rewritten: the file is the engine's own append-only log.
        self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 2)
        # And the repaired line is the record, so everything else in it survives.
        for field in ("dagRunId", "name", "startedAt", "pid"):
            self.assertEqual(record[field], self.running()[field], field)

    def test_the_report_never_describes_a_dead_runs_steps_as_running(self) -> None:
        path = self.write([self.running()])
        reconcile_status_file(path)
        statuses = [node["status"] for node in self.status_of(path)["nodes"]]
        self.assertEqual(statuses, [STATUS_FAILED, 4])

    def test_a_live_run_is_left_exactly_as_it_was(self) -> None:
        path = self.write(
            [self.running(pid=os.getpid(), pidStartedAt=(self_start_time() or 0) * 1000)]
        )
        before = path.read_text(encoding="utf-8")
        outcome = reconcile_status_file(path)
        self.assertFalse(outcome.changed)
        self.assertEqual(outcome.verdict, STILL_RUNNING)
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_liveness_decides_it_and_never_the_status_field(self) -> None:
        # The crash is invisible in the record: the status still says running, which is
        # exactly why it is not the evidence.
        path = self.write([self.running()])
        self.assertEqual(self.status_of(path)["status"], STATUS_RUNNING)
        self.assertTrue(reconcile_status_file(path).changed)

    def test_a_record_naming_no_usable_process_is_left_alone(self) -> None:
        # Absent evidence is not evidence of death, and writing a terminal status into a
        # live run is the more damaging direction to be wrong in.
        for pid in (None, "1234", True):
            with self.subTest(pid=pid):
                path = self.write([self.running(pid=pid)])
                outcome = reconcile_status_file(path)
                self.assertFalse(outcome.changed)
                self.assertEqual(outcome.verdict, OWNER_UNKNOWN)

    def test_an_already_terminal_record_is_untouched(self) -> None:
        path = self.write([self.running(status=STATUS_FAILED)])
        outcome = reconcile_status_file(path)
        self.assertFalse(outcome.changed)
        self.assertEqual(outcome.verdict, ALREADY_TERMINAL)

    def test_a_record_whose_nodes_are_not_a_list_is_left_alone(self) -> None:
        path = self.write([self.running(nodes={"a": {"status": STATUS_RUNNING}})])
        outcome = reconcile_status_file(path)
        self.assertFalse(outcome.changed)
        self.assertEqual(outcome.verdict, UNRECOGNISED)

    def test_a_dry_run_reports_without_writing(self) -> None:
        path = self.write([self.running()])
        before = path.read_text(encoding="utf-8")
        outcome = reconcile_status_file(path, dry_run=True)
        self.assertFalse(outcome.changed)
        self.assertEqual(outcome.verdict, WOULD_RECONCILE)
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_the_last_valid_line_wins_over_every_earlier_snapshot(self) -> None:
        path = self.write([self.running(status=4), self.running()])
        self.assertTrue(reconcile_status_file(path).changed)

    def test_a_truncated_final_line_does_not_lose_the_record(self) -> None:
        path = self.write([self.running()])
        with path.open("a", encoding="utf-8") as handle:
            handle.write('{"dagRunId": "run_1", "sta')
        self.assertEqual(self.status_of(path)["status"], STATUS_RUNNING)

    def test_a_line_cut_mid_character_is_skipped_rather_than_fatal(self) -> None:
        path = self.write([self.running()])
        with path.open("ab") as handle:
            handle.write(b'{"dagRunId": "run_\xc3')
        self.assertEqual(self.status_of(path)["status"], STATUS_RUNNING)
        self.assertTrue(reconcile_status_file(path).changed)

    def test_an_empty_record_file_is_reported_not_repaired(self) -> None:
        path = self.write([])
        outcome = reconcile_status_file(path)
        self.assertFalse(outcome.changed)
        self.assertEqual(outcome.verdict, NO_RECORD)

    def test_a_whole_tree_of_records_reconciles_in_one_pass(self) -> None:
        for index in range(3):
            path = self.root / f"run{index}" / "a" / "status.jsonl"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(self.running()) + "\n", encoding="utf-8")
        results = reconcile(self.root)
        self.assertEqual(len([item for item in results if item.changed]), 3)
        self.assertTrue(all(item.verdict == RECONCILED for item in results))

    def test_a_path_that_does_not_exist_is_refused_rather_than_reported_clean(
        self,
    ) -> None:
        with self.assertRaises(CairnError) as caught:
            reconcile(self.root / "nowhere")
        self.assertEqual(caught.exception.cause, "run_record_unreadable")


class BaseConfiguration(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)
        self.path = self.root / "base.yaml"

    def write(self, text: str) -> None:
        self.path.write_text(text, encoding="utf-8")

    def test_the_engines_shipped_default_is_read_and_refused(self) -> None:
        self.write(
            "type: graph\nretry_policy:\n  limit: 3\n  interval_sec: 5\n\nmax_active_steps: 10\n"
        )
        self.assertEqual(read_base_retry_policy(self.path).limit, 3)
        with self.assertRaises(CairnError) as caught:
            assert_dag_retry_disabled(self.path)
        self.assertEqual(caught.exception.cause, "base_retry_enabled")

    def test_every_refusal_names_the_command_that_fixes_it(self) -> None:
        for text in (None, "type: graph\nretry_policy:\n  limit: 3\n  interval_sec: 5\n"):
            with self.subTest(text=text):
                if text is None:
                    self.path.unlink(missing_ok=True)
                else:
                    self.write(text)
                with self.assertRaises(CairnError) as caught:
                    assert_dag_retry_disabled(self.path)
                self.assertIn("supervise base-config --disable", str(caught.exception))

    def test_an_absent_file_is_refused_because_the_engine_will_create_it_enabled(
        self,
    ) -> None:
        with self.assertRaises(CairnError) as caught:
            assert_dag_retry_disabled(self.root / "nothing.yaml")
        self.assertEqual(caught.exception.cause, "base_retry_enabled")

    def test_a_disabled_policy_passes(self) -> None:
        self.write("retry_policy:\n  limit: 0\n  interval_sec: 1\n")
        assert_dag_retry_disabled(self.path)

    def test_a_trailing_comment_on_a_value_does_not_hide_the_limit(self) -> None:
        self.write('retry_policy:\n  limit: 0  # never retry\n  interval_sec: 1\n')
        self.assertEqual(read_base_retry_policy(self.path).limit, 0)
        assert_dag_retry_disabled(self.path)

    def test_a_quoted_limit_is_read_as_the_integer_it_is(self) -> None:
        self.write('retry_policy:\n  limit: "0"\n  interval_sec: 1\n')
        self.assertEqual(read_base_retry_policy(self.path).limit, 0)

    def test_the_flow_spelling_is_read_too(self) -> None:
        self.write("retry_policy: {limit: 0, interval_sec: 1}\n")
        self.assertEqual(read_base_retry_policy(self.path).limit, 0)
        assert_dag_retry_disabled(self.path)

    def test_a_comment_on_the_key_does_not_hide_the_block_beneath_it(self) -> None:
        # Trailing comments are this file's own idiom, and reading the key line as an
        # inline mapping because of one skipped the block and spliced over the key alone.
        self.write("retry_policy:  # tuned for flaky infra\n  limit: 3\n  interval_sec: 5\n")
        self.assertEqual(read_base_retry_policy(self.path).limit, 3)
        self.assertTrue(ensure_dag_retry_disabled(self.path))
        assert_dag_retry_disabled(self.path)
        self.assertEqual(self.path.read_text(encoding="utf-8").count("limit:"), 1)

    def test_a_shape_the_reader_cannot_account_for_is_refused_not_guessed(self) -> None:
        for text in (
            "retry_policy: {\n  limit: 3,\n  interval_sec: 5\n}\n",
            "retry_policy:\n  interval_sec: 5\n",
        ):
            with self.subTest(text=text):
                self.write(text)
                with self.assertRaises(CairnError) as caught:
                    read_base_retry_policy(self.path)
                self.assertEqual(caught.exception.cause, "base_config_unreadable")

    def test_a_key_the_reader_cannot_see_is_never_answered_with_a_duplicate(self) -> None:
        # Reading a present policy as absent makes `--disable` append a second one, which
        # is the duplicate key that stops the engine loading any workflow on the machine.
        for text in (
            "retry_policy : {limit: 3, interval_sec: 5}\nlog_dir: /x\n",
            '"retry_policy": {limit: 3, interval_sec: 5}\nlog_dir: /x\n',
        ):
            with self.subTest(text=text):
                self.write(text)
                self.assertEqual(read_base_retry_policy(self.path).limit, 3)
                ensure_dag_retry_disabled(self.path)
                assert_dag_retry_disabled(self.path)
                self.assertEqual(
                    self.path.read_text(encoding="utf-8").count("retry_policy"), 1
                )

    def test_a_comment_at_column_zero_does_not_cut_the_block_short(self) -> None:
        # YAML treats it as transparent, so a scan that stopped there would splice over
        # half the block and leave the rest attached as duplicate keys.
        self.write(
            "retry_policy:\n  limit: 3\n# how long to wait\n  interval_sec: 5\n"
            "log_dir: /var/log\n"
        )
        self.assertEqual(read_base_retry_policy(self.path).limit, 3)
        ensure_dag_retry_disabled(self.path)
        assert_dag_retry_disabled(self.path)
        text = self.path.read_text(encoding="utf-8")
        self.assertEqual(text.count("interval_sec"), 1)
        self.assertIn("log_dir: /var/log", text)

    def test_a_value_that_is_not_an_integer_is_refused_not_crashed_on(self) -> None:
        self.write("retry_policy:\n  limit: --5\n  interval_sec: 1\n")
        with self.assertRaises(CairnError) as caught:
            read_base_retry_policy(self.path)
        self.assertEqual(caught.exception.cause, "base_config_unreadable")

    def test_a_symlinked_configuration_is_written_through_rather_than_replaced(
        self,
    ) -> None:
        real = self.root / "dotfiles" / "dagu-base.yaml"
        real.parent.mkdir(parents=True)
        real.write_text("retry_policy:\n  limit: 3\n  interval_sec: 5\n", encoding="utf-8")
        self.path.symlink_to(real)
        ensure_dag_retry_disabled(self.path)
        self.assertTrue(self.path.is_symlink())
        self.assertEqual(read_base_retry_policy(real).limit, 0)

    def test_a_step_level_policy_is_not_mistaken_for_the_dag_level_one(self) -> None:
        self.write("defaults:\n  retry_policy:\n    limit: 2\n    interval_sec: 5\n")
        self.assertIsNone(read_base_retry_policy(self.path).limit)

    def test_two_declarations_are_ambiguous_and_refused(self) -> None:
        self.write(
            "retry_policy:\n  limit: 0\n  interval_sec: 1\nretry_policy:\n  limit: 3\n"
            "  interval_sec: 5\n"
        )
        with self.assertRaises(CairnError) as caught:
            read_base_retry_policy(self.path)
        self.assertEqual(caught.exception.cause, "base_config_unreadable")

    def test_disabling_edits_the_file_and_keeps_every_other_field(self) -> None:
        self.write(
            "type: graph\nretry_policy:\n  limit: 3\n  interval_sec: 5\nmax_active_steps: 10\n"
        )
        self.assertTrue(ensure_dag_retry_disabled(self.path))
        assert_dag_retry_disabled(self.path)
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("type: graph", text)
        self.assertIn("max_active_steps: 10", text)

    def test_a_block_carrying_a_list_is_replaced_whole_and_not_spliced_over(self) -> None:
        # An entry pattern alone stops at a list item, and splicing there leaves the
        # orphaned items under the new policy — a file the engine refuses to load at all,
        # taking every unrelated workflow on the machine with it.
        self.write(
            "retry_policy:\n  limit: 3\n  interval_sec: 5\n  exit_code:\n    - 1\n"
            "    - 2\nlog_dir: /tmp/x\n"
        )
        self.assertTrue(ensure_dag_retry_disabled(self.path))
        assert_dag_retry_disabled(self.path)
        text = self.path.read_text(encoding="utf-8")
        self.assertNotIn("- 1", text)
        self.assertIn("log_dir: /tmp/x", text)

    def test_a_blank_line_inside_the_block_does_not_leave_the_old_policy_behind(
        self,
    ) -> None:
        self.write("retry_policy:\n\n  limit: 3\n  interval_sec: 5\ntype: graph\n")
        self.assertTrue(ensure_dag_retry_disabled(self.path))
        text = self.path.read_text(encoding="utf-8")
        self.assertEqual(text.count("limit:"), 1)
        self.assertIn("type: graph", text)

    def test_disabling_twice_changes_nothing_the_second_time(self) -> None:
        self.write("retry_policy:\n  limit: 3\n  interval_sec: 5\n")
        self.assertTrue(ensure_dag_retry_disabled(self.path))
        self.assertFalse(ensure_dag_retry_disabled(self.path))

    def test_a_file_with_no_policy_gains_one(self) -> None:
        self.write("type: graph\n")
        self.assertTrue(ensure_dag_retry_disabled(self.path))
        assert_dag_retry_disabled(self.path)

    def test_the_write_is_replaced_whole_rather_than_truncated_in_place(self) -> None:
        # A killed truncating write would leave the user's other settings gone, and the
        # engine loads an empty base configuration without complaint. Only a replace can
        # be interrupted safely, and only a replace changes the inode.
        self.write("type: graph\nretry_policy:\n  limit: 3\n  interval_sec: 5\n")
        os.chmod(self.path, 0o600)
        before = self.path.stat().st_ino
        ensure_dag_retry_disabled(self.path)
        self.assertNotEqual(self.path.stat().st_ino, before)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_an_edit_that_did_not_land_leaves_the_file_exactly_as_it_was(self) -> None:
        # The last line of defence against a splice that produces a duplicate key, which
        # stops the engine loading any workflow on the machine.
        original = "type: graph\nretry_policy:\n  limit: 3\n  interval_sec: 5\n"
        self.write(original)
        with patch(
            "cairn.baseconfig.DISABLED_RETRY_POLICY", "retry_policy:\n  limit: 9\n"
        ), self.assertRaises(CairnError) as caught:
            ensure_dag_retry_disabled(self.path)
        self.assertEqual(caught.exception.cause, "base_config_unreadable")
        self.assertEqual(self.path.read_text(encoding="utf-8"), original)

    def test_a_quoted_limit_reads_the_same_in_either_spelling(self) -> None:
        for text in (
            'retry_policy:\n  limit: "0"\n  interval_sec: 1\n',
            'retry_policy: {limit: "0", interval_sec: 1}\n',
        ):
            with self.subTest(text=text):
                self.write(text)
                self.assertEqual(read_base_retry_policy(self.path).limit, 0)

    def test_a_policy_with_no_limit_is_refused_in_either_spelling(self) -> None:
        for text in (
            "retry_policy:\n  interval_sec: 5\n",
            "retry_policy: {interval_sec: 5}\n",
        ):
            with self.subTest(text=text):
                self.write(text)
                with self.assertRaises(CairnError) as caught:
                    read_base_retry_policy(self.path)
                self.assertEqual(caught.exception.cause, "base_config_unreadable")

    def test_the_engines_home_variable_locates_the_file(self) -> None:
        self.assertEqual(
            base_config_path({"DAGU_HOME": "/opt/dagu"}), Path("/opt/dagu/base.yaml")
        )
        self.assertEqual(
            base_config_path({}), Path.home() / ".config" / "dagu" / "base.yaml"
        )


class WorktreeClassifier(unittest.TestCase):
    """The convergence decision with no git in it — which is the point of separating it."""

    def test_a_foreign_worktree_outranks_every_other_reading(self) -> None:
        # Decided before anything that would move or delete: a directory belonging to
        # another repository is the one case where being wrong costs someone else's work.
        self.assertEqual(
            classify(Facts(identity=FOREIGN, disk="dir", locked=True, in_progress="a merge")),
            FOREIGN,
        )

    def test_a_locked_worktree_is_never_touched(self) -> None:
        self.assertEqual(
            classify(Facts(identity="ours", registration="here", disk="dir", locked=True)),
            LOCKED,
        )

    def test_a_branch_checked_out_elsewhere_halts_before_a_second_checkout(self) -> None:
        self.assertEqual(
            classify(Facts(identity="ours", disk="dir", branch_checked_out_at="/other")),
            ELSEWHERE,
        )

    def test_an_unfinished_operation_is_left_exactly_as_it_was(self) -> None:
        self.assertEqual(
            classify(
                Facts(
                    identity="ours",
                    registration="here",
                    disk="dir",
                    head="ours",
                    in_progress="a rebase",
                )
            ),
            INTERRUPTED,
        )

    def test_the_branchs_ancestry_decides_healthy_from_behind(self) -> None:
        ours = Facts(identity="ours", registration="here", disk="dir", head="ours")
        self.assertEqual(classify(replace(ours, relation=SAME_AS_PARENT)), HEALTHY)
        self.assertEqual(classify(replace(ours, relation=UNMERGED)), HEALTHY)
        self.assertEqual(classify(replace(ours, relation=ANCESTOR_OF_PARENT)), MERGED_BEHIND)

    def test_a_dirty_tree_does_not_change_which_state_a_worktree_is_in(self) -> None:
        # Cleanliness decides what may be done, never what the worktree *is*: reading it
        # as state is what lets one stray build artefact hide a stale base.
        for tree in ("clean", "dirty"):
            facts = Facts(
                identity="ours",
                registration="here",
                disk="dir",
                head="ours",
                relation=ANCESTOR_OF_PARENT,
                tree=tree,
            )
            self.assertEqual(classify(facts), MERGED_BEHIND)

    def test_a_registered_directory_git_cannot_read_is_repaired_before_anything_else(
        self,
    ) -> None:
        self.assertEqual(
            classify(Facts(identity=UNREADABLE, registration="here", disk="dir")),
            REPAIRABLE,
        )

    def test_the_same_directory_with_no_registration_left_is_junk(self) -> None:
        self.assertEqual(classify(Facts(identity=UNREADABLE, disk="dir")), JUNK)

    def test_a_registration_outliving_its_directory_is_its_own_state(self) -> None:
        for disk in ("absent", "empty_dir"):
            self.assertEqual(
                classify(Facts(registration="here", disk=disk)), STALE_REGISTRATION
            )

    def test_every_state_the_classifier_can_return_is_one_the_converger_handles(
        self,
    ) -> None:
        self.assertEqual(len(set(STATES)), len(STATES))
        self.assertEqual(classify(Facts()), ABSENT)
        for disk in ("dir", "file", "symlink"):
            self.assertEqual(classify(Facts(disk=disk)), JUNK)

    def test_a_shape_nobody_anticipated_refuses_rather_than_taking_the_last_arm(
        self,
    ) -> None:
        self.assertEqual(classify(Facts(disk="socket")), UNCLASSIFIED)


class WorktreeConvergence(RepositoryCase):
    def setUp(self) -> None:
        super().setUp()
        self.worktree = self.root / "repo.cairn-worktrees" / "demo" / "alpha"
        self.branch = "step/alpha"

    def setup(self) -> Any:
        return setup_worktree(self.repository, self.worktree, self.branch, "main")

    def test_a_missing_worktree_is_created_on_its_own_branch(self) -> None:
        result = self.setup()
        self.assertEqual(result.detail["state"], ABSENT)
        self.assertEqual(result.detail["case"], "created")
        self.assertTrue((self.worktree / "README.md").exists())
        self.assertEqual(
            git(self.worktree, ("rev-parse", "--abbrev-ref", "HEAD")).stdout, self.branch
        )

    def test_a_healthy_worktree_is_reused(self) -> None:
        self.setup()
        result = self.setup()
        self.assertEqual(result.detail["state"], HEALTHY)
        self.assertEqual(result.detail["case"], "reused")
        self.assertEqual(result.status, "noop")

    def test_uncommitted_work_is_kept_rather_than_reset(self) -> None:
        self.setup()
        (self.worktree / "wip.txt").write_text("half done\n", encoding="utf-8")
        result = self.setup()
        self.assertEqual(result.detail["state"], HEALTHY)
        self.assertTrue((self.worktree / "wip.txt").exists())

    def test_a_merged_branch_left_behind_the_parent_moves_forward_to_it(self) -> None:
        # A fast-forward, never a reset: the branch is a proven ancestor, so the move
        # cannot drop a commit, and git refuses it outright if it would cost an edit.
        self.setup()
        head = advance(self.repository, "moved.txt")
        result = self.setup()
        self.assertEqual(result.detail["state"], MERGED_BEHIND)
        self.assertEqual(result.detail["case"], "fast_forwarded")
        self.assertEqual(git(self.worktree, ("rev-parse", "HEAD")).stdout, head)
        self.assertTrue((self.worktree / "moved.txt").exists())

    def test_a_stray_untracked_file_cannot_hide_a_stale_base(self) -> None:
        # Deciding on cleanliness would reuse silently here, leaving the branch at a head
        # the parent has moved past — the case the built-in fails green. The move is
        # decided on ancestry instead, and a file the move does not touch cannot block it.
        self.setup()
        head = advance(self.repository, "moved.txt")
        (self.worktree / "build.log").write_text("noise\n", encoding="utf-8")
        result = self.setup()
        self.assertEqual(result.detail["state"], MERGED_BEHIND)
        self.assertEqual(result.detail["case"], "fast_forwarded")
        self.assertEqual(git(self.worktree, ("rev-parse", "HEAD")).stdout, head)
        self.assertTrue((self.worktree / "build.log").exists())

    def test_unmerged_commits_are_never_thrown_away_by_a_reset(self) -> None:
        self.setup()
        (self.worktree / "work.txt").write_text("real work\n", encoding="utf-8")
        git(self.worktree, ("add", "--all"))
        git(self.worktree, ("commit", "--quiet", "-m", "step work"))
        head = git(self.worktree, ("rev-parse", "HEAD")).stdout
        advance(self.repository, "moved.txt")
        result = self.setup()
        self.assertEqual(result.detail["state"], HEALTHY)
        self.assertEqual(git(self.worktree, ("rev-parse", "HEAD")).stdout, head)

    def test_a_detached_worktree_holding_work_halts_rather_than_destroying_it(
        self,
    ) -> None:
        # What a killed rebase leaves. Recreating over it would take an agent's staged and
        # uncommitted output with it, which convergence must never cost.
        self.setup()
        (self.worktree / "precious.txt").write_text("agent output\n", encoding="utf-8")
        git(self.worktree, ("add", "--all"))
        git(self.worktree, ("checkout", "--quiet", "--detach", "HEAD"))
        with self.assertRaises(CairnError) as caught:
            self.setup()
        self.assertEqual(caught.exception.cause, "worktree_dirty")
        self.assertTrue((self.worktree / "precious.txt").exists())

    def test_a_worktree_switched_to_another_branch_with_work_halts_too(self) -> None:
        self.setup()
        git(self.worktree, ("switch", "--quiet", "-c", "agent/scratch"))
        (self.worktree / "precious.txt").write_text("agent output\n", encoding="utf-8")
        with self.assertRaises(CairnError) as caught:
            self.setup()
        self.assertEqual(caught.exception.cause, "worktree_dirty")
        self.assertTrue((self.worktree / "precious.txt").exists())

    def test_a_clean_worktree_on_the_wrong_branch_is_moved_back_onto_it(self) -> None:
        # Checking the branch out costs nothing and keeps the checkout; recreating would
        # throw away a directory git could read perfectly well.
        self.setup()
        git(self.worktree, ("checkout", "--quiet", "--detach", "HEAD"))
        result = self.setup()
        self.assertEqual(result.detail["state"], WRONG_BRANCH)
        self.assertEqual(result.detail["case"], "switched_to_branch")
        self.assertEqual(
            git(self.worktree, ("rev-parse", "--abbrev-ref", "HEAD")).stdout, self.branch
        )

    def test_a_worktree_whose_git_file_was_broken_is_repaired_not_recreated(self) -> None:
        # The registration still names it, so git can relink it without touching what is
        # inside. Recreating would cost the untracked file for nothing.
        self.setup()
        (self.worktree / ".git").unlink()
        (self.worktree / "junk.txt").write_text("debris\n", encoding="utf-8")
        result = self.setup()
        self.assertIn(result.detail["state"], {HEALTHY, WRONG_BRANCH})
        self.assertTrue((self.worktree / "README.md").exists())
        self.assertTrue((self.worktree / "junk.txt").exists())

    def test_a_worktree_whose_admin_data_was_deleted_is_recreated(self) -> None:
        # Doc 07's own adversarial case. git stops resolving the directory entirely, so
        # asking git whose it is answers "nobody" for a directory that is plainly ours.
        self.setup()
        name = (self.worktree / ".git").read_text(encoding="utf-8").strip()
        admin = Path(name[len("gitdir:") :].strip())
        subprocess.run(["rm", "-rf", str(admin)], check=True)
        result = self.setup()
        self.assertEqual(result.detail["state"], JUNK)
        self.assertEqual(result.detail["case"], "recreated")
        self.assertTrue((self.worktree / "README.md").exists())

    def test_an_unreadable_worktrees_contents_are_moved_aside_not_deleted(self) -> None:
        # Recreating is right; deleting is not. With the admin data gone nothing can say
        # whether what is in there was ever committed, so it must not be guessed at.
        self.setup()
        (self.worktree / "precious.txt").write_text("agent output\n", encoding="utf-8")
        name = (self.worktree / ".git").read_text(encoding="utf-8").strip()
        admin = Path(name[len("gitdir:") :].strip())
        subprocess.run(["rm", "-rf", str(admin)], check=True)

        result = self.setup()
        self.assertEqual(result.detail["case"], "recreated")
        self.assertTrue((self.worktree / "README.md").exists())
        quarantined = result.detail["quarantined"]
        self.assertIsNotNone(quarantined)
        self.assertTrue((Path(str(quarantined)) / "precious.txt").exists())

    def test_a_registration_outliving_its_directory_does_not_fail_forever(self) -> None:
        self.setup()
        subprocess.run(["rm", "-rf", str(self.worktree)], check=True)
        result = self.setup()
        self.assertEqual(result.detail["state"], STALE_REGISTRATION)
        self.assertTrue((self.worktree / "README.md").exists())

    def test_a_registration_whose_directory_is_gone_does_not_hold_the_branch(self) -> None:
        # Branch names carry no plan slug while worktree paths do, so a crashed run of
        # another plan leaves a registration for this branch at a path nothing occupies.
        # Refusing on it would halt every later plan naming that step, permanently.
        elsewhere = self.root / "repo.cairn-worktrees" / "other-plan" / "alpha"
        git(self.repository, ("worktree", "add", "--quiet", str(elsewhere), "-b", self.branch))
        subprocess.run(["rm", "-rf", str(elsewhere)], check=True)
        self.setup()
        self.assertTrue((self.worktree / "README.md").exists())
        self.assertEqual(
            git(self.worktree, ("rev-parse", "--abbrev-ref", "HEAD")).stdout, self.branch
        )

    def test_a_branch_live_in_another_worktree_still_halts(self) -> None:
        elsewhere = self.root / "repo.cairn-worktrees" / "other-plan" / "alpha"
        git(self.repository, ("worktree", "add", "--quiet", str(elsewhere), "-b", self.branch))
        with self.assertRaises(CairnError) as caught:
            self.setup()
        self.assertEqual(caught.exception.cause, "worktree_unusable")
        self.assertIn(str(elsewhere), str(caught.exception))

    def test_a_move_that_fails_for_any_other_reason_is_never_reported_green(self) -> None:
        # Only a working tree that would lose an edit is a refusal Cairn accepts. Anything
        # else left the branch behind the parent, which is what this arm exists to clear.
        self.setup()
        advance(self.repository, "moved.txt")
        with patch(
            "cairn.worktrees.git",
            side_effect=lambda directory, arguments, **kwargs: (
                GitOutcome(1, "", "fatal: Unable to create index.lock: File exists")
                if tuple(arguments)[:2] == ("merge", "--ff-only")
                else git(directory, arguments, **kwargs)
            ),
        ), self.assertRaises(CairnError) as caught:
            self.setup()
        self.assertEqual(caught.exception.cause, "worktree_unusable")

    def test_a_symlinked_worktrees_root_is_still_a_worktrees_root(self) -> None:
        # Keeping worktrees on another volume behind a symlink is ordinary; resolving the
        # path first would refuse every convergence under it.
        volume = self.root / "big-disk"
        volume.mkdir()
        linked_root = self.root / "linked.cairn-worktrees"
        linked_root.symlink_to(volume)
        worktree = linked_root / "demo" / "alpha"
        worktree.mkdir(parents=True)
        (worktree / "debris.txt").write_text("from a dead run\n", encoding="utf-8")
        result = setup_worktree(self.repository, worktree, self.branch, "main")
        self.assertEqual(result.detail["state"], JUNK)
        self.assertTrue((worktree / "README.md").exists())
        self.assertTrue(
            (Path(str(result.detail["quarantined"])) / "debris.txt").exists()
        )

    def test_a_worktree_of_another_repository_halts_and_names_it(self) -> None:
        other = make_repository(self.root, "other")
        git(other, ("worktree", "add", "--quiet", str(self.worktree), "-b", "theirs"))
        with self.assertRaises(CairnError) as caught:
            self.setup()
        self.assertEqual(caught.exception.cause, "worktree_foreign")
        self.assertIn(str(common_directory(other)), str(caught.exception))

    def test_the_repositorys_own_working_tree_is_never_taken_as_a_step_worktree(
        self,
    ) -> None:
        with self.assertRaises(CairnError) as caught:
            setup_worktree(self.repository, self.repository, self.branch, "main")
        self.assertEqual(caught.exception.cause, "worktree_unusable")

    def test_a_directory_cairn_cannot_account_for_is_moved_aside_never_deleted(self) -> None:
        # Inside Cairn's own worktrees namespace this is debris from an earlier run, so
        # the run converges rather than stopping for someone to clear it by hand. What was
        # there is renamed, not deleted: nothing can say whether it was ever committed.
        self.worktree.mkdir(parents=True)
        (self.worktree / "someones-file.txt").write_text("precious\n", encoding="utf-8")
        result = self.setup()
        self.assertEqual(result.detail["state"], JUNK)
        quarantined = result.detail["quarantined"]
        self.assertIsNotNone(quarantined)
        self.assertTrue((Path(str(quarantined)) / "someones-file.txt").exists())
        self.assertTrue((self.worktree / "README.md").exists())

    def test_nothing_outside_a_cairn_worktrees_root_is_ever_moved(self) -> None:
        # The guard the quarantine rests on. Checked component-wise on the resolved path,
        # so a directory that merely reads like one is not inside it.
        stray = self.root / "not-cairns" / "alpha"
        stray.mkdir(parents=True)
        (stray / "someones-file.txt").write_text("precious\n", encoding="utf-8")
        with self.assertRaises(CairnError) as caught:
            setup_worktree(self.repository, stray, self.branch, "main")
        self.assertEqual(caught.exception.cause, "worktree_unusable")
        self.assertTrue((stray / "someones-file.txt").exists())

    def test_a_file_where_the_worktree_should_go_is_moved_aside(self) -> None:
        self.worktree.parent.mkdir(parents=True)
        self.worktree.write_text("not a directory\n", encoding="utf-8")
        result = self.setup()
        self.assertEqual(result.detail["state"], JUNK)
        self.assertEqual(
            Path(str(result.detail["quarantined"])).read_text(encoding="utf-8"),
            "not a directory\n",
        )
        self.assertTrue((self.worktree / "README.md").exists())


class CommitAndPrune(RepositoryCase):
    def test_nothing_staged_is_a_no_op_and_not_a_failure(self) -> None:
        result = commit_all(self.repository, "empty")
        self.assertEqual(result.status, "noop")
        self.assertEqual(result.exit_code, 0)

    def test_residue_that_cannot_be_staged_is_a_no_op_not_a_failed_commit(self) -> None:
        # The question is what the commit would record, so it is asked of the index: a
        # working tree can hold changes `add` cannot stage.
        inner = make_repository(self.root, "inner")
        git(
            self.repository,
            ("-c", "protocol.file.allow=always", "submodule", "add", "--quiet", str(inner), "sub"),
        )
        git(self.repository, ("commit", "--quiet", "-m", "add submodule"))
        (self.repository / "sub" / "scratch.txt").write_text("x\n", encoding="utf-8")
        result = commit_all(self.repository, "nothing of ours")
        self.assertEqual(result.status, "noop")

    def test_a_change_is_committed_and_the_commit_is_named(self) -> None:
        (self.repository / "new.txt").write_text("content\n", encoding="utf-8")
        result = commit_all(self.repository, "cairn(a): add new")
        self.assertEqual(result.status, "done")
        self.assertEqual(
            git(self.repository, ("log", "-1", "--pretty=%s")).stdout, "cairn(a): add new"
        )

    def test_a_prune_removes_merged_worktrees_and_branches(self) -> None:
        worktree = self.root / "trees" / "alpha"
        setup_worktree(self.repository, worktree, "step/alpha", "main")
        result = prune_worktrees(
            self.repository, [str(worktree)], ["step/alpha"], parent="main"
        )
        self.assertEqual(result.status, "done")
        self.assertFalse(worktree.exists())
        self.assertEqual(result.detail["deleted_branches"], ["step/alpha"])

    def test_a_prune_never_deletes_an_unmerged_branch(self) -> None:
        worktree = self.root / "trees" / "alpha"
        setup_worktree(self.repository, worktree, "step/alpha", "main")
        (worktree / "work.txt").write_text("real\n", encoding="utf-8")
        git(worktree, ("add", "--all"))
        git(worktree, ("commit", "--quiet", "-m", "unmerged work"))
        result = prune_worktrees(
            self.repository, [str(worktree)], ["step/alpha"], parent="main"
        )
        self.assertEqual(result.detail["retained_branches"], ["step/alpha"])
        self.assertIsNotNone(resolve_ref(self.repository, "refs/heads/step/alpha"))

    def test_merged_is_decided_against_the_parent_the_topology_named(self) -> None:
        # `git branch -d` asks about HEAD, so a branch already folded into the parent
        # would be retained forever whenever the repository sits on something else.
        worktree = self.root / "trees" / "alpha"
        setup_worktree(self.repository, worktree, "step/alpha", "main")
        git(self.repository, ("checkout", "--quiet", "-b", "elsewhere"))
        advance(self.repository, "unrelated.txt")
        result = prune_worktrees(
            self.repository, [str(worktree)], ["step/alpha"], parent="main"
        )
        self.assertEqual(result.detail["deleted_branches"], ["step/alpha"])

    def test_a_prune_refuses_a_dirty_worktree_and_says_so(self) -> None:
        worktree = self.root / "trees" / "alpha"
        setup_worktree(self.repository, worktree, "step/alpha", "main")
        (worktree / "wip.txt").write_text("killed mid-edit\n", encoding="utf-8")
        result = prune_worktrees(
            self.repository, [str(worktree)], ["step/alpha"], parent="main"
        )
        self.assertEqual(result.detail["kept"], [str(worktree)])
        self.assertTrue((worktree / "wip.txt").exists())
        self.assertTrue(result.follow_up_work)

    def test_a_prune_never_claims_uncommitted_work_in_a_path_that_is_not_there(
        self,
    ) -> None:
        # Re-running the plan is the recovery procedure, so the second prune must not
        # report work to rescue from a directory that no longer exists.
        missing = str(self.root / "trees" / "never-made")
        result = prune_worktrees(self.repository, [missing], ["step/nope"], parent="main")
        self.assertEqual(result.detail["already_gone"], [missing])
        self.assertEqual(result.detail["kept"], [])
        self.assertEqual(result.detail["retained_branches"], [])
        self.assertEqual(result.follow_up_work, [])


if __name__ == "__main__":
    unittest.main()
