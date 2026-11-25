from __future__ import annotations

from tests.helpers import auth_client


def test_emergency_lifecycle_and_acknowledge() -> None:
    patient, _ = auth_client("PATIENT")
    donor, _ = auth_client("DONOR")
    donor.put("/donors/me", json={"blood_type": "O_NEG", "city": "Metro"})

    r = patient.post(
        "/emergencies",
        # Audit D4 (2026-10-31): an SOS with no location alerts nobody; the
        # request shares the donor's city, so the donor is in scope.
        json={"blood_type": "A_POS", "location_text": "Metro ER", "city": "Metro",
              "note": "car accident"},
    )
    assert r.status_code == 201, r.text
    eid = r.json()["id"]
    assert r.json()["status"] == "ACTIVE"

    # matching available donor was notified
    assert any(n["type"] == "emergency.new" for n in donor.get("/notifications").json())

    # donor acknowledges -> status + notification to creator
    r = donor.post(f"/emergencies/{eid}/acknowledge", json={"note": "en route"})
    assert r.status_code == 200 and r.json()["status"] == "ACKNOWLEDGED"
    assert any(
        n["type"] == "emergency.acknowledged" for n in patient.get("/notifications").json()
    )

    # creator resolves
    r = patient.post(f"/emergencies/{eid}/resolve", json={})
    assert r.status_code == 200 and r.json()["status"] == "RESOLVED"
    assert r.json()["resolved_at"] is not None

    detail = patient.get(f"/emergencies/{eid}").json()
    assert [e["event_type"] for e in detail["events"]] == ["created", "acknowledged", "resolved"]


def test_only_creator_can_cancel_emergency() -> None:
    patient, _ = auth_client("PATIENT")
    other, _ = auth_client("PATIENT")
    eid = patient.post("/emergencies", json={"note": "help"}).json()["id"]
    assert other.post(f"/emergencies/{eid}/cancel", json={}).status_code == 404
    assert patient.post(f"/emergencies/{eid}/cancel", json={}).status_code == 200


def test_donor_cannot_create_emergency() -> None:
    donor, _ = auth_client("DONOR")
    assert donor.post("/emergencies", json={"note": "x"}).status_code == 403


def test_notifications_read_and_unread_count() -> None:
    patient, pat_email = auth_client("PATIENT")
    donor, _ = auth_client("DONOR")
    # Audit D4 (2026-10-31): blood-request alerts now apply the shared
    # distance policy, so the donor and the requests share a city.
    donor.put("/donors/me", json={"blood_type": "O_NEG", "city": "Metro"})
    patient.post("/blood-requests", json={"blood_type": "A_POS", "city": "Metro"})

    assert donor.get("/notifications/unread-count").json()["unread"] == 1
    nid = donor.get("/notifications").json()[0]["id"]

    r = donor.post(f"/notifications/{nid}/read")
    assert r.status_code == 200 and r.json()["read_at"] is not None
    assert donor.get("/notifications/unread-count").json()["unread"] == 0

    # a second request -> unread again, then read-all
    patient.post("/blood-requests", json={"blood_type": "B_POS", "city": "Metro"})
    assert donor.get("/notifications/unread-count").json()["unread"] == 1
    assert donor.post("/notifications/read-all").json()["unread"] == 0
    assert donor.get("/notifications?unread_only=true").json() == []


def test_cannot_read_someone_elses_notification() -> None:
    a, _ = auth_client("PATIENT")
    donor, _ = auth_client("DONOR")
    donor.put("/donors/me", json={"blood_type": "O_NEG", "city": "Metro"})  # D4: same city
    a.post("/blood-requests", json={"blood_type": "A_POS", "city": "Metro"})
    nid = donor.get("/notifications").json()[0]["id"]
    assert a.post(f"/notifications/{nid}/read").status_code == 404
