"""The run's graph, laid out from what the record recorded and from nothing else.

This is not the topology derivation over again. That one turns a plan into a runnable shape
and refuses a graph it cannot run; this one draws what an engine *did* run, so it must be
total. Every node reaches the layout, including one whose name the grammar does not cover and
one an edge names but the node list does not — a node dropped for being unrecognisable is a
node whose failure nothing draws, which is the same rule doc 12 applies to the record itself.

It therefore refuses nothing and hangs on nothing. A cycle cannot come out of the engine, but
a hand-edited record can carry one, and a layout that looped on it would cost a reader the
whole report rather than one picture.
"""

from __future__ import annotations

from cairn.record.model import Edge, EngineNode
from cairn.report.spine import GraphEdge, GraphNode


def layout(
    nodes: list[EngineNode], edges: list[Edge]
) -> tuple[tuple[GraphNode, ...], tuple[GraphEdge, ...]]:
    """Every node placed in a layer, and every edge kept, with the ones that go back marked.

    A node sits one layer below its deepest upstream. The walk is bounded by the node count,
    so an edge that points backwards settles instead of circling: it is drawn, marked as
    going back, and the picture stays a picture.
    """
    placed = {node["name"]: 0 for node in nodes}
    upstreams: dict[str, list[str]] = {name: [] for name in placed}
    for edge in edges:
        if edge["downstream"] in upstreams and edge["upstream"] in placed:
            upstreams[edge["downstream"]].append(edge["upstream"])

    for _ in range(len(placed)):
        moved = False
        for name, sources in upstreams.items():
            deepest = max((placed[source] for source in sources), default=-1)
            if deepest + 1 > placed[name]:
                placed[name] = deepest + 1
                moved = True
        if not moved:
            break

    # The relaxation settles a cycle by pushing its members down once per pass, so a graph
    # that carries one comes out with its layers scattered far below zero's neighbourhood —
    # a nine-node drawing with twenty empty rows through the middle of it. Compacting to the
    # layers actually occupied costs nothing and keeps the picture a picture.
    occupied = {layer: rank for rank, layer in enumerate(sorted(set(placed.values())))}
    placed = {name: occupied[layer] for name, layer in placed.items()}

    columns: dict[int, int] = {}
    drawn: list[GraphNode] = []
    for node in nodes:
        layer = placed[node["name"]]
        column = columns.get(layer, 0)
        columns[layer] = column + 1
        drawn.append(
            GraphNode(
                name=node["name"],
                status=node["status_name"],
                layer=layer,
                column=column,
            )
        )
    return tuple(drawn), tuple(
        GraphEdge(
            upstream=edge["upstream"],
            downstream=edge["downstream"],
            back=(
                edge["upstream"] in placed
                and edge["downstream"] in placed
                and placed[edge["upstream"]] >= placed[edge["downstream"]]
            ),
        )
        for edge in edges
    )


__all__ = ["layout"]
