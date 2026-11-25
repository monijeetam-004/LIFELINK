from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, require_roles, verify_origin
from app.core import request_fulfillment
from app.core.request_fulfillment import OverAllocationError
from app.core.request_lifecycle import InvalidTransitionError, allowed_transitions
from app.models.blood_request import BloodRequest, BloodRequestEvent, DonorResponse
from app.models.blood_unit import BloodUnit
from app.models.emergency import Emergency
from app.models.enums import (
    BloodComponent,
    BloodRequestStatus,
    BloodType,
    BloodUnitState,
    DonorResponseStatus,
    EmergencyStatus,
    HospitalResponseStatus,
    Urgency,
    haversine_km,
)
from app.models.graph_pathway_log import GraphPathwayLog
from app.models.hospital import HospitalInventory, HospitalProfile, HospitalRequestResponse
from app.models.transport import TransportRoute
from app.models.user import User, UserRole
from app.schemas.blood_request import BloodRequestEventOut
from app.schemas.emergency import EmergencyOut
from app.schemas.graph import PathwayCandidateOut, ResourcePathwayOut
from app.schemas.hospital import (
    ComponentInventoryItem,
    HospitalActivityItemOut,
    HospitalDirectoryItem,
    HospitalInventoryLogOut,
    HospitalProfileOut,
    HospitalProfileUpdate,
    HospitalRequestItem,
    HospitalRequestResponseOut,
    HospitalRespondIn,
    HospitalRouteOut,
    InventoryActionIn,
    InventoryItem,
    InventoryReconcileIn,
    InventoryUpdate,
)
from app.services import (
    audit_service,
    blood_request_lifecycle,
    graph_service,
    inventory_service,
    notification_service,
)
from app.services.domain_service import get_or_create_hospital_profile
from app.services.inventory_service import InventoryActionError

router = APIRouter(prefix="/hospitals", tags=["hospitals"])

_hospital = require_roles(UserRole.HOSPITAL)
_OPEN_REQUEST_STATUSES = (
    BloodRequestStatus.PENDING,
    BloodRequestStatus.MATCHING,
    BloodRequestStatus.PARTIALLY_FULFILLED,
)


