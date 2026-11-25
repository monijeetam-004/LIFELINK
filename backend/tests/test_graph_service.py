"""Real, end-to-end tests of app/services/graph_service.py against genuine
Postgres data and a genuinely-unreachable Neo4j target.

These are NOT mocked: they use real SQLAlchemy rows (HospitalProfile /
HospitalInventory / TransportRoute) and drive graph_service through the
real `app.core.graph` degradation path (see tests/test_graph_engine.py for
why a deliberately-unresolvable host is used rather than depending on
whatever NEO4J_* happens to be in this environment's .env). What they
prove: the haversine fallback ranks correctly, excludes disrupted direct
routes, and every mutating sync function degrades to `False` rather than
raising or fabricating success. They do NOT prove the real Cypher in
`_PATHWAY_QUERY` / `resync_all` is syntactically valid against a live
server -- see scripts/verify_neo4j.py for that.
"""
from __future__ import annotations

import uuid

import pytest

from app.core import graph
from app.core.config import Settings
from app.db.session import SessionLocal
from app.models.enums import BloodType, FacilityType, GraphPathwaySource, TransportRouteStatus
from app.models.hospital import HospitalInventory, HospitalProfile
from app.models.transport import TransportRoute
from app.services import graph_service, inventory_service
from tests.helpers import seed_verified_user


def _stock(db, facility, blood_type: BloodType, units: int) -> None:
    """Real per-unit stock through the inventory service (the planner counts
    units, never a bare aggregate)."""
    inventory_service.reconcile_inventory(db, facility.id, blood_type, units,
                                          actor_user_id=None, note="test stock")


@pytest.fixture(autouse=True)
def _unreachable_graph(monkeypatch: pytest.MonkeyPatch):
    """Force the graph engine into a real, exercised "configured but
    unreachable" state for every test in this file, and make sure no
    driver survives between tests."""
    graph.close_driver()
    monkeypatch.setattr(
        graph,
        "settings",
        Settings(
            neo4j_uri="neo4j://lifelink-graph-does-not-exist.invalid:7687",
            neo4j_username="neo4j",
            neo4j_password="irrelevant",
            neo4j_database=None,
            neo4j_connection_timeout_seconds=3.0,
        ),
    )
    yield
    graph.close_driver()


def _make_facility(db, *, city: str, lat: float, lon: float, name: str) -> HospitalProfile:
    user_id = seed_verified_user(
        f"{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:6]}@example.com",
        role="HOSPITAL",
        full_name=name,
    )
    hp = HospitalProfile(
        user_id=user_id,
        name=name,
        city=city,
        latitude=lat,
        longitude=lon,
        verified=True,
        facility_type=FacilityType.HOSPITAL,
    )
    db.add(hp)
    db.flush()
    return hp


class TestRelationalFallbackWhenGraphUnreachable:
    def test_ranks_by_distance_and_reports_degraded(self) -> None:
        db = SessionLocal()
        try:
            origin = _make_facility(db, city="Mumbai", lat=19.0760, lon=72.8777, name="Origin Hosp")
            near = _make_facility(db, city="Thane", lat=19.2183, lon=72.9781, name="Near Hosp")
            far = _make_facility(db, city="Pune", lat=18.5204, lon=73.8567, name="Far Hosp")
            for facility in (near, far):
                _stock(db, facility, BloodType.O_NEG, 5)
            db.commit()

            result = graph_service.find_resource_pathways(
                db, blood_type=BloodType.O_NEG, origin_facility_id=origin.id
            )

            assert result.source == GraphPathwaySource.RELATIONAL_ROUTES
            assert result.graph_available is False
            assert result.degraded_reason is not None  # the real Neo4j connection error
            assert [c.facility_id for c in result.candidates] == [near.id, far.id]
            assert {c.route_basis for c in result.candidates} == {"STRAIGHT_LINE_ESTIMATE"}
            assert result.candidates[0].distance_km < result.candidates[1].distance_km
        finally:
            db.close()

    def test_excludes_facility_with_no_stock(self) -> None:
        db = SessionLocal()
        try:
            origin = _make_facility(db, city="Delhi", lat=28.7041, lon=77.1025, name="Origin D")
            stocked = _make_facility(db, city="Noida", lat=28.5355, lon=77.3910, name="Stocked D")
            unstocked = _make_facility(db, city="Gurgaon", lat=28.4595, lon=77.0266, name="Empty D")
            _stock(db, stocked, BloodType.AB_NEG, 2)
            db.add(HospitalInventory(
                hospital_id=unstocked.id, blood_type=BloodType.AB_NEG, units=0
            ))
            db.commit()

            result = graph_service.find_resource_pathways(
                db, blood_type=BloodType.AB_NEG, origin_facility_id=origin.id
            )

            ids = {c.facility_id for c in result.candidates}
            assert stocked.id in ids
            assert unstocked.id not in ids
        finally:
            db.close()

    def test_excludes_disrupted_direct_route(self) -> None:
        db = SessionLocal()
        try:
            origin = _make_facility(db, city="Chennai", lat=13.0827, lon=80.2707, name="Origin C")
            dest = _make_facility(db, city="Vellore", lat=12.9165, lon=79.1325, name="Dest C")
            _stock(db, dest, BloodType.B_POS, 3)
            db.add(TransportRoute(
                origin_facility_id=origin.id,
                destination_facility_id=dest.id,
                distance_km=140.0,
                transit_minutes=180,
                status=TransportRouteStatus.DISRUPTED,
                disrupted_reason="flooded highway",
            ))
            db.commit()

            result = graph_service.find_resource_pathways(
                db, blood_type=BloodType.B_POS, origin_facility_id=origin.id
            )

            assert dest.id not in {c.facility_id for c in result.candidates}
            assert result.disrupted_routes_excluded == 1
        finally:
            db.close()

    def test_no_candidates_returns_empty_not_an_error(self) -> None:
        db = SessionLocal()
        try:
            origin = _make_facility(db, city="Kolkata", lat=22.5726, lon=88.3639, name="Lonely")
            result = graph_service.find_resource_pathways(
                db, blood_type=BloodType.AB_POS, origin_facility_id=origin.id
            )
            assert result.candidates == []
            assert result.source == GraphPathwaySource.RELATIONAL_ROUTES
        finally:
            db.close()


