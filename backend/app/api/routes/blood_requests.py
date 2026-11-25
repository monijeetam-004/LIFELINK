from __future__ import annotations

import hashlib
import json
import uuid
from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, require_roles, verify_origin
from app.core.blood_compatibility import can_donate_to
from app.core.config import settings
from app.core.eligibility import eligible_on, is_eligible
from app.core.ratelimit import enforce_rate_limit
from app.core.request_lifecycle import OPEN_STATUSES as _OPEN_STATUSES
from app.core.request_lifecycle import InvalidTransitionError, allowed_transitions
from app.core.time import utcnow
from app.models.blood_request import BloodRequest, BloodRequestEvent, DonorResponse
from app.models.donation import Donation
from app.models.enums import (
    BloodRequestStatus,
    DonorResponseStatus,
    FamilyLinkStatus,
    ShortlistSubject,
)
from app.models.family import FamilyLink
from app.models.hospital import HospitalProfile
from app.models.profile import DonorProfile
from app.models.user import User, UserRole
from app.schemas.blood_request import (
    BloodRequestCreate,
    BloodRequestDetailOut,
    BloodRequestEventOut,
    BloodRequestOut,
    CancelIn,
    DonorResponseOut,
    RespondIn,
)
from app.services import (
    audit_service,
    blood_request_lifecycle,
    donor_commitment,
    donor_shortlist_service,
    notification_service,
)
from app.services.domain_service import (
    get_or_create_donor_profile,
    has_accepted_link,
    resolve_beneficiary,
)

router = APIRouter(prefix="/blood-requests", tags=["blood-requests"])


def _add_event(db: Session, req: BloodRequest, actor_id: uuid.UUID | None, kind: str,
               note: str | None = None) -> None:
    db.add(BloodRequestEvent(request_id=req.id, actor_user_id=actor_id, event_type=kind, note=note))