@router.get("/directory", response_model=list[HospitalDirectoryItem])
def hospital_directory(
    _: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[HospitalProfile]:
    """Minimal facility picker list for any authenticated user (donors included).

    Only hospitals with a ``name`` set are listed -- an empty HospitalProfile
    shell (created lazily on first ``/hospitals/me`` visit) isn't a real,
    named facility yet. Unverified hospitals are still included with
    ``verified: false`` rather than hidden, so the frontend can show real
    status.
    """
    return list(
        db.scalars(
            select(HospitalProfile)
            .where(HospitalProfile.name.is_not(None))
            .order_by(HospitalProfile.verified.desc(), HospitalProfile.name.asc())
        ).all()
    )


@router.get("/me", response_model=HospitalProfileOut)
def get_my_hospital(user: User = Depends(_hospital), db: Session = Depends(get_db)):
    hp = get_or_create_hospital_profile(db, user)
    db.commit()
    return hp


@router.put("/me", response_model=HospitalProfileOut)
def update_my_hospital(
    payload: HospitalProfileUpdate,
    _: None = Depends(verify_origin),
    user: User = Depends(_hospital),
    db: Session = Depends(get_db),
):
    hp = get_or_create_hospital_profile(db, user)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(hp, field, value)
    db.commit()
    db.refresh(hp)
    graph_service.sync_facility(hp)  # best-effort; never blocks this write
    return hp


def _inventory_map(db: Session, hospital_id: uuid.UUID) -> dict[BloodType, HospitalInventory]:
    # Phase 35: the existing GET/PUT /hospitals/me/inventory contract is
    # byte-identical only because this stays pinned to the WHOLE_BLOOD row
    # per blood type -- without this filter, a hospital that ever gets a
    # RED_BLOOD_CELLS/PLASMA/PLATELETS row for a blood type it already has
    # WHOLE_BLOOD stock for would collapse two distinct rows onto one dict
    # key (non-deterministic which one "wins"), silently corrupting this
    # legacy endpoint's response. See InventoryActionIn/component docstring.
    rows = db.scalars(
        select(HospitalInventory).where(
            HospitalInventory.hospital_id == hospital_id,
            HospitalInventory.component == BloodComponent.WHOLE_BLOOD,
        )
    ).all()
    return {r.blood_type: r for r in rows}


def _to_item(bt: BloodType, row: HospitalInventory | None) -> InventoryItem:
    units = row.units if row else 0
    reserved = row.reserved_units if row else 0
    return InventoryItem(
        blood_type=bt, units=units, reserved_units=reserved, available_units=units - reserved
    )


@router.get("/me/inventory", response_model=list[InventoryItem])
def get_inventory(user: User = Depends(_hospital), db: Session = Depends(get_db)):
    hp = get_or_create_hospital_profile(db, user)
    db.commit()
    # Phase 34: a read must be as honest as a write -- a unit that has
    # genuinely passed its shelf life must not still be counted as
    # available merely because no mutating action has touched this row
    # since (report section 5). `sweep_all_expired` durably commits any
    # real expiry it finds.
    inventory_service.sweep_all_expired(db, hp.id)
    existing = _inventory_map(db, hp.id)
    return [_to_item(bt, existing.get(bt)) for bt in BloodType]


@router.get("/me/inventory/components", response_model=list[ComponentInventoryItem])
def get_component_inventory(
    user: User = Depends(_hospital), db: Session = Depends(get_db)
) -> list[ComponentInventoryItem]:
    """Phase G: every (blood_type, component) row this facility holds, after
    the same lazy expiry sweep GET /me/inventory performs. Read-only; the
    caller's own facility only (no facility id is accepted)."""
    hp = get_or_create_hospital_profile(db, user)
    db.commit()
    inventory_service.sweep_all_expired(db, hp.id)
    rows = db.scalars(
        select(HospitalInventory)
        .where(HospitalInventory.hospital_id == hp.id)
        .order_by(HospitalInventory.blood_type, HospitalInventory.component)
    ).all()
    return [
        ComponentInventoryItem(
            blood_type=r.blood_type,
            component=r.component,
            units=r.units,
            reserved_units=r.reserved_units,
            available_units=r.units - r.reserved_units,
        )
        for r in rows
    ]


@router.put("/me/inventory", response_model=list[InventoryItem])
def put_inventory(
    payload: InventoryUpdate,
    _: None = Depends(verify_origin),
    user: User = Depends(_hospital),
    db: Session = Depends(get_db),
):
    """Bulk stock-take correction -- equivalent to calling RECONCILE once
    per item in `payload.items`. Kept for the existing full-grid editor;
    each changed type gets its own HospitalInventoryLog(action=RECONCILE)
    row exactly like the single-item /inventory/reconcile endpoint."""
    hp = get_or_create_hospital_profile(db, user)
    try:
        for item in payload.items:
            inventory_service.reconcile_inventory(
                db,
                hp.id,
                item.blood_type,
                item.units,
                actor_user_id=user.id,
            )
    except InventoryActionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    audit_service.record(
        db,
        "hospital.inventory_updated",
        actor_user_id=user.id,
        target_type="hospital",
        target_id=hp.id,
    )
    db.commit()
    refreshed = _inventory_map(db, hp.id)
    return [_to_item(bt, refreshed.get(bt)) for bt in BloodType]


def _do_inventory_action(
    action_fn,
    db: Session,
    hp: HospitalProfile,
    user: User,
    payload: InventoryActionIn,
    audit_action: str,
) -> InventoryItem:
    try:
        row = action_fn(
            db,
            hp.id,
            payload.blood_type,
            payload.units,
            actor_user_id=user.id,
            request_id=payload.request_id,
            note=payload.note,
            component=payload.component,
        )
    except InventoryActionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    audit_service.record(
        db,
        audit_action,
        actor_user_id=user.id,
        target_type="hospital_inventory",
        target_id=row.id,
        meta={
            "blood_type": payload.blood_type.value,
            "units": payload.units,
            "component": payload.component.value,
        },
    )
    db.commit()
    db.refresh(row)
    return _to_item(payload.blood_type, row)


@router.post("/me/inventory/add", response_model=InventoryItem)
def add_inventory(
    payload: InventoryActionIn,
    _: None = Depends(verify_origin),
    user: User = Depends(_hospital),
    db: Session = Depends(get_db),
):
    hp = get_or_create_hospital_profile(db, user)
    try:
        row = inventory_service.add_stock(
            db,
            hp.id,
            payload.blood_type,
            payload.units,
            actor_user_id=user.id,
            note=payload.note,
            component=payload.component,
            donation_id=payload.donation_id,
        )
    except InventoryActionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    audit_service.record(
        db,
        "hospital.inventory_add",
        actor_user_id=user.id,
        target_type="hospital_inventory",
        target_id=row.id,
        meta={
            "blood_type": payload.blood_type.value,
            "units": payload.units,
            "component": payload.component.value,
            "donation_id": str(payload.donation_id) if payload.donation_id else None,
        },
    )
    db.commit()
    db.refresh(row)
    return _to_item(payload.blood_type, row)


@router.post("/me/inventory/reserve", response_model=InventoryItem)
def reserve_inventory(
    payload: InventoryActionIn,
    _: None = Depends(verify_origin),
    user: User = Depends(_hospital),
    db: Session = Depends(get_db),
):
    hp = get_or_create_hospital_profile(db, user)
    return _do_inventory_action(
        inventory_service.reserve_stock, db, hp, user, payload, "hospital.inventory_reserve"
    )


@router.post("/me/inventory/allocate", response_model=InventoryItem)
def allocate_inventory(
    payload: InventoryActionIn,
    _: None = Depends(verify_origin),
    user: User = Depends(_hospital),
    db: Session = Depends(get_db),
):
    """Direct inventory allocation, independent of any specific incoming
    request (e.g. dispensing to a ward). To allocate *against* a tracked
    incoming request and record that on the request's own timeline, use
    POST /hospitals/me/requests/{id}/allocate instead."""
    hp = get_or_create_hospital_profile(db, user)
    return _do_inventory_action(
        inventory_service.allocate_stock, db, hp, user, payload, "hospital.inventory_allocate"
    )


@router.post("/me/inventory/release", response_model=InventoryItem)
def release_inventory(
    payload: InventoryActionIn,
    _: None = Depends(verify_origin),
    user: User = Depends(_hospital),
    db: Session = Depends(get_db),
):
    hp = get_or_create_hospital_profile(db, user)
    return _do_inventory_action(
        inventory_service.release_reservation, db, hp, user, payload, "hospital.inventory_release"
    )


@router.post("/me/inventory/expire", response_model=InventoryItem)
def expire_inventory(
    payload: InventoryActionIn,
    _: None = Depends(verify_origin),
    user: User = Depends(_hospital),
    db: Session = Depends(get_db),
):
    hp = get_or_create_hospital_profile(db, user)
    try:
        row = inventory_service.expire_units(
            db,
            hp.id,
            payload.blood_type,
            payload.units,
            actor_user_id=user.id,
            note=payload.note,
            component=payload.component,
        )
    except InventoryActionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    audit_service.record(
        db,
        "hospital.inventory_expire",
        actor_user_id=user.id,
        target_type="hospital_inventory",
        target_id=row.id,
        meta={
            "blood_type": payload.blood_type.value,
            "units": payload.units,
            "component": payload.component.value,
        },
    )
    db.commit()
    db.refresh(row)
    return _to_item(payload.blood_type, row)


@router.post("/me/inventory/reconcile", response_model=InventoryItem)
def reconcile_inventory_one(
    payload: InventoryReconcileIn,
    _: None = Depends(verify_origin),
    user: User = Depends(_hospital),
    db: Session = Depends(get_db),
):
    hp = get_or_create_hospital_profile(db, user)
    try:
        row = inventory_service.reconcile_inventory(
            db,
            hp.id,
            payload.blood_type,
            payload.units,
            actor_user_id=user.id,
            note=payload.note,
            component=payload.component,
        )
    except InventoryActionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    audit_service.record(
        db,
        "hospital.inventory_reconcile",
        actor_user_id=user.id,
        target_type="hospital_inventory",
        target_id=row.id,
        meta={
            "blood_type": payload.blood_type.value,
            "units": payload.units,
            "component": payload.component.value,
        },
    )
    db.commit()
    db.refresh(row)
    return _to_item(payload.blood_type, row)


@router.get("/me/inventory/log", response_model=list[HospitalInventoryLogOut])
def inventory_log(
    limit: int = 50,
    user: User = Depends(_hospital),
    db: Session = Depends(get_db),
):
    hp = get_or_create_hospital_profile(db, user)
    db.commit()
    return inventory_service.recent_log(db, hp.id, limit=min(max(limit, 1), 200))


def _visible_request_filter(hp: HospitalProfile):
    clauses = []
    if hp.city:
        clauses.append(BloodRequest.city == hp.city)
    if hp.name:
        clauses.append(BloodRequest.hospital_name == hp.name)
    return or_(*clauses) if clauses else None


@router.get("/me/requests", response_model=list[HospitalRequestItem])
def incoming_requests(
    include_closed: bool = False,
    user: User = Depends(_hospital),
    db: Session = Depends(get_db),
) -> list[HospitalRequestItem]:
    """Phase 24: this previously issued one extra query per visible request
    for `accepted_count` (a per-request COUNT that can never be satisfied by
    SQLAlchemy's identity map, since the WHERE clause differs every time)
    plus one `db.get(User, ...)` per request for the patient's name (which
    the identity map only dedups when the *same* patient repeats across
    rows -- not guaranteed, and not true in the general case). Reproduced
    with a real SQLAlchemy `before_cursor_execute` query counter against a
    hospital with several visible requests: N visible requests cost
    roughly 1 (base list) + 1 (hospital responses) + 2*N queries before this
    change. Both loops are now single batched queries run once before the
    per-row loop, so the total is a fixed 4 queries regardless of N, and
    the returned data (accepted_count int, patient_name str | None) is
    identical to before -- see
    tests/test_phase24_performance_hardening.py::test_incoming_requests_query_count
    for the reproduction and the before/after count assertion.
    """
    hp = get_or_create_hospital_profile(db, user)
    db.commit()
    where = _visible_request_filter(hp)
    if where is None:
        return []
    stmt = select(BloodRequest).where(where)
    if not include_closed:
        stmt = stmt.where(
            BloodRequest.status.in_((BloodRequestStatus.PENDING, BloodRequestStatus.MATCHING))
        )
    stmt = stmt.order_by(BloodRequest.urgency.desc(), BloodRequest.created_at.desc())

    responses = {
        r.request_id: r
        for r in db.scalars(
            select(HospitalRequestResponse).where(HospitalRequestResponse.hospital_id == hp.id)
        ).all()
    }
    requests = list(db.scalars(stmt).all())
    for req in requests:
        blood_request_lifecycle.expire_if_stale(db, req)

    request_ids = [req.id for req in requests]
    patient_ids = {req.patient_user_id for req in requests}

    accepted_counts: dict[uuid.UUID, int] = {}
    if request_ids:
        for req_id, count in db.execute(
            select(DonorResponse.request_id, func.count())
            .where(
                DonorResponse.request_id.in_(request_ids),
                DonorResponse.status == DonorResponseStatus.ACCEPTED,
            )
            .group_by(DonorResponse.request_id)
        ).all():
            accepted_counts[req_id] = int(count)

    patients: dict[uuid.UUID, User] = {}
    if patient_ids:
        patients = {u.id: u for u in db.scalars(select(User).where(User.id.in_(patient_ids))).all()}

    out: list[HospitalRequestItem] = []
    for req in requests:
        item = HospitalRequestItem.model_validate(req)
        item.allowed_transitions = allowed_transitions(req.status)
        patient = patients.get(req.patient_user_id)
        item.patient_name = patient.full_name if patient else None
        item.accepted_count = accepted_counts.get(req.id, 0)
        hr = responses.get(req.id)
        item.hospital_status = hr.status if hr else None
        if (
            hp.latitude is not None
            and hp.longitude is not None
            and req.latitude is not None
            and req.longitude is not None
        ):
            item.distance_km = haversine_km(hp.latitude, hp.longitude, req.latitude, req.longitude)
        out.append(item)
    return out


@router.post(
    "/me/requests/{request_id}/respond",
    response_model=HospitalRequestResponseOut,
)
def respond_to_incoming(
    request_id: uuid.UUID,
    payload: HospitalRespondIn,
    _: None = Depends(verify_origin),
    user: User = Depends(_hospital),
    db: Session = Depends(get_db),
) -> HospitalRequestResponse:
    hp = get_or_create_hospital_profile(db, user)
    req = _get_visible_request(db, hp, user, request_id, action="respond")

    hr = db.scalar(
        select(HospitalRequestResponse).where(
            HospitalRequestResponse.hospital_id == hp.id,
            HospitalRequestResponse.request_id == req.id,
        )
    )
    if hr is None:
        hr = HospitalRequestResponse(
            hospital_id=hp.id, request_id=req.id, status=payload.status, note=payload.note
        )
        db.add(hr)
    else:
        hr.status = payload.status
        hr.note = payload.note
    db.flush()

    db.add(
        BloodRequestEvent(
            request_id=req.id,
            actor_user_id=user.id,
            event_type=f"hospital_{payload.status.value.lower()}",
            note=(hp.name or "A hospital") + (f": {payload.note}" if payload.note else ""),
        )
    )
    notification_service.notify(
        db,
        req.requester_user_id,
        type="blood_request.hospital_update",
        title=f"{hp.name or 'A hospital'} is {payload.status.value.lower()} your request",
        body=payload.note,
        data={"request_id": str(req.id), "hospital_status": payload.status.value},
    )
    audit_service.record(
        db,
        "hospital.request_response",
        actor_user_id=user.id,
        target_type="blood_request",
        target_id=req.id,
        meta={"status": payload.status.value},
    )
    db.commit()
    db.refresh(hr)
    return hr


def _get_visible_request(
    db: Session,
    hp: HospitalProfile,
    user: User,
    request_id: uuid.UUID,
    *,
    action: str,
    for_update: bool = False,
) -> BloodRequest:
    req = (
        blood_request_lifecycle.get_for_update(db, request_id)
        if for_update
        else db.get(BloodRequest, request_id)
    )
    if req is not None:
        blood_request_lifecycle.expire_if_stale(db, req)
    where = _visible_request_filter(hp)
    if (
        req is None
        or where is None
        or not db.scalar(select(BloodRequest.id).where(BloodRequest.id == req.id, where))
    ):
        if req is not None:
            # The request is real, just outside this hospital's visibility
            # (a different city/facility) -- a genuine attempt to act on
            # another facility's business, not a bad id, so it's audited.
            audit_service.record(
                db,
                "hospital.request_access_denied",
                actor_user_id=user.id,
                target_type="blood_request",
                target_id=req.id,
                meta={"hospital_id": str(hp.id), "attempted_action": action},
            )
            db.commit()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not visible")
    return req


def _get_or_create_hospital_response(
    db: Session, hp: HospitalProfile, req: BloodRequest
) -> HospitalRequestResponse:
    hr = db.scalar(
        select(HospitalRequestResponse).where(
            HospitalRequestResponse.hospital_id == hp.id,
            HospitalRequestResponse.request_id == req.id,
        )
    )
    if hr is None:
        hr = HospitalRequestResponse(
            hospital_id=hp.id,
            request_id=req.id,
            status=HospitalResponseStatus.ACKNOWLEDGED,
        )
        db.add(hr)
        db.flush()
    return hr


@router.post(
    "/me/requests/{request_id}/allocate",
    response_model=HospitalRequestResponseOut,
)
def allocate_to_request(
    request_id: uuid.UUID,
    payload: InventoryActionIn,
    _: None = Depends(verify_origin),
    user: User = Depends(_hospital),
    db: Session = Depends(get_db),
) -> HospitalRequestResponse:
    """Dispatch `payload.units` of this hospital's own inventory against one
    incoming request: a real inventory deduction (via inventory_service,
    same never-negative guarantees as every other inventory action), not
    just a status label. Only open (PENDING/MATCHING/PARTIALLY_FULFILLED)
    requests can be allocated against.

    Phase E: quantity-aware. `req.fulfilled_units` (the single source of
    truth -- app.core.request_fulfillment) is checked and updated under
    the SAME row lock this endpoint already held before Phase E existed,
    so "two hospitals racing to fulfil the final remaining units"
    (Section 5 Race A) and "two partial allocations landing concurrently"
    (Race B) both serialize here exactly like every other concurrent
    mutation on this row already does -- no new locking mechanism."""
    hp = get_or_create_hospital_profile(db, user)
    # Locked for the rest of this transaction -- see Phase 10's original
    # comment on this same call, now also what makes the quantity check
    # below race-safe rather than just the status check.
    req = _get_visible_request(db, hp, user, request_id, action="allocate", for_update=True)
    if req.status not in _OPEN_REQUEST_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot allocate against a {req.status.value.lower()} request",
        )
    if payload.blood_type != req.blood_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"This request needs {req.blood_type.value}, not {payload.blood_type.value}",
        )
    # Quantity checked BEFORE any inventory is touched -- an over-
    # allocation attempt never reaches inventory_service at all, so a
    # rejected call has zero side effects anywhere (Section 33: never
    # trust a quantity without checking it first).
    try:
        request_fulfillment.validate_allocation(
            req.units_needed, req.fulfilled_units, payload.units
        )
    except OverAllocationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    try:
        inventory_service.allocate_stock(
            db,
            hp.id,
            payload.blood_type,
            payload.units,
            actor_user_id=user.id,
            request_id=req.id,
            note=payload.note,
            component=payload.component,
        )
    except InventoryActionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    old_status = req.status
    req.fulfilled_units += payload.units
    target = request_fulfillment.target_status_after_allocation(
        req.status, req.units_needed, req.fulfilled_units
    )

    hr = _get_or_create_hospital_response(db, hp, req)
    hr.status = HospitalResponseStatus.ALLOCATED
    hr.note = payload.note
    hr.units_allocated += payload.units
    db.flush()

    note = (hp.name or "A hospital") + f" allocated {payload.units} unit(s) from inventory"
    if target is not None:
        # The fulfilment bucket actually changed (PENDING/MATCHING ->
        # PARTIALLY_FULFILLED, or -> FULFILLED) -- go through the real
        # state machine (writes its own BloodRequestEvent/AuditLog
        # transition record), not a second, parallel status write.
        blood_request_lifecycle.transition(
            db,
            req,
            target,
            actor_user_id=user.id,
            reason=note,
            event_type="hospital_allocated",
            extra_meta={
                "units": payload.units,
                "blood_type": payload.blood_type.value,
                "fulfilled_units": req.fulfilled_units,
                "units_needed": req.units_needed,
            },
        )
    else:
        # Same bucket as before (e.g. a second partial allocation that
        # still leaves the request PARTIALLY_FULFILLED) -- a real event
        # worth recording, but not a status *transition*; validate_transition
        # would reject a same-state call, and rightly so (see
        # app.core.request_lifecycle's own same-state rule).
        db.add(
            BloodRequestEvent(
                request_id=req.id,
                actor_user_id=user.id,
                event_type="hospital_allocated",
                note=note,
            )
        )
        audit_service.record(
            db,
            "hospital.request_allocated",
            actor_user_id=user.id,
            target_type="blood_request",
            target_id=req.id,
            meta={
                "units": payload.units,
                "blood_type": payload.blood_type.value,
                "fulfilled_units": req.fulfilled_units,
                "units_needed": req.units_needed,
                "status": old_status.value,
            },
        )
    notification_service.notify(
        db,
        req.requester_user_id,
        type="blood_request.hospital_update",
        title=f"{hp.name or 'A hospital'} allocated units to your request",
        body=(
            f"{payload.units} unit(s) of {payload.blood_type.value} allocated from inventory "
            f"({req.fulfilled_units}/{req.units_needed} fulfilled)."
        ),
        data={
            "request_id": str(req.id),
            "hospital_status": HospitalResponseStatus.ALLOCATED.value,
            "fulfilled_units": req.fulfilled_units,
            "units_needed": req.units_needed,
        },
    )
    db.commit()
    db.refresh(hr)
    return hr


