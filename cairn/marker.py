"""The committed completion marker, its freshness key, and the run occasion.

A step's completion lives in the repository beside the work it describes, so a re-run is
the whole recovery story and no execution state has to survive for correctness.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypedDict, cast

from cairn.core import (
    EXIT_OK,
    CairnError,
    CommandResult,
    RuntimeContext,
    read_step_report,
    write_json,
)
from cairn.layout import occasion_path
from cairn.plan.schema import (
    INPUTS_SCOPE,
    ONCE_SCOPE,
    PERIOD_SCOPES,
    RUN_SCOPE,
    STEP_ID_PATTERN,
    WEEKLY_SCOPE,
)
from cairn.topology import node_name

MARKER_DIRECTORY = ".steps"
MARKER_SUFFIX = ".done"
# The marker records one line, and the step it quotes is free to answer at any length.
MARKER_SUMMARY_LIMIT = 200

# A wedged git would otherwise hold the marker write until the engine's own timeout kills
# the step, which would then report `cancelled` for work that had actually finished.
GIT_QUERY_TIMEOUT_SECONDS = 10.0

OCCASION_ENV = "CAIRN_OCCASION"
OCCASION_MOMENT_FORMAT = "%Y%m%dT%H%M%SZ"
OCCASION_PATTERN = re.compile(r"^(\d{8}T\d{6}Z)-[0-9a-f]{8}$")

# `once` records a constant, and its gate accepts any recorded key at all — so a step that
# declares no scope pays nothing for a feature it does not use, and its gate is an
# existence check by another name.
ONCE_KEY = "once"

# Every period but the week buckets by strftime; an ISO week number is not a field of the
# calendar year, so it is assembled from isocalendar instead.
PERIOD_FORMATS: dict[str, str] = {
    "hourly": "%Y-%m-%dT%H",
    "daily": "%Y-%m-%d",
    "monthly": "%Y-%m",
}

# A step that hashed either of these would key itself on state that has nothing to do with
# its inputs: its own protocol's markers, or every commit the repository has ever taken.
NOT_AN_INPUT = frozenset({MARKER_DIRECTORY, ".git"})

_STEP_ID = re.compile(f"^{STEP_ID_PATTERN}$")


class Marker(TypedDict):
    """What one `.steps/<id>.done` file records.

    `run_id` is the run that recorded the work, and it is here because the marker is the
    only artifact that survives a run, travels through every merge, and is per step — so it
    is the only honest answer to "which earlier run completed this" when a recovery run
    no-ops. Lineage is an observability contract: freshness still keys on scope and key
    alone, and a marker whose run cannot be found changes nothing a run does.
    """

    step_id: str
    run_id: str
    scope: str
    key: str
    summary: str


def mint_occasion(moment: datetime | None = None) -> str:
    """Mint the identity of one real occasion of running a plan.

    A scheduled trigger mints a new occasion; a recovery of a failed run continues an
    existing one. The moment is part of the value rather than read from the clock at each
    gate, so a run that crosses midnight cannot bucket its own steps into two days.
    """
    now = datetime.now(UTC) if moment is None else moment.astimezone(UTC)
    return f"{now.strftime(OCCASION_MOMENT_FORMAT)}-{secrets.token_hex(4)}"


def occasion_moment(occasion: str) -> datetime:
    """The moment an occasion was minted, in UTC.

    The pattern admits digits the calendar does not — a thirteenth month, a thirty-second
    day — so the parse is where an occasion is finally judged, and its failure has to
    carry the same cause as a malformed one.
    """
    match = OCCASION_PATTERN.match(occasion)
    try:
        if match is None:
            raise ValueError(occasion)
        return datetime.strptime(match.group(1), OCCASION_MOMENT_FORMAT).replace(
            tzinfo=UTC
        )
    except ValueError as exc:
        raise CairnError(
            "invalid_occasion",
            f"{occasion!r} is not an occasion minted by `cairn occasion new`",
        ) from exc


def _publish_occasion(path: Path) -> str:
    """Mint this run's occasion, or adopt the one a concurrent reader minted first.

    Created through a hard link rather than an exclusive open, so the file never exists
    empty: a fan-out wave's gates run at the same moment, and a loser that read a
    just-created file before its winner had written to it would key on nothing at all.
    """
    minted = mint_occasion()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(minted + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            # Whoever won wrote a whole value, but this file is on the user's own disk and
            # can arrive truncated or edited — so the adopted value is judged exactly as a
            # recorded one is, rather than becoming a freshness key nothing checked.
            return _judged(path, path.read_text(encoding="utf-8").strip())
    finally:
        Path(temporary).unlink(missing_ok=True)
    return minted


def _judged(path: Path, value: str) -> str:
    if not value:
        raise CairnError(
            "invalid_occasion",
            f"{path} holds no occasion, so every scoped step in this run would key on "
            "nothing at all",
        )
    occasion_moment(value)
    return value


def resolve_occasion(
    runs_root: Path, run_id: str, environment: Mapping[str, str]
) -> str:
    """The occasion this run is keyed on: the caller's where they supplied one, minted here
    where they did not.

    **The parameter is the override, not the source.** An occasion fixed when the workflow
    is written is reused by every later firing, and the freshness key for `run` scope *is*
    that value while a period key is the bucket its moment falls in — so from the second
    firing onward every scoped step finds a fresh marker, skips, and the run reports a clean
    success having done nothing. Measured over three firings of one such file: the first
    does the work, the second and third report `succeeded` with the step skipped.

    A cron firing has no override point at all ([03]), so nothing outside the run can supply
    one for it. Minting here is what covers every trigger path with one rule — and it is
    also what makes a *manual* re-run redo `run`-scoped work, which a pre-filled default in
    the engine's start dialog silently prevents.

    The run directory is keyed by run identity and `dagu retry` reuses that identity, so a
    recovery reads the occasion it is recovering rather than minting a second one. A fresh
    re-run under a new identity mints, and a caller who means to continue an earlier
    occasion passes it — which is the distinction doc 15 asks the trigger to make.
    """
    supplied = environment.get(OCCASION_ENV)
    if supplied:
        occasion_moment(supplied)
        return supplied
    path = occasion_path(runs_root, run_id)
    try:
        recorded = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        recorded = ""
    except (OSError, UnicodeDecodeError) as exc:
        raise CairnError(
            "invalid_occasion", f"{path}: the run's own occasion could not be read: {exc}"
        ) from exc
    if recorded:
        return _judged(path, recorded)
    try:
        return _publish_occasion(path)
    except (OSError, UnicodeDecodeError) as exc:
        raise CairnError(
            "invalid_occasion", f"{path}: this run's occasion could not be recorded: {exc}"
        ) from exc


def _period_key(scope: str, moment: datetime) -> str:
    if scope == WEEKLY_SCOPE:
        year, week, _ = moment.isocalendar()
        return f"{year}-W{week:02d}"
    return moment.strftime(PERIOD_FORMATS[scope])


def _read_path(root: Path, declared: str) -> Path:
    path = (root / declared).resolve()
    _refuse_unreadable(path, root, declared)
    return path


def _is_metadata(resolved: Path, root: Path) -> bool:
    """Whether a path within the tree is the repository's or this protocol's own state.

    A repository can be nested — a submodule or a vendored checkout — so `.git` disqualifies
    a path at any depth. Cairn's own marker directory only ever exists at the worktree root,
    so `.steps` disqualifies only there, and a plan directory that happens to share the name
    is hashed like any other input.
    """
    if not resolved.is_relative_to(root):
        return False
    parts = resolved.relative_to(root).parts
    return ".git" in parts or parts[:1] == (MARKER_DIRECTORY,)


def _refuse_unreadable(resolved: Path, root: Path, declared: str) -> None:
    """Judge a declared path by what it actually reads, not by what it is named.

    Both tests run against the resolved path, so a symlink cannot smuggle in a file
    outside the worktree, or the repository's own history, under a name that looks local.
    """
    if not resolved.is_relative_to(root):
        raise CairnError(
            "invalid_reads",
            f"declared read {declared!r} resolves outside the step's working directory",
        )
    if _is_metadata(resolved, root):
        raise CairnError(
            "invalid_reads",
            f"declared read {declared!r} names repository or protocol metadata, "
            "which is not an input to anything",
        )


def _file_states(root: Path, path: Path, declared: str) -> dict[str, str]:
    files = (
        sorted(item for item in path.rglob("*") if item.is_file())
        if path.is_dir()
        else [path]
    )
    states: dict[str, str] = {}
    for item in files:
        resolved = item.resolve()
        # What a walk happens to sweep up is not what the author declared, so an entry the
        # key has no business covering is passed over rather than made fatal. Refusing here
        # would let one stray symlink or one marker make a whole directory unkeyable, and
        # an unkeyable step does its work and fails to record it on every run for ever.
        if path.is_dir() and (
            _is_metadata(resolved, root) or not resolved.is_relative_to(root)
        ):
            continue
        try:
            # An input that is not there yet is a state like any other: the step redoes
            # its work the moment the file appears, which is what derived work should do.
            states[resolved.relative_to(root).as_posix()] = (
                hashlib.sha256(resolved.read_bytes()).hexdigest()
                if resolved.is_file()
                else "absent"
            )
        except OSError as exc:
            raise CairnError(
                "invalid_reads", f"declared read {declared!r}: {exc}"
            ) from exc
    if path.is_dir() and not states:
        # A directory a later step will fill is a state like a path that is not there yet:
        # the key moves the moment content arrives, rather than refusing until it does.
        states[f"{path.relative_to(root).as_posix()}/"] = "empty"
    return states


def _inputs_key(root: Path, reads: Sequence[str]) -> str:
    if not reads:
        raise CairnError(
            "invalid_reads", "scope 'inputs' requires at least one path in 'reads'"
        )
    states: dict[str, str] = {}
    for declared in reads:
        states.update(_file_states(root, _read_path(root, declared), declared))
    digest = hashlib.sha256()
    for name in sorted(states):
        digest.update(f"{name}\0{states[name]}\n".encode())
    return digest.hexdigest()


def current_key(
    scope: str,
    *,
    root: Path,
    reads: Sequence[str] = (),
    environment: Mapping[str, str],
    runs_root: Path,
    run_id: str,
) -> str:
    """The key the work would be done under, were it done now.

    The occasion is resolved only where a scope keys on it, so a plan of `once`- and
    `inputs`-scoped steps records none and pays nothing for a value it never reads.
    """
    if scope == ONCE_SCOPE:
        return ONCE_KEY
    if scope == INPUTS_SCOPE:
        return _inputs_key(root, reads)
    if scope == RUN_SCOPE:
        return resolve_occasion(runs_root, run_id, environment)
    if scope not in PERIOD_SCOPES:
        raise CairnError("invalid_scope", f"unknown freshness scope {scope!r}")
    return _period_key(
        scope, occasion_moment(resolve_occasion(runs_root, run_id, environment))
    )


def marker_path(root: Path, step_id: str) -> Path:
    if _STEP_ID.match(step_id) is None:
        raise CairnError("invalid_step_id", f"{step_id!r} is not a step id")
    return root / MARKER_DIRECTORY / f"{step_id}{MARKER_SUFFIX}"


def read_marker(root: Path, step_id: str) -> Marker | None:
    """The recorded marker, or None when the step has never been verified.

    A marker is a committed file, so it can arrive hand-edited, merge-mangled, or
    truncated. Every departure from the shape becomes `invalid_marker`, which the gate
    fails open on — the alternative is a report carrying whatever the file happened to
    hold, in the one place the design promises a frozen shape.
    """
    path = marker_path(root, step_id)
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise CairnError("invalid_marker", f"{path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise CairnError("invalid_marker", f"{path}: expected an object")
    record = cast(dict[str, Any], raw)
    damaged = [
        name
        for name in Marker.__annotations__
        if not isinstance(record.get(name), str)
    ]
    if damaged:
        raise CairnError(
            "invalid_marker",
            f"{path}: missing or not a string: " + ", ".join(sorted(damaged)),
        )
    if record["step_id"] != step_id:
        raise CairnError(
            "invalid_marker",
            f"{path}: records step {record['step_id']!r}, not {step_id!r}",
        )
    return cast(Marker, record)


def is_fresh(marker: Marker, scope: str, key: str) -> bool:
    """Whether a recorded marker still speaks for the work under the current key.

    `once` accepts whatever is recorded, including a key from a scope the step used to
    declare: a step an author has since declared done-once is done, and re-reading its
    former cadence would undo that decision.
    """
    if scope == ONCE_SCOPE:
        return True
    return marker["scope"] == scope and marker["key"] == key


def write_marker(
    root: Path, step_id: str, run_id: str, scope: str, key: str, summary: str
) -> Path:
    path = marker_path(root, step_id)
    marker: Marker = {
        "step_id": step_id,
        "run_id": run_id,
        "scope": scope,
        "key": key,
        "summary": summary,
    }
    write_json(path, cast(dict[str, Any], marker))
    return path


def _sweep_stale_markers(path: Path) -> None:
    """Clear temp files a killed writer left for this step, and only for this step.

    Unlike a report's, this directory is inside the worktree, so a fragment left by a
    SIGKILL would be swept into the work's own commit and ride every merge from there.

    The sweep is this step's own, because steps sharing a working directory write their
    markers at once and another step's fragment is the file that writer is about to move
    into place — collecting one would fail its step and, in a fan-out, abort the merge for
    every branch beside it. A fragment belonging to a step that never writes again is
    left for the commit step, which stages `.steps/<id>.done` by path and so carries no
    fragment into history.
    """
    for stale in path.parent.glob(f".{path.name}.*.tmp"):
        try:
            stale.unlink()
        except OSError:
            pass


def _one_line(summary: str) -> str:
    """Hold a marker's summary to the one line the protocol says it is.

    The text is a step's own account of itself, which for an agent step is model output of
    no fixed length or shape. It is the one thing a step writes that reaches git, so an
    unbounded or multi-line value would ride every merge from here.
    """
    flattened = " ".join(summary.split())
    if len(flattened) <= MARKER_SUMMARY_LIMIT:
        return flattened
    return flattened[: MARKER_SUMMARY_LIMIT - 1].rstrip() + "…"


def _refuse_ignored_marker(path: Path, working_directory: Path) -> None:
    """Refuse a marker the repository would silently decline to carry.

    Completion state that cannot be committed is completion state the next run cannot
    see, so every step would re-pay for ever while each run reported success. Outside a
    repository there is nothing to ask and nothing to refuse.
    """
    try:
        ignored = subprocess.run(
            ("git", "check-ignore", "-q", str(path)),
            cwd=working_directory,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=GIT_QUERY_TIMEOUT_SECONDS,
            # A step launched from inside another git invocation would otherwise be asked
            # about that repository rather than the worktree it is standing in.
            env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
        )
    except (OSError, subprocess.TimeoutExpired):
        return
    if ignored.returncode == 0:
        raise CairnError(
            "marker_ignored",
            f"{path} is ignored by this repository, so no commit would carry it",
        )


def _recording_run(
    root: Path, step_id: str, report: dict[str, Any], run_id: str
) -> str:
    """Which run actually did this work, which is not always the run writing the marker.

    A no-op run rewrites the marker — its gate opened over a step that did nothing, because
    the freshness key still matched. Stamping this run's identity there would replace the
    only durable answer to "who did the work" with the identity of a run that did none, and
    every recovery afterwards would inherit the lie. So a no-op keeps whatever the marker
    already recorded, and only a run that actually did the work claims it.

    An unreadable marker on that path leaves this run named, which is the honest answer when
    nothing else can be established.
    """
    if report["status"] != "noop":
        return run_id
    try:
        recorded = read_marker(root, step_id)
    except CairnError:
        return run_id
    return run_id if recorded is None else recorded["run_id"]


def run_marker_write(
    step_id: str,
    scope: str,
    reads: Sequence[str],
    context: RuntimeContext,
    environment: Mapping[str, str],
) -> CommandResult:
    """Record that this step's end state was asserted, under the key it was asserted at.

    Only verification reaches here. A marker an agent could write would survive its own
    failed verification, so an excluded step would find its own marker on the next run and
    never recover.

    The marker's one line is the step's own account of what it did. Only the step that did
    the work can say that, and the marker reaches git and outlives every report beside it,
    so a title composed at authoring time would be a lie carried in history.
    """
    working_directory = context.working_directory
    # The account belongs to the node that did the work, which the topology names for it —
    # not to this step, which is the record being written now.
    report = read_step_report(
        context.report_path.parent, node_name("work", step_id), context.run_id
    )
    summary = _one_line(report["summary"])
    key = current_key(
        scope,
        root=working_directory,
        reads=reads,
        environment=environment,
        runs_root=context.runs_root,
        run_id=context.run_id,
    )
    path = marker_path(working_directory, step_id)
    _refuse_ignored_marker(path, working_directory)
    _sweep_stale_markers(path)
    write_marker(
        working_directory,
        step_id,
        _recording_run(working_directory, step_id, report, context.run_id),
        scope,
        key,
        summary,
    )
    return CommandResult(
        EXIT_OK,
        "done",
        summary,
        [],
        False,
        None,
        {"scope": scope, "key": key, "marker": str(path)},
    )
