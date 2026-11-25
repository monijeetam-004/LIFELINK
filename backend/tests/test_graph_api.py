"""API-level tests for the graph/resource-pathway engine surface:
admin transport-routes CRUD/disrupt/recover, /admin/graph/resync,
/admin/graph/pathway-log, and the two resource-pathway read endpoints on
hospitals/ and emergencies/.

Neo4j is genuinely unreachable in this sandbox (see tests/test_graph_engine.py
for why), so every one of these tests exercises the REAL relational path
engine (PostgreSQL transport routes, `RELATIONAL_ROUTES`) end to end through
the actual HTTP API -- not a mock. Since the gap-elimination round the old
`HAVERSINE_FALLBACK` source is no longer produced: facilities on the
transport network are routed over real routes, and only facilities with no
recorded route at all get a per-candidate, labelled STRAIGHT_LINE_ESTIMATE.
`/admin/graph/resync` is the one endpoint expected to genuinely fail (503)
here, which is itself the behaviour under test: it must report failure
honestly rather than claim success.
"""
from __future__ import annotations

from tests.helpers import auth_client, verify_facility_of

NON_ADMIN_ROLES = ["PATIENT", "FAMILY", "DONOR", "HOSPITAL"]


def _hospital(city: str, lat: float, lon: float, name: str):
    """A verified facility: only verified facilities are pathway sources."""
    c, email = auth_client("HOSPITAL")
    r = c.put("/hospitals/me", json={"name": name, "city": city, "latitude": lat, "longitude": lon})
    assert r.status_code == 200, r.text
    verify_facility_of(email)
    return c, c.get("/hospitals/me").json()


# ---------------------------------------------------------------------- #
# Transport routes: CRUD
# ---------------------------------------------------------------------- #
def test_non_admin_forbidden_on_transport_routes() -> None:
    for role in NON_ADMIN_ROLES:
        client, _ = auth_client(role)
        assert client.get("/admin/transport-routes").status_code == 403
        assert client.post("/admin/transport-routes", json={}).status_code == 403


