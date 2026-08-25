"""The engine's machine-wide base configuration, read and edited without a YAML parser.

Every DAG on the machine inherits this file, and the engine ships it with DAG-level retry
active — which would re-execute every failed run of the last day, Cairn's or not, and for
Cairn a failed run is a paid agent session that mutated a repository. So the policy is
checked before any run rather than assumed, and written at acquisition.

Two properties matter more than convenience here, because the file is the user's and Cairn
is the only thing that edits it unattended:

**It fails closed.** Anything this reader cannot account for exactly — a shape it does not
recognise, a value it cannot read as an integer, two declarations of the same key — is
refused as unreadable. A hand-rolled reader that guesses is worse than one that stops: a
wrong "yes" here means every failed run on the machine gets re-executed silently.

**It verifies its own write.** After editing, the file is read back through the same reader
and the edit is required to have landed. A line-splice that produced a duplicate key or a
broken indent would otherwise leave a file the engine refuses to load at all, taking every
unrelated workflow on the machine down with it.
"""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple

from cairn.core import CairnError

BASE_CONFIG_NAME = "base.yaml"
DISABLE_COMMAND = "python3 -m cairn supervise base-config --disable"
DISABLED_RETRY_POLICY = "retry_policy:\n  limit: 0\n  interval_sec: 1\n"

_TOP_LEVEL_KEY = re.compile(r"^['\"]?([A-Za-z_][A-Za-z0-9_-]*)['\"]?\s*:(.*)$")
_NESTED_ENTRY = re.compile(r"^\s+([A-Za-z_][A-Za-z0-9_-]*):(.*)$")
_FLOW_MAPPING = re.compile(r"^\{([^{}]*)\}$")
_FLOW_ENTRY = re.compile(r"([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*([^,}]+)")
# A comment starts at a `#` that follows whitespace or begins the value; a `#` inside a
# quoted scalar does not. Only integers are read here, so quotes are stripped first and
# anything still unparseable is refused rather than guessed at.
_TRAILING_COMMENT = re.compile(r"(?:^|\s)#.*$")


class BasePolicy(NamedTuple):
    """The DAG-level retry policy a base configuration imposes on every run on the machine.

    `limit` is None only when no top-level `retry_policy` is present at all. A policy that
    is present but unreadable raises instead, because "present and not understood" and
    "absent" call for different answers.
    """

    limit: int | None
    first_line: int | None
    last_line: int | None


def base_config_path(environ: Mapping[str, str] | None = None) -> Path:
    """Where the engine keeps the configuration every DAG on the machine inherits.

    Resolved by arithmetic where every other engine path is asked of the engine
    ([enginehome.py]), and the asymmetry is deliberate. This directory is
    `os.UserConfigDir` on every platform the engine supports, so the arithmetic is right
    here where it is wrong for the data directory. And asking the binary would put a
    subprocess in front of the one check that must pass before a run's first spend — a
    check on a file that **invoking the engine creates**, carrying an active retry policy.
    Reading where this file is must not arm the hazard the reader is about to judge.
    """
    values = os.environ if environ is None else environ
    home = values.get("DAGU_HOME")
    if home:
        return Path(home) / BASE_CONFIG_NAME
    return Path.home() / ".config" / "dagu" / BASE_CONFIG_NAME


def _unreadable(path: Path, why: str) -> CairnError:
    return CairnError(
        "base_config_unreadable",
        f"{path}: {why}. Cairn will not guess at, or rewrite, a retry policy it cannot "
        "read — spell it as a block or as a mapping closed on one line, then run "
        f"`{DISABLE_COMMAND}`",
        detail={"path": str(path)},
    )


def _scalar(raw: str) -> str:
    return _TRAILING_COMMENT.sub("", raw).strip().strip("\"'")


def _integer(raw: str) -> int | None:
    try:
        return int(_scalar(raw))
    except ValueError:
        return None


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CairnError(
            "base_config_unreadable",
            f"{path}: {exc.strerror}",
            detail={"path": str(path)},
        ) from exc
    except UnicodeDecodeError as exc:
        raise _unreadable(path, "it is not UTF-8 text") from exc


