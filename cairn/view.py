"""Whether the address Cairn prints for a run is actually being served, and serving it.

Cairn prints one address per run — `layout.view_url` — and it is the first thing a person
tries to use. Nothing used to check it, so a run started, printed a link, and the browser
said nothing was there; the person worked out for themselves that `dagu server` had to be
running ([33]).

**Three states, and the third is why a status code is not enough.** Measured against Dagu
2.18.2: `GET /api/v1/health` answers `{"status":"healthy","timestamp":…,"uptime":…,
"version":"2.18.2"}` with no authentication, while `/health` and `/api/v2/health` both answer
`200` with the web app's HTML. A reader that believed a status code would call any web server,
or the wrong endpoint on the right one, a healthy view. So the body decides, and anything
else on that address is `foreign` — reported, never answered by starting a second server on
top of it.

**Starting one is the view and only the view.** The command is `dagu server`, never `dagu
start-all` and never `dagu scheduler`: the scheduler's retry scanner re-executes every failed
run recorded on this machine in the last day ([docs/triggers.md]), and a request to see a
graph never reaches for that.

**It outlives the session that started it**, launched exactly the way a run is
([skill/trigger.launch_detached]) — its own session, stdin from `/dev/null`, output appended
to a log under the engine home. Closing the conversation, a harness killing its process tree
and a terminal hangup all stop at that boundary, so the link still loads tomorrow.

Nothing here is on a run's path. The run never waits on the view and never depends on it:
`dagu start` runs a workflow with no server at all, and a run whose view was declined is the
same run.
"""

from __future__ import annotations

import json
import socket
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple, cast
from urllib.parse import urlsplit

from cairn.core import PopenFactory
from cairn.enginehome import ENGINE_BINARY, run_records_path
from cairn.layout import view_base, view_health_url
from cairn.skill.trigger import launch_detached
from cairn.workflow.schema import ENGINE_VERSION

# What the view's address answers, and the only three answers there are.
VIEW_SERVING = "serving"
VIEW_ABSENT = "absent"
VIEW_FOREIGN = "foreign"
VIEW_STATES: tuple[str, ...] = (VIEW_SERVING, VIEW_ABSENT, VIEW_FOREIGN)

# The body a serving view answers with, measured on 2.18.2 and matched on rather than
# guessed at: the field that says it is healthy, and the field that says what it is.
HEALTH_STATUS = "healthy"

# Another program's text, so it is read bounded. The body is 91 bytes on 2.18.2 and the web
# app's HTML is tens of kilobytes; a few kilobytes tells the two apart with room to spare.
HEALTH_BODY_LIMIT = 4096
# One request to loopback that either answers immediately or is refused immediately. Short,
# because this runs in front of a person waiting to be told where their run is.
HEALTH_TIMEOUT = 5.0

# How long a server Cairn just launched has to start answering. Measured: the server
# answered its own health check 3s after launch on a cold engine home, where it also writes
# its example DAGs; twenty seconds is room for a slower machine and still well inside the two
# minutes an agent harness allows its own tool call.
SERVING_TIMEOUT = 20.0
SERVING_INTERVAL = 0.2

# Where a server Cairn started says what it is doing, under the engine's own home beside the
# run history rather than in any repository: the server is the machine's, not one run's.
VIEW_LOG_FILE = "view-server.log"


class ViewStatus(NamedTuple):
    """What is on the view's address, with the reason in the same breath as the state.

    `version` is only ever the engine's own answer about itself, so it is `None` for every
    state but `serving` — a foreign server's version, if it even stated one, is a fact about
    something else.
    """

    state: str
    base: str
    reason: str
    version: str | None


class Answer(NamedTuple):
    """One reply to the health address, or the reason there was none.

    `code` is `None` where nothing answered at all, which is the whole of the difference
    between a view that is absent and a port that is held by something else.
    """

    code: int | None
    body: bytes
    said: str


Ask = Callable[[str, float], Answer]


class Binding(NamedTuple):
    """The host and port a server would be told to bind, read back off the address."""

    host: str
    port: int


class Bindable(NamedTuple):
    """Whether this shell may bind the view's address, asked before anything is launched."""

    binding: Binding | None
    reason: str

    @property
    def allowed(self) -> bool:
        return self.binding is not None


