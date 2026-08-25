"""The self-contained rendering, with the run's graph drawn into it.

Self-contained means one file that needs a browser and nothing else: no script, no
stylesheet, no font, no image, no network. That is both the offline requirement and the
cheapest security posture available — a page with no script has no place for one to appear,
which makes any `<script` in the output unambiguously a breakout rather than a feature.

The graph is inline SVG, which is a second escaping context inside the first rather than a
sink of its own. Its labels are node names the record carries verbatim, so `</text>` in one
would close the element and everything after it would be markup. They go through the same
escape as every other character node, and the whole drawing is required to parse as XML.

The one URL a page may carry is the engine's own view of the run, and it is filtered by
scheme: the base comes from an environment a person edits, and `javascript:` reaching an
`href` here would be the entire injection in one field.
"""

from __future__ import annotations

from collections.abc import Mapping

from cairn.report.sinks import Rendering, Scribe, for_html, for_url, join, one_line
from cairn.report.spine import (
    RULE_LINK,
    Block,
    Cell,
    Diagram,
    Document,
    Facing,
    Fact,
    Fields,
    GraphEdge,
    GraphNode,
    Headline,
    Nothing,
    Statement,
    Table,
    Verbatim,
)

BOX_WIDTH = 190
BOX_HEIGHT = 40
GAP_X = 30
GAP_Y = 34
MARGIN = 20

STYLE = """
:root { color-scheme: light dark; --ink: #1a1a1a; --paper: #ffffff; --line: #d0d0d0;
  --quiet: #5c5c5c; --alarm: #8a1c1c; --alarm-bg: #fbeaea; --caution: #7a5200;
  --caution-bg: #fdf3dd; }
@media (prefers-color-scheme: dark) {
  :root { --ink: #e8e8e8; --paper: #16181a; --line: #3a3f44; --quiet: #a8b0b8;
    --alarm: #ff9d9d; --alarm-bg: #3a1d1d; --caution: #f0cf7a; --caution-bg: #38300f; } }
body { background: var(--paper); color: var(--ink); margin: 0 auto; padding: 2rem 1.25rem;
  max-width: 60rem; line-height: 1.55;
  font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Helvetica, Arial,
    sans-serif; }
h1 { font-size: 1.5rem; } h2 { font-size: 1.2rem; margin-top: 2.5rem;
  border-bottom: 1px solid var(--line); padding-bottom: .3rem; }
h3 { font-size: 1rem; color: var(--quiet); margin-bottom: .4rem; }
p.headline { font-size: 1.25rem; font-weight: 600; margin: .5rem 0; padding: .75rem 1rem;
  border-radius: .4rem; border: 1px solid var(--line); }
p.headline.alarm { color: var(--alarm); background: var(--alarm-bg);
  border-color: var(--alarm); }
p.headline.caution { color: var(--caution); background: var(--caution-bg);
  border-color: var(--caution); }
table { border-collapse: collapse; width: 100%; margin: .5rem 0 1.25rem; display: block;
  overflow-x: auto; }
th, td { border: 1px solid var(--line); padding: .4rem .6rem; text-align: left;
  vertical-align: top; font-size: .92rem; }
th { color: var(--quiet); font-weight: 600; }
td.label { color: var(--quiet); white-space: nowrap; width: 1%; }
pre { background: color-mix(in srgb, var(--ink) 6%, transparent); border: 1px solid
  var(--line); border-radius: .3rem; padding: .6rem .8rem; overflow-x: auto;
  font-size: .88rem; }
figure { margin: 1rem 0; overflow-x: auto; }
figcaption { color: var(--quiet); font-size: .9rem; margin-bottom: .5rem; }
svg { max-width: 100%; height: auto; }
svg text { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px;
  fill: var(--ink); }
svg rect { fill: var(--paper); stroke: var(--quiet); }
svg rect.failed, svg rect.aborted { stroke: var(--alarm); stroke-width: 2; }
svg rect.skipped, svg rect.running { stroke: var(--caution); stroke-width: 2; }
svg path { stroke: var(--quiet); fill: none; }
svg path.back { stroke-dasharray: 4 3; }
"""


def render(document: Document, facts: Mapping[str, str]) -> Rendering:
    scribe = Scribe(facts)
    run = for_html(one_line(document.run_id))
    parts: list[str] = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>Cairn run {run}</title>",
        f"<style>{STYLE}</style>",
        "</head>",
        "<body>",
        f"<h1>Run {run}</h1>",
    ]
    for section in document.sections:
        scribe.enter(section.question.key)
        parts.append(f'<section id="cairn-{section.question.key}">')
        parts.append(f"<h2>{for_html(one_line(section.question.heading))}</h2>")
        for block in section.blocks:
            parts.extend(_block(block, scribe))
        parts.append("</section>")
    parts.extend(["</body>", "</html>", ""])
    return Rendering("\n".join(parts), tuple(scribe.stated))


def _text(scribe: Scribe, cell: Cell) -> str:
    return str(for_html(scribe.shown(cell)))


def _block(block: Block, scribe: Scribe) -> list[str]:
    if isinstance(block, Nothing):
        return [f"<p>{for_html(one_line(block.text))}</p>"]
    if isinstance(block, Headline):
        return [f'<p class="headline {block.tone}">{_sentence(block.text, scribe)}</p>']
    if isinstance(block, Statement):
        return [f"<p>{_sentence(block.text, scribe)}</p>"]
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


