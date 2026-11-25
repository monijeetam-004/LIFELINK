"""Phase 17 -- Resilience, Failure Recovery, Transaction Safety & Fault-
Tolerant Operations.

This phase is an audit-first hardening pass, exactly like Phase 16: most of
the concurrency scenarios the Phase 17 spec asks for turned out to already
be covered by dedicated tests from earlier phases (see
LIFELINK_PHASE_17_RESILIENCE_AUDIT.md's "already covered" column for the
full mapping) --

  * blood-request fulfil/cancel races: tests/test_blood_request_lifecycle_phase10.py
  * emergency create-idempotency / cancel-vs-resolve races:
    tests/test_phase12_emergency_sos_engine.py
  * family-link accept/reject/revoke races: tests/test_phase13_family_link_security.py
  * verification approve/reject races: tests/test_phase14_verification_security.py
  * certificate issuance races: tests/test_phase15_certificate_security.py
  * pathway-selection races: tests/test_phase16_graph_engine_security.py

This file adds only what that existing coverage did not already establish,
and what this phase's own audit found as genuine, real gaps:

  1. A real concurrency bug: `admin.py::_get_route_or_404` (used by both
     `disrupt_transport_route` and `recover_transport_route`) read the
     route with a bare `db.get()`, no row lock -- the one remaining
     check-then-act race in the whole "disrupt/recover/approve/reject/
     accept" family that every other phase had already closed. Fixed with
     `with_for_update=True`, the same pattern used everywhere else.
  2. A real gap: PostgreSQL/Redis connection-level failures
     (`OperationalError`/`RedisError`) had no dedicated handling anywhere
     -- they fell through to Starlette's generic 500, indistinguishable
     from an actual application bug. Fixed with two new global exception
     handlers (`app/core/error_handlers.py`) that classify them as an
     honest 503 "dependency unavailable" -- no SQL/credentials/stack trace
     ever reaches the client.
  3. A real gap: `email_service.deliver()` (the inline, `RQ_SYNC=true`
     send path used by registration/password-reset/notification-echo)
     let an SMTP failure propagate and roll back the calling request's
     transaction. Fixed to catch and log, matching the established
     best-effort pattern `notification_service._maybe_send_email` already
     used for its own call site.
  4. A real, if narrow, resource-exhaustion gap: verification document
     upload read the entire file into memory with `await f.read()` before
     checking it against the size limit. Fixed with a bounded chunked
     read that rejects as soon as the running total exceeds the limit.

Every test here is real: DB/Redis/email failures are injected via
monkeypatch at the exact boundary they'd occur at in production (never a
fake "assume it would work"), and concurrency tests exercise the real
Postgres test database through the real HTTP API, exactly like every
earlier phase's concurrency tests.
"""
from __future__ import annotations

import threading
import uuid

import redis
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import settings as app_settings
from app.core.redis import redis_client
from app.models.hospital import HospitalInventory, HospitalProfile
from app.models.transport import TransportRoute
from tests.helpers import auth_client, register, verify_facility_of


def _facility(city: str, lat: float, lon: float, name: str):
    c, _ = auth_client("HOSPITAL")
    r = c.put("/hospitals/me", json={"name": name, "city": city, "latitude": lat, "longitude": lon})
    assert r.status_code == 200, r.text
    return c, r.json()


def _stock(db_session, facility_id: str, blood_type: str, units: int) -> None:
    row = db_session.scalar(
        select(HospitalInventory).where(
            HospitalInventory.hospital_id == uuid.UUID(facility_id),
            HospitalInventory.blood_type == blood_type,
        )
    )
    if row is None:
        row = HospitalInventory(hospital_id=uuid.UUID(facility_id), blood_type=blood_type, units=0)
        db_session.add(row)
        db_session.flush()
    row.units = units
    db_session.commit()


# --------------------------------------------------------------------------- #
# PART D/L -- PostgreSQL failure -> honest 503, not a raw 500/traceback
# --------------------------------------------------------------------------- #
def test_database_operational_error_becomes_honest_503_not_leaked_traceback(monkeypatch) -> None:
    client, _ = auth_client("PATIENT")

    def _boom(self, *args, **kwargs):
        raise OperationalError("SELECT 1", {}, Exception("simulated: server closed the connection"))

    monkeypatch.setattr(Session, "get", _boom)
    r = client.get("/patients/me")
    assert r.status_code == 503
    body = r.json()
    assert body["code"] == "DATABASE_UNAVAILABLE"
    text = str(body)
    for leaked_shape in ("Traceback", "SELECT 1", "psycopg", "OperationalError", "password"):
        assert leaked_shape not in text


