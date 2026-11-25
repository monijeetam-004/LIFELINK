from __future__ import annotations

import uuid
from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, require_roles, verify_origin
from app.core import compatibility
from app.core.config import settings
from app.core.eligibility import eligible_on, is_eligible
from app.core.emergency_lifecycle import InvalidTransitionError, allowed_transitions
from app.core.matching_engine import (
    MatchCandidateInput,
    MatchStrategy,
    apply_distance_limit,
    rank_candidates,
)
from app.core.ratelimit import enforce_rate_limit
from app.core.time import utcnow
from app.models.emergency import Emergency, EmergencyEvent, EmergencyOverride, EmergencyResponse
from app.models.emergency_pathway import EmergencyPathway
from app.models.enums import (
    EMERGENCY_OPEN_STATUSES,
    BloodComponent,
    BloodType,
    BloodUnitState,
    DonorResponseStatus,
    EmergencyOverrideKind,
    EmergencyPathwayStatus,
    EmergencyStatus,
    FamilyLinkStatus,
    ShortlistSubject,
    haversine_km,
)
from app.models.family import FamilyLink
from app.models.graph_pathway_log import GraphPathwayLog
from app.models.hospital import HospitalInventoryLog, HospitalProfile
from app.models.profile import DonorProfile
from app.models.user import User, UserRole
from app.schemas.emergency import (
    EmergencyActionIn,
    EmergencyCreate,
    EmergencyDetailOut,
    EmergencyEventOut,
    EmergencyMatchOut,
    EmergencyMatchResponder,
    EmergencyOpenOut,
    EmergencyOut,
    EmergencyPathwayOut,
    EmergencyPathwaySelectIn,
    EmergencyResponderOut,
    EmergencyRespondIn,
)
from app.schemas.graph import PathwayCandidateOut, ResourcePathwayOut
from app.schemas.matching import (
    MatchFactorsOut,
    StrategyCandidateOut,
    StrategyComparisonOut,
    StrategyResultOut,
)
from app.schemas.pathway import (
    EmergencyOverrideIn,
    EmergencyOverrideOut,
    StoredPathwayOut,
    stored_pathway_out,
)
from app.services import (
    audit_service,
    donor_commitment,
    emergency_lifecycle,
    emergency_pathway_service,
    geocoding,
    graph_service,
    inventory_service,
    notification_service,
)
from app.services.domain_service import (
    get_or_create_donor_profile,
    has_accepted_link,
    resolve_beneficiary,
)
from app.services.inventory_service import InventoryActionError
from app.services.pathway_planner import PlanningError

router = APIRouter(prefix="/emergencies", tags=["emergencies"])

_RESPONDER_ROLES = (UserRole.DONOR, UserRole.HOSPITAL)


def _event(db: Session, em: Emergency, actor: uuid.UUID | None, kind: str, note: str | None = None):
    db.add(EmergencyEvent(emergency_id=em.id, actor_user_id=actor, event_type=kind, note=note))


def _accepted_count(db: Session, emergency_id: uuid.UUID) -> int:
    return int(db.scalar(
        select(func.count()).select_from(EmergencyResponse).where(
            EmergencyResponse.emergency_id == emergency_id,
            EmergencyResponse.status == DonorResponseStatus.ACCEPTED,
        )
    ) or 0)


def _hospital_area_match(hp: HospitalProfile | None, em: Emergency) -> bool:
    """Is this facility in the emergency's area? By real distance when both
    sides have coordinates (within HOSPITAL_ALERT_RADIUS_KM), otherwise by
    the structured city or a city named in the address text."""
    if hp is None:
        return False
    if (em.latitude is not None and em.longitude is not None
            and hp.latitude is not None and hp.longitude is not None):
        return haversine_km(em.latitude, em.longitude, hp.latitude, hp.longitude) \
            <= settings.hospital_alert_radius_km
    if not hp.city:
        return False
    city = hp.city.lower()
    if em.city and em.city.lower() == city:
        return True
    return bool(em.location_text and city in em.location_text.lower())


def _component(em: Emergency) -> BloodComponent:
    return em.component or BloodComponent.WHOLE_BLOOD


def _donor_compatible(donor_type: BloodType, em: Emergency) -> bool:
    """Central compatibility service, for the emergency's component."""
    return em.blood_type is None or compatibility.is_compatible(
        donor_type, em.blood_type, _component(em))


def _donor_is_candidate(db: Session, em: Emergency, user: User) -> bool:
    dp = db.scalar(select(DonorProfile).where(DonorProfile.user_id == user.id))
    if dp is None or not dp.is_available or dp.blood_type is None:
        return False
    return _donor_compatible(dp.blood_type, em)


def _can_view(db: Session, em: Emergency, user: User) -> bool:
    if user.id == em.created_by_user_id or (em.patient_user_id and user.id == em.patient_user_id):
        return True
    if em.patient_user_id and user.role == UserRole.FAMILY and has_accepted_link(
        db, user.id, em.patient_user_id
    ):
        return True
    if user.role == UserRole.ADMIN:
        return True
    if user.role == UserRole.HOSPITAL:
        hp = db.scalar(select(HospitalProfile).where(HospitalProfile.user_id == user.id))
        return _hospital_area_match(hp, em) or em.status in EMERGENCY_OPEN_STATUSES
    if user.role == UserRole.DONOR:
        responded = db.scalar(select(EmergencyResponse).where(
            EmergencyResponse.emergency_id == em.id,
            EmergencyResponse.responder_user_id == user.id,
        ))
        if responded is not None:
            return True
        return em.status in EMERGENCY_OPEN_STATUSES and _donor_is_candidate(db, em, user)
    return False


def _is_creator(em: Emergency, user: User) -> bool:
    return em.created_by_user_id == user.id


def _deny_access(db: Session, em: Emergency | None, user: User, *, action: str) -> NoReturn:
    """Same reasoning as blood_requests._deny_access: audit a denial only
    when the emergency genuinely exists (a real cross-tenant probe), never
    for a plain bad id."""
    if em is not None:
        audit_service.record(
            db, "emergency.access_denied", actor_user_id=user.id,
            target_type="emergency", target_id=em.id,
            meta={"actor_role": user.role.value, "attempted_action": action},
        )
        db.commit()
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


def _fill_names(db: Session, out: EmergencyOut, em: Emergency) -> None:
    creator = db.get(User, em.created_by_user_id)
    out.created_by_name = creator.full_name if creator else None
    if em.patient_user_id:
        patient = db.get(User, em.patient_user_id)
        out.patient_name = patient.full_name if patient else None
        out.filed_by_family = em.created_by_user_id != em.patient_user_id
    else:
        out.patient_name = None
        out.filed_by_family = False


def _slack_seconds(em: Emergency) -> float | None:
    """Positive: time remaining before `deadline`. Zero: exactly at the
    deadline. Negative: already overdue -- returned honestly, never
    clamped to zero, matching the Phase 12 spec's explicit "do not show an
    emergency as on-time when it is actually late." None when there is no
    deadline recorded at all (nothing to measure slack against)."""
    if em.deadline is None:
        return None
    return (em.deadline - utcnow()).total_seconds()


def _to_out(db: Session, em: Emergency) -> EmergencyOut:
    out = EmergencyOut.model_validate(em)
    _fill_names(db, out, em)
    out.slack_seconds = _slack_seconds(em)
    out.allowed_transitions = allowed_transitions(em.status)
    return out


# --------------------------------------------------------------------------- #
# Inventory reservation helpers (Phase 12) -- reuses the existing, unchanged
# app.services.inventory_service; no new reservation bookkeeping is added.
# Outstanding-per-emergency reservation amounts are *derived* from the
# existing HospitalInventoryLog audit trail (grouping by hospital + blood
# type, summing signed `delta_reserved`) rather than tracked in a new
# column, so "how much is still reserved for this emergency" is always
# provably consistent with the same ledger inventory_service itself writes.
# --------------------------------------------------------------------------- #
def _outstanding_reservations(
    db: Session, emergency_id: uuid.UUID
) -> list[tuple[uuid.UUID, BloodType, BloodComponent, int]]:
    rows = db.execute(
        select(
            HospitalInventoryLog.hospital_id,
            HospitalInventoryLog.blood_type,
            HospitalInventoryLog.component,
            func.sum(HospitalInventoryLog.delta_reserved),
        )
        .where(HospitalInventoryLog.emergency_id == emergency_id)
        .group_by(HospitalInventoryLog.hospital_id, HospitalInventoryLog.blood_type,
                  HospitalInventoryLog.component)
        .having(func.sum(HospitalInventoryLog.delta_reserved) > 0)
    ).all()
    return [(hid, bt, comp, int(units)) for hid, bt, comp, units in rows]