def read_base_retry_policy(path: Path) -> BasePolicy:
    """Read the top-level `retry_policy`, or refuse.

    Two shapes are recognised and no others: a flow mapping that opens and closes on the
    key's own line, and a block whose entries are indented beneath it. Anything else is an
    unreadable file rather than a guess.
    """
    lines = _read(path).splitlines()
    found: BasePolicy | None = None
    index = 0
    while index < len(lines):
        match = _TOP_LEVEL_KEY.match(lines[index])
        if match is None or match.group(1) != "retry_policy":
            index += 1
            continue
        if found is not None:
            raise _unreadable(path, "it declares 'retry_policy' more than once")
        first = index
        remainder = _scalar(match.group(2))
        if remainder:
            flow = _FLOW_MAPPING.match(remainder)
            if flow is None:
                raise _unreadable(
                    path,
                    f"the value after 'retry_policy:' on line {index + 1} is neither a "
                    "block nor a mapping closed on the same line",
                )
            entries = dict(_FLOW_ENTRY.findall(flow.group(1)))
            if "limit" not in entries:
                raise _unreadable(
                    path,
                    f"the 'retry_policy' mapping on line {index + 1} carries no 'limit'",
                )
            limit = _integer(entries["limit"])
            if limit is None:
                raise _unreadable(
                    path,
                    f"the 'retry_policy' limit on line {index + 1} is not an integer",
                )
            found = BasePolicy(limit, first, index)
            index += 1
            continue
        limit = None
        last = first
        index += 1
        # Consume the whole block: every blank line and every indented line belongs to it,
        # including list items, which an entry pattern alone would stop at and splice over.
        while index < len(lines):
            line = lines[index]
            stripped = line.strip()
            if stripped and not line.startswith((" ", "\t")):
                # A comment at column zero is transparent to YAML, so it does not end the
                # mapping — stopping there would splice over only part of the block.
                if not stripped.startswith("#"):
                    break
                index += 1
                continue
            if stripped:
                last = index
                entry = _NESTED_ENTRY.match(line)
                if entry is not None and entry.group(1) == "limit":
                    limit = _integer(entry.group(2))
            index += 1
        if limit is None:
            raise _unreadable(
                path,
                f"the 'retry_policy' block at line {first + 1} carries no readable "
                "integer 'limit'",
            )
        found = BasePolicy(limit, first, last)
    if found is None:
        return BasePolicy(None, None, None)
    return found


def read_base_scalar(path: Path, key: str) -> str | None:
    """One top-level scalar setting, or None where the file declares none.

    Read through the same matcher the retry policy uses, so a quoted key, an indented
    namesake and a second declaration are all judged the way they are there rather than by
    a second, looser reader. A duplicate is refused for the same reason: the engine's own
    answer to two declarations is not something a hand-rolled reader may guess at.
    """
    found: str | None = None
    for number, line in enumerate(_read(path).splitlines(), start=1):
        match = _TOP_LEVEL_KEY.match(line)
        if match is None or match.group(1) != key:
            continue
        if found is not None:
            raise _unreadable(path, f"it declares {key!r} more than once")
        found = _scalar(match.group(2))
        del number
    return found


# The engine ships this armed: a scheduler restarting after downtime executes every cron
# slot missed inside the window, up to a thousand of them, and for Cairn each is a paid
# agent session. Measured against Dagu 2.11.0, the empty string is the only spelling that
# turns it off — a zero duration is refused as "duration must be positive" — and the
# engine's own schema states that an omitted window replays nothing.
CATCHUP_KEY = "catchup_window"
CATCHUP_DISABLED_LINE = 'catchup_window: ""\n'


def assert_catchup_disabled(path: Path) -> None:
    """Refuse a machine whose base configuration would replay missed cron slots.

    Absence is off rather than armed, which is where this parts company with the retry
    policy beside it: the engine's schema states that an omitted window replays nothing,
    while an absent *file* is a file the engine is about to write with retry active. So the
    two hazards read their absences differently because the engine treats them differently.
    """
    if not path.exists():
        return
    window = read_base_scalar(path, CATCHUP_KEY)
    if not window:
        return
    raise CairnError(
        "base_catchup_enabled",
        f"{path} declares {CATCHUP_KEY}: {window!r}, so a scheduler starting after "
        "downtime replays every cron slot missed inside that window — for Cairn, a paid "
        f"agent session each. Run `{DISABLE_COMMAND}`, which writes the empty window that "
        "turns it off",
        detail={"path": str(path), CATCHUP_KEY: window},
    )


def assert_dag_retry_disabled(path: Path) -> None:
    """Refuse to start a run on an engine that would re-execute failed runs by itself.

    An absent file is refused rather than trusted: the engine writes `base.yaml` on the
    first invocation of any of its commands, with `retry_policy: {limit: 3}` active, so
    "not there yet" means "enabled from the next command onward".
    """
    if not path.exists():
        raise CairnError(
            "base_retry_enabled",
            f"{path} does not exist. The engine creates it on its next invocation with "
            f"DAG-level retry enabled, which would re-execute paid agent work unasked. "
            f"Run `{DISABLE_COMMAND}` once on this machine first",
            detail={"path": str(path)},
        )
    policy = read_base_retry_policy(path)
    if policy.limit == 0:
        return
    held = "no DAG-level retry policy" if policy.limit is None else f"a limit of {policy.limit}"
    raise CairnError(
        "base_retry_enabled",
        f"{path} declares {held}, so a scheduler would re-execute every failed run on "
        f"this machine, Cairn's or not. Run `{DISABLE_COMMAND}` to fix it",
        detail={"path": str(path), "limit": policy.limit},
    )