def test_create_route_auto_computes_distance_and_estimate() -> None:
    admin, _ = auth_client("ADMIN")
    a_client, _ = _hospital("Mumbai", 19.0760, 72.8777, "Route A Hosp")
    b_client, _ = _hospital("Pune", 18.5204, 73.8567, "Route B Hosp")

    directory = admin.get("/hospitals/directory").json()
    names = {d["name"]: d["id"] for d in directory}
    a_id = names["Route A Hosp"]
    b_id = names["Route B Hosp"]

    r = admin.post(
        "/admin/transport-routes",
        json={"origin_facility_id": a_id, "destination_facility_id": b_id},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["distance_km"] > 100  # Mumbai-Pune is genuinely ~120-150km
    assert body["transit_minutes"] > 0
    assert body["status"] == "OPERATIONAL"

    # Duplicate (either order) is rejected -- a transport link is undirected.
    dup1 = admin.post(
        "/admin/transport-routes",
        json={"origin_facility_id": a_id, "destination_facility_id": b_id},
    )
    assert dup1.status_code == 409
    dup2 = admin.post(
        "/admin/transport-routes",
        json={"origin_facility_id": b_id, "destination_facility_id": a_id},
    )
    assert dup2.status_code == 409


def test_create_route_requires_distinct_facilities() -> None:
    admin, _ = auth_client("ADMIN")
    _, _ = _hospital("Delhi", 28.7041, 77.1025, "Solo Hosp")
    directory = admin.get("/hospitals/directory").json()
    fid = next(d["id"] for d in directory if d["name"] == "Solo Hosp")
    r = admin.post(
        "/admin/transport-routes",
        json={"origin_facility_id": fid, "destination_facility_id": fid},
    )
    assert r.status_code == 400


def test_create_route_missing_coordinates_requires_explicit_distance() -> None:
    admin, _ = auth_client("ADMIN")
    a_client, _ = auth_client("HOSPITAL")
    a_client.put("/hospitals/me", json={"name": "No Coords A"})
    b_client, _ = auth_client("HOSPITAL")
    b_client.put("/hospitals/me", json={"name": "No Coords B"})

    directory = admin.get("/hospitals/directory").json()
    names = {d["name"]: d["id"] for d in directory}
    a_id, b_id = names["No Coords A"], names["No Coords B"]

    bad = admin.post(
        "/admin/transport-routes",
        json={"origin_facility_id": a_id, "destination_facility_id": b_id},
    )
    assert bad.status_code == 400

    good = admin.post(
        "/admin/transport-routes",
        json={
            "origin_facility_id": a_id, "destination_facility_id": b_id,
            "distance_km": 50.0, "transit_minutes": 60,
        },
    )
    assert good.status_code == 201
    assert good.json()["distance_km"] == 50.0
    assert good.json()["transit_minutes"] == 60


# ---------------------------------------------------------------------- #
# Disrupt / recover with computed impact
# ---------------------------------------------------------------------- #
def test_disrupt_and_recover_route_with_impact() -> None:
    admin, _ = auth_client("ADMIN")
    origin_client, _ = _hospital("Chennai", 13.0827, 80.2707, "Impact Origin")
    dest_client, _ = _hospital("Vellore", 12.9165, 79.1325, "Impact Dest")
    dest_client.put(
        "/hospitals/me/inventory",
        json={"items": [{"blood_type": "B_POS", "units": 4}]},
    )

    directory = admin.get("/hospitals/directory").json()
    names = {d["name"]: d["id"] for d in directory}
    origin_id, dest_id = names["Impact Origin"], names["Impact Dest"]

    created = admin.post(
        "/admin/transport-routes",
        json={"origin_facility_id": origin_id, "destination_facility_id": dest_id},
    )
    assert created.status_code == 201
    route_id = created.json()["id"]

    disrupt = admin.post(
        f"/admin/transport-routes/{route_id}/disrupt", json={"reason": "flooded highway"}
    )
    assert disrupt.status_code == 200, disrupt.text
    body = disrupt.json()
    assert body["route"]["status"] == "DISRUPTED"
    assert body["route"]["disrupted_reason"] == "flooded highway"
    # A real inventory-backed probe scenario exists (dest has B_POS stock),
    # so impact must be a real computed dict, not null.
    assert body["sample_blood_type"] == "B_POS"
    assert body["impact"] is not None
    assert body["impact"]["source"] == "RELATIONAL_ROUTES"

    # Disrupting an already-disrupted route is rejected.
    again = admin.post(
        f"/admin/transport-routes/{route_id}/disrupt", json={"reason": "still flooded"}
    )
    assert again.status_code == 409

    recover = admin.post(f"/admin/transport-routes/{route_id}/recover")
    assert recover.status_code == 200, recover.text
    rbody = recover.json()
    assert rbody["route"]["status"] == "OPERATIONAL"
    assert rbody["route"]["disrupted_reason"] is None

    # Recovering an already-operational route is rejected.
    again2 = admin.post(f"/admin/transport-routes/{route_id}/recover")
    assert again2.status_code == 409


def test_disrupt_with_no_inventory_anywhere_reports_no_impact_honestly() -> None:
    admin, _ = auth_client("ADMIN")
    _, _ = _hospital("Agra", 27.1767, 78.0081, "Empty Origin")
    _, _ = _hospital("Mathura", 27.4924, 77.6737, "Empty Dest")
    directory = admin.get("/hospitals/directory").json()
    names = {d["name"]: d["id"] for d in directory}

    created = admin.post(
        "/admin/transport-routes",
        json={
            "origin_facility_id": names["Empty Origin"],
            "destination_facility_id": names["Empty Dest"],
        },
    )
    route_id = created.json()["id"]
    disrupt = admin.post(
        f"/admin/transport-routes/{route_id}/disrupt", json={"reason": "test"}
    )
    assert disrupt.status_code == 200
    body = disrupt.json()
    assert body["sample_blood_type"] is None
    assert body["impact"] is None  # never fabricated


# ---------------------------------------------------------------------- #
# Graph resync -- expected to genuinely fail (503) in this sandbox
# ---------------------------------------------------------------------- #
def test_graph_resync_reports_failure_honestly_when_neo4j_unreachable() -> None:
    admin, _ = auth_client("ADMIN")
    r = admin.post("/admin/graph/resync")
    assert r.status_code == 503
    assert "credential" not in r.text.lower()
    assert "password" not in r.text.lower()


def test_graph_resync_forbidden_for_non_admin() -> None:
    for role in NON_ADMIN_ROLES:
        client, _ = auth_client(role)
        assert client.post("/admin/graph/resync").status_code == 403


# ---------------------------------------------------------------------- #
# Pathway log
# ---------------------------------------------------------------------- #
def test_pathway_log_records_hospital_query() -> None:
    admin, _ = auth_client("ADMIN")
    origin_client, _ = _hospital("Bengaluru", 12.9716, 77.5946, "Log Origin")
    r = origin_client.get("/hospitals/me/resource-pathways", params={"blood_type": "O_NEG"})
    assert r.status_code == 200
    assert r.json()["pathway_source"] == "RELATIONAL_ROUTES"

    log = admin.get("/admin/graph/pathway-log")
    assert log.status_code == 200
    assert log.json()["items"], "expected at least one pathway-log entry"
    entry = log.json()["items"][0]
    assert entry["blood_type"] == "O_NEG"
    assert entry["pathway_source"] == "RELATIONAL_ROUTES"


def test_pathway_log_forbidden_for_non_admin() -> None:
    for role in NON_ADMIN_ROLES:
        client, _ = auth_client(role)
        assert client.get("/admin/graph/pathway-log").status_code == 403


# ---------------------------------------------------------------------- #
# Hospital resource-pathways
# ---------------------------------------------------------------------- #
def test_hospital_resource_pathways_ranks_by_distance() -> None:
    origin_client, _ = _hospital("Hyderabad", 17.3850, 78.4867, "HP Origin")
    near_client, _ = _hospital("Secunderabad", 17.4399, 78.4983, "HP Near")
    far_client, _ = _hospital("Warangal", 17.9689, 79.5941, "HP Far")
    near_client.put(
        "/hospitals/me/inventory", json={"items": [{"blood_type": "AB_NEG", "units": 2}]}
    )
    far_client.put(
        "/hospitals/me/inventory", json={"items": [{"blood_type": "AB_NEG", "units": 6}]}
    )

    r = origin_client.get("/hospitals/me/resource-pathways", params={"blood_type": "AB_NEG"})
    assert r.status_code == 200
    body = r.json()
    assert body["pathway_source"] == "RELATIONAL_ROUTES"
    assert body["graph_available"] is False
    assert len(body["candidates"]) == 2
    assert body["candidates"][0]["distance_km"] <= body["candidates"][1]["distance_km"]
    # no transport routes are recorded for these facilities: each candidate
    # is an explicitly labelled straight-line estimate, not a route
    assert {c["route_basis"] for c in body["candidates"]} == {"STRAIGHT_LINE_ESTIMATE"}
    assert all(c["route_ids"] == [] for c in body["candidates"])


def test_hospital_resource_pathways_forbidden_for_non_hospital() -> None:
    for role in ["PATIENT", "FAMILY", "DONOR", "ADMIN"]:
        client, _ = auth_client(role)
        r = client.get("/hospitals/me/resource-pathways", params={"blood_type": "O_POS"})
        assert r.status_code == 403


# ---------------------------------------------------------------------- #
# Emergency resource-pathways
# ---------------------------------------------------------------------- #
def test_emergency_resource_pathways_requires_owning_facility() -> None:
    patient, _ = auth_client("PATIENT")
    em = patient.post(
        "/emergencies", json={"blood_type": "O_NEG", "location_text": "Metro ER"}
    )
    assert em.status_code == 201
    emergency_id = em.json()["id"]

    owner_client, _ = _hospital("Metro", 40.0, -73.0, "Owner Hosp")
    directory = owner_client.get("/hospitals/directory").json()
    owner_id = next(d["id"] for d in directory if d["name"] == "Owner Hosp")

    other_client, _ = _hospital("Elsewhere", 41.0, -74.0, "Other Hosp")

    ok = owner_client.get(
        f"/emergencies/{emergency_id}/resource-pathways",
        params={"origin_facility_id": owner_id},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["blood_type"] == "O_NEG"

    forbidden = other_client.get(
        f"/emergencies/{emergency_id}/resource-pathways",
        params={"origin_facility_id": owner_id},
    )
    assert forbidden.status_code == 403


def test_emergency_resource_pathways_admin_can_use_any_facility() -> None:
    patient, _ = auth_client("PATIENT")
    em = patient.post(
        "/emergencies", json={"blood_type": "A_POS", "location_text": "Central"}
    )
    emergency_id = em.json()["id"]

    admin, _ = auth_client("ADMIN")
    hosp_client, _ = _hospital("Central", 10.0, 10.0, "Admin Probe Hosp")
    facility_id = next(
        d["id"] for d in admin.get("/hospitals/directory").json() if d["name"] == "Admin Probe Hosp"
    )

    r = admin.get(
        f"/emergencies/{emergency_id}/resource-pathways",
        params={"origin_facility_id": facility_id},
    )
    assert r.status_code == 200


def test_emergency_resource_pathways_donor_forbidden() -> None:
    patient, _ = auth_client("PATIENT")
    em = patient.post(
        "/emergencies", json={"blood_type": "A_POS", "location_text": "Central"}
    )
    emergency_id = em.json()["id"]
    hosp_client, _ = _hospital("Central", 10.0, 10.0, "Donor Test Hosp")
    facility_id = next(
        d["id"] for d in hosp_client.get("/hospitals/directory").json()
        if d["name"] == "Donor Test Hosp"
    )
    # Make the donor a real, compatible candidate so `_can_view()` (emergency
    # visibility) passes and the request reaches this endpoint's own
    # HOSPITAL/ADMIN-only role check -- otherwise the 404 from _can_view()
    # would mask what this test is actually checking.
    donor, _ = auth_client("DONOR")
    donor.put("/donors/me", json={"blood_type": "A_POS", "is_available": True})
    r = donor.get(
        f"/emergencies/{emergency_id}/resource-pathways",
        params={"origin_facility_id": facility_id},
    )
    assert r.status_code == 403