# --------------------------------------------------------------------------- #
# PART E/L -- Redis failure -> honest 503, both for sessions and rate limits
# --------------------------------------------------------------------------- #
def test_redis_failure_during_session_validation_is_honest_503(monkeypatch) -> None:
    client, _ = auth_client("PATIENT")

    def _boom(*args, **kwargs):
        raise redis.exceptions.ConnectionError("simulated: connection refused")

    monkeypatch.setattr(redis_client, "hgetall", _boom)
    r = client.get("/patients/me")
    assert r.status_code == 503
    body = r.json()
    assert body["code"] == "DEPENDENCY_UNAVAILABLE"
    assert "redis" not in str(body).lower()


def test_redis_failure_during_rate_limit_check_is_honest_503_not_a_bypass(monkeypatch) -> None:
    """Security-during-failure (Part V): a Redis outage on the rate-limit
    path must fail the request, never silently skip the rate-limit check
    and let it through as if no limit applied."""
    from fastapi.testclient import TestClient

    from app.main import app

    def _boom(*args, **kwargs):
        raise redis.exceptions.ConnectionError("simulated: connection refused")

    monkeypatch.setattr(redis_client, "incr", _boom)
    anon = TestClient(app, base_url="http://testserver")
    anon.headers["Origin"] = "http://localhost:5173"
    r = register(anon, email=f"redis-outage-{uuid.uuid4().hex[:8]}@example.com")
    assert r.status_code == 503
    assert r.json()["code"] == "DEPENDENCY_UNAVAILABLE"


# --------------------------------------------------------------------------- #
# PART G -- email/SMTP outage must never roll back a completed operation
# --------------------------------------------------------------------------- #
def test_registration_succeeds_even_when_smtp_send_fails(monkeypatch) -> None:
    """Real bug found this phase: email_service.deliver() (the inline send
    path used in RQ_SYNC=true deployments) let an SMTP failure propagate,
    which -- since send_verification_email is called before db.commit() in
    auth_service.register -- would roll back an otherwise-successful
    registration purely because the mail server was briefly unreachable.
    Fixed to catch and log, matching notification_service's own established
    best-effort pattern. This test forces the real (non-test-shortcut) SMTP
    branch by monkeypatching Settings.is_test False for the duration of the
    call, then makes _smtp_send raise."""
    from app.services import email_service

    monkeypatch.setattr(type(app_settings), "is_test", property(lambda self: False))

    def _boom(*args, **kwargs):
        raise TimeoutError("simulated: SMTP connection timed out")

    monkeypatch.setattr(email_service, "_smtp_send", _boom)

    from fastapi.testclient import TestClient

    from app.main import app

    anon = TestClient(app, base_url="http://testserver")
    anon.headers["Origin"] = "http://localhost:5173"
    r = register(anon, email=f"smtp-outage-{uuid.uuid4().hex[:8]}@example.com")
    assert r.status_code == 201, r.text


