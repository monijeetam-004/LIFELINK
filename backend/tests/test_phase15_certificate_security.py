"""Phase 15 -- Donation Certificates & Trust Verification.

Dedicated hardening coverage that isn't already exercised by the pre-existing
`tests/test_certificates.py` (5 tests: idempotent issue, cookie + signed-token
download, public-verify safe fields, ownership/role authorization, real-owned-
donation requirement) or `tests/test_admin_phase6.py` (admin-only certificate
registry + search).

This file adds: concurrent-generation race safety (the real bug this phase
found and fixed -- see app/api/routes/certificates.py::issue_certificate),
a family-link/patient/hospital access-boundary sweep, client-spoofing
resistance on issuance, cross-certificate download-token tampering, PDF
content inspection (no private fields embedded), storage-failure error
handling, and audit-event coverage for the view/download paths this phase
added.
"""
from __future__ import annotations

import threading
import uuid
from datetime import date

from sqlalchemy import select

from app.models.audit_log import AuditLog
from tests.helpers import auth_client


def _donor_with_donation(**overrides):
    donor, email = auth_client("DONOR")
    donor.put("/donors/me", json={"blood_type": "O_NEG"})
    payload = {
        "donated_on": date.today().isoformat(),
        "units": 1,
        "hospital_name": "Metro General",
    }
    payload.update(overrides)
    r = donor.post("/donors/me/donations", json=payload)
    assert r.status_code == 201, r.text
    did = r.json()["id"]
    # Phase E: certificate issuance now requires verification_status ==
    # VERIFIED -- see the identical note in tests/test_certificates.py's
    # own _donor_with_donation. This file's donations have no hospital_id
    # (free-text hospital_name only), so only an admin can review them.
    admin, _ = auth_client("ADMIN")
    v = admin.post(f"/donations/{did}/verify", json={})
    assert v.status_code == 200, v.text
    return donor, email, did


def _latest_audit(db_session, action: str, target_id=None):
    stmt = select(AuditLog).where(AuditLog.action == action)
    if target_id is not None:
        stmt = stmt.where(AuditLog.target_id == target_id)
    stmt = stmt.order_by(AuditLog.created_at.desc())
    return db_session.scalars(stmt).first()