def _release_all_reservations(
    db: Session, em: Emergency, *, actor_user_id: uuid.UUID | None, reason: str
) -> list[str]:
    """Release every outstanding inventory reservation this emergency still
    holds across every hospital that reserved for it (used on cancel and
    resolve). Returns a list of human-readable notes for the audit meta --
    never silently swallows a release failure (that would misreport a
    resource as freed when it wasn't); a failure here is surfaced in the
    caller's transition event/audit instead of hidden.

    Gap elimination: grouped by component too (a PLASMA hold is released as
    PLASMA, never as whole blood), and pathway reservation records are
    closed in the same transaction so they never claim units that were
    already given back."""
    from app.models.emergency_pathway import PathwayReservation

    notes: list[str] = []
    for hospital_id, blood_type, component, units in _outstanding_reservations(db, em.id):
        try:
            inventory_service.release_reservation(
                db, hospital_id, blood_type, units,
                actor_user_id=actor_user_id, emergency_id=em.id, note=reason,
                component=component,
            )
            notes.append(f"released {units} {blood_type.value} {component.value} "
                         f"at facility {hospital_id}")
        except InventoryActionError as exc:  # pragma: no cover - defensive; see report
            notes.append(f"FAILED to release {units} {blood_type.value}: {exc}")
    now = utcnow()
    for r in db.scalars(select(PathwayReservation).where(
            PathwayReservation.emergency_id == em.id,
            PathwayReservation.active.is_(True))).all():
        r.active = False
        r.released_at = now
        r.release_reason = reason[:300]
    return notes


def _notify_accepted_responders(db: Session, em: Emergency, *, type: str, title: str,
                                body: str, exclude: uuid.UUID | None = None) -> None:
    for (rid,) in db.execute(
        select(EmergencyResponse.responder_user_id).where(
            EmergencyResponse.emergency_id == em.id,
            EmergencyResponse.status == DonorResponseStatus.ACCEPTED,
        )
    ).all():
        if rid != exclude:
            notification_service.notify(db, rid, type=type, title=title, body=body,
                                        data={"emergency_id": str(em.id)})


# --------------------------------------------------------------------------- #
# Create / list / detail
# --------------------------------------------------------------------------- #
def _find_open_duplicate(
    db: Session, creator_user_id: uuid.UUID, patient_user_id: uuid.UUID
) -> uuid.UUID | None:
    """Non-blocking duplicate-SOS signal (Phase 12): does this actor already
    have an OPEN emergency for the same patient? Never prevents creation --
    only surfaces `possible_duplicate_of` on the response so the frontend
    can warn without silently dropping a genuinely separate second
    emergency (e.g. a real second, unrelated need for the same patient)."""
    return db.scalar(
        select(Emergency.id).where(
            Emergency.created_by_user_id == creator_user_id,
            Emergency.patient_user_id == patient_user_id,
            Emergency.status.in_(EMERGENCY_OPEN_STATUSES),
        ).order_by(Emergency.created_at.desc()).limit(1)
    )


def _validate_target_facility(db: Session, target_facility_id: uuid.UUID | None) -> None:
    if target_facility_id is None:
        return
    facility = db.get(HospitalProfile, target_facility_id)
    if facility is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="target_facility_id not found"
        )
    if not facility.verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="target_facility_id is not a verified facility",
        )


def _alert_hospitals(db: Session, em: Emergency, *, exclude_user_id: uuid.UUID) -> int:
    """Alert the facilities that can actually help (gap elimination #20):

    * VERIFIED facilities only, owned by an active account;
    * within HOSPITAL_ALERT_RADIUS_KM of the emergency when both sides have
      coordinates -- otherwise the same city (structured, or named in the
      address text);
    * that hold at least one usable (AVAILABLE, in-date) unit of the
      requested component compatible with the patient -- unless the
      emergency has no blood type yet, when capability cannot be judged and
      every in-area verified facility is told;
    * plus the named target facility, always.
    """
    from app.models.blood_unit import BloodUnit
    from app.services.inventory_service import usable_until_cond

    component = _component(em)
    now = utcnow()
    rows = db.execute(
        select(HospitalProfile, User).join(User, User.id == HospitalProfile.user_id)
        .where(User.is_active.is_(True), HospitalProfile.verified.is_(True))
    ).all()
    capable: set[uuid.UUID] | None = None
    if em.blood_type is not None:
        types = compatibility.compatible_source_types(em.blood_type, component)
        capable = set(db.scalars(
            select(BloodUnit.hospital_id).where(
                BloodUnit.state == BloodUnitState.AVAILABLE,
                BloodUnit.component == component,
                BloodUnit.blood_type.in_(types),
                usable_until_cond(now),
            ).distinct()
        ).all())
    alerted = 0
    for hp, owner in rows:
        if owner.id == exclude_user_id:
            continue
        is_target = em.target_facility_id == hp.id
        if not is_target:
            if not _hospital_area_match(hp, em):
                continue
            if capable is not None and hp.id not in capable:
                continue
        notification_service.notify(
            db, owner.id, type="emergency.new",
            title="EMERGENCY: your facility can help" if not is_target
            else "EMERGENCY: delivery to your facility",
            body=em.location_text or (em.city or "Emergency nearby"),
            data={"emergency_id": str(em.id)},
        )
        alerted += 1
    return alerted


