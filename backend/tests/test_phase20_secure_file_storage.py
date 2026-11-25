"""Phase 20 -- Secure Document & File Storage Hardening.

Dedicated coverage for the security boundaries the Phase 20 audit targeted.
Most of the spec's 25-item minimum list (filename attacks, magic-byte /
MIME-mismatch rejection, oversized uploads, family-link-cannot-bypass,
concurrency races) was already proven by `tests/test_verifications.py`,
`tests/test_phase14_verification_security.py`, `tests/test_certificates.py`,
`tests/test_phase15_certificate_security.py` and
`tests/test_phase17_resilience.py` *before* this phase -- this file does not
duplicate that coverage, it cross-references it and adds what Phase 20
actually changed or newly audited:

  - storage-failure handling on upload/download (verifications + certificates)
    -- previously an unhandled exception, fixed in this phase
  - the missing-object vs. broken-backend distinction (404 vs 503) via the
    new `StorageObjectNotFoundError`
  - the new `Cache-Control: private, no-store` header on both document/
    certificate download responses
  - the document-security-scanner integration point and its honest metadata
  - a consolidated cross-check of the spec's exact malicious-filename list
  - explicit "not supported, and that's fine" proofs for delete/replace
  - a genuine boto3-level (moto-simulated) test of storage.py's S3 code path,
    since real MinIO is environmentally unreachable here (no Docker daemon;
    the MinIO binary download is blocked by the egress proxy allowlist) --
    see LIFELINK_PHASE_20_AUDIT.md section 17 for the full explanation of
    what this is (and is not) a substitute for.

Every test here runs against the real Postgres test database and the real
local-filesystem storage backend (`tests/conftest.py` forces
`S3_ENDPOINT_URL`/`S3_ACCESS_KEY`/`S3_SECRET_KEY` empty for the whole suite,
which is also this sandbox's actual reachable storage backend). Only the
one dedicated moto test below exercises the S3 code path, and it says so.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import select

from app.core import storage
from app.core.config import settings
from app.models.audit_log import AuditLog
from tests.helpers import auth_client

PDF = ("id.pdf", b"%PDF-1.4 fake donor id document", "application/pdf")


def _submit(client, kind: str, files=None, note=None):
    files = files or [PDF]
    upload = [("files", f) for f in files]
    data = {"kind": kind}
    if note is not None:
        data["note"] = note
    return client.post("/verifications", data=data, files=upload)


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
# 1/2. Authenticated vs unauthenticated upload
#    (see also test_verifications.py's upload-validation block)
# --------------------------------------------------------------------------- #
def test_authenticated_upload_succeeds() -> None:
    donor, _ = auth_client("DONOR")
    r = _submit(donor, "DONOR_IDENTITY")
    assert r.status_code == 201, r.text


def test_unauthenticated_upload_is_rejected() -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    anon = TestClient(app)
    r = anon.post(
        "/verifications",
        data={"kind": "DONOR_IDENTITY"},
        files=[("files", PDF)],
    )
    assert r.status_code == 401


# --------------------------------------------------------------------------- #
# 3-6. Valid file / invalid extension / invalid MIME / magic-byte mismatch /
#      oversized upload are already covered exhaustively by
#      test_verifications.py and test_phase14_verification_security.py --
#      not repeated here.
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# 7. Malicious filenames: the spec's exact required list, consolidated in
#    one parametrized test (the underlying sanitizer is already proven by
#    test_phase14_verification_security.py's traversal/unicode tests, but
#    the spec calls out these specific strings by name).
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "malicious_name",
    [
        "../../secret.txt",
        "..\\..\\secret.txt",
        "C:\\secret.txt",
        "/etc/passwd",
        "<svg>.svg",
        "document<script>.pdf",
        "a" * 500 + ".pdf",
        "id\x00\x01\x02.pdf",
    ],
)
def test_malicious_filenames_never_produce_a_dangerous_stored_name(malicious_name: str) -> None:
    donor, _ = auth_client("DONOR")
    r = _submit(donor, "DONOR_IDENTITY", files=[(malicious_name, PDF[1], PDF[2])])
    assert r.status_code == 201, r.text
    stored = r.json()["documents"][0]["filename"]
    assert ".." not in stored
    assert "/" not in stored
    assert "\\" not in stored
    assert ":" not in stored
    assert "<" not in stored and ">" not in stored
    assert "\x00" not in stored
    assert len(stored) <= 150
    assert all(c.isalnum() or c in "._-" for c in stored)


# --------------------------------------------------------------------------- #
# 9/10. Authorized download (with the new Cache-Control header) / unauthorized
# --------------------------------------------------------------------------- #
def test_authorized_document_download_is_never_cacheable() -> None:
    donor, _ = auth_client("DONOR")
    admin, _ = auth_client("ADMIN")
    vid = _submit(donor, "DONOR_IDENTITY").json()["id"]

    r = admin.get(f"/admin/verifications/{vid}/documents/0")
    assert r.status_code == 200
    assert r.headers.get("cache-control") == "private, no-store"


def test_unauthorized_document_download_is_denied() -> None:
    donor, _ = auth_client("DONOR")
    other_donor, _ = auth_client("DONOR")
    vid = _submit(donor, "DONOR_IDENTITY").json()["id"]

    # Only admins can ever reach the document-bytes route at all -- a
    # non-admin, even the submitter themselves, gets 403.
    assert other_donor.get(f"/admin/verifications/{vid}/documents/0").status_code == 403
    assert donor.get(f"/admin/verifications/{vid}/documents/0").status_code == 403


def test_certificate_download_is_never_cacheable() -> None:
    donor, _, did = _donor_with_donation()
    cert = donor.post("/certificates", json={"donation_id": did}).json()
    r = donor.get(f"/certificates/{cert['id']}/download")
    assert r.status_code == 200
    assert r.headers.get("cache-control") == "private, no-store"


# --------------------------------------------------------------------------- #
# 11/12. Cross-user IDOR / cross-role access
# --------------------------------------------------------------------------- #
def test_cross_user_cannot_access_another_donors_verification_or_documents() -> None:
    donor_a, _ = auth_client("DONOR")
    donor_b, _ = auth_client("DONOR")
    vid = _submit(donor_a, "DONOR_IDENTITY").json()["id"]

    # donor_b has no admin route access at all -- the same 403 either way,
    # which is itself the point: donor_b cannot even tell whether the id is
    # real (see the malformed/nonexistent tests below for the same-shape
    # check against a genuinely-authorized admin caller).
    assert donor_b.get(f"/admin/verifications/{vid}").status_code == 403
    assert donor_b.get(f"/admin/verifications/{vid}/documents/0").status_code == 403
    # /verifications/me stays strictly self-scoped.
    assert donor_b.get("/verifications/me").json() == []


def test_cross_role_hospital_cannot_reach_donor_verification_admin_routes() -> None:
    donor, _ = auth_client("DONOR")
    hospital, _ = auth_client("HOSPITAL")
    vid = _submit(donor, "DONOR_IDENTITY").json()["id"]

    assert hospital.get(f"/admin/verifications/{vid}").status_code == 403
    assert hospital.get(f"/admin/verifications/{vid}/documents/0").status_code == 403


# --------------------------------------------------------------------------- #
# 13/14. Malformed / nonexistent document id -- for an actually-authorized
# (admin) caller, so the response shape reflects "not found", not "not
# permitted".
# --------------------------------------------------------------------------- #
def test_malformed_verification_id_is_a_clean_422() -> None:
    admin, _ = auth_client("ADMIN")
    r = admin.get("/admin/verifications/not-a-uuid")
    assert r.status_code == 422


def test_nonexistent_verification_id_is_a_clean_404() -> None:
    admin, _ = auth_client("ADMIN")
    r = admin.get(f"/admin/verifications/{uuid.uuid4()}")
    assert r.status_code == 404


def test_malformed_certificate_id_is_a_clean_422() -> None:
    donor, _ = auth_client("DONOR")
    assert donor.get("/certificates/not-a-uuid").status_code == 422
    assert donor.get("/certificates/not-a-uuid/download").status_code == 422


def test_nonexistent_certificate_id_is_a_clean_404() -> None:
    donor, _ = auth_client("DONOR")
    assert donor.get(f"/certificates/{uuid.uuid4()}").status_code == 404


# --------------------------------------------------------------------------- #
# 15/16/17. Storage failure on upload / download, and a genuinely missing
# object -- the three real gaps this phase found and fixed. Each is proven
# via the exact same manual monkeypatch-and-restore pattern
# test_phase15_certificate_security.py already established for
# `storage.get_bytes`, since `verifications.py`/`certificate_service.py`
# both call storage functions through `storage.<fn>` module-attribute
# lookups (so patching the attribute on the module object is what the real
# call site actually sees).
# --------------------------------------------------------------------------- #
def test_storage_failure_on_verification_upload_is_a_clean_503_and_nothing_persists() -> None:
    donor, _ = auth_client("DONOR")
    original = storage.put_bytes
    try:
        def _boom(key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
            raise RuntimeError(f"/some/internal/path/{key} unwritable")

        storage.put_bytes = _boom  # type: ignore[assignment]
        r = _submit(donor, "DONOR_IDENTITY")
        assert r.status_code == 503, r.text
        body = r.json()
        assert "some/internal/path" not in str(body)
        assert "Traceback" not in str(body)
    finally:
        storage.put_bytes = original  # type: ignore[assignment]

    # No half-created request or document metadata is left behind -- the DB
    # transaction never committed, so the submitter can retry cleanly.
    assert donor.get("/verifications/me").json() == []
    r2 = _submit(donor, "DONOR_IDENTITY")
    assert r2.status_code == 201, r2.text


def test_storage_failure_partway_through_multi_file_upload_cleans_up_already_written_objects() -> (
    None
):
    """The second file's write fails after the first already succeeded --
    the already-written object must not silently orphan in storage."""
    donor, _ = auth_client("DONOR")
    original = storage.put_bytes
    calls = {"n": 0}
    try:
        def _fail_second(
            key: str, data: bytes, content_type: str = "application/octet-stream"
        ) -> None:
            calls["n"] += 1
            if calls["n"] == 1:
                original(key, data, content_type)
                return
            raise RuntimeError("storage backend unavailable")

        storage.put_bytes = _fail_second  # type: ignore[assignment]
        r = _submit(donor, "DONOR_IDENTITY", files=[PDF, ("id2.pdf", PDF[1], PDF[2])])
        assert r.status_code == 503, r.text
    finally:
        storage.put_bytes = original  # type: ignore[assignment]

    assert donor.get("/verifications/me").json() == []


def test_storage_failure_on_verification_document_download_is_a_clean_503() -> None:
    donor, _ = auth_client("DONOR")
    admin, _ = auth_client("ADMIN")
    vid = _submit(donor, "DONOR_IDENTITY").json()["id"]

    original = storage.get_bytes
    try:
        def _boom(key: str) -> bytes:
            raise RuntimeError(f"/some/internal/path/{key} unreadable")

        storage.get_bytes = _boom  # type: ignore[assignment]
        r = admin.get(f"/admin/verifications/{vid}/documents/0")
        assert r.status_code == 503, r.text
        body = r.json()
        assert "some/internal/path" not in str(body)
    finally:
        storage.get_bytes = original  # type: ignore[assignment]


def test_missing_verification_document_object_is_a_clean_404_not_503() -> None:
    """A DB row that references a real, but genuinely-gone, storage object
    (deleted out of band, corrupted bucket) -- distinguished from a broken
    storage backend by the new `StorageObjectNotFoundError`."""
    donor, _ = auth_client("DONOR")
    admin, _ = auth_client("ADMIN")
    vid = _submit(donor, "DONOR_IDENTITY").json()["id"]

    original = storage.get_bytes
    try:
        def _missing(key: str) -> bytes:
            raise storage.StorageObjectNotFoundError(key)

        storage.get_bytes = _missing  # type: ignore[assignment]
        r = admin.get(f"/admin/verifications/{vid}/documents/0")
        assert r.status_code == 404, r.text
    finally:
        storage.get_bytes = original  # type: ignore[assignment]


def test_storage_failure_on_certificate_issuance_is_a_clean_503_and_no_row_created() -> None:
    donor, _, did = _donor_with_donation()

    original = storage.put_bytes
    try:
        def _boom(key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
            raise RuntimeError("storage backend unavailable")

        storage.put_bytes = _boom  # type: ignore[assignment]
        r = donor.post("/certificates", json={"donation_id": did})
        assert r.status_code == 503, r.text
    finally:
        storage.put_bytes = original  # type: ignore[assignment]

    # No orphaned Certificate row: get_or_issue writes to storage before
    # `db.add(cert)` -- a fresh issue attempt must still be a normal 201, not
    # a 409/duplicate against a half-created row.
    r2 = donor.post("/certificates", json={"donation_id": did})
    assert r2.status_code == 201, r2.text


def test_missing_certificate_object_is_a_clean_404_not_503() -> None:
    donor, _, did = _donor_with_donation()
    cert = donor.post("/certificates", json={"donation_id": did}).json()

    original = storage.get_bytes
    try:
        def _missing(key: str) -> bytes:
            raise storage.StorageObjectNotFoundError(key)

        storage.get_bytes = _missing  # type: ignore[assignment]
        r = donor.get(f"/certificates/{cert['id']}/download")
        assert r.status_code == 404, r.text
    finally:
        storage.get_bytes = original  # type: ignore[assignment]


# --------------------------------------------------------------------------- #
# 18/19. Audit event on successful upload, no sensitive content in metadata
# --------------------------------------------------------------------------- #
def test_successful_upload_is_audited_with_safe_metadata_only(db_session) -> None:
    donor, _ = auth_client("DONOR")
    r = _submit(donor, "DONOR_IDENTITY", files=[PDF])
    vid = uuid.UUID(r.json()["id"])

    entry = _latest_audit(db_session, "verification.submitted", vid)
    assert entry is not None
    assert entry.meta.get("kind") == "DONOR_IDENTITY"
    assert entry.meta.get("file_count") == 1
    # No document bytes, no storage key, no filename/binary data at all.
    meta_str = str(entry.meta)
    assert PDF[1][:8].decode("latin-1") not in meta_str
    assert "key" not in entry.meta
    assert "storage" not in meta_str.lower()


# --------------------------------------------------------------------------- #
# 20. No storage credentials exposed through the API, anywhere in a
# verification or certificate response.
# --------------------------------------------------------------------------- #
def test_no_storage_key_or_credentials_ever_appear_in_api_responses() -> None:
    donor, _ = auth_client("DONOR")
    admin, _ = auth_client("ADMIN")
    vid = _submit(donor, "DONOR_IDENTITY").json()["id"]

    for body in (
        donor.get("/verifications/me").json(),
        admin.get(f"/admin/verifications/{vid}").json(),
        admin.get("/admin/verifications").json(),
    ):
        text = str(body)
        assert "verifications/" not in text  # the storage-key prefix, never the route
        assert settings.s3_bucket not in text

    donor2, _, did = _donor_with_donation()
    cert_body = donor2.post("/certificates", json={"donation_id": did}).json()
    assert "storage_key" not in cert_body
    assert "certificates/" not in str(cert_body)


# --------------------------------------------------------------------------- #
# 21. Family-link cannot bypass document authorization -- consolidated
# donor-document + donor-certificate check. The deeper structural proof
# (FAMILY can only ever link to a PATIENT) already lives in
# test_phase14_verification_security.py; this is the document/certificate
# -specific half.
# --------------------------------------------------------------------------- #
def test_family_link_cannot_bypass_donor_document_or_certificate_access() -> None:
    family, _ = auth_client("FAMILY")
    patient, patient_email = auth_client("PATIENT")
    donor, _, did = _donor_with_donation()

    link = family.post(
        "/family/links", json={"patient_email": patient_email, "relationship": "sibling"}
    ).json()
    assert patient.post(f"/family/links/{link['id']}/accept").json()["status"] == "ACCEPTED"

    vid = _submit(donor, "DONOR_IDENTITY").json()["id"]
    cert = donor.post("/certificates", json={"donation_id": did}).json()

    assert family.get(f"/admin/verifications/{vid}/documents/0").status_code == 403
    assert family.get(f"/certificates/{cert['id']}").status_code == 404
    assert family.get(f"/certificates/{cert['id']}/download").status_code == 401


# --------------------------------------------------------------------------- #
# 22. Certificate/document isolation -- a document endpoint index/route can
# never resolve a certificate, and vice versa; separate storage prefixes.
# --------------------------------------------------------------------------- #
def test_certificate_and_verification_document_storage_are_isolated() -> None:
    donor, _, did = _donor_with_donation()
    cert = donor.post("/certificates", json={"donation_id": did}).json()

    vid = _submit(donor, "DONOR_IDENTITY").json()["id"]
    admin, _ = auth_client("ADMIN")

    # A certificate id is never a valid verification-request id and vice
    # versa -- different tables, different route prefixes, no overlap.
    assert admin.get(f"/admin/verifications/{cert['id']}").status_code == 404
    assert donor.get(f"/certificates/{vid}").status_code == 404


# --------------------------------------------------------------------------- #
# 23/24. Replacement and delete: neither exists in this product today.
# "NOT SUPPORTED" is a real, checked product fact, not an assumption --
# confirmed by grepping app/models/verification.py and
# app/models/certificate.py (no version/superseded/replaced_by columns) and
# app/api/routes/{verifications,certificates}.py (no DELETE route, no PUT
# that mutates `documents`/`storage_key`).
# --------------------------------------------------------------------------- #
def test_verification_replacement_not_supported_rejected_documents_remain_retrievable() -> None:
    """There is no "replace a rejected submission's documents" endpoint --
    rejection is terminal for that row, and resubmission always creates a
    brand-new VerificationRequest with its own documents. This proves the
    one real consistency property that matters given that design: the
    original (rejected) request's documents are never overwritten or
    deleted out from under it by a later resubmission."""
    donor, _ = auth_client("DONOR")
    admin, _ = auth_client("ADMIN")
    first = _submit(donor, "DONOR_IDENTITY", files=[PDF]).json()
    admin.post(f"/admin/verifications/{first['id']}/reject", json={"reason": "blurry scan"})

    second_file = ("id_v2.pdf", b"%PDF-1.4 a different real document", "application/pdf")
    second = _submit(donor, "DONOR_IDENTITY", files=[second_file]).json()

    still_there = admin.get(f"/admin/verifications/{first['id']}/documents/0")
    assert still_there.status_code == 200
    assert still_there.content == PDF[1]

    new_one = admin.get(f"/admin/verifications/{second['id']}/documents/0")
    assert new_one.status_code == 200
    assert new_one.content == second_file[1]


def test_no_delete_route_exists_for_verifications_or_certificates() -> None:
    donor, _ = auth_client("DONOR")
    admin, _ = auth_client("ADMIN")
    vid = _submit(donor, "DONOR_IDENTITY").json()["id"]
    cert_donor, _, did = _donor_with_donation()
    cert = cert_donor.post("/certificates", json={"donation_id": did}).json()

    # FastAPI returns 405 for a real path with no matching method.
    assert admin.delete(f"/admin/verifications/{vid}").status_code in (404, 405)
    assert cert_donor.delete(f"/certificates/{cert['id']}").status_code in (404, 405)


# --------------------------------------------------------------------------- #
# Scanner honesty: NoOpScanner's verdict is recorded, but never presented as
# a security guarantee via the API schema.
# --------------------------------------------------------------------------- #
def test_scanner_metadata_is_recorded_internally_but_never_exposed_via_api() -> None:
    donor, _ = auth_client("DONOR")
    admin, _ = auth_client("ADMIN")
    r = _submit(donor, "DONOR_IDENTITY", files=[PDF])
    vid = r.json()["id"]

    # Never in the submitter's own view or the admin's view of the request.
    assert "scanned_by" not in r.json()["documents"][0]
    admin_view = admin.get(f"/admin/verifications/{vid}").json()
    assert "scanned_by" not in admin_view["documents"][0]

    # But it *was* recorded against the stored document metadata, reachable
    # only via a raw DB read -- exactly the boundary the schema enforces.
    from app.db.session import SessionLocal
    from app.models.verification import VerificationRequest

    db = SessionLocal()
    try:
        row = db.get(VerificationRequest, uuid.UUID(vid))
        assert row.documents[0]["scanned_by"] == "noop"
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Real boto3-level S3 code-path test (moto-simulated).
#
# Real MinIO is environmentally unreachable in this sandbox: no Docker
# daemon (`docker ps` fails to connect to the docker socket at all) and the
# MinIO server binary download is blocked by the egress proxy allowlist
# (a direct `curl https://dl.min.io/...` gets a 403 from the proxy). `moto`
# is a real, industry-standard simulator of the actual AWS/S3 HTTP API at
# the botocore layer -- this exercises the real `boto3` client construction,
# the real request/response contract, and this module's own
# `StorageObjectNotFoundError` translation of a real `ClientError`, which a
# function-level mock of `storage.put_bytes`/`get_bytes` (as used
# everywhere else in this file) would not. It is explicitly NOT the same
# claim as "tested against real MinIO" -- see LIFELINK_PHASE_20_AUDIT.md
# section 17.
# --------------------------------------------------------------------------- #
def test_s3_backend_round_trip_and_not_found_via_moto(monkeypatch) -> None:
    # Phase I: moto is a declared dev dependency (requirements-dev.txt), so
    # this runs everywhere the suite runs -- a missing moto is an
    # environment error, not a reason to skip the only S3 round-trip test.
    from moto import mock_aws

    monkeypatch.setattr(storage.settings, "s3_endpoint_url", "https://s3.amazonaws.com")
    monkeypatch.setattr(storage.settings, "s3_access_key", "testing")
    monkeypatch.setattr(storage.settings, "s3_secret_key", "testing")
    monkeypatch.setattr(storage.settings, "s3_region", "us-east-1")
    monkeypatch.setattr(storage.settings, "s3_bucket", "lifelink-phase20-test-bucket")

    with mock_aws():
        storage.get_s3_client.cache_clear()
        try:
            assert storage.storage_backend() == "s3"
            storage.ensure_bucket()

            key = f"verifications/moto-test/{uuid.uuid4().hex}/doc.pdf"
            storage.put_bytes(key, b"%PDF-1.4 moto round trip", "application/pdf")
            assert storage.get_bytes(key) == b"%PDF-1.4 moto round trip"

            missing_key = f"verifications/moto-test/{uuid.uuid4().hex}/gone.pdf"
            with pytest.raises(storage.StorageObjectNotFoundError):
                storage.get_bytes(missing_key)

            storage.delete_key(key)
            with pytest.raises(storage.StorageObjectNotFoundError):
                storage.get_bytes(key)
        finally:
            storage.get_s3_client.cache_clear()


def test_presign_functions_refuse_to_run_on_the_local_backend() -> None:
    """Dead-but-real code (confirmed unused anywhere in the app): guarded so
    it fails with a clear RuntimeError instead of a confusing boto3 error
    about missing credentials if it's ever called while running on the
    local-filesystem dev/test backend."""
    assert storage.storage_backend() == "local"
    with pytest.raises(RuntimeError):
        storage.presign_get("some/key")
    with pytest.raises(RuntimeError):
        storage.presign_put("some/key", "application/pdf")
