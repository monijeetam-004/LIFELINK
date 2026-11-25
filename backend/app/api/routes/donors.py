from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_roles, verify_origin
from app.core.blood_compatibility import can_donate_to
from app.core.eligibility import eligible_on
from app.core.time import utcnow
from app.models.appointment import DonorAppointment
from app.models.blood_request import BloodRequest, DonorResponse
from app.models.donation import Donation
from app.models.enums import (
    AppointmentStatus,
    BloodRequestStatus,
    BloodType,
    DonorAvailabilityState,
    DonorResponseStatus,
    haversine_km,
)
from app.models.hospital import HospitalProfile
from app.models.user import User, UserRole
from app.schemas.appointment import AppointmentCreate, AppointmentOut
from app.schemas.blood_request import (
    DonorMatchOut,
    DonorOpenRequestOut,
    DonorResponseOut,
)
from app.schemas.donation import DonationCreate, DonationOut
from app.schemas.profile import (
    AvailabilityUpdate,
    DonorProfileOut,
    DonorProfileUpdate,
)
from app.services import audit_service, notification_service
from app.services.domain_service import get_or_create_donor_profile

router = APIRouter(prefix="/donors", tags=["donors"])

_donor = require_roles(UserRole.DONOR)


@router.get("/me", response_model=DonorProfileOut)
def get_my_donor_profile(user: User = Depends(_donor), db: Session = Depends(get_db)):
    profile = get_or_create_donor_profile(db, user)
    db.commit()
    return profile


@router.put("/me", response_model=DonorProfileOut)
def update_my_donor_profile(
    payload: DonorProfileUpdate,
    _: None = Depends(verify_origin),
    user: User = Depends(_donor),
    db: Session = Depends(get_db),
):
    profile = get_or_create_donor_profile(db, user)
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(profile, field, value)
    # Audit 2026-10-31: record when the shared location was last set, so the
    # distance policy can label a stale location instead of trusting it.
    if {"latitude", "longitude", "share_location"} & changes.keys():
        profile.location_updated_at = (
            utcnow() if profile.latitude is not None and profile.longitude is not None else None
        )
    db.commit()
    db.refresh(profile)
    return profile


@router.put("/me/availability", response_model=DonorProfileOut)
def update_availability(
    payload: AvailabilityUpdate,
    _: None = Depends(verify_origin),
    user: User = Depends(_donor),
    db: Session = Depends(get_db),
):
    profile = get_or_create_donor_profile(db, user)
    profile.availability_state = payload.state
    # `is_available` is what every matching/notification query filters on --
    # only AVAILABLE counts as available to be matched; UNAVAILABLE and
    # NOT_NOW both take the donor out of matching (see DonorProfile's
    # availability_state comment for why they aren't given different rules).
    profile.is_available = payload.state == DonorAvailabilityState.AVAILABLE
    profile.availability_note = payload.availability_note
    # An explicit "I am available" is meaningful activity for the confidence
    # shortlist; a login or page view is not.
    if profile.is_available:
        profile.availability_confirmed_at = utcnow()
    db.commit()
    db.refresh(profile)
    return profile


def _patient_names(db: Session, reqs) -> dict:
    """Phase K: one query for every patient name a request list needs."""
    ids = {r.patient_user_id for r in reqs}
    if not ids:
        return {}
    return dict(db.execute(select(User.id, User.full_name).where(User.id.in_(ids))).all())


def _accepted_counts(db: Session, reqs) -> dict:
    """Phase K: ACCEPTED donor responses for many requests in one query."""
    ids = [r.id for r in reqs]
    if not ids:
        return {}
    return dict(db.execute(
        select(DonorResponse.request_id, func.count())
        .where(DonorResponse.request_id.in_(ids),
               DonorResponse.status == DonorResponseStatus.ACCEPTED)
        .group_by(DonorResponse.request_id)
    ).all())


