"""Phase 19 -- blood request creation idempotency.

Blood-request creation previously had no idempotency mechanism at all
(documented as a remaining limitation in LIFELINK_PHASE_18_REPORT.md).
This mirrors the proven Phase 12 emergency-idempotency design
(`Emergency.idempotency_key`, `uq_emergency_idempotency_key`,
SAVEPOINT-guarded race handling) applied to `BloodRequest`, with one
deliberate extension the emergency path does not have: a same-key/
different-payload replay is rejected (409) instead of silently served --
see LIFELINK_PHASE_19_IDEMPOTENCY_AUDIT.md.

Every test talks to the real HTTP API against the real Postgres test
database (or, where a direct DB read is the only way to prove a negative,
the `db_session` fixture) -- nothing here is mocked.
"""
from __future__ import annotations

import threading
import uuid

from sqlalchemy import func, select

from app.models.audit_log import AuditLog
from app.models.blood_request import BloodRequest
from app.models.notification import Notification
from tests.helpers import auth_client


def _ready_donor(blood_type: str = "O_NEG"):
    c, _ = auth_client("DONOR")
    c.put("/donors/me", json={"blood_type": blood_type, "city": "Metro"})
    return c


# --------------------------------------------------------------------------- #
# 1. Normal creation (no key) -- unchanged, backward compatible
# --------------------------------------------------------------------------- #
def test_normal_creation_without_idempotency_key_is_unchanged() -> None:
    patient, _ = auth_client("PATIENT")
    r = patient.post("/blood-requests", json={"blood_type": "O_NEG", "city": "Metro"})
    assert r.status_code == 201, r.text
    assert "idempotency_key" not in r.json() or r.json().get("idempotency_key") in (None,)


# --------------------------------------------------------------------------- #
# 2. Same key + same payload -- real replay
# --------------------------------------------------------------------------- #
def test_same_key_same_payload_returns_the_same_request() -> None:
    patient, _ = auth_client("PATIENT")
    key = str(uuid.uuid4())
    body = {"blood_type": "O_NEG", "units_needed": 2, "city": "Metro", "idempotency_key": key}

    first = patient.post("/blood-requests", json=body)
    assert first.status_code == 201, first.text
    rid = first.json()["id"]

    replay = patient.post("/blood-requests", json=body)
    assert replay.status_code == 200  # not 201 -- nothing new was created
    assert replay.json()["id"] == rid

    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        rows = db.scalars(select(BloodRequest).where(BloodRequest.idempotency_key == key)).all()
        assert len(rows) == 1
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# 3. Same key + different payload -- 409, not a silent replay
# --------------------------------------------------------------------------- #
def test_same_key_different_payload_is_rejected_with_409() -> None:
    patient, _ = auth_client("PATIENT")
    key = str(uuid.uuid4())
    first = patient.post(
        "/blood-requests", json={"blood_type": "O_NEG", "city": "Metro", "idempotency_key": key},
    )
    assert first.status_code == 201

    mismatched = patient.post(
        "/blood-requests", json={"blood_type": "A_POS", "city": "Metro", "idempotency_key": key},
    )
    assert mismatched.status_code == 409
    assert "different" in mismatched.json()["detail"].lower()

    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        rows = db.scalars(select(BloodRequest).where(BloodRequest.idempotency_key == key)).all()
        assert len(rows) == 1  # the mismatched payload created nothing
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# 4. Same key from different users -- scope is (requester, key), not global
# --------------------------------------------------------------------------- #
def test_same_key_from_different_users_creates_two_separate_requests() -> None:
    p1, _ = auth_client("PATIENT")
    p2, _ = auth_client("PATIENT")
    key = "shared-by-coincidence"
    a = p1.post("/blood-requests", json={"blood_type": "O_NEG", "idempotency_key": key})
    b = p2.post("/blood-requests", json={"blood_type": "O_NEG", "idempotency_key": key})
    assert a.status_code == 201 and b.status_code == 201
    assert a.json()["id"] != b.json()["id"]


