"""Gap elimination section 8: Neo4j.

Two layers, kept honest:

1. Always run: the Cypher query builder, the result parser, and the
   planner's handling of a graph answer (trusted only if every route in it
   is still operational in PostgreSQL; otherwise the relational engine
   answers and says why). The Neo4j *driver call* is replaced here -- these
   tests prove LifeLink's handling of Neo4j answers, NOT that Neo4j works.

2. Real Neo4j (``NEO4J_TEST_URI`` + ``NEO4J_TEST_USER`` +
   ``NEO4J_TEST_PASSWORD``): resync the projection from PostgreSQL and check
   Neo4j's paths equal the relational engine's on the same network. Skipped
   with an explicit reason when no server is available -- in the
   verification sandbox every container registry and dist.neo4j.org are
   blocked, so this layer is BLOCKED there (see
   docs/final-verification/NEO4J_SETUP.md).
"""

from __future__ import annotations

import os
import uuid
from datetime import timedelta

import pytest

from app.core.graph import GraphUnavailableError
from app.core.route_graph import Path
from app.core.time import utcnow
from app.models.enums import BloodComponent, BloodType, GraphPathwaySource, Urgency
from app.services import graph_service
from app.services import pathway_planner as planner
from tests import gap_fixtures as gf

NEO4J_URI = os.environ.get("NEO4J_TEST_URI")


@pytest.fixture
def db():
    s = gf.session()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def test_route_query_bounds_hops_and_filters_disrupted_and_excluded_routes():
    q = graph_service._route_paths_query(4)
    assert "[:ROUTE*1..4]" in q
    assert "r.status = 'OPERATIONAL'" in q and "$excluded" in q
    for bad in (0, 13, -1):
        with pytest.raises(ValueError):
            graph_service._route_paths_query(bad)


def test_parser_builds_ordered_paths_and_skips_unreachable_sources():
    dest, a, b, m = (uuid.uuid4() for _ in range(4))
    r1, r2 = uuid.uuid4(), uuid.uuid4()
    out = graph_service._parse_route_paths(
        [
            {
                "source_id": str(a),
                "best": {
                    "route_ids": [str(r1), str(r2)],
                    "node_ids": [str(a), str(m), str(dest)],
                    "km": 12.5,
                    "minutes": 30,
                },
            },
            {"source_id": str(b), "best": None},
        ],
        dest,
    )
    assert list(out) == [a]
    assert out[a].route_ids == [r1, r2] and out[a].node_ids == [a, m, dest]
    assert (out[a].distance_km, out[a].transit_minutes, out[a].hops) == (12.5, 30, 2)


def _req(dest):
    return planner.PlanRequest(
        recipient_type=BloodType.O_POS,
        component=BloodComponent.WHOLE_BLOOD,
        units_needed=1,
        urgency=Urgency.CRITICAL,
        deadline=None,
        destination_facility_id=dest.id,
        now=utcnow(),
        handling_minutes=30,
        risk_minutes=15,
    )


def _net(db):
    dest = gf.facility(db, "Dest", 12.0, 77.0)
    src = gf.facility(db, "Src", 12.1, 77.1)
    r = gf.route(db, dest, src, 10, 20)
    gf.stock(db, src, "O_POS", 1)
    return dest, src, r


def test_planner_uses_a_graph_answer_that_matches_postgres(db, monkeypatch):
    dest, src, r = _net(db)
    monkeypatch.setattr(planner, "graph_enabled", lambda: True)
    monkeypatch.setattr(
        graph_service,
        "route_paths_to",
        lambda **kw: {src.id: Path(src.id, dest.id, [src.id, dest.id], [r.id], 999.0, 999)},
    )
    result = planner.plan(db, _req(dest))
    assert result.source == GraphPathwaySource.GRAPH and result.graph_available
    opt = result.primary.legs[0].option
    # distances/times always come from PostgreSQL, never trusted from the graph
    assert (opt.distance_km, opt.transit_minutes) == (10, 20)


def test_planner_rejects_a_stale_graph_answer(db, monkeypatch):
    dest, src, r = _net(db)
    ghost = uuid.uuid4()  # a route the graph still has but PostgreSQL does not
    monkeypatch.setattr(planner, "graph_enabled", lambda: True)
    monkeypatch.setattr(
        graph_service,
        "route_paths_to",
        lambda **kw: {src.id: Path(src.id, dest.id, [src.id, dest.id], [ghost], 1.0, 1)},
    )
    result = planner.plan(db, _req(dest))
    assert result.source == GraphPathwaySource.RELATIONAL_ROUTES
    assert "out of date" in result.degraded_reason
    assert result.primary.legs[0].option.route_ids == [r.id]