def _write_atomically(path: Path, text: str) -> None:
    """Replace the file in one step, so a killed write cannot leave it half-written.

    The whole point of this module is a machine whose processes get killed; a truncating
    write here would silently reduce every other setting in the user's file to the
    engine's defaults, and the engine loads an empty base configuration without complaint.
    """
    # Written through the link rather than over it: replacing a symlink would leave the
    # user's own file untouched and silently re-enable retry the next time it is restored.
    target = path.resolve() if path.exists() else path
    mode = target.stat().st_mode & 0o777 if target.exists() else 0o600
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _top_level_keys(lines: list[str]) -> set[str]:
    return {
        match.group(1)
        for line in lines
        if (match := _TOP_LEVEL_KEY.match(line))
    }


def _disable_catchup(path: Path) -> bool:
    """Turn replay off in place, reporting whether anything changed.

    The two hazards are written by one command because a person asked one question — is it
    safe to run a scheduler here — and a refusal naming a remedy that fixes only half of it
    sends them back to the same refusal.
    """
    # Read tolerantly here: the filter below removes every declaration, so a file with two
    # of them is repairable rather than the dead end a refusal would make of it — the
    # refusal names this command as its remedy.
    declared = [
        line
        for line in _read(path).splitlines()
        if (match := _TOP_LEVEL_KEY.match(line)) and match.group(1) == CATCHUP_KEY
    ]
    if not declared or (len(declared) == 1 and not _scalar(declared[0].partition(":")[2])):
        return False
    lines = _read(path).splitlines(keepends=True)
    kept = [
        line
        for line in lines
        if not ((match := _TOP_LEVEL_KEY.match(line)) and match.group(1) == CATCHUP_KEY)
    ]
    # A file whose last line carries no newline would otherwise have the new key welded
    # onto the user's last setting, which reads back as that setting having a very odd
    # value rather than as damage.
    if kept and not kept[-1].endswith("\n"):
        kept.append("\n")
    _write_atomically(path, "".join(kept) + CATCHUP_DISABLED_LINE)
    try:
        landed = read_base_scalar(path, CATCHUP_KEY)
        kept_keys = _top_level_keys(_read(path).splitlines()) >= _top_level_keys(
            [line.rstrip("\n") for line in lines]
        )
    except CairnError:
        _write_atomically(path, "".join(lines))
        raise
    if landed or not kept_keys:
        _write_atomically(path, "".join(lines))
        raise _unreadable(
            path, "the catchup edit did not land and the file has been left unchanged"
        )
    return True


def ensure_dag_retry_disabled(path: Path) -> bool:
    """Write the disabling policy into the base configuration, reporting whether it changed.

    Acquisition edits rather than creates: the file exists on any machine that has run the
    engine once, and every other field in it is the user's. The edit is read back before it
    is accepted, because a splice that produced a duplicate key would leave a file the
    engine refuses to load — taking every unrelated workflow on the machine with it.
    """
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_atomically(path, CATCHUP_DISABLED_LINE + DISABLED_RETRY_POLICY)
        return True
    # Read *after* the catchup edit, because the retry splice below works on line indices
    # taken from the file as it now stands. Reading first would splice the new indices into
    # the old text — dropping whatever line the catchup edit removed and reverting the edit
    # itself, in a file every workflow on the machine inherits.
    catchup = _disable_catchup(path)
    original = _read(path)
    policy = read_base_retry_policy(path)
    if policy.limit == 0:
        return catchup

    lines = original.splitlines(keepends=True)
    replacement = DISABLED_RETRY_POLICY.splitlines(keepends=True)
    if policy.first_line is None or policy.last_line is None:
        prefix = lines if not original or original.endswith("\n") else [*lines, "\n"]
        updated = [*prefix, *replacement]
    else:
        updated = [*lines[: policy.first_line], *replacement, *lines[policy.last_line + 1 :]]
    _write_atomically(path, "".join(updated))

    try:
        landed = read_base_retry_policy(path)
        after = _read(path).splitlines()
        declarations = sum(
            1
            for line in after
            if (match := _TOP_LEVEL_KEY.match(line)) and match.group(1) == "retry_policy"
        )
        # Every other setting in this file is the user's. A splice that dropped one would
        # silently reduce it to the engine's default, which is the class of damage this
        # read-back exists to catch and which counting one key cannot see.
        kept = _top_level_keys(after) >= _top_level_keys(original.splitlines())
    except CairnError:
        _write_atomically(path, original)
        raise
    if landed.limit != 0 or declarations != 1 or not kept:
        _write_atomically(path, original)
        raise _unreadable(
            path, "the edit did not land as written and the file has been left unchanged"
        )
    return True


__all__ = [
    "BASE_CONFIG_NAME",
    "DISABLED_RETRY_POLICY",
    "DISABLE_COMMAND",
    "BasePolicy",
    "assert_dag_retry_disabled",
    "base_config_path",
    "ensure_dag_retry_disabled",
    "read_base_retry_policy",
]
