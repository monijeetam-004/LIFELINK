from __future__ import annotations

from tests.helpers import auth_client


def _hospital(city="Metro", name="St Jude", lat=40.0, lng=-73.0):
    c, _ = auth_client("HOSPITAL")
    c.put("/hospitals/me", json={"name": name, "city": city, "latitude": lat, "longitude": lng})
    return c


def test_hospital_profile_autocreate_and_update() -> None:
    c, _ = auth_client("HOSPITAL")
    r = c.get("/hospitals/me")
    assert r.status_code == 200 and r.json()["name"] is None
    r = c.put("/hospitals/me", json={"name": "St Jude", "city": "Metro", "license_no": "LIC-1"})
    assert r.status_code == 200 and r.json()["name"] == "St Jude"
    assert c.get("/hospitals/me").json()["city"] == "Metro"


def test_hospital_role_isolation() -> None:
    patient, _ = auth_client("PATIENT")
    donor, _ = auth_client("DONOR")
    assert patient.get("/hospitals/me").status_code == 403
    assert donor.get("/hospitals/me").status_code == 403


def test_inventory_defaults_update_and_persist() -> None:
    c = _hospital()
    inv = c.get("/hospitals/me/inventory").json()
    assert len(inv) == 8 and all(i["units"] == 0 for i in inv)

    r = c.put("/hospitals/me/inventory", json={"items": [
        {"blood_type": "O_NEG", "units": 12}, {"blood_type": "A_POS", "units": 40},
    ]})
    assert r.status_code == 200
    got = {i["blood_type"]: i["units"] for i in c.get("/hospitals/me/inventory").json()}
    assert got["O_NEG"] == 12 and got["A_POS"] == 40 and got["B_POS"] == 0

    # update existing row
    c.put("/hospitals/me/inventory", json={"items": [{"blood_type": "O_NEG", "units": 5}]})
    got = {i["blood_type"]: i["units"] for i in c.get("/hospitals/me/inventory").json()}
    assert got["O_NEG"] == 5


def test_incoming_requests_scoped_by_city_and_respond_flow() -> None:
    hosp = _hospital(city="Metro", name="St Jude")
    other = _hospital(city="Farville", name="Far Clinic")
    patient, _ = auth_client("PATIENT")

    rid = patient.post("/blood-requests", json={
        "blood_type": "O_NEG", "city": "Metro", "hospital_name": "St Jude", "urgency": "URGENT",
    }).json()["id"]

    seen = hosp.get("/hospitals/me/requests").json()
    assert any(x["id"] == rid for x in seen)
    assert all(x["id"] != rid for x in other.get("/hospitals/me/requests").json())

    # hospital was notified
    assert any(n["type"] == "blood_request.incoming" for n in hosp.get("/notifications").json())

    r = hosp.post(f"/hospitals/me/requests/{rid}/respond",
                  json={"status": "PREPARING", "note": "Cross-matching 2 units"})
    assert r.status_code == 200 and r.json()["status"] == "PREPARING"

    # reflected on the hospital's request list
    item = next(x for x in hosp.get("/hospitals/me/requests").json() if x["id"] == rid)
    assert item["hospital_status"] == "PREPARING"
    # patient-filed requests carry no coordinates, so no distance is computed
    assert item["distance_km"] is None

    # requester notified + event recorded
    assert any(n["type"] == "blood_request.hospital_update"
               for n in patient.get("/notifications").json())
    evts = [e["event_type"] for e in patient.get(f"/blood-requests/{rid}").json()["events"]]
    assert "hospital_preparing" in evts


def test_hospital_sees_request_detail_without_donor_identities() -> None:
    hosp = _hospital(city="Metro")
    patient, _ = auth_client("PATIENT")
    donor, _ = auth_client("DONOR")
    donor.put("/donors/me", json={"blood_type": "O_NEG"})
    rid = patient.post(
        "/blood-requests", json={"blood_type": "A_POS", "city": "Metro"}
    ).json()["id"]
    donor.post(f"/blood-requests/{rid}/respond", json={"action": "ACCEPTED"})

    det = hosp.get(f"/blood-requests/{rid}").json()
    assert det["accepted_count"] == 1          # aggregate visible
    assert det["responses"] == []             # donor identities hidden from hospital


def test_hospital_can_create_emergency_and_see_city_emergencies() -> None:
    hosp = _hospital(city="Metro")
    patient, _ = auth_client("PATIENT")
    patient.post("/emergencies", json={"blood_type": "O_NEG", "location_text": "Metro General ER"})
    assert len(hosp.get("/hospitals/me/emergencies").json()) == 1

    r = hosp.post("/emergencies", json={"note": "Mass casualty", "location_text": "Metro"})
    assert r.status_code == 201


# --------------------------------------------------------------------------- #
# Phase 5 -- inventory actions (add/reserve/allocate/release/expire/reconcile)
# --------------------------------------------------------------------------- #


