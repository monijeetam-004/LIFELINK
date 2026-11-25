from __future__ import annotations

from app.api.routes import verifications
from app.core.config import settings
from app.main import app
from tests.helpers import auth_client

# This router is wired into app.main by another agent's centralized change;
# registering it here (once, at import time) keeps this test module
# self-contained until that integration lands.
app.include_router(verifications.router)


PDF = ("id.pdf", b"%PDF-1.4 fake donor id document", "application/pdf")
PNG = ("id.png", b"\x89PNG\r\n\x1a\nfakepngbytes", "image/png")


def _submit(client, kind: str, files=None, note=None):
    files = files or [PDF]
    upload = [("files", f) for f in files]
    data = {"kind": kind}
    if note is not None:
        data["note"] = note
    return client.post("/verifications", data=data, files=upload)


# --------------------------------------------------------------------------- #
# Upload validation
# --------------------------------------------------------------------------- #


def test_zero_files_rejected() -> None:
    donor, _ = auth_client("DONOR")
    r = donor.post("/verifications", data={"kind": "DONOR_IDENTITY"})
    assert r.status_code == 422


def test_too_many_files_rejected() -> None:
    donor, _ = auth_client("DONOR")
    too_many = [
        ("files", (f"doc{i}.pdf", b"%PDF-1.4 x", "application/pdf"))
        for i in range(settings.verification_max_files + 1)
    ]
    r = donor.post("/verifications", data={"kind": "DONOR_IDENTITY"}, files=too_many)
    assert r.status_code == 422


def test_oversized_file_rejected() -> None:
    donor, _ = auth_client("DONOR")
    big = b"x" * (settings.verification_max_file_bytes + 1)
    r = donor.post(
        "/verifications",
        data={"kind": "DONOR_IDENTITY"},
        files=[("files", ("big.pdf", big, "application/pdf"))],
    )
    assert r.status_code == 422


def test_bad_content_type_rejected() -> None:
    donor, _ = auth_client("DONOR")
    r = donor.post(
        "/verifications",
        data={"kind": "DONOR_IDENTITY"},
        files=[("files", ("virus.exe", b"MZ...", "application/x-msdownload"))],
    )
    assert r.status_code == 422


# --------------------------------------------------------------------------- #
# Role / kind matching
# --------------------------------------------------------------------------- #


def test_donor_cannot_submit_hospital_kind() -> None:
    donor, _ = auth_client("DONOR")
    r = _submit(donor, "HOSPITAL_FACILITY")
    assert r.status_code == 403


def test_hospital_cannot_submit_donor_kind() -> None:
    hospital, _ = auth_client("HOSPITAL")
    r = _submit(hospital, "DONOR_IDENTITY")
    assert r.status_code == 403


def test_patient_cannot_submit_either_kind() -> None:
    patient, _ = auth_client("PATIENT")
    assert _submit(patient, "DONOR_IDENTITY").status_code in (403, 401)


# --------------------------------------------------------------------------- #
# Duplicate pending + rate limit
# --------------------------------------------------------------------------- #


def test_duplicate_pending_submission_conflicts() -> None:
    donor, _ = auth_client("DONOR")
    r1 = _submit(donor, "DONOR_IDENTITY", note="first")
    assert r1.status_code == 201, r1.text
    r2 = _submit(donor, "DONOR_IDENTITY", note="second")
    assert r2.status_code == 409


def test_submission_rate_limited_per_account() -> None:
    donor, _ = auth_client("DONOR")
    limit, _window = settings.rl_verification_submit_account
    codes = [_submit(donor, "DONOR_IDENTITY").status_code for _ in range(limit + 1)]
    # first call succeeds, the rest hit the duplicate-pending 409 until the
    # rate limiter itself kicks in on the call after `limit` attempts
    assert codes[0] == 201
    assert codes[-1] == 429


# --------------------------------------------------------------------------- #
# /verifications/me
# --------------------------------------------------------------------------- #


