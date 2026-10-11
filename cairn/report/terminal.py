"""The default rendering: a scrolling terminal, no colour, nothing to install.

Colour is absent rather than optional. Doc 14 asks for a rendering readable without colour
support, and one code path is the only way to be sure of that — a rendering that dropped to
plain text on a pipe would be a second rendering nobody reads before shipping. So the
weighting a colour would carry is carried by words and by position instead: the verdict is
the first line, and what makes it not a clean success is the second.

This is also the one sink where untrusted text damages the reader rather than the document.
An escape sequence in an agent's own account of itself could repaint the screen above it and
hide the exclusion the report exists to show, so every value goes through `for_terminal`.
"""

from __future__ import annotations

from collections.abc import Mapping

from cairn.report.sinks import Rendering, Scribe, for_terminal, join, one_line
from cairn.report.spine import (
    Block,
    Cell,
    Diagram,
    Document,
    Facing,
    Fields,
    Headline,
    Nothing,
    Statement,
    Table,
    Verbatim,
)
from cairn.report.width import display_width, fold, pad, terminal_width

INDENT = "  "


def render(
    document: Document, facts: Mapping[str, str], *, width: int | None = None
) -> Rendering:
    """The whole run, top to bottom, in the order the spine fixed.

    `width` is the window this is laid out against. Measured from the terminal when the
    caller does not say ([width.py]), so a narrow window folds rather than wrapping in the
    emulator — which breaks the alignment this rendering carries its weighting in.
    """
    scribe = Scribe(facts)
    columns = terminal_width(width)
    lines: list[str] = []
    for section in document.sections:
        scribe.enter(section.question.key)
        lines.append(f"== {section.question.heading.upper()} ==")
        lines.append("")
        for block in section.blocks:
            lines.extend(_block(block, scribe, columns))
    return Rendering("\n".join(lines).rstrip() + "\n", tuple(scribe.stated))


def _text(scribe: Scribe, cell: Cell) -> str:
    return str(for_terminal(scribe.shown(cell)))


def _labelled(scribe: Scribe, label: str, cell: Cell) -> str:
    """One table cell, logged against the column that names what it answers."""
    scribe.under(label)
    return _line(scribe, cell)


def _line(scribe: Scribe, cell: Cell) -> str:
    """One value on one line, for the rows this sink builds its structure out of.

    This rendering is line-delimited: a section is a heading on its own line and a field is
    a label and a value on one. So a value carrying a newline can draw a heading and a row
    of its own — a fabricated receipts section with a fabricated figure, in the one document
    whose premise is that a surface cannot invent a number. Prose keeps its shape; anything
    load-bearing for the layout does not.
    """
    return str(for_terminal(one_line(scribe.shown(cell))))


def _chrome(text: str) -> str:
    """A title or a label, escaped like everything else.

    These are Cairn's own words in every case the composition builds today — but they are
    built *from* record data (a step's id, a wave's number, an infrastructure node's name),
    and a record is a file on disk that has met no normaliser. An escape that only covers
    the values a renderer thinks are untrusted is an escape that covers whatever the last
    author remembered.
    """
    return str(for_terminal(one_line(text)))


def _block(block: Block, scribe: Scribe, columns: int) -> list[str]:
    if isinstance(block, Nothing):
        return [_chrome(block.text), ""]
    if isinstance(block, (Headline, Statement)):
        return [*_wrap(_sentence(block.text, scribe), "", columns), ""]
    if isinstance(block, Fields):
        return _fields(block, scribe, columns)
    if isinstance(block, Table):
        return _table(block, scribe, columns)
    if isinstance(block, Facing):
        return _facing(block, scribe, columns)
    if isinstance(block, Verbatim):
        return _verbatim(block, scribe)
    # No final check: exhausting the union leaves `block` narrowed to `Diagram`, so a
    # ninth block kind makes this call a type error rather than a silent mis-render.
    return _diagram(block, scribe, columns)


def _sentence(cells: tuple[Cell, ...], scribe: Scribe) -> str:
    scribe.under("")
    return join([_text(scribe, cell) for cell in cells])


