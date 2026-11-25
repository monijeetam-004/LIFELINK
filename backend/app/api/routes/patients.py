from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_roles, verify_origin
from app.models.enums import FamilyLinkStatus
from app.models.family import FamilyLink
from app.models.user import User, UserRole
from app.schemas.family import PersonOut
from app.schemas.profile import PatientProfileOut, PatientProfileUpdate
from app.services.domain_service import get_or_create_patient_profile

router = APIRouter(prefix="/patients", tags=["patients"])

_patient_or_family = require_roles(UserRole.PATIENT, UserRole.FAMILY)


@router.get("/me", response_model=PatientProfileOut)
def get_my_patient_profile(
    user: User = Depends(_patient_or_family), db: Session = Depends(get_db)
):
    profile = get_or_create_patient_profile(db, user)
    db.commit()
    return profile


@router.put("/me", response_model=PatientProfileOut)
def update_my_patient_profile(
    payload: PatientProfileUpdate,
    user: User = Depends(_patient_or_family),
    db: Session = Depends(get_db),
    _: None = Depends(verify_origin),
):
    profile = get_or_create_patient_profile(db, user)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)
    db.commit()
    db.refresh(profile)
    return profile


@router.get("/managed", response_model=list[PersonOut])
def list_managed_patients(
    user: User = Depends(require_roles(UserRole.FAMILY)), db: Session = Depends(get_db)
) -> list[User]:
    rows = db.scalars(
        select(User)
        .join(FamilyLink, FamilyLink.patient_user_id == User.id)
        .where(
            FamilyLink.family_user_id == user.id,
            FamilyLink.status == FamilyLinkStatus.ACCEPTED,
            # A deactivated/suspended patient is no longer manageable by
            # their family (mirrors domain_service.has_accepted_link).
            User.is_active.is_(True),
        )
    ).all()
    return list(rows)