class ViewStarted(NamedTuple):
    """A launched server, and whether it came up answering.

    The process identifier is carried because the person is told how to stop it in the same
    reply they are told it is up: it holds no run state, so stopping it loses nothing, and a
    person who cannot name the process cannot act on that.
    """

    status: ViewStatus
    command: tuple[str, ...]
    log: Path
    pid: int


def view_log_path() -> Path:
    """Where a server Cairn started appends everything it says."""
    return run_records_path().parent / VIEW_LOG_FILE


def ask_health(url: str, timeout: float) -> Answer:
    """Put the health question to whatever is on that address, and report what came back.

    Every outcome is a value rather than an exception, because all three states are ordinary
    answers to this question: a refused connection is the news that no view is running, and
    an HTTP error is the news that something else is.
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return Answer(int(response.status), response.read(HEALTH_BODY_LIMIT), "")
    except urllib.error.HTTPError as answered:
        # Something served that address and refused the request, which is a fact about what
        # is there — never an absence.
        return Answer(int(answered.code), answered.read(HEALTH_BODY_LIMIT), str(answered))
    except (urllib.error.URLError, OSError, ValueError) as unreachable:
        return Answer(None, b"", str(unreachable))


def status(base: str | None = None, *, ask: Ask = ask_health) -> ViewStatus:
    """What is on the view's address right now, as one of three states and a reason.

    The body is what decides. A JSON object saying it is healthy and naming a version is a
    view; everything else that answers is `foreign`, including the engine's own web app on a
    neighbouring path. The version is checked against the engine Cairn pins and the drift is
    stated rather than refused: a view of another version still draws the run, and the pin is
    the run's gate to enforce ([workflow/gate.assert_pinned]), not a reader's.
    """
    where = view_base() if base is None else base.rstrip("/")
    answer = ask(view_health_url(where), HEALTH_TIMEOUT)
    if answer.code is None:
        return ViewStatus(
            VIEW_ABSENT,
            where,
            f"nothing answered at {where}: {answer.said or 'the connection was refused'}",
            None,
        )
    if answer.code != 200:
        return ViewStatus(
            VIEW_FOREIGN,
            where,
            f"something on {where} answered the health check with {answer.code}",
            None,
        )
    try:
        document: Any = json.loads(answer.body)
    except (ValueError, UnicodeDecodeError):
        return ViewStatus(
            VIEW_FOREIGN,
            where,
            f"something on {where} answered 200 with {_glimpse(answer.body)} rather than "
            "the engine's health JSON",
            None,
        )
    if not isinstance(document, dict):
        return ViewStatus(
            VIEW_FOREIGN, where, f"something on {where} answered 200 with JSON that is not "
            "the engine's health document", None,
        )
    fields = cast(dict[str, Any], document)
    said = fields.get("status")
    version = fields.get("version")
    if said != HEALTH_STATUS or not isinstance(version, str) or not version:
        return ViewStatus(
            VIEW_FOREIGN,
            where,
            f"something on {where} answered 200 with a JSON document that does not say "
            f"{HEALTH_STATUS!r} and name its version",
            None,
        )
    if version != ENGINE_VERSION:
        return ViewStatus(
            VIEW_SERVING,
            where,
            f"{ENGINE_BINARY} {version} is serving {where}, and Cairn generates for "
            f"{ENGINE_VERSION}",
            version,
        )
    return ViewStatus(
        VIEW_SERVING, where, f"{ENGINE_BINARY} {version} is serving {where}", version
    )


def _glimpse(body: bytes) -> str:
    """Enough of another program's answer to recognise it by, and no more."""
    text = " ".join(body.decode("utf-8", errors="replace").split())
    return f"{text[:60]!r}" if text else "an empty body"


def binding(base: str) -> Binding | None:
    """The host and port to serve, read off the address Cairn prints, or None.

    None where the address is not one this machine could serve: a scheme the engine's server
    does not speak, no port to bind, or no host at all. A server started on a guess would
    serve an address nobody was given.
    """
    parts = urlsplit(base)
    if parts.scheme != "http":
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    if port is None or not parts.hostname:
        return None
    return Binding(parts.hostname, port)