@router.post(
    "/me/requests/{request_id}/fulfill",
    response_model=HospitalRequestResponseOut,
)
def fulfill_from_hospital(
    request_id: uuid.UUID,
    _: None = Depends(verify_origin),
    user: User = Depends(_hospital),
    db: Session = Depends(get_db),
) -> HospitalRequestResponse:
    """Mark this request fulfilled from this hospital's own supply. This
    sets the *same* BloodRequestStatus.FULFILLED the donor-matching path
    (POST /blood-requests/{id}/fulfill) sets -- one shared state machine,
    guarded the same way (only PENDING/MATCHING/PARTIALLY_FULFILLED can
    transition), so a request can be fulfilled by whichever real path
    actually supplied it, never both.

    Phase E honesty fix: this is a "declare the rest met through a
    channel this system doesn't itemize" action (e.g. a donor matched and
    donated in person, or this hospital supplied the remainder without a
    separate per-unit /allocate call for each one) -- NOT a bypass of the
    quantity model. Whatever `fulfilled_units` already is (possibly 0,
    possibly partially filled by prior /allocate calls from this or other
    hospitals), it is brought up to exactly `units_needed` here, and the
    gap is attributed to THIS hospital's own `units_allocated` so the
    per-hospital breakdown still sums correctly (Section 4's single-
    source-of-truth invariant holds for this path too, not just the
    granular /allocate path)."""
    hp = get_or_create_hospital_profile(db, user)
    req = _get_visible_request(db, hp, user, request_id, action="fulfill", for_update=True)
    gap = req.units_needed - req.fulfilled_units
    try:
        blood_request_lifecycle.transition(
            db,
            req,
            BloodRequestStatus.FULFILLED,
            actor_user_id=user.id,
            event_type="hospital_fulfilled",
            event_note=f"{hp.name or 'A hospital'} fulfilled this request from inventory",
            extra_meta={"units_declared_at_fulfil": gap, "units_needed": req.units_needed},
        )
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    req.fulfilled_units = req.units_needed

    hr = _get_or_create_hospital_response(db, hp, req)
    hr.status = HospitalResponseStatus.FULFILLED
    if gap > 0:
        hr.units_allocated += gap
    db.flush()

    # Mirror the donor-fulfilment path's donor-facing notifications for any
    # donors who had accepted -- their acceptance is still real even though
    # this hospital, not their individual donation, is what closed it out.
    accepted = db.scalars(
        select(DonorResponse).where(
            DonorResponse.request_id == req.id,
            DonorResponse.status == DonorResponseStatus.ACCEPTED,
        )
    ).all()
    for resp in accepted:
        notification_service.notify(
            db,
            resp.donor_user_id,
            type="blood_request.fulfilled",
            title="This request has been fulfilled",
            body=(
                f"{hp.name or 'A hospital'} fulfilled it from inventory. "
                "Thank you for offering to help."
            ),
            data={"request_id": str(req.id)},
        )
    notification_service.notify(
        db,
        req.requester_user_id,
        type="blood_request.hospital_update",
        title=f"{hp.name or 'A hospital'} fulfilled your request",
        body="Your request has been fulfilled from hospital inventory.",
        data={"request_id": str(req.id), "hospital_status": HospitalResponseStatus.FULFILLED.value},
    )
    audit_service.record(
        db,
        "hospital.request_fulfilled",
        actor_user_id=user.id,
        target_type="blood_request",
        target_id=req.id,
    )
    db.commit()
    db.refresh(hr)
    return hr


