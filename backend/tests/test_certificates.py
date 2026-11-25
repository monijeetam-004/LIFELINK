from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient

from app.main import app
from tests.helpers import auth_client


def _donor_with_donation(blood_type: str = "O_NEG"):
    donor, email = auth_client("DONOR")
    donor.put("/donors/me", json={"blood_type": blood_type})
    r = donor.post("/donors/me/donations", json={
        "donated_on": date.today().isoformat(), "units": 1, "hospital_name": "Metro General",
    })
    assert r.status_code == 201, r.text
    did = r.json()["id"]
    # Phase E: certificate issuance is now gated on verification_status ==
    # VERIFIED (app.api.routes.certificates.issue_certificate) -- this
    # donation has no hospital_id (free-text hospital_name only, same as
    # every pre-Phase-E donation), so only an admin can review it. This
    # helper exists to hand callers a donation ready for certificate
    # issuance, so it performs that one real review step itself rather
    # than having every certificate test re-derive it.
    admin, _ = auth_client("ADMIN")
    v = admin.post(f"/donations/{did}/verify", json={})
    assert v.status_code == 200, v.text
    return donor, email, did


def test_issue_certificate_is_idempotent_and_persisted() -> None:
    donor, _, did = _donor_with_donation()

    r = donor.post("/certificates", json={"donation_id": did})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["donation_id"] == did
    assert body["serial"].startswith("LL-")
    assert len(body["verify_code"]) >= 16
    assert body["donor_name"] == "Donor User"
    assert body["units"] == 1 and body["hospital_name"] == "Metro General"

    # issuing again returns the same certificate (no duplicate)
    again = donor.post("/certificates", json={"donation_id": did})
    assert again.status_code == 201
    assert again.json()["id"] == body["id"]
    assert len(donor.get("/certificates").json()) == 1

    # a certificate.ready notification was created
    assert any(n["type"] == "certificate.ready" for n in donor.get("/notifications").json())


def test_pdf_download_via_cookie_and_signed_token() -> None:
    donor, _, did = _donor_with_donation()
    cert = donor.post("/certificates", json={"donation_id": did}).json()
    cid, token = cert["id"], cert["download_token"]

    # authenticated (cookie) download
    r = donor.get(f"/certificates/{cid}/download")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content[:4] == b"%PDF"
    assert cert["serial"] in r.headers.get("content-disposition", "")

    # signed-token download works without a session cookie
    anon = TestClient(app)
    r = anon.get(f"/certificates/{cid}/download", params={"token": token})
    assert r.status_code == 200 and r.content[:4] == b"%PDF"

    # a bogus token is rejected
    bogus = anon.get(f"/certificates/{cid}/download", params={"token": "0.deadbeef"})
    assert bogus.status_code == 401
    # no auth at all is rejected
    assert anon.get(f"/certificates/{cid}/download").status_code == 401


def test_public_verification_exposes_safe_fields_only() -> None:
    donor, _, did = _donor_with_donation()
    cert = donor.post("/certificates", json={"donation_id": did}).json()

    anon = TestClient(app)
    r = anon.get(f"/certificates/verify/{cert['verify_code']}")
    assert r.status_code == 200
    v = r.json()
    assert v == {
        "valid": True,
        "serial": cert["serial"],
        "donor_name": "Donor User",
        "donated_on": date.today().isoformat(),
        "units": 1,
        "hospital_name": "Metro General",
        "issued_at": v["issued_at"],
        # Phase 31: revocation status is a safe, public field -- an unrevoked
        # certificate reports both as None/absent-of-info, never omitted.
        "revoked_at": None,
        "revocation_reason": None,
    }
    # no storage key / ids / verify code leaked
    assert "storage_key" not in v and "verify_code" not in v and "id" not in v

    # unknown / guessed code -> 404
    assert anon.get("/certificates/verify/not-a-real-code").status_code == 404


def test_ownership_and_role_authorization() -> None:
    donor, _, did = _donor_with_donation()
    cert = donor.post("/certificates", json={"donation_id": did}).json()
    cid = cert["id"]

    other_donor, _, _ = _donor_with_donation()
    patient, _ = auth_client("PATIENT")

    # another donor cannot read metadata or download
    assert other_donor.get(f"/certificates/{cid}").status_code == 404
    assert other_donor.get(f"/certificates/{cid}/download").status_code == 401
    # a patient has no certificates endpoint access
    assert patient.get("/certificates").status_code == 403
    # cannot issue a certificate for someone else's donation
    assert other_donor.post("/certificates", json={"donation_id": did}).status_code == 404
    # non-donor cannot issue
    assert patient.post("/certificates", json={"donation_id": did}).status_code == 403
    # anonymous cannot list or read
    anon = TestClient(app)
    assert anon.get("/certificates").status_code == 401
    assert anon.get(f"/certificates/{cid}").status_code == 401


def test_issue_requires_a_real_owned_donation() -> None:
    donor, _ = auth_client("DONOR")
    import uuid

    assert donor.post("/certificates", json={"donation_id": str(uuid.uuid4())}).status_code == 404
