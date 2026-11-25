"""Quarantine and release of uploaded verification documents.

Flow per document (stored in `VerificationRequest.documents[i]`):

  upload -> validate (type, size, magic bytes) -> scan
     CLEAN     -> stored under `verifications/...`, scan_status CLEAN
     INFECTED  -> nothing stored; the whole upload is refused (422) and an
                  audit record keeps the SHA-256 and signature name
     ERROR     -> stored under `quarantine/verifications/...`,
                  scan_status SCAN_FAILED; never downloadable, blocks
                  approval; retried by the worker (and by an admin "rescan")

  rescan of a SCAN_FAILED / PENDING_SCAN document:
     CLEAN     -> copied to `verifications/...`, quarantine copy deleted,
                  scan_status CLEAN
     INFECTED  -> quarantine copy deleted, scan_status INFECTED (permanently
                  unavailable), audited
     ERROR     -> stays SCAN_FAILED, attempt counted, retried later

Only documents with scan_status CLEAN are ever served or approvable
(`assert_downloadable`, `all_clean`). PENDING_SCAN is the state legacy
documents (uploaded before this hardening, when no scan existed) are
migrated into, so they too must pass a scan before being served.
"""

from __future__ import annotations

import enum
import hashlib
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import storage
from app.core.time import utcnow
from app.models.verification import VerificationRequest
from app.services import audit_service
from app.services.document_scanner import ScanResult, ScanVerdict, get_scanner

log = logging.getLogger("lifelink.scanner")


class ScanStatus(str, enum.Enum):
    PENDING_SCAN = "PENDING_SCAN"
    CLEAN = "CLEAN"
    INFECTED = "INFECTED"
    SCAN_FAILED = "SCAN_FAILED"


QUARANTINE_PREFIX = "quarantine/"


def status_of(doc: dict) -> ScanStatus:
    # Documents stored before this hardening carry no scan_status: they were
    # never scanned, so they are treated exactly like PENDING_SCAN.
    return ScanStatus(doc.get("scan_status", ScanStatus.PENDING_SCAN.value))


def scan_bytes(data: bytes) -> ScanResult:
    result = get_scanner().scan(data)
    if result.verdict == ScanVerdict.ERROR:
        log.warning("document scan failed: %s", result.detail)
    return result


def document_metadata(
    key: str, filename: str, content_type: str, data: bytes, result: ScanResult
) -> dict:
    """Metadata for a newly stored (CLEAN or SCAN_FAILED) document."""
    status = ScanStatus.CLEAN if result.verdict == ScanVerdict.CLEAN else ScanStatus.SCAN_FAILED
    return {
        "key": key,
        "filename": filename,
        "content_type": content_type,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "scanned_by": result.scanner,
        "scan_status": status.value,
        "scan_attempts": 1,
        "scanned_at": utcnow().isoformat(),
        "scan_detail": result.detail if status != ScanStatus.CLEAN else None,
    }


def storage_key(
    user_id: uuid.UUID, request_id: uuid.UUID, filename: str, result: ScanResult
) -> str:
    base = f"verifications/{user_id}/{request_id}/{uuid.uuid4().hex}_{filename}"
    return base if result.verdict == ScanVerdict.CLEAN else QUARANTINE_PREFIX + base


def assert_downloadable(doc: dict) -> None:
    from fastapi import HTTPException, status

    st = status_of(doc)
    if st != ScanStatus.CLEAN:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This document has not passed malware scanning (status: {st.value})",
        )


def all_clean(req: VerificationRequest) -> bool:
    return all(status_of(d) == ScanStatus.CLEAN for d in req.documents)