@router.get("/me/requests", response_model=list[DonorOpenRequestOut])
def open_requests_for_me(
    user: User = Depends(_donor), db: Session = Depends(get_db)
) -> list[DonorOpenRequestOut]:
    profile = get_or_create_donor_profile(db, user)
    db.commit()
    if profile.blood_type is None:
        return []
    # Recipient blood types this donor's red cells can serve.
    servable = [rt for rt in BloodType if can_donate_to(profile.blood_type, rt)]
    reqs = db.scalars(
        select(BloodRequest)
        .where(
            BloodRequest.status.in_(
                (BloodRequestStatus.PENDING, BloodRequestStatus.MATCHING)
            ),
            BloodRequest.blood_type.in_(servable),
        )
        .order_by(BloodRequest.urgency.desc(), BloodRequest.created_at.desc())
    ).all()

    # Phase K: previously three queries per open request (patient, accepted
    # count, own response) -- 2,043 statements / 1.4 s for 681 open requests
    # in the Phase K volume database. Now three queries in total; same
    # rows, same order, same fields.
    names = _patient_names(db, reqs)
    accepted = _accepted_counts(db, reqs)
    ids = [r.id for r in reqs]
    mine_by_req = {
        x.request_id: x for x in db.scalars(select(DonorResponse).where(
            DonorResponse.request_id.in_(ids), DonorResponse.donor_user_id == user.id,
        )).all()
    } if ids else {}
    out: list[DonorOpenRequestOut] = []
    for r in reqs:
        item = DonorOpenRequestOut.model_validate(r)
        item.patient_name = names.get(r.patient_user_id)
        item.accepted_count = int(accepted.get(r.id, 0))
        mine = mine_by_req.get(r.id)
        item.my_response = DonorResponseOut.model_validate(mine) if mine else None
        out.append(item)
    return out


@router.get("/me/responses", response_model=list[DonorMatchOut])
def my_match_history(
    user: User = Depends(_donor), db: Session = Depends(get_db)
) -> list[DonorMatchOut]:
    """Every request this donor has responded to, open or closed, newest first."""
    profile = get_or_create_donor_profile(db, user)
    db.commit()
    rows = db.execute(
        select(DonorResponse, BloodRequest)
        .join(BloodRequest, BloodRequest.id == DonorResponse.request_id)
        .where(DonorResponse.donor_user_id == user.id)
        .order_by(DonorResponse.created_at.desc())
    ).all()

    reqs = [r for _, r in rows]
    names = _patient_names(db, reqs)  # Phase K: batched, as above
    accepted = _accepted_counts(db, reqs)
    out: list[DonorMatchOut] = []
    for resp, r in rows:
        item = DonorMatchOut.model_validate(r)
        item.patient_name = names.get(r.patient_user_id)
        item.accepted_count = int(accepted.get(r.id, 0))
        item.my_response = DonorResponseOut.model_validate(resp)
        if (
            r.latitude is not None and r.longitude is not None
            and profile.latitude is not None and profile.longitude is not None
            and profile.share_location
        ):
            item.distance_km = haversine_km(
                r.latitude, r.longitude, profile.latitude, profile.longitude
            )
        out.append(item)
    return out


@router.get("/me/donations", response_model=list[DonationOut])
def my_donations(user: User = Depends(_donor), db: Session = Depends(get_db)) -> list[Donation]:
    return list(db.scalars(
        select(Donation).where(Donation.donor_user_id == user.id)
        .order_by(Donation.donated_on.desc())
    ).all())