class TestBestEffortSync:
    """Every incremental sync function must degrade to False, never raise,
    when Neo4j is unreachable -- this is what lets a real facility/
    inventory/route mutation in Postgres succeed even when the graph
    mirror can't be updated."""

    def test_sync_facility_returns_false(self) -> None:
        db = SessionLocal()
        try:
            hp = _make_facility(db, city="Jaipur", lat=26.9124, lon=75.7873, name="Sync Test")
            db.commit()
            assert graph_service.sync_facility(hp) is False
        finally:
            db.close()

    def test_sync_inventory_returns_false(self) -> None:
        db = SessionLocal()
        try:
            hp = _make_facility(db, city="Lucknow", lat=26.8467, lon=80.9462, name="Sync Inv")
            db.commit()
            assert (
                graph_service.sync_inventory(hp.id, BloodType.O_POS, 4) is False
            )
        finally:
            db.close()

    def test_sync_route_returns_false(self) -> None:
        db = SessionLocal()
        try:
            a = _make_facility(db, city="Agra", lat=27.1767, lon=78.0081, name="Route A")
            b = _make_facility(db, city="Mathura", lat=27.4924, lon=77.6737, name="Route B")
            route = TransportRoute(
                origin_facility_id=a.id, destination_facility_id=b.id,
                distance_km=58.0, transit_minutes=70,
            )
            db.add(route)
            db.commit()
            assert graph_service.sync_route(route) is False
        finally:
            db.close()


class TestResyncAllRaisesHonestly:
    """resync_all() is the one sync path that must NOT swallow a failure --
    it's the admin-triggered 'make the graph correct again' operation."""

    def test_resync_all_raises_graph_unavailable(self) -> None:
        db = SessionLocal()
        try:
            with pytest.raises(graph.GraphUnavailableError):
                graph_service.resync_all(db)
        finally:
            db.close()


class TestDiffPathwayRankings:
    """Pure function -- no I/O, no Neo4j involved."""

    def test_detects_distance_increase(self) -> None:
        before = graph_service.PathwayResult(
            source=GraphPathwaySource.GRAPH,
            candidates=[
                graph_service.PathwayCandidate(
                    facility_id=uuid.uuid4(), facility_name="A", city="X",
                    units=1, distance_km=10.0,
                )
            ],
        )
        after = graph_service.PathwayResult(
            source=GraphPathwaySource.GRAPH,
            candidates=[
                graph_service.PathwayCandidate(
                    facility_id=uuid.uuid4(), facility_name="B", city="Y",
                    units=1, distance_km=25.0,
                )
            ],
        )
        diff = graph_service.diff_pathway_rankings(before, after)
        assert diff["distance_increase_km"] == 15.0
        assert diff["became_unreachable"] is False

    def test_detects_became_unreachable(self) -> None:
        before = graph_service.PathwayResult(
            source=GraphPathwaySource.GRAPH,
            candidates=[
                graph_service.PathwayCandidate(
                    facility_id=uuid.uuid4(), facility_name="A", city="X",
                    units=1, distance_km=10.0,
                )
            ],
        )
        after = graph_service.PathwayResult(source=GraphPathwaySource.GRAPH, candidates=[])
        diff = graph_service.diff_pathway_rankings(before, after)
        assert diff["became_unreachable"] is True
        assert diff["distance_increase_km"] is None
