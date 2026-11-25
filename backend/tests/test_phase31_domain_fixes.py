"""Phase 31 -- domain-correctness fixes: emergency deadlines get a real,
non-clinical consequence; an emergency accept is gated by the same
donation-interval eligibility rule blood-request accept already enforces;
a donor can no longer hold two simultaneous ACCEPTED commitments across
requests/emergencies; and a verification submission can no longer create
two simultaneous PENDING requests of the same kind for the same user under
concurrency. See docs/LIFELINK_PHASE_31_DOMAIN_AUDIT.md.

Every test here talks to the real HTTP API (or, for state a public endpoint
doesn't expose, the `db_session` fixture) against the real Postgres test
database -- nothing is mocked.
"""
from __future__ import annotations

import threading
import uuid
from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models.audit_log import AuditLog
from app.models.emergency import Emergency
from tests.helpers import auth_client, verify_facility_of

REAL_PDF = b"%PDF-1.4 fake donor id document"


def _donor_with_certificate(blood_type: str = "O_NEG"):
    donor, email = auth_client("DONOR")
    donor.put("/donors/me", json={"blood_type": blood_type})
    donation = donor.post("/donors/me/donations", json={
        "donated_on": date.today().isoformat(), "units": 1, "hospital_name": "Metro General",
    }).json()
    # Phase E: certificate issuance requires verification_status == VERIFIED
    # -- this donation has no hospital_id, so only an admin can review it
    # (see tests/test_certificates.py's _donor_with_donation for the same
    # note).
    admin, _ = auth_client("ADMIN")
    v = admin.post(f"/donations/{donation['id']}/verify", json={})
    assert v.status_code == 200, v.text
    cert = donor.post("/certificates", json={"donation_id": donation["id"]}).json()
    return donor, email, cert


def _submit_verification(client, kind: str = "DONOR_IDENTITY"):
    return client.post(
        "/verifications",
        data={"kind": kind},
        files=[("files", ("id.pdf", REAL_PDF, "application/pdf"))],
    )


def _ready_donor(blood_type: str = "O_NEG"):
    c, email = auth_client("DONOR")
    # Audit D4 (2026-10-31): a city, so the shared distance policy can place
    # this donor relative to a request in the same city.
    c.put("/donors/me", json={"blood_type": blood_type, "is_available": True, "city": "Metro"})
    return c, email


def _recently_donated_donor(blood_type: str = "O_NEG"):
    c, email = _ready_donor(blood_type)
    c.put(
        "/donors/me",
        json={"last_donation_date": (date.today() - timedelta(days=5)).isoformat()},
    )
    return c, email


# --------------------------------------------------------------------------- #
# A. Emergency deadline gets a real, non-clinical consequence
# --------------------------------------------------------------------------- #
def test_past_deadline_is_flagged_on_read_without_changing_status() -> None:
    patient, _ = auth_client("PATIENT")
    r = patient.post("/emergencies", json={
        "blood_type": "O_NEG", "deadline": "2000-01-01T00:00:00Z",
    })
    assert r.status_code == 201
    eid = r.json()["id"]
    # Creation itself never flags it -- only a subsequent read/action does
    # (mirrors blood_request_lifecycle.expire_if_stale's lazy-on-read design;
    # there is no scheduled job anywhere in this codebase).
    assert r.json()["deadline_exceeded_at"] is None
    assert r.json()["status"] == "ACTIVE"

    detail = patient.get(f"/emergencies/{eid}").json()
    assert detail["deadline_exceeded_at"] is not None
    # The deadline being missed must never be silently reinterpreted as the
    # system deciding the emergency is resolved/cancelled/any-less-urgent --
    # Section 9's explicit ban on autonomous clinical/operational decisions.
    assert detail["status"] == "ACTIVE"
    assert [e["event_type"] for e in detail["events"]] == ["created", "deadline_exceeded"]

    # Creator is notified.
    assert any(
        n["type"] == "emergency.deadline_exceeded" for n in patient.get("/notifications").json()
    )


