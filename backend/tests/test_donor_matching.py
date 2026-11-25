from __future__ import annotations

from datetime import date, timedelta

from app.core.eligibility import donation_interval_days
from tests.helpers import auth_client

# Audit D3 (2026-10-31): the interval now follows DONOR_ELIGIBILITY_POLICY
# (default IN_DCR_2020, 120 days) instead of a hard-coded, uncited 56.


def _ready_donor(blood_type: str = "O_NEG", city: str | None = None):
    c, email = auth_client("DONOR")
    c.put("/donors/me", json={"blood_type": blood_type, "city": city})
    return c, email


def _open_request(patient, blood_type: str = "O_POS") -> str:
    return patient.post("/blood-requests", json={"blood_type": blood_type}).json()["id"]


def test_profile_exposes_real_eligibility() -> None:
    donor, _ = _ready_donor()
    body = donor.get("/donors/me").json()
    assert body["eligible"] is True
    assert body["eligible_on"] is None

    recent = date.today() - timedelta(days=10)
    donor.put("/donors/me", json={"last_donation_date": recent.isoformat()})
    body = donor.get("/donors/me").json()
    assert body["eligible"] is False
    assert body["eligible_on"] == (recent + timedelta(days=donation_interval_days())).isoformat()


def test_recently_donated_donor_cannot_accept() -> None:
    patient, _ = auth_client("PATIENT")
    donor, _ = _ready_donor()
    donor.put(
        "/donors/me",
        json={"last_donation_date": (date.today() - timedelta(days=5)).isoformat()},
    )
    rid = _open_request(patient)

    r = donor.post(f"/blood-requests/{rid}/respond", json={"action": "ACCEPTED"})
    assert r.status_code == 400
    assert "eligible" in r.json()["detail"].lower()

    # once the interval has passed, the same donor can accept
    donor.put(
        "/donors/me",
        json={"last_donation_date": (
            date.today() - timedelta(days=donation_interval_days() + 1)).isoformat()},
    )
    r = donor.post(f"/blood-requests/{rid}/respond", json={"action": "ACCEPTED"})
    assert r.status_code == 200


def test_unavailable_donor_cannot_accept_but_can_decline() -> None:
    patient, _ = auth_client("PATIENT")
    donor, _ = _ready_donor()
    donor.put("/donors/me/availability", json={"state": "UNAVAILABLE"})
    rid = _open_request(patient)

    accept = donor.post(f"/blood-requests/{rid}/respond", json={"action": "ACCEPTED"})
    decline = donor.post(f"/blood-requests/{rid}/respond", json={"action": "DECLINED"})
    assert accept.status_code == 400
    assert decline.status_code == 200


def test_donor_can_respond_not_now() -> None:
    """NOT_NOW is a real third response, distinct from DECLINED, and can be
    changed later just like ACCEPTED/DECLINED can (no duplicate row created)."""
    patient, _ = auth_client("PATIENT")
    donor, _ = _ready_donor()
    rid = _open_request(patient)

    r = donor.post(f"/blood-requests/{rid}/respond", json={"action": "NOT_NOW"})
    assert r.status_code == 200
    assert r.json()["status"] == "NOT_NOW"

    mine = [
        req["my_response"]
        for req in donor.get("/donors/me/requests").json()
        if req["id"] == rid
    ][0]
    assert mine["status"] == "NOT_NOW"

    # donor can change their mind later -- still one row, updated in place
    r = donor.post(f"/blood-requests/{rid}/respond", json={"action": "ACCEPTED"})
    assert r.status_code == 200
    assert r.json()["status"] == "ACCEPTED"


