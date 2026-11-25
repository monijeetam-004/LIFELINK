"""Donor-identity / hospital-facility verification workflow.

A donor or hospital submits documents (`POST /verifications`); an admin reviews
them and, on approval, is the ONLY code path that ever sets
``DonorProfile.identity_verified`` or ``HospitalProfile.verified`` -- see
``_apply_approval`` below. Files are stored via ``app.core.storage`` (S3/MinIO
in prod, local disk in dev/tests); the DB row only ever holds the storage key
and metadata, never the bytes.

Both the donor/hospital-facing ``/verifications*`` routes and the admin-facing
``/admin/verifications*`` routes live in this one router -- the admin routes
just spell out the ``/admin/verifications`` prefix explicitly per-route.
"""
from __future__ import annotations

import hashlib
import re
import uuid

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, require_roles, verify_origin
from app.core import storage
from app.core.config import settings
from app.core.ratelimit import enforce_rate_limit
from app.core.storage import StorageObjectNotFoundError
from app.models.enums import VerificationKind, VerificationStatus
from app.models.user import User, UserRole
from app.models.verification import VerificationRequest
from app.schemas.verification import (
    VerificationApproveIn,
    VerificationRejectIn,
    VerificationRequestOut,
)
from app.services import audit_service, document_quarantine, notification_service
from app.services.document_scanner import ScanVerdict
from app.services.domain_service import get_or_create_donor_profile, get_or_create_hospital_profile

# Phase 20: private documents must never be cached by an intermediary proxy
# or the browser's disk cache -- these are sensitive identity/facility
# documents, not public assets. Applied to every response that streams
# document bytes back to a client.
_NO_STORE = "private, no-store"

router = APIRouter(tags=["verifications"])

_admin = require_roles(UserRole.ADMIN)

# Which role is allowed to submit which verification kind.
_ROLE_FOR_KIND = {
    VerificationKind.DONOR_IDENTITY: UserRole.DONOR,
    VerificationKind.HOSPITAL_FACILITY: UserRole.HOSPITAL,
}

_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND, detail="Verification request not found"
)


def _safe_filename(name: str | None) -> str:
    """Strip path separators / odd characters; never trust the client's filename.

    Also closes null-byte and path-traversal filenames (``../../etc/passwd``,
    ``report.pdf\x00.exe``): the split on ``/`` (after normalising ``\\``)
    drops any directory component, and the regex keeps only
    ``[A-Za-z0-9._-]`` -- a null byte or ``..`` segment never survives it.
    The result is never used as a filesystem path (see ``submit_verification``
    below, which stores under an app-generated ``uuid4().hex``-prefixed key),
    only as display metadata and a ``Content-Disposition`` filename.
    """
    base = (name or "upload").replace("\\", "/").split("/")[-1]
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base).strip("._ ")
    return (base or "upload")[:150]


# Magic-byte signatures for the content types this endpoint accepts. The
# client's declared ``UploadFile.content_type`` is attacker-controlled (it's
# just a multipart header) -- Phase 14 hardening: never trust it alone.
# Sniffing the real bytes and requiring them to agree with an *allowed* type
# closes the "upload an .exe/.html payload declared as application/pdf"
# class of attack (spec's "MIME/content mismatch" test case). The sniffed
# type, not the client-declared one, is what gets stored and later served
# back as the ``Content-Type`` on admin document download.
_MAGIC_SNIFFERS: tuple[tuple[str, tuple[bytes, ...]], ...] = (
    ("application/pdf", (b"%PDF-",)),
    ("image/png", (b"\x89PNG\r\n\x1a\n",)),
    ("image/jpeg", (b"\xff\xd8\xff",)),
)


async def _read_bounded(f: UploadFile, max_bytes: int) -> bytes:
    """Phase 17: read in fixed-size chunks and reject as soon as the running
    total exceeds ``max_bytes``, instead of ``await f.read()``'s previous
    unbounded single read.

    Before this fix, an oversized upload was still fully read into one
    Python ``bytes`` object (however large the client sent, limited only by
    Starlette's own spooled-temp-file behavior and available disk/RAM) even
    though the very next line was always going to reject it for being too
    big -- a real, if narrow, memory/disk resource-exhaustion exposure
    (Phase 17 Part M: "every externally controllable quantity must have a
    sensible bound... never use user-controlled values directly for...
    file size... unless validated and bounded"). This reads at most
    ``max_bytes + 1`` chunk-aligned bytes before the 422 is raised, so a
    hostile multi-gigabyte upload can never be fully buffered server-side
    just to be told "too large"."""
    chunk_size = 65536
    total = 0
    parts: list[bytes] = []
    while True:
        chunk = await f.read(chunk_size)
        if not chunk:
            break
        parts.append(chunk)
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=422,
                detail=f"File exceeds the {max_bytes} byte limit",
            )
    return b"".join(parts)