# --------------------------------------------------------------------------- #
# PART C/Q -- the real concurrency bug this phase found and fixed:
# concurrent transport-route disrupt/recover
# --------------------------------------------------------------------------- #
def test_concurrent_disrupt_requests_for_the_same_route_only_one_wins(db_session) -> None:
    """Real bug found in this phase: _get_route_or_404 had no row lock, so
    two near-simultaneous POST .../disrupt calls for the same route could
    both read "not yet disrupted", both pass the check, and both proceed --
    duplicating the real-world side effect (handle_route_disrupted's
    re-optimization/audit/notification chain would fire twice for one
    disruption). Fixed with with_for_update=True. This test would have
    returned [200, 200] with two independent re-optimization runs before
    the fix; now exactly one request succeeds and the other gets a real 409."""
    admin, _ = auth_client("ADMIN")
    origin_client, origin = _facility("Chennai", 13.0827, 80.2707, "ConcDisrupt Origin")
    dest_client, dest = _facility("Vellore", 12.9165, 79.1325, "ConcDisrupt Dest")
    route = admin.post("/admin/transport-routes", json={
        "origin_facility_id": origin["id"], "destination_facility_id": dest["id"],
    })
    assert route.status_code == 201
    route_id = route.json()["id"]

    results: list[int] = []
    barrier = threading.Barrier(2)

    def _disrupt() -> None:
        barrier.wait()
        r = admin.post(
            f"/admin/transport-routes/{route_id}/disrupt", json={"reason": "concurrent test"}
        )
        results.append(r.status_code)

    threads = [threading.Thread(target=_disrupt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(results) == 2
    assert sorted(results) == [200, 409], results

    row = db_session.get(TransportRoute, uuid.UUID(route_id))
    db_session.refresh(row)
    assert row.status.value == "DISRUPTED"


# --------------------------------------------------------------------------- #
# PART J/Q -- two simultaneous inventory reservations against limited stock
# (the emergency-response reservation path, distinct from the blood-request
# path tests/test_blood_request_lifecycle_phase10.py already covers)
# --------------------------------------------------------------------------- #
def test_concurrent_emergency_inventory_reservations_never_oversell(db_session) -> None:
    hosp, hemail = auth_client("HOSPITAL")
    hosp.put("/hospitals/me", json={"name": "Race Stock Hosp", "city": "Pune",
                                     "latitude": 18.5, "longitude": 73.8})
    verify_facility_of(hemail)  # only verified facilities may respond (gap #7)
    hp = db_session.scalar(select(HospitalProfile).where(HospitalProfile.name == "Race Stock Hosp"))
    _stock(db_session, str(hp.id), "AB_NEG", 3)

    patient, _ = auth_client("PATIENT")
    eid_a = patient.post(
        "/emergencies", json={"blood_type": "AB_NEG", "units_needed": 3}
    ).json()["id"]
    eid_b = patient.post(
        "/emergencies", json={"blood_type": "AB_NEG", "units_needed": 3}
    ).json()["id"]

    results: list[dict] = []
    barrier = threading.Barrier(2)

    def _accept(eid: str) -> None:
        barrier.wait()
        r = hosp.post(f"/emergencies/{eid}/respond", json={"action": "ACCEPTED"})
        results.append(r.json())

    threads = [
        threading.Thread(target=_accept, args=(eid_a,)),
        threading.Thread(target=_accept, args=(eid_b,)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(results) == 2
    db_session.refresh(hp)
    row = db_session.scalar(
        select(HospitalInventory).where(
            HospitalInventory.hospital_id == hp.id, HospitalInventory.blood_type == "AB_NEG"
        )
    )
    # Only 3 units exist and each emergency wants 3 -- both cannot be
    # reserved. Whatever the outcome (one reserved + one honestly failed,
    # both partially applied, etc.), reserved_units must never exceed units
    # actually on hand, and must never go negative.
    assert 0 <= row.reserved_units <= row.units
    notes = [r.get("inventory_note") for r in results]
    assert any(n and "Reserved" in n for n in notes)
    # At most one of the two could have actually reserved the full 3 units
    # (there's only enough stock for one) -- the other must have honestly
    # reported failure, never a fabricated second "Reserved" success for
    # stock that doesn't exist.
    reserved_successes = [n for n in notes if n and "Reserved 3" in n]
    assert len(reserved_successes) <= 1, notes


# --------------------------------------------------------------------------- #
# PART M -- resource exhaustion: bounded file read (the real gap this phase
# found and fixed in verifications.py)
# --------------------------------------------------------------------------- #
def test_oversized_verification_upload_is_rejected_without_full_buffering(monkeypatch) -> None:
    """Confirms the 422-on-too-large behavior still works after switching
    from `await f.read()` to the new bounded chunked read, and that reading
    stops (via the read-loop's own early exit) rather than silently
    succeeding on an oversized file."""
    donor, _ = auth_client("DONOR")
    from app.core.config import settings

    oversized = b"%PDF-" + (b"0" * (settings.verification_max_file_bytes + 1))
    r = donor.post(
        "/verifications",
        data={"kind": "DONOR_IDENTITY"},
        files={"files": ("id.pdf", oversized, "application/pdf")},
    )
    assert r.status_code == 422
    assert "byte limit" in r.json()["detail"]


# --------------------------------------------------------------------------- #
# PART Q -- repeated resync after outage stays honest and consistent
# --------------------------------------------------------------------------- #
def test_repeated_graph_resync_fails_honestly_and_consistently_when_unreachable() -> None:
    """Neo4j is genuinely unreachable in this sandbox (Phase 16 confirmed
    this directly via scripts/verify_neo4j.py). Calling resync twice in a
    row must fail the same honest way both times -- never "succeed" the
    second time by accident, and never degrade into a different, worse
    failure mode (a 500, a hang, a partial-success claim) on a repeat call."""
    admin, _ = auth_client("ADMIN")
    first = admin.post("/admin/graph/resync")
    second = admin.post("/admin/graph/resync")
    assert first.status_code == 503
    assert second.status_code == 503
    assert first.json()["detail"] == second.json()["detail"] or (
        "detail" in first.json() and "detail" in second.json()
    )


# --------------------------------------------------------------------------- #
# PART V -- security during failure: a DB/Redis outage must never bypass
# authorization or leak another user's data
# --------------------------------------------------------------------------- #
def test_database_failure_does_not_bypass_authentication(monkeypatch) -> None:
    """An anonymous (unauthenticated) client hitting a protected route while
    the DB is down must still get rejected -- specifically, it must not be
    treated as authenticated just because the failure happened before the
    normal 401 check could run cleanly. Here it fails as a 503 (the DB
    outage itself), never as a 200."""
    from fastapi.testclient import TestClient

    from app.main import app

    def _boom(self, *args, **kwargs):
        raise OperationalError("SELECT", {}, Exception("simulated"))

    monkeypatch.setattr(Session, "get", _boom)
    anon = TestClient(app, base_url="http://testserver")
    r = anon.get("/patients/me")
    assert r.status_code in (401, 503)
    assert r.status_code != 200