def rescan_document(
    db: Session, req: VerificationRequest, index: int, actor_user_id: uuid.UUID | None
) -> dict:
    """Rescan one non-CLEAN document; returns the updated metadata. The
    caller commits. Never raises for a scanner problem (that is a result)."""
    docs = [dict(d) for d in req.documents]
    doc = docs[index]
    current = status_of(doc)
    if current in (ScanStatus.CLEAN, ScanStatus.INFECTED):
        return doc  # final states
    data = storage.get_bytes(doc["key"])
    result = scan_bytes(data)
    doc["scan_attempts"] = int(doc.get("scan_attempts", 0)) + 1
    doc["scanned_at"] = utcnow().isoformat()
    doc["scanned_by"] = result.scanner
    old_key = doc["key"]
    if result.verdict == ScanVerdict.CLEAN:
        new_key = (
            old_key[len(QUARANTINE_PREFIX) :] if old_key.startswith(QUARANTINE_PREFIX) else old_key
        )
        if new_key != old_key:
            storage.put_bytes(new_key, data, doc["content_type"])
            storage.delete_key(old_key)
        doc.update({"key": new_key, "scan_status": ScanStatus.CLEAN.value, "scan_detail": None})
        action = "verification.document_released"
    elif result.verdict == ScanVerdict.INFECTED:
        storage.delete_key(old_key)
        doc.update(
            {
                "scan_status": ScanStatus.INFECTED.value,
                "scan_detail": result.signature,
                "key": old_key,
            }
        )
        action = "verification.document_infected"
    else:
        doc.update({"scan_status": ScanStatus.SCAN_FAILED.value, "scan_detail": result.detail})
        action = "verification.document_scan_failed"
    docs[index] = doc
    req.documents = docs  # reassign: JSON column change detection
    audit_service.record(
        db,
        action,
        actor_user_id=actor_user_id,
        target_type="verification_request",
        target_id=req.id,
        meta={
            "index": index,
            "sha256": doc.get("sha256"),
            "scanner": result.scanner,
            "signature": result.signature,
            "detail": result.detail,
            "attempt": doc["scan_attempts"],
        },
    )
    return doc


def rescan_pending(db: Session) -> dict[str, int]:
    """Rescan every non-final (PENDING_SCAN / SCAN_FAILED) document, on any
    request (an approved request's legacy documents are withheld too until
    scanned). One short locked transaction per request. Used by the worker
    job and `scripts/rescan_documents.py`."""
    from sqlalchemy import String, cast, or_

    counts = {s.value: 0 for s in ScanStatus}
    counts["UNAVAILABLE"] = 0
    text = cast(VerificationRequest.documents, String)
    ids = db.scalars(
        select(VerificationRequest.id).where(
            or_(
                text.contains(ScanStatus.SCAN_FAILED.value),
                text.contains(ScanStatus.PENDING_SCAN.value),
                ~text.contains("scan_status"),
            ),
            text != "[]",
        )
    ).all()
    for rid in ids:
        req = db.get(VerificationRequest, rid, with_for_update={"skip_locked": True})
        if req is None:
            db.rollback()
            continue
        for i, d in enumerate(list(req.documents)):
            if status_of(d) not in (ScanStatus.PENDING_SCAN, ScanStatus.SCAN_FAILED):
                continue
            try:
                updated = rescan_document(db, req, i, actor_user_id=None)
            except storage.StorageObjectNotFoundError:
                counts["UNAVAILABLE"] += 1
                continue
            except Exception:
                log.warning("rescan: storage unavailable for request %s", rid, exc_info=True)
                counts["UNAVAILABLE"] += 1
                continue
            counts[updated["scan_status"]] += 1
        db.commit()
    return counts


def schedule_rescan() -> None:
    """Ask the worker to retry quarantined documents later (no-op in
    RQ_SYNC/test mode, where `scripts/rescan_documents.py` or the admin
    rescan action is used instead)."""
    from app.core.config import settings

    if settings.rq_sync:
        return
    try:
        from datetime import timedelta

        from app.workers.queue import email_queue

        email_queue.enqueue_in(
            timedelta(seconds=settings.document_rescan_delay_seconds),
            "app.workers.tasks.rescan_documents",
            description="rescan_documents",
        )
    except Exception:  # pragma: no cover - queue outage; manual rescan still works
        log.warning("could not schedule document rescan", exc_info=True)
