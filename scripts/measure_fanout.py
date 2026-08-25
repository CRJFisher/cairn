"""Measure what the git write mutex costs a fan-out, and what the fan-out buys.

Two numbers, because the exit criteria ask two different questions.

The first is the mutex's own cost: the same concurrent worktree-and-commit workload run
with the mutex and without it. The second is the fan-out's wall-clock through the real
engine with real worktrees and the mutex in the middle of it, against the sum of the same
work run one step at a time.

Run it from this package's root:

    python3 -m scripts.measure_fanout [--steps N] [--seconds S]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from cairn.baseconfig import ensure_dag_retry_disabled
from cairn.gitio import git
from cairn.locks import git_write_mutex

PACKAGE_ROOT = Path(__file__).parents[1]


def make_repository(root: Path, name: str) -> Path:
    repository = root / name
    repository.mkdir(parents=True)
    git(repository, ("init", "--initial-branch=main", "--quiet", "."))
    git(repository, ("config", "user.email", "cairn@measure"))
    git(repository, ("config", "user.name", "Cairn Measure"))
    (repository / "README.md").write_text("start\n", encoding="utf-8")
    git(repository, ("add", "--all"))
    git(repository, ("commit", "--quiet", "-m", "init"))
    return repository


def one_writer(repository: Path, trees: Path, index: int, *, guarded: bool) -> None:
    """One step's whole git footprint: a worktree, an edit, a commit on its own branch."""
    worktree = trees / f"step_{index}"
    branch = f"step/measure_{index}"

    def guard() -> Any:
        return git_write_mutex(repository) if guarded else nullcontext()

    with guard():
        git(repository, ("worktree", "add", "--quiet", "-b", branch, str(worktree), "main"))
    (worktree / f"file_{index}.txt").write_text(f"work {index}\n", encoding="utf-8")
    with guard():
        git(worktree, ("add", "--all"))
        git(worktree, ("commit", "--quiet", "-m", f"step {index}"))


def measure_mutex_cost(steps: int, repeats: int) -> dict[str, float]:
    results: dict[str, float] = {}
    for label, guarded in (("without the mutex", False), ("with the mutex", True)):
        elapsed: list[float] = []
        for _ in range(repeats):
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                repository = make_repository(root, "repo")
                trees = root / "trees"
                trees.mkdir()
                def write(
                    index: int,
                    repository: Path = repository,
                    trees: Path = trees,
                    guarded: bool = guarded,
                ) -> None:
                    one_writer(repository, trees, index, guarded=guarded)

                started = time.monotonic()
                with ThreadPoolExecutor(max_workers=steps) as pool:
                    list(pool.map(write, range(steps)))
                elapsed.append(time.monotonic() - started)
        results[label] = min(elapsed)
    return results


def write_workflow(
    path: Path, repository: Path, trees: Path, steps: int, seconds: float, concurrent: bool
) -> None:
    body: list[dict[str, Any]] = []
    for index in range(steps):
        worktree = trees / f"step_{index}"
        body.append(
            {
                "name": f"setup_s{index}",
                "run": (
                    f"{sys.executable} -m cairn worktree setup --worktree {worktree} "
                    f"--branch step/s{index} --base main"
                ),
                "working_dir": str(repository),
                "timeout_sec": 300,
                "retry_policy": {"limit": 0, "interval_sec": 1},
            }
        )
        body.append(
            {
                "name": f"work_s{index}",
                "run": (
                    f"{sys.executable} -c 'import pathlib,time; "
                    f'pathlib.Path("out_{index}.txt").write_text("x"); '
                    f"time.sleep({seconds})'"
                ),
                "working_dir": str(worktree),
                "timeout_sec": 300,
                "retry_policy": {"limit": 0, "interval_sec": 1},
                "depends": [f"setup_s{index}"],
            }
        )
        body.append(
            {
                "name": f"commit_s{index}",
                "run": f"{sys.executable} -m cairn commit --message 'cairn(s{index}): work'",
                "working_dir": str(worktree),
                "timeout_sec": 300,
                "retry_policy": {"limit": 0, "interval_sec": 1},
                "depends": [f"work_s{index}"],
            }
        )
    workflow = {
        "type": "graph",
        "retry_policy": {"limit": 0, "interval_sec": 1},
        "max_active_steps": steps * 3 if concurrent else 1,
        "env": [{"PYTHONPATH": str(PACKAGE_ROOT)}],
        "steps": body,
    }
    path.write_text(json.dumps(workflow, indent=2), encoding="utf-8")


def measure_engine_fanout(steps: int, seconds: float) -> dict[str, float]:
    engine = shutil.which("dagu")
    if engine is None:
        return {}
    results: dict[str, float] = {}
    for label, concurrent in (("one step at a time", False), ("fanned out", True)):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "engine-home"
            home.mkdir()
            ensure_dag_retry_disabled(home / "base.yaml")
            repository = make_repository(root, "repo")
            trees = root / "trees"
            trees.mkdir()
            workflow = root / "measure.yaml"
            write_workflow(workflow, repository, trees, steps, seconds, concurrent)
            started = time.monotonic()
            outcome = subprocess.run(
                [engine, "start", "--run-id", f"measure_{label.replace(' ', '_')}", str(workflow)],
                capture_output=True,
                text=True,
                env={**os.environ, "DAGU_HOME": str(home)},
                check=False,
                timeout=1800,
            )
            results[label] = time.monotonic() - started
            if outcome.returncode != 0:
                print(outcome.stdout, file=sys.stderr)
                print(outcome.stderr, file=sys.stderr)
                raise SystemExit(f"the {label} run failed")
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--steps", type=int, default=5)
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--repeats", type=int, default=3)
    arguments = parser.parse_args()

    print(f"{arguments.steps} independent steps\n")

    print("git write mutex, over a worktree-add plus a commit per step")
    mutex = measure_mutex_cost(arguments.steps, arguments.repeats)
    for label, elapsed in mutex.items():
        print(f"  {label:<20} {elapsed:6.2f}s")
    baseline = mutex["without the mutex"]
    added = mutex["with the mutex"] - baseline
    # Reported per write, because the percentage is the wrong unit: the mutex makes git
    # writes serial, and what matters is the absolute time that costs a step whose work is
    # measured in minutes.
    print(f"  added                {added:6.2f}s over {arguments.steps * 2} git writes")
    print(f"  per git write        {added / (arguments.steps * 2) * 1000:6.0f}ms\n")

    print(f"engine fan-out, {arguments.seconds:g}s of work per step, mutex in place")
    engine = measure_engine_fanout(arguments.steps, arguments.seconds)
    if not engine:
        # A silent skip reads exactly like a passing measurement, and the fan-out numbers
        # are the half that needs the engine.
        print("  the engine is not installed, so the fan-out was not measured")
        return 1
    for label, elapsed in engine.items():
        print(f"  {label:<20} {elapsed:6.2f}s")
    ratio = engine["fanned out"] / engine["one step at a time"]
    print(f"  ratio                {ratio:6.2f}  (1.00 is no gain, {1 / arguments.steps:.2f} is ideal)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
