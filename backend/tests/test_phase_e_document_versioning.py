"""Phase E -- verification-document versioning and integrity.

Proves: submissions are append-only versions (1, 2, 3 ... per user+kind,
each superseding the last); an earlier version's documents, checksums
and decision are never rewritten by a later one; every stored document
carries a SHA-256 of its exact bytes; an admin download re-verifies that
checksum and refuses (409 + audit) when the stored object was altered;
approve/reject audit records are bound to the version and checksums
actually reviewed; and two concurrent submissions can't share a version.
"""
from __future__ import annotations

import hashlib
import threading
import uuid

from sqlalchemy import select

from app.core import storage
from app.db.session import SessionLocal
from app.models.audit_log import AuditLog
from app.models.verification import VerificationRequest
from tests.helpers import auth_client

PDF_1 = b"%PDF-1.4 version one"
PDF_2 = b"%PDF-1.4 version two -- corrected scan"


def _submit(client, data: bytes = PDF_1, kind: str = "DONOR_IDENTITY"):
    return client.post(
        "/verifications", data={"kind": kind},
        files=[("files", ("id.pdf", data, "application/pdf"))],
    )


def _row(vid: str) -> VerificationRequest:
    db = SessionLocal()
    try:
        row = db.get(VerificationRequest, uuid.UUID(vid))
        db.expunge(row)
        return row
    finally:
        db.close()


def _audit(db_session, action: str, target_id: str) -> AuditLog | None:
    return db_session.scalars(
        select(AuditLog).where(AuditLog.action == action,
                               AuditLog.target_id == uuid.UUID(target_id))
        .order_by(AuditLog.created_at.desc())
    ).first()


def test_first_submission_is_version_1_with_checksum() -> None:
    donor, _ = auth_client("DONOR")
    r = _submit(donor)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["version"] == 1 and body["supersedes_request_id"] is None
    assert body["documents"][0]["sha256"] == hashlib.sha256(PDF_1).hexdigest()
    assert "key" not in body["documents"][0]  # storage key still never exposed


def test_resubmission_after_rejection_is_new_version_old_untouched(db_session) -> None:
    donor, _ = auth_client("DONOR")
    admin, _ = auth_client("ADMIN")
    v1 = _submit(donor).json()
    admin.post(f"/admin/verifications/{v1['id']}/reject", json={"reason": "blurry scan"})

    r = _submit(donor, PDF_2)
    assert r.status_code == 201, r.text
    v2 = r.json()
    assert v2["version"] == 2 and v2["supersedes_request_id"] == v1["id"]
    assert v2["documents"][0]["sha256"] == hashlib.sha256(PDF_2).hexdigest()

    old = _row(v1["id"])
    assert old.status.value == "REJECTED" and old.review_note == "blurry scan"
    assert old.documents[0]["sha256"] == hashlib.sha256(PDF_1).hexdigest()
    # v1's bytes are still in storage, unchanged, under their own key.
    assert storage.get_bytes(old.documents[0]["key"]) == PDF_1
    assert old.documents[0]["key"] != _row(v2["id"]).documents[0]["key"]

    sub = _audit(db_session, "verification.submitted", v2["id"])
    assert sub.meta["version"] == 2 and sub.meta["supersedes_request_id"] == v1["id"]


def test_versions_are_per_kind_and_per_user() -> None:
    d1, _ = auth_client("DONOR")
    d2, _ = auth_client("DONOR")
    assert _submit(d1).json()["version"] == 1
    assert _submit(d2).json()["version"] == 1


def test_my_history_lists_every_version() -> None:
    donor, _ = auth_client("DONOR")
    admin, _ = auth_client("ADMIN")
    v1 = _submit(donor).json()
    admin.post(f"/admin/verifications/{v1['id']}/reject", json={"reason": "expired id"})
    _submit(donor, PDF_2)
    versions = sorted(v["version"] for v in donor.get("/verifications/me").json())
    assert versions == [1, 2]


def test_admin_download_verifies_checksum() -> None:
    donor, _ = auth_client("DONOR")
    admin, _ = auth_client("ADMIN")
    vid = _submit(donor).json()["id"]
    r = admin.get(f"/admin/verifications/{vid}/documents/0")
    assert r.status_code == 200
    assert r.content == PDF_1
    assert r.headers["X-Document-Integrity"] == "sha256-verified"


def test_tampered_document_is_refused_and_audited(db_session) -> None:
    donor, _ = auth_client("DONOR")
    admin, _ = auth_client("ADMIN")
    vid = _submit(donor).json()["id"]
    key = _row(vid).documents[0]["key"]
    storage.put_bytes(key, b"%PDF-1.4 swapped after submission", "application/pdf")

    r = admin.get(f"/admin/verifications/{vid}/documents/0")
    assert r.status_code == 409
    assert "integrity" in r.json()["detail"].lower()
    entry = _audit(db_session, "verification.document_integrity_failed", vid)
    assert entry is not None
    assert entry.meta["expected_sha256"] == hashlib.sha256(PDF_1).hexdigest()


def test_legacy_document_without_checksum_is_labelled_unverified() -> None:
    donor, _ = auth_client("DONOR")
    admin, _ = auth_client("ADMIN")
    vid = _submit(donor).json()["id"]
    db = SessionLocal()
    try:
        row = db.get(VerificationRequest, uuid.UUID(vid))
        docs = [dict(d) for d in row.documents]
        docs[0].pop("sha256")
        row.documents = docs
        db.commit()
    finally:
        db.close()
    r = admin.get(f"/admin/verifications/{vid}/documents/0")
    assert r.status_code == 200
    assert r.headers["X-Document-Integrity"] == "unverified-legacy"


def test_approval_audit_binds_version_and_checksums(db_session) -> None:
    donor, _ = auth_client("DONOR")
    admin, _ = auth_client("ADMIN")
    vid = _submit(donor).json()["id"]
    assert admin.post(f"/admin/verifications/{vid}/approve", json={}).status_code == 200
    entry = _audit(db_session, "admin.verification_approved", vid)
    assert entry.meta["version"] == 1
    assert entry.meta["document_sha256"] == [hashlib.sha256(PDF_1).hexdigest()]


def test_concurrent_resubmissions_never_share_a_version() -> None:
    donor, _ = auth_client("DONOR")
    admin, _ = auth_client("ADMIN")
    v1 = _submit(donor).json()
    admin.post(f"/admin/verifications/{v1['id']}/reject", json={"reason": "redo"})

    barrier = threading.Barrier(2)
    codes: list[int] = []

    def go(payload: bytes):
        barrier.wait()
        codes.append(_submit(donor, payload).status_code)

    ts = [threading.Thread(target=go, args=(p,)) for p in (PDF_1, PDF_2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(codes) == [201, 409], codes
    versions = sorted(v["version"] for v in donor.get("/verifications/me").json())
    assert versions == [1, 2]
