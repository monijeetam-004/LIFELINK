from __future__ import annotations

import uuid
from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.db.session import SessionLocal
from app.main import app
from app.models.hospital import HospitalProfile
from tests.helpers import _PW, auth_client, seed_verified_user

TOMORROW = (date.today() + timedelta(days=1)).isoformat()


def _login(email: str) -> TestClient:
    """Log in as an already-seeded user without re-seeding (auth_client always
    seeds, which would collide with a user created by _seed_hospital)."""
    client = TestClient(app, base_url="http://testserver")
    client.headers["Origin"] = "http://localhost:5173"
    resp = client.post("/auth/login", json={"email": email, "password": _PW})
    assert resp.status_code == 200, resp.text
    return client


def _seed_hospital(
    name: str = "General Hospital", city: str = "Metro", verified: bool = True
) -> tuple[str, uuid.UUID, uuid.UUID]:
    """Seed a hospital user + a named HospitalProfile directly, bypassing the
    (separately-owned) verification workflow. Returns (email, user_id, hospital_id).
    """
    email = f"hosp-{uuid.uuid4().hex[:8]}@example.com"
    user_id = seed_verified_user(email, role="HOSPITAL", full_name=name)
    db = SessionLocal()
    try:
        hp = HospitalProfile(user_id=user_id, name=name, city=city, verified=verified)
        db.add(hp)
        db.commit()
        db.refresh(hp)
        return email, user_id, hp.id
    finally:
        db.close()


def _donor():
    c, email = auth_client("DONOR")
    return c, email


def test_directory_lists_hospitals_for_any_role_and_requires_auth() -> None:
    _seed_hospital("Verified Med Center", "Metro", verified=True)
    _seed_hospital("Unverified Clinic", "Metro", verified=False)

    donor, _ = auth_client("DONOR")
    patient, _ = auth_client("PATIENT")
    hospital, _ = auth_client("HOSPITAL")

    for client in (donor, patient, hospital):
        r = client.get("/hospitals/directory")
        assert r.status_code == 200, r.text
        names = {item["name"]: item["verified"] for item in r.json()}
        assert names.get("Verified Med Center") is True
        assert names.get("Unverified Clinic") is False
        # minimal shape only
        item = r.json()[0]
        assert set(item.keys()) == {"id", "name", "city", "verified"}
        # verified-first ordering
        verified_flags = [i["verified"] for i in r.json()]
        assert verified_flags == sorted(verified_flags, reverse=True)

    from fastapi.testclient import TestClient

    from app.main import app

    anon = TestClient(app)
    assert anon.get("/hospitals/directory").status_code == 401


def test_create_appointment_requires_a_facility() -> None:
    donor, _ = _donor()
    r = donor.post("/donors/me/appointments", json={"preferred_date": TOMORROW})
    assert r.status_code == 400, r.text


def test_create_appointment_bogus_hospital_id_404() -> None:
    donor, _ = _donor()
    r = donor.post(
        "/donors/me/appointments",
        json={"hospital_id": str(uuid.uuid4()), "preferred_date": TOMORROW},
    )
    assert r.status_code == 404, r.text


def test_create_appointment_rejects_past_date() -> None:
    donor, _ = _donor()
    past = (date.today() - timedelta(days=1)).isoformat()
    r = donor.post(
        "/donors/me/appointments",
        json={"hospital_name": "Walk-in Clinic", "preferred_date": past},
    )
    assert r.status_code in (400, 422), r.text