def _idempotency_payload_hash(payload: BloodRequestCreate) -> str:
    """SHA-256 of the fields that define what this request actually asks
    for. Deliberately excludes the idempotency_key itself. Used only to
    detect a same-key/different-payload replay (Phase 19) -- the emergency
    idempotency precedent this mirrors does not do this at all, so this is
    a deliberate, documented extension rather than an assumption; see
    LIFELINK_PHASE_19_IDEMPOTENCY_AUDIT.md."""
    canonical = {
        "blood_type": payload.blood_type.value,
        "units_needed": payload.units_needed,
        "urgency": payload.urgency.value,
        "hospital_name": payload.hospital_name,
        "city": payload.city,
        "note": payload.note,
        "needed_by": payload.needed_by.isoformat() if payload.needed_by else None,
        "patient_email": payload.patient_email,
    }
    blob = json.dumps(canonical, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _accepted_count(db: Session, request_id: uuid.UUID) -> int:
    return int(db.scalar(
        select(func.count()).select_from(DonorResponse).where(
            DonorResponse.request_id == request_id,
            DonorResponse.status == DonorResponseStatus.ACCEPTED,
        )
    ) or 0)


def _accepted_counts(db: Session, request_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    """Hardening (measured N+1): one grouped query for a whole list instead
    of one count per request."""
    if not request_ids:
        return {}
    return {
        rid: int(n)
        for rid, n in db.execute(
            select(DonorResponse.request_id, func.count())
            .where(
                DonorResponse.request_id.in_(request_ids),
                DonorResponse.status == DonorResponseStatus.ACCEPTED,
            )
            .group_by(DonorResponse.request_id)
        ).all()
    }


def _to_out(
    db: Session, req: BloodRequest, *, accepted_count: int | None = None
) -> BloodRequestOut:
    out = BloodRequestOut.model_validate(req)
    patient = db.get(User, req.patient_user_id)
    out.patient_name = patient.full_name if patient else None
    if req.requester_user_id == req.patient_user_id:
        out.requester_name = out.patient_name
        out.filed_by_family = False
    else:
        requester = db.get(User, req.requester_user_id)
        out.requester_name = requester.full_name if requester else None
        out.filed_by_family = True
    out.accepted_count = (
        _accepted_count(db, req.id) if accepted_count is None else accepted_count
    )
    out.allowed_transitions = allowed_transitions(req.status)
    return out


def _hospital_can_see(db: Session, req: BloodRequest, user: User) -> bool:
    hp = db.scalar(select(HospitalProfile).where(HospitalProfile.user_id == user.id))
    if hp is None:
        return False
    if hp.city and req.city and hp.city == req.city:
        return True
    if hp.name and req.hospital_name and hp.name == req.hospital_name:
        return True
    return False


def _can_view(db: Session, req: BloodRequest, user: User) -> bool:
    if user.id in (req.requester_user_id, req.patient_user_id):
        return True
    if user.role == UserRole.FAMILY and has_accepted_link(db, user.id, req.patient_user_id):
        return True
    if user.role == UserRole.DONOR:
        resp = db.scalar(select(DonorResponse).where(
            DonorResponse.request_id == req.id, DonorResponse.donor_user_id == user.id
        ))
        return resp is not None
    if user.role == UserRole.HOSPITAL:
        return _hospital_can_see(db, req, user)
    return False


def _can_manage(db: Session, req: BloodRequest, user: User) -> bool:
    if user.id in (req.requester_user_id, req.patient_user_id):
        return True
    return user.role == UserRole.FAMILY and has_accepted_link(db, user.id, req.patient_user_id)


def _deny_access(db: Session, req: BloodRequest | None, user: User, *, action: str) -> NoReturn:
    """Raise the standard "not found" response for a view/manage attempt that
    failed object-level authorization. When the request genuinely exists,
    this is a real cross-tenant access attempt (someone else's request id,
    guessed or enumerated) rather than a bad id, so it's audited -- the same
    distinction auth_service draws between "no such account" and "wrong
    password for a real one." A non-existent id is not itself suspicious and
    is not audited, to keep this signal meaningful.
    """
    if req is not None:
        audit_service.record(
            db, "blood_request.access_denied", actor_user_id=user.id,
            target_type="blood_request", target_id=req.id,
            meta={"actor_role": user.role.value, "attempted_action": action},
        )
        db.commit()
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")


def _existing_by_idempotency_key(
    db: Session, user: User, key: str
) -> BloodRequest | None:
    return db.scalar(
        select(BloodRequest).where(
            BloodRequest.requester_user_id == user.id,
            BloodRequest.idempotency_key == key,
        )
    )


@router.post("", response_model=BloodRequestOut, status_code=status.HTTP_201_CREATED)
def create_request(
    payload: BloodRequestCreate,
    response: Response,
    _: None = Depends(verify_origin),
    user: User = Depends(require_roles(UserRole.PATIENT, UserRole.FAMILY, UserRole.HOSPITAL)),
    db: Session = Depends(get_db),
) -> BloodRequestOut:
    # Phase 19 idempotent replay: mirrors the Phase 12 emergency pattern
    # (create_emergency) -- the exact same (requester, idempotency_key)
    # pair returns the existing request instead of creating a second one,
    # checked up front so a replay never re-evaluates beneficiary
    # resolution or hospital-profile lookups. Unlike the emergency
    # precedent, a same-key/different-payload replay is explicitly
    # rejected (409) rather than silently served -- see
    # LIFELINK_PHASE_19_IDEMPOTENCY_AUDIT.md for why this deliberately
    # goes further than the existing emergency behavior.
    payload_hash = _idempotency_payload_hash(payload)
    if payload.idempotency_key is not None:
        existing = _existing_by_idempotency_key(db, user, payload.idempotency_key)
        if existing is not None:
            if existing.idempotency_payload_hash != payload_hash:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="This idempotency key was already used with a different request.",
                )
            response.status_code = status.HTTP_200_OK
            return _to_out(db, existing)

    # Phase 23: rate-limited only past the idempotency-replay/conflict checks
    # above, so retrying the exact same submission never consumes a request
    # against this limit -- only genuinely distinct request creations do.
    enforce_rate_limit(
        "blood_request_create:account", str(user.id), *settings.rl_blood_request_create_account
    )

    beneficiary = resolve_beneficiary(db, user, payload.patient_email)

    lat, lng, hospital_name, city = None, None, payload.hospital_name, payload.city
    if user.role == UserRole.HOSPITAL:
        hp = db.scalar(select(HospitalProfile).where(HospitalProfile.user_id == user.id))
        if hp is not None:
            lat, lng = hp.latitude, hp.longitude
            hospital_name = hospital_name or hp.name
            city = city or hp.city

    def _build_request() -> BloodRequest:
        return BloodRequest(
            requester_user_id=user.id,
            patient_user_id=beneficiary.id,
            blood_type=payload.blood_type,
            units_needed=payload.units_needed,
            urgency=payload.urgency,
            status=BloodRequestStatus.PENDING,
            hospital_name=hospital_name,
            city=city,
            note=payload.note,
            needed_by=payload.needed_by,
            latitude=lat,
            longitude=lng,
            idempotency_key=payload.idempotency_key,
            idempotency_payload_hash=payload_hash if payload.idempotency_key is not None else None,
        )

    if payload.idempotency_key is not None:
        # Race-safe insert: two near-simultaneous requests carrying the same
        # (requester, key) pair must not both create a row -- the second one
        # to reach the DB hits uq_blood_request_idempotency_key and replays
        # the first's result instead, exactly like create_emergency's own
        # SAVEPOINT-guarded race handling. db.begin_nested() is called
        # before `req` is added to the session: Session.begin_nested()
        # autoflushes any already-pending objects while establishing the
        # SAVEPOINT, so a genuine collision is caught inside this try block
        # rather than outside it.
        savepoint = db.begin_nested()
        try:
            req = _build_request()
            db.add(req)
            db.flush()
            savepoint.commit()
        except IntegrityError:
            savepoint.rollback()
            existing = _existing_by_idempotency_key(db, user, payload.idempotency_key)
            if existing is None:
                raise  # pragma: no cover - constraint violation implies a row exists
            if existing.idempotency_payload_hash != payload_hash:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="This idempotency key was already used with a different request.",
                ) from None
            response.status_code = status.HTTP_200_OK
            return _to_out(db, existing)
    else:
        req = _build_request()
        db.add(req)
        db.flush()
    _add_event(
        db, req, user.id, "created",
        f"{payload.units_needed} unit(s) {payload.blood_type.value}, {payload.urgency.value}",
    )

    # Audit D4 (2026-10-31): blood-request alerts now apply the same shared
    # distance policy as SOS alerts and ranking (app/core/distance_policy.py):
    # MAX_DONOR_DISTANCE_KM for measured distances, same-city for donors
    # without a shared location, and no alert at all for a request that has
    # neither coordinates nor a city (no silent nationwide broadcast).
    alert_stats: dict[str, int] = {}
    notified = 0
    if settings.donor_alert_mode == "shortlist":
        # Contact only the first ranked batch; more follow declines/timeouts.
        subject = donor_shortlist_service.load_subject(db, ShortlistSubject.BLOOD_REQUEST, req.id)
        notified = len(donor_shortlist_service.contact_next_batch(
            db, subject, actor_user_id=user.id))
    else:
        notified = notification_service.notify_matching_donors(
            db,
            recipient_blood_type=payload.blood_type,
            type="blood_request.new",
            title="A patient near you needs blood",
            body=f"{payload.blood_type.value} · {payload.urgency.value}"
                 + (f" · {payload.city}" if payload.city else ""),
            data={"request_id": str(req.id)},
            city=payload.city,
            urgency=payload.urgency,
            origin_lat=lat,
            origin_lng=lng,
            origin_city=city,
            max_distance_km=settings.max_donor_distance_km,
            stats=alert_stats,
        )
    if alert_stats.get("no_request_location"):
        _add_event(
            db, req, None, "donor_alerts_withheld",
            "No donor was alerted: the request has no city or coordinates, so the "
            f"{settings.max_donor_distance_km:g} km donor distance limit cannot be applied. "
            "Add a city to reach nearby donors.",
        )
    if beneficiary.id != user.id:
        notification_service.notify(
            db, beneficiary.id, type="blood_request.filed_for_you",
            title="A blood request was filed for you",
            body=f"{user.full_name} filed a {payload.blood_type.value} request on your behalf.",
            data={"request_id": str(req.id)},
        )
    if city:
        for (hosp_user_id,) in db.execute(
            select(HospitalProfile.user_id).where(HospitalProfile.city == city)
        ).all():
            if hosp_user_id != user.id:
                notification_service.notify(
                    db, hosp_user_id, type="blood_request.incoming",
                    title="Incoming blood request in your area",
                    body=f"{payload.blood_type.value} · {payload.urgency.value}",
                    data={"request_id": str(req.id)},
                )
    audit_service.record(db, "blood_request.created", actor_user_id=user.id,
                         target_type="blood_request", target_id=req.id,
                         meta={"notified_donors": notified, "donor_alert_skips": alert_stats})
    db.commit()
    db.refresh(req)
    return _to_out(db, req)


