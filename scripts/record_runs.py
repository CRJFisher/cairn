"""Record the fixture corpus by running a real engine, one shape per recorded run.

Every fixture's `status.jsonl` is a real Dagu 2.11.0 file, copied verbatim from a run this
script executed. That is not fastidiousness: the corpus exists to prove things *about the
engine*, and the one fixture the exit criteria name — a run the engine calls a clean success
over an excluded step — is a claim only the engine can make. Hand-authoring it would assert
the author's belief and test nothing.

The step reports are real too, written by real `python3 -m cairn exec` invocations inside
those runs. What no free run can produce is an agent's own detail — cost, session identity,
turns — so exactly one shape carries a hand-augmented work report, and its README says so.

Two shapes cannot come from a Cairn-generated workflow, because Cairn's emitted pattern is
designed to make them impossible: a real exclusion always leaves a `failed` node behind, so
the engine reports `PartiallySucceeded` rather than a clean success. Those are written by
hand against the node-name grammar, and the fixture's README names the recipe. The
extraction is the check that Cairn's own pattern still leaves that failed node; a corpus
that could only express the safe shape could not perform that check.

One shape spends money. `agent` runs a real coding-agent session, which is the only way a
recorded run can carry a step's receipts — its cost, its session identity, its turns — rather
than only their absence. It is therefore **not** recorded by default and refuses without an
explicit opt-in, which is [17](17-paid-end-to-end.md)'s discipline applied to the one paid
thing that exists today: the obvious command cannot spend a penny.

    python3 -m scripts.record_runs                       # every free shape
    python3 -m scripts.record_runs --shape green         # one of them
    CAIRN_PAID=1 python3 -m scripts.record_runs --paid   # the agent shape, deliberately
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from paid.redact import redact_reports
from paid.spend import opted_in, refuse_unpaid
from paid.vocabulary import PAID_OPT_IN

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = PACKAGE_ROOT / "fixtures" / "runs"
ENGINE = shutil.which("dagu")

# Long enough that the run is unambiguously mid-flight when it is sampled or killed, short
# enough that a recording session is not a coffee break.
HOLD_SECONDS = 30
SAMPLE_SECONDS = 3

# Which shape spends is the recorder's own knowledge, and so is what one has cost. The gate
# those two numbers are handed to belongs to the suite that spends ([paid/spend.py]), so
# there is one refusal in this repository rather than one per caller.
PAID_SHAPES = frozenset({"agent"})
MEASURED_COST_USD = 0.32


def cairn(*arguments: str) -> str:
    """One quoted invocation, the way the emitters build one.

    Joining on spaces would split an argument that has one, which the engine then hands to
    argparse as a stray operand — a step that fails on its own command line rather than on
    its work, and a fixture that records the wrong thing.
    """
    return shlex.join([sys.executable, "-m", "cairn", *arguments])


def step(
    name: str,
    run: str,
    *,
    depends: list[str] | None = None,
    precondition: str | None = None,
    continue_on: dict[str, bool] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": name,
        "run": run,
        "working_dir": "{WORKDIR}",
        "timeout_sec": 120,
        "retry_policy": {"limit": 0, "interval_sec": 1},
    }
    if depends:
        body["depends"] = depends
    if precondition is not None:
        body["preconditions"] = [{"condition": precondition}]
    if continue_on is not None:
        body["continue_on"] = continue_on
    return body


def workflow(steps: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "graph",
        "max_active_steps": max(len(steps), 1),
        "retry_policy": {"limit": 0, "interval_sec": 1},
        "params": [
            {"CAIRN_REPOSITORY": "{WORKDIR}"},
            {"CAIRN_PARENT_BRANCH": "main"},
            {"CAIRN_OCCASION": "20260810T120000Z-fixture"},
        ],
        "env": [
            {"PYTHONPATH": str(PACKAGE_ROOT)},
            {"CAIRN_RUNS_DIR": "{RUNS}"},
        ],
        "steps": steps,
    }


def _verified_step(name: str, *, after: list[str] | None = None) -> list[dict[str, Any]]:
    """One step's five nodes, the way the topology lays them out, all of them passing."""
    return [
        step(f"work_{name}", cairn("exec", "--command", "true"), depends=after),
        step(f"verify_{name}", "true", depends=[f"work_{name}"]),
        step(f"mark_{name}", cairn("exec", "--command", "true"), depends=[f"verify_{name}"]),
        step(
            f"commit_{name}",
            cairn("exec", "--command", "true"),
            depends=[f"mark_{name}"],
            continue_on={"skipped": True},
        ),
    ]