def _wrap(text: str, indent: str, columns: int) -> list[str]:
    return fold(text, width=columns, indent=indent)


def _fields(block: Fields, scribe: Scribe, columns: int) -> list[str]:
    """A label and its value on one line, with a long value folded under the value column.

    Folded on whitespace only, so a path, a URL or a session id — one word with nothing to
    fold at — stays whole and overruns rather than arriving in halves a reader can neither
    paste nor search for. The continuation is indented past the label, so the label column
    being blank is what says the line is a continuation.
    """
    lines: list[str] = []
    if block.title is not None:
        lines.append(f"{_chrome(block.title)}:")
    # Padded by display cells rather than by codepoint count: a column of labels aligned by
    # `len` ragged-edges on the first one that is not Latin ([width.py]).
    width = max((display_width(_chrome(label)) for label, _ in block.rows), default=0)
    indent = INDENT + " " * (width + 2)
    budget = max(columns - display_width(indent), 1)
    for label, cell in block.rows:
        scribe.under(label)
        folded = fold(_line(scribe, cell), width=budget)
        if display_width(folded[0]) > budget:
            # One word with nothing to fold at — a path, a URL, a session id — and no room
            # for it beside the label. It gets a line of its own, so it is still one
            # selection, rather than pushing the label's line off the screen.
            lines.append(f"{INDENT}{_chrome(label)}")
            lines.extend(indent + fragment for fragment in folded)
            continue
        lines.append(f"{INDENT}{pad(_chrome(label), width)}  {folded[0]}")
        lines.extend(indent + fragment for fragment in folded[1:])
    lines.append("")
    return lines


def _table(block: Table, scribe: Scribe, columns: int) -> list[str]:
    """One row per paragraph, one field per line.

    Not a grid. A column padded to fit an agent's own summary is a column an agent chooses
    the width of, and the widest cell in this corpus is a paragraph.
    """
    lines: list[str] = []
    if block.title is not None:
        lines.append(f"{_chrome(block.title)}:")
    for row in block.rows:
        rendered = [
            _labelled(scribe, column, cell)
            for column, cell in zip(block.columns, row, strict=True)
        ]
        lines.extend(_wrap(f"{_chrome(block.columns[0])}: {rendered[0]}", INDENT, columns))
        for column, value in zip(block.columns[1:], rendered[1:], strict=True):
            lines.extend(_wrap(f"{_chrome(column)}: {value}", INDENT * 2, columns))
        lines.append("")
    return lines


def _facing(block: Facing, scribe: Scribe, columns: int) -> list[str]:
    return [
        *_wrap(
            f"{_line(scribe, block.subject)}: two accounts that do not agree.", "", columns
        ),
        *_wrap(f"{_chrome(block.left_label)}: {_text(scribe, block.left)}", INDENT, columns),
        *_wrap(f"{_chrome(block.right_label)}: {_text(scribe, block.right)}", INDENT, columns),
        "",
    ]


def _verbatim(block: Verbatim, scribe: Scribe) -> list[str]:
    """Never wrapped, never indented, never decorated: this is the text a person copies.

    The payload's own lines go out at column zero. An indent is a mutation like any other:
    a reader who selects the block gets two spaces on the front of every line of a command
    they are about to paste, or of a payload they are about to compare ([29 D]).
    """
    scribe.under(block.title or "")
    lines: list[str] = []
    if block.title is not None:
        lines.append(f"{_chrome(block.title)}:")
    lines.extend(_text(scribe, block.text).split("\n"))
    lines.append("")
    return lines


def _diagram(block: Diagram, scribe: Scribe, columns: int) -> list[str]:
    """The graph as its layers, which is what a terminal can carry of a drawing."""
    lines = [*_wrap(_sentence(block.caption, scribe), "", columns), ""]
    for layer in sorted({node.layer for node in block.nodes}):
        names = [
            f"{for_terminal(one_line(node.name))} ({for_terminal(one_line(node.status))})"
            for node in block.nodes
            if node.layer == layer
        ]
        lines.extend(_wrap(f"{layer + 1}. {', '.join(names)}", INDENT, columns))
    lines.append("")
    return lines


__all__ = ["render"]
