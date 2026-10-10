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

**A server Cairn starts is a server Cairn has claimed.** The engine's authentication begins
unclaimed and the first local process to reach its setup endpoint becomes administrator — a
shell `curl` won that race against a human in the spike — so an unclaimed running server is
an open administrator account on the user's machine. `start` claims it in the same breath as
starting it and hands the credentials over; a claim that cannot be made stops the server
instead. There is no third end state ([16]).

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
import secrets
import socket
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple, cast
from urllib.parse import urlsplit

from cairn.core import Child, PopenFactory
from cairn.enginehome import ENGINE_BINARY, run_records_path
from cairn.layout import view_base, view_health_url, view_setup_url
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

# What became of the administrator account on a server Cairn started, and the only three
# answers there are.
CLAIM_CLAIMED = "claimed"
CLAIM_HELD = "held"
CLAIM_REFUSED = "refused"
CLAIM_STATES: tuple[str, ...] = (CLAIM_CLAIMED, CLAIM_HELD, CLAIM_REFUSED)

# The account Cairn creates, named for what created it so a person reading the engine's own
# user list knows where it came from.
CLAIM_USERNAME = "cairn"
# Measured against Dagu 2.18.2, the engine refuses a password under twelve characters as not
# meeting its security requirements; `token_urlsafe(24)` is thirty-two of them from the
# platform's own entropy source.
CLAIM_PASSWORD_BYTES = 24
# One request to a server that has already answered its health check, so it is bounded the
# way that check is rather than the way a launch is.
CLAIM_TIMEOUT = 10.0


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
Post = Callable[[str, bytes, float], Answer]


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


class ViewClaim(NamedTuple):
    """What became of the administrator account on a server Cairn started.

    The credentials are present only for `claimed`, because that is the only state in which
    Cairn knows any: `held` means somebody else completed setup on this machine and their
    password is theirs, and `refused` means nothing was claimed at all.
    """

    state: str
    username: str | None
    password: str | None
    reason: str


class ViewStarted(NamedTuple):
    """A launched server, and whether it came up answering.

    The process identifier is carried because the person is told how to stop it in the same
    reply they are told it is up: it holds no run state, so stopping it loses nothing, and a
    person who cannot name the process cannot act on that.

    `claim` is `None` only where nothing was started or nothing answered. A server that is
    up and has no claim beside it is a state this module does not return.
    """

    status: ViewStatus
    command: tuple[str, ...]
    log: Path
    pid: int
    claim: ViewClaim | None = None


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


def post_setup(url: str, payload: bytes, timeout: float) -> Answer:
    """Put a claim to the setup address, and report what came back.

    Every outcome is a value, the way a health answer is: a refusal is as much a fact about
    the account as a success, and the caller has a server to stop either way.
    """
    request = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return Answer(int(response.status), response.read(HEALTH_BODY_LIMIT), "")
    except urllib.error.HTTPError as answered:
        return Answer(int(answered.code), answered.read(HEALTH_BODY_LIMIT), str(answered))
    except (urllib.error.URLError, OSError, ValueError) as unreachable:
        return Answer(None, b"", str(unreachable))


def mint_password() -> str:
    return secrets.token_urlsafe(CLAIM_PASSWORD_BYTES)


