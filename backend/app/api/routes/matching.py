"""Matching foundation + live map data.

Deterministic and factual: compatibility from the real blood matrix, real
haversine distance where coordinates exist, real availability and acceptance
state. No score is computed and no match is fabricated.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, require_roles
from app.core.blood_compatibility import (
    compatible_donor_types,
    compatible_recipient_types,
    to_ui_label,
)
from app.core.config import settings
from app.core.eligibility import is_eligible
from app.core.matching_engine import (
    MatchCandidateInput,
    MatchStrategy,
    apply_distance_limit,
    rank_candidates,
)
from app.models.blood_request import BloodRequest, DonorResponse
from app.models.enums import (
    BloodType,
    DonorResponseStatus,
    haversine_km,
)
from app.models.hospital import HospitalProfile
from app.models.profile import DonorProfile
from app.models.user import User, UserRole
from app.schemas.matching import (
    BloodCompatibilityOut,
    BloodGroupCompatibility,
    MapDataOut,
    MapDonorPin,
    MatchCandidate,
    MatchCandidatesOut,
    MatchFactorsOut,
    StrategyCandidateOut,
    StrategyComparisonOut,
    StrategyResultOut,
)
from app.services import audit_service
from app.services.domain_service import has_accepted_link

router = APIRouter(tags=["matching"])

# Coordinates are published to the map only at ~1km resolution.
_COORD_PRECISION = 2


def _round(v: float) -> float:
    return round(v, _COORD_PRECISION)


def _can_manage_request(db: Session, req: BloodRequest, user: User) -> bool:
    if user.id in (req.requester_user_id, req.patient_user_id):
        return True
    return user.role == UserRole.FAMILY and has_accepted_link(db, user.id, req.patient_user_id)


def _hospital_sees_request(db: Session, req: BloodRequest, user: User) -> bool:
    hp = db.scalar(select(HospitalProfile).where(HospitalProfile.user_id == user.id))
    if hp is None:
        return False
    return bool(
        (hp.city and req.city and hp.city == req.city)
        or (hp.name and req.hospital_name and hp.name == req.hospital_name)
    )


def _donor_candidate_inputs(
    db: Session, req: BloodRequest, compatible: list[BloodType]
) -> list[MatchCandidateInput]:
    """Build the engine's `MatchCandidateInput` list for a blood request's
    compatible, available donors. `name` is included on every input
    regardless of caller visibility -- it's the route's job to decide
    whether to expose it (donor names are gated on `is_manager`), not the
    pure ranking engine's.
    """
    accepted_ids = {
        d for (d,) in db.execute(
            select(DonorResponse.donor_user_id).where(
                DonorResponse.request_id == req.id,
                DonorResponse.status == DonorResponseStatus.ACCEPTED,
            )
        ).all()
    }
    rows = db.execute(
        select(DonorProfile, User)
        .join(User, User.id == DonorProfile.user_id)
        .where(
            DonorProfile.blood_type.in_(compatible),
            DonorProfile.is_available.is_(True),
            User.is_active.is_(True),
        )
    ).all()

    inputs: list[MatchCandidateInput] = []
    for dp, u in rows:
        distance = None
        if (
            req.latitude is not None and req.longitude is not None
            and dp.latitude is not None and dp.longitude is not None
            and dp.share_location
        ):
            distance = haversine_km(req.latitude, req.longitude, dp.latitude, dp.longitude)
        ref = str(u.id)[:8]
        inputs.append(MatchCandidateInput(
            ref=ref,
            name=u.full_name,
            blood_type=dp.blood_type,
            role="DONOR",
            is_available=dp.is_available,
            has_accepted=u.id in accepted_ids,
            distance_km=distance,
            last_donation_date=dp.last_donation_date,
            identity_verified=dp.identity_verified,
            is_eligible=is_eligible(dp.last_donation_date),
            alert_radius_km=dp.alert_radius_km,
            availability_updated_at=dp.updated_at,
            city=dp.city,
        ))
    return inputs


@router.get(
    "/blood-requests/{request_id}/match-candidates",
    response_model=MatchCandidatesOut,
)
def match_candidates(
    request_id: uuid.UUID,
    strategy: MatchStrategy = Query(default=MatchStrategy.NEAREST),
    override_distance_limit: bool = Query(default=False),
    override_reason: str | None = Query(default=None, max_length=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MatchCandidatesOut:
    req = db.get(BloodRequest, request_id)
    if req is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")

    # Audit 2026-10-31: an administrator may view the ranking -- the
    # distance-limit override below is ADMIN-only, and before this change an
    # admin got 404 here, so the override could never actually be used.
    is_manager = _can_manage_request(db, req, user) or user.role == UserRole.ADMIN
    is_hospital = user.role == UserRole.HOSPITAL and _hospital_sees_request(db, req, user)
    if not (is_manager or is_hospital):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")

    compatible = compatible_donor_types(req.blood_type)
    inputs = _donor_candidate_inputs(db, req, compatible)
    # Gap elimination: MAX_DONOR_DISTANCE_KM. Lifting it is an ADMIN-only
    # decision and every use is audited -- anyone else asking for it gets a
    # 403, never a silent fallback to the unfiltered list.
    if override_distance_limit:
        if user.role != UserRole.ADMIN:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="Only an administrator can override the distance limit")
        # Audit 2026-10-31: an override needs a recorded reason, like the
        # emergency DONOR_DISTANCE override already did.
        reason = (override_reason or "").strip()
        if len(reason) < 5:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail="A reason (at least 5 characters) is required to "
                                       "override the distance limit")
        audit_service.record(
            db, "matching.distance_limit_overridden", actor_user_id=user.id,
            target_type="blood_request", target_id=req.id,
            meta={"limit_km": settings.max_donor_distance_km, "reason": reason},
        )
        db.commit()
    policy = apply_distance_limit(inputs, limit_km=settings.max_donor_distance_km,
                                  override=override_distance_limit)
    inputs = policy.kept
    by_ref = {c.ref: c for c in inputs}
    ranked = rank_candidates(inputs, strategy=strategy, urgency=req.urgency)

    candidates = [
        MatchCandidate(
            donor_ref=rc.ref,
            donor_name=(by_ref[rc.ref].name if is_manager else None),
            blood_type=rc.blood_type,  # type: ignore[arg-type]
            is_available=rc.factors.available,
            has_accepted=rc.factors.has_accepted,
            distance_km=rc.factors.distance_km,
            last_donation_date=by_ref[rc.ref].last_donation_date,
            city=by_ref[rc.ref].city,
            rank=rc.rank,
            factors=MatchFactorsOut(**rc.factors.__dict__),
            explanation=rc.explanation,
        )
        for rc in ranked
    ]
    return MatchCandidatesOut(
        request_id=req.id,
        recipient_blood_type=req.blood_type,
        compatible_donor_types=compatible,
        strategy=strategy,
        candidates=candidates,
        distance_limit_km=policy.limit_km,
        excluded_beyond_limit=len(policy.excluded_refs),
        unknown_distance=policy.unknown_distance,
        distance_override_active=policy.override_active,
    )


@router.get(
    "/blood-requests/{request_id}/match-strategies",
    response_model=StrategyComparisonOut,
)
def blood_request_match_strategies(
    request_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StrategyComparisonOut:
    """Research-mode strategy comparison: the same real candidate pool as
    `match-candidates`, ranked under all four Phase 9 strategies at once
    so they can be compared side by side. Always anonymised (no donor
    names) regardless of caller, since this is a comparison/analysis view
    rather than an actionable candidate list.
    """
    req = db.get(BloodRequest, request_id)
    if req is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")
    is_manager = _can_manage_request(db, req, user)
    is_hospital = user.role == UserRole.HOSPITAL and _hospital_sees_request(db, req, user)
    if not (is_manager or is_hospital):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")

    compatible = compatible_donor_types(req.blood_type)
    inputs = apply_distance_limit(
        _donor_candidate_inputs(db, req, compatible), limit_km=settings.max_donor_distance_km,
    ).kept

    strategies = [
        StrategyResultOut(
            strategy=strategy,
            candidates=[
                StrategyCandidateOut(
                    donor_ref=rc.ref,
                    blood_type=rc.blood_type,
                    rank=rc.rank,
                    factors=MatchFactorsOut(**rc.factors.__dict__),
                    explanation=rc.explanation,
                )
                for rc in rank_candidates(inputs, strategy=strategy, urgency=req.urgency)
            ],
        )
        for strategy in MatchStrategy
    ]
    return StrategyComparisonOut(recipient_blood_type=req.blood_type, strategies=strategies)


@router.get("/matching/blood-compatibility", response_model=BloodCompatibilityOut)
def blood_compatibility(user: User = Depends(get_current_user)) -> BloodCompatibilityOut:
    """The full authoritative ABO/Rh compatibility matrix, straight from
    the central engine (`app.core.blood_compatibility`). Any signed-in
    user may read this -- it's the same reference chart the frontend used
    to hardcode; there is nothing sensitive in it.
    """
    return BloodCompatibilityOut(
        groups=[
            BloodGroupCompatibility(
                blood_type=bt,
                ui_label=to_ui_label(bt),
                can_donate_to=compatible_recipient_types(bt),
                can_receive_from=compatible_donor_types(bt),
            )
            for bt in BloodType
        ]
    )


@router.get("/map/data", response_model=MapDataOut)
def map_data(
    blood_type: BloodType | None = Query(default=None),
    user: User = Depends(require_roles(UserRole.HOSPITAL, UserRole.PATIENT, UserRole.FAMILY)),
    db: Session = Depends(get_db),
) -> MapDataOut:
    center = None
    if user.role == UserRole.HOSPITAL:
        hp = db.scalar(select(HospitalProfile).where(HospitalProfile.user_id == user.id))
        if hp and hp.latitude is not None and hp.longitude is not None:
            center = {"lat": hp.latitude, "lng": hp.longitude}

    wanted = compatible_donor_types(blood_type) if blood_type else None
    stmt = select(DonorProfile).where(
        DonorProfile.is_available.is_(True),
        DonorProfile.share_location.is_(True),
        DonorProfile.latitude.is_not(None),
        DonorProfile.longitude.is_not(None),
    )
    if wanted is not None:
        stmt = stmt.where(DonorProfile.blood_type.in_(wanted))

    pins = [
        MapDonorPin(
            blood_type=dp.blood_type,  # type: ignore[arg-type]
            latitude=_round(dp.latitude),   # type: ignore[arg-type]
            longitude=_round(dp.longitude),  # type: ignore[arg-type]
        )
        for dp in db.scalars(stmt).all()
        if dp.blood_type is not None
    ]
    return MapDataOut(center=center, donors=pins)