def _excluded_step(name: str, *, after: list[str] | None = None) -> list[dict[str, Any]]:
    """A step whose gate closes without any node failing — the shape I5 exists to catch.

    Cairn's own emitter cannot produce this: its assertion carries `continue_on: {failure:
    true}`, so a real exclusion leaves a `failed` node and the engine reports
    `PartiallySucceeded`. Here the marker's precondition simply declines, every other node
    succeeds or skips, and the engine reports a plain clean success over a step that
    recorded nothing.
    """
    return [
        step(f"work_{name}", cairn("exec", "--command", "true"), depends=after),
        step(f"verify_{name}", "true", depends=[f"work_{name}"]),
        step(f"mark_{name}", "true", depends=[f"verify_{name}"], precondition="false"),
        step(
            f"commit_{name}",
            cairn("exec", "--command", "true"),
            depends=[f"mark_{name}"],
            continue_on={"skipped": True},
        ),
    ]


AGENT_TASK = (
    "Bring this directory to a state where a file named note.txt exists and contains the "
    "single word hello. If it already does, change nothing."
)

SHAPES: dict[str, dict[str, Any]] = {
    "agent": {
        "why": (
            "one real paid agent step, so the corpus carries a step's receipts — its cost, "
            "its session identity, its turn count and its model — rather than only their "
            "absence. Everything else here is a command step, which can never populate them"
        ),
        "repository": True,
        "steps": [
            step(
                "work_alpha",
                cairn(
                    "agent", "run", "--provider", "claude",
                    "--prompt", AGENT_TASK,
                    "--max-budget-usd", "1",
                ),
            ),
            step("verify_alpha", "test -f note.txt", depends=["work_alpha"]),
            step(
                "mark_alpha",
                cairn("exec", "--command", "true"),
                depends=["verify_alpha"],
            ),
            step(
                "commit_alpha",
                cairn("commit", "--message", "cairn(alpha): the note"),
                depends=["mark_alpha"],
                continue_on={"skipped": True},
            ),
        ],
    },
    "green": {
        "why": "every step verified, nothing excluded, nothing left to do",
        "steps": [*_verified_step("alpha"), *_verified_step("beta", after=["commit_alpha"])],
    },
    "red": {
        "why": "a step fails, and everything behind it never runs",
        "steps": [
            step("work_alpha", cairn("exec", "--command", "false")),
            step("verify_alpha", "true", depends=["work_alpha"]),
            step("mark_alpha", "true", depends=["verify_alpha"], precondition="false"),
            step("commit_alpha", "true", depends=["mark_alpha"]),
            *_verified_step("beta", after=["commit_alpha"]),
        ],
    },
    "green-with-exclusions": {
        "why": (
            "the engine reports a clean Succeeded with exit 0 over an excluded step, "
            "because every node either succeeded or skipped and none failed. This is I5's "
            "regression fixture and the reason the verdict is derived by walking nodes"
        ),
        "steps": [
            *_verified_step("alpha"),
            *_excluded_step("beta", after=["commit_alpha"]),
            step("join_w1", cairn("exec", "--command", "true"), depends=["commit_beta"]),
        ],
    },
    "all-no-op": {
        "why": (
            "a recovery run in which every step's marker was still fresh, so the real "
            "marker gate skipped all of them and each left a no-op report naming the run "
            "that did the work. The engine spells this exactly as it spells a clean green"
        ),
        "repository": True,
        "markers": ("alpha", "beta"),
        "steps": [
            step(
                f"work_{name}",
                cairn("exec", "--command", "true"),
                depends=None if name == "alpha" else ["commit_alpha"],
                precondition=cairn("marker", "absent", "--step", name, "--scope", "once"),
                continue_on={"skipped": True},
            )
            for name in ("alpha", "beta")
        ]
        + [
            step(
                f"commit_{name}",
                cairn("exec", "--command", "true"),
                depends=[f"work_{name}"],
                continue_on={"skipped": True},
            )
            for name in ("alpha", "beta")
        ],
    },
    "blocked": {
        "why": (
            "a step is blocked on a human decision. The engine run and every other report "
            "are real; the work step's own `needs_user_decision` is set here after the "
            "fact, because no free provider can produce an agent that asks for a decision"
        ),
        "augment": {
            "work_alpha": {"needs_user_decision": True, "summary": "the schema change needs a call on the sentinel's default"}
        },
        "steps": [
            *_verified_step("alpha"),
        ],
    },
    "mid-run": {
        "why": (
            "sampled while the engine was still appending to it, so the file is many "
            "snapshots rather than the single compacted line a finished attempt leaves. "
            "Two steps are in flight and one has not started, which is the only place the "
            "difference between a sibling not yet started and a step downstream of a halt "
            "can be seen. Read cold its recording process is gone, so it reads as a crash "
            "unless a reader supplies a live process — which is the point: liveness is a "
            "property of now, never of the file"
        ),
        "steps": [
            step("work_alpha", cairn("exec", "--command", f"sleep {HOLD_SECONDS}")),
            step("work_beta", cairn("exec", "--command", f"sleep {HOLD_SECONDS}")),
            step("verify_alpha", "true", depends=["work_alpha"]),
            step("mark_alpha", "true", depends=["verify_alpha"]),
            step("commit_alpha", "true", depends=["mark_alpha"]),
            step("work_gamma", cairn("exec", "--command", "true"), depends=["commit_alpha"]),
            step("verify_gamma", "true", depends=["work_gamma"]),
            step("mark_gamma", "true", depends=["verify_gamma"]),
            step("commit_gamma", "true", depends=["mark_gamma"]),
        ],
        "sample": True,
    },
    "crashed": {
        "why": (
            "the orchestrator was killed mid-run, so the engine's record says `running` "
            "with no finish time and will say so forever"
        ),
        "steps": [
            step("work_alpha", cairn("exec", "--command", f"sleep {HOLD_SECONDS}")),
            step("verify_alpha", "true", depends=["work_alpha"]),
            step("mark_alpha", "true", depends=["verify_alpha"]),
            step("commit_alpha", "true", depends=["mark_alpha"]),
        ],
        "kill": True,
    },
}