@router.get("/me/requests/{request_id}/events", response_model=list[BloodRequestEventOut])
def request_events(
    request_id: uuid.UUID,
    user: User = Depends(_hospital),
    db: Session = Depends(get_db),
):
    hp = get_or_create_hospital_profile(db, user)
    db.commit()
    req = _get_visible_request(db, hp, user, request_id, action="view_events")
    return list(
        db.scalars(
            select(BloodRequestEvent)
            .where(BloodRequestEvent.request_id == req.id)
            .order_by(BloodRequestEvent.created_at.asc())
        ).all()
    )


@router.get("/me/emergencies", response_model=list[EmergencyOut])
def incoming_emergencies(
    user: User = Depends(_hospital), db: Session = Depends(get_db)
) -> list[Emergency]:
    hp = get_or_create_hospital_profile(db, user)
    db.commit()
    if not hp.city:
        return []
    # Emergencies in the same city whose creator's request references this city.
    return list(
        db.scalars(
            select(Emergency)
            .where(
                Emergency.status.in_((EmergencyStatus.ACTIVE, EmergencyStatus.ACKNOWLEDGED)),
                Emergency.location_text.ilike(f"%{hp.city}%"),
            )
            .order_by(Emergency.created_at.desc())
        ).all()
    )


@router.get("/me/resource-pathways", response_model=ResourcePathwayOut)
def my_resource_pathways(
    blood_type: BloodType,
    max_results: int = Query(default=5, ge=1, le=50),
    component: BloodComponent = Query(default=BloodComponent.WHOLE_BLOOD),
    units_needed: int = Query(default=1, ge=1, le=50),
    urgency: Urgency = Query(default=Urgency.ROUTINE),
    user: User = Depends(_hospital),
    db: Session = Depends(get_db),
) -> ResourcePathwayOut:
    """Real graph-backed (or honestly-labelled haversine-fallback) resource
    discovery: which other facilities currently hold stock of `blood_type`
    and how to reach them, ranked by real transport distance. This is the
    routine, non-emergency use of the graph engine (see
    GET /emergencies/{id}/resource-pathways for the emergency-flow one).

    Phase 16: max_results is now bounded (1-50, same shape as the existing
    admin pathway-log's `limit`) -- previously an unconstrained int, so a
    client could request an arbitrarily large result set, and a negative
    value would either reach Neo4j's LIMIT clause as an invalid parameter
    (surfacing as a confusing "graph unavailable" fallback instead of a
    clear 422) or, in the haversine fallback, produce a nonsensical Python
    slice (`candidates[:-5]` drops from the end instead of limiting).
    """
    hp = get_or_create_hospital_profile(db, user)
    db.commit()
    # Gap elimination: `blood_type` is the PATIENT's type; sources are every
    # compatible type for `component`, counting only usable, unreserved,
    # in-date-on-arrival units at verified facilities.
    result = graph_service.find_resource_pathways(
        db,
        blood_type=blood_type,
        origin_facility_id=hp.id,
        max_results=max_results,
        component=component,
        units_needed=units_needed,
        urgency=urgency,
    )
    db.add(
        GraphPathwayLog(
            requested_by_user_id=user.id,
            emergency_id=None,
            origin_facility_id=hp.id,
            blood_type=blood_type,
            pathway_source=result.source,
            best_distance_km=result.best_distance_km,
            candidate_count=len(result.candidates),
            disrupted_routes_excluded=result.disrupted_routes_excluded,
            graph_available=result.graph_available,
        )
    )
    db.commit()
    return ResourcePathwayOut(
        blood_type=blood_type,
        origin_facility_id=hp.id,
        pathway_source=result.source,
        graph_available=result.graph_available,
        disrupted_routes_excluded=result.disrupted_routes_excluded,
        degraded_reason=result.degraded_reason,
        candidates=[PathwayCandidateOut(**vars(c)) for c in result.candidates],
        component=component.value,
        units_needed=units_needed,
    )