def _sniff_content_type(data: bytes) -> str | None:
    """Return the real content type implied by the file's own leading bytes,
    or ``None`` if it doesn't match any signature this endpoint recognizes."""
    for content_type, signatures in _MAGIC_SNIFFERS:
        if any(data.startswith(sig) for sig in signatures):
            return content_type
    return None


# --------------------------------------------------------------------------- #
# Submitter-facing
# --------------------------------------------------------------------------- #


@router.post(
    "/verifications", response_model=VerificationRequestOut, status_code=status.HTTP_201_CREATED
)
async def submit_verification(
    kind: VerificationKind = Form(...),
    note: str | None = Form(default=None),
    files: list[UploadFile] = File(...),
    _: None = Depends(verify_origin),
    user: User = Depends(require_roles(UserRole.DONOR, UserRole.HOSPITAL)),
    db: Session = Depends(get_db),
) -> VerificationRequest:
    required_role = _ROLE_FOR_KIND[kind]
    if user.role != required_role:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Only a {required_role.value.lower()} account can submit {kind.value}",
        )

    enforce_rate_limit(
        "verification_submit", str(user.id), *settings.rl_verification_submit_account
    )

    real_files = [f for f in files if f.filename]
    if not real_files:
        raise HTTPException(
            status_code=422, detail="At least one file is required"
        )
    if len(real_files) > settings.verification_max_files:
        raise HTTPException(
            status_code=422,
            detail=f"At most {settings.verification_max_files} files allowed",
        )

    existing = db.scalar(
        select(VerificationRequest).where(
            VerificationRequest.user_id == user.id,
            VerificationRequest.kind == kind,
            VerificationRequest.status == VerificationStatus.PENDING,
        )
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A pending verification request of this kind is already in review",
        )

    allowed_types = settings.verification_allowed_content_type_list
    prepared: list[tuple[str, bytes, str, str]] = []
    for f in real_files:
        if f.content_type not in allowed_types:
            raise HTTPException(
                status_code=422,
                detail=f"Unsupported file type: {f.content_type}",
            )
        data = await _read_bounded(f, settings.verification_max_file_bytes)
        if not data:
            raise HTTPException(
                status_code=422, detail="Empty file"
            )
        # Phase 14: the declared content_type above is just a client-supplied
        # multipart header -- verify the file's own bytes actually match it
        # before trusting either. Two distinct failure modes, both rejected:
        # (1) the bytes don't sniff to any type this endpoint recognizes at
        # all (an executable or HTML payload wearing a ".pdf" content type),
        # and (2) the bytes genuinely are a different *allowed* type than
        # what was declared (a real PNG declared as application/pdf) -- the
        # spec's "MIME/content mismatch" case is exactly this second one, so
        # declared and sniffed must agree, not merely both be individually
        # allowed. The already-validated `f.content_type` is what gets
        # persisted, since it's now confirmed to match reality.
        sniffed = _sniff_content_type(data)
        if sniffed is None or sniffed != f.content_type:
            raise HTTPException(
                status_code=422,
                detail="File content does not match the declared file type",
            )
        # Hardening: real malware scanning (app/services/document_scanner.py,
        # app/services/document_quarantine.py). INFECTED refuses the whole
        # upload (nothing is stored; the attempt is audited with the file's
        # SHA-256 and the signature name). A scanner ERROR is NOT treated as
        # clean: the file is accepted into quarantine (SCAN_FAILED), cannot
        # be downloaded or approved, and is rescanned later.
        scan = document_quarantine.scan_bytes(data)
        if scan.verdict == ScanVerdict.INFECTED:
            audit_service.record(
                db, "verification.upload_rejected_infected", actor_user_id=user.id,
                target_type="user", target_id=user.id,
                meta={"sha256": hashlib.sha256(data).hexdigest(), "signature": scan.signature,
                      "scanner": scan.scanner, "content_type": f.content_type},
            )
            db.commit()
            raise HTTPException(
                status_code=422,
                detail="File failed a security scan and cannot be accepted",
            )
        prepared.append((_safe_filename(f.filename), data, f.content_type, scan))

    # Phase 31: the `existing is not None` pre-flight check above is a fast,
    # friendly rejection for the common case, but it is not by itself
    # race-safe -- two concurrent submissions could both read no PENDING
    # row and both reach here. `uq_verification_one_pending_per_kind` (a
    # partial unique index, see app.models.verification) is the real
    # guarantee; a SAVEPOINT lets the loser of that race get the same
    # honest 409 instead of an unhandled IntegrityError, mirroring the
    # identical pattern already used for concurrent first-response inserts
    # in blood_requests.respond_to_request / emergencies.respond_to_emergency.
    # Phase E: versioning. The previous submission of this same (user,
    # kind) -- whatever its decision -- is what this one supersedes; the
    # new row gets the next ordinal. Read here, enforced by
    # `uq_verification_version` at insert time (see the SAVEPOINT below):
    # a concurrent submission that computed the same version loses with the
    # same honest 409 as the PENDING-index race, never a duplicate version.
    previous = db.scalar(
        select(VerificationRequest)
        .where(VerificationRequest.user_id == user.id, VerificationRequest.kind == kind)
        .order_by(VerificationRequest.version.desc(), VerificationRequest.created_at.desc())
        .limit(1)
    )
    savepoint = db.begin_nested()
    try:
        req = VerificationRequest(
            user_id=user.id, kind=kind, status=VerificationStatus.PENDING, documents=[], note=note,
            version=(previous.version + 1) if previous is not None else 1,
            supersedes_request_id=previous.id if previous is not None else None,
        )
        db.add(req)
        db.flush()
        savepoint.commit()
    except IntegrityError:
        savepoint.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A pending verification request of this kind is already in review",
        ) from None

    # Phase 20: every file was already fully read and validated (content
    # type, size, magic bytes, scan) into `prepared` above, before this
    # loop does any storage I/O -- so a failure here is only ever a genuine
    # storage-backend problem, never a bad upload. If a later file's write
    # fails after earlier ones in this same request already succeeded,
    # best-effort delete those now: without this, the DB transaction still
    # rolls back (the request never commits `req`/`documents`), but the
    # already-written objects would otherwise silently orphan in storage,
    # attached to nothing.
    documents: list[dict] = []
    written_keys: list[str] = []
    try:
        for filename, data, content_type, scan in prepared:
            # Clean files go to `verifications/...`; files whose scan errored
            # go to `quarantine/verifications/...` (never served).
            key = document_quarantine.storage_key(user.id, req.id, filename, scan)
            storage.put_bytes(key, data, content_type)
            written_keys.append(key)
            # Phase E: `sha256` of the exact bytes written is re-verified on
            # every admin download and bound into the decision audit record.
            documents.append(
                document_quarantine.document_metadata(key, filename, content_type, data, scan)
            )
    except Exception as exc:
        for key in written_keys:
            storage.delete_key(key)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Document storage is temporarily unavailable. Please try again.",
        ) from exc
    req.documents = documents
    if not document_quarantine.all_clean(req):
        document_quarantine.schedule_rescan()

    audit_service.record(
        db,
        "verification.submitted",
        actor_user_id=user.id,
        target_type="verification_request",
        target_id=req.id,
        meta={
            "kind": kind.value, "file_count": len(documents),
            "version": req.version,
            "supersedes_request_id": (
                str(req.supersedes_request_id) if req.supersedes_request_id else None
            ),
            "document_sha256": [d["sha256"] for d in documents],
        },
    )
    # Phase 18: this is a real, previously-missing event -- grepping every
    # notification_service call site across the backend before this phase
    # found zero notifications ever sent to an admin for anything, so a new
    # submission sat invisible until an admin happened to open the console.
    # Every admin gets an in-app notification (never an email/SMS echo: this
    # is routine queue volume, not an emergency, and admins already live in
    # the console); the body stays to what the admin already sees on the
    # verification list itself (submitter's name, kind) -- no document
    # contents, no storage keys, nothing beyond that.
    for (admin_user_id,) in db.execute(
        select(User.id).where(User.role == UserRole.ADMIN, User.is_active.is_(True))
    ).all():
        notification_service.notify(
            db, admin_user_id, type="verification.submitted",
            title=f"New {kind.value.lower()} verification submitted",
            body=f"{user.full_name} submitted {len(documents)} document(s) for review.",
            data={"verification_id": str(req.id), "kind": kind.value},
        )
    db.commit()
    db.refresh(req)
    return req


