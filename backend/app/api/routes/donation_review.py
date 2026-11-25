"""Phase E -- donation verification: whether a self-reported `Donation`
row's facts have been confirmed by anyone with real authority over them.

New top-level `/donations` resource, mirroring Phase D's `/shipments`
precedent exactly rather than cramming a cross-role (HOSPITAL-or-ADMIN
acting on a DONOR's own row) concern into `donors.py` (which is donor-
self-service only, every route there gated on `require_roles(UserRole.DONOR)`)
or into `admin.py` (which would make it admin-only, when a hospital tied to
the donation should also be able to review it).

See `app.models.enums.DonationVerificationStatus` for the full state
policy and `docs/LIFELINK_VERIFICATION_LIFECYCLE.md` for the complete
picture. In one sentence: this module answers "did this donation really
happen", never a clinical question -- there is no lab result, no
clinical-staff role, and no BloodUnit creation pathway coupled to a
Donation row anywhere in this codebase (verified by reading
`app.api.routes.donors.log_donation` and every inventory-mutating route in
`app.api.routes.hospitals` in full: ADD is a wholly separate hospital
action with its own authorization, never triggered by logging a
donation) -- so there is no "unverified donation creates inventory" risk
to close here. The one real, concrete place a self-reported fact was
being turned into an artifact presented as authoritative is certificate
issuance (`app.api.routes.certificates.issue_certificate`), which this
phase gates on `verification_status == VERIFIED` (see that module).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_roles, verify_origin
from app.core.time import utcnow
from app.models.donation import Donation
from app.models.enums import DonationVerificationStatus
from app.models.hospital import HospitalProfile
from app.models.user import User, UserRole
from app.schemas.donation import DonationOut, DonationReviewIn
from app.services import audit_service, notification_service
from app.services.domain_service import get_or_create_hospital_profile

router = APIRouter(prefix="/donations", tags=["donations"])

_actor = require_roles(UserRole.HOSPITAL, UserRole.ADMIN)


def _actor_facility(db: Session, user: User) -> HospitalProfile | None:
    """`None` for an ADMIN (may review any donation); the caller's own
    `HospitalProfile` for a HOSPITAL user -- same convention as
    `app.api.routes.shipments._actor_facility`."""
    if user.role == UserRole.HOSPITAL:
        return get_or_create_hospital_profile(db, user)
    return None


def _assert_can_review(donation: Donation, hp: HospitalProfile | None) -> None:
    """A donor can never review their own donation -- structurally true
    here regardless (`_actor` only accepts HOSPITAL/ADMIN, never DONOR),
    but also explicit: a hospital may review only a donation tied to
    ITS OWN `hospital_id`; a donation with no `hospital_id` at all
    (every historical row, and any unstructured/off-platform donation)
    can only be reviewed by an admin -- this system has no way to
    honestly attribute authority over free text to one specific facility."""
    if hp is None:
        return  # admin
    if donation.hospital_id is None or donation.hospital_id != hp.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Only the facility this donation is linked to (or an admin) "
                "can review it"
            ),
        )


def _get_for_review(db: Session, donation_id: uuid.UUID) -> Donation:
    donation = db.get(Donation, donation_id, with_for_update=True)
    if donation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Donation not found")
    return donation


@router.get("", response_model=list[DonationOut])
def list_donations_for_review(
    verification_status: DonationVerificationStatus | None = Query(
        default=DonationVerificationStatus.DONOR_REPORTED
    ),
    unlinked_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(_actor),
    db: Session = Depends(get_db),
) -> list[Donation]:
    """The review queue. A hospital sees ONLY donations linked to its own
    facility (`hospital_id`) -- never another facility's, never unlinked
    ones (those are admin-only, see `_assert_can_review`). An admin sees
    everything, optionally just the unlinked rows only an admin can act on.
    Without this list a reviewer could only act on a donation whose UUID
    it already knew, i.e. the review workflow was unreachable in practice."""
    hp = _actor_facility(db, user)
    stmt = select(Donation)
    if verification_status is not None:
        stmt = stmt.where(Donation.verification_status == verification_status)
    if hp is not None:
        stmt = stmt.where(Donation.hospital_id == hp.id)
        db.commit()  # get_or_create_hospital_profile may have inserted
    elif unlinked_only:
        stmt = stmt.where(Donation.hospital_id.is_(None))
    stmt = stmt.order_by(Donation.donated_on.desc(), Donation.created_at.desc()).limit(limit)
    return list(db.scalars(stmt).all())


@router.get("/{donation_id}", response_model=DonationOut)
def get_donation_for_review(
    donation_id: uuid.UUID, user: User = Depends(_actor), db: Session = Depends(get_db)
) -> Donation:
    donation = db.get(Donation, donation_id)
    if donation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Donation not found")
    hp = _actor_facility(db, user)
    _assert_can_review(donation, hp)
    return donation


@router.post("/{donation_id}/verify", response_model=DonationOut)
def verify_donation(
    donation_id: uuid.UUID,
    payload: DonationReviewIn,
    _: None = Depends(verify_origin),
    user: User = Depends(_actor),
    db: Session = Depends(get_db),
) -> Donation:
    """DONOR_REPORTED/REJECTED -> VERIFIED. Legal from either (an honest
    correction of a prior mistaken rejection is real and must stay
    possible -- see DonationVerificationStatus's own docstring); a
    same-state VERIFIED->VERIFIED call is rejected as a stale/duplicate
    action, the same rule every other lifecycle in this codebase applies."""
    donation = _get_for_review(db, donation_id)
    hp = _actor_facility(db, user)
    _assert_can_review(donation, hp)
    if donation.verification_status == DonationVerificationStatus.VERIFIED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="This donation is already verified"
        )

    donation.verification_status = DonationVerificationStatus.VERIFIED
    donation.verified_by_user_id = user.id
    donation.verified_at = utcnow()
    donation.verification_note = payload.note
    db.flush()

    audit_service.record(
        db, "donation.verified", actor_user_id=user.id, target_type="donation",
        target_id=donation.id,
        meta={"donor_user_id": str(donation.donor_user_id), "note": payload.note},
    )
    notification_service.notify(
        db, donation.donor_user_id, type="donation.verified",
        title="Your donation was verified",
        body=payload.note or "A facility confirmed this donation on your record.",
        data={"donation_id": str(donation.id)},
    )
    db.commit()
    db.refresh(donation)
    return donation


@router.post("/{donation_id}/reject", response_model=DonationOut)
def reject_donation(
    donation_id: uuid.UUID,
    payload: DonationReviewIn,
    _: None = Depends(verify_origin),
    user: User = Depends(_actor),
    db: Session = Depends(get_db),
) -> Donation:
    """DONOR_REPORTED/VERIFIED -> REJECTED. Symmetric with verify above --
    a mistaken VERIFIED can be corrected back to REJECTED, each one a
    fresh, audited, actor-attributed decision, never a silent overwrite.
    Rejecting a donation that already has an issued `Certificate` does
    NOT retroactively revoke that certificate (a separate, explicit
    revocation action already exists for certificates --
    app.api.routes.certificates.revoke_certificate -- and this phase does
    not fold the two together; see docs/LIFELINK_VERIFICATION_LIFECYCLE.md)."""
    donation = _get_for_review(db, donation_id)
    hp = _actor_facility(db, user)
    _assert_can_review(donation, hp)
    if donation.verification_status == DonationVerificationStatus.REJECTED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="This donation is already rejected"
        )

    donation.verification_status = DonationVerificationStatus.REJECTED
    donation.verified_by_user_id = user.id
    donation.verified_at = utcnow()
    donation.verification_note = payload.note
    db.flush()

    audit_service.record(
        db, "donation.rejected", actor_user_id=user.id, target_type="donation",
        target_id=donation.id,
        meta={"donor_user_id": str(donation.donor_user_id), "note": payload.note},
    )
    notification_service.notify(
        db, donation.donor_user_id, type="donation.rejected",
        title="A donation on your record was disputed",
        body=payload.note or "A facility could not confirm this donation.",
        data={"donation_id": str(donation.id)},
    )
    db.commit()
    db.refresh(donation)
    return donation
