"""Phase 12 -- the emergency/SOS engine, formalized: declarative lifecycle +
row locking, idempotent creation, non-blocking duplicate detection, honest
deadline/slack, versioned resource pathways (including automatic
re-optimization after a real transport disruption), and inventory
reservation tied to a real HOSPITAL responder's accept -- all built on the
pre-existing, unmodified graph/matching/notification/audit architecture
(see LIFELINK_PHASE_12_REPORT.md for the full audit + design rationale).

Every test here talks to the real HTTP API (or, for the handful that need
to inspect/seed state a public endpoint doesn't expose, the `db_session`
fixture) against the real Postgres test database -- nothing is mocked.
"""
from __future__ import annotations

import threading
import uuid

from sqlalchemy import select

from app.models.emergency import Emergency
from app.models.hospital import HospitalProfile
from tests.helpers import auth_client, stock_units, verify_facility_of


def _ready_donor(blood_type: str = "O_NEG"):
    c, email = auth_client("DONOR")
    c.put("/donors/me", json={"blood_type": blood_type, "is_available": True})
    return c, email


def _facility(city: str, lat: float, lon: float, name: str, verified: bool = True):
    """An operating facility. Verified by default: since gap elimination #7
    only a verified facility can respond to emergencies or be a pathway
    source; pass verified=False to test the unverified case."""
    c, email = auth_client("HOSPITAL")
    r = c.put("/hospitals/me", json={"name": name, "city": city, "latitude": lat, "longitude": lon})
    assert r.status_code == 200, r.text
    if verified:
        verify_facility_of(email)
        r = c.get("/hospitals/me")
    # HospitalProfileOut now exposes `id` (the hospital_profiles.id primary
    # key FK'd by Emergency.target_facility_id / EmergencyPathway's facility
    # columns) alongside `user_id` -- see the Phase 12 addition in
    # app/schemas/hospital.py.
    return c, r.json()


def _facility_id(caller, name: str) -> str:
    return next(d["id"] for d in caller.get("/hospitals/directory").json() if d["name"] == name)


def _verify_facility(db_session, facility_id: str) -> None:
    hp = db_session.get(HospitalProfile, uuid.UUID(facility_id))
    hp.verified = True
    db_session.commit()


def _stock(db_session, facility_id: str, blood_type: str, units: int) -> None:
    stock_units(facility_id, blood_type, units)


# --------------------------------------------------------------------------- #
# A. Happy path (with the new fields wired end to end)
# --------------------------------------------------------------------------- #
def test_happy_path_reports_allowed_transitions_and_slack() -> None:
    patient, _ = auth_client("PATIENT")
    donor, _ = _ready_donor("O_NEG")

    r = patient.post("/emergencies", json={
        "blood_type": "A_POS", "location_text": "Metro ER", "units_needed": 2,
    })
    assert r.status_code == 201
    body = r.json()
    eid = body["id"]
    assert body["units_needed"] == 2
    assert set(body["allowed_transitions"]) == {
        "ACKNOWLEDGED", "ASSISTANCE_FOUND", "CANCELLED", "MATCHING", "RESOLVED",
    }

    donor.post(f"/emergencies/{eid}/acknowledge", json={})
    ack = patient.get(f"/emergencies/{eid}").json()
    assert set(ack["allowed_transitions"]) == {
        "ASSISTANCE_FOUND", "CANCELLED", "MATCHING", "RESOLVED",
    }

    donor.post(f"/emergencies/{eid}/respond", json={"action": "ACCEPTED"})
    assert patient.get(f"/emergencies/{eid}").json()["status"] == "MATCHING"

    patient.post(f"/emergencies/{eid}/resolve", json={})
    resolved = patient.get(f"/emergencies/{eid}").json()
    assert resolved["status"] == "RESOLVED"
    assert resolved["allowed_transitions"] == []  # terminal