def _donation_payload_hash(payload: DonationCreate) -> str:
    """SHA-256 of the fields that define what this donation actually
    records. Deliberately excludes the idempotency_key itself. Used only to
    detect a same-key/different-payload replay -- mirrors
    app.api.routes.blood_requests._idempotency_payload_hash exactly; see
    LIFELINK_PHASE_26_DONATION_FRONTEND_AUDIT.md."""
    canonical = {
        "units": payload.units,
        "donated_on": payload.donated_on.isoformat(),
        "hospital_name": payload.hospital_name,
        "hospital_id": str(payload.hospital_id) if payload.hospital_id else None,
        "note": payload.note,
        "blood_request_id": str(payload.blood_request_id) if payload.blood_request_id else None,
    }
    blob = json.dumps(canonical, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _existing_donation_by_idempotency_key(
    db: Session, user: User, key: str
) -> Donation | None:
    return db.scalar(
        select(Donation).where(
            Donation.donor_user_id == user.id,
            Donation.idempotency_key == key,
        )
    )


def _apply_last_donation_date(db: Session, user: User, donated_on: date) -> None:
    profile = get_or_create_donor_profile(db, user)
    if profile.last_donation_date is None or donated_on > profile.last_donation_date:
        profile.last_donation_date = donated_on


@router.post("/me/donations", response_model=DonationOut, status_code=status.HTTP_201_CREATED)
def log_donation(
    payload: DonationCreate,
    response: Response,
    _: None = Depends(verify_origin),
    user: User = Depends(_donor),
    db: Session = Depends(get_db),
) -> Donation:
    if payload.blood_request_id is not None:
        req = db.get(BloodRequest, payload.blood_request_id)
        if req is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")
        # Phase 23: previously any existing blood_request_id was accepted here
        # with no check that this donor was ever party to it -- a donor could
        # attach an arbitrary, unrelated request (someone else's, in another
        # city, of a different blood type) to their own self-reported
        # donation history. Require that this donor actually accepted that
        # specific request before letting the donation reference it; a
        # donation with no request reference (blood_request_id=None) is
        # unaffected, since that's the legitimate "off-platform donation"
        # case this field was optional for in the first place.
        has_accepted_response = db.scalar(
            select(func.count()).select_from(DonorResponse).where(
                DonorResponse.request_id == payload.blood_request_id,
                DonorResponse.donor_user_id == user.id,
                DonorResponse.status == DonorResponseStatus.ACCEPTED,
            )
        ) or 0
        if has_accepted_response == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You can only log a donation against a request you accepted.",
            )

    hospital_name = payload.hospital_name
    if payload.hospital_id is not None:
        # Phase E: a real facility, not free text -- required to exist so
        # that `hospital_id` always points at something real, which is
        # what gives it the authority to later verify this donation.
        facility = db.get(HospitalProfile, payload.hospital_id)
        if facility is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Facility not found")
        if hospital_name is None:
            hospital_name = facility.name

    # Phase 26 idempotent replay: mirrors the Phase 19 blood-request pattern
    # (create_request) exactly -- the exact same (donor, idempotency_key)
    # pair returns the existing donation instead of creating a second one,
    # checked up front so a replay never re-evaluates the ownership check
    # above twice with side effects. A same-key/different-payload replay is
    # explicitly rejected (409), matching the blood-request precedent this
    # was found to lack in Phase 25 (LIFELINK_PHASE_25_TESTING_AUDIT.md /
    # LIFELINK_PHASE_25_TESTING_REPORT.md Bugs Found #2).
    payload_hash = _donation_payload_hash(payload)
    if payload.idempotency_key is not None:
        existing = _existing_donation_by_idempotency_key(db, user, payload.idempotency_key)
        if existing is not None:
            if existing.idempotency_payload_hash != payload_hash:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="This idempotency key was already used with a different donation.",
                )
            response.status_code = status.HTTP_200_OK
            return existing

    def _build_donation() -> Donation:
        return Donation(
            donor_user_id=user.id,
            blood_request_id=payload.blood_request_id,
            units=payload.units,
            donated_on=payload.donated_on,
            hospital_name=hospital_name,
            hospital_id=payload.hospital_id,
            note=payload.note,
            idempotency_key=payload.idempotency_key,
            idempotency_payload_hash=payload_hash if payload.idempotency_key is not None else None,
        )

    if payload.idempotency_key is not None:
        # Race-safe insert: two near-simultaneous requests carrying the same
        # (donor, key) pair must not both create a row -- the second one to
        # reach the DB hits uq_donation_idempotency_key and replays the
        # first's result instead, exactly like create_request's own
        # SAVEPOINT-guarded race handling. db.begin_nested() is called
        # before `donation` is added to the session: Session.begin_nested()
        # autoflushes any already-pending objects while establishing the
        # SAVEPOINT, so a genuine collision is caught inside this try block
        # rather than outside it. The database's unique constraint -- not
        # this application-level check-then-insert -- is what's actually
        # authoritative here.
        savepoint = db.begin_nested()
        try:
            donation = _build_donation()
            db.add(donation)
            db.flush()
            savepoint.commit()
        except IntegrityError:
            savepoint.rollback()
            existing = _existing_donation_by_idempotency_key(db, user, payload.idempotency_key)
            if existing is None:
                raise  # pragma: no cover - constraint violation implies a row exists
            if existing.idempotency_payload_hash != payload_hash:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="This idempotency key was already used with a different donation.",
                ) from None
            # No further writes were made in this (losing) transaction --
            # the winner already applied and committed the profile update
            # below before this IntegrityError could even be raised (the
            # unique-constraint check blocks on, then sees, the winner's
            # committed row) -- so there is nothing here to commit, exactly
            # like create_request's identical branch.
            response.status_code = status.HTTP_200_OK
            return existing
    else:
        donation = _build_donation()
        db.add(donation)
        db.flush()

    _apply_last_donation_date(db, user, payload.donated_on)
    # Phase 26: donation logging previously recorded no audit event at all
    # (a real gap flagged in Phase 25's audit) -- every other mutation in
    # this codebase does. Recorded only on a genuine new row, never on a
    # replay (the early-return branches above never reach here), so a
    # replayed request cannot produce a duplicate audit event.
    audit_service.record(
        db, "donation.logged", actor_user_id=user.id, target_type="donation",
        target_id=donation.id,
        meta={"units": payload.units, "blood_request_id": str(payload.blood_request_id)
              if payload.blood_request_id else None},
    )
    db.commit()
    db.refresh(donation)
    return donation