@router.get("/verifications/me", response_model=list[VerificationRequestOut])
def list_my_verifications(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[VerificationRequest]:
    return list(
        db.scalars(
            select(VerificationRequest)
            .where(VerificationRequest.user_id == user.id)
            .order_by(VerificationRequest.created_at.desc())
        ).all()
    )


# --------------------------------------------------------------------------- #
# Admin-facing
# --------------------------------------------------------------------------- #


@router.get("/admin/verifications", response_model=list[VerificationRequestOut])
def admin_list_verifications(
    kind: VerificationKind | None = None,
    status_filter: VerificationStatus | None = Query(default=None, alias="status"),
    query: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(_admin),
    db: Session = Depends(get_db),
) -> list[VerificationRequest]:
    stmt = select(VerificationRequest)
    if kind is not None:
        stmt = stmt.where(VerificationRequest.kind == kind)
    if status_filter is not None:
        stmt = stmt.where(VerificationRequest.status == status_filter)
    if query:
        # Phase 19: same ilike-search convention as /admin/users and
        # /admin/certificates -- an admin triaging the queue typically
        # knows the submitter's name or email, not the request's UUID.
        like = f"%{query.strip()}%"
        stmt = stmt.join(User, User.id == VerificationRequest.user_id).where(
            or_(User.full_name.ilike(like), User.email.ilike(like))
        )
    stmt = stmt.order_by(VerificationRequest.created_at.desc()).limit(limit)
    return list(db.scalars(stmt).all())


def _get_or_404(db: Session, request_id: uuid.UUID) -> VerificationRequest:
    req = db.get(VerificationRequest, request_id)
    if req is None:
        raise _NOT_FOUND
    return req


def _get_for_update_or_404(db: Session, request_id: uuid.UUID) -> VerificationRequest:
    """Same lookup, but with a `SELECT ... FOR UPDATE` row lock held for the
    rest of this transaction -- used only by approve/reject (the two routes
    that can change `status`), never by the read-only GET routes above.

    Before Phase 14 both routes fetched with a bare `db.get()`: two admins
    approving/rejecting the same PENDING request at (near-)the same instant
    could both read PENDING, both pass the `status != PENDING` check, and
    both "succeed" -- the same class of race Phase 10/12/13 already closed
    for BloodRequest/Emergency/FamilyLink. Locking here closes it here too:
    the second caller blocks until the first commits, then re-reads the
    now-current (no longer PENDING) status and gets a real 409."""
    req = db.get(VerificationRequest, request_id, with_for_update=True)
    if req is None:
        raise _NOT_FOUND
    return req


@router.get("/admin/verifications/{request_id}", response_model=VerificationRequestOut)
def admin_get_verification(
    request_id: uuid.UUID, user: User = Depends(_admin), db: Session = Depends(get_db)
) -> VerificationRequest:
    return _get_or_404(db, request_id)


@router.get("/admin/verifications/{request_id}/documents/{index}")
def admin_get_verification_document(
    request_id: uuid.UUID,
    index: int,
    user: User = Depends(_admin),
    db: Session = Depends(get_db),
) -> Response:
    req = _get_or_404(db, request_id)
    if index < 0 or index >= len(req.documents):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    doc = req.documents[index]
    # Hardening: only a document that passed malware scanning is ever served.
    document_quarantine.assert_downloadable(doc)
    # Phase 20: previously an unhandled `storage.get_bytes` exception --
    # including a genuinely missing object -- propagated as a bare,
    # unhandled 500. Distinguished here exactly like
    # certificates.py::download_certificate: "the object was never there
    # (or is gone)" is an honest 404, "the storage backend itself is
    # erroring" is an honest 503 -- neither leaks a backend-internal detail
    # (an S3 error body, a local filesystem path) into the response.
    try:
        data = storage.get_bytes(doc["key"])
    except StorageObjectNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Document is temporarily unavailable. Please try again shortly.",
        ) from exc
    # Phase E: integrity re-check. If a checksum was recorded at submission
    # and the bytes now in storage don't match it, the object was altered
    # (or corrupted) after the fact -- refuse to serve it as the submitted
    # document, and leave an audit record. Pre-Phase-E documents have no
    # recorded checksum; they are served, but labelled as unverifiable
    # rather than silently presented as checked.
    expected = doc.get("sha256")
    if expected is not None:
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected:
            audit_service.record(
                db, "verification.document_integrity_failed", actor_user_id=user.id,
                target_type="verification_request", target_id=req.id,
                meta={"index": index, "expected_sha256": expected, "actual_sha256": actual},
            )
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Document integrity check failed: the stored file no longer "
                    "matches the checksum recorded at submission"
                ),
            )
        integrity = "sha256-verified"
    else:
        integrity = "unverified-legacy"
    # Gap elimination #26: reading someone's identity document is itself an
    # audited event (who looked at what, when).
    audit_service.record(
        db, "admin.verification_document_viewed", actor_user_id=user.id,
        target_type="verification_request", target_id=req.id,
        meta={"index": index, "integrity": integrity},
    )
    db.commit()
    return Response(
        content=data,
        media_type=doc["content_type"],
        headers={
            "Content-Disposition": f'inline; filename="{doc["filename"]}"',
            "Cache-Control": _NO_STORE,
            "X-Document-Integrity": integrity,
        },
    )