# --------------------------------------------------------------------------- #
# 5. Missing key -- fully optional, backward compatible
# --------------------------------------------------------------------------- #
def test_missing_key_creates_a_new_request_every_time() -> None:
    patient, _ = auth_client("PATIENT")
    a = patient.post("/blood-requests", json={"blood_type": "O_NEG"})
    b = patient.post("/blood-requests", json={"blood_type": "O_NEG"})
    assert a.status_code == 201 and b.status_code == 201
    assert a.json()["id"] != b.json()["id"]


# --------------------------------------------------------------------------- #
# 6. Malformed key -- empty string / wrong type is rejected at validation
# --------------------------------------------------------------------------- #
def test_empty_string_key_is_rejected_by_validation() -> None:
    patient, _ = auth_client("PATIENT")
    r = patient.post("/blood-requests", json={"blood_type": "O_NEG", "idempotency_key": ""})
    assert r.status_code == 422


def test_non_string_key_is_rejected_by_validation() -> None:
    patient, _ = auth_client("PATIENT")
    r = patient.post("/blood-requests", json={"blood_type": "O_NEG", "idempotency_key": 12345})
    assert r.status_code == 422


# --------------------------------------------------------------------------- #
# 7. Oversized key -- bounded length
# --------------------------------------------------------------------------- #
def test_oversized_key_is_rejected_by_validation() -> None:
    patient, _ = auth_client("PATIENT")
    r = patient.post(
        "/blood-requests", json={"blood_type": "O_NEG", "idempotency_key": "x" * 201},
    )
    assert r.status_code == 422


def test_max_length_key_is_accepted() -> None:
    patient, _ = auth_client("PATIENT")
    r = patient.post(
        "/blood-requests", json={"blood_type": "O_NEG", "idempotency_key": "x" * 200},
    )
    assert r.status_code == 201, r.text


