"""One real `claude -p`, started in its own process group and bounded three ways.

`providers.run_claude` is deliberately not reused. It forces `--json-schema
STEP_REPORT_SCHEMA` and `_translate_result` *raises* when the fields that schema names are
absent — which is every session that is not a Cairn step. A probe driving the skill produces
a conversation, not a step report, so running it through that path would turn every correct
probe into a provider-protocol error. What is reused is `launch`, which turns the one failure
a start can produce into a cause, and nothing else.

The three bounds catch three different pathologies and none of them subsumes another.
`--max-turns` catches a session looping over a task it cannot finish; `--max-budget-usd`
catches a session whose turns are individually cheap and collectively not; the parent's own
wall clock catches a session that is not spending anything because it is stuck. Teardown is a
process-group kill, because the session's own children — a `git` that is waiting on a lock, a
`dagu` that is following a run — are not this process's to leave behind.

The environment is built from empty rather than filtered. A probe measures how a model reads
a request under the skill's rules; a `CAIRN_*` variable inherited from whoever started the
suite would put a second author in the room, and `CAIRN_PAID` inherited into a probe would
let a probe's own session start a paid thing. An allowlist forgets nothing when a new
variable is invented; a denylist forgets everything.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any, NamedTuple, Protocol, cast

from cairn.core import CairnError, launch

from paid.spend import Launch

PROVIDER_BINARY = "claude"


def tool(name: str) -> str:
    """Where a tool this suite drives actually is, or a refusal naming it.

    The provider is launched by absolute path and is deliberately **not** on the probe's own
    PATH. A probe that could run `claude` by name could open a session this suite never
    priced and the ledger would never see it; resolving the path here costs nothing and
    removes the possibility.
    """
    found = shutil.which(name)
    if found is None:
        raise CairnError(
            "provider_unavailable",
            f"{name!r} is not on PATH, and the paid suite drives it directly",
        )
    return found


# Everything the provider needs to find its credentials and its own installation, and
# nothing that could carry an opinion about Cairn into the session under test.
INHERITED: tuple[str, ...] = ("HOME", "USER", "LOGNAME", "SHELL", "TERM", "LANG", "LC_ALL", "TZ")

# No MCP server, whatever the machine running the suite has configured. A probe that could
# reach a tool the corpus never mentions is measuring that machine rather than the skill.
EMPTY_MCP = '{"mcpServers":{}}'

# Only the project's own settings, so the skill under test is the one in the tree beside this
# file rather than whatever is installed at the user level, and the user's hooks and memory
# do not join the conversation. The probe seeds `.claude/skills/cairn` itself.
#
# Nothing is passed to `--settings`, and no bundled skill is turned off. The set a probe is
# offered is the set a user's session is offered, so a reading taken here is taken in the
# configuration a person actually has — which is only affordable because Cairn is entered by
# name ([SKILL.md], [probes.py `invoke`]) and no other skill can answer in its place.
SETTING_SOURCES = "project"

# The mode Cairn already runs its own paid steps under ([__main__.py `_agent`]). A probe that
# had to be asked about every command would measure the permission dialog.
PERMISSION_MODE = "auto"

TERMINATE_GRACE_SECONDS = 5.0


class Bounds(NamedTuple):
    """Three independent ceilings on one session, each for a different way to run away."""

    turns: int
    budget_usd: float
    seconds: float


class Started(NamedTuple):
    """What one launch produced, before anything has been read out of it."""

    ordinal: int
    role: str
    session_id: str
    transcript: str
    exit_code: int | None
    seconds: float
    timed_out: bool
    command: tuple[str, ...]


class Group(Protocol):
    """`core.Child`, plus the pid the process group is named by.

    The group is what teardown addresses. A session's own children outlive a terminate sent
    to the session alone, and an orphaned `dagu follow` holding a pipe open is how a suite
    that has finished measuring comes to hang.
    """

    pid: int
    returncode: int | None
    stdin: Any
    stdout: Any

    def wait(self, timeout: float | None = ...) -> int: ...
    def communicate(self, *arguments: Any, **options: Any) -> tuple[Any, Any]: ...
    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...


GroupFactory = Callable[..., Group]


def environment(
    *,
    path: str,
    tmpdir: str,
    python_path: str,
    dagu_home: str | None = None,
    source: dict[str, str] | None = None,
) -> dict[str, str]:
    """The whole environment a probe runs under, built from empty.

    `GIT_CONFIG_GLOBAL` and `GIT_CONFIG_SYSTEM` are pinned at `/dev/null` rather than
    cleared: git falls back to the real files when the variables are absent, and a probe
    repository that inherited a global `user.name` would record whoever ran the suite.
    """
    values = os.environ if source is None else source
    built = {name: values[name] for name in INHERITED if name in values}
    built["PATH"] = path
    built["TMPDIR"] = tmpdir
    built["PYTHONPATH"] = python_path
    built["GIT_CONFIG_GLOBAL"] = os.devnull
    built["GIT_CONFIG_SYSTEM"] = os.devnull
    if dagu_home is not None:
        built["DAGU_HOME"] = dagu_home
    return built


def command(
    prompt: str,
    *,
    model: str,
    bounds: Bounds,
    session_id: str | None = None,
    resume: str | None = None,
    binary: str = PROVIDER_BINARY,
) -> list[str]:
    """The whole command line, so a free test can read what a paid run will do.

    Either a new session's id or the id of the one being continued, never both: the provider
    rejects the pair, and a case that asked for both would fail after the ladder had already
    priced it.
    """
    if (session_id is None) == (resume is None):
        raise CairnError(
            "invalid_arguments",
            "a launch either opens a session or continues one, and this asked for "
            f"{'both' if session_id is not None else 'neither'}",
        )
    line = [
        binary,
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-mode",
        PERMISSION_MODE,
        "--setting-sources",
        SETTING_SOURCES,
        "--strict-mcp-config",
        "--mcp-config",
        EMPTY_MCP,
        "--model",
        model,
        "--max-turns",
        str(bounds.turns),
        "--max-budget-usd",
        str(bounds.budget_usd),
    ]
    line.extend(("--session-id", session_id) if session_id is not None else ("--resume", cast(str, resume)))
    line.append(prompt)
    return line


def transcript_of(lines: Iterable[str]) -> str:
    """Every line up to and including the terminal result, and no further.

    Reading to EOF would wait for every inheritor of the pipe to close it, and a session's
    own children can hold it open indefinitely. The result is terminal and every assistant
    message precedes it, so stopping there loses nothing the observer reads.
    """
    kept: list[str] = []
    for line in lines:
        kept.append(line if line.endswith("\n") else line + "\n")
        if _is_result(line):
            break
    return "".join(kept)


def _is_result(line: str) -> bool:
    if not line.strip():
        return False
    try:
        message: Any = json.loads(line)
    except json.JSONDecodeError:
        return False
    if not isinstance(message, dict):
        return False
    return cast(dict[str, Any], message).get("type") == "result"


def run(
    token: Launch,
    prompt: str,
    *,
    cwd: Path,
    model: str,
    variables: dict[str, str],
    bounds: Bounds,
    resume: str | None = None,
    popen_factory: GroupFactory = subprocess.Popen,
) -> Started:
    """Start one session, read it to its result, and leave nothing running.

    The `Launch` is taken as a parameter it cannot construct: `Ledger.claim` is the only
    thing that mints one, so a loop that opens sessions faster than anyone counted meets the
    cap after one extra rather than after seven hundred.
    """
    session_id = str(uuid.uuid4()) if resume is None else resume
    line = command(
        prompt,
        model=model,
        bounds=bounds,
        session_id=None if resume is not None else session_id,
        resume=resume,
        binary=tool(PROVIDER_BINARY),
    )
    started = time.monotonic()
    process = cast(
        Group,
        launch(
            popen_factory,
            line,
            cwd=str(cwd),
            env=variables,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            start_new_session=True,
        ),
    )
    expired = _Watchdog(process, bounds.seconds)
    try:
        transcript = transcript_of(cast(Iterator[str], process.stdout))
    finally:
        expired.cancel()
        stop_group(process)
    return Started(
        ordinal=token.ordinal,
        role=token.role,
        session_id=session_id,
        transcript=transcript,
        exit_code=process.returncode,
        seconds=round(time.monotonic() - started, 3),
        timed_out=expired.fired,
        command=tuple(line),
    )


class _Watchdog:
    """The bound no session can talk its way past, because the parent holds it."""

    def __init__(self, process: Group, seconds: float) -> None:
        self.fired = False
        self._process = process
        self._timer = threading.Timer(seconds, self._fire)
        self._timer.start()

    def _fire(self) -> None:
        self.fired = True
        stop_group(self._process, immediate=True)

    def cancel(self) -> None:
        self._timer.cancel()


def stop_group(process: Group, *, immediate: bool = False) -> None:
    """Stop the session and everything it started, addressed as one group."""
    if process.poll() is not None:
        return
    try:
        group = os.getpgid(process.pid)
    except ProcessLookupError:
        return
    try:
        os.killpg(group, signal.SIGKILL if immediate else signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    if immediate:
        return
    try:
        process.wait(timeout=TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(group, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            return
        process.wait()
