from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, get_optional_user, require_roles, verify_origin
from app.core.storage import StorageObjectNotFoundError
from app.models.certificate import Certificate
from app.models.donation import Donation
from app.models.enums import DonationVerificationStatus
from app.models.user import User, UserRole
from app.schemas.certificate import (
    CertificateIssueIn,
    CertificateOut,
    CertificateVerifyOut,
)
from app.services import audit_service, certificate_service
from app.services.certificate_service import CertificateStorageError

router = APIRouter(prefix="/certificates", tags=["certificates"])

_NOT_FOUND = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Certificate not found")
# Phase 20: certificates are per-donor sensitive PDFs, never a publicly
# cacheable asset -- see verifications.py's identical `_NO_STORE`.
_NO_STORE = "private, no-store"


def _to_out(db: Session, cert: Certificate) -> CertificateOut:
    out = CertificateOut.model_validate(cert)
    donation = db.get(Donation, cert.donation_id)
    donor = db.get(User, cert.donor_user_id)
    out.donor_name = donor.full_name if donor else None
    if donation is not None:
        out.donated_on = donation.donated_on
        out.units = donation.units
        out.hospital_name = donation.hospital_name
    out.verify_path = certificate_service.verify_url_path(cert.verify_code)
    out.download_token = certificate_service.make_download_token(cert.id)
    out.revoked_at = cert.revoked_at
    out.revocation_reason = cert.revocation_reason
    return out


@router.get("", response_model=list[CertificateOut])
def list_my_certificates(
    user: User = Depends(require_roles(UserRole.DONOR)),
    db: Session = Depends(get_db),
) -> list[CertificateOut]:
    rows = db.scalars(
        select(Certificate)
        .where(Certificate.donor_user_id == user.id)
        .order_by(Certificate.created_at.desc())
    ).all()
    return [_to_out(db, c) for c in rows]


@router.post("", response_model=CertificateOut, status_code=status.HTTP_201_CREATED)
def issue_certificate(
    payload: CertificateIssueIn,
    _: None = Depends(verify_origin),
    user: User = Depends(require_roles(UserRole.DONOR)),
    db: Session = Depends(get_db),
) -> CertificateOut:
    # Phase 15: locked (SELECT ... FOR UPDATE), not a bare db.get(). Certificate
    # generation is a real PDF render + object-storage write + a DB insert that
    # is unique on donation_id -- without a lock, two near-simultaneous requests
    # for the *same* donation could both pass get_or_issue's "does one already
    # exist?" check before either has inserted, both render/upload a PDF, and
    # one would then fail on the unique constraint with an unhandled
    # IntegrityError instead of cleanly returning the other's certificate.
    # Locking the Donation row here serializes exactly that case: the second
    # caller blocks until the first commits, then get_or_issue's own re-check
    # finds the now-persisted row and returns it -- no duplicate insert is ever
    # attempted, and no wasted PDF/storage work happens on the losing side.
    donation = db.get(Donation, payload.donation_id, with_for_update=True)
    if donation is None or donation.donor_user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Donation not found")

    # Phase E: a self-reported donation becomes a publicly-verifiable PDF
    # artifact here -- the one concrete place in this codebase a donor's
    # own attestation was being presented as confirmed fact (see
    # app.api.routes.donation_review's module docstring for why no other
    # coupling point exists). Gate only the *first* issuance: get_or_issue
    # is otherwise idempotent (re-fetches an existing certificate for the
    # same donation), and a certificate already issued -- whether from
    # before this gate existed, or legitimately while VERIFIED -- must
    # keep resolving the same way every other idempotent fetch in this
    # codebase does, never retroactively revoked by a later status change.
    # Rejecting (not revoking) a donation afterwards is a separate,
    # explicit concern -- see donation_review.reject_donation's docstring.
    existing_cert = db.scalar(select(Certificate).where(Certificate.donation_id == donation.id))
    if (
        existing_cert is None
        and donation.verification_status != DonationVerificationStatus.VERIFIED
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This donation must be verified by the facility (or an admin) "
                "before a certificate can be issued"
            ),
        )
    try:
        cert = certificate_service.get_or_issue(db, user, donation)
    except CertificateStorageError as exc:
        # Storage failed before any DB row was created (see get_or_issue's
        # ordering) -- an honest 503, never a false "issued" response.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Certificate generation is temporarily unavailable. Please try again.",
        ) from exc
    db.commit()
    db.refresh(cert)
    return _to_out(db, cert)