def test_deadline_exceeded_flag_is_set_exactly_once(db_session) -> None:
    patient, _ = auth_client("PATIENT")
    eid = patient.post("/emergencies", json={
        "blood_type": "O_NEG", "deadline": "2000-01-01T00:00:00Z",
    }).json()["id"]

    first = patient.get(f"/emergencies/{eid}").json()["deadline_exceeded_at"]
    second = patient.get(f"/emergencies/{eid}").json()["deadline_exceeded_at"]
    assert first == second

    em = db_session.get(Emergency, uuid.UUID(eid))
    events = list(
        db_session.scalars(
            select(AuditLog).where(
                AuditLog.target_type == "emergency",
                AuditLog.target_id == em.id,
                AuditLog.action == "emergency.deadline_exceeded",
            )
        ).all()
    )
    assert len(events) == 1  # not one per read

    notifications = [
        n for n in patient.get("/notifications").json()
        if n["type"] == "emergency.deadline_exceeded"
    ]
    assert len(notifications) == 1  # not one per read


def test_future_deadline_is_never_flagged() -> None:
    patient, _ = auth_client("PATIENT")
    eid = patient.post("/emergencies", json={
        "blood_type": "O_NEG", "deadline": "2099-01-01T00:00:00Z",
    }).json()["id"]
    detail = patient.get(f"/emergencies/{eid}").json()
    assert detail["deadline_exceeded_at"] is None


def test_no_deadline_is_never_flagged() -> None:
    patient, _ = auth_client("PATIENT")
    eid = patient.post("/emergencies", json={"blood_type": "O_NEG"}).json()["id"]
    detail = patient.get(f"/emergencies/{eid}").json()
    assert detail["deadline_exceeded_at"] is None


def test_deadline_exceeded_is_flagged_from_a_mutating_route_too() -> None:
    """Not just GET -- any route that fetches the row (acknowledge, respond,
    cancel, resolve, assistance-found) must notice too, since a responder
    interacting with an overdue emergency is exactly when this matters most."""
    patient, _ = auth_client("PATIENT")
    donor, _ = _ready_donor()
    eid = patient.post("/emergencies", json={
        "blood_type": "O_NEG", "deadline": "2000-01-01T00:00:00Z",
    }).json()["id"]

    r = donor.post(f"/emergencies/{eid}/acknowledge", json={})
    assert r.status_code == 200
    assert r.json()["deadline_exceeded_at"] is not None
    assert r.json()["status"] == "ACKNOWLEDGED"  # the real transition still applies normally


# --------------------------------------------------------------------------- #
# B. Emergency accept is gated by the same eligibility rule as blood-request
#    accept -- previously `is_eligible` was imported but only used for
#    match-candidate display, never enforced at accept-time.
# --------------------------------------------------------------------------- #
def test_recently_donated_donor_cannot_accept_emergency() -> None:
    patient, _ = auth_client("PATIENT")
    donor, _ = _recently_donated_donor()
    eid = patient.post("/emergencies", json={"blood_type": "O_NEG"}).json()["id"]

    r = donor.post(f"/emergencies/{eid}/respond", json={"action": "ACCEPTED"})
    assert r.status_code == 400
    assert "eligible" in r.json()["detail"].lower()

    # declining is still allowed even while ineligible -- only ACCEPT is gated
    r = donor.post(f"/emergencies/{eid}/respond", json={"action": "DECLINED"})
    assert r.status_code == 200


def test_eligible_donor_can_still_accept_emergency() -> None:
    patient, _ = auth_client("PATIENT")
    donor, _ = _ready_donor()
    eid = patient.post("/emergencies", json={"blood_type": "O_NEG"}).json()["id"]
    r = donor.post(f"/emergencies/{eid}/respond", json={"action": "ACCEPTED"})
    assert r.status_code == 200


# --------------------------------------------------------------------------- #
# C. Cross-request/cross-emergency donor double-booking guard -- a physical
#    donor can only actually donate in one place at a time.
# --------------------------------------------------------------------------- #
def test_donor_cannot_accept_two_open_blood_requests_at_once() -> None:
    patient, _ = auth_client("PATIENT")
    donor, _ = _ready_donor()
    r1 = patient.post("/blood-requests", json={"blood_type": "O_NEG"}).json()["id"]
    r2 = patient.post("/blood-requests", json={"blood_type": "O_NEG"}).json()["id"]

    ok = donor.post(f"/blood-requests/{r1}/respond", json={"action": "ACCEPTED"})
    assert ok.status_code == 200

    conflict = donor.post(f"/blood-requests/{r2}/respond", json={"action": "ACCEPTED"})
    assert conflict.status_code == 409
    assert "already accepted" in conflict.json()["detail"].lower()

    # re-accepting the SAME request (idempotent replay) is never blocked
    replay = donor.post(f"/blood-requests/{r1}/respond", json={"action": "ACCEPTED"})
    assert replay.status_code == 200


