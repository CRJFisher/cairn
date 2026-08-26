"""The engine as a subprocess, the version it must be, and the two commands that gate a run.

The engine is invoked as a binary and never imported: its API is GPL-3.0 and importing it
would put the obligation on the combined work ([research-dagu.md]).

**The gate is mandatory and it is not sufficient.** `dagu validate` is strict about shape and
`dagu dry` builds an execution plan, so between them they catch an unknown key, a bad step
id, a missing retry interval, a dangling dependency and a cycle. Between them they still pass
an unresolved substitution, a missing working directory, `mark_success`, and a gate command
that cannot launch — which is why the preflight runs first and refuses on its own authority.

Both run against a scratch engine home rather than the operator's. Two reasons, and the
second is the one that matters: `dagu dry` writes, and a `--dagu-home` the engine has never
seen is created carrying `retry_policy: {limit: 3}` **active** — arming precisely the
scheduler hazard that re-executes every failed run on the machine ([09]).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from cairn.baseconfig import BASE_CONFIG_NAME, ensure_dag_retry_disabled
from cairn.enginehome import ENGINE_BINARY
from cairn.workflow.preflight import Fault
from cairn.workflow.schema import ENGINE_VERSION, WORKFLOW_SUFFIX

GATE_TIMEOUT = 120

# The rehearsal's own bound, and it is deliberately not `GATE_TIMEOUT`. This check exists so
# a person driving Cairn through an agent harness learns their shell cannot start a run
# *before* the harness's own tool call is killed — and that call is two minutes. A rehearsal
# bounded at two minutes would be killed at the same moment its refusal printed, leaving
# exactly the silence it was written to replace ([19 C]). Measured at 0.25s against a
# working engine, so twenty seconds is thirty times the observed cost and a sixth of the
# budget the caller has.
REHEARSAL_TIMEOUT = 20

# The engine writes its own structured log to stderr before it writes its findings, and the
# findings are last: `time=… level=WARN msg=…` lines, then `Error: Validation failed for
# <path>`, then one `- field '<name>': <reason>` per finding. A reader that kept the head of
# that stream kept the logging and dropped the cause — which is exactly what a fixed-length
# cut did, because the path in the middle is as long as the repository's own.
ENGINE_LOG_LINE = re.compile(r"^time=\S+\s+level=")
# What a refusal may carry of the engine's own words. Generous, because the reason is the
# whole point of reading it, and bounded, because it is text from another program.
REASON_LIMIT = 2000


def engine_reason(completed: subprocess.CompletedProcess[str]) -> str:
    """What the engine actually said, with its logging dropped and its findings kept.

    Every line the engine logged about itself is dropped and every line it wrote about the
    file is kept, so a refusal carries the cause a person has to act on rather than the
    noise in front of it. A refusal that hides its cause is a refusal the person has to
    reproduce by hand to read — which is what cost the second person to drive Cairn an
    attempt ([19 A]).
    """
    stream = completed.stderr or completed.stdout or ""
    said = [
        line.strip()
        for line in stream.splitlines()
        if line.strip() and ENGINE_LOG_LINE.match(line) is None
    ]
    return " ".join(said)[:REASON_LIMIT]


class EngineUnavailable(Exception):
    """No engine to gate against — never a gate that quietly passed."""


def engine_path(binary: str | None = None) -> str:
    resolved = binary or shutil.which(ENGINE_BINARY)
    if not resolved:
        raise EngineUnavailable(
            f"no {ENGINE_BINARY!r} on PATH. Cairn generates for {ENGINE_BINARY} "
            f"{ENGINE_VERSION} and validates every definition against it before a run, so "
            "there is no path that skips the gate"
        )
    return resolved


def engine_version(binary: str | None = None) -> str:
    try:
        completed = subprocess.run(
            (engine_path(binary), "version"),
            capture_output=True,
            text=True,
            timeout=GATE_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EngineUnavailable(f"{ENGINE_BINARY} version could not be run: {exc}") from exc
    if completed.returncode != 0:
        raise EngineUnavailable(
            f"{ENGINE_BINARY} version exited {completed.returncode}: {completed.stderr.strip()}"
        )
    return completed.stdout.strip()


def assert_pinned(binary: str | None = None) -> None:
    """Halt on any engine but the pinned one, naming both versions.

    The workflow format carries no version field of its own, so the pin is the installed
    binary and nothing else — and a mismatch presents as format drift rather than as a load
    error. Exact equality, because a range would claim knowledge about versions nothing here
    has been measured against.
    """
    found = engine_version(binary)
    if found != ENGINE_VERSION:
        raise EngineUnavailable(
            f"cairn generates for {ENGINE_BINARY} {ENGINE_VERSION} and the binary on this "
            f"machine is {found}. The workflow format carries no version of its own, so a "
            "mismatch is a format question rather than a bug in this plan: install "
            f"{ENGINE_VERSION}, or re-pin cairn deliberately and run its suite against the "
            "new engine"
        )


def scratch_home(root: Path) -> Path:
    """An engine home the run rehearsal and both gate commands can be pointed at.

    Shared rather than copied, because the rule it keeps is the sharp one: a `--dagu-home`
    the engine has never seen is created carrying `retry_policy: {limit: 3}` **active**,
    arming the scanner that re-executes every failed run on the machine ([09]). A second
    copy of this is how the two come to disagree, and the disagreement would arm it.
    """
    home = root / "home"
    home.mkdir(parents=True, exist_ok=True)
    ensure_dag_retry_disabled(home / BASE_CONFIG_NAME)
    return home


# One step that exits immediately, under a name well inside the engine's own bound so a
# rehearsal can never fail on its own name and report the host's fault as the plan's.
REHEARSAL_NAME = "cairn-rehearsal"
REHEARSAL_DAG: dict[str, Any] = {
    "type": "graph",
    "steps": [
        {
            "name": "probe",
            "run": "true",
            "timeout_sec": 30,
            "retry_policy": {"limit": 0, "interval_sec": 1},
        }
    ],
}


def rehearse_start(*, binary: str | None = None) -> None:
    """Refuse a shell the engine cannot take a run on, before anyone pays for finding out.

    **What the two gate commands structurally cannot ask.** Every `dagu start` opens a unix
    socket for the run — `/tmp/@dagu__<home>_<dag>_<hash>.sock` — before any step runs, and
    a shell that may not `bind` one gets `failed to start the unix socket server: listen
    unix …: bind: operation not permitted`. `dagu validate` and `dagu dry` never bind, so a
    workflow authors cleanly in an environment that cannot run it — and the cause reached
    the person only after their acceptance had been spent ([19 C]). That is exactly the cost
    `refuse_unusable_engine` exists to prevent, and until now it checked only the version.

    Anyone driving Cairn through a coding-agent harness is who this is for: such harnesses
    sandbox their shell by default, and the person may not know a socket is involved at all.

    Against a scratch home and never the machine's own, for the reason `scratch_home` states.
    The rehearsal DAG runs `true`, so it takes no lock, writes no report and touches no
    repository. Measured at 0.25s against a working engine, `Result: Succeeded`.
    """
    engine = engine_path(binary)
    with tempfile.TemporaryDirectory(prefix="cairn-rehearsal-") as scratch:
        root = Path(scratch)
        home = scratch_home(root)
        definition = root / f"{REHEARSAL_NAME}{WORKFLOW_SUFFIX}"
        definition.write_text(json.dumps(REHEARSAL_DAG, indent=2) + "\n", encoding="utf-8")
        try:
            completed = subprocess.run(
                (engine, "start", "--dagu-home", str(home), str(definition)),
                capture_output=True,
                text=True,
                timeout=REHEARSAL_TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired as silent:
            # The other spelling of the same fault: in the machine's own engine home the
            # bind was observed to sit for two minutes writing no status and no log, rather
            # than failing outright. A bound turns that silence into a refusal that arrives
            # while the acceptance is still standing.
            raise EngineUnavailable(
                f"{ENGINE_BINARY} did not take a one-step rehearsal run on within "
                f"{REHEARSAL_TIMEOUT}s, so this shell cannot start a run. Every run opens a unix "
                "socket before any step runs; a sandboxed shell is refused the bind. Issue "
                "the start from a shell allowed to bind a unix socket"
            ) from silent
        except (OSError, subprocess.SubprocessError) as unusable:
            raise EngineUnavailable(
                f"{ENGINE_BINARY} could not be asked to start a rehearsal run: {unusable}"
            ) from unusable
        if completed.returncode != 0:
            raise EngineUnavailable(
                f"{ENGINE_BINARY} could not take a one-step rehearsal run on from this "
                f"shell, so it could not take this plan's run on either: "
                f"{engine_reason(completed)}. Every run opens a unix socket before any step "
                "runs, and the shell that starts it must be allowed to bind one — a "
                "sandboxed agent harness usually is not. Issue the start from a shell with "
                "the sandbox lifted for that command"
            )


def gate(path: Path, *, binary: str | None = None, named: Path | None = None) -> list[Fault]:
    """Run both engine checks against a scratch data directory, and name what failed.

    The path is resolved before it is handed over: a relative path the engine cannot find is
    silently re-resolved against its own `dags` directory, so a gate given one could validate
    a different file entirely.

    `named` is where the gated bytes will be published, and it is what a refusal names. The
    authoring path gates a file in a scratch directory so a definition that failed is never
    left where anything could start it ([workflow/cli.py]) — and a person who is told a
    temporary directory's path has been told nothing they can look at.
    """
    assert_pinned(binary)
    engine = engine_path(binary)
    target = path.resolve()
    faults: list[Fault] = []
    with tempfile.TemporaryDirectory(prefix="cairn-gate-") as scratch:
        home = scratch_home(Path(scratch))
        for verb in ("validate", "dry"):
            try:
                completed = subprocess.run(
                    (engine, verb, "--dagu-home", str(home), str(target)),
                    capture_output=True,
                    text=True,
                    timeout=GATE_TIMEOUT,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise EngineUnavailable(
                    f"{ENGINE_BINARY} {verb} did not finish: {exc}"
                ) from exc
            if completed.returncode != 0:
                # The file's own published name leads, in Cairn's spelling and never cut:
                # the engine's copy of it is inside its message, and that message is what
                # gets bounded.
                reason = engine_reason(completed) or f"exited {completed.returncode}"
                faults.append(Fault(f"engine_{verb}", None, f"{named or target}: {reason}"))
                break
    return faults


__all__ = [
    "ENGINE_BINARY",
    "EngineUnavailable",
    "assert_pinned",
    "engine_path",
    "engine_reason",
    "engine_version",
    "gate",
    "rehearse_start",
    "scratch_home",
]