def bindable(base: str) -> Bindable:
    """Whether this shell may bind the view's address, before anything is launched.

    The same wall a run meets at the engine's unix socket ([workflow/gate.rehearse_start]),
    in its TCP spelling: an agent harness sandboxes its shell by default, and a server
    launched detached into that refusal would be a process that dies in a log nobody is
    watching while the person waits for a page to load. Asking for the bind here turns that
    silence into a sentence.

    A port something else already holds answers here too, which is the one race `status` can
    lose: a server that appeared between the reading and the launch.
    """
    where = binding(base)
    if where is None:
        return Bindable(
            None,
            f"{base} is not an address this machine can serve: Cairn starts a server on an "
            "http address with a port, and points at anything else through CAIRN_VIEW_BASE",
        )
    family = socket.AF_INET6 if ":" in where.host else socket.AF_INET
    probe = socket.socket(family, socket.SOCK_STREAM)
    try:
        probe.bind((where.host, where.port))
    except OSError as refused:
        return Bindable(
            None,
            f"this shell cannot bind {where.host}:{where.port}: {refused}. A sandboxed "
            "shell is refused the bind, and a port something else already holds cannot be "
            "taken; issue the start from a shell allowed to bind a TCP port, or point "
            "CAIRN_VIEW_BASE at a view served elsewhere",
        )
    finally:
        probe.close()
    return Bindable(where, f"this shell can bind {where.host}:{where.port}")


def serve_command(where: Binding) -> tuple[str, ...]:
    """The one invocation Cairn makes to serve a view.

    `server` and nothing else. `start-all` starts the scheduler beside it, and the
    scheduler's retry scanner re-executes every failed run on the machine — which is
    [capabilities/scheduling.md]'s escalation and never a step on the way to a graph.
    """
    return (ENGINE_BINARY, "server", "--host", where.host, "--port", str(where.port))


def start(
    where: Binding,
    *,
    base: str | None = None,
    factory: PopenFactory = subprocess.Popen,
    ask: Ask = ask_health,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> ViewStarted:
    """Serve the view, detached, and return once the address answers — or once it will not.

    Detached for the reason a run is: the server has to outlive the conversation that asked
    for it, and a process group the harness kills takes everything inside it. It is Cairn's
    command rather than a background job of the agent's own shell, because a shell job ties
    the server's life to the session and the link dies with the terminal.

    The poll is what makes the reply honest. A launched process says nothing about whether
    anything is listening, and the address Cairn is about to hand over is the thing being
    claimed — so it is read back from the health check, through the same reader that found it
    absent.
    """
    log = view_log_path()
    command = serve_command(where)
    process = launch_detached(command, log, factory)
    address = base if base is not None else f"http://{where.host}:{where.port}"
    pid = process.pid
    deadline = monotonic() + SERVING_TIMEOUT
    while True:
        found = status(address, ask=ask)
        if found.state == VIEW_SERVING:
            return ViewStarted(found, command, log, pid)
        exited = process.poll()
        if exited is not None:
            return ViewStarted(
                found._replace(
                    reason=f"{' '.join(command)} exited {exited} without serving "
                    f"{address}; what it said is at {log}"
                ),
                command,
                log,
                pid,
            )
        if monotonic() >= deadline:
            # The process is deliberately left alone. It may be a moment from answering, and
            # killing a server that is coming up, on a timer, is the one destructive move
            # available here — the person is told where it says what it is doing instead.
            return ViewStarted(
                found._replace(
                    reason=f"{address} did not answer within {SERVING_TIMEOUT:.0f}s of "
                    f"{' '.join(command)} being launched; what it says is at {log}"
                ),
                command,
                log,
                pid,
            )
        sleeper(SERVING_INTERVAL)


__all__ = [
    "HEALTH_BODY_LIMIT",
    "HEALTH_STATUS",
    "HEALTH_TIMEOUT",
    "SERVING_INTERVAL",
    "SERVING_TIMEOUT",
    "VIEW_ABSENT",
    "VIEW_FOREIGN",
    "VIEW_LOG_FILE",
    "VIEW_SERVING",
    "VIEW_STATES",
    "Answer",
    "Ask",
    "Bindable",
    "Binding",
    "ViewStarted",
    "ViewStatus",
    "ask_health",
    "bindable",
    "binding",
    "serve_command",
    "start",
    "status",
    "view_log_path",
]