def test_donor_cannot_accept_open_emergency_while_holding_a_blood_request_commitment() -> None:
    patient, _ = auth_client("PATIENT")
    donor, _ = _ready_donor()
    rid = patient.post("/blood-requests", json={"blood_type": "O_NEG"}).json()["id"]
    eid = patient.post("/emergencies", json={"blood_type": "O_NEG"}).json()["id"]

    ok = donor.post(f"/blood-requests/{rid}/respond", json={"action": "ACCEPTED"})
    assert ok.status_code == 200

    conflict = donor.post(f"/emergencies/{eid}/respond", json={"action": "ACCEPTED"})
    assert conflict.status_code == 409
    assert "already accepted" in conflict.json()["detail"].lower()


def test_donor_cannot_accept_two_open_emergencies_at_once() -> None:
    patient, _ = auth_client("PATIENT")
    donor, _ = _ready_donor()
    e1 = patient.post("/emergencies", json={"blood_type": "O_NEG"}).json()["id"]
    e2 = patient.post("/emergencies", json={"blood_type": "O_NEG"}).json()["id"]

    assert donor.post(f"/emergencies/{e1}/respond", json={"action": "ACCEPTED"}).status_code == 200
    conflict = donor.post(f"/emergencies/{e2}/respond", json={"action": "ACCEPTED"})
    assert conflict.status_code == 409


def test_double_booking_guard_frees_up_after_the_first_commitment_closes() -> None:
    """The guard only blocks *open* commitments -- once the first request is
    cancelled the donor is free to accept elsewhere, matching the module's
    own documented scope (an OPEN-status filter, not a permanent lock)."""
    patient, _ = auth_client("PATIENT")
    donor, _ = _ready_donor()
    r1 = patient.post("/blood-requests", json={"blood_type": "O_NEG"}).json()["id"]
    r2 = patient.post("/blood-requests", json={"blood_type": "O_NEG"}).json()["id"]

    accept = donor.post(f"/blood-requests/{r1}/respond", json={"action": "ACCEPTED"})
    assert accept.status_code == 200
    assert patient.post(f"/blood-requests/{r1}/cancel", json={}).status_code == 200

    ok = donor.post(f"/blood-requests/{r2}/respond", json={"action": "ACCEPTED"})
    assert ok.status_code == 200


def test_hospital_responder_is_not_subject_to_the_double_booking_guard() -> None:
    """A hospital is an institution coordinating supply/logistics, not a
    single physical body -- accepting to help with several concurrent
    emergencies at once is normal and correct (see
    app.services.donor_commitment's module docstring)."""
    patient, _ = auth_client("PATIENT")
    hospital, hemail = auth_client("HOSPITAL")
    hospital.put("/hospitals/me", json={"name": "General Hospital", "city": "Metro"})
    verify_facility_of(hemail)  # only verified facilities may respond (gap #7)
    e1 = patient.post("/emergencies", json={"blood_type": "O_NEG"}).json()["id"]
    e2 = patient.post("/emergencies", json={"blood_type": "O_NEG"}).json()["id"]

    accept1 = hospital.post(f"/emergencies/{e1}/respond", json={"action": "ACCEPTED"})
    accept2 = hospital.post(f"/emergencies/{e2}/respond", json={"action": "ACCEPTED"})
    assert accept1.status_code == 200
    assert accept2.status_code == 200


# --------------------------------------------------------------------------- #
# D. notify_matching_donors skips a donor who is not currently eligible --
#    they were already blocked at accept-time; alerting them anyway was just
#    wasted attention and a false sense of reachable supply.
# --------------------------------------------------------------------------- #
def test_ineligible_donor_is_not_alerted_for_a_new_matching_request() -> None:
    patient, _ = auth_client("PATIENT")
    ineligible, _ = _recently_donated_donor("O_NEG")
    eligible, _ = _ready_donor("O_NEG")

    patient.post("/blood-requests", json={"blood_type": "O_NEG", "city": "Metro"})

    assert any(
        n["type"] == "blood_request.new" for n in eligible.get("/notifications").json()
    )
    assert not any(
        n["type"] == "blood_request.new" for n in ineligible.get("/notifications").json()
    )


