from __future__ import annotations

from tests.helpers import auth_client


def test_patient_profile_autocreates_and_updates() -> None:
    c, _ = auth_client("PATIENT")
    r = c.get("/patients/me")
    assert r.status_code == 200
    assert r.json()["blood_type"] is None

    r = c.put(
        "/patients/me",
        json={"blood_type": "O_NEG", "city": "Metro", "phone": "9876543210"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["blood_type"] == "O_NEG"
    assert body["city"] == "Metro"
    # A bare 10-digit number is normalized to E.164 against the default
    # region (IN) -- see app/core/phone.py.
    assert body["phone"] == "+919876543210"

    # persisted
    assert c.get("/patients/me").json()["city"] == "Metro"


def test_patient_profile_rejects_malformed_phone() -> None:
    c, _ = auth_client("PATIENT")
    r = c.put("/patients/me", json={"phone": "555-1"})
    assert r.status_code == 422


def test_donor_profile_and_availability_persist() -> None:
    c, _ = auth_client("DONOR")
    r = c.get("/donors/me")
    assert r.status_code == 200
    assert r.json()["is_available"] is True  # default
    assert r.json()["availability_state"] == "AVAILABLE"  # default

    c.put("/donors/me", json={"blood_type": "A_POS", "city": "Metro"})
    r = c.put(
        "/donors/me/availability",
        json={"state": "UNAVAILABLE", "availability_note": "Travelling"},
    )
    assert r.status_code == 200
    assert r.json()["is_available"] is False
    assert r.json()["availability_state"] == "UNAVAILABLE"

    got = c.get("/donors/me").json()
    assert got["is_available"] is False
    assert got["availability_state"] == "UNAVAILABLE"
    assert got["availability_note"] == "Travelling"
    assert got["blood_type"] == "A_POS"


def test_donor_availability_not_now_state() -> None:
    """NOT_NOW is a distinct, honest three-way state, not a UI alias for
    UNAVAILABLE -- it takes the donor out of matching identically (both flip
    is_available False) but is surfaced back to the donor as its own value."""
    c, _ = auth_client("DONOR")
    r = c.put("/donors/me/availability", json={"state": "NOT_NOW"})
    assert r.status_code == 200
    body = r.json()
    assert body["is_available"] is False
    assert body["availability_state"] == "NOT_NOW"


def test_role_isolation_on_profiles() -> None:
    donor, _ = auth_client("DONOR")
    patient, _ = auth_client("PATIENT")
    assert donor.get("/patients/me").status_code == 403
    assert patient.get("/donors/me").status_code == 403


def test_profile_requires_auth() -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    anon = TestClient(app)
    assert anon.get("/patients/me").status_code == 401
    assert anon.get("/donors/me").status_code == 401