def claim_admin(
    base: str,
    *,
    post: Post = post_setup,
    mint: Callable[[], str] = mint_password,
) -> ViewClaim:
    """Take the administrator account on a server, so that nothing else can.

    The engine's server starts with its authentication unclaimed and the **first** local
    process to reach its setup endpoint becomes administrator — a shell `curl` beat every
    human to it when this was measured. A server left in that state is an open administrator
    account on the user's machine for as long as it runs, so Cairn claims the account in the
    same breath as starting the server and hands the credentials to the person.

    The password is minted here and returned once. Cairn writes it nowhere: the engine keeps
    its own hashed copy, and a copy in Cairn's state would be a credential Cairn holds.

    `held` is not a failure. Setup is completed once per machine and survives restarts, so
    the second server Cairn starts on a machine finds the account already taken — which is
    the end state this exists to reach.
    """
    password = mint()
    payload = json.dumps({"username": CLAIM_USERNAME, "password": password}).encode("utf-8")
    answer = post(view_setup_url(base), payload, CLAIM_TIMEOUT)
    if answer.code == 200:
        return ViewClaim(
            CLAIM_CLAIMED,
            CLAIM_USERNAME,
            password,
            f"Cairn created the administrator account {CLAIM_USERNAME!r} on {base}",
        )
    if answer.code == 403:
        return ViewClaim(
            CLAIM_HELD,
            None,
            None,
            f"the administrator account on {base} was already claimed, so this server "
            "answers to whoever holds it",
        )
    if answer.code is None:
        return ViewClaim(
            CLAIM_REFUSED,
            None,
            None,
            f"nothing answered the claim at {view_setup_url(base)}: "
            f"{answer.said or 'the connection was refused'}",
        )
    return ViewClaim(
        CLAIM_REFUSED,
        None,
        None,
        f"the claim at {view_setup_url(base)} answered {answer.code} with "
        f"{_glimpse(answer.body)}",
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


def stop(process: Child) -> None:
    """End a server Cairn started, for the one case where leaving it up is the hazard.

    A server whose administrator account could not be claimed is an open account for as
    long as it runs, and Cairn started it — so the only other acceptable end state is no
    server. Stopping one loses nothing: it holds no run state, and no run waits on it.
    """
    try:
        process.terminate()
    except OSError:
        pass


def start(
    where: Binding,
    *,
    base: str | None = None,
    factory: PopenFactory = subprocess.Popen,
    ask: Ask = ask_health,
    claim: Callable[[str], ViewClaim] = claim_admin,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> ViewStarted:
    """Serve the view, detached, claim its administrator account, and hand both over.

    Detached for the reason a run is: the server has to outlive the conversation that asked
    for it, and a process group the harness kills takes everything inside it. It is Cairn's
    command rather than a background job of the agent's own shell, because a shell job ties
    the server's life to the session and the link dies with the terminal.

    The poll is what makes the reply honest. A launched process says nothing about whether
    anything is listening, and the address Cairn is about to hand over is the thing being
    claimed — so it is read back from the health check, through the same reader that found it
    absent.

    **The claim is not a separate step a caller may skip.** The server's authentication
    begins unclaimed and the first local process to reach its setup endpoint becomes
    administrator, so a server that is up and unclaimed is an open administrator account on
    the user's machine. There are two end states here and no third: a server whose account
    Cairn created and handed over, or no server at all.
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
            taken = claim(address)
            if taken.state != CLAIM_REFUSED:
                return ViewStarted(found, command, log, pid, taken)
            stop(process)
            return ViewStarted(
                found._replace(
                    state=VIEW_ABSENT,
                    reason=f"{taken.reason}. Cairn stopped the server it started rather "
                    "than leave an administrator account on this machine for the next "
                    "local process to take",
                ),
                command,
                log,
                pid,
                taken,
            )
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
    "CLAIM_CLAIMED",
    "CLAIM_HELD",
    "CLAIM_PASSWORD_BYTES",
    "CLAIM_REFUSED",
    "CLAIM_STATES",
    "CLAIM_TIMEOUT",
    "CLAIM_USERNAME",
    "VIEW_ABSENT",
    "VIEW_FOREIGN",
    "VIEW_LOG_FILE",
    "VIEW_SERVING",
    "VIEW_STATES",
    "Answer",
    "Ask",
    "Bindable",
    "Binding",
    "ViewClaim",
    "ViewStarted",
    "ViewStatus",
    "Post",
    "ask_health",
    "claim_admin",
    "bindable",
    "binding",
    "mint_password",
    "post_setup",
    "serve_command",
    "start",
    "stop",
    "status",
    "view_log_path",
]