@router.get("/me/routes", response_model=list[HospitalRouteOut])
def my_transport_routes(
    user: User = Depends(_hospital), db: Session = Depends(get_db)
) -> list[HospitalRouteOut]:
    """Real transport links (app/models/transport.py) touching this
    facility -- the same rows an admin manages under
    /admin/transport-routes, exposed read-only. A hospital cannot create or
    disrupt a route (that stays an admin/demo-control action); it can only
    see what already connects to it and whether it's currently disrupted,
    for the Logistics view."""
    hp = get_or_create_hospital_profile(db, user)
    db.commit()
    rows = db.scalars(
        select(TransportRoute).where(
            or_(
                TransportRoute.origin_facility_id == hp.id,
                TransportRoute.destination_facility_id == hp.id,
            )
        )
    ).all()
    partner_ids = {
        (r.destination_facility_id if r.origin_facility_id == hp.id else r.origin_facility_id)
        for r in rows
    }
    partners = (
        {
            p.id: p
            for p in db.scalars(
                select(HospitalProfile).where(HospitalProfile.id.in_(partner_ids))
            ).all()
        }
        if partner_ids
        else {}
    )
    out: list[HospitalRouteOut] = []
    for r in rows:
        partner_id = (
            r.destination_facility_id if r.origin_facility_id == hp.id else r.origin_facility_id
        )
        partner = partners.get(partner_id)
        out.append(
            HospitalRouteOut(
                id=r.id,
                partner_facility_id=partner_id,
                partner_facility_name=partner.name if partner else None,
                partner_facility_city=partner.city if partner else None,
                distance_km=r.distance_km,
                transit_minutes=r.transit_minutes,
                status=r.status.value,
                disrupted_reason=r.disrupted_reason,
            )
        )
    return out


