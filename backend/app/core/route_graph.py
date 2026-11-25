"""Hop-bounded shortest paths over the transport-route network.

Pure functions, no I/O: given the operational transport routes (undirected
edges between facilities) this computes, for every facility, the best path
to a destination facility using at most ``max_hops`` routes, and returns the
full ordered route list and node list of that path -- not just its ends.

It is used by the relational pathway engine (the authoritative path search
when Neo4j is not configured or not reachable) and by the dry-run simulator.
It is a standard algorithm, not an emulation of Neo4j: a hop-bounded
Bellman-Ford over the edge list, which gives the exact minimum-cost path
among all paths with at most ``max_hops`` edges. Neo4j's bounded
variable-length query (``app.services.graph_service``) answers the same
question; when Neo4j is live the integration test suite compares the two.

Cost: ``objective="minutes"`` minimises total transit time (used for urgent
and critical requests), ``objective="km"`` minimises total distance (routine
requests). The other metric, the hop count and the route ids break ties, so
results are deterministic.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Literal

Objective = Literal["minutes", "km"]


@dataclass(frozen=True)
class Edge:
    route_id: uuid.UUID
    a: uuid.UUID
    b: uuid.UUID
    distance_km: float
    transit_minutes: int


@dataclass
class Path:
    """A path from ``source`` to ``destination``. ``node_ids`` and
    ``route_ids`` are ordered in the direction blood travels:
    node_ids[0] == source, node_ids[-1] == destination, and
    route_ids[i] connects node_ids[i] and node_ids[i+1]."""

    source: uuid.UUID
    destination: uuid.UUID
    node_ids: list[uuid.UUID] = field(default_factory=list)
    route_ids: list[uuid.UUID] = field(default_factory=list)
    distance_km: float = 0.0
    transit_minutes: int = 0

    @property
    def hops(self) -> int:
        return len(self.route_ids)


def _cost(objective: Objective, km: float, minutes: int, hops: int, tie: str):
    if objective == "minutes":
        return (minutes, round(km, 6), hops, tie)
    return (round(km, 6), minutes, hops, tie)


def shortest_paths_to(
    edges: list[Edge],
    destination: uuid.UUID,
    *,
    max_hops: int,
    objective: Objective = "minutes",
    excluded_route_ids: frozenset[uuid.UUID] | set[uuid.UUID] = frozenset(),
) -> dict[uuid.UUID, Path]:
    """Best path from every reachable facility to ``destination``.

    The destination itself is included with an empty path (it can supply
    from its own stock with no transport). Edges in ``excluded_route_ids``
    are ignored (callers pass every DISRUPTED route plus any route being
    simulated as failed).

    Implementation: hop-level dynamic programming. ``levels[k][v]`` is the
    best label for reaching the destination from ``v`` using at most ``k``
    routes; each label points back to the exact (node, level) it extends,
    so the reconstructed path is always the one whose cost was computed.
    """
    usable = [e for e in edges if e.route_id not in excluded_route_ids]
    # label = (km, minutes, hops, tie, kind, pred_node, route_id)
    #   kind "root"  -> the destination itself
    #   kind "carry" -> same node, same label as level k-1
    #   kind "edge"  -> extends (pred_node, k-1) by route_id
    levels: list[dict[uuid.UUID, tuple]] = [{destination: (0.0, 0, 0, "", "root", None, None)}]

    def key(lab: tuple):
        return _cost(objective, lab[0], lab[1], lab[2], lab[3])

    for k in range(1, max_hops + 1):
        prev = levels[k - 1]
        cur: dict[uuid.UUID, tuple] = {
            node: (lab[0], lab[1], lab[2], lab[3], "carry", node, None)
            for node, lab in prev.items()
        }
        changed = False
        for e in usable:
            for near, far in ((e.a, e.b), (e.b, e.a)):
                if near not in prev or far == destination:
                    continue
                km, minutes, hops, tie = prev[near][:4]
                cand = (
                    km + e.distance_km,
                    minutes + e.transit_minutes,
                    hops + 1,
                    f"{tie}|{e.route_id}",
                    "edge",
                    near,
                    e.route_id,
                )
                existing = cur.get(far)
                if existing is None or key(cand) < key(existing):
                    cur[far] = cand
                    changed = True
        levels.append(cur)
        if not changed:
            break

    top = len(levels) - 1
    paths: dict[uuid.UUID, Path] = {}
    for node, lab in levels[top].items():
        path = Path(
            source=node,
            destination=destination,
            distance_km=round(lab[0], 3),
            transit_minutes=int(lab[1]),
        )
        k, cur_node = top, node
        while True:
            entry = levels[k][cur_node]
            kind = entry[4]
            if kind == "root":
                path.node_ids.append(cur_node)
                break
            if kind == "carry":
                k -= 1
                continue
            path.node_ids.append(cur_node)
            path.route_ids.append(entry[6])
            cur_node = entry[5]
            k -= 1
        paths[node] = path
    return paths
