"""Shared helpers for the Phase 2 patient / family / donor flows."""
from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.enums import FamilyLinkStatus
from app.models.family import FamilyLink
from app.models.hospital import HospitalProfile
from app.models.notification_preference import NotificationPreference
from app.models.profile import DonorProfile, PatientProfile
from app.models.user import User, UserRole


def _get_or_create_by_user(db: Session, model, user: User):
    """Race-safe get-or-create of a one-row-per-user table.

    Two first requests from the same new user can arrive concurrently (the
    SPA loads /donors/me and /donors/me/responses in parallel). Both used to
    miss the SELECT and both INSERT; the loser hit the unique index on
    user_id and the request failed with a 500 (seen in the 2026-10-12 E2E
    run). The insert now runs in a savepoint; on a unique violation the
    savepoint is rolled back and the winner's row is returned.
    """
    row = db.scalar(select(model).where(model.user_id == user.id))
    if row is not None:
        return row
    try:
        with db.begin_nested():
            row = model(user_id=user.id)
            db.add(row)
            db.flush()
        return row
    except IntegrityError:
        row = db.scalar(select(model).where(model.user_id == user.id))
        if row is None:  # a different constraint failed: do not hide it
            raise
        return row


def get_or_create_patient_profile(db: Session, user: User) -> PatientProfile:
    return _get_or_create_by_user(db, PatientProfile, user)


def get_or_create_donor_profile(db: Session, user: User) -> DonorProfile:
    return _get_or_create_by_user(db, DonorProfile, user)


def get_or_create_hospital_profile(db: Session, user: User) -> HospitalProfile:
    return _get_or_create_by_user(db, HospitalProfile, user)


def get_or_create_notification_preference(db: Session, user: User) -> NotificationPreference:
    pref = _get_or_create_by_user(db, NotificationPreference, user)
    return pref


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email.strip().lower()))


def resolve_user_phone(db: Session, user: User) -> str | None:
    """Best-effort phone number lookup for SMS delivery (app/services/sms_service.py).

    Phone numbers live on the role-specific profile table, not on User
    itself, so there's no single column to read -- this checks whichever
    table the user's role actually has one on. Returns the stored value
    as-is; profile-update schemas already normalize it to E.164 on write
    (app/core/phone.py), so a row with a phone number set is expected to
    already be in that form.

    Returns None, honestly, rather than guessing, for a user with no phone
    on file, or for a role with no phone field at all: FAMILY and ADMIN
    users have no profile table in this schema, so they simply cannot
    receive SMS today. That's a real, documented limitation
    (LIFELINK_SMS_PROVIDER.md), not a bug to work around here.
    """
    if user.role == UserRole.PATIENT:
        patient_profile = db.scalar(
            select(PatientProfile).where(PatientProfile.user_id == user.id)
        )
        return patient_profile.phone if patient_profile else None
    if user.role == UserRole.DONOR:
        donor_profile = db.scalar(select(DonorProfile).where(DonorProfile.user_id == user.id))
        return donor_profile.phone if donor_profile else None
    if user.role == UserRole.HOSPITAL:
        hospital_profile = db.scalar(
            select(HospitalProfile).where(HospitalProfile.user_id == user.id)
        )
        return hospital_profile.phone if hospital_profile else None
    return None


def has_accepted_link(db: Session, family_user_id, patient_user_id) -> bool:
    link = db.scalar(
        select(FamilyLink).where(
            FamilyLink.family_user_id == family_user_id,
            FamilyLink.patient_user_id == patient_user_id,
            FamilyLink.status == FamilyLinkStatus.ACCEPTED,
        )
    )
    if link is None:
        return False
    # A deactivated/suspended patient account is no longer manageable by their
    # family members, even though the link row itself still exists (it's not
    # deleted so the historical relationship and audit trail are preserved).
    patient = db.get(User, patient_user_id)
    return patient is not None and patient.is_active


def resolve_beneficiary(db: Session, requester: User, patient_email: str | None) -> User:
    """Return the patient a request/emergency is for.

    - No ``patient_email``: the requester themselves (must be PATIENT/FAMILY).
    - ``patient_email`` given: requester must be FAMILY with an ACCEPTED link
      to that patient.
    """
    if patient_email is None:
        if requester.role not in (UserRole.PATIENT, UserRole.FAMILY, UserRole.HOSPITAL):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only patients, family members or hospitals can file this",
            )
        return requester

    target = get_user_by_email(db, patient_email)
    if target is None or target.role != UserRole.PATIENT:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No patient with that email"
        )
    if requester.id == target.id:
        return target
    if not has_accepted_link(db, requester.id, target.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not an authorised family member for this patient",
        )
    return target