def _render(document: dict[str, Any], workdir: Path, runs: Path) -> str:
    text = json.dumps(document, indent=2)
    return text.replace("{WORKDIR}", str(workdir)).replace("{RUNS}", str(runs))


def record(shape: str, definition: dict[str, Any]) -> None:
    if ENGINE is None:
        raise SystemExit("dagu is not on PATH, and every fixture is a real engine run")
    target = FIXTURES / shape
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        home, workdir, runs = root / "home", root / "work", root / "runs"
        for directory in (home, workdir, runs):
            directory.mkdir(parents=True)
        if definition.get("repository"):
            _seed_repository(workdir, definition.get("markers", ()))
        (home / "base.yaml").write_text(
            "retry_policy:\n  limit: 0\n  interval_sec: 1\n", encoding="utf-8"
        )
        path = root / f"{shape}.yaml"
        path.write_text(
            _render(workflow(definition["steps"]), workdir, runs), encoding="utf-8"
        )
        run_id = f"fixture-{shape.replace('-', '')}"
        environment = {**os.environ, "DAGU_HOME": str(home)}
        command = [ENGINE, "start", "--run-id", run_id, str(path)]

        if definition.get("sample") or definition.get("kill"):
            child = subprocess.Popen(
                command,
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            time.sleep(SAMPLE_SECONDS)
            state = _find_state(home)
            _publish(target, state, runs, run_id, definition["why"], shape)
            if definition.get("kill"):
                os.kill(child.pid, 9)
            child.wait(timeout=HOLD_SECONDS * 2)
            return

        subprocess.run(command, env=environment, capture_output=True, check=False)
        augment = definition.get("augment", {})
        _augment(runs / run_id / "reports", augment)
        for name in redact_reports(runs / run_id / "reports"):
            augment = {**augment, name: {**augment.get(name, {}), "rate_limits": "redacted"}}
        _publish(
            target, _find_state(home), runs, run_id, definition["why"], shape, augment
        )


def _seed_repository(workdir: Path, markers: tuple[str, ...]) -> None:
    """A real repository carrying real markers, so the real gate decides the real no-op."""
    for name in ("init --initial-branch=main .", "config user.email cairn@fixture",
                 "config user.name Cairn"):
        subprocess.run(["git", *shlex.split(name)], cwd=workdir, check=True,
                       capture_output=True)
    steps = workdir / ".steps"
    steps.mkdir(exist_ok=True)
    for name in markers:
        (steps / f"{name}.done").write_text(
            json.dumps(
                {
                    "step_id": name,
                    "run_id": "fixture-earlierrun",
                    "scope": "once",
                    "key": "once",
                    "summary": f"{name} was done by an earlier run",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    (workdir / "README.md").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=workdir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=workdir, check=True,
                   capture_output=True)


def _augment(reports: Path, changes: dict[str, dict[str, Any]]) -> None:
    """Set the fields no free run can produce, and only those the shape declares.

    Every other byte of the fixture is what the engine and Cairn actually wrote. The
    recording notes exactly which fields were set here, so a reader is never left guessing
    which half of a fixture is a measurement.
    """
    for name, fields in changes.items():
        path = reports / f"{name}.json"
        if not path.is_file():
            continue
        report: Any = json.loads(path.read_text(encoding="utf-8"))
        report.update(fields)
        path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _find_state(home: Path) -> Path:
    found = sorted((home / "data").rglob("status.jsonl"))
    if not found:
        raise SystemExit(f"the engine left no state file under {home}")
    return found[-1]


def _publish(
    target: Path,
    state: Path,
    runs: Path,
    run_id: str,
    why: str,
    shape: str,
    augmented: dict[str, dict[str, Any]] | None = None,
) -> None:
    if target.exists():
        shutil.rmtree(target)
    (target / "reports").mkdir(parents=True)
    shutil.copy2(state, target / "status.jsonl")
    reports = runs / run_id / "reports"
    if reports.is_dir():
        for report in sorted(reports.glob("*.json")):
            shutil.copy2(report, target / "reports" / report.name)
    (target / "recording.json").write_text(
        json.dumps(
            {
                "shape": shape,
                "run_id": run_id,
                "engine": "2.11.0",
                "why": why,
                "hand_set_fields": augmented or {},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"recorded {shape} -> {target}")


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="record_runs", description=__doc__)
    parser.add_argument("--shape", action="append", choices=sorted(SHAPES))
    parser.add_argument(
        "--paid",
        action="store_true",
        help=f"record the shapes that spend money; also needs {PAID_OPT_IN}=1",
    )
    args = parser.parse_args(arguments)
    # Naming no shape means every free one. A paid shape is never swept in by a bare command,
    # which is the whole of why it is a separate set rather than a flag on one.
    free: set[str] = set(SHAPES) - set(PAID_SHAPES)
    chosen: list[str] = args.shape or sorted(set(SHAPES) if args.paid else free)
    paid = [shape for shape in chosen if shape in PAID_SHAPES]
    refuse_unpaid(
        paid,
        opted_in=opted_in(args.paid),
        measured_usd=MEASURED_COST_USD * len(paid),
    )
    for shape in chosen:
        record(shape, SHAPES[shape])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
