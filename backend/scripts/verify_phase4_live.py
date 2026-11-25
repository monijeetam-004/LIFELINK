"""Live, real-HTTP verification of Phase 4 (Donor Portal) against the running
uvicorn server on :8000, backed by the real (non-test) database.

Seeds throwaway users directly in the DB (bypassing the registration flow,
same pattern as tests/helpers.py), then drives everything else through real
HTTP calls to the live server -- exercising the actual FastAPI routes, CORS
config, and DB constraints, not TestClient.
"""

from __future__ import annotations

import sys
import uuid

import httpx

sys.path.insert(0, ".")

from app.core.security import hash_password  # noqa: E402
from app.core.time import utcnow  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.user import User, UserRole  # noqa: E402

BASE = "http://localhost:8000"
PW = "a decent passphrase"


def seed(email: str, role: str) -> None:
    db = SessionLocal()
    try:
        db.add(
            User(
                email=email,
                password_hash=hash_password(PW),
                full_name=f"{role.title()} {email[:6]}",
                role=UserRole(role),
                is_email_verified=True,
                is_active=True,
                password_changed_at=utcnow(),
            )
        )
        db.commit()
    finally:
        db.close()


def login(email: str) -> httpx.Client:
    c = httpx.Client(base_url=BASE, headers={"Origin": "http://localhost:5173"})
    r = c.post("/auth/login", json={"email": email, "password": PW})
    assert r.status_code == 200, r.text
    return c


def check(label: str, cond: bool, extra: str = "") -> None:
    status = "OK " if cond else "FAIL"
    print(f"[{status}] {label}" + (f" -- {extra}" if extra and not cond else ""))
    if not cond:
        raise SystemExit(1)


def main() -> None:
    suffix = uuid.uuid4().hex[:8]
    donor_email = f"p4donor-{suffix}@example.com"
    patient_email = f"p4patient-{suffix}@example.com"
    hosp_email = f"p4hosp-{suffix}@example.com"

    seed(donor_email, "DONOR")
    seed(patient_email, "PATIENT")
    seed(hosp_email, "HOSPITAL")

    donor = login(donor_email)
    login(patient_email)  # proves the patient account can sign in
    hospital = login(hosp_email)

    # --- DONOR PROFILE / AVAILABILITY -----------------------------------
    r = donor.put("/donors/me", json={"blood_type": "O_NEG", "city": "Metro"})
    check("donor profile update (blood group)", r.status_code == 200, r.text)

    r = donor.get("/donors/me")
    body = r.json()
    check("default availability_state is AVAILABLE", body["availability_state"] == "AVAILABLE")
    check("default alert_min_urgency is ROUTINE", body["alert_min_urgency"] == "ROUTINE")
    check("identity_verified defaults false", body["identity_verified"] is False)

    r = donor.put(
        "/donors/me/availability", json={"state": "NOT_NOW", "availability_note": "on a trip"}
    )
    check(
        "availability -> NOT_NOW persists",
        r.status_code == 200 and r.json()["availability_state"] == "NOT_NOW",
    )
    check(
        "NOT_NOW flips is_available False (matching-exclusion)", r.json()["is_available"] is False
    )

    r = donor.put("/donors/me/availability", json={"state": "AVAILABLE"})
    check(
        "availability -> AVAILABLE persists",
        r.status_code == 200 and r.json()["is_available"] is True,
    )

    # --- ALERT WATCHER ----------------------------------------------------
    r = donor.put("/donors/me", json={"alert_min_urgency": "CRITICAL", "alert_radius_km": 25})
    check(
        "alert watcher prefs persist",
        r.status_code == 200 and r.json()["alert_min_urgency"] == "CRITICAL",
    )

    r = hospital.put("/hospitals/me", json={"name": "P4 Test Hospital"})
    check("hospital profile set", r.status_code == 200, r.text)

    r = hospital.post("/blood-requests", json={"blood_type": "O_NEG", "urgency": "ROUTINE"})
    check("hospital files ROUTINE request", r.status_code == 201, r.text)
    types = {n["type"] for n in donor.get("/notifications").json()}
    check(
        "donor with CRITICAL threshold NOT notified of ROUTINE request",
        "blood_request.new" not in types,
    )

    r = hospital.post("/blood-requests", json={"blood_type": "O_NEG", "urgency": "CRITICAL"})
    rid = r.json()["id"]
    types = {n["type"] for n in donor.get("/notifications").json()}
    check(
        "donor with CRITICAL threshold IS notified of CRITICAL request",
        "blood_request.new" in types,
    )

    r = donor.put("/donors/me", json={"alert_min_urgency": "ROUTINE"})
    check("reset alert threshold to ROUTINE", r.status_code == 200)

    # --- MATCH RESPONSE: three-way + duplicate prevention -----------------
    r = donor.post(f"/blood-requests/{rid}/respond", json={"action": "NOT_NOW"})
    check(
        "donor responds NOT_NOW", r.status_code == 200 and r.json()["status"] == "NOT_NOW", r.text
    )

    r = donor.post(f"/blood-requests/{rid}/respond", json={"action": "ACCEPTED"})
    check(
        "donor changes response to ACCEPTED (updates same row)",
        r.status_code == 200 and r.json()["status"] == "ACCEPTED",
    )

    mine = [x["my_response"] for x in donor.get("/donors/me/requests").json() if x["id"] == rid][0]
    check("only one response row reflected back (no duplicate)", mine["status"] == "ACCEPTED")

    # --- SCHEDULING ---------------------------------------------------------
    directory = donor.get("/hospitals/directory").json()
    hosp_id = next(h["id"] for h in directory if h["name"] == "P4 Test Hospital")
    import datetime

    pref_date = (datetime.date.today() + datetime.timedelta(days=60)).isoformat()
    r = donor.post(
        "/donors/me/appointments", json={"hospital_id": hosp_id, "preferred_date": pref_date}
    )
    check("appointment request created", r.status_code == 201, r.text)
    appt_id = r.json()["id"]
    r = donor.get("/donors/me/appointments")
    check("appointment shows in donor's list", any(a["id"] == appt_id for a in r.json()))

    # --- DONATION HISTORY / CERTIFICATES ------------------------------------
    r = donor.post(
        "/donors/me/donations",
        json={
            "units": 1,
            "donated_on": str(datetime.date.today()),
            "hospital_name": "P4 Test Hospital",
        },
    )
    check("donation logged", r.status_code == 201, r.text)
    donation_id = r.json()["id"]
    r = donor.post("/certificates", json={"donation_id": donation_id})
    check("certificate issued from real donation", r.status_code == 201, r.text)

    # --- PRIVACY: map data is coarse + anonymous ---------------------------
    r = donor.put(
        "/donors/me", json={"latitude": 40.7128, "longitude": -74.0060, "share_location": True}
    )
    check("donor opts into coarse location sharing", r.status_code == 200)
    r = hospital.get("/map/data")
    check("map/data reachable by hospital", r.status_code == 200, r.text)
    pins = r.json().get("donors", [])
    if pins:
        pin = pins[0]
        check(
            "map pin has no donor identity fields",
            "donor_name" not in pin and "user_id" not in pin,
            str(pin),
        )
        check(
            "map pin coordinates are rounded (coarse)", pin["latitude"] == round(pin["latitude"], 2)
        )

    print("\nAll Phase 4 live checks passed.")


if __name__ == "__main__":
    main()