def test_not_now_response_logged_distinctly_from_declined() -> None:
    """The event timeline (and the honest label the frontend puts on it) must
    tell NOT_NOW apart from DECLINED -- that distinction is the entire reason
    NOT_NOW exists (see DonorResponseStatus.NOT_NOW's docstring)."""
    patient, _ = auth_client("PATIENT")
    donor, _ = _ready_donor()
    rid = _open_request(patient)

    donor.post(f"/blood-requests/{rid}/respond", json={"action": "NOT_NOW"})
    events = [e["event_type"] for e in patient.get(f"/blood-requests/{rid}").json()["events"]]
    assert "donor_not_now" in events
    assert "donor_declined" not in events

    donor.post(f"/blood-requests/{rid}/respond", json={"action": "DECLINED"})
    events = [e["event_type"] for e in patient.get(f"/blood-requests/{rid}").json()["events"]]
    assert "donor_declined" in events


def test_accept_notifies_both_parties() -> None:
    patient, _ = auth_client("PATIENT")
    donor, _ = _ready_donor()
    rid = _open_request(patient)
    donor.post(f"/blood-requests/{rid}/respond", json={"action": "ACCEPTED"})

    ptypes = {n["type"] for n in patient.get("/notifications").json()}
    dtypes = {n["type"] for n in donor.get("/notifications").json()}
    assert "blood_request.donor_accepted" in ptypes
    assert "blood_request.you_accepted" in dtypes


def test_withdrawal_reopens_request_and_notifies_requester() -> None:
    patient, _ = auth_client("PATIENT")
    donor, _ = _ready_donor()
    rid = _open_request(patient)

    donor.post(f"/blood-requests/{rid}/respond", json={"action": "ACCEPTED"})
    assert patient.get(f"/blood-requests/{rid}").json()["status"] == "MATCHING"

    r = donor.post(f"/blood-requests/{rid}/respond", json={"action": "DECLINED"})
    assert r.status_code == 200

    detail = patient.get(f"/blood-requests/{rid}").json()
    assert detail["status"] == "PENDING"  # reopened — no accepted donors remain
    assert detail["accepted_count"] == 0
    kinds = [e["event_type"] for e in detail["events"]]
    assert "donor_withdrew" in kinds
    assert "reopened" in kinds
    assert any(
        n["type"] == "blood_request.donor_withdrew" for n in patient.get("/notifications").json()
    )


def test_withdrawal_keeps_matching_when_other_donors_remain() -> None:
    patient, _ = auth_client("PATIENT")
    d1, _ = _ready_donor()
    d2, _ = _ready_donor()
    rid = _open_request(patient)

    d1.post(f"/blood-requests/{rid}/respond", json={"action": "ACCEPTED"})
    d2.post(f"/blood-requests/{rid}/respond", json={"action": "ACCEPTED"})
    d1.post(f"/blood-requests/{rid}/respond", json={"action": "DECLINED"})

    detail = patient.get(f"/blood-requests/{rid}").json()
    assert detail["status"] == "MATCHING"
    assert detail["accepted_count"] == 1


def test_match_history_persists_across_status_changes() -> None:
    patient, _ = auth_client("PATIENT")
    donor, _ = _ready_donor()
    rid = _open_request(patient)
    donor.post(f"/blood-requests/{rid}/respond", json={"action": "ACCEPTED"})

    hist = donor.get("/donors/me/responses").json()
    assert len(hist) == 1
    assert hist[0]["id"] == rid
    assert hist[0]["my_response"]["status"] == "ACCEPTED"
    assert hist[0]["status"] == "MATCHING"

    patient.post(f"/blood-requests/{rid}/fulfill")
    hist = donor.get("/donors/me/responses").json()
    assert len(hist) == 1
    assert hist[0]["status"] == "FULFILLED"
    # and a donation was recorded from the accepted response
    assert len(donor.get("/donors/me/donations").json()) == 1


def test_match_history_role_boundaries() -> None:
    patient, _ = auth_client("PATIENT")
    assert patient.get("/donors/me/responses").status_code == 403

    from fastapi.testclient import TestClient

    from app.main import app

    assert TestClient(app).get("/donors/me/responses").status_code == 401
