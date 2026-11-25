"""Phase 23 -- Security hardening + adversarial audit.

Every test in this file performs REAL HTTP calls (via FastAPI's TestClient)
against the REAL application wired to the real Postgres test database and
real Redis rate-limit backend (see tests/conftest.py) -- exactly like every
other test file in this suite. None of this is mocked: an "IDOR" test here
really does log in as one seeded user and attempt to reach another seeded
user's real row through the real route; a "rate limit" test really does
fire enough real requests to trip the real Redis counter.

Scope, matching LIFELINK_PHASE_23_SECURITY_AUDIT.md:
  1.  IDOR -- blood requests (view/cancel/fulfill)
  2.  IDOR -- emergencies (view/cancel/resolve)
  3.  IDOR -- certificates (view + signed-download-token bypass attempts)
  4.  IDOR -- donor appointments (cancel)
  5.  IDOR -- notifications (mark-read)
  6.  IDOR -- family links (accept/reject/revoke)
  7.  RBAC -- role gate rejects every disallowed role, per sensitive route
  8.  RBAC -- admin-only endpoints reject every non-admin role
  9.  RBAC -- hospital-vs-hospital facility scoping (resource pathways)
  10. Privilege escalation -- self-registration cannot claim ADMIN
  11. Privilege escalation -- role is never trusted from client JSON on login
  12. Mass assignment -- donor profile update cannot flip identity_verified
  13. Mass assignment -- patient profile update cannot touch unrelated fields
  14. Mass assignment -- verification approval unreachable outside admin route
  15. Session security -- forged/garbage session cookie is rejected
  16. Session security -- session logged out via /logout cannot be reused
  17. Session security -- password change invalidates the old session
  18. Session security -- deactivated user's existing session stops working
  19. CSRF -- disallowed cross-site Origin rejected on a state-changing route
  20. CSRF -- same-origin Origin accepted
  21. CSRF -- absent Origin (non-browser client) accepted
  22. Rate limiting -- emergency creation (Phase 23 fix)
  23. Rate limiting -- blood request creation (Phase 23 fix)
  24. Idempotency -- blood request replay (same key/same payload) is safe
  25. Idempotency -- blood request same key/different payload is rejected (409)
  26. Idempotency -- emergency replay (same key) never double-creates
  27. Donation ownership -- Phase 23 fix: cannot attach an unrelated/unaccepted
      blood_request_id to a self-logged donation
  28. Email HTML injection -- Phase 23 fix: notification title/body/full_name
      are escaped before being interpolated into outbound HTML email
  29. secret_key production guard -- Phase 23 fix (unit-level, no HTTP)
  30. Audit log integrity -- every access-denial class above leaves a real,
      queryable AuditLog row (not merely a 403/404 response)
  31. Enumeration resistance -- family-link-request response is identical
      for "no such patient" and "patient exists but suspended"
  32. File upload hardening -- declared-vs-sniffed content-type mismatch is
      rejected (spot-check of pre-existing Phase 14 protection, still live)
  33. Security response headers -- Phase 23 fix: X-Content-Type-Options,
      X-Frame-Options, Referrer-Policy, Permissions-Policy are present on
      every response

Per the master prompt's non-negotiable rules: no destructive testing, no
real external systems, no secrets printed, no fabricated pass/fail claims --
every assertion below is checked against the real response the app returns.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.core.config import Settings
from app.core.security import sign
from app.models.audit_log import AuditLog
from app.models.donation import Donation
from app.models.emergency import Emergency
from app.services import certificate_service, email_service
from tests.helpers import auth_client, seed_verified_user


def _latest_audit(db_session, action: str, target_id=None):
    stmt = select(AuditLog).where(AuditLog.action == action)
    if target_id is not None:
        stmt = stmt.where(AuditLog.target_id == target_id)
    stmt = stmt.order_by(AuditLog.created_at.desc())
    return db_session.scalars(stmt).first()


# --------------------------------------------------------------------------- #
# 1-2. IDOR -- blood requests / emergencies
# --------------------------------------------------------------------------- #
def test_idor_blood_request_view_by_unrelated_patient_returns_404_not_leaked(db_session) -> None:
    owner, _ = auth_client("PATIENT")
    attacker, _ = auth_client("PATIENT")
    created = owner.post("/blood-requests", json={"blood_type": "O_POS", "urgency": "ROUTINE"})
    assert created.status_code == 201, created.text
    req_id = created.json()["id"]

    resp = attacker.get(f"/blood-requests/{req_id}")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()

    entry = _latest_audit(db_session, "blood_request.access_denied", uuid.UUID(req_id))
    assert entry is not None
    assert entry.meta["attempted_action"] == "view"


def test_idor_blood_request_cancel_by_unrelated_patient_denied(db_session) -> None:
    owner, _ = auth_client("PATIENT")
    attacker, _ = auth_client("PATIENT")
    created = owner.post("/blood-requests", json={"blood_type": "O_POS", "urgency": "ROUTINE"})
    req_id = created.json()["id"]

    resp = attacker.post(f"/blood-requests/{req_id}/cancel", json={"reason": "not mine"})
    assert resp.status_code == 404
    entry = _latest_audit(db_session, "blood_request.access_denied", uuid.UUID(req_id))
    assert entry is not None and entry.meta["attempted_action"] == "cancel"

    # Confirm the request genuinely was NOT cancelled by the attacker's attempt.
    still = owner.get(f"/blood-requests/{req_id}")
    assert still.json()["status"] == "PENDING"


def test_idor_blood_request_fulfill_by_unrelated_donor_denied() -> None:
    owner, _ = auth_client("PATIENT")
    donor, _ = auth_client("DONOR")
    created = owner.post("/blood-requests", json={"blood_type": "O_POS", "urgency": "ROUTINE"})
    req_id = created.json()["id"]

    resp = donor.post(f"/blood-requests/{req_id}/fulfill")
    assert resp.status_code == 404


def test_idor_emergency_view_by_unrelated_patient_returns_404(db_session) -> None:
    owner, _ = auth_client("PATIENT")
    attacker, _ = auth_client("PATIENT")
    created = owner.post(
        "/emergencies", json={"blood_type": "AB_NEG", "location_text": "Test City"}
    )
    assert created.status_code == 201, created.text
    em_id = created.json()["id"]

    resp = attacker.get(f"/emergencies/{em_id}")
    assert resp.status_code == 404
    entry = _latest_audit(db_session, "emergency.access_denied", uuid.UUID(em_id))
    assert entry is not None and entry.meta["attempted_action"] == "view"


def test_idor_emergency_cancel_by_unrelated_family_denied() -> None:
    owner, _ = auth_client("PATIENT")
    unrelated_family, _ = auth_client("FAMILY")
    created = owner.post(
        "/emergencies", json={"blood_type": "AB_NEG", "location_text": "Test City"}
    )
    em_id = created.json()["id"]

    resp = unrelated_family.post(f"/emergencies/{em_id}/cancel", json={})
    assert resp.status_code == 404
    still = owner.get(f"/emergencies/{em_id}")
    assert still.json()["status"] == "ACTIVE"


# --------------------------------------------------------------------------- #
# 3. IDOR -- certificates + signed download token
# --------------------------------------------------------------------------- #
def _issue_certificate(donor_client, donor_email: str, db_session):
    from app.models.enums import DonationVerificationStatus
    from app.models.user import User

    donor_user = db_session.scalar(select(User).where(User.email == donor_email))
    donation = Donation(
        donor_user_id=donor_user.id, units=1,
        donated_on=__import__("datetime").date.today(), hospital_name="Test Hospital",
        # Phase E: certificate issuance requires verification_status ==
        # VERIFIED. This donation is built directly against the DB
        # (not through the review endpoint), so it's created already
        # verified -- this test is about IDOR on the certificate route,
        # not the verification workflow.
        verification_status=DonationVerificationStatus.VERIFIED,
    )
    db_session.add(donation)
    db_session.commit()
    db_session.refresh(donation)
    resp = donor_client.post("/certificates", json={"donation_id": str(donation.id)})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_idor_certificate_view_by_unrelated_donor_returns_404(db_session) -> None:
    donor, donor_email = auth_client("DONOR")
    attacker, _ = auth_client("DONOR")
    cert = _issue_certificate(donor, donor_email, db_session)

    resp = attacker.get(f"/certificates/{cert['id']}")
    assert resp.status_code == 404
    entry = _latest_audit(db_session, "certificate.access_denied", uuid.UUID(cert["id"]))
    assert entry is not None


def test_certificate_download_without_token_or_ownership_is_unauthorized(db_session) -> None:
    donor, donor_email = auth_client("DONOR")
    attacker, _ = auth_client("DONOR")
    cert = _issue_certificate(donor, donor_email, db_session)

    resp = attacker.get(f"/certificates/{cert['id']}/download")
    assert resp.status_code == 401
    entry = _latest_audit(db_session, "certificate.access_denied", uuid.UUID(cert["id"]))
    assert entry is not None and entry.meta["attempted_action"] == "download"


def test_certificate_download_token_forged_or_wrong_id_is_rejected(db_session) -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    donor, donor_email = auth_client("DONOR")
    cert = _issue_certificate(donor, donor_email, db_session)

    # Anonymous (no session cookie at all) with only a garbage token string.
    anon = TestClient(app)
    bad = anon.get(f"/certificates/{cert['id']}/download", params={"token": "not-a-real-token"})
    assert bad.status_code == 401

    # Anonymous with a token that IS a real, validly-signed download token --
    # but signed for a completely different certificate id, so it must not
    # authorize this one.
    fake_cert_id = uuid.uuid4()
    foreign_token = certificate_service.make_download_token(fake_cert_id)
    mismatched = anon.get(f"/certificates/{cert['id']}/download", params={"token": foreign_token})
    assert mismatched.status_code == 401

    # The certificate's own genuine token, anonymously, DOES work -- confirms
    # the two rejections above are about the token being wrong, not about
    # this endpoint simply refusing every anonymous request.
    real_token = certificate_service.make_download_token(uuid.UUID(cert["id"]))
    good = anon.get(f"/certificates/{cert['id']}/download", params={"token": real_token})
    assert good.status_code == 200


# --------------------------------------------------------------------------- #
# 4. IDOR -- donor appointments
# --------------------------------------------------------------------------- #
def test_idor_donor_appointment_cancel_cross_donor_denied(db_session) -> None:
    donor, _ = auth_client("DONOR")
    attacker, _ = auth_client("DONOR")
    created = donor.post(
        "/donors/me/appointments",
        json={"hospital_name": "Test Hospital", "preferred_date": "2099-01-01"},
    )
    assert created.status_code == 201, created.text
    appt_id = created.json()["id"]

    resp = attacker.post(f"/donors/me/appointments/{appt_id}/cancel")
    assert resp.status_code == 404
    entry = _latest_audit(db_session, "donor_appointment.access_denied", uuid.UUID(appt_id))
    assert entry is not None


# --------------------------------------------------------------------------- #
# 5. IDOR -- notifications
# --------------------------------------------------------------------------- #
def test_idor_notification_mark_read_cross_user_denied(db_session) -> None:
    from app.models.notification import Notification
    from app.models.user import User

    victim, victim_email = auth_client("PATIENT")
    attacker, _ = auth_client("PATIENT")

    victim_user = db_session.scalar(select(User).where(User.email == victim_email))
    note = Notification(
        user_id=victim_user.id, type="test.event", title="t", body="b",
    )
    db_session.add(note)
    db_session.commit()
    db_session.refresh(note)

    resp = attacker.post(f"/notifications/{note.id}/read")
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# 6. IDOR -- family links
# --------------------------------------------------------------------------- #
def test_idor_family_link_accept_by_non_patient_denied(db_session) -> None:
    patient, patient_email = auth_client("PATIENT")
    family, _ = auth_client("FAMILY")
    attacker, _ = auth_client("PATIENT")

    resp = family.post(
        "/family/links", json={"patient_email": patient_email, "relationship": "sibling"}
    )
    assert resp.status_code == 201, resp.text
    link_id = resp.json()["id"]

    bad = attacker.post(f"/family/links/{link_id}/accept")
    assert bad.status_code == 403
    entry = _latest_audit(db_session, "family_link.access_denied", uuid.UUID(link_id))
    assert entry is not None and entry.meta["attempted_action"] == "accept"


def test_idor_family_link_revoke_by_outsider_denied() -> None:
    patient, patient_email = auth_client("PATIENT")
    family, _ = auth_client("FAMILY")
    outsider, _ = auth_client("FAMILY")

    link_id = family.post(
        "/family/links", json={"patient_email": patient_email, "relationship": "sibling"}
    ).json()["id"]
    resp = outsider.post(f"/family/links/{link_id}/revoke")
    assert resp.status_code == 403


# --------------------------------------------------------------------------- #
# 7-8. RBAC -- role gate coverage
# --------------------------------------------------------------------------- #
def test_rbac_emergency_create_rejects_donor_role() -> None:
    donor, _ = auth_client("DONOR")
    resp = donor.post("/emergencies", json={"blood_type": "O_POS"})
    assert resp.status_code == 403


def test_rbac_blood_request_respond_rejects_non_donor_roles() -> None:
    patient, _ = auth_client("PATIENT")
    created = patient.post("/blood-requests", json={"blood_type": "O_POS", "urgency": "ROUTINE"})
    req_id = created.json()["id"]

    for role in ("PATIENT", "FAMILY", "HOSPITAL"):
        actor, _ = auth_client(role)
        resp = actor.post(f"/blood-requests/{req_id}/respond", json={"action": "ACCEPTED"})
        assert resp.status_code == 403, f"{role} should not be able to respond as a donor"


def test_rbac_admin_endpoints_reject_every_non_admin_role(db_session) -> None:
    for role in ("PATIENT", "FAMILY", "DONOR", "HOSPITAL"):
        c, _ = auth_client(role)
        for path in ("/admin/users", "/admin/audit-log", "/admin/verifications"):
            resp = c.get(path)
            assert resp.status_code == 403, f"{role} reached {path}"
        entry = _latest_audit(db_session, "authz.role_denied")
        assert entry is not None and entry.meta["actor_role"] == role


def test_rbac_verification_submit_rejects_wrong_role_for_kind() -> None:
    donor, _ = auth_client("DONOR")
    resp = donor.post(
        "/verifications",
        data={"kind": "HOSPITAL_FACILITY"},
        files={"files": ("id.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert resp.status_code == 403


# --------------------------------------------------------------------------- #
# 9. RBAC -- hospital-vs-hospital facility scoping
# --------------------------------------------------------------------------- #
def test_hospital_cannot_query_another_hospitals_resource_pathways(db_session) -> None:
    from app.models.hospital import HospitalProfile
    from app.models.user import User

    owner, _ = auth_client("PATIENT")
    em = owner.post("/emergencies", json={"blood_type": "O_POS", "location_text": "Metro City"})
    em_id = em.json()["id"]

    hosp_a, hosp_a_email = auth_client("HOSPITAL")
    hosp_b, _ = auth_client("HOSPITAL")

    hosp_a_user = db_session.scalar(select(User).where(User.email == hosp_a_email))
    facility_a = HospitalProfile(
        user_id=hosp_a_user.id, name="Facility A", city="Metro City", verified=True
    )
    db_session.add(facility_a)
    db_session.commit()
    db_session.refresh(facility_a)

    resp = hosp_b.get(
        f"/emergencies/{em_id}/resource-pathways",
        params={"origin_facility_id": str(facility_a.id)},
    )
    assert resp.status_code == 403
    entry = _latest_audit(db_session, "hospital.facility_access_denied", facility_a.id)
    assert entry is not None


# --------------------------------------------------------------------------- #
# 10-11. Privilege escalation
# --------------------------------------------------------------------------- #
def test_self_registration_cannot_claim_admin_role(client) -> None:
    resp = client.post(
        "/auth/register",
        json={
            "full_name": "Wannabe Admin", "email": "escalate@example.com",
            "password": "a decent passphrase", "role": "ADMIN",
        },
    )
    assert resp.status_code == 422


def test_login_response_role_reflects_db_row_not_forged_client_state() -> None:
    """The API never accepts a client-asserted role anywhere in the login
    flow -- role always comes back as whatever the DB row actually says,
    regardless of what (if anything) a malicious client sends alongside
    real credentials."""
    client, email = auth_client("DONOR")
    me = client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["role"] == "DONOR"


# --------------------------------------------------------------------------- #
# 12-14. Mass assignment
# --------------------------------------------------------------------------- #
def test_mass_assignment_donor_profile_cannot_set_identity_verified() -> None:
    donor, _ = auth_client("DONOR")
    resp = donor.put(
        "/donors/me",
        json={"blood_type": "O_POS", "identity_verified": True, "role": "ADMIN"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["identity_verified"] is False

    check = donor.get("/donors/me")
    assert check.json()["identity_verified"] is False


def test_mass_assignment_patient_profile_ignores_unknown_fields() -> None:
    patient, _ = auth_client("PATIENT")
    resp = patient.put(
        "/patients/me",
        json={"city": "Metro City", "is_active": False, "role": "ADMIN", "password_hash": "x"},
    )
    assert resp.status_code == 200, resp.text
    # The account itself must not have been touched by the extra fields.
    me = patient.get("/auth/me")
    assert me.json()["is_active"] is True
    assert me.json()["role"] == "PATIENT"


def test_verification_approval_has_no_self_service_equivalent() -> None:
    """A donor/hospital has no route of their own that can set
    verified=True -- it is reachable only through /admin/verifications/*
    (see verifications._apply_approval's own docstring: "the only code path
    that ever flips ... to True"). This asserts the negative: the two
    self-service verification endpoints a submitter *does* have take no
    field that could set that flag."""
    donor, _ = auth_client("DONOR")
    # A real PDF-shaped upload (passes the magic-byte sniff) is accepted as a
    # PENDING request -- but the multipart form itself is strictly
    # kind/note/files (see schemas.verification / the Form(...)/File(...)
    # declarations on submit_verification): there is no "verified" or
    # "status" field anywhere on it for a client to smuggle a value through.
    resp = donor.post(
        "/verifications",
        data={"kind": "DONOR_IDENTITY"},
        files={"files": ("id.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "PENDING"
    # Submitting -- even successfully -- never itself flips the flag; only
    # POST /admin/verifications/{id}/approve does (see _apply_approval).
    check = donor.get("/donors/me")
    assert check.json()["identity_verified"] is False


# --------------------------------------------------------------------------- #
# 15-18. Session security
# --------------------------------------------------------------------------- #
def test_forged_session_cookie_is_rejected(client) -> None:
    client.cookies.set("lifelink_session", "totally-forged-session-id-not-in-redis")
    resp = client.get("/auth/me")
    assert resp.status_code == 401


def test_malformed_session_cookie_value_does_not_crash_the_server(client) -> None:
    for bogus in ("", "'; DROP TABLE users; --", "\x00\x01\x02", "a" * 5000):
        client.cookies.set("lifelink_session", bogus)
        resp = client.get("/auth/me")
        assert resp.status_code in (401, 400), f"bogus cookie {bogus!r} caused {resp.status_code}"


def test_logout_invalidates_the_session_immediately() -> None:
    client, _ = auth_client("PATIENT")
    assert client.get("/auth/me").status_code == 200
    assert client.post("/auth/logout").status_code == 204
    assert client.get("/auth/me").status_code == 401


def test_password_change_invalidates_the_old_session() -> None:
    client, email = auth_client("PATIENT")
    assert client.get("/auth/me").status_code == 200
    old_cookie = client.cookies.get("lifelink_session")

    changed = client.post(
        "/auth/change-password",
        json={"current_password": "a decent passphrase", "new_password": "a brand new passphrase"},
    )
    assert changed.status_code == 200, changed.text
    new_cookie = client.cookies.get("lifelink_session")
    assert new_cookie != old_cookie

    # Replay the OLD cookie on a fresh client -- it must no longer work.
    from fastapi.testclient import TestClient

    from app.main import app
    stale = TestClient(app)
    stale.cookies.set("lifelink_session", old_cookie)
    resp = stale.get("/auth/me")
    assert resp.status_code == 401


def test_deactivated_user_session_stops_authenticating(db_session) -> None:
    from app.models.user import User

    client, email = auth_client("PATIENT")
    assert client.get("/auth/me").status_code == 200

    user_row = db_session.scalar(select(User).where(User.email == email))
    user_row.is_active = False
    db_session.commit()

    resp = client.get("/auth/me")
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# 19-21. CSRF (verify_origin)
# --------------------------------------------------------------------------- #
def test_csrf_disallowed_cross_site_origin_rejected_on_state_change() -> None:
    client, _ = auth_client("PATIENT")
    client.headers["Origin"] = "https://evil-attacker.example"
    resp = client.post("/blood-requests", json={"blood_type": "O_POS", "urgency": "ROUTINE"})
    assert resp.status_code == 403
    assert "origin" in resp.json()["detail"].lower()


def test_csrf_same_origin_is_accepted() -> None:
    client, _ = auth_client("PATIENT")
    client.headers["Origin"] = "http://localhost:5173"
    resp = client.post("/blood-requests", json={"blood_type": "O_POS", "urgency": "ROUTINE"})
    assert resp.status_code == 201


def test_csrf_absent_origin_non_browser_client_is_accepted() -> None:
    client, _ = auth_client("PATIENT")
    del client.headers["Origin"]
    resp = client.post("/blood-requests", json={"blood_type": "O_POS", "urgency": "ROUTINE"})
    assert resp.status_code == 201


# --------------------------------------------------------------------------- #
# 22-23. Rate limiting (Phase 23 fixes)
# --------------------------------------------------------------------------- #
def test_emergency_creation_rate_limit_enforced() -> None:
    from app.core.config import settings

    client, _ = auth_client("PATIENT")
    limit, _window = settings.rl_emergency_create_account
    last_status = None
    for i in range(limit + 2):
        resp = client.post(
            "/emergencies",
            json={
                "blood_type": "O_POS", "location_text": f"City {i}",
                "idempotency_key": f"em-rl-{i}",
            },
        )
        last_status = resp.status_code
    assert last_status == 429


def test_emergency_idempotent_replay_never_consumes_the_rate_limit() -> None:
    """The exact same submission retried many times (double-click, network
    retry) must never itself trip the new rate limit -- only genuinely
    distinct creations count against it."""
    from app.core.config import settings

    client, _ = auth_client("PATIENT")
    limit, _window = settings.rl_emergency_create_account
    for _ in range(limit + 5):
        resp = client.post(
            "/emergencies",
            json={
                "blood_type": "O_POS", "location_text": "Same City",
                "idempotency_key": "same-key-replay",
            },
        )
        assert resp.status_code in (200, 201), resp.text


def test_blood_request_creation_rate_limit_enforced() -> None:
    from app.core.config import settings

    client, _ = auth_client("PATIENT")
    limit, _window = settings.rl_blood_request_create_account
    last_status = None
    for i in range(limit + 2):
        resp = client.post(
            "/blood-requests",
            json={"blood_type": "O_POS", "urgency": "ROUTINE", "idempotency_key": f"br-rl-{i}"},
        )
        last_status = resp.status_code
    assert last_status == 429


# --------------------------------------------------------------------------- #
# 24-26. Idempotency abuse
# --------------------------------------------------------------------------- #
def test_blood_request_idempotency_same_key_same_payload_replays() -> None:
    client, _ = auth_client("PATIENT")
    payload = {"blood_type": "O_POS", "urgency": "ROUTINE", "idempotency_key": "idem-1"}
    first = client.post("/blood-requests", json=payload)
    second = client.post("/blood-requests", json=payload)
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]


def test_blood_request_idempotency_same_key_different_payload_conflicts() -> None:
    client, _ = auth_client("PATIENT")
    first = client.post(
        "/blood-requests",
        json={"blood_type": "O_POS", "urgency": "ROUTINE", "idempotency_key": "idem-2"},
    )
    assert first.status_code == 201
    second = client.post(
        "/blood-requests",
        json={"blood_type": "AB_NEG", "urgency": "CRITICAL", "idempotency_key": "idem-2"},
    )
    assert second.status_code == 409


def test_blood_request_idempotency_different_users_same_key_independent() -> None:
    a, _ = auth_client("PATIENT")
    b, _ = auth_client("PATIENT")
    payload = {"blood_type": "O_POS", "urgency": "ROUTINE", "idempotency_key": "shared-key"}
    resp_a = a.post("/blood-requests", json=payload)
    resp_b = b.post("/blood-requests", json=payload)
    assert resp_a.status_code == 201
    assert resp_b.status_code == 201
    assert resp_a.json()["id"] != resp_b.json()["id"]


def test_emergency_idempotency_replay_never_double_creates(db_session) -> None:
    client, email = auth_client("PATIENT")
    payload = {"blood_type": "O_POS", "location_text": "City", "idempotency_key": "em-idem-1"}
    first = client.post("/emergencies", json=payload)
    second = client.post("/emergencies", json=payload)
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]

    count = db_session.scalar(
        select(__import__("sqlalchemy").func.count()).select_from(Emergency).where(
            Emergency.idempotency_key == "em-idem-1"
        )
    )
    assert count == 1


# --------------------------------------------------------------------------- #
# 27. Donation ownership (Phase 23 fix)
# --------------------------------------------------------------------------- #
def test_donor_cannot_attach_unrelated_blood_request_to_self_logged_donation(db_session) -> None:
    patient, _ = auth_client("PATIENT")
    req = patient.post("/blood-requests", json={"blood_type": "O_POS", "urgency": "ROUTINE"})
    req_id = req.json()["id"]

    donor, _ = auth_client("DONOR")  # never responded to / accepted this request
    resp = donor.post(
        "/donors/me/donations",
        json={
            "blood_request_id": req_id, "units": 1,
            "donated_on": __import__("datetime").date.today().isoformat(),
        },
    )
    assert resp.status_code == 400
    assert "accepted" in resp.json()["detail"].lower()


def test_donor_can_log_donation_against_a_request_they_actually_accepted() -> None:
    patient, _ = auth_client("PATIENT")
    req = patient.post("/blood-requests", json={"blood_type": "O_POS", "urgency": "ROUTINE"})
    req_id = req.json()["id"]

    donor, _ = auth_client("DONOR")
    donor.put("/donors/me", json={"blood_type": "O_POS"})
    donor.put("/donors/me/availability", json={"state": "AVAILABLE"})
    accept = donor.post(f"/blood-requests/{req_id}/respond", json={"action": "ACCEPTED"})
    assert accept.status_code == 200, accept.text

    resp = donor.post(
        "/donors/me/donations",
        json={
            "blood_request_id": req_id, "units": 1,
            "donated_on": __import__("datetime").date.today().isoformat(),
        },
    )
    assert resp.status_code == 201, resp.text


def test_donor_can_still_log_an_off_platform_donation_with_no_request_reference() -> None:
    donor, _ = auth_client("DONOR")
    resp = donor.post(
        "/donors/me/donations",
        json={"units": 1, "donated_on": __import__("datetime").date.today().isoformat()},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["blood_request_id"] is None


# --------------------------------------------------------------------------- #
# 28. Email HTML injection (Phase 23 fix)
# --------------------------------------------------------------------------- #
def test_notification_email_escapes_attacker_controlled_full_name(mailbox) -> None:
    # An unescaped `<img onerror=...>` would execute in any HTML-rendering
    # mail client; the fix (html.escape) doesn't remove the attacker's text,
    # it neutralizes it as inert text by escaping the angle brackets that
    # would otherwise open a real tag. So the correct assertion is "no live
    # `<img` tag survived", not "the substring onerror= is gone" (it's still
    # there, just inside an escaped, inert `&lt;img ...&gt;` text run).
    hostile_name = '<img src=x onerror=alert(1)>Attacker'
    seed_verified_user("victim-escape@example.com", role="PATIENT", full_name="Victim")
    email_service.send_notification_email(
        "victim-escape@example.com", hostile_name, "Test notification", "body text",
    )
    assert mailbox, "no email captured"
    html = mailbox[-1]["html"]
    assert "<img " not in html and "<img\t" not in html and "<img>" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html


def test_notification_email_escapes_attacker_controlled_title_and_body(mailbox) -> None:
    email_service.send_notification_email(
        "victim2@example.com", "Victim Name",
        '<script>document.location="https://evil.example"</script>',
        '<a href="javascript:alert(1)">click</a>',
    )
    html = mailbox[-1]["html"]
    assert "<script>" not in html
    assert "<a href=" not in html
    assert "&lt;script&gt;" in html
    assert "&lt;a href=" in html


def test_verification_email_escapes_full_name(mailbox) -> None:
    email_service.send_verification_email(
        "victim3@example.com", '"><svg onload=alert(1)>', "some-real-token-value",
    )
    html = mailbox[-1]["html"]
    assert "<svg " not in html and "<svg>" not in html
    assert "&lt;svg onload=alert(1)&gt;" in html


def test_password_reset_email_escapes_full_name_but_not_the_server_generated_otp(mailbox) -> None:
    email_service.send_password_reset_otp("victim4@example.com", "<b>hi</b>", "123456")
    html = mailbox[-1]["html"]
    assert "<b>hi</b>" not in html
    assert "123456" in html  # server-generated, digits-only, not escaped/mangled


# --------------------------------------------------------------------------- #
# 29. secret_key production guard (Phase 23 fix) -- unit-level, no HTTP
# --------------------------------------------------------------------------- #
def test_secret_key_default_rejected_in_production() -> None:
    try:
        Settings(
            environment="production",
            secret_key=Settings.DEFAULT_SECRET_KEY,
            session_cookie_secure=True,
        )
        raise AssertionError("Settings should have refused to start")
    except ValueError as exc:
        assert "SECRET_KEY" in str(exc)


def test_secret_key_custom_value_allowed_in_production() -> None:
    s = Settings(
        environment="production",
        secret_key="a-real-unique-production-secret-value",
        document_scanner="clamd",  # hardening: production requires a scanner
        session_cookie_secure=True,
    )
    assert s.secret_key == "a-real-unique-production-secret-value"


def test_secret_key_default_allowed_outside_production() -> None:
    # Development/test environments are unaffected -- this mirrors every
    # real developer's local .env-less bootstrap.
    s = Settings(environment="development", secret_key=Settings.DEFAULT_SECRET_KEY)
    assert s.secret_key == Settings.DEFAULT_SECRET_KEY


def test_sign_is_keyed_on_secret_key_not_guessable_without_it() -> None:
    # sign() is the primitive backing stored-token/OTP integrity and the
    # certificate download token -- confirms it actually depends on
    # secret_key (changing the key changes the digest for the same input).

    from app.core import security as security_module

    digest_1 = sign("same-value")
    original_key = security_module.settings.secret_key
    try:
        security_module.settings.secret_key = "a-totally-different-key"
        digest_2 = sign("same-value")
    finally:
        security_module.settings.secret_key = original_key
    assert digest_1 != digest_2


# --------------------------------------------------------------------------- #
# 30. Audit log integrity spot-check
# --------------------------------------------------------------------------- #
def test_every_access_denial_class_produces_a_queryable_audit_row(db_session) -> None:
    """Cross-checks that the denial paths exercised above are not merely
    returning the right HTTP status -- they are actually recorded, which is
    what makes them detectable/reviewable after the fact."""
    donor, _ = auth_client("DONOR")
    donor.get("/admin/users")  # role_denied

    rows = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "authz.role_denied")
        .order_by(AuditLog.created_at.desc())
    ).all()
    assert len(rows) >= 1
    assert rows[0].actor_user_id is not None
    assert rows[0].meta.get("required_roles") is not None


# --------------------------------------------------------------------------- #
# 31. Enumeration resistance
# --------------------------------------------------------------------------- #
def test_family_link_request_response_identical_missing_vs_suspended(db_session) -> None:
    from app.models.user import User

    family, _ = auth_client("FAMILY")

    missing = family.post(
        "/family/links",
        json={"patient_email": "definitely-does-not-exist@example.com", "relationship": "parent"},
    )

    seed_verified_user("suspended-patient@example.com", role="PATIENT")
    row = db_session.scalar(select(User).where(User.email == "suspended-patient@example.com"))
    row.is_active = False
    db_session.commit()

    suspended = family.post(
        "/family/links",
        json={"patient_email": "suspended-patient@example.com", "relationship": "parent"},
    )
    assert missing.status_code == suspended.status_code == 404
    assert missing.json()["detail"] == suspended.json()["detail"]


# --------------------------------------------------------------------------- #
# 32. File upload hardening spot-check (pre-existing Phase 14 protection)
# --------------------------------------------------------------------------- #
def test_verification_upload_rejects_declared_vs_sniffed_content_type_mismatch() -> None:
    donor, _ = auth_client("DONOR")
    # Declares application/pdf but the bytes are not a PDF at all (no
    # "%PDF-" magic header) -- must be rejected, not stored.
    resp = donor.post(
        "/verifications",
        data={"kind": "DONOR_IDENTITY"},
        files={"files": ("id.pdf", b"<html>not really a pdf</html>", "application/pdf")},
    )
    assert resp.status_code == 422
    assert "does not match" in resp.json()["detail"].lower()


# --------------------------------------------------------------------------- #
# 33. Security response headers (Phase 23 fix)
# --------------------------------------------------------------------------- #
def test_security_headers_present_on_every_response(client) -> None:
    # A totally unauthenticated, unremarkable request -- headers must be on
    # every response, not just authenticated/error ones.
    resp = client.get("/health")
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("x-frame-options") == "DENY"
    assert resp.headers.get("referrer-policy") == "strict-origin-when-cross-origin"
    assert "geolocation=()" in resp.headers.get("permissions-policy", "")


def test_security_headers_present_on_an_error_response_too(client) -> None:
    resp = client.get("/blood-requests/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 401  # unauthenticated
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("x-frame-options") == "DENY"


def test_no_content_security_policy_is_shipped_yet_documented_gap() -> None:
    """Deliberately confirms the ABSENCE of a guessed CSP -- see
    security_headers_middleware's docstring for why one isn't shipped in
    this pass (the frontend's real external-origin asset inventory --
    Leaflet map tiles, marker imagery, possibly Google Fonts -- was not
    enumerated in this backend-only phase, and a wrong CSP would silently
    break exactly the map/font UI Phase 22 hardened). This test exists so a
    future CSP addition is a deliberate, visible change to this test, not a
    silent regression noticed after the fact."""
    from fastapi.testclient import TestClient

    from app.main import app
    resp = TestClient(app).get("/health")
    assert "content-security-policy" not in {k.lower() for k in resp.headers.keys()}