@router.post("", response_model=EmergencyOut, status_code=status.HTTP_201_CREATED)
def create_emergency(
    payload: EmergencyCreate,
    response: Response,
    _: None = Depends(verify_origin),
    user: User = Depends(require_roles(UserRole.PATIENT, UserRole.FAMILY, UserRole.HOSPITAL)),
    db: Session = Depends(get_db),
) -> EmergencyOut:
    # Idempotent replay: the exact same (actor, idempotency_key) pair
    # returns the existing emergency instead of creating a second one --
    # protects against double-clicking the SOS button, a browser/network
    # retry, or a duplicate frontend submission. Checked up front so a
    # replay never even evaluates target-facility validation twice.
    if payload.idempotency_key is not None:
        existing = db.scalar(
            select(Emergency).where(
                Emergency.created_by_user_id == user.id,
                Emergency.idempotency_key == payload.idempotency_key,
            )
        )
        if existing is not None:
            response.status_code = status.HTTP_200_OK
            return _to_out(db, existing)

    # Phase 23: rate-limited only past the idempotency-replay check above, so
    # retrying the exact same submission (a double-click, a network retry)
    # never consumes a request against this limit -- only genuinely distinct
    # emergency creations do.
    enforce_rate_limit(
        "emergency_create:account", str(user.id), *settings.rl_emergency_create_account
    )

    _validate_target_facility(db, payload.target_facility_id)
    beneficiary = resolve_beneficiary(db, user, payload.patient_email)
    possible_duplicate_of = _find_open_duplicate(db, user.id, beneficiary.id)
    # Gap elimination: resolve a structured location (coordinates + where
    # they came from). Never invented -- see app.services.geocoding.
    try:
        geo = geocoding.resolve(
            db, latitude=payload.latitude, longitude=payload.longitude, city=payload.city,
            location_text=payload.location_text,
            target_facility=(db.get(HospitalProfile, payload.target_facility_id)
                             if payload.target_facility_id else None),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail=str(exc)) from exc

    def _build_emergency() -> Emergency:
        return Emergency(
            created_by_user_id=user.id,
            patient_user_id=beneficiary.id,
            blood_type=payload.blood_type,
            urgency=payload.urgency,
            status=EmergencyStatus.ACTIVE,
            location_text=payload.location_text,
            note=payload.note,
            units_needed=payload.units_needed,
            deadline=payload.deadline,
            idempotency_key=payload.idempotency_key,
            target_facility_id=payload.target_facility_id,
            component=payload.component,
            city=payload.city,
            latitude=geo.latitude if geo else None,
            longitude=geo.longitude if geo else None,
            geocode_source=geo.source if geo else None,
            geocode_detail=geo.detail[:300] if geo else None,
        )

    if payload.idempotency_key is not None:
        # Race-safe insert: two near-simultaneous requests carrying the
        # same (actor, key) pair must not both create a row -- the second
        # one to reach the DB hits the unique constraint added in Phase 12
        # (`uq_emergency_idempotency_key`) and replays the first's result
        # instead, exactly like the existing accept/decline race handling
        # in respond_to_emergency below.
        #
        # `db.begin_nested()` must be called *before* `em` is added to the
        # session: Session.begin_nested() autoflushes any already-pending
        # objects while establishing the SAVEPOINT, so if `em` were added
        # first, a genuine unique-constraint collision would raise here --
        # outside this try block -- instead of inside it where it's caught.
        savepoint = db.begin_nested()
        try:
            em = _build_emergency()
            db.add(em)
            db.flush()
            savepoint.commit()
        except IntegrityError:
            savepoint.rollback()
            existing = db.scalar(
                select(Emergency).where(
                    Emergency.created_by_user_id == user.id,
                    Emergency.idempotency_key == payload.idempotency_key,
                )
            )
            if existing is None:
                raise  # pragma: no cover - constraint violation implies a row exists
            response.status_code = status.HTTP_200_OK
            return _to_out(db, existing)
    else:
        em = _build_emergency()
        db.add(em)
        db.flush()
    _event(db, em, user.id, "created", payload.note)

    notified = 0
    alert_stats: dict[str, int] = {}
    if payload.blood_type is not None and settings.donor_alert_mode == "shortlist":
        from app.services import donor_shortlist_service

        db.flush()
        subject = donor_shortlist_service.load_subject(db, ShortlistSubject.EMERGENCY, em.id)
        notified = len(donor_shortlist_service.contact_next_batch(
            db, subject, actor_user_id=user.id))
    elif payload.blood_type is not None:
        notified = notification_service.notify_matching_donors(
            db,
            recipient_blood_type=payload.blood_type,
            type="emergency.new",
            title="EMERGENCY: blood needed now",
            body=f"{payload.blood_type.value} {payload.component.value.replace('_', ' ').lower()}"
                 + (f" · {payload.location_text}" if payload.location_text else ""),
            data={"emergency_id": str(em.id)},
            exclude_user_id=user.id,
            # Gap elimination: the donor's urgency preference, opt-out,
            # hourly cap, alert radius and the donor distance limit all
            # apply to SOS alerts now (see notify_matching_donors).
            urgency=payload.urgency,
            component=payload.component,
            origin_lat=em.latitude,
            origin_lng=em.longitude,
            origin_city=em.city,
            max_distance_km=settings.max_donor_distance_km,
            emergency=True,
            stats=alert_stats,
        )
        if alert_stats.get("no_request_location"):
            _event(db, em, None, "donor_alerts_withheld",
                   "No donor was alerted: the emergency has no location or city, so the "
                   f"{settings.max_donor_distance_km:g} km donor distance limit cannot be "
                   "applied. Add a location, or an administrator can grant an audited "
                   "donor-distance override.")
    hospitals_alerted = _alert_hospitals(db, em, exclude_user_id=user.id)
    audit_service.record(db, "emergency.created", actor_user_id=user.id,
                         target_type="emergency", target_id=em.id,
                         meta={
                             "notified_donors": notified,
                             "donor_alert_skips": alert_stats,
                             "hospitals_alerted": hospitals_alerted,
                             "component": payload.component.value,
                             "geocode_source": (
                                 em.geocode_source.value if em.geocode_source else None),
                             "possible_duplicate_of": (
                                 str(possible_duplicate_of) if possible_duplicate_of else None
                             ),
                         })
    db.commit()
    db.refresh(em)
    out = _to_out(db, em)
    out.possible_duplicate_of = possible_duplicate_of
    return out