def test_planner_rejects_a_stale_graph_that_misses_a_newer_better_route(db, monkeypatch):
    """Audit A8 (2026-10-31): every route the graph returns is operational,
    but the projection predates a faster route added in PostgreSQL. Checking
    only the returned routes would accept the slower path."""
    dest, src, slow = _net(db)  # 10 km / 20 min, direct
    mid = gf.facility(db, "Mid", 12.05, 77.05)
    # added later (the projection never saw them): 2 hops, 6 minutes in total
    fast_a = gf.route(db, dest, mid, 2, 3)
    fast_b = gf.route(db, mid, src, 2, 3)
    monkeypatch.setattr(planner, "graph_enabled", lambda: True)
    monkeypatch.setattr(
        graph_service,
        "route_paths_to",
        lambda **kw: {src.id: Path(src.id, dest.id, [src.id, dest.id], [slow.id], 10.0, 20)},
    )
    result = planner.plan(db, _req(dest))
    assert result.source == GraphPathwaySource.RELATIONAL_ROUTES
    assert "better or missing path" in result.degraded_reason
    assert set(result.primary.legs[0].option.route_ids) == {fast_a.id, fast_b.id}


def test_planner_rejects_a_stale_graph_that_misses_a_reachable_source(db, monkeypatch):
    dest, src, _r = _net(db)
    other = gf.facility(db, "Other", 12.2, 77.2)
    gf.route(db, dest, other, 3, 5)
    gf.stock(db, other, "O_POS", 1)
    monkeypatch.setattr(planner, "graph_enabled", lambda: True)
    monkeypatch.setattr(
        graph_service,
        "route_paths_to",
        lambda **kw: {src.id: Path(src.id, dest.id, [src.id, dest.id], [_r.id], 10.0, 20)},
    )
    result = planner.plan(db, _req(dest))
    assert result.source == GraphPathwaySource.RELATIONAL_ROUTES
    assert result.primary.legs[0].option.facility_id == other.id


def test_planner_falls_back_when_neo4j_is_unreachable(db, monkeypatch):
    dest, _src, r = _net(db)

    def boom(**kw):
        raise GraphUnavailableError("connection refused")

    monkeypatch.setattr(planner, "graph_enabled", lambda: True)
    monkeypatch.setattr(graph_service, "route_paths_to", boom)
    result = planner.plan(db, _req(dest))
    assert result.source == GraphPathwaySource.RELATIONAL_ROUTES
    assert result.degraded_reason == "connection refused"
    assert result.primary.legs[0].option.route_ids == [r.id]


@pytest.mark.skipif(
    not NEO4J_URI,
    reason="NEO4J_TEST_URI not set: no Neo4j server available "
    "(registries and dist.neo4j.org blocked in the verification sandbox)",
)
def test_real_neo4j_paths_equal_relational_paths(db, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "neo4j_uri", NEO4J_URI)
    monkeypatch.setattr(settings, "neo4j_username", os.environ.get("NEO4J_TEST_USER", "neo4j"))
    monkeypatch.setattr(settings, "neo4j_password", os.environ["NEO4J_TEST_PASSWORD"])
    dest = gf.facility(db, "Dest", 12.0, 77.0)
    hubs = [gf.facility(db, f"H{i}", 12.0 + i / 10, 77.0) for i in range(1, 5)]
    chain = [dest, *hubs]
    for a, b in zip(chain, chain[1:], strict=False):
        gf.route(db, a, b, 10, 15)
    gf.route(db, dest, hubs[-1], 50, 30)  # long direct road, faster than the chain
    gf.stock(db, hubs[-1], "O_POS", 1)
    db.commit()
    graph_service.resync_all(db)
    graph = planner.plan(db, _req(dest))
    relational = planner.plan(db, planner.PlanRequest(**{**vars(_req(dest)), "use_graph": False}))
    assert graph.source == GraphPathwaySource.GRAPH
    assert graph.primary.legs[0].option.route_ids == relational.primary.legs[0].option.route_ids
    assert timedelta(minutes=graph.primary.eta_minutes) == timedelta(
        minutes=relational.primary.eta_minutes
    )