@router.post(
    "/me/appointments", response_model=AppointmentOut, status_code=status.HTTP_201_CREATED
)
def request_appointment(
    payload: AppointmentCreate,
    _: None = Depends(verify_origin),
    user: User = Depends(_donor),
    db: Session = Depends(get_db),
) -> DonorAppointment:
    has_id = payload.hospital_id is not None
    has_name = bool(payload.hospital_name and payload.hospital_name.strip())
    if not has_id and not has_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide either hospital_id or hospital_name",
        )

    hospital_name = payload.hospital_name.strip() if has_name else None
    hospital: HospitalProfile | None = None
    if has_id:
        hospital = db.get(HospitalProfile, payload.hospital_id)
        if hospital is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Hospital not found")
        # Prefer the registered facility's name for display convenience.
        hospital_name = hospital.name or hospital_name

    if payload.preferred_date < date.today():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Preferred date cannot be in the past",
        )

    profile = get_or_create_donor_profile(db, user)
    due = eligible_on(profile.last_donation_date)
    if due is not None and payload.preferred_date < due:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"You are not eligible to donate until {due.isoformat()} "
                   f"based on your last donation date",
        )

    appt = DonorAppointment(
        donor_user_id=user.id,
        hospital_id=hospital.id if hospital else None,
        hospital_name=hospital_name,
        preferred_date=payload.preferred_date,
        status=AppointmentStatus.REQUESTED,
        note=payload.note,
    )
    db.add(appt)
    db.flush()

    audit_service.record(
        db, "donor.appointment_requested", actor_user_id=user.id,
        target_type="donor_appointment", target_id=appt.id,
        meta={"hospital_id": str(hospital.id) if hospital else None,
              "preferred_date": payload.preferred_date.isoformat()},
    )
    if hospital is not None:
        notification_service.notify(
            db, hospital.user_id, type="appointment.requested",
            title=f"New appointment request from {user.full_name}",
            body=f"Requested for {payload.preferred_date.isoformat()}"
                 + (f": {payload.note}" if payload.note else ""),
            data={
                "appointment_id": str(appt.id),
                "preferred_date": payload.preferred_date.isoformat(),
            },
        )
    db.commit()
    db.refresh(appt)
    return appt


@router.get("/me/appointments", response_model=list[AppointmentOut])
def my_appointments(
    user: User = Depends(_donor), db: Session = Depends(get_db)
) -> list[DonorAppointment]:
    return list(db.scalars(
        select(DonorAppointment).where(DonorAppointment.donor_user_id == user.id)
        .order_by(DonorAppointment.preferred_date.desc())
    ).all())


@router.post("/me/appointments/{appointment_id}/cancel", response_model=AppointmentOut)
def cancel_appointment(
    appointment_id: uuid.UUID,
    _: None = Depends(verify_origin),
    user: User = Depends(_donor),
    db: Session = Depends(get_db),
) -> DonorAppointment:
    appt = db.scalar(
        select(DonorAppointment).where(
            DonorAppointment.id == appointment_id,
            DonorAppointment.donor_user_id == user.id,
        )
    )
    if appt is None:
        # Distinguish "no such appointment at all" from "a real appointment,
        # just someone else's" -- only the latter is a genuine cross-tenant
        # probe worth an audit record (same reasoning as every other
        # ownership check in this phase).
        exists_for_other = db.scalar(
            select(DonorAppointment.id).where(DonorAppointment.id == appointment_id)
        )
        if exists_for_other is not None:
            audit_service.record(
                db, "donor_appointment.access_denied", actor_user_id=user.id,
                target_type="donor_appointment", target_id=appointment_id,
                meta={"attempted_action": "cancel"},
            )
            db.commit()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found")
    if appt.status not in (AppointmentStatus.REQUESTED, AppointmentStatus.CONFIRMED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Appointment is already {appt.status.value.lower()}",
        )

    appt.status = AppointmentStatus.CANCELLED
    audit_service.record(
        db, "donor.appointment_cancelled", actor_user_id=user.id,
        target_type="donor_appointment", target_id=appt.id,
    )
    if appt.hospital_id is not None:
        hospital = db.get(HospitalProfile, appt.hospital_id)
        if hospital is not None:
            notification_service.notify(
                db, hospital.user_id, type="appointment.cancelled",
                title=f"{user.full_name} cancelled their appointment request",
                body=f"Was requested for {appt.preferred_date.isoformat()}",
                data={"appointment_id": str(appt.id)},
            )
    db.commit()
    db.refresh(appt)
    return appt