def test_me_only_shows_own_requests() -> None:
    donor_a, _ = auth_client("DONOR")
    donor_b, _ = auth_client("DONOR")
    assert _submit(donor_a, "DONOR_IDENTITY").status_code == 201
    assert _submit(donor_b, "DONOR_IDENTITY").status_code == 201

    mine = donor_a.get("/verifications/me").json()
    assert len(mine) == 1
    assert mine[0]["kind"] == "DONOR_IDENTITY"
    assert mine[0]["status"] == "PENDING"
    # storage key must never be exposed
    assert "key" not in mine[0]["documents"][0]
    assert mine[0]["documents"][0]["filename"] == "id.pdf"

    other = donor_b.get("/verifications/me").json()
    assert len(other) == 1 and other[0]["id"] != mine[0]["id"]


# --------------------------------------------------------------------------- #
# Admin authorization
# --------------------------------------------------------------------------- #


def test_admin_routes_403_for_non_admin() -> None:
    donor, _ = auth_client("DONOR")
    r = _submit(donor, "DONOR_IDENTITY")
    vid = r.json()["id"]

    non_admin_clients = [
        auth_client("DONOR")[0], auth_client("HOSPITAL")[0], auth_client("PATIENT")[0]
    ]
    for c in non_admin_clients:
        assert c.get("/admin/verifications").status_code == 403
        assert c.get(f"/admin/verifications/{vid}").status_code == 403
        assert c.get(f"/admin/verifications/{vid}/documents/0").status_code == 403
        assert c.post(f"/admin/verifications/{vid}/approve", json={}).status_code == 403
        assert (
            c.post(f"/admin/verifications/{vid}/reject", json={"reason": "no"}).status_code == 403
        )


# --------------------------------------------------------------------------- #
# Document round-trip through real storage
# --------------------------------------------------------------------------- #


def test_document_download_roundtrips_bytes_and_404s_on_bad_index() -> None:
    donor, _ = auth_client("DONOR")
    admin, _ = auth_client("ADMIN")
    r = _submit(donor, "DONOR_IDENTITY", files=[PDF, PNG])
    vid = r.json()["id"]

    got0 = admin.get(f"/admin/verifications/{vid}/documents/0")
    assert got0.status_code == 200
    assert got0.content == PDF[1]
    assert got0.headers["content-type"].startswith("application/pdf")

    got1 = admin.get(f"/admin/verifications/{vid}/documents/1")
    assert got1.status_code == 200
    assert got1.content == PNG[1]

    assert admin.get(f"/admin/verifications/{vid}/documents/2").status_code == 404
    assert admin.get(f"/admin/verifications/{vid}/documents/-1").status_code == 404

    import uuid

    assert admin.get(f"/admin/verifications/{uuid.uuid4()}/documents/0").status_code == 404


# --------------------------------------------------------------------------- #
# Approve
# --------------------------------------------------------------------------- #


