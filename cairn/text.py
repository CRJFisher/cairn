"""One normalisation pass for text Cairn did not write, and the numbers beside it.

Agent output and repository content are both untrusted input to a renderer. The policy is
one pass here — string coercion, control-character stripping, a length cap — and then
context-specific escaping at each final sink, never the other way round. A pre-escaped
string is never stored or reused across contexts: an escape is a property of where text is
going rather than of what it is, and text escaped for one sink is wrong in the next.

So what these functions produce is **unescaped**. `<script>` survives here as itself and is
escaped by whichever surface renders it ([14]).

The numeric admissions belong with the text for one reason: they are the same job. A cost
of `NaN` serialises as a bare `NaN`, which is not JSON, so one provider's odd answer would
make the whole record unreadable by every reader downstream.
"""

from __future__ import annotations

import math
import unicodedata
from typing import cast

# A summary is one line by contract, and 200 is what the committed marker has always held.
LINE_LIMIT = 200
# Prose that is allowed its own shape: a step's task, an agent's account of follow-up work.
TEXT_LIMIT = 2000
# A list an agent controls the length of. Fifty follow-ups is already a report nobody reads.
LIST_LIMIT = 50

ELLIPSIS = "…"

# Kept because a step's task is meant to have them. Everything else in `Cc` goes, along
# with every `Cf` — which is where the bidi overrides live, and a path that renders as its
# own opposite is a lie the reader cannot see.
KEPT_CONTROLS = "\n\t"

# They end a statement in a script context, split `str.splitlines`, and are invisible
# everywhere else — so they are named by codepoint here rather than written literally.
LINE_SEPARATORS = "\u2028\u2029"


def _unwritable(character: str) -> bool:
    """A codepoint no sink can carry, whatever it means.

    A lone surrogate is not text: it survives JSON, and then `str.encode` refuses it — so one
    hostile node name would cost a run its whole report, in every format at once, as an
    uncaught error rather than a rendering. A noncharacter is legal to encode and illegal in
    XML, which costs the drawn graph instead. Neither can be displayed by anything, so both
    go here rather than being handled by each surface that would choke on them.

    Only the noncharacters are taken, never the whole unassigned category: a codepoint this
    Python's tables do not know yet is a character from a newer Unicode, and dropping those
    would quietly delete the future.
    """
    if unicodedata.category(character) == "Cs":
        return True
    point = ord(character)
    return point & 0xFFFE == 0xFFFE or 0xFDD0 <= point <= 0xFDEF


def _strip(value: str, *, keep: str) -> str:
    return "".join(
        character
        for character in value
        if character in keep
        or (
            unicodedata.category(character) not in ("Cc", "Cf")
            and character not in LINE_SEPARATORS
            and not _unwritable(character)
        )
    )


def _cap(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + ELLIPSIS


def flatten(value: object, *, limit: int = LINE_LIMIT) -> str:
    """One line, whatever arrived: coerced, stripped, collapsed and capped.

    The newline and the tab survive the strip so that `split` can collapse them into the
    single spaces they stand for. Taking them out first would run two words together, which
    is a quieter corruption than leaving them in.
    """
    return _cap(" ".join(_strip(str(value), keep=KEPT_CONTROLS).split()), limit)


def normalise(value: object, *, limit: int = TEXT_LIMIT) -> str:
    """Text that keeps its own shape, with everything unprintable taken out of it."""
    return _cap(_strip(str(value), keep=KEPT_CONTROLS).strip(), limit)


def normalise_all(values: object, *, limit: int = LIST_LIMIT) -> list[str]:
    """A list an agent controls, bounded in both directions."""
    if not isinstance(values, list):
        return []
    items = cast(list[object], values)
    return [normalise(value) for value in items[:limit]]


def as_money(value: object) -> float | None:
    """A cost, or nothing. A figure that is not a finite number is not a figure.

    `NaN` and `inf` are the sharp cases: both are legal Python floats and neither is legal
    JSON, so admitting one would cost every reader the whole record rather than one field.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return float(value)


def as_count(value: object) -> int | None:
    """A count, or nothing."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


__all__ = [
    "ELLIPSIS",
    "KEPT_CONTROLS",
    "LINE_LIMIT",
    "LIST_LIMIT",
    "TEXT_LIMIT",
    "as_count",
    "as_money",
    "flatten",
    "normalise",
    "normalise_all",
]
