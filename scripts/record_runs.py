"""Record the fixture corpus by running a real engine, one shape per recorded run.

Every fixture's `status.jsonl` is a real file written by the pinned engine during a run this
script executed, and the script refuses to record against any other version. That is not fastidiousness: the corpus exists to prove things *about the
engine*, and the one fixture the exit criteria name — a run the engine calls a clean success
over an excluded step — is a claim only the engine can make. Hand-authoring it would assert
the author's belief and test nothing.

The step reports are real too, written by real `python3 -m cairn exec` invocations inside
those runs. What no command step can produce is an agent's own detail — session identity,
turns — so the `agent` shape opens one real session through `cairn agent run`. It spends the
subscription's allowance, so it is recorded only when it is named; the corpus test that every
recording names the pinned engine is what says it is due.

Two shapes cannot come from a Cairn-generated workflow, because Cairn's emitted pattern is
designed to make them impossible: a real exclusion always leaves a `failed` node behind, so
the engine reports `PartiallySucceeded` rather than a clean success. Those are written by
hand against the node-name grammar, and the fixture's README names the recipe. The
extraction is the check that Cairn's own pattern still leaves that failed node; a corpus
that could only express the safe shape could not perform that check.

    python3 -m scripts.record_runs                  # every command-step shape
    python3 -m scripts.record_runs --shape green    # one of them
    python3 -m scripts.record_runs --shape agent    # the one real session
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

from cairn.assertions import command_digest
from cairn.core import write_json
from cairn.headroom import READING_FILE
from cairn.layout import headroom_directory
from cairn.locks import acquire_run_lock
from cairn.workflow.gate import EngineUnavailable, assert_pinned
from cairn.workflow.schema import ENGINE_VERSION

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = PACKAGE_ROOT / "fixtures" / "runs"
ENGINE = shutil.which("dagu")

# Long enough that the run is unambiguously mid-flight when it is sampled or killed, short
# enough that a recording session is not a coffee break.
HOLD_SECONDS = 30
SAMPLE_SECONDS = 3

# What a recorded command names its interpreter by. The engine writes a step's command into
# its state file verbatim, and the recording runs under `sys.executable` so that the Cairn
# being recorded is the Cairn in this checkout — so the file it writes would otherwise carry
# the absolute interpreter path of the machine that recorded it, which is an author-shaped
# literal in a checked-in fixture ([16] task 7). The invocation was a `python3` one either
# way; what the normalisation drops is which `python3`.
RECORDED_INTERPRETER = "python3"


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
    timeout: int = 120,
    identifier: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": name,
        "run": run,
        "working_dir": "{WORKDIR}",
        "timeout_sec": timeout,
        "retry_policy": {"limit": 0, "interval_sec": 1},
    }
    if identifier is not None:
        body["id"] = identifier
    if depends:
        body["depends"] = depends
    if precondition is not None:
        body["preconditions"] = [{"condition": precondition}]
    if continue_on is not None:
        body["continue_on"] = continue_on
    return body


def workflow(steps: list[dict[str, Any]], *, provider_on_path: Path | None) -> dict[str, Any]:
    """The document the engine runs. `provider_on_path` puts a shape's own `claude` first.

    The engine hands a step a curated environment, so a shape that must not reach the real
    provider says so here rather than relying on whatever the recorder's shell had.
    """
    environment: list[dict[str, str]] = [
        {"PYTHONPATH": str(PACKAGE_ROOT)},
        {"CAIRN_RUNS_DIR": "{RUNS}"},
    ]
    if provider_on_path is not None:
        environment.append(
            {"PATH": f"{provider_on_path}:{os.environ.get('PATH', '')}"}
        )
    return {
        "type": "graph",
        "max_active_steps": max(len(steps), 1),
        "retry_policy": {"limit": 0, "interval_sec": 1},
        "params": [
            {"CAIRN_REPOSITORY": "{WORKDIR}"},
            {"CAIRN_PARENT_BRANCH": "main"},
            {"CAIRN_OCCASION": "20260810T120000Z-fixture"},
        ],
        "env": environment,
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


# How far ahead a seeded window reopens. Past `QUOTA_WAIT`, so a step that meets it is
# stopped there rather than held, and a recording takes seconds instead of days.
SEEDED_RESET_SECONDS = 3 * 86400
# The assertion both held shapes carry, and the bound their gate records for it.
HELD_ASSERTION = "true"
HELD_ASSERTION_BOUND = 600


def _held_step(name: str, *, after: list[str]) -> list[dict[str, Any]]:
    """One agent step's four nodes, laid out exactly as the emitter lays them out.

    The gates are the real ones, reading the real report the real `cairn agent run` left:
    what a held fixture has to record is what those gates do with it.
    """
    return [
        step(
            f"work_{name}",
            cairn(
                "agent", "run", "--provider", "claude", "--model", "sonnet",
                "--prompt", "Bring this directory to a state where note.txt holds hello.",
            ),
            depends=after,
            precondition=cairn("marker", "absent", "--step", name, "--scope", "once"),
            continue_on={"failure": True, "skipped": True},
        ),
        step(
            f"verify_{name}",
            HELD_ASSERTION,
            depends=[f"work_{name}"],
            identifier=f"verify_{name}",
            precondition=cairn(
                "verify", "needed", "--step", name,
                "--command-digest", command_digest(HELD_ASSERTION),
                "--bound", str(HELD_ASSERTION_BOUND),
            ),
            continue_on={"failure": True, "skipped": True},
        ),
        step(
            f"mark_{name}",
            cairn("exec", "--command", "true"),
            depends=[f"verify_{name}"],
            precondition=cairn(
                "verify", "gate", "--step", name, "--position", "chain",
                "--verify-exit", f"${{verify_{name}.exit_code}}",
            ),
        ),
        step(
            f"commit_{name}",
            cairn("exec", "--command", "true"),
            depends=[f"mark_{name}"],
            continue_on={"skipped": True},
        ),
    ]


def _seed_reading(runs: Path, windows: dict[str, dict[str, Any]], now: float) -> None:
    """Leave the shared reading the recorded run will find, in the gauge's own format.

    An allowance that is spent cannot be recorded: it is somebody's subscription running
    out. The reading is the one input these two shapes are given; the admission read off it,
    the hold, the gates and the record are all the real code on a real file.
    """
    write_json(
        headroom_directory(runs) / READING_FILE,
        {
            "windows": {
                name: {
                    "used": window.get("used"),
                    "status": window["status"],
                    "resets_at": int(now) + int(window["reopens_in"]),
                    "source": "stream",
                    "read_at": now,
                }
                for name, window in windows.items()
            },
            "api_key_source": None,
            "funding_read_at": None,
            "endpoint_called_at": None,
            "endpoint_failures": 0,
        },
    )


def _provider_stub(directory: Path, limit: tuple[str, int] | None) -> Path:
    """A `claude` on `PATH` for the length of one recording, and what it says.

    `limit` is the window a session meets mid-turn: the stub speaks the provider's own
    stream protocol — one `rate_limit_event` and one `result` ending `blocking_limit` — and
    nothing else, because a real account meeting a real limit is not something a recording
    can arrange. Given none, the stub is there to be caught: a shape whose step must be
    stopped before any session opens records a provider fault instead if one ever is.
    """
    directory.mkdir(parents=True, exist_ok=True)
    if limit is None:
        body = (
            'import sys\n'
            'print("cairn fixture: no session should have been opened", file=sys.stderr)\n'
            'sys.exit(9)\n'
        )
    else:
        window, resets_at = limit
        body = (
            "import json, sys\n"
            "arguments = sys.argv[1:]\n"
            'session = arguments[arguments.index("--session-id") + 1]\n'
            "sys.stdin.read()\n"
            "for message in (\n"
            '    {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", '
            f'"rateLimitType": "{window}", "resetsAt": {resets_at}, '
            '"overageStatus": "rejected", "isUsingOverage": False}},\n'
            '    {"type": "result", "subtype": "error_during_execution", '
            '"session_id": session, "num_turns": 3, "permission_denials": [], '
            '"terminal_reason": "blocking_limit", "result": "limit reached"},\n'
            "):\n"
            "    print(json.dumps(message), flush=True)\n"
            "sys.exit(1)\n"
        )
    stub = directory / "claude"
    stub.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    stub.chmod(0o755)
    return directory


SHAPES: dict[str, dict[str, Any]] = {
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
            "fact, because no command step can produce an agent that asks for a decision"
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
    "timed-out": {
        "why": (
            "the engine killed a step at its bound before any report was written — its "
            "node is a plain `failed` with the kill spelled only in its error — so its "
            "assertion's own gate declined the assertion for want of that report, the "
            "mark gate closed over the same absence, and the step behind it was skipped "
            "with its own gates doing the same. The record reads the kill over the gate's "
            "word for the first, and the halt over the engine's `skipped` for the second"
        ),
        "steps": [
            # A raw command rather than `cairn exec`, because the wrapper would catch the
            # engine's signal and leave a `cancelled` report; the fault this shape pins is
            # a step that left none.
            step(
                "work_alpha",
                f"sleep {HOLD_SECONDS}",
                timeout=2,
                continue_on={"failure": True, "skipped": True},
            ),
            step(
                "verify_alpha",
                "true",
                depends=["work_alpha"],
                identifier="verify_alpha",
                # The gate the emitter writes, on the node the emitter writes it on. Without
                # it this shape would record an assertion running over a step that left no
                # report — which no generated workflow can produce ([24 B]).
                precondition=cairn(
                    "verify", "needed", "--step", "alpha",
                    "--command-digest", command_digest("true"),
                ),
                continue_on={"failure": True, "skipped": True},
            ),
            step(
                "mark_alpha",
                cairn("exec", "--command", "true"),
                depends=["verify_alpha"],
                precondition=cairn(
                    "verify", "gate", "--step", "alpha", "--position", "chain",
                    "--verify-exit", "${verify_alpha.exit_code}",
                ),
            ),
            step("commit_alpha", cairn("exec", "--command", "true"), depends=["mark_alpha"]),
            step(
                "work_beta",
                cairn("exec", "--command", "true"),
                depends=["commit_alpha"],
                precondition=cairn("marker", "absent", "--step", "beta", "--scope", "once"),
                continue_on={"failure": True, "skipped": True},
            ),
            step(
                "verify_beta",
                "true",
                depends=["work_beta"],
                identifier="verify_beta",
                precondition=cairn(
                    "verify", "needed", "--step", "beta",
                    "--command-digest", command_digest("true"),
                ),
                continue_on={"failure": True, "skipped": True},
            ),
            step(
                "mark_beta",
                cairn("exec", "--command", "true"),
                depends=["verify_beta"],
                precondition=cairn(
                    "verify", "gate", "--step", "beta", "--position", "chain",
                    "--verify-exit", "${verify_beta.exit_code}",
                ),
            ),
            step("commit_beta", cairn("exec", "--command", "true"), depends=["mark_beta"]),
        ],
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
    "damaged": {
        "why": (
            "a run every step of which the engine and the gates recorded as verified, "
            "whose evidence was then damaged in five declared ways: the engine's own run "
            "status is gone, one node name is recorded twice, one work report claims the "
            "marker gate skipped work the engine says ran, one is the account of another "
            "node under this one's filename, and one is truncated. Nothing refused raises "
            "anything, every refusal is named, and the run is not a clean success"
        ),
        "steps": [
            *_verified_step("alpha"),
            *_verified_step("beta", after=["commit_alpha"]),
        ],
        "damage": {
            "state": {"drop": ["status"], "duplicate_node": ["commit_beta"]},
            "reports": {
                "work_alpha": {"status": "noop"},
                "work_beta": {"step_id": "work_alpha"},
            },
            "truncate": ["commit_alpha"],
        },
    },
    "held-before-session": {
        "why": (
            "the subscription's weekly allowance was past its hold threshold when the "
            "second step was admitted, and reopens days later — further ahead than the step "
            "may wait — so the step ended `quota_held` four seconds in, with no session "
            "opened and nothing in the tree changed. Its assertion was declined for having "
            "nothing to assert, and its gate closed with no divergence"
        ),
        "reading": {"seven_day": {"used": 0.98, "status": "allowed_warning", "reopens_in": SEEDED_RESET_SECONDS}},
        "provider": None,
        "steps": [*_verified_step("alpha"), *_held_step("beta", after=["commit_alpha"])],
    },
    "held-after-limit": {
        "why": (
            "the second step was admitted against an open 5-hour window, opened its "
            "session, and met the weekly limit mid-turn. The window reopens further ahead "
            "than the step may wait, so it ended `quota_held` holding a session id — and "
            "because that session did work, its assertion ran over what it left and its "
            "gate recorded the divergence between the two"
        ),
        "reading": {"five_hour": {"used": 0.1, "status": "allowed", "reopens_in": 9000}},
        "provider": ("seven_day", SEEDED_RESET_SECONDS),
        "steps": [*_verified_step("alpha"), *_held_step("beta", after=["commit_alpha"])],
    },
    "agent": {
        "why": (
            "one real agent step, so the corpus carries a step's receipts — its session "
            "identity, its turn count and its model — rather than only their absence. "
            "Everything else here is a command step, which can never populate them"
        ),
        "spends": True,
        "redact_account_state": ("work_alpha",),
        "steps": [
            step(
                "work_alpha",
                cairn(
                    "agent", "run", "--provider", "claude", "--model", "sonnet",
                    "--prompt",
                    "Bring this directory to a state where a file named note.txt exists "
                    "and contains the single word hello. If it already does, change "
                    "nothing.",
                ),
                timeout=600,
            ),
            step("verify_alpha", "test -f note.txt", depends=["work_alpha"]),
            step(
                "mark_alpha", cairn("exec", "--command", "true"), depends=["verify_alpha"]
            ),
            step(
                "commit_alpha",
                cairn("commit", "--step", "alpha", "--message", "cairn(alpha): the note"),
                depends=["mark_alpha"],
                continue_on={"skipped": True},
            ),
        ],
    },
}


def _render(document: dict[str, Any], workdir: Path, runs: Path) -> str:
    text = json.dumps(document, indent=2)
    return text.replace("{WORKDIR}", str(workdir)).replace("{RUNS}", str(runs))


def record(shape: str, definition: dict[str, Any]) -> None:
    if ENGINE is None:
        raise SystemExit("dagu is not on PATH, and every fixture is a real engine run")
    try:
        assert_pinned(ENGINE)
    except EngineUnavailable as exc:
        # A fixture stamped with the pin but written by another engine would describe an
        # engine nothing here runs against.
        raise SystemExit(str(exc)) from None
    target = FIXTURES / shape
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        home, workdir, runs = root / "home", root / "work", root / "runs"
        for directory in (home, workdir, runs):
            directory.mkdir(parents=True)
        run_id = f"fixture-{shape.replace('-', '')}"
        # Every runtime step proves it owns the repository it stands in before it writes, so
        # each shape runs in a real repository holding this run's lock — the state a real
        # run's own `lock acquire` node leaves before its first step.
        _seed_repository(workdir, definition.get("markers", ()))
        acquire_run_lock(
            workdir, run_id=run_id, plan=f"fixture-{shape}", run_timeout_seconds=HOLD_SECONDS * 4
        )
        (home / "base.yaml").write_text(
            "retry_policy:\n  limit: 0\n  interval_sec: 1\n", encoding="utf-8"
        )
        now = time.time()
        seeded: dict[str, Any] = definition.get("reading", {})
        if seeded:
            _seed_reading(runs, seeded, now)
        limit: tuple[str, int] | None = definition.get("provider")
        provider_on_path = (
            _provider_stub(
                root / "bin",
                None if limit is None else (limit[0], int(now) + limit[1]),
            )
            if "provider" in definition
            else None
        )
        path = root / f"{shape}.yaml"
        path.write_text(
            _render(
                workflow(definition["steps"], provider_on_path=provider_on_path),
                workdir,
                runs,
            ),
            encoding="utf-8",
        )
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
        redacted = definition.get("redact_account_state", ())
        _redact_account_state(runs / run_id / "reports", redacted)
        augment = {
            **augment,
            **{name: {"rate_limits": "redacted", "resets_at": "redacted"} for name in redacted},
        }
        _publish(
            target,
            _find_state(home),
            runs,
            run_id,
            definition["why"],
            shape,
            augment,
            definition.get("damage", {}),
            _seeded(seeded, limit, provider_on_path is not None)
            if "provider" in definition or seeded
            else {},
        )
        _damage(target, definition.get("damage", {}))


def _seeded(
    reading: dict[str, Any], limit: tuple[str, int] | None, stubbed: bool
) -> dict[str, Any]:
    """What this shape was given before the run, so no reader has to guess at it."""
    if not stubbed:
        return {"reading": reading, "provider": None}
    return {
        "reading": reading,
        "provider": (
            "a stub claude first on PATH, which meets the "
            f"{limit[0]} limit on its first turn and ends blocking_limit"
            if limit is not None
            else "a stub claude first on PATH, which fails if anything opens a session"
        ),
    }


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


def _damage(target: Path, declared: dict[str, Any]) -> None:
    """Break a published fixture in the ways the shape declares, and in no others.

    Damage cannot be recorded: the engine and Cairn's own gates are built so that none of
    these documents can be produced, which is why the corpus has to carry a run whose
    evidence was broken on purpose. Each edit is in the shape's own definition and is
    copied into the recording beside it, so no reader has to guess which half of the
    fixture is a measurement.
    """
    if not declared:
        return
    state: dict[str, Any] = declared.get("state", {})
    path = target / "status.jsonl"
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    record: dict[str, Any] = json.loads(lines[-1])
    for field in state.get("drop", []):
        record.pop(field, None)
    for name in state.get("duplicate_node", []):
        found = next(
            node for node in record["nodes"] if node["step"]["name"] == name
        )
        record["nodes"].append({**found, "status": 2, "error": "exit status 1"})
    lines[-1] = json.dumps(record)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _augment(target / "reports", declared.get("reports", {}))
    for name in declared.get("truncate", []):
        report = target / "reports" / f"{name}.json"
        text = report.read_text(encoding="utf-8")
        report.write_text(text[: len(text) // 2], encoding="utf-8")


def _redact_account_state(reports: Path, names: tuple[str, ...]) -> None:
    """Blank the recording account's own allowance figures, keeping their shape.

    A real session's report carries how much of each window the recording machine had used
    and when it resets. Those are personal to whoever recorded the fixture. The provider's
    raw events are zeroed, since nothing in the record reads them; the reset moments Cairn
    reads are nulled, which the record states as not known rather than as a moment.
    """
    for name in names:
        path = reports / f"{name}.json"
        if not path.is_file():
            continue
        report: Any = json.loads(path.read_text(encoding="utf-8"))
        detail = report.get("detail", {})
        for event in detail.get("rate_limits", []):
            for key in ("session_id", "uuid"):
                if key in event:
                    event[key] = "00000000-0000-0000-0000-000000000000"
            info = event.get("rate_limit_info", {})
            for key in ("resetsAt", "utilization"):
                if key in info:
                    info[key] = 0 if key == "resetsAt" else 0.0
        if "resets_at" in detail:
            detail["resets_at"] = None
        for window in detail.get("headroom", {}).get("reading", {}).values():
            window["resets_at"] = None
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
    damage: dict[str, Any] | None = None,
    seeded: dict[str, Any] | None = None,
) -> None:
    # Only what a recording writes is replaced. The README beside it is a person's account of
    # the shape, and a re-recording that took it along would leave the fixture unexplained.
    shutil.rmtree(target / "reports", ignore_errors=True)
    for recorded in ("status.jsonl", "recording.json"):
        (target / recorded).unlink(missing_ok=True)
    (target / "reports").mkdir(parents=True)
    (target / "status.jsonl").write_text(
        state.read_text(encoding="utf-8").replace(sys.executable, RECORDED_INTERPRETER),
        encoding="utf-8",
    )
    reports = runs / run_id / "reports"
    if reports.is_dir():
        for report in sorted(reports.glob("*.json")):
            shutil.copy2(report, target / "reports" / report.name)
    (target / "recording.json").write_text(
        json.dumps(
            {
                "shape": shape,
                "run_id": run_id,
                "engine": ENGINE_VERSION,
                "why": why,
                "hand_set_fields": augmented or {},
                "seeded": seeded or {},
                "normalised": {"interpreter": RECORDED_INTERPRETER},
                "damage": damage or {},
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
    args = parser.parse_args(arguments)
    # A shape that opens a real session spends the subscription's allowance, so it is
    # recorded only when someone names it.
    chosen: list[str] = args.shape or sorted(
        shape for shape, definition in SHAPES.items() if not definition.get("spends")
    )
    for shape in chosen:
        record(shape, SHAPES[shape])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