@router.get("", response_model=list[BloodRequestOut])
def list_my_requests(
    status_filter: BloodRequestStatus | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[BloodRequestOut]:
    visible_patient_ids: set[uuid.UUID] = {user.id}
    if user.role == UserRole.FAMILY:
        # A family member sees every request for a patient they have an
        # ACCEPTED link to -- not just the ones they personally filed -- so
        # the family dashboard reflects the patient's real status, including
        # requests the patient filed themselves from their own account.
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
    stmt = select(BloodRequest).where(
        or_(
            BloodRequest.requester_user_id == user.id,
            BloodRequest.patient_user_id.in_(visible_patient_ids),
        )
    )
    if status_filter is not None:
        stmt = stmt.where(BloodRequest.status == status_filter)
    stmt = stmt.order_by(BloodRequest.created_at.desc())
    rows = db.scalars(stmt).all()
    for r in rows:
        blood_request_lifecycle.expire_if_stale(db, r)
    counts = _accepted_counts(db, [r.id for r in rows])
    return [_to_out(db, r, accepted_count=counts.get(r.id, 0)) for r in rows]


@router.get("/{request_id}", response_model=BloodRequestDetailOut)
def get_request(
    request_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BloodRequestDetailOut:
    req = db.get(BloodRequest, request_id)
    if req is not None:
        blood_request_lifecycle.expire_if_stale(db, req)
    if req is None or not _can_view(db, req, user):
        _deny_access(db, req, user, action="view")

    base = _to_out(db, req)
    detail = BloodRequestDetailOut(**base.model_dump())

    events = db.scalars(
        select(BloodRequestEvent).where(BloodRequestEvent.request_id == req.id)
        .order_by(BloodRequestEvent.created_at.asc())
    ).all()
    detail.events = [BloodRequestEventOut.model_validate(e) for e in events]

    # Responses (donor identity only visible to request managers).
    if _can_manage(db, req, user):
        responses = db.scalars(
            select(DonorResponse).where(DonorResponse.request_id == req.id)
            .order_by(DonorResponse.created_at.asc())
        ).all()
        out_list = []
        for r in responses:
            ro = DonorResponseOut.model_validate(r)
            donor = db.get(User, r.donor_user_id)
            dp = db.scalar(select(DonorProfile).where(DonorProfile.user_id == r.donor_user_id))
            ro.donor_name = donor.full_name if donor else None
            ro.donor_blood_type = dp.blood_type if dp else None
            out_list.append(ro)
        detail.responses = out_list

    if user.role == UserRole.DONOR:
        mine = db.scalar(select(DonorResponse).where(
            DonorResponse.request_id == req.id, DonorResponse.donor_user_id == user.id
        ))
        detail.my_response = DonorResponseOut.model_validate(mine) if mine else None

    return detail


@router.post("/{request_id}/cancel", response_model=BloodRequestOut)
def cancel_request(
    request_id: uuid.UUID,
    payload: CancelIn,
    _: None = Depends(verify_origin),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BloodRequestOut:
    req = blood_request_lifecycle.get_for_update(db, request_id)
    if req is not None:
        blood_request_lifecycle.expire_if_stale(db, req)
    if req is None or not _can_manage(db, req, user):
        _deny_access(db, req, user, action="cancel")
    try:
        blood_request_lifecycle.transition(
            db, req, BloodRequestStatus.CANCELLED,
            actor_user_id=user.id, reason=payload.reason,
        )
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    for (donor_id,) in db.execute(
        select(DonorResponse.donor_user_id).where(
            DonorResponse.request_id == req.id,
            DonorResponse.status == DonorResponseStatus.ACCEPTED,
        )
    ).all():
        notification_service.notify(
            db, donor_id, type="blood_request.cancelled",
            title="A request you accepted was cancelled",
            body=payload.reason or "The requester cancelled this request.",
            data={"request_id": str(req.id)},
        )
    db.commit()
    db.refresh(req)
    return _to_out(db, req)


@router.post("/{request_id}/fulfill", response_model=BloodRequestOut)
def fulfill_request(
    request_id: uuid.UUID,
    _: None = Depends(verify_origin),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BloodRequestOut:
    req = blood_request_lifecycle.get_for_update(db, request_id)
    if req is not None:
        blood_request_lifecycle.expire_if_stale(db, req)
    if req is None or not _can_manage(db, req, user):
        _deny_access(db, req, user, action="fulfill")

    accepted = db.scalars(
        select(DonorResponse).where(
            DonorResponse.request_id == req.id,
            DonorResponse.status == DonorResponseStatus.ACCEPTED,
        )
    ).all()
    # Phase E honesty fix: mirrors hospitals.fulfill_from_hospital -- this
    # is the donor-matched completion path declaring the request met
    # through donor donations this system doesn't itemize per unit;
    # whatever `fulfilled_units` already was (possibly partially filled by
    # a hospital's prior /allocate calls), it is brought up to exactly
    # `units_needed` here so the single-source-of-truth invariant holds
    # for this path too.
    try:
        blood_request_lifecycle.transition(
            db, req, BloodRequestStatus.FULFILLED,
            actor_user_id=user.id,
            extra_meta={
                "donations_recorded": len(accepted),
                "units_needed": req.units_needed,
                "fulfilled_units_before": req.fulfilled_units,
            },
        )
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    req.fulfilled_units = req.units_needed

    for resp in accepted:
        db.add(Donation(
            donor_user_id=resp.donor_user_id,
            blood_request_id=req.id,
            units=1,
            donated_on=utcnow().date(),
            hospital_name=req.hospital_name,
            note="Recorded on request fulfilment",
        ))
        dp = db.scalar(select(DonorProfile).where(DonorProfile.user_id == resp.donor_user_id))
        if dp is not None:
            dp.last_donation_date = utcnow().date()
        notification_service.notify(
            db, resp.donor_user_id, type="blood_request.fulfilled",
            title="Thank you — a request you helped with is fulfilled",
            body="Your donation has been recorded in your history.",
            data={"request_id": str(req.id)},
        )
    db.commit()
    db.refresh(req)
    return _to_out(db, req)


@router.post("/{request_id}/respond", response_model=DonorResponseOut)
def respond_to_request(
    request_id: uuid.UUID,
    payload: RespondIn,
    _: None = Depends(verify_origin),
    user: User = Depends(require_roles(UserRole.DONOR)),
    db: Session = Depends(get_db),
) -> DonorResponseOut:
    req = blood_request_lifecycle.get_for_update(db, request_id)
    if req is not None:
        blood_request_lifecycle.expire_if_stale(db, req)
    if req is None or req.status not in _OPEN_STATUSES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not open")

    profile = get_or_create_donor_profile(db, user)
    if profile.blood_type is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Set your blood type in your donor profile first")
    if not can_donate_to(profile.blood_type, req.blood_type):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Your blood type is not compatible with this request")

    accepting = payload.action == DonorResponseStatus.ACCEPTED
    if accepting:
        if not profile.is_available:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Mark yourself available before accepting a request",
            )
        if not is_eligible(profile.last_donation_date):
            due = eligible_on(profile.last_donation_date)
            when = due.isoformat() if due else "your donation interval passes"
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"You are not eligible to donate again until {when}",
            )
        # Phase 31: a donor can only physically donate in one place -- block
        # accepting a second open request/emergency while already committed
        # to one (excludes this same request, so re-accepting/idempotent
        # replays of an existing ACCEPTED response are never blocked).
        conflict = donor_commitment.find_conflicting_commitment(
            db, user.id, exclude_request_id=req.id,
        )
        if conflict is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"You cannot accept this request: {conflict}. "
                       "Withdraw first if you need to switch.",
            )

    existing = db.scalar(select(DonorResponse).where(
        DonorResponse.request_id == req.id, DonorResponse.donor_user_id == user.id
    ))
    was_accepted = existing is not None and existing.status == DonorResponseStatus.ACCEPTED
    if existing is None:
        # Concurrency-safe first response: two simultaneous responses from the
        # same donor (double-tap, two tabs) would otherwise both pass this
        # "no existing row" check and both try to insert, tripping the DB's
        # uq_donor_response_pair constraint. A SAVEPOINT means only the
        # losing insert is rolled back -- not the whole request -- and it
        # falls back to updating the row the winner just created, rather than
        # surfacing a raw 500.
        savepoint = db.begin_nested()
        try:
            existing = DonorResponse(request_id=req.id, donor_user_id=user.id,
                                     status=payload.action, note=payload.note)
            db.add(existing)
            db.flush()
            savepoint.commit()
        except IntegrityError:
            savepoint.rollback()
            existing = db.scalar(select(DonorResponse).where(
                DonorResponse.request_id == req.id, DonorResponse.donor_user_id == user.id
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
        _add_event(db, req, user.id, "donor_accepted")
    elif withdrawing:
        _add_event(db, req, user.id, "donor_withdrew", payload.note)
    elif payload.action == DonorResponseStatus.NOT_NOW:
        # Distinct from "declined" so the requester/analytics can tell a
        # temporary "can't help right now" apart from a firm no -- the whole
        # reason NOT_NOW exists (see its definition in models/enums.py).
        _add_event(db, req, user.id, "donor_not_now", payload.note)
    else:
        _add_event(db, req, user.id, "donor_declined")

    # Confidence shortlist: the donor's answer updates their contact row
    # (created now if they answered the open alert without an invitation);
    # a "no" lets the shortlist contact the next ranked donor.
    donor_shortlist_service.record_response(
        db, ShortlistSubject.BLOOD_REQUEST, req.id, user.id, payload.action,
        was_accepted=was_accepted,
    )
    if not accepting:
        donor_shortlist_service.maybe_escalate(
            db, donor_shortlist_service.load_subject(db, ShortlistSubject.BLOOD_REQUEST, req.id))

    if accepting and req.status == BloodRequestStatus.PENDING:
        try:
            blood_request_lifecycle.transition(
                db, req, BloodRequestStatus.MATCHING,
                actor_user_id=user.id, reason="A donor accepted",
            )
        except InvalidTransitionError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if (
        withdrawing
        and req.status == BloodRequestStatus.MATCHING
        and _accepted_count(db, req.id) == 0
    ):
        try:
            blood_request_lifecycle.transition(
                db, req, BloodRequestStatus.PENDING,
                actor_user_id=None, reason="All matched donors withdrew",
                event_type="reopened", event_note="All matched donors withdrew",
            )
        except InvalidTransitionError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    if accepting:
        notification_service.notify(
            db, req.requester_user_id, type="blood_request.donor_accepted",
            title="A donor accepted your request",
            body=f"{user.full_name} ({profile.blood_type.value}) can help.",
            data={"request_id": str(req.id), "donor_user_id": str(user.id)},
        )
        notification_service.notify(
            db, user.id, type="blood_request.you_accepted",
            title="You're confirmed to help",
            body="The requester has been notified. They may contact you to arrange the donation.",
            data={"request_id": str(req.id)},
        )
    elif withdrawing:
        notification_service.notify(
            db, req.requester_user_id, type="blood_request.donor_withdrew",
            title="A donor withdrew from your request",
            body=f"{user.full_name} can no longer donate. Matching continues.",
            data={"request_id": str(req.id), "donor_user_id": str(user.id)},
        )
    audit_service.record(db, "blood_request.responded", actor_user_id=user.id,
                         target_type="blood_request", target_id=req.id,
                         meta={"action": payload.action.value, "withdrew": withdrawing})
    db.commit()
    db.refresh(existing)
    return DonorResponseOut.model_validate(existing)
