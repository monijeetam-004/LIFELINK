"""Concurrency-safety of donor Match Response (Accept/Decline/Not Now).

A true database-level race (two INSERTs for the same (request, donor) pair
racing past the "does a row already exist" check at the same instant) can't
be reliably forced through synchronous TestClient calls -- but the invariant
that matters is unconditional: two near-simultaneous responses from the same
donor to the same request/emergency must never 500, and must always leave
exactly one row behind. That invariant holds whether or not this particular
run actually lands the two requests on opposite sides of the SAVEPOINT
(see the try/except IntegrityError block in blood_requests.respond_to_request
and the identical one in emergencies.respond_to_emergency), so this test is
deterministic rather than a race it hopes to win.
"""
from __future__ import annotations

import threading

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.blood_request import DonorResponse
from app.models.emergency import EmergencyResponse
from tests.helpers import auth_client


def test_concurrent_duplicate_blood_request_responses_never_500() -> None:
    patient, _ = auth_client("PATIENT")
    donor, _ = auth_client("DONOR")
    donor.put("/donors/me", json={"blood_type": "O_NEG"})
    rid = patient.post("/blood-requests", json={"blood_type": "O_NEG"}).json()["id"]

    results: list[int] = []
    barrier = threading.Barrier(2)

    def _respond(action: str) -> None:
        barrier.wait()
        r = donor.post(f"/blood-requests/{rid}/respond", json={"action": action})
        results.append(r.status_code)

    threads = [
        threading.Thread(target=_respond, args=("ACCEPTED",)),
        threading.Thread(target=_respond, args=("NOT_NOW",)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results == [200, 200], results

    db = SessionLocal()
    try:
        rows = db.scalars(
            select(DonorResponse).where(DonorResponse.request_id == rid)
        ).all()
    finally:
        db.close()
    assert len(rows) == 1
    assert rows[0].status in ("ACCEPTED", "NOT_NOW")


def test_concurrent_duplicate_emergency_responses_never_500() -> None:
    patient, _ = auth_client("PATIENT")
    donor, _ = auth_client("DONOR")
    donor.put("/donors/me", json={"blood_type": "O_NEG"})
    eid = patient.post("/emergencies", json={"blood_type": "O_NEG"}).json()["id"]

    results: list[int] = []
    barrier = threading.Barrier(2)

    def _respond(action: str) -> None:
        barrier.wait()
        r = donor.post(f"/emergencies/{eid}/respond", json={"action": action})
        results.append(r.status_code)

    threads = [
        threading.Thread(target=_respond, args=("ACCEPTED",)),
        threading.Thread(target=_respond, args=("DECLINED",)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results == [200, 200], results

    db = SessionLocal()
    try:
        rows = db.scalars(
            select(EmergencyResponse).where(EmergencyResponse.emergency_id == eid)
        ).all()
    finally:
        db.close()
    assert len(rows) == 1
    assert rows[0].status in ("ACCEPTED", "DECLINED")
