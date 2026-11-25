"""Phase 16 -- Neo4j Graph Decision Engine & Resource Pathway Hardening.

The repository already contained a mature, single Neo4j integration
(app/core/graph.py + app/services/graph_service.py + the emergency/hospital/
admin pathway routes) with extensive existing mocked-Neo4j coverage
(tests/test_graph_engine.py, tests/test_graph_service.py,
tests/test_graph_service_logic.py) and real end-to-end pathway/versioning/
disruption/re-optimization coverage (tests/test_phase12_emergency_sos_engine.py).
This file adds only what that existing coverage did not already establish:

  * a real concurrency bug this phase's audit found and fixed --
    `emergency_pathway_service.create_pathway_version` read-then-wrote the
    "current ACTIVE pathway" for an emergency with no row lock, so two
    near-simultaneous callers (e.g. a hospital's manual re-selection racing
    an admin's disruption-triggered re-optimization) could both try to
    INSERT a new ACTIVE row and collide on the partial unique index
    `uq_emergency_pathway_one_active` as an unhandled IntegrityError -- the
    same class of bug Phase 15 found and fixed for certificate issuance.
    Fixed by locking the Emergency row (SELECT ... FOR UPDATE) for the
    duration of `create_pathway_version`;
  * a real input-validation gap this phase's audit found and fixed --
    `max_results` on both resource-pathway endpoints was an unbounded
    `int`, unlike the existing, more careful admin pathway-log's
    `Query(..., ge=1, le=200)`. Fixed by bounding both to 1-50;
  * an IDOR sweep angle not covered by the existing hospital/donor/admin
    tests in tests/test_graph_api.py: an unrelated PATIENT and a FAMILY
    member without an accepted link to the emergency's patient;
  * privacy confirmation that pathway responses never carry Neo4j
    internals, credentials, or donor PII (schema-level, plus a live check);
  * confirmation that the graph/pathway layer is never the allocation
    authority -- selecting or querying a pathway never touches
    HospitalInventory, only an explicit hospital "accept" does (Phase 10/12,
    unmodified);
  * confirmation that a genuinely unreachable graph engine is reflected
    honestly on the persisted EmergencyPathway (pathway_source=
    RELATIONAL_ROUTES, graph_available=False) rather than silently
    reported as a successful graph decision;
  * a Cypher-injection-shaped input test at the API boundary -- FastAPI/
    Pydantic type validation (BloodType enum, UUID path/query params)
    rejects malformed input with 422 before it can ever reach a Cypher
    parameter, which is confirmed here rather than merely asserted.

Real Neo4j test policy (Phase 16 spec section 39): this sandbox cannot
reach Neo4j/Aura over Bolt (confirmed by directly re-running
scripts/verify_neo4j.py in this session -- ServiceUnavailable, exit 1,
consistent with LIFELINK_ENV_NEO4J_VERIFICATION_REPORT.md from a prior
phase). Every test in this file is therefore hermetic: it either exercises
the real Postgres-backed API end to end (relying on graph_service's own
honest haversine fallback, which requires no live Neo4j) or monkeypatches
graph_service.find_resource_pathways directly to simulate graph
availability/unavailability deterministically. No test fakes a live Neo4j
connection or claims live verification.
"""
from __future__ import annotations

import threading
import uuid

from sqlalchemy import select

from app.core.graph import GraphUnavailableError
from app.models.hospital import HospitalInventory, HospitalProfile
from app.services import graph_service
from tests.helpers import auth_client, stock_units


def _facility(city: str, lat: float, lon: float, name: str):
    c, _ = auth_client("HOSPITAL")
    r = c.put("/hospitals/me", json={"name": name, "city": city, "latitude": lat, "longitude": lon})
    assert r.status_code == 200, r.text
    return c, r.json()


def _verify_facility(db_session, facility_id: str) -> None:
    hp = db_session.get(HospitalProfile, uuid.UUID(facility_id))
    hp.verified = True
    db_session.commit()


def _stock(db_session, facility_id: str, blood_type: str, units: int) -> None:
    row = db_session.scalar(
        select(HospitalInventory).where(
            HospitalInventory.hospital_id == uuid.UUID(facility_id),
            HospitalInventory.blood_type == blood_type,
        )
    )
    if row is None:
        row = HospitalInventory(hospital_id=uuid.UUID(facility_id), blood_type=blood_type, units=0)
        db_session.add(row)
        db_session.flush()
    row.units = units
    db_session.commit()


