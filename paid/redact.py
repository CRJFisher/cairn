"""The account's own rate-limit state, taken back out of what a paid run left behind.

A real agent step records the machine's rate-limit standing twice: `providers.py` keeps every
`rate_limit_event` on the step report's `detail.rate_limits`, and it tees the provider's raw
stream to stdout, which the engine captures into the run's log. Neither reaches a run record
— `record/extract.py` lifts named fields and not the bag — but both sit on disk in whatever
tree the run happened in, and this suite makes two kinds of tree out of that: a fixture that
is committed, and a probe world a session under test is free to read and quote.

Two treatments, because the two trees want different things. **A committed fixture keeps the
shape and loses the values**, so the corpus still looks like a real report; a probe world
**loses the field**, because nothing there is published and an empty list is the honest shape
for a run that met no limit. `named_state` is the independent check, run over a world before
any probe reads it: a scrub nobody verified is a scrub that silently stopped matching.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

# Every key the state is carried under, in a report and in a streamed event alike. One
# spelling in one place, because the scrub and the check that the scrub worked must look for
# the same things or the check is theatre.
#
# `resets_at` is the one that is easy to miss and the one that matters most on its own:
# `providers.py` derives it from the events and writes it beside them as a plain timestamp,
# so a scrub that took only the list out would leave the moment the account's limit lifts
# sitting in a world 253 sessions are free to read.
STATE_KEY = "rate_limit_info"
STATE_EVENT = "rate_limit_event"
REPORT_KEY = "rate_limits"
RESET_KEY = "resets_at"

# What must not appear anywhere in a probe world, and what a published line must never carry
# under any key. The two differ by one name on purpose: a scrubbed world keeps an emptied
# `rate_limits` field, because a report whose field vanished would teach a reader it does not
# exist — so the world check cannot look for that name, while the record guard must.
NAMED: tuple[str, ...] = (STATE_KEY, STATE_EVENT, RESET_KEY)
ACCOUNT_KEYS: tuple[str, ...] = (REPORT_KEY, STATE_KEY, RESET_KEY)

# What a committed fixture keeps: the shape of an event, with nothing personal in it. The
# corpus is meant to read like a real report, and a report whose rate-limit field vanished
# would teach a reader that the field does not exist.
REDACTED_RATE_LIMIT: dict[str, Any] = {
    STATE_KEY: {
        "isUsingOverage": False,
        "rateLimitType": "seven_day",
        "resetsAt": 0,
        "status": "allowed",
        "utilization": 0.0,
    },
    "session_id": "00000000-0000-0000-0000-000000000000",
    "type": STATE_EVENT,
    "uuid": "00000000-0000-0000-0000-000000000000",
}


def redact_reports(reports: Path, *, keep_shape: bool = True) -> list[str]:
    """Take the recording machine's own account state out of a run's step reports.

    `keep_shape` is the difference between the two trees: a committed fixture keeps one
    zeroed event so the shape survives, and a probe world keeps none.
    """
    redacted: list[str] = []
    for path in sorted(reports.glob("*.json")):
        report: Any = json.loads(path.read_text(encoding="utf-8"))
        raw: Any = report.get("detail")
        if not isinstance(raw, dict):
            continue
        detail = cast(dict[str, Any], raw)
        # On the keys being there rather than on what they hold. A step that met no limit
        # still writes `resets_at: null`, and skipping it leaves the name in a file the
        # check reads — the scrub deciding there was nothing to do, and the check calling
        # that a leak.
        if REPORT_KEY not in detail and RESET_KEY not in detail:
            continue
        detail[REPORT_KEY] = [REDACTED_RATE_LIMIT] if keep_shape else []
        # Kept as null where the shape matters and taken out entirely where it does not: a
        # world is checked by looking for these names in its bytes, and a key left behind
        # with a null beside it would fail that check for ever.
        if keep_shape:
            detail[RESET_KEY] = None
        else:
            detail.pop(RESET_KEY, None)
        path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        redacted.append(path.stem)
    return redacted


def redact_stream(path: Path) -> bool:
    """Drop every line naming the account's state from one captured provider stream.

    **The same test `named_state` applies, so the scrub and the check cannot disagree.** An
    earlier rule dropped only lines that parsed as an event, which left a line merely *saying*
    the words for the check to condemn — a sweep aborted, after the money, over prose. A
    captured stream is line-oriented and nothing in it is published, so a line that names the
    state goes whether it is an event or a session quoting one.

    Line by line rather than by parsing the file: an engine log is the provider's stream with
    the engine's own writing around it, so there is no document to load.
    """
    try:
        original = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    if not any(name in original for name in NAMED):
        return False
    lines = original.splitlines()
    kept = [line for line in lines if not any(name in line for name in NAMED)]
    if len(kept) == len(lines):
        return False
    path.write_text(
        "\n".join(kept) + ("\n" if original.endswith("\n") else ""), encoding="utf-8"
    )
    return True


def redact_world(*, reports: Path, streams: Path) -> list[str]:
    """Every place in one probe world that a paid step wrote account state, emptied.

    Two treatments over two named places rather than one walk over everything: a step report
    is a document whose field is emptied, and a captured stream is lines that go. Walking the
    whole world with the line rule would cut a line out of the middle of a pretty-printed
    report and leave JSON nobody can load.
    """
    changed = [
        str(reports / f"{stem}.json")
        for stem in (redact_reports(reports, keep_shape=False) if reports.is_dir() else [])
    ]
    if streams.is_dir():
        changed.extend(
            str(path)
            for path in sorted(streams.rglob("*"))
            if path.is_file() and redact_stream(path)
        )
    return changed


def named_state(*places: Path) -> list[str]:
    """Every file in these places still naming the account's state, which must be none.

    The check is separate from the scrub and reads the places the scrub just walked, because
    the failure this guards against is a scrub that stopped matching what the provider writes
    — which no amount of care inside the scrub can detect.

    **The same places, and no wider.** A world also holds the skill's own documentation, and
    `docs/supervision.md` names `detail.resets_at` in a sentence explaining what the field is
    for. Prose naming a field is not the account's state, and a check that walked the whole
    world would refuse every sweep over it — the scrub's scope is the two places a paid step
    writes, so the check's is too.

    **Bytes rather than text, and a file it cannot read is a failure rather than a pass.** A
    session's captured stream is one file holding whatever a real model emitted, and a
    truncated multi-byte sequence in it would otherwise make the check say "clean" about a
    file it declined to open. A check that cannot tell "nothing found" from "not looked at"
    is the theatre this exists to avoid.
    """
    found: list[str] = []
    wanted = [name.encode("utf-8") for name in NAMED]
    for place in places:
        for path in sorted(place.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            try:
                body = path.read_bytes()
            except OSError:
                found.append(str(path))
                continue
            if any(name in body for name in wanted):
                found.append(str(path))
    return found