def _units(c, blood_type="O_NEG") -> dict:
    row = next(i for i in c.get("/hospitals/me/inventory").json() if i["blood_type"] == blood_type)
    return row


def test_inventory_add_reserve_allocate_release_cycle() -> None:
    c = _hospital()

    r = c.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 20})
    assert r.status_code == 200
    row = r.json()
    assert row["units"] == 20 and row["reserved_units"] == 0 and row["available_units"] == 20

    r = c.post("/hospitals/me/inventory/reserve", json={"blood_type": "O_NEG", "units": 5})
    row = r.json()
    assert row["units"] == 20 and row["reserved_units"] == 5 and row["available_units"] == 15

    # Releasing gives the reservation back without touching on-hand stock.
    r = c.post("/hospitals/me/inventory/release", json={"blood_type": "O_NEG", "units": 5})
    row = r.json()
    assert row["units"] == 20 and row["reserved_units"] == 0 and row["available_units"] == 20

    # Allocate straight from on-hand stock (no prior reservation needed).
    r = c.post("/hospitals/me/inventory/allocate", json={"blood_type": "O_NEG", "units": 8})
    row = r.json()
    assert row["units"] == 12 and row["reserved_units"] == 0 and row["available_units"] == 12

    # Allocate again, this time against a real reservation -- the matching
    # reservation is released automatically as part of the allocation.
    c.post("/hospitals/me/inventory/reserve", json={"blood_type": "O_NEG", "units": 4})
    r = c.post("/hospitals/me/inventory/allocate", json={"blood_type": "O_NEG", "units": 4})
    row = r.json()
    assert row["units"] == 8 and row["reserved_units"] == 0

    r = c.post("/hospitals/me/inventory/expire", json={"blood_type": "O_NEG", "units": 3})
    row = r.json()
    assert row["units"] == 5

    r = c.post("/hospitals/me/inventory/reconcile", json={"blood_type": "O_NEG", "units": 50})
    row = r.json()
    assert row["units"] == 50

    log = c.get("/hospitals/me/inventory/log").json()
    actions = [entry["action"] for entry in log]
    # Newest first; every action performed above is present.
    expected_actions = (
        "RECONCILE", "EXPIRE", "ALLOCATE", "RESERVE", "ALLOCATE", "RELEASE", "RESERVE", "ADD",
    )
    for expected in expected_actions:
        assert expected in actions


def test_inventory_never_allows_negative_or_over_reservation() -> None:
    c = _hospital()
    c.post("/hospitals/me/inventory/add", json={"blood_type": "AB_NEG", "units": 5})

    # Reserve more than on hand.
    r = c.post("/hospitals/me/inventory/reserve", json={"blood_type": "AB_NEG", "units": 10})
    assert r.status_code == 409

    # Allocate more than on hand.
    r = c.post("/hospitals/me/inventory/allocate", json={"blood_type": "AB_NEG", "units": 10})
    assert r.status_code == 409

    # Reserve within bounds, then try to allocate/expire more than what's
    # still unreserved.
    c.post("/hospitals/me/inventory/reserve", json={"blood_type": "AB_NEG", "units": 5})
    r = c.post("/hospitals/me/inventory/expire", json={"blood_type": "AB_NEG", "units": 1})
    assert r.status_code == 409  # all 5 units are reserved -- none are free to expire

    # Release more than is reserved.
    r = c.post("/hospitals/me/inventory/release", json={"blood_type": "AB_NEG", "units": 99})
    assert r.status_code == 409

    # Reconcile below the current reservation total.
    r = c.post("/hospitals/me/inventory/reconcile", json={"blood_type": "AB_NEG", "units": 2})
    assert r.status_code == 409

    # A non-positive delta is rejected at the schema level, never silently
    # accepted as a no-op or a negative adjustment.
    r = c.post("/hospitals/me/inventory/add", json={"blood_type": "AB_NEG", "units": 0})
    assert r.status_code == 422
    r = c.post("/hospitals/me/inventory/add", json={"blood_type": "AB_NEG", "units": -1})
    assert r.status_code == 422

    # Inventory never went negative at any point.
    assert _units(c, "AB_NEG")["units"] >= 0
    assert _units(c, "AB_NEG")["reserved_units"] >= 0


# --------------------------------------------------------------------------- #
# Phase 5 -- hospital allocate/fulfill against a real incoming request
# --------------------------------------------------------------------------- #


