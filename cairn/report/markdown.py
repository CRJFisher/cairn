"""The durable rendering: a document that reads in a repository and in a pull request.

Markdown's escaping is not one rule but three, because the sink is three contexts wearing one
name. A pipe ends a table cell and a line break ends the whole table. A `#` at the start of a
line is a heading, so a summary that begins with one forges structure. And a pull request
renders this as HTML, which means a tag inside an agent's own account of itself is live
markup — so prose is escaped for HTML here too, and no value the record supplies is ever
spelled as a link.

The one thing deliberately left unescaped is a verbatim span, whose fence is instead made
longer than any backtick run inside it. A resume command that reached a reader with an
`&amp;&amp;` in it is a receipt that fails at the moment it is used.
"""

from __future__ import annotations

from collections.abc import Mapping

from cairn.report.sinks import (
    Rendering,
    Scribe,
    for_markdown,
    for_markdown_cell,
    for_markdown_code,
    join,
    one_line,
)
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

# Every section carries one, so a reader — or a test — can find where each answer begins
# without matching on a heading a copy edit could move.
MARKER = "<!-- cairn:section:{key} -->"


def render(document: Document, facts: Mapping[str, str]) -> Rendering:
    scribe = Scribe(facts)
    lines: list[str] = [f"# Run {for_markdown(one_line(document.run_id))}", ""]
    for section in document.sections:
        scribe.enter(section.question.key)
        lines.append(MARKER.format(key=section.question.key))
        lines.append(f"## {section.question.heading}")
        lines.append("")
        for block in section.blocks:
            lines.extend(_block(block, scribe))
    return Rendering("\n".join(lines).rstrip() + "\n", tuple(scribe.stated))


def _prose(scribe: Scribe, cell: Cell) -> str:
    return str(for_markdown(scribe.shown(cell)))


def _labelled(scribe: Scribe, label: str, cell: Cell) -> str:
    """One table cell, logged against the column that names what it answers."""
    scribe.under(label)
    return _cell(scribe, cell)


def _cell(scribe: Scribe, cell: Cell) -> str:
    return str(for_markdown_cell(scribe.shown(cell)))


def _block(block: Block, scribe: Scribe) -> list[str]:
    if isinstance(block, Nothing):
        return [for_markdown(one_line(block.text)), ""]
    if isinstance(block, Headline):
        return [f"**{_sentence(block.text, scribe)}**", ""]
    if isinstance(block, Statement):
        return [_sentence(block.text, scribe), ""]
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
    return join([_prose(scribe, cell) for cell in cells])


def _fields(block: Fields, scribe: Scribe) -> list[str]:
    lines: list[str] = []
    if block.title is not None:
        lines.append(f"### {for_markdown(one_line(block.title))}")
        lines.append("")
    lines.append("| | |")
    lines.append("| --- | --- |")
    for label, cell in block.rows:
        scribe.under(label)
        lines.append(f"| {for_markdown_cell(one_line(label))} | {_cell(scribe, cell)} |")
    lines.append("")
    return lines


def _table(block: Table, scribe: Scribe) -> list[str]:
    lines: list[str] = []
    if block.title is not None:
        lines.append(f"### {for_markdown(one_line(block.title))}")
        lines.append("")
    lines.append("| " + " | ".join(block.columns) + " |")
    lines.append("| " + " | ".join("---" for _ in block.columns) + " |")
    for row in block.rows:
        lines.append(
            "| "
            + " | ".join(
                _labelled(scribe, column, cell)
                for column, cell in zip(block.columns, row, strict=True)
            )
            + " |"
        )
    lines.append("")
    return lines


def _facing(block: Facing, scribe: Scribe) -> list[str]:
    """Two columns of one row, so neither account can be read as the heading of the other."""
    return [
        f"**{_prose(scribe, block.subject)}** — two accounts that do not agree.",
        "",
        f"| {block.left_label} | {block.right_label} |",
        "| --- | --- |",
        f"| {_cell(scribe, block.left)} | {_cell(scribe, block.right)} |",
        "",
    ]


def _verbatim(block: Verbatim, scribe: Scribe) -> list[str]:
    scribe.under(block.title or "")
    lines: list[str] = []
    if block.title is not None:
        lines.append(f"{for_markdown(one_line(block.title))}:")
        lines.append("")
    lines.append(str(for_markdown_code(scribe.shown(block.text))))
    lines.append("")
    return lines


def _diagram(block: Diagram, scribe: Scribe) -> list[str]:
    lines = [_sentence(block.caption, scribe), ""]
    for layer in sorted({node.layer for node in block.nodes}):
        names = ", ".join(
            f"{for_markdown(one_line(node.name))} ({for_markdown(one_line(node.status))})"
            for node in block.nodes
            if node.layer == layer
        )
        lines.append(f"{layer + 1}. {names}")
    lines.append("")
    return lines


__all__ = ["MARKER", "render"]