def test_approve_donor_identity_flips_identity_verified_and_notifies() -> None:
    donor, _ = auth_client("DONOR")
    admin, _ = auth_client("ADMIN")
    vid = _submit(donor, "DONOR_IDENTITY").json()["id"]

    assert donor.get("/donors/me").json()["identity_verified"] is False

    r = admin.post(f"/admin/verifications/{vid}/approve", json={"note": "looks good"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "APPROVED"
    assert body["review_note"] == "looks good"

    assert donor.get("/donors/me").json()["identity_verified"] is True
    assert any(
        n["type"] == "verification.approved" for n in donor.get("/notifications").json()
    )


def test_approve_hospital_facility_flips_verified_and_notifies() -> None:
    hospital, _ = auth_client("HOSPITAL")
    admin, _ = auth_client("ADMIN")
    vid = _submit(hospital, "HOSPITAL_FACILITY").json()["id"]

    assert hospital.get("/hospitals/me").json()["verified"] is False

    r = admin.post(f"/admin/verifications/{vid}/approve", json={})
    assert r.status_code == 200

    assert hospital.get("/hospitals/me").json()["verified"] is True
    assert any(
        n["type"] == "verification.approved" for n in hospital.get("/notifications").json()
    )


# --------------------------------------------------------------------------- #
# Reject
# --------------------------------------------------------------------------- #


def test_reject_requires_a_reason() -> None:
    donor, _ = auth_client("DONOR")
    admin, _ = auth_client("ADMIN")
    vid = _submit(donor, "DONOR_IDENTITY").json()["id"]

    assert admin.post(f"/admin/verifications/{vid}/reject", json={}).status_code == 422
    # "ok" is 2 characters, below the required min_length=3
    assert (
        admin.post(f"/admin/verifications/{vid}/reject", json={"reason": "ok"}).status_code == 422
    )


def test_reject_with_reason_sets_status_and_is_retrievable_and_notifies() -> None:
    donor, _ = auth_client("DONOR")
    admin, _ = auth_client("ADMIN")
    vid = _submit(donor, "DONOR_IDENTITY").json()["id"]

    r = admin.post(
        f"/admin/verifications/{vid}/reject", json={"reason": "Document is blurry / unreadable"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "REJECTED"
    assert body["review_note"] == "Document is blurry / unreadable"

    mine = donor.get("/verifications/me").json()[0]
    assert mine["status"] == "REJECTED"
    assert mine["review_note"] == "Document is blurry / unreadable"

    assert donor.get("/donors/me").json()["identity_verified"] is False
    assert any(
        n["type"] == "verification.rejected" for n in donor.get("/notifications").json()
    )


# --------------------------------------------------------------------------- #
# Already-decided conflicts
# --------------------------------------------------------------------------- #


def test_approving_already_decided_request_conflicts() -> None:
    donor, _ = auth_client("DONOR")
    admin, _ = auth_client("ADMIN")
    vid = _submit(donor, "DONOR_IDENTITY").json()["id"]

    assert admin.post(f"/admin/verifications/{vid}/approve", json={}).status_code == 200
    assert admin.post(f"/admin/verifications/{vid}/approve", json={}).status_code == 409
    assert (
        admin.post(f"/admin/verifications/{vid}/reject", json={"reason": "too late"}).status_code
        == 409
    )


def test_rejecting_already_decided_request_conflicts() -> None:
    donor, _ = auth_client("DONOR")
    admin, _ = auth_client("ADMIN")
    vid = _submit(donor, "DONOR_IDENTITY").json()["id"]

    assert (
        admin.post(f"/admin/verifications/{vid}/reject", json={"reason": "not clear"}).status_code
        == 200
    )
    assert (
        admin.post(f"/admin/verifications/{vid}/reject", json={"reason": "again"}).status_code
        == 409
    )
    assert admin.post(f"/admin/verifications/{vid}/approve", json={}).status_code == 409


# --------------------------------------------------------------------------- #
# Admin listing
# --------------------------------------------------------------------------- #


def test_admin_list_filters_by_kind_and_status() -> None:
    donor, _ = auth_client("DONOR")
    hospital, _ = auth_client("HOSPITAL")
    admin, _ = auth_client("ADMIN")
    _submit(donor, "DONOR_IDENTITY")
    _submit(hospital, "HOSPITAL_FACILITY")

    all_reqs = admin.get("/admin/verifications").json()
    assert len(all_reqs) == 2

    donor_only = admin.get("/admin/verifications", params={"kind": "DONOR_IDENTITY"}).json()
    assert len(donor_only) == 1 and donor_only[0]["kind"] == "DONOR_IDENTITY"

    pending_only = admin.get("/admin/verifications", params={"status": "PENDING"}).json()
    assert len(pending_only) == 2

    vid = donor_only[0]["id"]
    admin.post(f"/admin/verifications/{vid}/approve", json={})
    approved_only = admin.get("/admin/verifications", params={"status": "APPROVED"}).json()
    assert len(approved_only) == 1 and approved_only[0]["id"] == vid