@router.get("/me/activity", response_model=list[HospitalActivityItemOut])
def recent_activity(
    limit: int = 25,
    user: User = Depends(_hospital),
    db: Session = Depends(get_db),
) -> list[HospitalActivityItemOut]:
    """Merged, real recent-activity feed for the dashboard: this hospital's
    own inventory-action log plus events on requests visible to it,
    interleaved by time. Both source tables are already real
    (HospitalInventoryLog, BloodRequestEvent) -- this endpoint only merges
    and caps them; it invents no activity of its own."""
    hp = get_or_create_hospital_profile(db, user)
    db.commit()
    capped = min(max(limit, 1), 100)

    items: list[HospitalActivityItemOut] = []
    for log in inventory_service.recent_log(db, hp.id, limit=capped):
        verb = log.action.value.lower()
        if log.delta_units:
            summary = f"Inventory {verb}: {abs(log.delta_units)} unit(s) of {log.blood_type.value}"
        else:
            summary = f"Inventory {verb}: {abs(log.delta_reserved)} unit(s) reserved change"
        items.append(
            HospitalActivityItemOut(
                kind="inventory",
                created_at=log.created_at,
                summary=summary,
                request_id=log.request_id,
                blood_type=log.blood_type,
            )
        )

    where = _visible_request_filter(hp)
    if where is not None:
        visible_ids = db.scalars(select(BloodRequest.id).where(where)).all()
        if visible_ids:
            events = db.scalars(
                select(BloodRequestEvent)
                .where(BloodRequestEvent.request_id.in_(visible_ids))
                .order_by(BloodRequestEvent.created_at.desc())
                .limit(capped)
            ).all()
            for e in events:
                items.append(
                    HospitalActivityItemOut(
                        kind="request",
                        created_at=e.created_at,
                        summary=e.note or e.event_type.replace("_", " "),
                        request_id=e.request_id,
                        blood_type=None,
                    )
                )

    items.sort(key=lambda i: i.created_at, reverse=True)
    return items[:capped]