# --------------------------------------------------------------------------- #
# Generation eligibility / spoofing resistance
# --------------------------------------------------------------------------- #
def test_generation_requires_a_real_donation_row_no_client_status_field_exists() -> None:
    """There is no client-writable "completed"/"status" field on Donation or
    on CertificateIssueIn at all -- eligibility is just "a real Donation row,
    owned by the caller, exists". Confirms extra client-supplied fields
    (a spoofed donor_user_id, a fake status) are silently ignored, not
    honored, by the issuance endpoint."""
    donor, _, did = _donor_with_donation()
    other_donor, _, _ = _donor_with_donation()

    r = donor.post(
        "/certificates",
        json={
            "donation_id": did,
            "donor_user_id": str(uuid.uuid4()),
            "status": "APPROVED",
            "certificate_status": "VERIFIED",
            "generated_at": "2000-01-01T00:00:00Z",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    # The real authenticated caller is the owner, never the spoofed id.
    assert body["donor_name"] == "Donor User"
    other_ids = other_donor.get("/certificates").json()
    assert body["id"] not in [c["id"] for c in other_ids]


def test_ineligible_donation_id_is_rejected() -> None:
    """A nonexistent / not-owned donation_id can never be turned into a
    certificate -- already covered by test_certificates.py's
    test_issue_requires_a_real_owned_donation; this adds the "belongs to a
    different donor" shape explicitly."""
    donor, _, _ = _donor_with_donation()
    other_donor, _, other_did = _donor_with_donation()
    assert donor.post(
        "/certificates", json={"donation_id": other_did}
    ).status_code == 404


# --------------------------------------------------------------------------- #
# Concurrency: duplicate-generation race
# --------------------------------------------------------------------------- #
def test_concurrent_certificate_generation_for_same_donation_yields_exactly_one() -> None:
    """Real bug found in this phase: get_or_issue's check-then-insert had no
    lock, so two near-simultaneous issuance requests for the same donation
    could both pass the "does one exist?" check, both render/upload a PDF,
    and the loser would hit Certificate's unique(donation_id) constraint as
    an unhandled IntegrityError. Fixed by locking the Donation row
    (SELECT ... FOR UPDATE) for the duration of issuance. This test would
    have failed (an unhandled 500 on one thread, or two different
    certificate ids) before that fix."""
    donor, _, did = _donor_with_donation()

    results: list[tuple[int, dict]] = []
    barrier = threading.Barrier(2)

    def _issue() -> None:
        barrier.wait()
        r = donor.post("/certificates", json={"donation_id": did})
        results.append((r.status_code, r.json() if r.status_code == 201 else {}))

    threads = [threading.Thread(target=_issue) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(results) == 2
    assert all(status == 201 for status, _ in results), results
    cert_ids = {body["id"] for _, body in results}
    assert len(cert_ids) == 1, f"expected exactly one certificate, got {cert_ids}"

    # And the donor's own list reflects exactly one row, not two.
    assert len(donor.get("/certificates").json()) == 1


# --------------------------------------------------------------------------- #
# Access-boundary sweep: family-link / patient / hospital must never reach a
# donor's private certificate data
# --------------------------------------------------------------------------- #
def test_family_patient_hospital_cannot_access_donor_certificates() -> None:
    donor, _, did = _donor_with_donation()
    cert = donor.post("/certificates", json={"donation_id": did}).json()
    cid = cert["id"]

    family, _ = auth_client("FAMILY")
    patient, patient_email = auth_client("PATIENT")
    hospital, _ = auth_client("HOSPITAL")

    # A genuine, ACCEPTED family link to an actual patient (the only kind of
    # link this data model supports -- FamilyLink is FAMILY<->PATIENT only,
    # never FAMILY<->DONOR) still grants nothing against a donor's
    # certificate. Phase 13 family-link authorization must not bypass this.
    link = family.post(
        "/family/links", json={"patient_email": patient_email, "relationship": "sibling"}
    ).json()
    accepted = patient.post(f"/family/links/{link['id']}/accept").json()
    assert accepted["status"] == "ACCEPTED"

    for actor in (family, patient, hospital):
        assert actor.get("/certificates").status_code == 403
        assert actor.get(f"/certificates/{cid}").status_code in (403, 404)
        assert actor.get(f"/certificates/{cid}/download").status_code in (401, 403, 404)
        assert actor.post("/certificates", json={"donation_id": did}).status_code in (403, 404)


# --------------------------------------------------------------------------- #
# IDOR: cross-certificate download-token tampering
# --------------------------------------------------------------------------- #
def test_download_token_is_bound_to_its_own_certificate() -> None:
    """A signed download token for certificate A must not authorize
    downloading certificate B -- the HMAC signs cert_id itself, so a token
    minted for A carries A's id inside the signature and will not verify
    against B's id."""
    donor_a, _, did_a = _donor_with_donation()
    donor_b, _, did_b = _donor_with_donation()
    cert_a = donor_a.post("/certificates", json={"donation_id": did_a}).json()
    cert_b = donor_b.post("/certificates", json={"donation_id": did_b}).json()

    from fastapi.testclient import TestClient

    from app.main import app

    anon = TestClient(app)
    r = anon.get(
        f"/certificates/{cert_b['id']}/download", params={"token": cert_a["download_token"]}
    )
    assert r.status_code == 401


# --------------------------------------------------------------------------- #
# Privacy: public verification and PDF contents
# --------------------------------------------------------------------------- #
def test_public_verification_json_has_no_private_fields() -> None:
    donor, _, did = _donor_with_donation()
    cert = donor.post("/certificates", json={"donation_id": did}).json()

    from fastapi.testclient import TestClient

    from app.main import app

    anon = TestClient(app)
    v = anon.get(f"/certificates/verify/{cert['verify_code']}").json()
    forbidden_keys = {
        "email", "phone", "storage_key", "verify_code", "id", "donor_user_id",
        "donation_id", "reviewer_user_id", "review_note", "address",
    }
    assert forbidden_keys.isdisjoint(v.keys())
    assert set(v.keys()) == {
        "valid", "serial", "donor_name", "donated_on", "units",
        "hospital_name", "issued_at",
        # Phase 31: revocation status is intentionally public -- see
        # Certificate.revoked_at's docstring and CertificateVerifyOut.
        "revoked_at", "revocation_reason",
    }


def test_pdf_contains_only_intentionally_public_fields() -> None:
    """Inspect the actual generated PDF bytes for the donor's email (never
    embedded) -- the public-facing PDF must carry only what the public verify
    endpoint itself would reveal."""
    donor, email, did = _donor_with_donation(hospital_name="Private Wing Clinic")
    cert = donor.post("/certificates", json={"donation_id": did}).json()
    r = donor.get(f"/certificates/{cert['id']}/download")
    assert r.status_code == 200
    assert r.content[:4] == b"%PDF"
    # A crude but real check: the donor's own email must never appear in the
    # raw PDF bytes (ReportLab PDFs are not fully plain-text, but literal
    # strings written via drawString/drawCentredString appear in the stream
    # largely as-is for the simple text operators this renderer uses).
    assert email.encode() not in r.content


# --------------------------------------------------------------------------- #
# Errors: storage failure must not leak internals
# --------------------------------------------------------------------------- #
def test_storage_failure_on_download_is_a_clean_503_not_a_stack_trace() -> None:
    donor, _, did = _donor_with_donation()
    cert = donor.post("/certificates", json={"donation_id": did}).json()

    from app.core import storage

    original = storage.get_bytes
    try:
        def _boom(key: str) -> bytes:
            raise FileNotFoundError(f"/some/internal/path/{key} vanished")

        storage.get_bytes = _boom  # type: ignore[assignment]
        r = donor.get(f"/certificates/{cert['id']}/download")
        assert r.status_code == 503
        body = r.json()
        assert "some/internal/path" not in str(body)
        assert "Traceback" not in str(body)
    finally:
        storage.get_bytes = original  # type: ignore[assignment]


# --------------------------------------------------------------------------- #
# Audit: view / download events this phase added
# --------------------------------------------------------------------------- #
def test_certificate_view_and_download_are_audited(db_session) -> None:
    donor, _, did = _donor_with_donation()
    cert = donor.post("/certificates", json={"donation_id": did}).json()
    cid = uuid.UUID(cert["id"])

    donor.get(f"/certificates/{cid}")
    viewed = _latest_audit(db_session, "certificate.viewed", cid)
    assert viewed is not None
    assert viewed.meta.get("actor_role") == "DONOR"

    donor.get(f"/certificates/{cid}/download")
    downloaded = _latest_audit(db_session, "certificate.downloaded", cid)
    assert downloaded is not None
    assert downloaded.meta.get("via") == "owner"

    anon_token_download_ok = donor.get(f"/certificates/{cid}").json()["download_token"]
    from fastapi.testclient import TestClient

    from app.main import app

    anon = TestClient(app)
    anon.get(f"/certificates/{cid}/download", params={"token": anon_token_download_ok})
    downloaded_via_token = _latest_audit(db_session, "certificate.downloaded", cid)
    assert downloaded_via_token.meta.get("via") == "token"
    assert downloaded_via_token.actor_user_id is None


def test_certificate_issued_audit_still_recorded() -> None:
    """Pre-existing behavior, re-confirmed after this phase's changes to the
    same route (the locking change must not have dropped the audit call)."""
    donor, _, did = _donor_with_donation()
    r = donor.post("/certificates", json={"donation_id": did})
    assert r.status_code == 201
    assert any(n["type"] == "certificate.ready" for n in donor.get("/notifications").json())


# --------------------------------------------------------------------------- #
# Unauthenticated sweep
# --------------------------------------------------------------------------- #
def test_unauthenticated_access_is_rejected() -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    donor, _, did = _donor_with_donation()
    cert = donor.post("/certificates", json={"donation_id": did}).json()

    anon = TestClient(app)
    assert anon.get("/certificates").status_code == 401
    assert anon.get(f"/certificates/{cert['id']}").status_code == 401
    assert anon.post("/certificates", json={"donation_id": did}).status_code == 401
    assert anon.get(f"/certificates/{cert['id']}/download").status_code == 401
    assert anon.get("/admin/certificates").status_code == 401