# --------------------------------------------------------------------------- #
# 8. Concurrency -- two identical requests must create exactly one row
# --------------------------------------------------------------------------- #
def test_concurrent_identical_idempotency_key_requests_create_exactly_one_request() -> None:
    patient, _ = auth_client("PATIENT")
    key = str(uuid.uuid4())
    body = {"blood_type": "O_NEG", "city": "Metro", "idempotency_key": key}
    results: list[dict] = []
    barrier = threading.Barrier(2)

    def _create() -> None:
        barrier.wait()
        r = patient.post("/blood-requests", json=body)
        results.append({"status": r.status_code, "id": r.json()["id"]})

    threads = [threading.Thread(target=_create) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(r["status"] for r in results) == [200, 201]
    assert results[0]["id"] == results[1]["id"]

    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        rows = db.scalars(select(BloodRequest).where(BloodRequest.idempotency_key == key)).all()
        assert len(rows) == 1
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# 9. Retry after simulated response loss -- the exact client scenario
# --------------------------------------------------------------------------- #
def test_retry_after_simulated_response_loss_is_safe() -> None:
    """Client sends the request, the server creates it, but the client never
    sees the response (dropped connection) and retries with the same key."""
    patient, _ = auth_client("PATIENT")
    key = str(uuid.uuid4())
    body = {"blood_type": "AB_POS", "units_needed": 3, "idempotency_key": key}

    first = patient.post("/blood-requests", json=body)
    assert first.status_code == 201
    original = first.json()

    # Simulate the client never having seen `first`'s response and retrying
    # the identical logical submission.
    retry = patient.post("/blood-requests", json=body)
    assert retry.status_code == 200
    assert retry.json()["id"] == original["id"]
    assert retry.json()["units_needed"] == original["units_needed"]


# --------------------------------------------------------------------------- #
# 10. Database rollback scenario -- an invalid beneficiary must not leave a
#     half-created row behind that a later retry with a corrected payload
#     could collide with.
# --------------------------------------------------------------------------- #
def test_failed_creation_does_not_block_a_later_successful_retry_with_the_same_key() -> None:
    family, _ = auth_client("FAMILY")
    key = str(uuid.uuid4())
    # No accepted link yet -- resolve_beneficiary will reject this.
    failed = family.post(
        "/blood-requests",
        json={"blood_type": "O_NEG", "patient_email": "nobody@example.com", "idempotency_key": key},
    )
    assert failed.status_code >= 400
    assert failed.status_code != 201

    # Retrying with the same key but a valid payload (filing for self, no
    # patient_email) must succeed -- the failed attempt left nothing behind
    # to collide with, because it never reached db.add()/db.flush().
    retry = family.post("/blood-requests", json={"blood_type": "O_NEG", "idempotency_key": key})
    assert retry.status_code == 201, retry.text


# --------------------------------------------------------------------------- #
# 11 & 12. Side-effect duplication: a replay must not double-notify donors,
#           the beneficiary, or double-write an audit event.
# --------------------------------------------------------------------------- #
def test_replay_does_not_double_notify_donors_or_duplicate_audit_events() -> None:
    donor = _ready_donor("O_NEG")
    donor_id = uuid.UUID(donor.get("/auth/me").json()["id"])
    patient, _ = auth_client("PATIENT")
    key = str(uuid.uuid4())
    body = {"blood_type": "O_NEG", "city": "Metro", "idempotency_key": key}

    first = patient.post("/blood-requests", json=body)
    assert first.status_code == 201
    replay = patient.post("/blood-requests", json=body)
    assert replay.status_code == 200

    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        notif_count = db.scalar(
            select(func.count()).select_from(Notification).where(
                Notification.user_id == donor_id,
                Notification.type == "blood_request.new",
            )
        )
        assert notif_count == 1

        audit_count = db.scalar(
            select(func.count()).select_from(AuditLog).where(
                AuditLog.action == "blood_request.created",
                AuditLog.target_id == uuid.UUID(first.json()["id"]),
            )
        )
        assert audit_count == 1
    finally:
        db.close()


def test_different_idempotency_keys_do_each_notify_donors_once() -> None:
    """Control: two genuinely distinct submissions should each notify once
    -- proves the replay-suppression above isn't just suppressing all
    donor notifications for the requester."""
    donor = _ready_donor("O_NEG")
    donor_id = uuid.UUID(donor.get("/auth/me").json()["id"])
    patient, _ = auth_client("PATIENT")

    patient.post(
        "/blood-requests",
        json={"blood_type": "O_NEG", "city": "Metro", "idempotency_key": "key-a"},
    )
    patient.post(
        "/blood-requests",
        json={"blood_type": "O_NEG", "city": "Metro", "idempotency_key": "key-b"},
    )

    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        notif_count = db.scalar(
            select(func.count()).select_from(Notification).where(
                Notification.user_id == donor_id,
                Notification.type == "blood_request.new",
            )
        )
        assert notif_count == 2
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# 13. Unauthorized request -- idempotency doesn't bypass auth
# --------------------------------------------------------------------------- #
def test_unauthenticated_request_with_idempotency_key_is_rejected() -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    c = TestClient(app, base_url="http://testserver")
    c.headers["Origin"] = "http://localhost:5173"
    r = c.post(
        "/blood-requests",
        json={"blood_type": "O_NEG", "idempotency_key": str(uuid.uuid4())},
    )
    assert r.status_code == 401


def test_donor_role_cannot_create_a_blood_request_even_with_a_key() -> None:
    donor = _ready_donor("O_NEG")
    r = donor.post(
        "/blood-requests", json={"blood_type": "O_NEG", "idempotency_key": str(uuid.uuid4())},
    )
    assert r.status_code == 403


# --------------------------------------------------------------------------- #
# 15. Interaction with the existing request lifecycle
# --------------------------------------------------------------------------- #
def test_replayed_request_can_still_be_cancelled_normally() -> None:
    patient, _ = auth_client("PATIENT")
    key = str(uuid.uuid4())
    body = {"blood_type": "O_NEG", "idempotency_key": key}
    patient.post("/blood-requests", json=body)
    replay = patient.post("/blood-requests", json=body)
    rid = replay.json()["id"]

    cancel = patient.post(f"/blood-requests/{rid}/cancel", json={"reason": "no longer needed"})
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "CANCELLED"
