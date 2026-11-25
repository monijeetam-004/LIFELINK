"""Isolated unit tests of graph_service's query-building and result-parsing
logic, using a minimal fake Neo4j session stand-in instead of a live server.

IMPORTANT LIMITATION (stated plainly, not glossed over): these tests prove
that this codebase builds the Cypher text and parameters it intends to, and
that result-parsing/ranking logic is correct given a result shape shaped
like Neo4j's. They do NOT prove the Cypher is syntactically valid or
behaves as intended against a real Neo4j server -- this sandbox has no
network path to one (see the phase report). scripts/verify_neo4j.py is
provided for a human to run in an environment with real Neo4j connectivity
to close that gap.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager

from app.models.enums import BloodType, FacilityType, TransportRouteStatus
from app.models.hospital import HospitalProfile
from app.models.transport import TransportRoute
from app.services import graph_service


class FakeRecord(dict):
    def data(self) -> dict:
        return dict(self)


class FakeResult:
    def __init__(self, records: list[dict]) -> None:
        self._records = [FakeRecord(r) for r in records]

    def __iter__(self):
        return iter(self._records)

    def single(self):
        return self._records[0] if self._records else None


class FakeSession:
    """Records every `.run(query, **params)` call; returns a canned
    FakeResult when `query` contains one of the registered markers, else an
    empty result."""

    def __init__(self) -> None:
        self.queries: list[tuple[str, dict]] = []
        self.canned: dict[str, list[dict]] = {}

    def run(self, query: str, **params):
        self.queries.append((query, params))
        for marker, records in self.canned.items():
            if marker in query:
                return FakeResult(records)
        return FakeResult([])

    def close(self) -> None:
        pass


@contextmanager
def _fake_graph_session(session: FakeSession):
    yield session


class TestParseRoutePaths:
    """Pure function -- no fake driver needed. Since the gap-elimination
    round Neo4j returns the FULL path per source (ordered route ids and node
    ids), not just a distance per facility."""

    def test_full_path_is_parsed_in_order(self) -> None:
        dest, src, mid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        r1, r2 = uuid.uuid4(), uuid.uuid4()
        paths = graph_service._parse_route_paths([{
            "source_id": str(src),
            "best": {"route_ids": [str(r1), str(r2)], "node_ids": [str(src), str(mid), str(dest)],
                     "km": 12.345, "minutes": 20},
        }], dest)
        p = paths[src]
        assert p.route_ids == [r1, r2] and p.node_ids == [src, mid, dest]
        assert p.distance_km == 12.345 and p.transit_minutes == 20 and p.hops == 2

    def test_unreachable_source_is_absent_not_zero(self) -> None:
        dest, src = uuid.uuid4(), uuid.uuid4()
        assert graph_service._parse_route_paths([{"source_id": str(src), "best": None}], dest) == {}

    def test_every_source_is_kept(self) -> None:
        dest = uuid.uuid4()
        a, b = uuid.uuid4(), uuid.uuid4()
        rec = lambda s, km: {"source_id": str(s), "best": {  # noqa: E731
            "route_ids": [str(uuid.uuid4())], "node_ids": [str(s), str(dest)],
            "km": km, "minutes": 1}}
        paths = graph_service._parse_route_paths([rec(a, 5.0), rec(b, 1.0)], dest)
        assert set(paths) == {a, b}


class TestCypherIssuance:
    """Fake-driver tests -- see module docstring for what they do and do
    not prove."""

    def test_ensure_constraints_issues_expected_statements(self, monkeypatch) -> None:
        session = FakeSession()
        monkeypatch.setattr(
            graph_service, "graph_session", lambda: _fake_graph_session(session)
        )
        graph_service.ensure_constraints()
        queries = [q for q, _ in session.queries]
        assert any("CREATE CONSTRAINT facility_id_unique" in q for q in queries)
        assert any("CREATE CONSTRAINT blood_type_code_unique" in q for q in queries)
        assert any("CREATE CONSTRAINT city_name_unique" in q for q in queries)
        assert any("CREATE INDEX facility_city_idx" in q for q in queries)
        assert any("CREATE INDEX route_id_idx" in q for q in queries)
        assert any("CREATE INDEX route_status_idx" in q for q in queries)
        assert len(queries) == 6

    def test_merge_facility_tx_sends_expected_params(self) -> None:
        session = FakeSession()
        hp = HospitalProfile(
            id=uuid.uuid4(), user_id=uuid.uuid4(), name="Test", city="Metro",
            latitude=1.0, longitude=2.0, verified=True, facility_type=FacilityType.HOSPITAL,
        )
        graph_service._merge_facility_tx(session, hp)
        assert len(session.queries) == 1
        query, params = session.queries[0]
        assert "MERGE (f:Facility {id: $id})" in query
        assert "LOCATED_IN" in query
        assert params["id"] == str(hp.id)
        assert params["name"] == "Test"
        assert params["facility_type"] == "HOSPITAL"
        assert params["city"] == "Metro"
        assert params["latitude"] == 1.0

    def test_merge_facility_tx_handles_no_city(self) -> None:
        session = FakeSession()
        hp = HospitalProfile(
            id=uuid.uuid4(), user_id=uuid.uuid4(), name="No City", city=None,
            latitude=None, longitude=None, verified=False, facility_type=FacilityType.BLOOD_BANK,
        )
        graph_service._merge_facility_tx(session, hp)
        _, params = session.queries[0]
        assert params["city"] is None
        assert params["facility_type"] == "BLOOD_BANK"

    def test_merge_stock_tx_sends_expected_params(self) -> None:
        session = FakeSession()
        hospital_id = uuid.uuid4()
        graph_service._merge_stock_tx(session, hospital_id, BloodType.O_NEG, 7)
        query, params = session.queries[0]
        assert "HAS_STOCK" in query
        assert params["facility_id"] == str(hospital_id)
        assert params["blood_type"] == "O_NEG"
        assert params["units"] == 7

    def test_merge_route_tx_sends_expected_params(self) -> None:
        session = FakeSession()
        route = TransportRoute(
            id=uuid.uuid4(), origin_facility_id=uuid.uuid4(),
            destination_facility_id=uuid.uuid4(),
            distance_km=42.5, transit_minutes=55, status=TransportRouteStatus.OPERATIONAL,
        )
        graph_service._merge_route_tx(session, route)
        query, params = session.queries[0]
        assert "MERGE (a)-[r:ROUTE {route_id: $route_id}]->(b)" in query
        assert params["distance_km"] == 42.5
        assert params["status"] == "OPERATIONAL"
        assert params["route_id"] == str(route.id)
        assert params["disrupted_reason"] is None

    def test_route_paths_to_sends_parameters_and_parses_fake_result(self, monkeypatch) -> None:
        session = FakeSession()
        dest, src, rid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        excluded = uuid.uuid4()
        session.canned["[:ROUTE*1..4]"] = [{
            "source_id": str(src),
            "best": {"route_ids": [str(rid)], "node_ids": [str(src), str(dest)],
                     "km": 8.2, "minutes": 15},
        }]
        monkeypatch.setattr(
            graph_service, "graph_session", lambda: _fake_graph_session(session)
        )
        paths = graph_service.route_paths_to(
            destination_id=dest, source_ids=[src], objective="minutes", max_hops=4,
            excluded_route_ids=frozenset({excluded}),
        )
        query, params = session.queries[0]
        assert params == {"dest_id": str(dest), "source_ids": [str(src)],
                          "excluded": [str(excluded)], "objective": "minutes"}
        assert paths[src].route_ids == [rid] and paths[src].distance_km == 8.2

    def test_route_paths_to_with_no_sources_does_not_query(self, monkeypatch) -> None:
        session = FakeSession()
        monkeypatch.setattr(
            graph_service, "graph_session", lambda: _fake_graph_session(session)
        )
        assert graph_service.route_paths_to(destination_id=uuid.uuid4(), source_ids=[],
                                             objective="km", max_hops=3) == {}
        assert session.queries == []