@router.get("/verify/{code}", response_model=CertificateVerifyOut)
def verify_certificate(code: str, db: Session = Depends(get_db)) -> CertificateVerifyOut:
    """Public — confirms a certificate by its non-guessable code. Safe fields only."""
    cert = db.scalar(select(Certificate).where(Certificate.verify_code == code))
    if cert is None:
        raise _NOT_FOUND
    donation = db.get(Donation, cert.donation_id)
    donor = db.get(User, cert.donor_user_id)
    if donation is None or donor is None:
        raise _NOT_FOUND
    return CertificateVerifyOut(
        # Phase 31: honestly reflects revocation -- a revoked certificate's
        # code still resolves (the factual "what was issued" record is
        # never erased), but `valid` is False rather than indistinguishable
        # from a currently-trustworthy certificate.
        valid=cert.revoked_at is None,
        serial=cert.serial,
        donor_name=donor.full_name,
        donated_on=donation.donated_on,
        units=donation.units,
        hospital_name=donation.hospital_name,
        issued_at=cert.created_at,
        revoked_at=cert.revoked_at,
        revocation_reason=cert.revocation_reason,
    )


@router.get("/{certificate_id}", response_model=CertificateOut)
def get_certificate(
    certificate_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CertificateOut:
    cert = db.get(Certificate, certificate_id)
    if cert is None:
        raise _NOT_FOUND
    if cert.donor_user_id != user.id and user.role != UserRole.ADMIN:
        # A real certificate, someone else's -- a genuine cross-tenant probe,
        # not just a bad id, so it's audited (see the identical reasoning in
        # blood_requests._deny_access).
        audit_service.record(
            db,
            "certificate.access_denied",
            actor_user_id=user.id,
            target_type="certificate",
            target_id=cert.id,
            meta={"actor_role": user.role.value, "attempted_action": "view"},
        )
        db.commit()
        raise _NOT_FOUND
    # Phase 15: a real, authorized view of a specific certificate's detail is
    # a security-relevant read of donor-identifying data (donor name, donation
    # specifics) -- audited the same way the pre-existing denied-access branch
    # above already is, just for the success path instead of the failure path.
    audit_service.record(
        db,
        "certificate.viewed",
        actor_user_id=user.id,
        target_type="certificate",
        target_id=cert.id,
        meta={"actor_role": user.role.value},
    )
    db.commit()
    return _to_out(db, cert)


@router.get("/{certificate_id}/download")
def download_certificate(
    certificate_id: uuid.UUID,
    token: str | None = Query(default=None),
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
) -> Response:
    cert = db.get(Certificate, certificate_id)
    if cert is None:
        raise _NOT_FOUND

    authorized = False
    auth_via = "token"
    if token and certificate_service.check_download_token(cert.id, token):
        authorized = True
        auth_via = "token"
    elif user is not None and (cert.donor_user_id == user.id or user.role == UserRole.ADMIN):
        authorized = True
        auth_via = "owner" if cert.donor_user_id == user.id else "admin"
    if not authorized:
        if user is not None:
            # An identified, logged-in user without a valid token trying to
            # pull someone else's certificate -- a genuine cross-tenant
            # attempt worth a record, same as get_certificate above. An
            # anonymous request with a bad/missing token has no actor to
            # attribute it to, so it's just rejected, not audited.
            audit_service.record(
                db,
                "certificate.access_denied",
                actor_user_id=user.id,
                target_type="certificate",
                target_id=cert.id,
                meta={"actor_role": user.role.value, "attempted_action": "download"},
            )
            db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authorized")

    try:
        pdf = certificate_service.load_pdf(cert)
    except StorageObjectNotFoundError as exc:
        # Phase 20: distinguished from the generic case below -- a real DB
        # row whose object is genuinely gone from storage (deleted out of
        # band, corrupted bucket, etc.) is an honest 404, not a 503 that
        # implies "try again later" when retrying can never help.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Certificate file not found"
        ) from exc
    except Exception as exc:
        # Phase 15: a genuinely-authorized request whose underlying object
        # storage read failed (backend outage) must not surface as a bare
        # unhandled exception -- that risks an opaque 500 with no clear
        # cause and, depending on the storage backend, could leak
        # backend-internal details (an S3 error message, a local
        # filesystem path) into the response. Fail closed with a clean,
        # intentional error instead; nothing about the failure is exposed
        # beyond "the file isn't available right now."
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Certificate file is temporarily unavailable",
        ) from exc

    # Phase 15: audit a successful download the same way an unsuccessful one
    # already was -- "who downloaded which certificate, authenticated how" is
    # exactly the kind of security-sensitive access the spec calls out.
    # Anonymous signed-token downloads have no actor to attribute (the token
    # itself is the credential), so actor_user_id is left null rather than
    # invented.
    audit_service.record(
        db,
        "certificate.downloaded",
        actor_user_id=user.id if user is not None else None,
        target_type="certificate",
        target_id=cert.id,
        meta={"via": auth_via},
    )
    db.commit()

    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{cert.serial}.pdf"',
            "Cache-Control": _NO_STORE,
        },
    )