# --------------------------------------------------------------------------- #
# Gap elimination: per-unit lifecycle (list, thaw, quarantine, release,
# discard). Every action is one identified unit, row-locked, audited, and
# keeps the aggregate counters equal to the per-unit truth.
# --------------------------------------------------------------------------- #
class BloodUnitOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    unit_code: str
    blood_type: BloodType
    component: BloodComponent
    state: BloodUnitState
    provenance: str
    received_at: datetime
    expires_at: datetime | None
    thawed_at: datetime | None
    thaw_expires_at: datetime | None
    effective_expires_at: datetime | None = None
    status_reason: str | None
    reserved_for_emergency_id: uuid.UUID | None
    reserved_for_request_id: uuid.UUID | None
    reserved_for_shipment_id: uuid.UUID | None


class UnitActionIn(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


class UnitThawIn(BaseModel):
    note: str | None = Field(default=None, max_length=300)


def _unit_out(u: BloodUnit) -> BloodUnitOut:
    out = BloodUnitOut.model_validate(u)
    out.provenance = u.provenance.value
    clocks = [t for t in (u.expires_at, u.thaw_expires_at) if t is not None]
    out.effective_expires_at = min(clocks) if clocks else None
    return out


@router.get("/me/units", response_model=list[BloodUnitOut])
def list_my_units(
    state: BloodUnitState | None = None,
    component: BloodComponent | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
    user: User = Depends(_hospital),
    db: Session = Depends(get_db),
) -> list[BloodUnitOut]:
    hp = get_or_create_hospital_profile(db, user)
    inventory_service.sweep_all_expired(db, hp.id)
    stmt = select(BloodUnit).where(BloodUnit.hospital_id == hp.id)
    if state is not None:
        stmt = stmt.where(BloodUnit.state == state)
    if component is not None:
        stmt = stmt.where(BloodUnit.component == component)
    rows = db.scalars(
        stmt.order_by(BloodUnit.expires_at.asc().nulls_last(), BloodUnit.unit_code).limit(limit)
    ).all()
    return [_unit_out(u) for u in rows]


def _unit_action(db: Session, user: User, unit_id: uuid.UUID, action: str, fn, **kwargs):
    hp = get_or_create_hospital_profile(db, user)
    try:
        unit = fn(db, hp.id, unit_id, actor_user_id=user.id, **kwargs)
    except InventoryActionError as exc:
        db.rollback()
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in str(exc).lower()
            else status.HTTP_409_CONFLICT
        )
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    audit_service.record(
        db,
        f"hospital.unit_{action}",
        actor_user_id=user.id,
        target_type="blood_unit",
        target_id=unit.id,
        meta={
            "unit_code": unit.unit_code,
            "state": unit.state.value,
            **{k: v for k, v in kwargs.items() if isinstance(v, str)},
        },
    )
    db.commit()
    db.refresh(unit)
    return _unit_out(unit)