def _labelled(scribe: Scribe, label: str, cell: Cell) -> str:
    """One table cell, logged against the column that names what it answers."""
    scribe.under(label)
    return _text(scribe, cell)


def _value(scribe: Scribe, cell: Cell) -> str:
    """A field's value, and the one place a value may become a link.

    Two conditions, not one. The composition has to have declared this fact linkable, and
    the value has to carry a scheme a document may follow. Linking on shape alone would let
    any record string that happens to begin `http` put an outbound link into a page whose
    whole contract is that it needs no network — and a repository path, a transcript
    location and a plan's name are all record strings.
    """
    shown = scribe.shown(cell)
    linkable = isinstance(cell, Fact) and cell.rule == RULE_LINK
    link = for_url(shown) if linkable else None
    if link is None:
        return str(for_html(shown))
    return f'<a href="{link}">{for_html(shown)}</a>'


def _fields(block: Fields, scribe: Scribe) -> list[str]:
    parts: list[str] = []
    if block.title is not None:
        parts.append(f"<h3>{for_html(one_line(block.title))}</h3>")
    parts.append("<table>")
    for label, cell in block.rows:
        scribe.under(label)
        parts.append(
            f'<tr><td class="label">{for_html(one_line(label))}</td>'
            f"<td>{_value(scribe, cell)}</td></tr>"
        )
    parts.append("</table>")
    return parts


def _table(block: Table, scribe: Scribe) -> list[str]:
    parts: list[str] = []
    if block.title is not None:
        parts.append(f"<h3>{for_html(one_line(block.title))}</h3>")
    parts.append("<table>")
    parts.append(
        "<tr>"
        + "".join(f"<th>{for_html(one_line(column))}</th>" for column in block.columns)
        + "</tr>"
    )
    for row in block.rows:
        parts.append(
            "<tr>"
            + "".join(
                f"<td>{_labelled(scribe, column, cell)}</td>"
                for column, cell in zip(block.columns, row, strict=True)
            )
            + "</tr>"
        )
    parts.append("</table>")
    return parts


def _facing(block: Facing, scribe: Scribe) -> list[str]:
    return [
        (
            f"<p><strong>{_text(scribe, block.subject)}</strong> — two accounts that do "
            "not agree.</p>"
        ),
        "<table>",
        (
            f"<tr><th>{for_html(one_line(block.left_label))}</th>"
            f"<th>{for_html(one_line(block.right_label))}</th></tr>"
        ),
        (
            f"<tr><td>{_text(scribe, block.left)}</td>"
            f"<td>{_text(scribe, block.right)}</td></tr>"
        ),
        "</table>",
    ]


def _verbatim(block: Verbatim, scribe: Scribe) -> list[str]:
    scribe.under(block.title or "")
    parts: list[str] = []
    if block.title is not None:
        parts.append(f"<h3>{for_html(one_line(block.title))}</h3>")
    parts.append(f"<pre><code>{_text(scribe, block.text)}</code></pre>")
    return parts


def _diagram(block: Diagram, scribe: Scribe) -> list[str]:
    return [
        "<figure>",
        f"<figcaption>{_sentence(block.caption, scribe)}</figcaption>",
        *_svg(block.nodes, block.edges),
        "</figure>",
    ]


def _position(node: GraphNode) -> tuple[int, int]:
    return (
        MARGIN + node.column * (BOX_WIDTH + GAP_X),
        MARGIN + node.layer * (BOX_HEIGHT + GAP_Y),
    )


def _svg(nodes: tuple[GraphNode, ...], edges: tuple[GraphEdge, ...]) -> list[str]:
    """The graph, drawn with no asset and no script — geometry and character data only."""
    if not nodes:
        return []
    placed = {node.name: _position(node) for node in nodes}
    width = max(x for x, _ in placed.values()) + BOX_WIDTH + MARGIN
    height = max(y for _, y in placed.values()) + BOX_HEIGHT + MARGIN
    parts = [
        (
            f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
            'xmlns="http://www.w3.org/2000/svg" role="img">'
        )
    ]
    for edge in edges:
        start = placed.get(edge.upstream)
        end = placed.get(edge.downstream)
        if start is None or end is None:
            continue
        x1, y1 = start[0] + BOX_WIDTH // 2, start[1] + BOX_HEIGHT
        x2, y2 = end[0] + BOX_WIDTH // 2, end[1]
        middle = (y1 + y2) // 2
        classes = "back" if edge.back else ""
        parts.append(
            f'<path class="{classes}" d="M {x1} {y1} L {x1} {middle} L {x2} {middle} '
            f'L {x2} {y2}"/>'
        )
    for node in nodes:
        x, y = placed[node.name]
        label = for_html(one_line(node.name))
        status = for_html(one_line(node.status))
        parts.append(
            f'<rect class="{status}" x="{x}" y="{y}" width="{BOX_WIDTH}" '
            f'height="{BOX_HEIGHT}" rx="4"/>'
        )
        parts.append(f'<text x="{x + 8}" y="{y + 17}">{label}</text>')
        parts.append(f'<text x="{x + 8}" y="{y + 31}">{status}</text>')
    parts.append("</svg>")
    return parts


__all__ = ["STYLE", "render"]
