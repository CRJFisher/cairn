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

import textwrap
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

WIDTH = 88
INDENT = "  "


def render(document: Document, facts: Mapping[str, str]) -> Rendering:
    """The whole run, top to bottom, in the order the spine fixed."""
    scribe = Scribe(facts)
    lines: list[str] = []
    for section in document.sections:
        scribe.enter(section.question.key)
        lines.append(f"== {section.question.heading.upper()} ==")
        lines.append("")
        for block in section.blocks:
            lines.extend(_block(block, scribe))
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
    of its own — a fabricated receipts section with a fabricated cost, in the one document
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


def _block(block: Block, scribe: Scribe) -> list[str]:
    if isinstance(block, Nothing):
        return [_chrome(block.text), ""]
    if isinstance(block, (Headline, Statement)):
        return [*_wrap(_sentence(block.text, scribe), ""), ""]
    if isinstance(block, Fields):
        return _fields(block, scribe)
    if isinstance(block, Table):
        return _table(block, scribe)
    if isinstance(block, Facing):
        return _facing(block, scribe)
    if isinstance(block, Verbatim):
        return _verbatim(block, scribe)
    # No final check: exhausting the union leaves `block` narrowed to `Diagram`, so a
    # ninth block kind makes this call a type error rather than a silent mis-render.
    return _diagram(block, scribe)


def _sentence(cells: tuple[Cell, ...], scribe: Scribe) -> str:
    scribe.under("")
    return join([_text(scribe, cell) for cell in cells])


def _wrap(text: str, indent: str) -> list[str]:
    return textwrap.wrap(
        text,
        width=WIDTH,
        initial_indent=indent,
        subsequent_indent=indent,
        break_long_words=False,
        break_on_hyphens=False,
    ) or [indent + text]


def _fields(block: Fields, scribe: Scribe) -> list[str]:
    lines: list[str] = []
    if block.title is not None:
        lines.append(f"{_chrome(block.title)}:")
    width = max((len(label) for label, _ in block.rows), default=0)
    for label, cell in block.rows:
        scribe.under(label)
        lines.append(f"{INDENT}{_chrome(label).ljust(width)}  {_line(scribe, cell)}")
    lines.append("")
    return lines


def _table(block: Table, scribe: Scribe) -> list[str]:
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
        lines.append(f"{INDENT}{_chrome(block.columns[0])}: {rendered[0]}")
        for column, value in zip(block.columns[1:], rendered[1:], strict=True):
            lines.extend(_wrap(f"{_chrome(column)}: {value}", INDENT * 2))
        lines.append("")
    return lines


def _facing(block: Facing, scribe: Scribe) -> list[str]:
    return [
        f"{_line(scribe, block.subject)}: two accounts that do not agree.",
        *_wrap(f"{_chrome(block.left_label)}: {_text(scribe, block.left)}", INDENT),
        *_wrap(f"{_chrome(block.right_label)}: {_text(scribe, block.right)}", INDENT),
        "",
    ]


def _verbatim(block: Verbatim, scribe: Scribe) -> list[str]:
    scribe.under(block.title or "")
    """Never wrapped, never decorated: this is the text a person copies."""
    lines: list[str] = []
    if block.title is not None:
        lines.append(f"{_chrome(block.title)}:")
    lines.extend(f"{INDENT}{line}" for line in _text(scribe, block.text).split("\n"))
    lines.append("")
    return lines


def _diagram(block: Diagram, scribe: Scribe) -> list[str]:
    """The graph as its layers, which is what a terminal can carry of a drawing."""
    lines = [*_wrap(_sentence(block.caption, scribe), ""), ""]
    for layer in sorted({node.layer for node in block.nodes}):
        names = [
            f"{for_terminal(one_line(node.name))} ({for_terminal(one_line(node.status))})"
            for node in block.nodes
            if node.layer == layer
        ]
        lines.extend(_wrap(f"{layer + 1}. {', '.join(names)}", INDENT))
    lines.append("")
    return lines


__all__ = ["render"]