# --------------------------------------------------------------------------- #
# E. Verification submission: a partial unique index closes a real
#    check-then-insert TOCTOU race in the pre-existing "no duplicate PENDING
#    request" pre-flight check.
# --------------------------------------------------------------------------- #
def test_second_pending_verification_of_same_kind_is_rejected() -> None:
    donor, _ = auth_client("DONOR")
    first = _submit_verification(donor)
    assert first.status_code == 201

    second = _submit_verification(donor)
    assert second.status_code == 409
    assert "pending" in second.json()["detail"].lower()


def test_concurrent_duplicate_verification_submissions_only_one_succeeds() -> None:
    """The pre-flight SELECT alone cannot close this race -- two threads can
    both observe zero PENDING rows before either commits its INSERT. This
    proves the actual invariant is enforced at the database level
    (`uq_verification_one_pending_per_kind`), not merely in the common
    single-request case."""
    donor, _ = auth_client("DONOR")
    results: list[int] = []
    barrier = threading.Barrier(2)

    def _go() -> None:
        barrier.wait()
        results.append(_submit_verification(donor).status_code)

    threads = [threading.Thread(target=_go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == [201, 409], results
    # Exactly one PENDING row ever exists for this (user, kind) -- never two.
    pending = [
        v for v in donor.get("/verifications/me").json()
        if v["kind"] == "DONOR_IDENTITY" and v["status"] == "PENDING"
    ]
    assert len(pending) == 1


def test_resubmission_after_rejection_is_allowed() -> None:
    """The partial unique index only ever covers PENDING rows -- a user must
    still be able to resubmit after an admin rejects their first attempt."""
    donor, _ = auth_client("DONOR")
    admin, _ = auth_client("ADMIN")
    vid = _submit_verification(donor).json()["id"]
    r = admin.post(f"/admin/verifications/{vid}/reject", json={"reason": "blurry photo"})
    assert r.status_code == 200

    resubmit = _submit_verification(donor)
    assert resubmit.status_code == 201


# --------------------------------------------------------------------------- #
# F. Certificate revocation -- before Phase 31 a Certificate could never be
#    invalidated once issued; the public verify endpoint always reported
#    `valid: True` for any row that existed, with no way to distinguish a
#    genuinely trustworthy certificate from one an admin has since revoked.
# --------------------------------------------------------------------------- #
def test_admin_can_revoke_a_certificate_and_public_verify_reflects_it() -> None:
    donor, _, cert = _donor_with_certificate()
    admin, _ = auth_client("ADMIN")

    anon = TestClient(app)
    before = anon.get(f"/certificates/verify/{cert['verify_code']}").json()
    assert before["valid"] is True

    r = admin.post(f"/admin/certificates/{cert['id']}/revoke", json={"reason": "issued in error"})
    assert r.status_code == 200
    assert r.json()["revoked_at"] is not None
    assert r.json()["revocation_reason"] == "issued in error"

    after = anon.get(f"/certificates/verify/{cert['verify_code']}").json()
    assert after["valid"] is False
    assert after["revoked_at"] is not None
    assert after["revocation_reason"] == "issued in error"
    # The factual record is never erased -- only its current trust status changes.
    assert after["serial"] == cert["serial"]

    # The donor's own view of it also reflects the revocation.
    mine = donor.get(f"/certificates/{cert['id']}").json()
    assert mine["revoked_at"] is not None


def test_only_admin_can_revoke_a_certificate() -> None:
    donor, _, cert = _donor_with_certificate()
    r = donor.post(f"/certificates/{cert['id']}/revoke", json={"reason": "nope"})
    assert r.status_code == 404  # not an admin route at all under /certificates

    # The real admin-namespaced route rejects a non-admin actor outright,
    # same as every other /admin/* route (require_roles(UserRole.ADMIN)).
    forbidden = donor.post(
        f"/admin/certificates/{cert['id']}/revoke", json={"reason": "nope"}
    )
    assert forbidden.status_code == 403


def test_revoke_requires_a_real_reason() -> None:
    _, _, cert = _donor_with_certificate()
    admin, _ = auth_client("ADMIN")
    r = admin.post(f"/admin/certificates/{cert['id']}/revoke", json={"reason": "x"})
    assert r.status_code == 422  # below CertificateRevokeIn's min_length=3


def test_revoking_an_unknown_certificate_404s() -> None:
    admin, _ = auth_client("ADMIN")
    r = admin.post(
        f"/admin/certificates/{uuid.uuid4()}/revoke", json={"reason": "does not exist"}
    )
    assert r.status_code == 404