# --------------------------------------------------------------------------- #
# D. Deadline / slack -- positive, zero, negative, all honest, never clamped
# --------------------------------------------------------------------------- #
def test_slack_is_positive_for_a_future_deadline() -> None:
    patient, _ = auth_client("PATIENT")
    r = patient.post("/emergencies", json={
        "blood_type": "O_NEG", "deadline": "2099-01-01T00:00:00Z",
    })
    assert r.json()["slack_seconds"] > 0


def test_slack_is_negative_for_a_past_deadline_and_never_clamped() -> None:
    patient, _ = auth_client("PATIENT")
    r = patient.post("/emergencies", json={
        "blood_type": "O_NEG", "deadline": "2000-01-01T00:00:00Z",
    })
    slack = r.json()["slack_seconds"]
    assert slack < 0  # genuinely overdue -- never reported as zero or positive


def test_slack_is_none_without_a_deadline() -> None:
    patient, _ = auth_client("PATIENT")
    r = patient.post("/emergencies", json={"blood_type": "O_NEG"})
    assert r.json()["slack_seconds"] is None


# --------------------------------------------------------------------------- #
# E. Idempotent creation -- double-click / retry protection
# --------------------------------------------------------------------------- #
def test_same_idempotency_key_returns_the_same_emergency() -> None:
    patient, _ = auth_client("PATIENT")
    key = str(uuid.uuid4())
    first = patient.post("/emergencies", json={"blood_type": "O_NEG", "idempotency_key": key})
    assert first.status_code == 201
    eid = first.json()["id"]

    replay = patient.post("/emergencies", json={"blood_type": "O_NEG", "idempotency_key": key})
    assert replay.status_code == 200  # not 201 -- nothing new was created
    assert replay.json()["id"] == eid

    # Only one row actually exists.
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        count = db.scalar(
            select(Emergency).where(Emergency.idempotency_key == key)
        )
        assert count is not None
        rows = db.scalars(select(Emergency).where(Emergency.idempotency_key == key)).all()
        assert len(rows) == 1
    finally:
        db.close()


def test_different_idempotency_keys_create_separate_emergencies() -> None:
    patient, _ = auth_client("PATIENT")
    a = patient.post("/emergencies", json={"blood_type": "O_NEG", "idempotency_key": "key-a"})
    b = patient.post("/emergencies", json={"blood_type": "O_NEG", "idempotency_key": "key-b"})
    assert a.status_code == 201 and b.status_code == 201
    assert a.json()["id"] != b.json()["id"]


def test_same_key_from_a_different_actor_is_a_separate_emergency() -> None:
    """The idempotency scope is (actor, key) -- two different users
    coincidentally generating the same client-side key must not collide."""
    p1, _ = auth_client("PATIENT")
    p2, _ = auth_client("PATIENT")
    key = "shared-by-coincidence"
    a = p1.post("/emergencies", json={"blood_type": "O_NEG", "idempotency_key": key})
    b = p2.post("/emergencies", json={"blood_type": "O_NEG", "idempotency_key": key})
    assert a.status_code == 201 and b.status_code == 201
    assert a.json()["id"] != b.json()["id"]


