from __future__ import annotations

from tests.helpers import auth_client


def _ready_donor(blood_type="O_NEG", city=None):
    c, email = auth_client("DONOR")
    c.put("/donors/me", json={"blood_type": blood_type, "city": city})
    return c, email


def test_patient_creates_and_tracks_request() -> None:
    patient, _ = auth_client("PATIENT")
    r = patient.post(
        "/blood-requests",
        json={"blood_type": "A_POS", "units_needed": 2, "urgency": "URGENT", "city": "Metro"},
    )
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    assert r.json()["status"] == "PENDING"
    assert r.json()["patient_name"] == "Patient User"

    lst = patient.get("/blood-requests").json()
    assert len(lst) == 1 and lst[0]["id"] == rid

    detail = patient.get(f"/blood-requests/{rid}").json()
    assert [e["event_type"] for e in detail["events"]] == ["created"]

    # status filter
    assert patient.get("/blood-requests?status_filter=FULFILLED").json() == []


def test_donor_sees_compatible_request_and_accepts() -> None:
    patient, _ = auth_client("PATIENT")
    donor, _ = _ready_donor("O_NEG")  # universal donor -> compatible with A_POS

    rid = patient.post("/blood-requests", json={"blood_type": "A_POS"}).json()["id"]

    open_reqs = donor.get("/donors/me/requests").json()
    assert any(x["id"] == rid for x in open_reqs)

    r = donor.post(f"/blood-requests/{rid}/respond", json={"action": "ACCEPTED"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ACCEPTED"

    # request advances to MATCHING and requester is notified
    assert patient.get(f"/blood-requests/{rid}").json()["status"] == "MATCHING"
    assert any(
        n["type"] == "blood_request.donor_accepted" for n in patient.get("/notifications").json()
    )

    # manager sees the donor response with identity
    detail = patient.get(f"/blood-requests/{rid}").json()
    assert len(detail["responses"]) == 1
    assert detail["responses"][0]["donor_blood_type"] == "O_NEG"
    assert detail["accepted_count"] == 1


def test_incompatible_donor_cannot_accept() -> None:
    patient, _ = auth_client("PATIENT")
    donor, _ = _ready_donor("A_POS")  # A_POS cannot donate to O_NEG
    rid = patient.post("/blood-requests", json={"blood_type": "O_NEG"}).json()["id"]
    # not even shown as open to this donor
    assert all(x["id"] != rid for x in donor.get("/donors/me/requests").json())
    r = donor.post(f"/blood-requests/{rid}/respond", json={"action": "ACCEPTED"})
    assert r.status_code == 400


def test_donor_without_blood_type_cannot_respond() -> None:
    patient, _ = auth_client("PATIENT")
    donor, _ = auth_client("DONOR")  # no blood type set
    rid = patient.post("/blood-requests", json={"blood_type": "O_NEG"}).json()["id"]
    r = donor.post(f"/blood-requests/{rid}/respond", json={"action": "ACCEPTED"})
    assert r.status_code == 400


def test_fulfill_records_donation_history() -> None:
    patient, _ = auth_client("PATIENT")
    donor, _ = _ready_donor("O_NEG")
    rid = patient.post("/blood-requests", json={"blood_type": "O_POS"}).json()["id"]
    donor.post(f"/blood-requests/{rid}/respond", json={"action": "ACCEPTED"})

    r = patient.post(f"/blood-requests/{rid}/fulfill")
    assert r.status_code == 200
    assert r.json()["status"] == "FULFILLED"
    assert r.json()["fulfilled_at"] is not None

    donations = donor.get("/donors/me/donations").json()
    assert len(donations) == 1
    assert donations[0]["blood_request_id"] == rid
    assert donor.get("/donors/me").json()["last_donation_date"] is not None


def test_cancel_request() -> None:
    patient, _ = auth_client("PATIENT")
    rid = patient.post("/blood-requests", json={"blood_type": "A_POS"}).json()["id"]
    r = patient.post(f"/blood-requests/{rid}/cancel", json={"reason": "found elsewhere"})
    assert r.status_code == 200 and r.json()["status"] == "CANCELLED"
    # cannot cancel twice
    assert patient.post(f"/blood-requests/{rid}/cancel", json={}).status_code == 409


def test_unrelated_user_cannot_view_request() -> None:
    patient, _ = auth_client("PATIENT")
    other, _ = auth_client("PATIENT")
    rid = patient.post("/blood-requests", json={"blood_type": "A_POS"}).json()["id"]
    assert other.get(f"/blood-requests/{rid}").status_code == 404
    assert other.post(f"/blood-requests/{rid}/cancel", json={}).status_code == 404


def test_donor_cannot_create_request() -> None:
    donor, _ = _ready_donor()
    assert donor.post("/blood-requests", json={"blood_type": "A_POS"}).status_code == 403