def test_hospital_allocate_and_fulfill_request() -> None:
    hosp = _hospital(city="Metro", name="St Jude")
    hosp.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 10})
    patient, _ = auth_client("PATIENT")
    rid = patient.post("/blood-requests", json={
        "blood_type": "O_NEG", "city": "Metro", "hospital_name": "St Jude", "units_needed": 2,
    }).json()["id"]

    r = hosp.post(f"/hospitals/me/requests/{rid}/allocate",
                  json={"blood_type": "O_NEG", "units": 2, "note": "2 units cross-matched"})
    assert r.status_code == 200
    assert r.json()["status"] == "ALLOCATED"
    assert r.json()["units_allocated"] == 2
    assert _units(hosp, "O_NEG")["units"] == 8

    evts = [e["event_type"] for e in patient.get(f"/blood-requests/{rid}").json()["events"]]
    assert "hospital_allocated" in evts
    assert any(n["type"] == "blood_request.hospital_update"
               for n in patient.get("/notifications").json())

    # Phase E: allocating the FULL requested quantity (2/2) now carries the
    # shared BloodRequestStatus to FULFILLED by itself -- allocate is
    # quantity-aware (app.core.request_fulfillment.target_status_after_allocation),
    # so a separate explicit "/fulfill" call is no longer needed (or
    # possible) once the request is already fully covered. This replaces
    # the pre-Phase-E behavior where ALLOCATED left the shared status
    # untouched until a second, manual "/fulfill" call.
    detail = patient.get(f"/blood-requests/{rid}").json()
    assert detail["status"] == "FULFILLED"
    assert detail["fulfilled_units"] == 2

    # The explicit endpoint correctly refuses to "fulfill" what the
    # quantity model already completed -- not a new gap, the same
    # already-terminal guard every other transition in this request's
    # lifecycle enforces.
    r = hosp.post(f"/hospitals/me/requests/{rid}/fulfill")
    assert r.status_code == 409
    r = hosp.post(
        f"/hospitals/me/requests/{rid}/allocate", json={"blood_type": "O_NEG", "units": 1}
    )
    assert r.status_code == 409


def test_hospital_allocate_rejects_wrong_blood_type_and_insufficient_stock() -> None:
    hosp = _hospital(city="Metro", name="St Jude")
    hosp.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 1})
    patient, _ = auth_client("PATIENT")
    rid = patient.post("/blood-requests", json={
        "blood_type": "O_NEG", "city": "Metro", "hospital_name": "St Jude",
    }).json()["id"]

    r = hosp.post(f"/hospitals/me/requests/{rid}/allocate",
                  json={"blood_type": "A_POS", "units": 1})
    assert r.status_code == 400

    r = hosp.post(f"/hospitals/me/requests/{rid}/allocate",
                  json={"blood_type": "O_NEG", "units": 5})
    assert r.status_code == 409


def test_respond_endpoint_rejects_allocated_and_fulfilled_status() -> None:
    """ALLOCATED/FULFILLED have real side effects (inventory deduction,
    shared-state-machine transition) -- the free-form /respond endpoint
    must refuse them and point callers at the dedicated endpoints."""
    hosp = _hospital(city="Metro", name="St Jude")
    patient, _ = auth_client("PATIENT")
    rid = patient.post("/blood-requests", json={
        "blood_type": "O_NEG", "city": "Metro", "hospital_name": "St Jude",
    }).json()["id"]

    for bad_status in ("ALLOCATED", "FULFILLED"):
        r = hosp.post(f"/hospitals/me/requests/{rid}/respond", json={"status": bad_status})
        assert r.status_code == 422


# --------------------------------------------------------------------------- #
# Phase 5 -- transport routes + merged activity feed
# --------------------------------------------------------------------------- #


def test_hospital_sees_its_own_transport_routes() -> None:
    admin, _ = auth_client("ADMIN")
    a = _hospital(city="Metro", name="Hospital A")
    b = _hospital(city="Farville", name="Hospital B")
    a_id = a.get("/hospitals/me").json()["user_id"]
    b_id = b.get("/hospitals/me").json()["user_id"]
    # HospitalProfileOut exposes user_id, not the facility id the route API
    # needs -- fetch the real facility ids from the directory instead.
    directory = {h["name"]: h["id"] for h in admin.get("/hospitals/directory").json()}

    r = admin.post("/admin/transport-routes", json={
        "origin_facility_id": directory["Hospital A"],
        "destination_facility_id": directory["Hospital B"],
    })
    assert r.status_code == 201

    routes = a.get("/hospitals/me/routes").json()
    assert len(routes) == 1
    assert routes[0]["partner_facility_name"] == "Hospital B"
    assert routes[0]["status"] == "OPERATIONAL"

    other = _hospital(city="Nowhere", name="Hospital C")
    assert other.get("/hospitals/me/routes").json() == []
    assert a_id and b_id  # sanity: profiles really did resolve


def test_hospital_activity_feed_merges_inventory_and_request_events() -> None:
    hosp = _hospital(city="Metro", name="St Jude")
    hosp.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 5})
    patient, _ = auth_client("PATIENT")
    patient.post("/blood-requests", json={
        "blood_type": "O_NEG", "city": "Metro", "hospital_name": "St Jude",
    })

    activity = hosp.get("/hospitals/me/activity").json()
    kinds = {a["kind"] for a in activity}
    assert "inventory" in kinds
    assert "request" in kinds
    # newest first
    created = [a["created_at"] for a in activity]
    assert created == sorted(created, reverse=True)