@router.post("/me/units/{unit_id}/thaw", response_model=BloodUnitOut)
def thaw_unit(
    unit_id: uuid.UUID,
    payload: UnitThawIn,
    _: None = Depends(verify_origin),
    user: User = Depends(_hospital),
    db: Session = Depends(get_db),
) -> BloodUnitOut:
    return _unit_action(
        db, user, unit_id, "thawed", inventory_service.thaw_plasma_unit, note=payload.note
    )


@router.post("/me/units/{unit_id}/quarantine", response_model=BloodUnitOut)
def quarantine_unit(
    unit_id: uuid.UUID,
    payload: UnitActionIn,
    _: None = Depends(verify_origin),
    user: User = Depends(_hospital),
    db: Session = Depends(get_db),
) -> BloodUnitOut:
    return _unit_action(
        db, user, unit_id, "quarantined", inventory_service.quarantine_unit, reason=payload.reason
    )


@router.post("/me/units/{unit_id}/release-quarantine", response_model=BloodUnitOut)
def release_quarantine(
    unit_id: uuid.UUID,
    payload: UnitActionIn,
    _: None = Depends(verify_origin),
    user: User = Depends(_hospital),
    db: Session = Depends(get_db),
) -> BloodUnitOut:
    return _unit_action(
        db,
        user,
        unit_id,
        "quarantine_released",
        inventory_service.release_from_quarantine,
        reason=payload.reason,
    )


@router.post("/me/units/{unit_id}/discard", response_model=BloodUnitOut)
def discard_unit(
    unit_id: uuid.UUID,
    payload: UnitActionIn,
    _: None = Depends(verify_origin),
    user: User = Depends(_hospital),
    db: Session = Depends(get_db),
) -> BloodUnitOut:
    return _unit_action(
        db, user, unit_id, "discarded", inventory_service.discard_unit, reason=payload.reason
    )