@router.get("", response_model=list[EmergencyOut])
def list_my_emergencies(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[EmergencyOut]:
    visible_patient_ids: set[uuid.UUID] = {user.id}
    if user.role == UserRole.FAMILY:
        # Same reasoning as the blood-request list: a family member should see
        # a linked patient's emergencies even if the patient raised the SOS
        # themselves, not only the ones the family member personally started.
        visible_patient_ids |= set(
            db.scalars(
                select(FamilyLink.patient_user_id)
                .join(User, User.id == FamilyLink.patient_user_id)
                .where(
                    FamilyLink.family_user_id == user.id,
                    FamilyLink.status == FamilyLinkStatus.ACCEPTED,
                    User.is_active.is_(True),
                )
            ).all()
        )
    rows = db.scalars(
        select(Emergency)
        .where(or_(
            Emergency.created_by_user_id == user.id,
            Emergency.patient_user_id.in_(visible_patient_ids),
        ))
        .order_by(Emergency.created_at.desc())
    ).all()
    for em in rows:
        emergency_lifecycle.flag_deadline_exceeded_if_needed(db, em)
    return [_to_out(db, em) for em in rows]


@router.get("/active", response_model=list[EmergencyOpenOut])
def list_active_for_responder(
    user: User = Depends(require_roles(*_RESPONDER_ROLES)),
    db: Session = Depends(get_db),
) -> list[EmergencyOpenOut]:
    """Open emergencies this donor / hospital could respond to."""
    open_states = (EmergencyStatus.ACTIVE, EmergencyStatus.ACKNOWLEDGED, EmergencyStatus.MATCHING)
    rows = db.scalars(
        select(Emergency).where(Emergency.status.in_(open_states))
        .order_by(Emergency.created_at.desc())
    ).all()

    hp = None
    dp = None
    if user.role == UserRole.HOSPITAL:
        hp = db.scalar(select(HospitalProfile).where(HospitalProfile.user_id == user.id))
    else:
        dp = db.scalar(select(DonorProfile).where(DonorProfile.user_id == user.id))

    # Phase K: this loop used to run two queries per open emergency (the
    # caller's own response + the accepted count) -- 405 statements for 200
    # open emergencies in the Phase K volume database. Both are now fetched
    # for every open emergency in one query each; the visibility rules and
    # the response shape are unchanged.
    ids = [em.id for em in rows]
    mine_by_em = {
        r.emergency_id: r for r in db.scalars(select(EmergencyResponse).where(
            EmergencyResponse.emergency_id.in_(ids),
            EmergencyResponse.responder_user_id == user.id,
        )).all()
    } if ids else {}
    accepted_by_em = dict(db.execute(
        select(EmergencyResponse.emergency_id, func.count())
        .where(EmergencyResponse.emergency_id.in_(ids),
               EmergencyResponse.status == DonorResponseStatus.ACCEPTED)
        .group_by(EmergencyResponse.emergency_id)
    ).all()) if ids else {}

    out: list[EmergencyOpenOut] = []
    for em in rows:
        emergency_lifecycle.flag_deadline_exceeded_if_needed(db, em)
        if user.role == UserRole.HOSPITAL:
            visible = _hospital_area_match(hp, em) or (
                hp is not None and em.target_facility_id == hp.id)
        else:
            visible = (
                dp is not None and dp.is_available and dp.blood_type is not None
                and _donor_compatible(dp.blood_type, em)
            )
        mine = mine_by_em.get(em.id)
        if not visible and mine is None:
            continue
        item = EmergencyOpenOut.model_validate(em)
        item.accepted_count = int(accepted_by_em.get(em.id, 0))
        item.my_response = EmergencyResponderOut.model_validate(mine) if mine else None
        out.append(item)
    return out


@router.get("/{emergency_id}", response_model=EmergencyDetailOut)
def get_emergency(
    emergency_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EmergencyDetailOut:
    em = db.get(Emergency, emergency_id)
    if em is None or not _can_view(db, em, user):
        _deny_access(db, em, user, action="view")
    em = emergency_lifecycle.flag_deadline_exceeded_if_needed(db, em)

    detail = EmergencyDetailOut.model_validate(em)
    _fill_names(db, detail, em)
    detail.slack_seconds = _slack_seconds(em)
    detail.allowed_transitions = allowed_transitions(em.status)
    detail.accepted_count = _accepted_count(db, em.id)

    pathways = db.scalars(
        select(EmergencyPathway).where(EmergencyPathway.emergency_id == em.id)
        .order_by(EmergencyPathway.version.asc())
    ).all()
    detail.pathway_history = [EmergencyPathwayOut.model_validate(p) for p in pathways]
    detail.current_pathway = next(
        (p for p in detail.pathway_history if p.status == EmergencyPathwayStatus.ACTIVE), None
    )
    detail.backup_pathways = sorted(
        (p for p in detail.pathway_history if p.status == EmergencyPathwayStatus.BACKUP),
        key=lambda p: p.backup_rank,
    )
    detail.overrides = [
        EmergencyOverrideOut.model_validate(o).model_dump(mode="json")
        for o in db.scalars(select(EmergencyOverride).where(
            EmergencyOverride.emergency_id == em.id).order_by(EmergencyOverride.created_at))
    ]

    events = db.scalars(
        select(EmergencyEvent).where(EmergencyEvent.emergency_id == em.id)
        .order_by(EmergencyEvent.created_at.asc())
    ).all()
    detail.events = [EmergencyEventOut.model_validate(e) for e in events]

    is_creator = _is_creator(em, user)
    responders = db.scalars(
        select(EmergencyResponse).where(EmergencyResponse.emergency_id == em.id)
        .order_by(EmergencyResponse.created_at.asc())
    ).all()
    out_list = []
    for r in responders:
        ro = EmergencyResponderOut.model_validate(r)
        if is_creator or user.role == UserRole.ADMIN:
            responder = db.get(User, r.responder_user_id)
            ro.responder_name = responder.full_name if responder else None
        else:
            ro.responder_name = None
        out_list.append(ro)
    detail.responders = out_list

    if user.role in _RESPONDER_ROLES:
        mine = db.scalar(select(EmergencyResponse).where(
            EmergencyResponse.emergency_id == em.id,
            EmergencyResponse.responder_user_id == user.id,
        ))
        detail.my_response = EmergencyResponderOut.model_validate(mine) if mine else None
    return detail


def _emergency_candidate_inputs(
    db: Session, em: Emergency, compatible: list[BloodType]
) -> tuple[list[MatchCandidateInput], dict[str, tuple[str | None, UserRole, bool | None]]]:
    """Build the engine's `MatchCandidateInput` list for an emergency's
    donor + hospital responders. Returns the inputs plus a
    `ref -> (name, role, facility_verified)` side-table.

    Gap elimination:
    * donor distance is the REAL haversine distance from the emergency's
      coordinates to a donor who shares their location (None -- "unknown",
      ranked last -- otherwise), so NEAREST is genuinely distance-aware;
    * hospitals: only VERIFIED facilities are offered as responders (an
      unverified one that already accepted stays visible, flagged), and a
      hospital's distance is its real distance from the emergency when both
      have coordinates; in-area matching uses distance first, city second.
    """
    accepted_ids = {
        d for (d,) in db.execute(
            select(EmergencyResponse.responder_user_id).where(
                EmergencyResponse.emergency_id == em.id,
                EmergencyResponse.status == DonorResponseStatus.ACCEPTED,
            )
        ).all()
    }
    has_coords = em.latitude is not None and em.longitude is not None

    donor_stmt = (
        select(DonorProfile, User)
        .join(User, User.id == DonorProfile.user_id)
        .where(DonorProfile.is_available.is_(True), User.is_active.is_(True))
    )
    if compatible:
        donor_stmt = donor_stmt.where(DonorProfile.blood_type.in_(compatible))
    else:
        donor_stmt = donor_stmt.where(DonorProfile.blood_type.is_not(None))

    inputs: list[MatchCandidateInput] = []
    meta: dict[str, tuple[str | None, UserRole, bool | None]] = {}
    for dp, u in db.execute(donor_stmt).all():
        ref = str(u.id)[:8]
        meta[ref] = (u.full_name, UserRole.DONOR, None)
        distance = None
        if (has_coords and dp.share_location and dp.latitude is not None
                and dp.longitude is not None):
            distance = round(haversine_km(em.latitude, em.longitude,
                                          dp.latitude, dp.longitude), 3)
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

    for hp, u in db.execute(
        select(HospitalProfile, User).join(User, User.id == HospitalProfile.user_id)
        .where(User.is_active.is_(True))
    ).all():
        accepted = u.id in accepted_ids
        in_area = _hospital_area_match(hp, em) or em.target_facility_id == hp.id
        if not (in_area or accepted):
            continue
        if not hp.verified and not accepted:
            continue
        ref = str(u.id)[:8]
        meta[ref] = ((hp.name or u.full_name), UserRole.HOSPITAL, bool(hp.verified))
        distance = None
        if has_coords and hp.latitude is not None and hp.longitude is not None:
            distance = round(haversine_km(em.latitude, em.longitude,
                                          hp.latitude, hp.longitude), 3)
        inputs.append(MatchCandidateInput(
            ref=ref,
            name=hp.name or u.full_name,
            blood_type=None,
            role="HOSPITAL",
            is_available=True,
            has_accepted=accepted,
            distance_km=distance,
            last_donation_date=None,
            identity_verified=bool(hp.verified),
            is_eligible=None,
            alert_radius_km=None,
            availability_updated_at=hp.updated_at,
            city=hp.city,
        ))
    return inputs, meta


def _distance_policy(db: Session, em: Emergency, inputs: list[MatchCandidateInput]):
    override = emergency_pathway_service.active_override(
        db, em.id, EmergencyOverrideKind.DONOR_DISTANCE)
    return apply_distance_limit(inputs, limit_km=settings.max_donor_distance_km,
                                override=override)


def _compatible_for(em: Emergency) -> list[BloodType]:
    if em.blood_type is None:
        return []
    return compatibility.compatible_source_types(em.blood_type, _component(em))


@router.get("/{emergency_id}/match-candidates", response_model=EmergencyMatchOut)
def emergency_match_candidates(
    emergency_id: uuid.UUID,
    strategy: MatchStrategy = Query(default=MatchStrategy.NEAREST),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EmergencyMatchOut:
    em = db.get(Emergency, emergency_id)
    if em is None or not _can_view(db, em, user):
        _deny_access(db, em, user, action="view_match_candidates")
    is_manager = _is_creator(em, user) or user.role == UserRole.ADMIN or (
        em.patient_user_id and user.id == em.patient_user_id
    )

    compatible = _compatible_for(em)
    inputs, meta = _emergency_candidate_inputs(db, em, compatible)
    policy = _distance_policy(db, em, inputs)
    inputs = policy.kept

    # Gap elimination: distances are real (haversine from the emergency's
    # coordinates) wherever both sides have coordinates, so NEAREST ranks
    # by true distance; unknown distances rank last, never as "0 km".
    ranked = rank_candidates(inputs, strategy=strategy, urgency=em.urgency)
    by_ref = {c.ref: c for c in inputs}

    candidates = [
        EmergencyMatchResponder(
            responder_ref=rc.ref,
            responder_name=(meta[rc.ref][0] if is_manager else None),
            role=meta[rc.ref][1],
            facility_verified=meta[rc.ref][2],
            blood_type=rc.blood_type,
            is_available=rc.factors.available,
            has_accepted=rc.factors.has_accepted,
            distance_km=rc.factors.distance_km,
            city=by_ref[rc.ref].city,
            rank=rc.rank,
            factors=MatchFactorsOut(**rc.factors.__dict__),
            explanation=rc.explanation,
        )
        for rc in ranked
    ]
    return EmergencyMatchOut(
        emergency_id=em.id,
        recipient_blood_type=em.blood_type,
        compatible_donor_types=compatible,
        strategy=strategy,
        candidates=candidates,
        component=_component(em),
        distance_limit_km=policy.limit_km,
        excluded_beyond_limit=len(policy.excluded_refs),
        unknown_distance=policy.unknown_distance,
        distance_override_active=policy.override_active,
        emergency_has_coordinates=em.latitude is not None and em.longitude is not None,
    )


@router.get("/{emergency_id}/match-strategies", response_model=StrategyComparisonOut)
def emergency_match_strategies(
    emergency_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StrategyComparisonOut:
    """Research-mode strategy comparison for an emergency: the same real
    responder pool as `match-candidates`, ranked under all four Phase 9
    strategies side by side. Always anonymised, like the blood-request
    equivalent in `app.api.routes.matching`."""
    em = db.get(Emergency, emergency_id)
    if em is None or not _can_view(db, em, user):
        _deny_access(db, em, user, action="view_match_candidates")

    compatible = _compatible_for(em)
    inputs, _meta = _emergency_candidate_inputs(db, em, compatible)
    inputs = _distance_policy(db, em, inputs).kept

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
                for rc in rank_candidates(inputs, strategy=strategy, urgency=em.urgency)
            ],
        )
        for strategy in MatchStrategy
    ]
    return StrategyComparisonOut(recipient_blood_type=em.blood_type, strategies=strategies)


@router.get("/{emergency_id}/resource-pathways", response_model=ResourcePathwayOut)
def emergency_resource_pathways(
    emergency_id: uuid.UUID,
    origin_facility_id: uuid.UUID,
    max_results: int = Query(default=5, ge=1, le=50),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ResourcePathwayOut:
    """Real graph-backed (or honestly-labelled haversine-fallback) resource
    discovery scoped to a live emergency: from `origin_facility_id`, which
    other facilities currently hold stock of this emergency's blood type
    and how to reach them. This is the graph engine wired into the real
    emergency flow, not a disconnected demo -- it reuses `_can_view()` for
    visibility and requires the caller to actually be the origin facility
    (or an admin), same as every other hospital-scoped mutation in this API.

    Phase 16: max_results is now bounded (1-50) -- see the identical fix
    and rationale on GET /hospitals/me/resource-pathways.
    """
    em = db.get(Emergency, emergency_id)
    if em is None or not _can_view(db, em, user):
        _deny_access(db, em, user, action="view_resource_pathways")
    if em.blood_type is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This emergency has no blood type on record to search for",
        )
    if user.role not in (UserRole.HOSPITAL, UserRole.ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only hospitals or admins can query resource pathways",
        )

    origin = db.get(HospitalProfile, origin_facility_id)
    if origin is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Facility not found")
    if user.role == UserRole.HOSPITAL and origin.user_id != user.id:
        # A hospital querying another facility's resource pathways as if it
        # were its own -- a real cross-tenant attempt, always worth a record.
        audit_service.record(
            db, "hospital.facility_access_denied", actor_user_id=user.id,
            target_type="hospital", target_id=origin.id,
            meta={"attempted_action": "resource_pathways", "emergency_id": str(em.id)},
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not your facility"
        )

    result = graph_service.find_resource_pathways(
        db, blood_type=em.blood_type, origin_facility_id=origin.id, max_results=max_results,
        component=_component(em), units_needed=em.units_needed or 1,
        urgency=em.urgency, emergency_id=em.id,
    )
    db.add(GraphPathwayLog(
        requested_by_user_id=user.id,
        emergency_id=em.id,
        origin_facility_id=origin.id,
        blood_type=em.blood_type,
        pathway_source=result.source,
        best_distance_km=result.best_distance_km,
        candidate_count=len(result.candidates),
        disrupted_routes_excluded=result.disrupted_routes_excluded,
        graph_available=result.graph_available,
    ))
    db.commit()
    return ResourcePathwayOut(
        blood_type=em.blood_type,
        origin_facility_id=origin.id,
        pathway_source=result.source,
        graph_available=result.graph_available,
        disrupted_routes_excluded=result.disrupted_routes_excluded,
        degraded_reason=result.degraded_reason,
        candidates=[PathwayCandidateOut(**vars(c)) for c in result.candidates],
        component=_component(em).value,
        units_needed=em.units_needed or 1,
    )


@router.post("/{emergency_id}/pathway", response_model=EmergencyPathwayOut)
def select_emergency_pathway(
    emergency_id: uuid.UUID,
    payload: EmergencyPathwaySelectIn,
    _: None = Depends(verify_origin),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EmergencyPathwayOut:
    """Plan (or re-plan) this emergency's delivery pathway and store it as a
    new plan round: one ACTIVE primary plus up to PATHWAY_MAX_BACKUPS BACKUP
    pathways, each with its complete route (every facility and transport
    route, in order). Earlier live pathways are SUPERSEDED, never deleted.

    `origin_facility_id` is the facility the blood must reach (the
    requesting hospital). A hospital may only plan for its own facility; an
    admin may omit it (target facility, else nearest verified hospital).
    """
    em = db.get(Emergency, emergency_id)
    if em is None or not _can_view(db, em, user):
        _deny_access(db, em, user, action="select_pathway")
    if user.role not in (UserRole.HOSPITAL, UserRole.ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only hospitals or admins can select a resource pathway",
        )
    destination_id = payload.origin_facility_id
    if destination_id is None and user.role == UserRole.HOSPITAL:
        own = db.scalar(select(HospitalProfile).where(HospitalProfile.user_id == user.id))
        destination_id = own.id if own else None
        if destination_id is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="Your facility profile is not set up")
    if destination_id is not None:
        origin = db.get(HospitalProfile, destination_id)
        if origin is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail="Facility not found")
        if user.role == UserRole.HOSPITAL and origin.user_id != user.id:
            audit_service.record(
                db, "hospital.facility_access_denied", actor_user_id=user.id,
                target_type="hospital", target_id=origin.id,
                meta={"attempted_action": "select_pathway", "emergency_id": str(em.id)},
            )
            db.commit()
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="Not your facility")
    if em.status not in EMERGENCY_OPEN_STATUSES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="This emergency is no longer open")

    reason = "manual_reoptimization" if _has_active_pathway(db, em.id) else "initial_selection"
    try:
        pathway, backups, result = emergency_pathway_service.create_plan_round(
            db, em, requested_by_user_id=user.id, reason=reason,
            destination_facility_id=destination_id,
        )
    except PlanningError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    audit_service.record(
        db, "emergency.pathway_selected", actor_user_id=user.id,
        target_type="emergency", target_id=em.id,
        meta={"version": pathway.version, "plan_round": pathway.plan_round,
              "pathway_source": pathway.pathway_source.value,
              "feasibility": pathway.feasibility.value if pathway.feasibility else None,
              "backups": [b.version for b in backups], "strategy": result.strategy.name},
    )
    db.commit()
    db.refresh(pathway)
    return EmergencyPathwayOut.model_validate(pathway)