def test_concurrent_identical_idempotency_key_requests_create_exactly_one_emergency() -> None:
    patient, _ = auth_client("PATIENT")
    key = str(uuid.uuid4())
    results: list[dict] = []
    barrier = threading.Barrier(2)

    def _create() -> None:
        barrier.wait()
        r = patient.post("/emergencies", json={"blood_type": "O_NEG", "idempotency_key": key})
        results.append({"status": r.status_code, "id": r.json()["id"]})

    threads = [threading.Thread(target=_create) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(r["status"] for r in results) == [200, 201]
    assert results[0]["id"] == results[1]["id"]


# --------------------------------------------------------------------------- #
# F. Non-blocking duplicate detection
# --------------------------------------------------------------------------- #
def test_second_open_emergency_for_same_patient_is_flagged_but_not_blocked() -> None:
    patient, _ = auth_client("PATIENT")
    first = patient.post("/emergencies", json={"blood_type": "O_NEG"})
    assert first.json()["possible_duplicate_of"] is None

    second = patient.post("/emergencies", json={"blood_type": "A_POS"})
    assert second.status_code == 201  # never blocked
    assert second.json()["possible_duplicate_of"] == first.json()["id"]


def test_duplicate_flag_is_absent_once_the_first_is_closed() -> None:
    patient, _ = auth_client("PATIENT")
    first = patient.post("/emergencies", json={"blood_type": "O_NEG"})
    patient.post(f"/emergencies/{first.json()['id']}/cancel", json={})

    second = patient.post("/emergencies", json={"blood_type": "O_NEG"})
    assert second.json()["possible_duplicate_of"] is None


# --------------------------------------------------------------------------- #
# Target facility validation
# --------------------------------------------------------------------------- #
def test_target_facility_must_exist_and_be_verified(db_session) -> None:
    patient, _ = auth_client("PATIENT")

    missing = patient.post("/emergencies", json={
        "blood_type": "O_NEG", "target_facility_id": str(uuid.uuid4()),
    })
    assert missing.status_code == 400

    _, facility = _facility("Metro", 10.0, 10.0, "Unverified Hosp", verified=False)
    unverified = patient.post("/emergencies", json={
        "blood_type": "O_NEG", "target_facility_id": facility["id"],
    })
    assert unverified.status_code == 400

    _verify_facility(db_session, facility["id"])
    ok = patient.post("/emergencies", json={
        "blood_type": "O_NEG", "target_facility_id": facility["id"],
    })
    assert ok.status_code == 201
    assert ok.json()["target_facility_id"] == facility["id"]


def test_target_facility_is_optional() -> None:
    patient, _ = auth_client("PATIENT")
    r = patient.post("/emergencies", json={"blood_type": "O_NEG"})
    assert r.status_code == 201
    assert r.json()["target_facility_id"] is None


# --------------------------------------------------------------------------- #
# H. Invalid transitions rejected, allowed list included
# --------------------------------------------------------------------------- #
def test_cannot_acknowledge_an_already_matching_emergency() -> None:
    patient, _ = auth_client("PATIENT")
    donor, _ = _ready_donor("O_NEG")
    eid = patient.post("/emergencies", json={"blood_type": "O_NEG"}).json()["id"]
    donor.post(f"/emergencies/{eid}/respond", json={"action": "ACCEPTED"})  # -> MATCHING

    second_donor, _ = _ready_donor("O_NEG")
    r = second_donor.post(f"/emergencies/{eid}/acknowledge", json={})
    assert r.status_code == 409


def test_cannot_cancel_a_resolved_emergency() -> None:
    patient, _ = auth_client("PATIENT")
    eid = patient.post("/emergencies", json={"blood_type": "O_NEG"}).json()["id"]
    patient.post(f"/emergencies/{eid}/resolve", json={})
    r = patient.post(f"/emergencies/{eid}/cancel", json={"note": "too late"})
    assert r.status_code == 409


def test_cannot_resolve_a_cancelled_emergency() -> None:
    patient, _ = auth_client("PATIENT")
    eid = patient.post("/emergencies", json={"blood_type": "O_NEG"}).json()["id"]
    patient.post(f"/emergencies/{eid}/cancel", json={})
    r = patient.post(f"/emergencies/{eid}/resolve", json={})
    assert r.status_code == 409


# --------------------------------------------------------------------------- #
# I. Row-locked concurrency: two managers cancel/resolve the same emergency
# --------------------------------------------------------------------------- #
def test_concurrent_cancel_and_resolve_only_one_wins() -> None:
    family, fam_email = auth_client("FAMILY")
    patient, pat_email = auth_client("PATIENT")
    link_id = family.post(
        "/family/links", json={"patient_email": pat_email, "relationship": "daughter"}
    ).json()["id"]
    patient.post(f"/family/links/{link_id}/accept", json={})

    eid = patient.post("/emergencies", json={"blood_type": "O_NEG"}).json()["id"]

    results: list[int] = []
    barrier = threading.Barrier(2)

    def _cancel() -> None:
        barrier.wait()
        results.append(patient.post(f"/emergencies/{eid}/cancel", json={}).status_code)

    def _resolve() -> None:
        barrier.wait()
        results.append(family.post(f"/emergencies/{eid}/resolve", json={}).status_code)

    threads = [threading.Thread(target=_cancel), threading.Thread(target=_resolve)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == [200, 409]
    final_status = patient.get(f"/emergencies/{eid}").json()["status"]
    assert final_status in ("CANCELLED", "RESOLVED")  # exactly one transition won, never both


# --------------------------------------------------------------------------- #
# Inventory reservation tied to a real HOSPITAL responder accepting
# --------------------------------------------------------------------------- #
def test_hospital_accept_reserves_inventory_and_cancel_releases_it(db_session) -> None:
    patient, _ = auth_client("PATIENT")
    hosp_client, facility = _facility("Metro", 10.0, 10.0, "Reserve Test Hosp")
    _stock(db_session, facility["id"], "O_NEG", 5)

    eid = patient.post("/emergencies", json={
        "blood_type": "O_NEG", "units_needed": 2,
    }).json()["id"]

    r = hosp_client.post(f"/emergencies/{eid}/respond", json={"action": "ACCEPTED"})
    assert r.status_code == 200
    assert "Reserved 2 O_NEG" in r.json()["inventory_note"]

    inv = hosp_client.get("/hospitals/me/inventory").json()
    row = next(i for i in inv if i["blood_type"] == "O_NEG")
    assert row["reserved_units"] == 2

    cancel = patient.post(f"/emergencies/{eid}/cancel", json={})
    assert cancel.status_code == 200
    inv_after = hosp_client.get("/hospitals/me/inventory").json()
    row_after = next(i for i in inv_after if i["blood_type"] == "O_NEG")
    assert row_after["reserved_units"] == 0  # released, not left dangling


def test_hospital_withdrawal_releases_its_own_reservation_only(db_session) -> None:
    patient, _ = auth_client("PATIENT")
    hosp_a, facility_a = _facility("Metro", 10.0, 10.0, "Hosp A Reserve")
    hosp_b, facility_b = _facility("Metro", 10.1, 10.1, "Hosp B Reserve")
    _stock(db_session, facility_a["id"], "O_NEG", 5)
    _stock(db_session, facility_b["id"], "O_NEG", 5)

    eid = patient.post("/emergencies", json={"blood_type": "O_NEG", "units_needed": 1}).json()["id"]
    hosp_a.post(f"/emergencies/{eid}/respond", json={"action": "ACCEPTED"})
    hosp_b.post(f"/emergencies/{eid}/respond", json={"action": "ACCEPTED"})

    # Both reserved independently.
    assert hosp_a.get("/hospitals/me/inventory").json()[
        [i["blood_type"] for i in hosp_a.get("/hospitals/me/inventory").json()].index("O_NEG")
    ]["reserved_units"] == 1

    hosp_a.post(f"/emergencies/{eid}/respond", json={"action": "DECLINED"})
    inv_a_all = hosp_a.get("/hospitals/me/inventory").json()
    inv_b_all = hosp_b.get("/hospitals/me/inventory").json()
    inv_a = next(i for i in inv_a_all if i["blood_type"] == "O_NEG")
    inv_b = next(i for i in inv_b_all if i["blood_type"] == "O_NEG")
    assert inv_a["reserved_units"] == 0  # A withdrew, A's reservation released
    assert inv_b["reserved_units"] == 1  # B never touched


def test_reservation_failure_is_reported_honestly_not_silently_succeeded(db_session) -> None:
    patient, _ = auth_client("PATIENT")
    hosp_client, facility = _facility("Metro", 10.0, 10.0, "Insufficient Stock Hosp")
    _stock(db_session, facility["id"], "O_NEG", 1)  # only 1 on hand

    eid = patient.post("/emergencies", json={
        "blood_type": "O_NEG", "units_needed": 5,  # more than available
    }).json()["id"]

    r = hosp_client.post(f"/emergencies/{eid}/respond", json={"action": "ACCEPTED"})
    assert r.status_code == 200  # the accept itself still succeeds
    assert "Could not reserve" in r.json()["inventory_note"]  # but never claims success
    inv_all = hosp_client.get("/hospitals/me/inventory").json()
    inv = next(i for i in inv_all if i["blood_type"] == "O_NEG")
    assert inv["reserved_units"] == 0  # nothing was actually reserved


# --------------------------------------------------------------------------- #
# Pathway versioning + real transport-disruption re-optimization
# --------------------------------------------------------------------------- #
def test_selecting_a_pathway_creates_version_one() -> None:
    patient, _ = auth_client("PATIENT")
    admin, _ = auth_client("ADMIN")
    origin_client, origin = _facility("Chennai", 13.08, 80.27, "Pathway Origin")

    eid = patient.post("/emergencies", json={"blood_type": "B_POS"}).json()["id"]
    r = origin_client.post(f"/emergencies/{eid}/pathway", json={"origin_facility_id": origin["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 1
    assert r.json()["status"] == "ACTIVE"

    detail = patient.get(f"/emergencies/{eid}").json()
    assert detail["current_pathway"]["version"] == 1
    assert len(detail["pathway_history"]) == 1


def test_manual_reselection_creates_version_two_and_supersedes_version_one() -> None:
    patient, _ = auth_client("PATIENT")
    origin_client, origin = _facility("Chennai", 13.08, 80.27, "Pathway Origin 2")
    eid = patient.post("/emergencies", json={"blood_type": "B_POS"}).json()["id"]

    origin_client.post(f"/emergencies/{eid}/pathway", json={"origin_facility_id": origin["id"]})
    second = origin_client.post(
        f"/emergencies/{eid}/pathway", json={"origin_facility_id": origin["id"]}
    )
    assert second.json()["version"] == 2

    detail = patient.get(f"/emergencies/{eid}").json()
    assert detail["current_pathway"]["version"] == 2
    versions = {p["version"]: p["status"] for p in detail["pathway_history"]}
    assert versions == {1: "SUPERSEDED", 2: "ACTIVE"}


def test_transport_disruption_reoptimizes_affected_emergency_pathway(db_session) -> None:
    admin, _ = auth_client("ADMIN")
    patient, _ = auth_client("PATIENT")
    origin_client, origin = _facility("Chennai", 13.0827, 80.2707, "Disrupt Origin")
    dest_client, dest = _facility("Vellore", 12.9165, 79.1325, "Disrupt Dest")
    _stock(db_session, dest["id"], "B_POS", 4)

    route = admin.post("/admin/transport-routes", json={
        "origin_facility_id": origin["id"], "destination_facility_id": dest["id"],
    })
    assert route.status_code == 201
    route_id = route.json()["id"]

    eid = patient.post("/emergencies", json={"blood_type": "B_POS"}).json()["id"]
    selected = origin_client.post(
        f"/emergencies/{eid}/pathway", json={"origin_facility_id": origin["id"]}
    )
    assert selected.json()["selected_facility_id"] == dest["id"]
    assert selected.json()["version"] == 1

    disrupt = admin.post(
        f"/admin/transport-routes/{route_id}/disrupt", json={"reason": "flooded bridge"}
    )
    assert disrupt.status_code == 200, disrupt.text
    affected = disrupt.json()["affected_emergencies"]
    assert len(affected) == 1
    assert affected[0]["emergency_id"] == eid
    assert affected[0]["previous_version"] == 1
    assert affected[0]["new_version"] == 2
    # No backup existed (one source only), so a fresh plan was made.
    assert affected[0]["kind"] == "REPLANNED"
    # The only route to the facility with stock is now disrupted, and both
    # facilities are on the transport network -- honestly unreachable, not a
    # fabricated "still fine" and not a straight-line guess.
    assert affected[0]["still_reachable"] is False

    detail = patient.get(f"/emergencies/{eid}").json()
    assert detail["current_pathway"]["version"] == 2
    assert detail["current_pathway"]["selected_facility_id"] is None
    assert detail["current_pathway"]["feasibility"] == "IMPOSSIBLE"
    versions = {p["version"]: p["status"] for p in detail["pathway_history"]}
    # Gap elimination: a pathway killed by a failure is INVALIDATED (with
    # the reason), distinct from one merely replaced by a newer plan.
    assert versions == {1: "INVALIDATED", 2: "ACTIVE"}
    v1 = next(p for p in detail["pathway_history"] if p["version"] == 1)
    assert route_id in v1["invalidated_reason"]

    events = [e["event_type"] for e in detail["events"]]
    assert "transport_disrupted" in events
    assert "pathway_reoptimized" in events

    notif_types = {n["type"] for n in patient.get("/notifications").json()}
    assert "emergency.pathway_reoptimized" in notif_types


def test_disruption_of_an_unrelated_route_does_not_touch_this_emergency() -> None:
    admin, _ = auth_client("ADMIN")
    patient, _ = auth_client("PATIENT")
    origin_client, origin = _facility("Chennai", 13.0827, 80.2707, "Unrelated Origin")
    dest_client, dest = _facility("Vellore", 12.9165, 79.1325, "Unrelated Dest")
    _, other_a = _facility("Pune", 18.52, 73.85, "Other Route A")
    _, other_b = _facility("Mumbai", 19.07, 72.87, "Other Route B")

    eid = patient.post("/emergencies", json={"blood_type": "AB_NEG"}).json()["id"]
    origin_client.post(f"/emergencies/{eid}/pathway", json={"origin_facility_id": origin["id"]})

    unrelated_route = admin.post("/admin/transport-routes", json={
        "origin_facility_id": other_a["id"], "destination_facility_id": other_b["id"],
    }).json()
    disrupt = admin.post(
        f"/admin/transport-routes/{unrelated_route['id']}/disrupt", json={"reason": "unrelated"}
    )
    assert disrupt.json()["affected_emergencies"] == []

    detail = patient.get(f"/emergencies/{eid}").json()
    assert detail["current_pathway"]["version"] == 1  # untouched


def test_pathway_endpoint_requires_hospital_or_admin_role() -> None:
    patient, _ = auth_client("PATIENT")
    donor, _ = _ready_donor("O_NEG")
    _, facility = _facility("Metro", 10.0, 10.0, "Role Check Hosp")
    eid = patient.post("/emergencies", json={"blood_type": "O_NEG"}).json()["id"]

    r = donor.post(f"/emergencies/{eid}/pathway", json={"origin_facility_id": facility["id"]})
    assert r.status_code == 403


def test_pathway_endpoint_requires_owning_facility_for_hospital_caller() -> None:
    patient, _ = auth_client("PATIENT")
    owner, owner_facility = _facility("Metro", 10.0, 10.0, "Pathway Owner")
    other, _ = _facility("Elsewhere", 11.0, 11.0, "Pathway Not Owner")
    eid = patient.post("/emergencies", json={"blood_type": "O_NEG"}).json()["id"]

    r = other.post(
        f"/emergencies/{eid}/pathway", json={"origin_facility_id": owner_facility["id"]}
    )
    assert r.status_code == 403


# --------------------------------------------------------------------------- #
# Full end-to-end scenarios (spec section 28): every subsystem touched by one
# continuous, realistic chain of events -- lifecycle, inventory, pathway
# versioning, a real transport disruption + re-optimization, and honest
# resolution -- plus a separate cancellation scenario proving resources are
# released, never left dangling.
# --------------------------------------------------------------------------- #
def test_full_scenario_create_through_disruption_to_resolution(db_session) -> None:
    patient, _ = auth_client("PATIENT")
    admin, _ = auth_client("ADMIN")
    donor, _ = _ready_donor("O_NEG")
    hosp_client, hosp = _facility("Chennai", 13.0827, 80.2707, "E2E Responder Hospital")
    dest_client, dest = _facility("Vellore", 12.9165, 79.1325, "E2E Distant Facility")
    _stock(db_session, dest["id"], "O_NEG", 10)

    # 1. Create -- idempotency key present (as the real SOS button always
    # sends one), deadline in the future, a real unit count.
    key = str(uuid.uuid4())
    created = patient.post("/emergencies", json={
        "blood_type": "O_NEG", "location_text": "Chennai ER", "units_needed": 2,
        "deadline": "2099-01-01T00:00:00Z", "idempotency_key": key,
    })
    assert created.status_code == 201
    body = created.json()
    eid = body["id"]
    assert body["status"] == "ACTIVE"
    assert body["slack_seconds"] > 0
    assert set(body["allowed_transitions"]) == {
        "ACKNOWLEDGED", "ASSISTANCE_FOUND", "CANCELLED", "MATCHING", "RESOLVED",
    }

    # A retried submit (double-click) with the same key must not create a
    # second emergency.
    replay = patient.post("/emergencies", json={
        "blood_type": "O_NEG", "units_needed": 2, "idempotency_key": key,
    })
    assert replay.status_code == 200 and replay.json()["id"] == eid

    # 2. Acknowledge -> ACKNOWLEDGED.
    assert donor.post(f"/emergencies/{eid}/acknowledge", json={}).status_code == 200

    # 3. A hospital responder accepts -> MATCHING, and a real inventory
    # reservation is made against the hospital's own stock.
    _stock(db_session, hosp["id"], "O_NEG", 5)
    accept = hosp_client.post(f"/emergencies/{eid}/respond", json={"action": "ACCEPTED"})
    assert accept.status_code == 200
    assert "Reserved" in (accept.json()["inventory_note"] or "")
    assert patient.get(f"/emergencies/{eid}").json()["status"] == "MATCHING"
    inv = next(
        i for i in hosp_client.get("/hospitals/me/inventory").json() if i["blood_type"] == "O_NEG"
    )
    assert inv["reserved_units"] == 2

    # 4. The receiving ER (the delivery destination, no stock of its own)
    # plans a real resource pathway. It is connected to the responding
    # hospital by a short route and to the distant facility by a long one,
    # so the CRITICAL strategy picks the responding hospital (fastest) as
    # the primary and stores the distant facility as a route-disjoint
    # backup.
    er_client, er = _facility("Chennai", 13.0700, 80.2600, "E2E Receiving ER")
    short_route = admin.post("/admin/transport-routes", json={
        "origin_facility_id": er["id"], "destination_facility_id": hosp["id"],
    }).json()
    long_route = admin.post("/admin/transport-routes", json={
        "origin_facility_id": er["id"], "destination_facility_id": dest["id"],
    }).json()
    pathway1 = er_client.post(
        f"/emergencies/{eid}/pathway", json={"origin_facility_id": er["id"]}
    )
    assert pathway1.status_code == 200, pathway1.text
    assert pathway1.json()["version"] == 1
    assert pathway1.json()["selected_facility_id"] == hosp["id"]
    stored = {p["version"]: p for p in er_client.get(f"/emergencies/{eid}/pathways").json()}
    assert stored[1]["legs"][0]["steps"][0]["route_id"] == short_route["id"]
    assert stored[2]["status"] == "BACKUP"
    assert stored[2]["legs"][0]["source_facility_id"] == dest["id"]
    assert stored[2]["legs"][0]["steps"][0]["route_id"] == long_route["id"]

    # 5. Assistance found -> ASSISTANCE_FOUND.
    assert hosp_client.post(f"/emergencies/{eid}/assistance-found", json={}).status_code == 200

    # 6. A real transport disruption on the route the ACTIVE pathway
    # actually uses: the backup is re-validated and promoted (no fresh plan
    # needed); the failed pathway is INVALIDATED with the reason.
    disrupt = admin.post(
        f"/admin/transport-routes/{short_route['id']}/disrupt",
        json={"reason": "e2e scenario: road closure"},
    )
    assert disrupt.status_code == 200
    affected = disrupt.json()["affected_emergencies"]
    assert any(a["emergency_id"] == eid for a in affected)
    reopt = next(a for a in affected if a["emergency_id"] == eid)
    assert reopt["previous_version"] == 1 and reopt["new_version"] == 2
    assert reopt["kind"] == "BACKUP_PROMOTED" and reopt["still_reachable"] is True

    detail_after_disruption = patient.get(f"/emergencies/{eid}").json()
    assert detail_after_disruption["current_pathway"]["version"] == 2
    assert detail_after_disruption["current_pathway"]["selected_facility_id"] == dest["id"]
    versions = {p["version"]: p["status"] for p in detail_after_disruption["pathway_history"]}
    assert versions == {1: "INVALIDATED", 2: "ACTIVE"}
    event_types = [e["event_type"] for e in detail_after_disruption["events"]]
    assert "transport_disrupted" in event_types and "pathway_reoptimized" in event_types
    assert "backup_promoted" in event_types
    notif_types = {n["type"] for n in patient.get("/notifications").json()}
    assert "emergency.pathway_reoptimized" in notif_types

    # 7. Resolve -> RESOLVED, terminal (no further transitions), inventory
    # reservation still intact (resolution is a successful outcome, not a
    # release -- the units were actually used).
    resolved = patient.post(f"/emergencies/{eid}/resolve", json={})
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "RESOLVED"
    assert resolved.json()["allowed_transitions"] == []
    assert resolved.json()["resolved_at"] is not None
    # Base-lifecycle event ordering (created/acknowledged/.../resolved) is
    # already pinned exactly in test_full_sos_lifecycle_to_resolved; this
    # scenario only needs to confirm the whole chain reached RESOLVED with
    # the Phase 12 pathway events present somewhere in between (already
    # asserted above), so it isn't re-pinned here.
    final_events = [e["event_type"] for e in patient.get(f"/emergencies/{eid}").json()["events"]]
    assert "created" in final_events and "resolved" in final_events
    assert final_events.index("created") < final_events.index("resolved")

    # A second resolve attempt on a terminal emergency is correctly rejected.
    assert patient.post(f"/emergencies/{eid}/resolve", json={}).status_code == 409


def test_full_scenario_cancellation_releases_reservation_and_pathway_stays_auditable(
    db_session,
) -> None:
    patient, _ = auth_client("PATIENT")
    hosp_client, hosp = _facility("Metro", 10.0, 10.0, "E2E Cancel Hospital")
    _stock(db_session, hosp["id"], "O_NEG", 4)

    eid = patient.post("/emergencies", json={
        "blood_type": "O_NEG", "units_needed": 3,
    }).json()["id"]

    accept = hosp_client.post(f"/emergencies/{eid}/respond", json={"action": "ACCEPTED"})
    assert accept.status_code == 200
    inv = next(
        i for i in hosp_client.get("/hospitals/me/inventory").json() if i["blood_type"] == "O_NEG"
    )
    assert inv["reserved_units"] == 3

    pathway = hosp_client.post(
        f"/emergencies/{eid}/pathway", json={"origin_facility_id": hosp["id"]}
    )
    assert pathway.status_code == 200

    cancelled = patient.post(f"/emergencies/{eid}/cancel", json={"note": "resolved by other means"})
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"
    assert cancelled.json()["allowed_transitions"] == []

    # The reservation is released -- never left dangling against a cancelled
    # emergency -- but the pathway history is untouched (cancellation is not
    # a data-deleting operation).
    inv_after = next(
        i for i in hosp_client.get("/hospitals/me/inventory").json() if i["blood_type"] == "O_NEG"
    )
    assert inv_after["reserved_units"] == 0
    detail = patient.get(f"/emergencies/{eid}").json()
    assert detail["current_pathway"]["version"] == 1  # still there, just historical now
    assert detail["status"] == "CANCELLED"

    # A cancelled emergency cannot be reopened via any transition.
    assert patient.post(f"/emergencies/{eid}/resolve", json={}).status_code == 409
    respond_after_cancel = hosp_client.post(
        f"/emergencies/{eid}/respond", json={"action": "ACCEPTED"}
    )
    assert respond_after_cancel.status_code == 404