def test_create_appointment_rejects_date_before_eligibility() -> None:
    donor, _ = _donor()
    # Audit D3 (2026-10-31): the interval now follows the configured, cited
    # policy (IN_DCR_2020: 120 days for every donor) instead of a fixed 56.
    from app.core.eligibility import donation_interval_days

    interval = donation_interval_days()
    recent = date.today() - timedelta(days=10)  # interval not yet elapsed
    donor.put("/donors/me", json={"last_donation_date": recent.isoformat()})

    too_soon = TOMORROW  # in the future, but before eligible_on (recent + interval)
    r = donor.post(
        "/donors/me/appointments",
        json={"hospital_name": "Walk-in Clinic", "preferred_date": too_soon},
    )
    assert r.status_code == 400, r.text
    assert "eligib" in r.json()["detail"].lower()

    ok_date = (recent + timedelta(days=interval)).isoformat()
    r = donor.post(
        "/donors/me/appointments",
        json={"hospital_name": "Walk-in Clinic", "preferred_date": ok_date},
    )
    assert r.status_code == 201, r.text


def test_create_appointment_success_notifies_hospital() -> None:
    donor, donor_email = _donor()
    hosp_email, _, hospital_id = _seed_hospital()

    r = donor.post(
        "/donors/me/appointments",
        json={
            "hospital_id": str(hospital_id),
            "preferred_date": TOMORROW,
            "note": "Prefer morning slot",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "REQUESTED"
    assert body["hospital_id"] == str(hospital_id)
    assert body["hospital_name"] == "General Hospital"

    hosp_client = _login(hosp_email)
    notes = hosp_client.get("/notifications").json()
    types = [n["type"] for n in notes]
    assert "appointment.requested" in types


def test_list_appointments_is_scoped_to_caller() -> None:
    donor1, _ = _donor()
    donor2, _ = _donor()
    donor1.post(
        "/donors/me/appointments",
        json={"hospital_name": "Clinic A", "preferred_date": TOMORROW},
    )
    donor2.post(
        "/donors/me/appointments",
        json={"hospital_name": "Clinic B", "preferred_date": TOMORROW},
    )

    r1 = donor1.get("/donors/me/appointments")
    assert r1.status_code == 200
    assert len(r1.json()) == 1
    assert r1.json()[0]["hospital_name"] == "Clinic A"

    r2 = donor2.get("/donors/me/appointments")
    assert len(r2.json()) == 1
    assert r2.json()[0]["hospital_name"] == "Clinic B"


def test_cancel_by_non_owner_404() -> None:
    donor1, _ = _donor()
    donor2, _ = _donor()
    appt = donor1.post(
        "/donors/me/appointments",
        json={"hospital_name": "Clinic A", "preferred_date": TOMORROW},
    ).json()

    r = donor2.post(f"/donors/me/appointments/{appt['id']}/cancel")
    assert r.status_code == 404, r.text


def test_cancel_already_cancelled_409() -> None:
    donor, _ = _donor()
    appt = donor.post(
        "/donors/me/appointments",
        json={"hospital_name": "Clinic A", "preferred_date": TOMORROW},
    ).json()

    first = donor.post(f"/donors/me/appointments/{appt['id']}/cancel")
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "CANCELLED"

    second = donor.post(f"/donors/me/appointments/{appt['id']}/cancel")
    assert second.status_code == 409, second.text


def test_cancel_flips_status_and_notifies_hospital() -> None:
    donor, _ = _donor()
    hosp_email, _, hospital_id = _seed_hospital()

    appt = donor.post(
        "/donors/me/appointments",
        json={"hospital_id": str(hospital_id), "preferred_date": TOMORROW},
    ).json()

    r = donor.post(f"/donors/me/appointments/{appt['id']}/cancel")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "CANCELLED"

    # persisted
    listed = donor.get("/donors/me/appointments").json()
    assert listed[0]["status"] == "CANCELLED"

    hosp_client = _login(hosp_email)
    types = [n["type"] for n in hosp_client.get("/notifications").json()]
    assert "appointment.cancelled" in types


def test_appointments_endpoints_require_donor_role() -> None:
    patient, _ = auth_client("PATIENT")
    assert patient.get("/donors/me/appointments").status_code == 403
    assert patient.post(
        "/donors/me/appointments",
        json={"hospital_name": "Clinic", "preferred_date": TOMORROW},
    ).status_code == 403