@router.get("/{emergency_id}/pathways", response_model=list[StoredPathwayOut])
def list_emergency_pathways(
    emergency_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[StoredPathwayOut]:
    """Every stored pathway of this emergency -- the ACTIVE primary, its
    BACKUPs and all history -- each with its legs and ordered route steps
    (and each route's status at planning time and now)."""
    em = db.get(Emergency, emergency_id)
    if em is None or not _can_view(db, em, user):
        _deny_access(db, em, user, action="view_pathways")
    rows = db.scalars(select(EmergencyPathway).where(
        EmergencyPathway.emergency_id == em.id).order_by(EmergencyPathway.version)).all()
    return [stored_pathway_out(db, p) for p in rows]


def _require_destination_or_admin(db: Session, em: Emergency, user: User,
                                  pathway: EmergencyPathway, *, action: str) -> None:
    if user.role == UserRole.ADMIN:
        return
    dest = db.get(HospitalProfile, pathway.origin_facility_id)
    if user.role != UserRole.HOSPITAL or dest is None or dest.user_id != user.id:
        audit_service.record(
            db, "hospital.facility_access_denied", actor_user_id=user.id,
            target_type="emergency", target_id=em.id, meta={"attempted_action": action},
        )
        db.commit()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Only the destination facility or an admin can do this")


def _active_pathway_or_409(db: Session, em: Emergency) -> EmergencyPathway:
    p = db.scalar(select(EmergencyPathway).where(
        EmergencyPathway.emergency_id == em.id,
        EmergencyPathway.status == EmergencyPathwayStatus.ACTIVE))
    if p is None or p.selected_facility_id is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="This emergency has no usable active pathway")
    return p


@router.post("/{emergency_id}/pathway/reserve", response_model=StoredPathwayOut)
def reserve_emergency_pathway(
    emergency_id: uuid.UUID,
    _: None = Depends(verify_origin),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StoredPathwayOut:
    """Reserve, at every source facility of the ACTIVE pathway, exactly the
    units it plans to take -- only units still in date when that leg's
    delivery arrives. All-or-nothing: if any leg cannot be reserved,
    nothing is (409)."""
    em = emergency_lifecycle.get_for_update(db, emergency_id)
    if em is None or not _can_view(db, em, user):
        _deny_access(db, em, user, action="reserve_pathway")
    if em.status not in EMERGENCY_OPEN_STATUSES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="This emergency is no longer open")
    pathway = _active_pathway_or_409(db, em)
    _require_destination_or_admin(db, em, user, pathway, action="reserve_pathway")
    problems = emergency_pathway_service.validate_pathway(db, em, pathway)
    if problems:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="The active pathway is no longer valid: " + "; ".join(problems))
    try:
        emergency_pathway_service.reserve_pathway(db, em, pathway, actor_user_id=user.id)
    except InventoryActionError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    db.commit()
    return stored_pathway_out(db, pathway)