def _open_emergency(patient, blood_type: str = "B_POS") -> str:
    return patient.post("/emergencies", json={"blood_type": blood_type}).json()["id"]


# --------------------------------------------------------------------------- #
# CONCURRENCY -- the real bug this phase found and fixed
# --------------------------------------------------------------------------- #
def test_concurrent_pathway_selection_for_same_emergency_yields_no_active_collision(
    db_session,
) -> None:
    """Real bug found in this phase: create_pathway_version's "read the
    current ACTIVE pathway, then insert a new ACTIVE row" had no lock, so
    two near-simultaneous POST /pathway calls for the same emergency could
    both pass the read, both insert version N+1 as ACTIVE, and the loser
    would hit uq_emergency_pathway_one_active as an unhandled 500. Fixed by
    locking the Emergency row for the duration of create_pathway_version.
    This test would have flaked/500'd on at least one thread before that
    fix; now both must succeed and end with exactly one ACTIVE version."""
    admin, _ = auth_client("ADMIN")
    patient, _ = auth_client("PATIENT")
    origin_client, origin = _facility("Chennai", 13.0827, 80.2707, "Race Origin")
    dest_client, dest = _facility("Vellore", 12.9165, 79.1325, "Race Dest")
    _stock(db_session, dest["id"], "B_POS", 4)
    admin.post("/admin/transport-routes", json={
        "origin_facility_id": origin["id"], "destination_facility_id": dest["id"],
    })

    eid = _open_emergency(patient)

    results: list[int] = []
    barrier = threading.Barrier(2)

    def _select() -> None:
        barrier.wait()
        r = origin_client.post(
            f"/emergencies/{eid}/pathway", json={"origin_facility_id": origin["id"]}
        )
        results.append(r.status_code)

    threads = [threading.Thread(target=_select) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(results) == 2
    assert all(code == 200 for code in results), results

    detail = patient.get(f"/emergencies/{eid}").json()
    active = [p for p in detail["pathway_history"] if p["status"] == "ACTIVE"]
    assert len(active) == 1, detail["pathway_history"]
    # Exactly two versions were created (one per request), one superseded.
    assert len(detail["pathway_history"]) == 2


# --------------------------------------------------------------------------- #
# INPUT VALIDATION -- unbounded max_results (real gap found and fixed)
# --------------------------------------------------------------------------- #
def test_hospital_pathway_max_results_is_bounded() -> None:
    hosp, _ = auth_client("HOSPITAL")
    hosp.put(
        "/hospitals/me",
        json={"name": "Bounds Hosp", "city": "Pune", "latitude": 18.5, "longitude": 73.8},
    )

    for bad in (0, -1, 51, 10_000):
        r = hosp.get(
            "/hospitals/me/resource-pathways",
            params={"blood_type": "O_NEG", "max_results": bad},
        )
        assert r.status_code == 422, (bad, r.text)

    r = hosp.get(
        "/hospitals/me/resource-pathways", params={"blood_type": "O_NEG", "max_results": 5}
    )
    assert r.status_code == 200


def test_emergency_pathway_max_results_is_bounded(db_session) -> None:
    patient, _ = auth_client("PATIENT")
    origin_client, origin = _facility("Chennai", 13.0827, 80.2707, "Bounds Origin")
    _verify_facility(db_session, origin["id"])
    eid = _open_emergency(patient)

    for bad in (0, -5, 100):
        r = origin_client.get(
            f"/emergencies/{eid}/resource-pathways",
            params={"origin_facility_id": origin["id"], "max_results": bad},
        )
        assert r.status_code == 422, (bad, r.text)


# --------------------------------------------------------------------------- #
# CYPHER / INJECTION SAFETY -- malformed input never reaches a Cypher param
# --------------------------------------------------------------------------- #
def test_malicious_blood_type_and_facility_id_are_rejected_before_reaching_graph_layer() -> None:
    """BloodType is a closed Pydantic enum and origin_facility_id/emergency_id
    are UUID-typed path/query params -- a Cypher-injection-shaped string
    (quotes, MATCH/DELETE keywords, comment sequences) can never reach
    graph_service's parameterized queries at all; it is rejected as a plain
    422 at the FastAPI validation layer first."""
    hosp, _ = auth_client("HOSPITAL")
    hosp.put(
        "/hospitals/me",
        json={"name": "Injection Hosp", "city": "Pune", "latitude": 18.5, "longitude": 73.8},
    )

    injected = "O_NEG'}) DETACH DELETE n //"
    r = hosp.get("/hospitals/me/resource-pathways", params={"blood_type": injected})
    assert r.status_code == 422

    patient, _ = auth_client("PATIENT")
    eid = _open_emergency(patient)
    r2 = patient.get(
        f"/emergencies/{eid}/resource-pathways",
        params={"origin_facility_id": "'; MATCH (n) DETACH DELETE n; --"},
    )
    assert r2.status_code == 422


def test_resync_endpoint_never_exposes_cypher_or_credentials_on_failure() -> None:
    """This sandbox has no live Neo4j -- /admin/graph/resync must fail
    honestly (503), and the error body must never contain the Aura URI,
    username, password, or a raw driver exception message."""
    admin, _ = auth_client("ADMIN")
    r = admin.post("/admin/graph/resync")
    assert r.status_code == 503, r.text
    body = str(r.json())
    for secret_shaped in ("neo4j+s://", "bolt://", "password", "NEO4J_PASSWORD"):
        assert secret_shaped not in body


# --------------------------------------------------------------------------- #
# IDOR -- angles not already covered by tests/test_graph_api.py
# --------------------------------------------------------------------------- #
def test_unrelated_patient_cannot_view_another_patients_emergency_pathways(db_session) -> None:
    owner, _ = auth_client("PATIENT")
    stranger, _ = auth_client("PATIENT")
    origin_client, origin = _facility("Chennai", 13.0827, 80.2707, "IDOR Origin")
    _verify_facility(db_session, origin["id"])
    eid = _open_emergency(owner)

    r = stranger.get(
        f"/emergencies/{eid}/resource-pathways", params={"origin_facility_id": origin["id"]}
    )
    assert r.status_code == 404
    r2 = stranger.post(f"/emergencies/{eid}/pathway", json={"origin_facility_id": origin["id"]})
    assert r2.status_code == 404


def test_family_without_accepted_link_cannot_view_emergency_pathways(db_session) -> None:
    patient, patient_email = auth_client("PATIENT")
    family, _ = auth_client("FAMILY")
    origin_client, origin = _facility("Chennai", 13.0827, 80.2707, "Family IDOR Origin")
    _verify_facility(db_session, origin["id"])
    eid = _open_emergency(patient)

    # No link at all.
    r = family.get(
        f"/emergencies/{eid}/resource-pathways", params={"origin_facility_id": origin["id"]}
    )
    assert r.status_code == 404

    # A link exists but is not yet ACCEPTED -- still no access.
    family.post("/family/links", json={"patient_email": patient_email, "relationship": "cousin"})
    r2 = family.get(
        f"/emergencies/{eid}/resource-pathways", params={"origin_facility_id": origin["id"]}
    )
    assert r2.status_code == 404


# --------------------------------------------------------------------------- #
# PRIVACY -- pathway responses carry no PII / graph internals
# --------------------------------------------------------------------------- #
def test_resource_pathway_response_has_no_pii_or_graph_internals(db_session) -> None:
    hosp, _ = auth_client("HOSPITAL")
    hosp.put(
        "/hospitals/me",
        json={"name": "Privacy Hosp", "city": "Pune", "latitude": 18.5, "longitude": 73.8},
    ).json()
    dest_client, dest = _facility("Mumbai", 19.076, 72.877, "Privacy Dest")
    _stock(db_session, dest["id"], "O_NEG", 3)

    r = hosp.get("/hospitals/me/resource-pathways", params={"blood_type": "O_NEG"})
    assert r.status_code == 200
    body = r.json()
    forbidden = {
        "donor_email", "donor_phone", "email", "phone", "password", "neo4j_uri",
        "elementId", "identity", "internal_id", "review_note", "address",
    }
    assert forbidden.isdisjoint(body.keys())
    for c in body["candidates"]:
        assert forbidden.isdisjoint(c.keys())
        assert set(c.keys()) == {
            "facility_id", "facility_name", "city", "units", "distance_km",
            "transit_minutes", "hops",
        }


# --------------------------------------------------------------------------- #
# INVENTORY BOUNDARY -- graph/pathway layer is never allocation authority
# --------------------------------------------------------------------------- #
def test_selecting_a_pathway_never_reserves_or_alters_inventory(db_session) -> None:
    """Phase 16 spec section 36: Neo4j must never be the final allocation
    authority. Selecting (or repeatedly re-selecting) a resource pathway is
    purely informational -- it must not touch HospitalInventory.units at
    all. Only an explicit hospital "accept" (Phase 10/12, unmodified)
    reserves inventory."""
    admin, _ = auth_client("ADMIN")
    patient, _ = auth_client("PATIENT")
    origin_client, origin = _facility("Chennai", 13.0827, 80.2707, "Inv Origin")
    dest_client, dest = _facility("Vellore", 12.9165, 79.1325, "Inv Dest")
    _stock(db_session, dest["id"], "B_POS", 4)
    admin.post("/admin/transport-routes", json={
        "origin_facility_id": origin["id"], "destination_facility_id": dest["id"],
    })
    eid = _open_emergency(patient)

    for _ in range(3):
        r = origin_client.post(
            f"/emergencies/{eid}/pathway", json={"origin_facility_id": origin["id"]}
        )
        assert r.status_code == 200

    row = db_session.scalar(
        select(HospitalInventory).where(
            HospitalInventory.hospital_id == uuid.UUID(dest["id"]),
            HospitalInventory.blood_type == "B_POS",
        )
    )
    assert row.units == 4, "pathway selection must never mutate hospital inventory"


# --------------------------------------------------------------------------- #
# FALLBACK HONESTY -- a genuinely unreachable graph must never be reported
# as a live graph success on the persisted pathway
# --------------------------------------------------------------------------- #
def test_pathway_persists_honest_fallback_source_when_graph_unavailable(
    db_session, monkeypatch
) -> None:
    """Neo4j configured but unreachable: the persisted pathway must name the
    engine that really answered (RELATIONAL_ROUTES) with graph_available
    False -- never GRAPH. (Pre-gap-elimination this was HAVERSINE_FALLBACK;
    the relational route engine replaced the straight-line fallback.)"""
    from app.services import pathway_planner

    patient, _ = auth_client("PATIENT")
    origin_client, origin = _facility("Chennai", 13.0827, 80.2707, "Fallback Origin")
    _dest_client, dest = _facility("Vellore", 12.9165, 79.1325, "Fallback Dest")
    _verify_facility(db_session, dest["id"])
    stock_units(dest["id"], "B_POS", 2)
    eid = _open_emergency(patient)

    def _unreachable(**_kw):
        raise GraphUnavailableError("simulated: Bolt connection refused")

    monkeypatch.setattr(pathway_planner, "graph_enabled", lambda: True)
    monkeypatch.setattr(graph_service, "route_paths_to", _unreachable)

    r = origin_client.post(f"/emergencies/{eid}/pathway", json={"origin_facility_id": origin["id"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pathway_source"] == "RELATIONAL_ROUTES"
    assert body["graph_available"] is False
    assert body["selected_facility_id"] == dest["id"]


# --------------------------------------------------------------------------- #
# GRAPH HEALTH -- never "ok" when unreachable (API-level confirmation)
# --------------------------------------------------------------------------- #
def test_admin_health_never_reports_graph_ok_when_configured_but_unreachable(monkeypatch) -> None:
    from app.core import graph as graph_core

    def _fake_health():
        return {
            "configured": True, "connected": False, "status": "unreachable",
            "reason": "ServiceUnavailable",
        }

    monkeypatch.setattr(graph_core, "graph_health", _fake_health)

    admin, _ = auth_client("ADMIN")
    r = admin.get("/admin/system/health")
    assert r.status_code == 200
    body = r.json()
    assert body["graph"]["status"] != "ok"
    assert body["graph"]["connected"] is False
    assert body["status"] == "degraded"
