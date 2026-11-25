"""Phase 26 -- donation creation idempotency.

`POST /donors/me/donations` previously had no idempotency mechanism at all
-- a real, reproduced gap documented in LIFELINK_PHASE_25_TESTING_AUDIT.md /
LIFELINK_PHASE_25_TESTING_REPORT.md (Bugs Found #2): two concurrent
identical submissions created two donation rows. This mirrors the proven
Phase 19 blood-request idempotency design (`BloodRequest.idempotency_key`,
`uq_blood_request_idempotency_key`, SAVEPOINT-guarded race handling,
same-key/different-payload rejected with 409) applied to `Donation`, scoped
per donor rather than per requester -- see
LIFELINK_PHASE_26_DONATION_FRONTEND_AUDIT.md for the full design writeup.

Every test talks to the real HTTP API against the real Postgres test
database (or, where a direct DB read is the only way to prove a negative,
a fresh `SessionLocal()`) -- nothing here is mocked.
"""
from __future__ import annotations

import threading
import uuid
from datetime import date

from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.models.audit_log import AuditLog
from app.models.donation import Donation
from tests.helpers import auth_client


def _today() -> str:
    return date.today().isoformat()


def _ready_donor():
    c, _ = auth_client("DONOR")
    return c


# --------------------------------------------------------------------------- #
# 1. Normal creation (no key) -- unchanged, backward compatible
# --------------------------------------------------------------------------- #
def test_normal_creation_without_idempotency_key_is_unchanged() -> None:
    donor = _ready_donor()
    r = donor.post("/donors/me/donations", json={"units": 1, "donated_on": _today()})
    assert r.status_code == 201, r.text


def test_missing_key_creates_a_new_donation_every_time() -> None:
    donor = _ready_donor()
    a = donor.post("/donors/me/donations", json={"units": 1, "donated_on": _today()})
    b = donor.post("/donors/me/donations", json={"units": 1, "donated_on": _today()})
    assert a.status_code == 201 and b.status_code == 201
    assert a.json()["id"] != b.json()["id"]


# --------------------------------------------------------------------------- #
# 2. Scenario A/B -- first request creates, replay with same key+payload
#    returns/replays the existing donation (not a second one)
# --------------------------------------------------------------------------- #
def test_same_key_same_payload_returns_the_same_donation() -> None:
    donor = _ready_donor()
    key = str(uuid.uuid4())
    body = {"units": 2, "donated_on": _today(), "hospital_name": "City Hospital",
            "idempotency_key": key}

    first = donor.post("/donors/me/donations", json=body)
    assert first.status_code == 201, first.text
    donation_id = first.json()["id"]

    replay = donor.post("/donors/me/donations", json=body)
    assert replay.status_code == 200  # not 201 -- nothing new was created
    assert replay.json()["id"] == donation_id

    db = SessionLocal()
    try:
        rows = db.scalars(select(Donation).where(Donation.idempotency_key == key)).all()
        assert len(rows) == 1
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# 3. Scenario C -- same key + different payload is rejected with 409, not a
#    silent replay of the first payload
# --------------------------------------------------------------------------- #
def test_same_key_different_payload_is_rejected_with_409() -> None:
    donor = _ready_donor()
    key = str(uuid.uuid4())
    first = donor.post(
        "/donors/me/donations",
        json={"units": 1, "donated_on": _today(), "idempotency_key": key},
    )
    assert first.status_code == 201

    mismatched = donor.post(
        "/donors/me/donations",
        json={"units": 5, "donated_on": _today(), "idempotency_key": key},
    )
    assert mismatched.status_code == 409
    assert "different" in mismatched.json()["detail"].lower()

    db = SessionLocal()
    try:
        rows = db.scalars(select(Donation).where(Donation.idempotency_key == key)).all()
        assert len(rows) == 1  # the mismatched payload created nothing
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# 4. Same key from different donors -- scope is (donor, key), not global.
#    Also the IDOR-relevant case: donor B cannot "replay" donor A's key to
#    get back donor A's donation -- they simply get their own separate row.
# --------------------------------------------------------------------------- #
def test_same_key_from_different_donors_are_two_donations_not_a_cross_donor_replay() -> None:
    a = _ready_donor()
    b = _ready_donor()
    key = "shared-by-coincidence"
    body = {"units": 1, "donated_on": _today(), "idempotency_key": key}

    ra = a.post("/donors/me/donations", json=body)
    rb = b.post("/donors/me/donations", json=body)
    assert ra.status_code == 201 and rb.status_code == 201
    assert ra.json()["id"] != rb.json()["id"]
    assert ra.json()["donor_user_id"] != rb.json()["donor_user_id"]

    # And B can never see/fetch A's row via A's own donations list.
    mine = b.get("/donors/me/donations").json()
    assert all(d["id"] != ra.json()["id"] for d in mine)