@router.post("/{emergency_id}/pathway/handoff", response_model=StoredPathwayOut)
def hand_off_emergency_pathway(
    emergency_id: uuid.UUID,
    _: None = Depends(verify_origin),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StoredPathwayOut:
    """Audit C21 (2026-10-31): turn the ACTIVE, reserved pathway into one
    shipment per leg in a single transaction. The reserved units move to the
    shipments WITHOUT being released, so nothing else can take them in
    between; each shipment is linked to this emergency and pathway.
    Idempotent: a second call returns the same shipments. All-or-nothing:
    if any leg's units are no longer usable at arrival, nothing changes (409)."""
    from app.services import pathway_handoff_service

    em = emergency_lifecycle.get_for_update(db, emergency_id)
    if em is None or not _can_view(db, em, user):
        _deny_access(db, em, user, action="handoff_pathway")
    if em.status not in EMERGENCY_OPEN_STATUSES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="This emergency is no longer open")
    pathway = _active_pathway_or_409(db, em)
    _require_destination_or_admin(db, em, user, pathway, action="handoff_pathway")
    try:
        pathway_handoff_service.hand_off(db, em, pathway, actor_user_id=user.id)
    except (pathway_handoff_service.HandoffError, InventoryActionError) as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    db.commit()
    return stored_pathway_out(db, pathway)


@router.post("/{emergency_id}/pathway/release", response_model=StoredPathwayOut)
def release_emergency_pathway(
    emergency_id: uuid.UUID,
    payload: EmergencyActionIn,
    _: None = Depends(verify_origin),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StoredPathwayOut:
    em = emergency_lifecycle.get_for_update(db, emergency_id)
    if em is None or not _can_view(db, em, user):
        _deny_access(db, em, user, action="release_pathway")
    pathway = _active_pathway_or_409(db, em)
    _require_destination_or_admin(db, em, user, pathway, action="release_pathway")
    released = emergency_pathway_service.release_pathway_reservations(
        db, pathway, actor_user_id=user.id, reason=payload.note or "released by user")
    if not released:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="The active pathway holds no reservations")
    _event(db, em, user.id, "pathway_released", payload.note)
    audit_service.record(db, "emergency.pathway_released", actor_user_id=user.id,
                         target_type="emergency", target_id=em.id,
                         meta={"pathway_version": pathway.version})
    db.commit()
    return stored_pathway_out(db, pathway)


@router.post("/{emergency_id}/overrides", response_model=EmergencyOverrideOut,
             status_code=status.HTTP_201_CREATED)