@router.post(
    "/admin/verifications/{request_id}/documents/{index}/rescan",
    response_model=VerificationRequestOut,
)
def admin_rescan_verification_document(
    request_id: uuid.UUID,
    index: int,
    _: None = Depends(verify_origin),
    user: User = Depends(_admin),
    db: Session = Depends(get_db),
) -> VerificationRequest:
    """Retry the malware scan of a quarantined document (SCAN_FAILED or a
    legacy PENDING_SCAN document). CLEAN and INFECTED are final."""
    req = _get_for_update_or_404(db, request_id)
    if index < 0 or index >= len(req.documents):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    try:
        document_quarantine.rescan_document(db, req, index, actor_user_id=user.id)
    except StorageObjectNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Document storage is temporarily unavailable. Please try again.",
        ) from exc
    db.commit()
    db.refresh(req)
    return req


def _apply_approval(db: Session, req: VerificationRequest, subject: User) -> None:
    """The only code path that ever flips identity_verified / verified to True."""
    if req.kind == VerificationKind.DONOR_IDENTITY:
        profile = get_or_create_donor_profile(db, subject)
        profile.identity_verified = True
    else:
        profile = get_or_create_hospital_profile(db, subject)
        profile.verified = True


@router.post("/admin/verifications/{request_id}/approve", response_model=VerificationRequestOut)
def admin_approve_verification(
    request_id: uuid.UUID,
    payload: VerificationApproveIn,
    _: None = Depends(verify_origin),
    user: User = Depends(_admin),
    db: Session = Depends(get_db),
) -> VerificationRequest:
    req = _get_for_update_or_404(db, request_id)
    if req.status != VerificationStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Request has already been decided"
        )
    subject = db.get(User, req.user_id)
    if subject is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Submitter no longer exists"
        )
    # Hardening: an approval is a statement about the documents; it is
    # refused while any of them has not passed malware scanning.
    if not document_quarantine.all_clean(req):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Every document must pass malware scanning before approval",
        )

    req.status = VerificationStatus.APPROVED
    req.reviewer_user_id = user.id
    req.review_note = payload.note
    _apply_approval(db, req, subject)

    audit_service.record(
        db,
        "admin.verification_approved",
        actor_user_id=user.id,
        target_type="verification_request",
        target_id=req.id,
        # Phase E: the decision is bound to the exact document version and
        # bytes reviewed -- a later resubmission is a new row with its own
        # version, never a silent change to what this approval covered.
        meta={
            "kind": req.kind.value, "version": req.version,
            "document_sha256": [d.get("sha256") for d in req.documents],
        },
    )
    notification_service.notify(
        db,
        req.user_id,
        type="verification.approved",
        title="Your verification was approved",
        body=payload.note or "Your submitted documents have been approved.",
        data={"verification_id": str(req.id), "kind": req.kind.value},
    )
    db.commit()
    db.refresh(req)
    return req


@router.post("/admin/verifications/{request_id}/reject", response_model=VerificationRequestOut)
def admin_reject_verification(
    request_id: uuid.UUID,
    payload: VerificationRejectIn,
    _: None = Depends(verify_origin),
    user: User = Depends(_admin),
    db: Session = Depends(get_db),
) -> VerificationRequest:
    req = _get_for_update_or_404(db, request_id)
    if req.status != VerificationStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Request has already been decided"
        )

    req.status = VerificationStatus.REJECTED
    req.reviewer_user_id = user.id
    req.review_note = payload.reason

    audit_service.record(
        db,
        "admin.verification_rejected",
        actor_user_id=user.id,
        target_type="verification_request",
        target_id=req.id,
        meta={
            "kind": req.kind.value, "reason": payload.reason, "version": req.version,
            "document_sha256": [d.get("sha256") for d in req.documents],
        },
    )
    notification_service.notify(
        db,
        req.user_id,
        type="verification.rejected",
        title="Your verification was rejected",
        body=payload.reason,
        data={"verification_id": str(req.id), "kind": req.kind.value},
    )
    db.commit()
    db.refresh(req)
    return req