# --------------------------------------------------------------------------- #
# 5. Scenario D -- concurrency: N simultaneous identical requests must
#    create exactly one donation, no 500s, no uncaught IntegrityError.
# --------------------------------------------------------------------------- #
def _run_concurrent_identical_submissions(n: int) -> list[dict]:
    donor = _ready_donor()
    key = str(uuid.uuid4())
    body = {"units": 1, "donated_on": _today(), "idempotency_key": key}
    results: list[dict] = []
    barrier = threading.Barrier(n)

    def _create() -> None:
        barrier.wait()
        r = donor.post("/donors/me/donations", json=body)
        results.append({"status": r.status_code, "id": r.json().get("id")})

    threads = [threading.Thread(target=_create) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # No 500s / uncaught errors of any kind -- every response is a clean
    # 200 (replay) or 201 (the one real winner).
    assert all(r["status"] in (200, 201) for r in results), results
    created_count = sum(1 for r in results if r["status"] == 201)
    assert created_count == 1, f"exactly one request must be the real creation, got {results}"

    db = SessionLocal()
    try:
        rows = db.scalars(select(Donation).where(Donation.idempotency_key == key)).all()
    finally:
        db.close()
    assert len(rows) == 1, f"expected exactly 1 donation row for {n} concurrent identical requests"
    ids = {r["id"] for r in results}
    assert ids == {str(rows[0].id)}
    return results


def test_two_concurrent_identical_requests_create_exactly_one_donation() -> None:
    _run_concurrent_identical_submissions(2)


def test_five_concurrent_identical_requests_create_exactly_one_donation() -> None:
    _run_concurrent_identical_submissions(5)


def test_ten_concurrent_identical_requests_create_exactly_one_donation() -> None:
    _run_concurrent_identical_submissions(10)


def test_twenty_concurrent_identical_requests_create_exactly_one_donation() -> None:
    _run_concurrent_identical_submissions(20)


# --------------------------------------------------------------------------- #
# 6. Scenario F -- replay must not duplicate the audit event. (Donation
#    logging previously recorded no audit event at all -- a real gap
#    flagged in Phase 25 -- so this also proves the new event fires exactly
#    once per genuine donation, never once per HTTP call.)
# --------------------------------------------------------------------------- #
def test_replay_does_not_duplicate_the_audit_event() -> None:
    donor = _ready_donor()
    key = str(uuid.uuid4())
    body = {"units": 1, "donated_on": _today(), "idempotency_key": key}

    first = donor.post("/donors/me/donations", json=body)
    assert first.status_code == 201
    donation_id = uuid.UUID(first.json()["id"])
    replay = donor.post("/donors/me/donations", json=body)
    assert replay.status_code == 200

    db = SessionLocal()
    try:
        audit_count = db.scalar(
            select(func.count()).select_from(AuditLog).where(
                AuditLog.action == "donation.logged",
                AuditLog.target_id == donation_id,
            )
        )
        assert audit_count == 1
    finally:
        db.close()


def test_different_idempotency_keys_do_each_audit_once() -> None:
    """Control: two genuinely distinct submissions should each audit once --
    proves the replay-suppression above isn't suppressing all audit events
    for this donor."""
    donor = _ready_donor()
    donor_id = uuid.UUID(donor.get("/auth/me").json()["id"])
    donor.post(
        "/donors/me/donations",
        json={"units": 1, "donated_on": _today(), "idempotency_key": "key-a"},
    )
    donor.post(
        "/donors/me/donations",
        json={"units": 1, "donated_on": _today(), "idempotency_key": "key-b"},
    )
    db = SessionLocal()
    try:
        audit_count = db.scalar(
            select(func.count()).select_from(AuditLog).where(
                AuditLog.action == "donation.logged",
                AuditLog.actor_user_id == donor_id,
            )
        )
        assert audit_count == 2
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# 7. Scenario E -- replay must not produce a duplicate notification.
#    `log_donation` sends no notification to anyone today (grepped: no
#    `notification_service.notify` call anywhere in the donation-logging
#    path) -- documented honestly rather than inventing a notification path
#    that doesn't exist. The assertion below is the real, current behavior:
#    zero notifications before AND after a replay, proving no duplicate.
# --------------------------------------------------------------------------- #
def test_replay_produces_no_notification_because_donation_logging_sends_none() -> None:
    from app.models.notification import Notification

    donor = _ready_donor()
    donor_id = uuid.UUID(donor.get("/auth/me").json()["id"])
    key = str(uuid.uuid4())
    body = {"units": 1, "donated_on": _today(), "idempotency_key": key}

    donor.post("/donors/me/donations", json=body)
    donor.post("/donors/me/donations", json=body)

    db = SessionLocal()
    try:
        notif_count = db.scalar(
            select(func.count()).select_from(Notification).where(
                Notification.user_id == donor_id,
            )
        )
        assert notif_count == 0
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# 8. Scenario G -- replay must not duplicate the certificate. Certificates
#    are issued via a separate endpoint (POST /certificates), keyed
#    uniquely on donation_id -- proving a replayed donation-log request
#    still resolves to one donation_id is what keeps a later certificate
#    issuance from ever producing two certificates for "the same donation".
# --------------------------------------------------------------------------- #
def test_replayed_donation_still_yields_exactly_one_certificate_on_issuance() -> None:
    donor = _ready_donor()
    key = str(uuid.uuid4())
    body = {"units": 1, "donated_on": _today(), "idempotency_key": key}

    first = donor.post("/donors/me/donations", json=body)
    replay = donor.post("/donors/me/donations", json=body)
    assert first.json()["id"] == replay.json()["id"]
    donation_id = first.json()["id"]

    # Phase E: certificate issuance now requires verification_status ==
    # VERIFIED -- this donation has no hospital_id, so only an admin can
    # review it. Not what this test is about (replay-safety of issuance),
    # so just clear the real precondition like every other certificate
    # fixture in this phase does.
    admin, _ = auth_client("ADMIN")
    v = admin.post(f"/donations/{donation_id}/verify", json={})
    assert v.status_code == 200, v.text

    cert_a = donor.post("/certificates", json={"donation_id": donation_id})
    cert_b = donor.post("/certificates", json={"donation_id": donation_id})
    assert cert_a.status_code == 201, cert_a.text
    assert cert_b.status_code == 201, cert_b.text
    assert cert_a.json()["id"] == cert_b.json()["id"]

    from app.models.certificate import Certificate
    db = SessionLocal()
    try:
        rows = db.scalars(
            select(Certificate).where(Certificate.donation_id == uuid.UUID(donation_id))
        ).all()
        assert len(rows) == 1
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# 9. Malformed key -- empty string / wrong type / oversized rejected at
#    validation, matching the blood-request idempotency precedent exactly.
# --------------------------------------------------------------------------- #
def test_empty_string_key_is_rejected_by_validation() -> None:
    donor = _ready_donor()
    r = donor.post(
        "/donors/me/donations",
        json={"units": 1, "donated_on": _today(), "idempotency_key": ""},
    )
    assert r.status_code == 422


def test_non_string_key_is_rejected_by_validation() -> None:
    donor = _ready_donor()
    r = donor.post(
        "/donors/me/donations",
        json={"units": 1, "donated_on": _today(), "idempotency_key": 12345},
    )
    assert r.status_code == 422


def test_oversized_key_is_rejected_by_validation() -> None:
    donor = _ready_donor()
    r = donor.post(
        "/donors/me/donations",
        json={"units": 1, "donated_on": _today(), "idempotency_key": "x" * 201},
    )
    assert r.status_code == 422


def test_max_length_key_is_accepted() -> None:
    donor = _ready_donor()
    r = donor.post(
        "/donors/me/donations",
        json={"units": 1, "donated_on": _today(), "idempotency_key": "x" * 200},
    )
    assert r.status_code == 201, r.text


# --------------------------------------------------------------------------- #
# 10. A failed creation (ownership check rejects the blood_request_id) must
#     not leave a half-created row behind that blocks a later, corrected
#     retry with the same key.
# --------------------------------------------------------------------------- #
def test_failed_creation_does_not_block_a_later_successful_retry_with_the_same_key() -> None:
    patient, _ = auth_client("PATIENT")
    req_id = patient.post(
        "/blood-requests", json={"blood_type": "O_POS", "urgency": "ROUTINE"}
    ).json()["id"]

    donor = _ready_donor()  # never accepted this request
    key = str(uuid.uuid4())
    failed = donor.post(
        "/donors/me/donations",
        json={"blood_request_id": req_id, "units": 1, "donated_on": _today(),
              "idempotency_key": key},
    )
    assert failed.status_code == 400

    retry = donor.post(
        "/donors/me/donations",
        json={"units": 1, "donated_on": _today(), "idempotency_key": key},
    )
    assert retry.status_code == 201, retry.text


# --------------------------------------------------------------------------- #
# 11. Security regression -- role gate is unaffected by the idempotency key;
#     only DONOR may ever create a donation, no role bypasses it (including
#     ADMIN -- role checks in this codebase never implicitly elevate).
# --------------------------------------------------------------------------- #
def test_family_cannot_create_a_donation_even_with_a_key() -> None:
    family, _ = auth_client("FAMILY")
    r = family.post(
        "/donors/me/donations",
        json={"units": 1, "donated_on": _today(), "idempotency_key": str(uuid.uuid4())},
    )
    assert r.status_code == 403


def test_hospital_cannot_create_a_donation_even_with_a_key() -> None:
    hospital, _ = auth_client("HOSPITAL")
    r = hospital.post(
        "/donors/me/donations",
        json={"units": 1, "donated_on": _today(), "idempotency_key": str(uuid.uuid4())},
    )
    assert r.status_code == 403


def test_admin_cannot_create_a_donation_even_with_a_key() -> None:
    admin, _ = auth_client("ADMIN")
    r = admin.post(
        "/donors/me/donations",
        json={"units": 1, "donated_on": _today(), "idempotency_key": str(uuid.uuid4())},
    )
    assert r.status_code == 403


def test_unauthenticated_request_with_idempotency_key_is_rejected() -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    c = TestClient(app, base_url="http://testserver")
    c.headers["Origin"] = "http://localhost:5173"
    r = c.post(
        "/donors/me/donations",
        json={"units": 1, "donated_on": _today(), "idempotency_key": str(uuid.uuid4())},
    )
    assert r.status_code == 401


# --------------------------------------------------------------------------- #
# 12. Donor identity always comes from the session, never the payload -- a
#     donor cannot manipulate ownership. (`DonationCreate` has no
#     `donor_user_id` field at all, so this is enforced by the schema
#     itself; this test proves it end-to-end against the real API, which
#     silently ignores an unknown field rather than erroring, so this is
#     worth confirming rather than assuming.)
# --------------------------------------------------------------------------- #
def test_donation_is_attributed_to_the_authenticated_donor_not_a_client_supplied_id() -> None:
    a = _ready_donor()
    b = _ready_donor()
    b_id = b.get("/auth/me").json()["id"]

    r = a.post(
        "/donors/me/donations",
        json={"units": 1, "donated_on": _today(), "donor_user_id": b_id},
    )
    assert r.status_code == 201, r.text
    assert r.json()["donor_user_id"] != b_id
    assert r.json()["donor_user_id"] == a.get("/auth/me").json()["id"]