def create_emergency_override(
    emergency_id: uuid.UUID,
    payload: EmergencyOverrideIn,
    _: None = Depends(verify_origin),
    user: User = Depends(require_roles(UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> EmergencyOverride:
    """ADMIN-only, audited policy override for one emergency:
    DONOR_DISTANCE lifts MAX_DONOR_DISTANCE_KM for its donor ranking;
    UNVERIFIED_FACILITY lets its pathway source from unverified facilities.
    A reason is mandatory; the override is visible on the emergency."""
    em = emergency_lifecycle.get_for_update(db, emergency_id)
    if em is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    kind = EmergencyOverrideKind(payload.kind)
    if emergency_pathway_service.active_override(db, em.id, kind):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="This override is already active")
    ov = EmergencyOverride(emergency_id=em.id, kind=kind, reason=payload.reason,
                           created_by_user_id=user.id)
    db.add(ov)
    db.flush()
    _event(db, em, user.id, "override_granted", f"{kind.value}: {payload.reason}")
    audit_service.record(db, "emergency.override_granted", actor_user_id=user.id,
                         target_type="emergency", target_id=em.id,
                         meta={"kind": kind.value, "reason": payload.reason,
                               "override_id": str(ov.id)})
    db.commit()
    db.refresh(ov)
    return ov


@router.post("/{emergency_id}/overrides/{override_id}/revoke",
             response_model=EmergencyOverrideOut)
def revoke_emergency_override(
    emergency_id: uuid.UUID,
    override_id: uuid.UUID,
    _: None = Depends(verify_origin),
    user: User = Depends(require_roles(UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> EmergencyOverride:
    ov = db.get(EmergencyOverride, override_id, with_for_update=True)
    if ov is None or ov.emergency_id != emergency_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if ov.revoked_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Already revoked")
    ov.revoked_at = utcnow()
    ov.revoked_by_user_id = user.id
    em = db.get(Emergency, emergency_id)
    _event(db, em, user.id, "override_revoked", ov.kind.value)
    db.flush()
    if ov.kind == EmergencyOverrideKind.UNVERIFIED_FACILITY:
        # Without the override, a live plan sourcing from an unverified
        # facility is no longer acceptable: re-validate and fail over.
        for p in db.scalars(select(EmergencyPathway).where(
                EmergencyPathway.emergency_id == emergency_id,
                EmergencyPathway.status == EmergencyPathwayStatus.BACKUP)).all():
            problems = emergency_pathway_service.validate_pathway(db, em, p)
            if problems:
                p.status = EmergencyPathwayStatus.INVALIDATED
                p.invalidated_at = utcnow()
                p.invalidated_reason = "; ".join(problems)[:500]
        active = db.scalar(select(EmergencyPathway).where(
            EmergencyPathway.emergency_id == emergency_id,
            EmergencyPathway.status == EmergencyPathwayStatus.ACTIVE))
        if (active is not None and active.selected_facility_id is not None
                and emergency_pathway_service.validate_pathway(db, em, active)):
            emergency_pathway_service.fail_over(
                db, em, active, reason="unverified-facility override revoked",
                actor_user_id=user.id)
    audit_service.record(db, "emergency.override_revoked", actor_user_id=user.id,
                         target_type="emergency", target_id=emergency_id,
                         meta={"kind": ov.kind.value, "override_id": str(ov.id)})
    db.commit()
    db.refresh(ov)
    return ov


def _has_active_pathway(db: Session, emergency_id: uuid.UUID) -> bool:
    return db.scalar(
        select(EmergencyPathway.id).where(
            EmergencyPathway.emergency_id == emergency_id,
            EmergencyPathway.status == EmergencyPathwayStatus.ACTIVE,
        )
    ) is not None


# --------------------------------------------------------------------------- #
# Creator actions: cancel / resolve / assistance-found
# --------------------------------------------------------------------------- #
def _can_manage(db: Session, em: Emergency, user: User) -> bool:
    """Who may cancel/resolve this emergency: whoever raised it, the patient
    it's for, or a family member with an ACCEPTED link to that patient --
    mirrors blood_requests._can_manage so a family member can finish
    managing an emergency the patient started themselves, not only ones
    they personally raised.
    """
    if user.id == em.created_by_user_id:
        return True
    if em.patient_user_id and user.id == em.patient_user_id:
        return True
    return bool(
        em.patient_user_id
        and user.role == UserRole.FAMILY
        and has_accepted_link(db, user.id, em.patient_user_id)
    )


def _load_owned(db: Session, emergency_id: uuid.UUID, user: User, *, action: str) -> Emergency:
    """Same authorization as before, but now fetches through
    `emergency_lifecycle.get_for_update` (a real `SELECT ... FOR UPDATE`)
    so the caller holds a row lock for the rest of this request -- closing
    the Phase 12 concurrency gap where e.g. a simultaneous cancel and
    resolve (or two managers both cancelling at once) could previously both
    read the same pre-mutation status and both "succeed"."""
    em = emergency_lifecycle.get_for_update(db, emergency_id)
    if em is None or not _can_manage(db, em, user):
        _deny_access(db, em, user, action=action)
    return emergency_lifecycle.flag_deadline_exceeded_if_needed(db, em)


@router.post("/{emergency_id}/cancel", response_model=EmergencyOut)
def cancel_emergency(
    emergency_id: uuid.UUID,
    payload: EmergencyActionIn,
    _: None = Depends(verify_origin),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EmergencyOut:
    em = _load_owned(db, emergency_id, user, action="cancel")
    try:
        emergency_lifecycle.transition(
            db, em, EmergencyStatus.CANCELLED, actor_user_id=user.id, reason=payload.note,
            event_type="cancelled", event_note=payload.note,
        )
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    released = _release_all_reservations(
        db, em, actor_user_id=user.id, reason="Emergency cancelled",
    )
    _notify_accepted_responders(
        db, em, type="emergency.cancelled",
        title="An emergency you responded to was cancelled",
        body=payload.note or "The requester cancelled this emergency.",
    )
    if released:
        audit_service.record(db, "emergency.reservations_released", actor_user_id=user.id,
                             target_type="emergency", target_id=em.id,
                             meta={"releases": released})
    db.commit()
    db.refresh(em)
    return _to_out(db, em)


@router.post("/{emergency_id}/resolve", response_model=EmergencyOut)
def resolve_emergency(
    emergency_id: uuid.UUID,
    payload: EmergencyActionIn,
    _: None = Depends(verify_origin),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EmergencyOut:
    em = _load_owned(db, emergency_id, user, action="resolve")
    try:
        emergency_lifecycle.transition(
            db, em, EmergencyStatus.RESOLVED, actor_user_id=user.id, reason=payload.note,
            event_type="resolved", event_note=payload.note,
        )
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    # Any reservation the emergency itself still holds (never allocated by
    # the hospital's own dispatch flow) is released now that coordination
    # is over -- a resolved emergency must not leave a stale earmark
    # sitting against real inventory forever.
    released = _release_all_reservations(
        db, em, actor_user_id=user.id, reason="Emergency resolved",
    )
    _notify_accepted_responders(
        db, em, type="emergency.resolved",
        title="Thank you — an emergency you helped with is resolved",
        body="The requester marked this emergency resolved.",
    )
    if released:
        audit_service.record(db, "emergency.reservations_released", actor_user_id=user.id,
                             target_type="emergency", target_id=em.id,
                             meta={"releases": released})
    db.commit()
    db.refresh(em)
    return _to_out(db, em)


@router.post("/{emergency_id}/assistance-found", response_model=EmergencyOut)
def assistance_found(
    emergency_id: uuid.UUID,
    payload: EmergencyActionIn,
    _: None = Depends(verify_origin),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EmergencyOut:
    em = emergency_lifecycle.get_for_update(db, emergency_id)
    if em is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    allowed = _can_manage(db, em, user)
    if not allowed and user.role == UserRole.HOSPITAL:
        allowed = db.scalar(select(EmergencyResponse).where(
            EmergencyResponse.emergency_id == em.id,
            EmergencyResponse.responder_user_id == user.id,
            EmergencyResponse.status == DonorResponseStatus.ACCEPTED,
        )) is not None
    if not allowed:
        _deny_access(db, em, user, action="assistance_found")
    em = emergency_lifecycle.flag_deadline_exceeded_if_needed(db, em)

    try:
        emergency_lifecycle.transition(
            db, em, EmergencyStatus.ASSISTANCE_FOUND, actor_user_id=user.id,
            reason=payload.note, event_type="assistance_found", event_note=payload.note,
        )
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    notification_service.notify(
        db, em.created_by_user_id, type="emergency.assistance_found",
        title="Assistance secured for your emergency",
        body=payload.note or f"{user.full_name} confirmed help is on the way.",
        data={"emergency_id": str(em.id)},
    )
    _notify_accepted_responders(
        db, em, type="emergency.assistance_found",
        title="Assistance confirmed for an emergency you joined",
        body="Coordination is underway. Await further instructions.",
        exclude=user.id,
    )
    db.commit()
    db.refresh(em)
    return _to_out(db, em)


# --------------------------------------------------------------------------- #
# Responder actions: acknowledge / respond
# --------------------------------------------------------------------------- #
@router.post("/{emergency_id}/acknowledge", response_model=EmergencyOut)
def acknowledge_emergency(
    emergency_id: uuid.UUID,
    payload: EmergencyActionIn,
    _: None = Depends(verify_origin),
    user: User = Depends(require_roles(UserRole.DONOR, UserRole.HOSPITAL, UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> EmergencyOut:
    em = emergency_lifecycle.get_for_update(db, emergency_id)
    if em is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    em = emergency_lifecycle.flag_deadline_exceeded_if_needed(db, em)
    # Endpoint-specific rule, narrower than the transition table: this
    # donor/hospital/admin "acknowledge" action only ever applies to a
    # brand-new ACTIVE emergency. MATCHING -> ACKNOWLEDGED is a real, legal
    # transition (the "all responders withdrew, reopen" case), but it is
    # only ever triggered internally by respond_to_emergency below, never
    # through this endpoint -- same "table is state-machine truth, endpoint
    # decides who/when" split as app.core.request_lifecycle.
    if em.status != EmergencyStatus.ACTIVE:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=f"Emergency is {em.status.value.lower()}")
    try:
        emergency_lifecycle.transition(
            db, em, EmergencyStatus.ACKNOWLEDGED, actor_user_id=user.id,
            reason=payload.note, event_type="acknowledged", event_note=payload.note,
        )
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    notification_service.notify(
        db, em.created_by_user_id, type="emergency.acknowledged",
        title="Your emergency was acknowledged",
        body=f"{user.full_name} ({user.role.value.title()}) is responding.",
        data={"emergency_id": str(em.id)},
    )
    db.commit()
    db.refresh(em)
    return _to_out(db, em)


def _reserve_compatible(
    db: Session, hp: HospitalProfile, em: Emergency, *, actor_user_id: uuid.UUID,
) -> list[tuple[BloodType, int]]:
    """Reserve `em.units_needed` units of the emergency's COMPONENT at `hp`,
    using compatible blood types in the central preference order (identical
    type first), counting only AVAILABLE units that are in date right now.
    All-or-nothing (SAVEPOINT): either the full quantity is reserved or
    nothing is, and the error says how many compatible units exist."""
    from app.models.blood_unit import BloodUnit
    from app.services.inventory_service import usable_until_cond

    component = _component(em)
    # Touch every inventory row first: runs the expiry sweep and backfills
    # any aggregate-only stock into real unit rows (these commit on their
    # own), so the counts below are per-unit truth and the SAVEPOINT below
    # never contains one of those commits.
    inventory_service.sweep_all_expired(db, hp.id)
    now = utcnow()
    order = compatibility.compatible_source_types(em.blood_type, component)
    counts = dict(db.execute(
        select(BloodUnit.blood_type, func.count()).where(
            BloodUnit.hospital_id == hp.id, BloodUnit.component == component,
            BloodUnit.state == BloodUnitState.AVAILABLE, BloodUnit.blood_type.in_(order),
            usable_until_cond(now),
        ).group_by(BloodUnit.blood_type)
    ).all())
    needed = em.units_needed or 1
    plan: list[tuple[BloodType, int]] = []
    for bt in order:
        if needed <= 0:
            break
        take = min(counts.get(bt, 0), needed)
        if take > 0:
            plan.append((bt, take))
            needed -= take
    if needed > 0:
        have = sum(n for _, n in plan)
        raise InventoryActionError(
            f"Only {have} compatible usable {component.value} unit(s) available for a "
            f"{em.blood_type.value} patient; {em.units_needed} needed"
        )
    savepoint = db.begin_nested()
    try:
        for bt, n in plan:
            inventory_service.reserve_stock(
                db, hp.id, bt, n, actor_user_id=actor_user_id, emergency_id=em.id,
                component=component, usable_until=now,
                note="Reserved for emergency response",
            )
    except InventoryActionError:
        savepoint.rollback()
        raise
    savepoint.commit()
    return plan


@router.post("/{emergency_id}/respond", response_model=EmergencyResponderOut)
def respond_to_emergency(
    emergency_id: uuid.UUID,
    payload: EmergencyRespondIn,
    _: None = Depends(verify_origin),
    user: User = Depends(require_roles(*_RESPONDER_ROLES)),
    db: Session = Depends(get_db),
) -> EmergencyResponderOut:
    em = emergency_lifecycle.get_for_update(db, emergency_id)
    if em is None or em.status not in EMERGENCY_OPEN_STATUSES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Emergency is not open")
    em = emergency_lifecycle.flag_deadline_exceeded_if_needed(db, em)

    accepting = payload.action == DonorResponseStatus.ACCEPTED
    if accepting and user.role == UserRole.DONOR:
        dp = get_or_create_donor_profile(db, user)
        if dp.blood_type is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="Set your blood type in your donor profile first")
        if not dp.is_available:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="Mark yourself available before responding")
        if not _donor_compatible(dp.blood_type, em):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="Your blood type is not compatible with this emergency")
        # Phase 31 fix: this eligibility gate already exists on the
        # structurally-identical blood_requests.respond_to_request path
        # (the same 56-day donation-interval check) but was missing here --
        # a real inconsistency, not a deliberate difference: an emergency
        # SOS accept is exactly as much a donation commitment as a blood
        # request accept, so it must be gated the same way.
        if not is_eligible(dp.last_donation_date):
            due = eligible_on(dp.last_donation_date)
            when = due.isoformat() if due else "your donation interval passes"
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"You are not eligible to donate again until {when}",
            )
        # Phase 31: same cross-request/cross-emergency double-booking guard
        # as blood_requests.respond_to_request -- a physical donor can only
        # be in one place. Does not apply to a HOSPITAL responder (an
        # institution, not a single body) -- see donor_commitment's module
        # docstring.
        conflict = donor_commitment.find_conflicting_commitment(
            db, user.id, exclude_emergency_id=em.id,
        )
        if conflict is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"You cannot accept this emergency: {conflict}. "
                       "Withdraw first if you need to switch.",
            )

    if accepting and user.role == UserRole.HOSPITAL:
        # Gap elimination #7: only a VERIFIED facility can commit to an
        # emergency (an audited UNVERIFIED_FACILITY override aside).
        hp_check = db.scalar(select(HospitalProfile).where(HospitalProfile.user_id == user.id))
        if hp_check is None or not (
            hp_check.verified or emergency_pathway_service.active_override(
                db, em.id, EmergencyOverrideKind.UNVERIFIED_FACILITY)
        ):
            audit_service.record(
                db, "emergency.unverified_facility_refused", actor_user_id=user.id,
                target_type="emergency", target_id=em.id,
                meta={"facility_id": str(hp_check.id) if hp_check else None},
            )
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only a verified facility can respond to an emergency. "
                       "Complete facility verification first.",
            )

    existing = db.scalar(select(EmergencyResponse).where(
        EmergencyResponse.emergency_id == em.id,
        EmergencyResponse.responder_user_id == user.id,
    ))
    was_accepted = existing is not None and existing.status == DonorResponseStatus.ACCEPTED
    if existing is None:
        # Concurrency-safe first response -- see the identical comment in
        # blood_requests.respond_to_request for why this needs a SAVEPOINT
        # rather than a plain insert.
        savepoint = db.begin_nested()
        try:
            existing = EmergencyResponse(
                emergency_id=em.id, responder_user_id=user.id, responder_role=user.role,
                status=payload.action, note=payload.note,
            )
            db.add(existing)
            db.flush()
            savepoint.commit()
        except IntegrityError:
            savepoint.rollback()
            existing = db.scalar(select(EmergencyResponse).where(
                EmergencyResponse.emergency_id == em.id,
                EmergencyResponse.responder_user_id == user.id,
            ))
            if existing is None:
                raise  # pragma: no cover - constraint violation implies a row exists
            was_accepted = existing.status == DonorResponseStatus.ACCEPTED
            existing.status = payload.action
            existing.note = payload.note
            db.flush()
    else:
        existing.status = payload.action
        existing.note = payload.note
        db.flush()

    withdrawing = was_accepted and not accepting
    if accepting:
        _event(db, em, user.id, "responder_accepted", payload.note)
    elif withdrawing:
        _event(db, em, user.id, "responder_withdrew", payload.note)
    elif payload.action == DonorResponseStatus.NOT_NOW:
        # Distinct from "declined" -- see the identical comment in
        # blood_requests.respond_to_request.
        _event(db, em, user.id, "responder_not_now", payload.note)
    else:
        _event(db, em, user.id, "responder_declined")

    if user.role == UserRole.DONOR:
        # Confidence shortlist bookkeeping (see blood_requests.respond_to_request).
        from app.services import donor_shortlist_service

        donor_shortlist_service.record_response(
            db, ShortlistSubject.EMERGENCY, em.id, user.id, payload.action,
            was_accepted=was_accepted,
        )
        if not accepting:
            donor_shortlist_service.maybe_escalate(
                db, donor_shortlist_service.load_subject(db, ShortlistSubject.EMERGENCY, em.id))

    if accepting and em.status in (EmergencyStatus.ACTIVE, EmergencyStatus.ACKNOWLEDGED):
        emergency_lifecycle.transition(
            db, em, EmergencyStatus.MATCHING, actor_user_id=user.id,
            reason="A responder accepted", event_type="matching_started",
            event_note=f"{user.full_name} accepted",
        )
    if (
        withdrawing
        and em.status == EmergencyStatus.MATCHING
        and _accepted_count(db, em.id) == 0
    ):
        emergency_lifecycle.transition(
            db, em, EmergencyStatus.ACKNOWLEDGED, actor_user_id=None,
            reason="All responders withdrew", event_type="reopened",
            event_note="All responders withdrew",
        )

    # ---- Inventory reservation (Phase 12) -- only a HOSPITAL responder has
    # inventory to offer, and only when the emergency actually specifies
    # what's needed. Never blocks the accept/withdraw itself -- a hospital
    # can still coordinate even if the exact recorded stock falls short --
    # but the outcome is always reported honestly via `inventory_note`,
    # never silently assumed to have succeeded.
    inventory_note: str | None = None
    if user.role == UserRole.HOSPITAL and em.blood_type is not None and em.units_needed:
        hp = db.scalar(select(HospitalProfile).where(HospitalProfile.user_id == user.id))
        if hp is not None:
            if accepting and not was_accepted:
                try:
                    taken = _reserve_compatible(db, hp, em, actor_user_id=user.id)
                    inventory_note = (
                        "Reserved " + " + ".join(f"{n} {bt.value}" for bt, n in taken)
                        + f" {_component(em).value.replace('_', ' ').lower()} unit(s) "
                        "at your facility"
                    )
                except InventoryActionError as exc:
                    inventory_note = f"Could not reserve stock: {exc}"
                    _event(db, em, user.id, "inventory_reservation_failed", str(exc))
            elif withdrawing:
                for hospital_id, blood_type, component, units in _outstanding_reservations(
                        db, em.id):
                    if hospital_id == hp.id:
                        try:
                            inventory_service.release_reservation(
                                db, hospital_id, blood_type, units,
                                actor_user_id=user.id, emergency_id=em.id,
                                note="Released: responder withdrew", component=component,
                            )
                            inventory_note = f"Released {units} {blood_type.value} unit(s)"
                        except InventoryActionError as exc:  # pragma: no cover - defensive
                            inventory_note = f"Could not release reservation: {exc}"

    if accepting:
        notification_service.notify(
            db, em.created_by_user_id, type="emergency.responder_accepted",
            title="A responder is on the way",
            body=f"{user.full_name} ({user.role.value.title()}) can help now.",
            data={"emergency_id": str(em.id), "responder_user_id": str(user.id)},
        )
        notification_service.notify(
            db, user.id, type="emergency.you_responded",
            title="You joined an emergency response",
            body="The requester has been notified you are helping.",
            data={"emergency_id": str(em.id)},
        )
    elif withdrawing:
        notification_service.notify(
            db, em.created_by_user_id, type="emergency.responder_withdrew",
            title="A responder withdrew from your emergency",
            body=f"{user.full_name} can no longer help. Matching continues.",
            data={"emergency_id": str(em.id), "responder_user_id": str(user.id)},
        )
    audit_service.record(db, "emergency.responded", actor_user_id=user.id,
                         target_type="emergency", target_id=em.id,
                         meta={
                             "action": payload.action.value, "withdrew": withdrawing,
                             "inventory_note": inventory_note,
                         })
    db.commit()
    db.refresh(existing)
    out = EmergencyResponderOut.model_validate(existing)
    out.inventory_note = inventory_note
    return out
