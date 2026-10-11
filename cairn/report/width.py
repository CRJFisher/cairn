"""How wide a string is on a terminal, and the folding and padding that rest on it.

`len` counts codepoints, and a terminal lays out **cells**. The two differ in three ways
that all reach this report: a CJK ideograph and a fullwidth form take two cells, a combining
mark and a joiner take none, and an emoji is frequently several codepoints that render as one
two-cell glyph. A column padded by codepoint count is a column that ragged-edges on the first
step whose summary is not Latin, and a line folded by codepoint count runs off the screen —
which in the one rendering with no colour is how the exclusion scrolls out of view.

The active terminal is asked for its width rather than assumed. A reading that is not
available — a pipe, a file, a harness with no terminal at all — falls back to the column
bound this report was designed against, so a piped document is stable whatever ran it.
"""

from __future__ import annotations

import shutil
import unicodedata

# The fallback, which is also what every piped and written document is laid out against.
WIDTH = 88
# Below this a label and its value cannot share a line at all, and a terminal reporting
# fewer columns than this is reporting something other than a window a person is reading.
MIN_WIDTH = 40

# Both are invisible, so they are named by codepoint rather than written literally. The
# selector turns a text-presentation codepoint into its emoji presentation, which is two
# cells wide where the bare codepoint was one.
_JOINER = "\u200d"
_EMOJI_PRESENTATION = "\ufe0f"
_WIDE = ("W", "F")
_MODIFIERS = range(0x1F3FB, 0x1F400)


def _cells(character: str) -> int:
    """How many cells one codepoint occupies on its own."""
    if unicodedata.combining(character) or unicodedata.category(character) in (
        "Mn",
        "Me",
        "Cf",
        "Cc",
    ):
        return 0
    if ord(character) in _MODIFIERS:
        # A skin-tone modifier recolours the glyph before it rather than adding one, and the
        # tables call it wide — so counting it would double every emoji carrying one.
        return 0
    return 2 if unicodedata.east_asian_width(character) in _WIDE else 1


def display_width(text: str) -> int:
    """How wide this text is on a terminal, in cells.

    A joiner sequence is counted once: `U+200D` binds the codepoint after it into the glyph
    before it, so a family emoji is one two-cell glyph rather than the four its codepoints
    would suggest.
    """
    cells = 0
    previous = 0
    joining = False
    for character in text:
        if character == _JOINER:
            joining = True
            previous = 0
            continue
        if character == _EMOJI_PRESENTATION:
            if previous == 1:
                cells += 1
                previous = 2
            continue
        width = _cells(character)
        if joining:
            joining = False
            previous = 0
            continue
        cells += width
        previous = width
    return cells


def terminal_width(declared: int | None = None) -> int:
    """The width to lay a document out against: the caller's, the terminal's, or the bound."""
    if declared is not None:
        return max(MIN_WIDTH, declared)
    return max(MIN_WIDTH, shutil.get_terminal_size(fallback=(WIDTH, 24)).columns)


def pad(text: str, cells: int) -> str:
    """`str.ljust` by display cells, so a column of labels aligns whatever is in them."""
    return text + " " * max(cells - display_width(text), 0)


def fold(text: str, *, width: int, indent: str = "") -> list[str]:
    """One paragraph onto lines no wider than `width` cells, never breaking a word.

    A word wider than the whole line is left whole and allowed to overrun: breaking it would
    cut a path, an identifier or a URL in half, and a reader can neither paste nor search for
    the halves.
    """
    budget = max(width - display_width(indent), 1)
    lines: list[str] = []
    current: list[str] = []
    occupied = 0
    for word in text.split():
        cells = display_width(word)
        if current and occupied + 1 + cells > budget:
            lines.append(indent + " ".join(current))
            current, occupied = [word], cells
            continue
        occupied += cells + (1 if current else 0)
        current.append(word)
    if current:
        lines.append(indent + " ".join(current))
    return lines or [indent + text]


__all__ = [
    "MIN_WIDTH",
    "WIDTH",
    "display_width",
    "fold",
    "pad",
    "terminal_width",
]
