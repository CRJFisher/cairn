"""Where a run's records go, as path arithmetic and nothing else.

A run's reports and its record live together, keyed by run identity, in Cairn's own state
directory beside the generated workflows — regenerable, outside every working tree, and
never inside the repository the plan is changing.

Nothing here touches git, the filesystem or the rest of Cairn. The runs root is resolved
once at authoring time, where a subprocess is affordable, and travels to every step in the
emitted workflow's `env:` block; a step composes its own path from that root and the run id
the engine gave it. The verify gate reads a step's report and fails **closed**, so a wedged
git between it and its own report path would push it shut over work that was actually done.

Being a leaf is what makes that true, so the run-id refusal leaves as a plain `ValueError`
and the one caller that has a report to write turns it into a cause.
"""

from __future__ import annotations

import ipaddress
import os
import re
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import quote

# The runs root, resolved at authoring time and exported into every step. Measured against
# Dagu 2.11.0: an `env:` entry reaches a step, a precondition and the lifecycle handler
# alike, which is what lets the run's release resolve its own report path.
RUNS_ROOT_ENV = "CAIRN_RUNS_DIR"

RUNS_DIRECTORY = "runs"
REPORTS_DIRECTORY = "reports"
RECORD_FILE = "record.json"
# What the engine itself said while it was taking a run on. Not the run's logs — those are
# the engine's own, under its home, and the view reads them. This is the start command's
# stdout and stderr, which nothing else keeps: a socket it could not bind, a definition it
# would not load, a run id it already holds. A detached start has nowhere else to say it.
ENGINE_LOG_FILE = "engine.log"
# The occasion this run is keyed on, beside its reports rather than under them: a report is
# one step's account and this is the whole run's, and every step's gate reads it.
OCCASION_FILE = "occasion"

# A run id reaches Cairn from `dagu start --run-id`, where a caller chooses it, and it is
# used here as a path segment. Anything that is not one plain segment is refused rather
# than resolved, because `..` would put a run's records outside the runs root entirely.
RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def check_run_id(run_id: str) -> str:
    """Refuse a run id that would not stay inside the runs root."""
    if RUN_ID.fullmatch(run_id) is None:
        raise ValueError(
            f"{run_id!r} is not a run id Cairn will key a directory on; it must be one "
            f"path segment matching {RUN_ID.pattern}"
        )
    return run_id


def run_directory(runs_root: Path, run_id: str) -> Path:
    """Everything one run leaves behind, under one directory named for the run.

    Per run rather than per attempt, and that is a decision: `dagu retry` reuses the run id
    and preserves the steps it skips, so a per-attempt directory would hide the first
    attempt's reports and the record would show finished work as never having happened.
    """
    return runs_root / check_run_id(run_id)


def reports_directory(runs_root: Path, run_id: str) -> Path:
    return run_directory(runs_root, run_id) / REPORTS_DIRECTORY


def record_path(runs_root: Path, run_id: str) -> Path:
    return run_directory(runs_root, run_id) / RECORD_FILE


def engine_log_path(runs_root: Path, run_id: str) -> Path:
    return run_directory(runs_root, run_id) / ENGINE_LOG_FILE


# Where the engine's own view serves one run. Measured against Dagu 2.11.0: the server binds
# `127.0.0.1:8080` unless told otherwise, and `/dag-runs/<name>/<run-id>` renders the run —
# live and cold alike, which is what makes a finished run readable from the surface that
# showed it running. A machine that binds the server elsewhere says so here.
VIEW_BASE_ENV = "CAIRN_VIEW_BASE"
VIEW_BASE_DEFAULT = "http://127.0.0.1:8080"
# The engine's own two, honoured where Cairn has not been told otherwise, so a machine that
# already says where its server listens does not have to say it twice.
ENGINE_HOST_ENV = "DAGU_HOST"
ENGINE_PORT_ENV = "DAGU_PORT"
_SCHEME = re.compile(r"https?://", re.IGNORECASE)


def view_base(environ: Mapping[str, str] | None = None) -> str:
    """Where this machine serves the engine's view.

    Cairn's own variable wins, then the engine's own two, then the measured default. A value
    with no scheme is refused rather than used: it would compose a relative link, which is
    worse than the default because it looks like an answer and goes nowhere.
    """
    values = os.environ if environ is None else environ
    declared = (values.get(VIEW_BASE_ENV) or "").strip()
    if declared and _SCHEME.match(declared):
        return declared.rstrip("/")
    host = (values.get(ENGINE_HOST_ENV) or "").strip()
    port = (values.get(ENGINE_PORT_ENV) or "").strip()
    if not host and not port:
        return VIEW_BASE_DEFAULT
    return f"http://{_reachable(host)}:{port or '8080'}"


def _reachable(host: str) -> str:
    """A bind address as somewhere a browser can actually go.

    `DAGU_HOST` is where the server listens, not where a reader is. The two wildcards mean
    "every interface" and resolve to nothing in a browser, and a bare IPv6 literal has to be
    bracketed or its port reads as part of the address.
    """
    if not host or host in ("0.0.0.0", "::", "[::]"):
        return "127.0.0.1"
    try:
        ipaddress.IPv6Address(host.strip("[]"))
    except ValueError:
        return host
    return f"[{host.strip('[]')}]"


def view_url(dag_name: str, run_id: str, base: str | None = None) -> str:
    """The live view's address for one run.

    Composed from the **engine's own name for the workflow**, which is its filename, rather
    than from the plan's slug: a definition published under another name — into the
    directory a scheduler watches, or through `--out` — is served under that name and
    nowhere else.
    """
    root = view_base() if base is None else base.rstrip("/")
    return f"{root}/dag-runs/{quote(dag_name, safe='')}/{quote(run_id, safe='')}"


def occasion_path(runs_root: Path, run_id: str) -> Path:
    """Where this run records the occasion it is keyed on.

    Per run rather than per attempt, which is what makes `dagu retry` — which reuses the
    run id — continue the occasion it is recovering rather than mint a second one.
    """
    return run_directory(runs_root, run_id) / OCCASION_FILE


__all__ = [
    "ENGINE_HOST_ENV",
    "ENGINE_LOG_FILE",
    "ENGINE_PORT_ENV",
    "OCCASION_FILE",
    "RECORD_FILE",
    "REPORTS_DIRECTORY",
    "RUNS_DIRECTORY",
    "RUNS_ROOT_ENV",
    "RUN_ID",
    "VIEW_BASE_DEFAULT",
    "VIEW_BASE_ENV",
    "check_run_id",
    "engine_log_path",
    "occasion_path",
    "record_path",
    "reports_directory",
    "run_directory",
    "view_base",
    "view_url",
]
