"""Phase D -- shipment / transport / chain-of-custody REST API.

Orchestration lives here, not in a separate domain service module -- the
same convention `app.api.routes.emergencies`/`app.api.routes.hospitals`
already use for BloodRequest/Emergency: `app.core.shipment_lifecycle`
(pure transition table) and `app.services.shipment_service` (the two DB
primitives -- row lock + validated transition, see that module) are the
only shipment-specific helpers; every real effect is delegated to the
existing cross-domain services exactly like every other mutating route in
this codebase does:

  * `app.services.inventory_service`     -- reserve/release/allocate/
                                             receive_transferred_units.
  * `app.services.notification_service`  -- in-app/email/SMS notifications.
  * `app.services.audit_service`         -- the admin-facing AuditLog trail
                                             (also written by
                                             shipment_service.transition).

No second idempotency mechanism, no second event bus, no second audit
table -- see the Phase D master prompt's own "NO SHORTCUTS" section.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_roles, verify_origin
from app.core.shipment_lifecycle import (
    InvalidShipmentTransitionError,
    allowed_transitions,
    can_report_delay,
)
from app.core.time import utcnow
from app.models.blood_unit import BloodUnit
from app.models.enums import (
    BloodUnitState,
    ColdChainStatus,
    ShipmentItemOutcome,
    ShipmentReconciliationStatus,
    ShipmentStatus,
)
from app.models.hospital import HospitalProfile
from app.models.shipment import Shipment, ShipmentEvent, ShipmentItem
from app.models.transport import TransportRoute
from app.models.user import User, UserRole
from app.schemas.shipment import (
    ShipmentActionIn,
    ShipmentCreateIn,
    ShipmentDelayIn,
    ShipmentEventOut,
    ShipmentItemOut,
    ShipmentOut,
    ShipmentReceiveIn,
    ShipmentReconcileIn,
    TelemetryIn,
    TelemetryOut,
    TelemetrySimulateIn,
    UnitTraceOut,
)
from app.services import (
    audit_service,
    cold_chain_service,
    inventory_service,
    notification_service,
    shipment_service,
)
from app.services.domain_service import get_or_create_hospital_profile
from app.services.inventory_service import InventoryActionError

router = APIRouter(prefix="/shipments", tags=["shipments"])

_actor = require_roles(UserRole.HOSPITAL, UserRole.ADMIN)


# --------------------------------------------------------------------------- #
# Authorization / facility-resolution helpers
# --------------------------------------------------------------------------- #


def _actor_facility(db: Session, user: User) -> HospitalProfile | None:
    """`None` for an ADMIN (acts across every facility); the caller's own
    `HospitalProfile` for a HOSPITAL user."""
    if user.role == UserRole.HOSPITAL:
        return get_or_create_hospital_profile(db, user)
    return None


def _assert_is_party(shipment: Shipment, hp: HospitalProfile | None) -> None:
    if hp is not None and hp.id not in (
        shipment.origin_facility_id,
        shipment.destination_facility_id,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This shipment does not involve your facility",
        )


def _assert_is_origin(shipment: Shipment, hp: HospitalProfile | None) -> None:
    if hp is not None and hp.id != shipment.origin_facility_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the origin facility (or an admin) can do this",
        )


def _assert_is_destination(shipment: Shipment, hp: HospitalProfile | None) -> None:
    if hp is not None and hp.id != shipment.destination_facility_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the destination facility (or an admin) can do this",
        )


def _validate_facility_exists(
    db: Session, facility_id: uuid.UUID, field_name: str
) -> HospitalProfile:
    facility = db.get(HospitalProfile, facility_id)
    if facility is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} not found"
        )
    return facility


def _validate_destination_facility(db: Session, facility_id: uuid.UUID) -> HospitalProfile:
    """Mirrors `emergencies._validate_target_facility` exactly: a facility
    being NAMED (as opposed to the actor's own facility, whose standing is
    the actor's own account's concern) must be real and verified."""
    facility = _validate_facility_exists(db, facility_id, "destination_facility_id")
    if not facility.verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="destination_facility_id is not a verified facility",
        )
    return facility


# --------------------------------------------------------------------------- #
# Response assembly
# --------------------------------------------------------------------------- #


def _find_route(db: Session, a: uuid.UUID, b: uuid.UUID) -> TransportRoute | None:
    """Undirected lookup -- mirrors `hospitals.my_transport_routes`."""
    return db.scalar(
        select(TransportRoute).where(
            or_(
                (TransportRoute.origin_facility_id == a)
                & (TransportRoute.destination_facility_id == b),
                (TransportRoute.origin_facility_id == b)
                & (TransportRoute.destination_facility_id == a),
            )
        )
    )


def _item_out(item: ShipmentItem, unit: BloodUnit | None) -> ShipmentItemOut:
    return ShipmentItemOut(
        id=item.id,
        blood_unit_id=item.blood_unit_id,
        is_active=item.is_active,
        unit_code=unit.unit_code if unit else None,
        blood_type=unit.blood_type if unit else None,
        component=unit.component if unit else None,
        outcome=item.outcome,
        outcome_reason=item.outcome_reason,
        outcome_at=item.outcome_at,
        resulting_unit_id=item.resulting_unit_id,
    )


def _to_out(db: Session, shipment: Shipment) -> ShipmentOut:
    facilities = {
        f.id: f
        for f in db.scalars(
            select(HospitalProfile).where(
                HospitalProfile.id.in_(
                    [shipment.origin_facility_id, shipment.destination_facility_id]
                )
            )
        ).all()
    }
    items = list(
        db.scalars(select(ShipmentItem).where(ShipmentItem.shipment_id == shipment.id)).all()
    )
    units = (
        {
            u.id: u
            for u in db.scalars(
                select(BloodUnit).where(BloodUnit.id.in_([i.blood_unit_id for i in items]))
            ).all()
        }
        if items
        else {}
    )
    route = _find_route(db, shipment.origin_facility_id, shipment.destination_facility_id)
    out = ShipmentOut.model_validate(shipment)
    origin = facilities.get(shipment.origin_facility_id)
    destination = facilities.get(shipment.destination_facility_id)
    out.origin_facility_name = origin.name if origin else None
    out.destination_facility_name = destination.name if destination else None
    out.allowed_transitions = allowed_transitions(shipment.status)
    out.route_status = route.status.value if route else None
    out.route_disrupted_reason = route.disrupted_reason if route else None
    out.items = [_item_out(i, units.get(i.blood_unit_id)) for i in items]
    return out


# --------------------------------------------------------------------------- #
# Create
# --------------------------------------------------------------------------- #


def _shipment_payload_hash(payload: ShipmentCreateIn) -> str:
    """SHA-256 of the fields that define what this shipment actually
    requests -- mirrors `donors._donation_payload_hash` /
    `blood_requests._idempotency_payload_hash` exactly. Deliberately
    excludes the idempotency_key itself."""
    canonical = {
        "origin_facility_id": str(payload.origin_facility_id),
        "destination_facility_id": str(payload.destination_facility_id),
        "transport_route_id": str(payload.transport_route_id)
        if payload.transport_route_id
        else None,
        "items": sorted(
            [
                {
                    "blood_type": line.blood_type.value,
                    "component": line.component.value,
                    "units": line.units,
                }
                for line in payload.items
            ],
            key=lambda d: (d["blood_type"], d["component"], d["units"]),
        ),
    }
    blob = json.dumps(canonical, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _existing_shipment_by_idempotency_key(db: Session, user: User, key: str) -> Shipment | None:
    return db.scalar(
        select(Shipment).where(
            Shipment.created_by_user_id == user.id,
            Shipment.idempotency_key == key,
        )
    )


@router.post("", response_model=ShipmentOut, status_code=status.HTTP_201_CREATED)
def create_shipment(
    payload: ShipmentCreateIn,
    response: Response,
    _: None = Depends(verify_origin),
    user: User = Depends(_actor),
    db: Session = Depends(get_db),
) -> ShipmentOut:
    """Create a shipment and RESERVE (not yet allocate) the specific
    origin-side units it will carry -- see `ShipmentStatus`'s docstring
    for why CREATED means RESERVED, not ALLOCATED, and
    `Shipment.__table_args__` for the idempotency pattern (Section 11)."""
    if payload.origin_facility_id == payload.destination_facility_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="origin_facility_id and destination_facility_id must differ",
        )
    _validate_facility_exists(db, payload.origin_facility_id, "origin_facility_id")
    _validate_destination_facility(db, payload.destination_facility_id)
    hp = _actor_facility(db, user)
    if hp is not None and hp.id != payload.origin_facility_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only create a shipment from your own facility",
        )
    if payload.transport_route_id is not None:
        route = db.get(TransportRoute, payload.transport_route_id)
        if route is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="transport_route_id not found"
            )

    # Gap elimination: one shipment = one storage temperature class.
    try:
        temp_min_c, temp_max_c = cold_chain_service.range_for_components(
            {line.component for line in payload.items}
        )
    except cold_chain_service.TelemetryError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    payload_hash = _shipment_payload_hash(payload)
    if payload.idempotency_key is not None:
        existing = _existing_shipment_by_idempotency_key(db, user, payload.idempotency_key)
        if existing is not None:
            if existing.idempotency_payload_hash != payload_hash:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="This idempotency key was already used with a different shipment.",
                )
            response.status_code = status.HTTP_200_OK
            return _to_out(db, existing)

    def _build_shipment() -> Shipment:
        return Shipment(
            shipment_code=shipment_service.generate_shipment_code(),
            origin_facility_id=payload.origin_facility_id,
            destination_facility_id=payload.destination_facility_id,
            transport_route_id=payload.transport_route_id,
            status=ShipmentStatus.CREATED,
            created_by_user_id=user.id,
            note=payload.note,
            idempotency_key=payload.idempotency_key,
            idempotency_payload_hash=payload_hash if payload.idempotency_key is not None else None,
            temp_min_c=temp_min_c,
            temp_max_c=temp_max_c,
            cold_chain_status=ColdChainStatus.NO_DATA,
        )

    if payload.idempotency_key is not None:
        # Race-safe insert -- see donors.log_donation's identical pattern.
        savepoint = db.begin_nested()
        try:
            shipment = _build_shipment()
            db.add(shipment)
            db.flush()
            savepoint.commit()
        except IntegrityError:
            savepoint.rollback()
            existing = _existing_shipment_by_idempotency_key(db, user, payload.idempotency_key)
            if existing is None:
                raise  # pragma: no cover - constraint violation implies a row exists
            if existing.idempotency_payload_hash != payload_hash:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="This idempotency key was already used with a different shipment.",
                ) from None
            response.status_code = status.HTTP_200_OK
            return _to_out(db, existing)
    else:
        shipment = _build_shipment()
        db.add(shipment)
        db.flush()

    try:
        for line in payload.items:
            inventory_service.reserve_stock(
                db,
                payload.origin_facility_id,
                line.blood_type,
                line.units,
                actor_user_id=user.id,
                shipment_id=shipment.id,
                component=line.component,
                note=f"Reserved for shipment {shipment.shipment_code}",
            )
    except InventoryActionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    reserved_units = list(
        db.scalars(select(BloodUnit).where(BloodUnit.reserved_for_shipment_id == shipment.id)).all()
    )
    db.add_all([ShipmentItem(shipment_id=shipment.id, blood_unit_id=u.id) for u in reserved_units])
    db.flush()

    db.add(
        ShipmentEvent(
            shipment_id=shipment.id,
            actor_user_id=user.id,
            event_type="created",
            note=f"Created with {len(reserved_units)} unit(s) reserved from origin",
        )
    )
    audit_service.record(
        db,
        "shipment.created",
        actor_user_id=user.id,
        target_type="shipment",
        target_id=shipment.id,
        meta={
            "origin_facility_id": str(payload.origin_facility_id),
            "destination_facility_id": str(payload.destination_facility_id),
            "units": len(reserved_units),
        },
    )
    destination = db.get(HospitalProfile, payload.destination_facility_id)
    origin = db.get(HospitalProfile, payload.origin_facility_id)
    if destination is not None:
        notification_service.notify(
            db,
            destination.user_id,
            type="shipment.created",
            title=f"Incoming shipment {shipment.shipment_code}",
            body=f"{origin.name if origin else 'A facility'} is preparing "
            f"{len(reserved_units)} unit(s) for you.",
            data={"shipment_id": str(shipment.id), "shipment_code": shipment.shipment_code},
        )
    db.commit()
    db.refresh(shipment)
    return _to_out(db, shipment)


# --------------------------------------------------------------------------- #
# Lifecycle actions
# --------------------------------------------------------------------------- #


def _active_items_with_units(
    db: Session, shipment_id: uuid.UUID
) -> list[tuple[ShipmentItem, BloodUnit]]:
    items = list(
        db.scalars(
            select(ShipmentItem)
            .where(
                ShipmentItem.shipment_id == shipment_id,
                ShipmentItem.is_active.is_(True),
            )
            .with_for_update()
        ).all()
    )
    if not items:
        return []
    units = {
        u.id: u
        for u in db.scalars(
            select(BloodUnit).where(BloodUnit.id.in_([i.blood_unit_id for i in items]))
        ).all()
    }
    return [(i, units[i.blood_unit_id]) for i in items if i.blood_unit_id in units]


def _deactivate_items(db: Session, shipment_id: uuid.UUID) -> None:
    """Flip every active item inactive via the ORM (not a Core bulk
    `UPDATE`) so already-loaded `ShipmentItem` objects in this same
    session -- e.g. the ones `_active_items_with_units` just fetched and
    locked above -- are mutated in place and stay consistent with what a
    subsequent `select(ShipmentItem)` in this same request (building the
    response) sees; a Core bulk update bypasses the session's identity
    map and would leave those in-memory objects stale."""
    items = db.scalars(
        select(ShipmentItem).where(
            ShipmentItem.shipment_id == shipment_id,
            ShipmentItem.is_active.is_(True),
        )
    ).all()
    for item in items:
        item.is_active = False
    db.flush()


def _group_by_type_component(pairs: list[tuple[ShipmentItem, BloodUnit]]) -> dict[tuple, int]:
    groups: dict[tuple, int] = {}
    for _item, unit in pairs:
        key = (unit.blood_type, unit.component)
        groups[key] = groups.get(key, 0) + 1
    return groups


@router.post("/{shipment_id}/dispatch", response_model=ShipmentOut)
def dispatch_shipment(
    shipment_id: uuid.UUID,
    payload: ShipmentActionIn,
    _: None = Depends(verify_origin),
    user: User = Depends(_actor),
    db: Session = Depends(get_db),
) -> ShipmentOut:
    """Physical departure from the origin. Units move RESERVED ->
    ALLOCATED (terminal, origin-side) -- see Sections 5/7. Preconditions
    (shipment exists, CREATED, actor authorized, has units, not already
    dispatched) are all re-checked here transactionally, under the row
    lock -- never trusted from the frontend (Section 5)."""
    shipment = shipment_service.get_for_update(db, shipment_id)
    if shipment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shipment not found")
    hp = _actor_facility(db, user)
    _assert_is_origin(shipment, hp)

    try:
        shipment_service.transition(
            db,
            shipment,
            ShipmentStatus.DISPATCHED,
            actor_user_id=user.id,
            reason=payload.reason,
            event_type="dispatched",
        )
    except InvalidShipmentTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    pairs = _active_items_with_units(db, shipment.id)
    if not pairs:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This shipment has no active reserved units to dispatch",
        )
    try:
        for (blood_type, component), count in _group_by_type_component(pairs).items():
            inventory_service.allocate_stock(
                db,
                shipment.origin_facility_id,
                blood_type,
                count,
                actor_user_id=user.id,
                shipment_id=shipment.id,
                component=component,
                note=f"Dispatched on shipment {shipment.shipment_code}",
            )
    except InventoryActionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    destination = db.get(HospitalProfile, shipment.destination_facility_id)
    if destination is not None:
        notification_service.notify(
            db,
            destination.user_id,
            type="shipment.dispatched",
            title=f"Shipment {shipment.shipment_code} dispatched",
            body="Your incoming shipment has physically left the origin facility.",
            data={"shipment_id": str(shipment.id), "shipment_code": shipment.shipment_code},
        )
    db.commit()
    db.refresh(shipment)
    return _to_out(db, shipment)


@router.post("/{shipment_id}/in-transit", response_model=ShipmentOut)
def mark_in_transit(
    shipment_id: uuid.UUID,
    payload: ShipmentActionIn,
    _: None = Depends(verify_origin),
    user: User = Depends(_actor),
    db: Session = Depends(get_db),
) -> ShipmentOut:
    shipment = shipment_service.get_for_update(db, shipment_id)
    if shipment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shipment not found")
    hp = _actor_facility(db, user)
    _assert_is_party(shipment, hp)
    try:
        shipment_service.transition(
            db,
            shipment,
            ShipmentStatus.IN_TRANSIT,
            actor_user_id=user.id,
            reason=payload.reason,
            event_type="in_transit",
        )
    except InvalidShipmentTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    if shipment.estimated_arrival_at is None:
        route = _find_route(db, shipment.origin_facility_id, shipment.destination_facility_id)
        if route is not None:
            # Explicitly labeled an estimate (Section 17) -- a static
            # figure derived from the registered route's transit_minutes,
            # never a live-GPS/fleet-tracking computation (none exists in
            # this product).
            shipment.estimated_arrival_at = utcnow() + timedelta(minutes=route.transit_minutes)

    destination = db.get(HospitalProfile, shipment.destination_facility_id)
    if destination is not None:
        notification_service.notify(
            db,
            destination.user_id,
            type="shipment.in_transit",
            title=f"Shipment {shipment.shipment_code} is in transit",
            body="Your incoming shipment is now on its way.",
            data={"shipment_id": str(shipment.id), "shipment_code": shipment.shipment_code},
        )
    db.commit()
    db.refresh(shipment)
    return _to_out(db, shipment)


@router.post("/{shipment_id}/delay", response_model=ShipmentOut)
def report_delay(
    shipment_id: uuid.UUID,
    payload: ShipmentDelayIn,
    _: None = Depends(verify_origin),
    user: User = Depends(_actor),
    db: Session = Depends(get_db),
) -> ShipmentOut:
    """DELAYED is an annotation, not a status transition (see
    `ShipmentStatus`'s docstring) -- legal only while DISPATCHED or
    IN_TRANSIT (`shipment_lifecycle.can_report_delay`). The full history of
    every delay report lives in `ShipmentEvent`, never overwritten; only
    the *most recent* report is reflected on `Shipment.delayed_at`/
    `delay_reason`."""
    shipment = shipment_service.get_for_update(db, shipment_id)
    if shipment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shipment not found")
    hp = _actor_facility(db, user)
    _assert_is_party(shipment, hp)
    if not can_report_delay(shipment.status):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot report a delay for a shipment in status {shipment.status.value}",
        )
    shipment.delayed_at = utcnow()
    shipment.delay_reason = payload.reason
    if payload.new_estimated_arrival_at is not None:
        shipment.estimated_arrival_at = payload.new_estimated_arrival_at
    db.flush()
    db.add(
        ShipmentEvent(
            shipment_id=shipment.id,
            actor_user_id=user.id,
            event_type="delayed",
            note=payload.reason,
        )
    )
    audit_service.record(
        db,
        "shipment.delayed",
        actor_user_id=user.id,
        target_type="shipment",
        target_id=shipment.id,
        meta={"reason": payload.reason},
    )
    for facility_id in {shipment.origin_facility_id, shipment.destination_facility_id}:
        facility = db.get(HospitalProfile, facility_id)
        if facility is not None:
            notification_service.notify(
                db,
                facility.user_id,
                type="shipment.delayed",
                title=f"Shipment {shipment.shipment_code} delayed",
                body=payload.reason,
                data={"shipment_id": str(shipment.id), "shipment_code": shipment.shipment_code},
            )
    db.commit()
    db.refresh(shipment)
    return _to_out(db, shipment)


@router.post("/{shipment_id}/arrive", response_model=ShipmentOut)
def mark_arrived(
    shipment_id: uuid.UUID,
    payload: ShipmentActionIn,
    _: None = Depends(verify_origin),
    user: User = Depends(_actor),
    db: Session = Depends(get_db),
) -> ShipmentOut:
    shipment = shipment_service.get_for_update(db, shipment_id)
    if shipment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shipment not found")
    hp = _actor_facility(db, user)
    _assert_is_party(shipment, hp)
    try:
        shipment_service.transition(
            db,
            shipment,
            ShipmentStatus.ARRIVED,
            actor_user_id=user.id,
            reason=payload.reason,
            event_type="arrived",
        )
    except InvalidShipmentTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    destination = db.get(HospitalProfile, shipment.destination_facility_id)
    if destination is not None:
        notification_service.notify(
            db,
            destination.user_id,
            type="shipment.arrived",
            title=f"Shipment {shipment.shipment_code} has arrived",
            body="Your incoming shipment has reached the facility -- ready to receive.",
            data={"shipment_id": str(shipment.id), "shipment_code": shipment.shipment_code},
        )
    db.commit()
    db.refresh(shipment)
    return _to_out(db, shipment)


@router.post("/{shipment_id}/receive", response_model=ShipmentOut)
def receive_shipment(
    shipment_id: uuid.UUID,
    payload: ShipmentReceiveIn,
    _: None = Depends(verify_origin),
    user: User = Depends(_actor),
    db: Session = Depends(get_db),
) -> ShipmentOut:
    """Destination formally takes custody -- the real inventory-ownership
    transfer (Section 7): new destination-side `BloodUnit` rows are
    created via `inventory_service.receive_transferred_units`. Idempotent
    against a duplicate call: the first call's `transition()` moves status
    to RECEIVED (terminal); a later call gets a clean 409.

    Gap elimination -- every carried unit gets its own recorded outcome:
      * RECEIVED_OK  -> AVAILABLE at the destination (EXPIRED_ON_ARRIVAL if
                        its effective expiry already passed);
      * QUARANTINED  -> received but held back (not counted as stock);
      * DAMAGED / SPOILED -> received and immediately DISCARDED (custody
                        recorded, never stocked);
      * LOST         -> no destination unit at all.
    Units not listed default to RECEIVED_OK -- or to QUARANTINED when the
    shipment's telemetry shows an excursion, a sensor failure or a data
    gap: blood with a doubtful cold chain is never silently stocked."""
    shipment = shipment_service.get_for_update(db, shipment_id)
    if shipment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shipment not found")
    hp = _actor_facility(db, user)
    _assert_is_destination(shipment, hp)

    try:
        shipment_service.transition(
            db,
            shipment,
            ShipmentStatus.RECEIVED,
            actor_user_id=user.id,
            reason=payload.reason,
            event_type="received",
        )
    except InvalidShipmentTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    report = cold_chain_service.evaluate_and_store(db, shipment)
    doubtful = report.status in cold_chain_service.BAD_STATUSES

    pairs = _active_items_with_units(db, shipment.id)
    by_code = {unit.unit_code: (item, unit) for item, unit in pairs}
    requested: dict[str, tuple[ShipmentItemOutcome, str | None]] = {}
    for line in payload.items:
        if line.unit_code not in by_code:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Unit {line.unit_code} is not carried by this shipment",
            )
        if line.unit_code in requested:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Unit {line.unit_code} listed twice",
            )
        outcome = ShipmentItemOutcome(line.outcome)
        if outcome != ShipmentItemOutcome.RECEIVED_OK and not line.reason:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"A reason is required for {outcome.value}",
            )
        if outcome == ShipmentItemOutcome.RECEIVED_OK and doubtful:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Cold chain is {report.status.value}; units cannot be accepted "
                    "straight into stock. Receive them as QUARANTINED for review."
                ),
            )
        requested[line.unit_code] = (outcome, line.reason)
    default = (
        (ShipmentItemOutcome.QUARANTINED, f"cold chain {report.status.value}: held for review")
        if doubtful
        else (ShipmentItemOutcome.RECEIVED_OK, None)
    )
    decided = {code: requested.get(code, default) for code in by_code}

    to_receive = [
        unit
        for code, (item, unit) in by_code.items()
        if decided[code][0] != ShipmentItemOutcome.LOST
    ]
    created = inventory_service.receive_transferred_units(
        db,
        shipment.destination_facility_id,
        to_receive,
        actor_user_id=user.id,
        shipment_id=shipment.id,
        note=f"Received via shipment {shipment.shipment_code}",
    )
    new_by_source = {u.source_unit_id: u for u in created}
    now = utcnow()
    counts: dict[str, int] = {}
    for code, (item, unit) in by_code.items():
        outcome, reason = decided[code]
        new_unit = new_by_source.get(unit.id)
        if new_unit is not None and new_unit.state == BloodUnitState.EXPIRED:
            outcome, reason = ShipmentItemOutcome.EXPIRED_ON_ARRIVAL, "past expiry on arrival"
        elif new_unit is not None and outcome == ShipmentItemOutcome.QUARANTINED:
            inventory_service.quarantine_unit(
                db,
                shipment.destination_facility_id,
                new_unit.id,
                actor_user_id=user.id,
                reason=reason or "quarantined at receipt",
            )
        elif new_unit is not None and outcome in (
            ShipmentItemOutcome.DAMAGED,
            ShipmentItemOutcome.SPOILED,
        ):
            inventory_service.discard_unit(
                db,
                shipment.destination_facility_id,
                new_unit.id,
                actor_user_id=user.id,
                reason=f"{outcome.value.lower()} at receipt: {reason}",
            )
        item.outcome = outcome
        item.outcome_reason = reason
        item.outcome_at = now
        item.outcome_by_user_id = user.id
        item.resulting_unit_id = new_unit.id if new_unit is not None else None
        counts[outcome.value] = counts.get(outcome.value, 0) + 1
    _deactivate_items(db, shipment.id)
    db.add(
        ShipmentEvent(
            shipment_id=shipment.id,
            actor_user_id=user.id,
            event_type="receipt_outcomes",
            note=", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
            + f" (cold chain {report.status.value})",
        )
    )
    audit_service.record(
        db,
        "shipment.receipt_outcomes",
        actor_user_id=user.id,
        target_type="shipment",
        target_id=shipment.id,
        meta={"outcomes": counts, "cold_chain_status": report.status.value},
    )

    origin = db.get(HospitalProfile, shipment.origin_facility_id)
    if origin is not None:
        notification_service.notify(
            db,
            origin.user_id,
            type="shipment.received",
            title=f"Shipment {shipment.shipment_code} received",
            body=(
                "The destination facility recorded receipt: "
                + ", ".join(f"{v} {k.replace('_', ' ').lower()}" for k, v in sorted(counts.items()))
                + "."
            ),
            data={"shipment_id": str(shipment.id), "shipment_code": shipment.shipment_code},
        )
    db.commit()
    db.refresh(shipment)
    return _to_out(db, shipment)


@router.post("/{shipment_id}/cancel", response_model=ShipmentOut)
def cancel_shipment(
    shipment_id: uuid.UUID,
    payload: ShipmentActionIn,
    _: None = Depends(verify_origin),
    user: User = Depends(_actor),
    db: Session = Depends(get_db),
) -> ShipmentOut:
    """Only legal from CREATED -- before physical departure, so releasing
    the reservation back to AVAILABLE is safe (Section 13: "do not
    automatically release inventory if that could create an unsafe
    double-allocation" -- it cannot here, because nothing has been
    ALLOCATED yet)."""
    shipment = shipment_service.get_for_update(db, shipment_id)
    if shipment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shipment not found")
    hp = _actor_facility(db, user)
    _assert_is_origin(shipment, hp)

    try:
        shipment_service.transition(
            db,
            shipment,
            ShipmentStatus.CANCELLED,
            actor_user_id=user.id,
            reason=payload.reason,
            event_type="cancelled",
        )
    except InvalidShipmentTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    shipment.cancel_reason = payload.reason

    pairs = _active_items_with_units(db, shipment.id)
    try:
        for (blood_type, component), count in _group_by_type_component(pairs).items():
            inventory_service.release_reservation(
                db,
                shipment.origin_facility_id,
                blood_type,
                count,
                actor_user_id=user.id,
                shipment_id=shipment.id,
                component=component,
                note=f"Released -- shipment {shipment.shipment_code} cancelled",
            )
    except InventoryActionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    _deactivate_items(db, shipment.id)
    # Audit C20 (2026-10-31): a cancelled emergency shipment re-plans its
    # emergency (the released units may be re-reserved by the new plan).
    from app.services import pathway_handoff_service

    pathway_handoff_service.handle_linked_shipment_outcome(
        db, shipment, outcome="cancelled", reason=payload.reason, actor_user_id=user.id,
    )

    destination = db.get(HospitalProfile, shipment.destination_facility_id)
    if destination is not None:
        notification_service.notify(
            db,
            destination.user_id,
            type="shipment.cancelled",
            title=f"Shipment {shipment.shipment_code} cancelled",
            body=payload.reason or "The origin facility cancelled this shipment.",
            data={"shipment_id": str(shipment.id), "shipment_code": shipment.shipment_code},
        )
    db.commit()
    db.refresh(shipment)
    return _to_out(db, shipment)


@router.post("/{shipment_id}/fail", response_model=ShipmentOut)
def fail_shipment(
    shipment_id: uuid.UUID,
    payload: ShipmentActionIn,
    _: None = Depends(verify_origin),
    user: User = Depends(_actor),
    db: Session = Depends(get_db),
) -> ShipmentOut:
    """Legal only from DISPATCHED/IN_TRANSIT/ARRIVED -- something went
    wrong after physical departure. Deliberately does NOT release or
    return the origin's ALLOCATED units (Section 14: "never silently
    return blood to AVAILABLE") -- this is a disclosed, real limitation: a
    failed shipment requires a separate, explicit reconciliation action
    (e.g. a hospital admin re-adding genuinely-recovered stock via the
    existing ADD action with a note) rather than an automatic reversal
    this system cannot honestly verify actually happened."""
    shipment = shipment_service.get_for_update(db, shipment_id)
    if shipment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shipment not found")
    hp = _actor_facility(db, user)
    _assert_is_party(shipment, hp)

    try:
        shipment_service.transition(
            db,
            shipment,
            ShipmentStatus.FAILED,
            actor_user_id=user.id,
            reason=payload.reason,
            event_type="failed",
        )
    except InvalidShipmentTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    shipment.failure_reason = payload.reason
    # Phase E: a failed shipment now carries an explicit, visible "a
    # decision about these stranded units is owed" marker instead of the
    # Phase D gap of nothing at all -- see reconcile_shipment below.
    shipment.reconciliation_status = ShipmentReconciliationStatus.REQUIRED
    db.flush()
    _deactivate_items(db, shipment.id)
    # Audit C20 (2026-10-31): the stranded units still go to reconciliation
    # (never silently restocked), and an emergency shipment now also
    # re-plans its emergency for the units still missing.
    from app.services import pathway_handoff_service

    pathway_handoff_service.handle_linked_shipment_outcome(
        db, shipment, outcome="failed", reason=payload.reason, actor_user_id=user.id,
    )

    for facility_id in {shipment.origin_facility_id, shipment.destination_facility_id}:
        facility = db.get(HospitalProfile, facility_id)
        if facility is not None:
            notification_service.notify(
                db,
                facility.user_id,
                type="shipment.failed",
                title=f"Shipment {shipment.shipment_code} failed",
                body=payload.reason or "This shipment could not be completed.",
                data={"shipment_id": str(shipment.id), "shipment_code": shipment.shipment_code},
            )
    db.commit()
    db.refresh(shipment)
    return _to_out(db, shipment)


@router.post("/{shipment_id}/reconcile", response_model=ShipmentOut)
def reconcile_shipment(
    shipment_id: uuid.UUID,
    payload: ShipmentReconcileIn,
    _: None = Depends(verify_origin),
    user: User = Depends(_actor),
    db: Session = Depends(get_db),
) -> ShipmentOut:
    """Phase E -- the single, explicit decision about a FAILED shipment's
    stranded units (origin-side, ALLOCATED, terminal). The safety policy:

      * Who: the ORIGIN facility (whose stock it was) or an admin. Never
        the destination -- it cannot decide what re-enters someone else's
        inventory.
      * When: only while `status == FAILED` and `reconciliation_status ==
        REQUIRED`. Exactly once; a second decision is a 409 (row-locked,
        so two concurrent reconcilers cannot both restock).
      * RETURNED_TO_ORIGIN restocks ONLY with a human attestation of both
        physical return (every carried unit code echoed back -- partial
        returns are refused; use QUARANTINED and record the details) and
        `cold_chain_maintained=true`. Units re-enter as NEW origin-side
        rows linked to the originals (`source_unit_id`,
        `source_shipment_id` = this shipment) via the same
        `receive_transferred_units` path Phase D receipt uses -- keeping
        each unit's ORIGINAL `expires_at` (no fresh shelf-life clock) and
        putting any unit already past expiry straight into EXPIRED, never
        AVAILABLE. The original ALLOCATED rows are never mutated, so
        ALLOCATED stays terminal and nothing is "un-allocated".
      * REDIRECTED / QUARANTINED: recorded and audited; no inventory change.
        Units remain ALLOCATED at origin -- truthful, they were dispatched.
      * Never automatic: nothing calls this except this endpoint.
    """
    shipment = shipment_service.get_for_update(db, shipment_id)
    if shipment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shipment not found")
    hp = _actor_facility(db, user)
    _assert_is_origin(shipment, hp)

    if shipment.status != ShipmentStatus.FAILED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only a FAILED shipment can be reconciled",
        )
    if shipment.reconciliation_status != ShipmentReconciliationStatus.REQUIRED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This shipment was already reconciled "
                f"({getattr(shipment.reconciliation_status, 'value', 'n/a')})"
            ),
        )
    if payload.outcome == ShipmentReconciliationStatus.REQUIRED:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="outcome must be RETURNED_TO_ORIGIN, REDIRECTED or QUARANTINED",
        )

    items = list(
        db.scalars(select(ShipmentItem).where(ShipmentItem.shipment_id == shipment.id)).all()
    )
    units = (
        list(
            db.scalars(
                select(BloodUnit)
                .where(BloodUnit.id.in_([i.blood_unit_id for i in items]))
                .with_for_update()
            ).all()
        )
        if items
        else []
    )
    carried_codes = sorted(u.unit_code for u in units)

    restocked = 0
    expired_on_return = 0
    if payload.outcome == ShipmentReconciliationStatus.RETURNED_TO_ORIGIN:
        if not payload.cold_chain_maintained:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    "Units can only be returned to stock with an explicit "
                    "cold_chain_maintained=true attestation; otherwise reconcile "
                    "as QUARANTINED"
                ),
            )
        # Gap elimination: an attestation cannot override recorded evidence.
        report = cold_chain_service.evaluate_and_store(db, shipment)
        if report.status in cold_chain_service.BAD_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f"Recorded telemetry shows {report.status.value}; these units cannot "
                    "be returned to stock -- reconcile as QUARANTINED"
                ),
            )
        if sorted(set(payload.unit_codes)) != carried_codes or len(payload.unit_codes) != len(
            carried_codes
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    "unit_codes must list exactly every unit this shipment carried "
                    "(partial returns are not restocked -- reconcile as QUARANTINED "
                    "and record which units came back)"
                ),
            )
        returned = inventory_service.receive_transferred_units(
            db,
            shipment.origin_facility_id,
            units,
            actor_user_id=user.id,
            shipment_id=shipment.id,
            note=(
                f"Returned to origin after failed shipment {shipment.shipment_code} "
                "(reconciliation, cold chain attested)"
            ),
        )
        restocked = sum(1 for u in returned if u.state.value == "AVAILABLE")
        by_src = {u.source_unit_id: u.id for u in returned}
        for item in items:
            item.resulting_unit_id = by_src.get(item.blood_unit_id)
        expired_on_return = len(returned) - restocked
    elif payload.redirected_to_facility_id is not None:
        if payload.outcome != ShipmentReconciliationStatus.REDIRECTED:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="redirected_to_facility_id is only valid for REDIRECTED",
            )
        _validate_facility_exists(
            db, payload.redirected_to_facility_id, "redirected_to_facility_id"
        )

    item_outcome = {
        ShipmentReconciliationStatus.RETURNED_TO_ORIGIN: ShipmentItemOutcome.RETURNED,
        ShipmentReconciliationStatus.REDIRECTED: ShipmentItemOutcome.REDIRECTED,
        ShipmentReconciliationStatus.QUARANTINED: ShipmentItemOutcome.QUARANTINED,
    }[payload.outcome]
    now_ts = utcnow()
    for item in items:
        item.outcome = item_outcome
        item.outcome_reason = payload.note[:500]
        item.outcome_at = now_ts
        item.outcome_by_user_id = user.id
    shipment.reconciliation_status = payload.outcome
    shipment.reconciled_at = utcnow()
    shipment.reconciled_by_user_id = user.id
    shipment.reconciliation_note = payload.note
    if payload.outcome == ShipmentReconciliationStatus.REDIRECTED:
        shipment.redirected_to_facility_id = payload.redirected_to_facility_id
    db.add(
        ShipmentEvent(
            shipment_id=shipment.id,
            actor_user_id=user.id,
            event_type=f"reconciled_{payload.outcome.value.lower()}",
            note=payload.note,
        )
    )
    audit_service.record(
        db,
        "shipment.reconciled",
        actor_user_id=user.id,
        target_type="shipment",
        target_id=shipment.id,
        meta={
            "outcome": payload.outcome.value,
            "unit_codes": carried_codes,
            "cold_chain_maintained": payload.cold_chain_maintained,
            "restocked": restocked,
            "expired_on_return": expired_on_return,
            "redirected_to_facility_id": (
                str(payload.redirected_to_facility_id)
                if payload.redirected_to_facility_id
                else None
            ),
            "note": payload.note,
        },
    )
    for facility_id in {shipment.origin_facility_id, shipment.destination_facility_id}:
        facility = db.get(HospitalProfile, facility_id)
        if facility is not None:
            notification_service.notify(
                db,
                facility.user_id,
                type="shipment.reconciled",
                title=f"Shipment {shipment.shipment_code} reconciled",
                body=f"Outcome: {payload.outcome.value.replace('_', ' ').lower()}. {payload.note}",
                data={
                    "shipment_id": str(shipment.id),
                    "shipment_code": shipment.shipment_code,
                    "outcome": payload.outcome.value,
                    "restocked": restocked,
                },
            )
    db.commit()
    db.refresh(shipment)
    return _to_out(db, shipment)


# --------------------------------------------------------------------------- #
# Gap elimination: cold-chain telemetry
# --------------------------------------------------------------------------- #
def _telemetry_out(
    db: Session, shipment: Shipment, report, *, inserted=None, duplicates=None
) -> TelemetryOut:
    from app.models.shipment import ShipmentTemperatureReading

    readings = db.scalars(
        select(ShipmentTemperatureReading)
        .where(ShipmentTemperatureReading.shipment_id == shipment.id)
        .order_by(ShipmentTemperatureReading.recorded_at)
    ).all()
    return TelemetryOut(
        report={
            "status": report.status,
            "readings": report.readings,
            "valid_readings": report.valid_readings,
            "failed_readings": report.failed_readings,
            "min_c": report.min_c,
            "max_c": report.max_c,
            "range_min_c": report.range_min_c,
            "range_max_c": report.range_max_c,
            "excursions": [vars(e) for e in report.excursions],
            "gaps_minutes": [g[2] for g in report.gaps],
            "findings": report.findings,
            "sources": report.sources,
        },
        readings=list(readings),
        inserted=inserted,
        duplicates=duplicates,
    )


@router.post("/{shipment_id}/telemetry", response_model=TelemetryOut)
def add_telemetry(
    shipment_id: uuid.UUID,
    payload: TelemetryIn,
    _: None = Depends(verify_origin),
    user: User = Depends(_actor),
    db: Session = Depends(get_db),
) -> TelemetryOut:
    """Attach temperature readings (from a data logger export, a gateway or
    a manual check). Idempotent per (sensor, timestamp). The shipment's
    cold-chain status is re-evaluated immediately."""
    shipment = shipment_service.get_for_update(db, shipment_id)
    if shipment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shipment not found")
    _assert_is_party(shipment, _actor_facility(db, user))
    try:
        inserted, dup = cold_chain_service.ingest(
            db,
            shipment,
            [
                cold_chain_service.ReadingIn(
                    r.recorded_at, r.temperature_c, r.sensor_id, r.error_code
                )
                for r in payload.readings
            ],
            source=payload.source,
            submitted_by_user_id=user.id,
        )
    except cold_chain_service.TelemetryError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    before = shipment.cold_chain_status
    report = cold_chain_service.evaluate_and_store(db, shipment)
    audit_service.record(
        db,
        "shipment.telemetry_ingested",
        actor_user_id=user.id,
        target_type="shipment",
        target_id=shipment.id,
        meta={
            "inserted": inserted,
            "duplicates": dup,
            "source": payload.source.value,
            "cold_chain_status": report.status.value,
        },
    )
    if report.status in cold_chain_service.BAD_STATUSES and before != report.status:
        db.add(
            ShipmentEvent(
                shipment_id=shipment.id,
                actor_user_id=user.id,
                event_type="cold_chain_alert",
                note=f"{report.status.value}: " + "; ".join(report.findings)[:900],
            )
        )
        for facility_id in {shipment.origin_facility_id, shipment.destination_facility_id}:
            facility = db.get(HospitalProfile, facility_id)
            if facility is not None:
                notification_service.notify(
                    db,
                    facility.user_id,
                    type="shipment.cold_chain_alert",
                    title=f"Cold-chain alert: {shipment.shipment_code}",
                    body=report.findings[0] if report.findings else report.status.value,
                    data={"shipment_id": str(shipment.id), "status": report.status.value},
                )
    db.commit()
    return _telemetry_out(db, shipment, report, inserted=inserted, duplicates=dup)


@router.get("/{shipment_id}/telemetry", response_model=TelemetryOut)
def get_telemetry(
    shipment_id: uuid.UUID,
    user: User = Depends(_actor),
    db: Session = Depends(get_db),
) -> TelemetryOut:
    shipment = db.get(Shipment, shipment_id)
    if shipment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shipment not found")
    _assert_is_party(shipment, _actor_facility(db, user))
    return _telemetry_out(db, shipment, cold_chain_service.evaluate(db, shipment))


@router.post("/{shipment_id}/telemetry/simulate", response_model=TelemetryOut)
def simulate_telemetry(
    shipment_id: uuid.UUID,
    payload: TelemetrySimulateIn,
    _: None = Depends(verify_origin),
    user: User = Depends(require_roles(UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> TelemetryOut:
    """DEVELOPMENT ONLY (refused in production): generate readings labelled
    SIMULATED, for demos and tests of the evaluator. Never real evidence."""
    shipment = shipment_service.get_for_update(db, shipment_id)
    if shipment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shipment not found")
    try:
        inserted = cold_chain_service.simulate(
            db,
            shipment,
            minutes=payload.minutes,
            interval_minutes=payload.interval_minutes,
            excursion_at_minute=payload.excursion_at_minute,
            excursion_c=payload.excursion_c,
            sensor_failure_at_minute=payload.sensor_failure_at_minute,
            seed=payload.seed,
            submitted_by_user_id=user.id,
        )
    except cold_chain_service.TelemetryError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    report = cold_chain_service.evaluate_and_store(db, shipment)
    audit_service.record(
        db,
        "shipment.telemetry_simulated",
        actor_user_id=user.id,
        target_type="shipment",
        target_id=shipment.id,
        meta={"inserted": inserted, "cold_chain_status": report.status.value},
    )
    db.commit()
    return _telemetry_out(db, shipment, report, inserted=inserted, duplicates=0)


# --------------------------------------------------------------------------- #
# Read / search / traceability
# --------------------------------------------------------------------------- #


@router.get("", response_model=list[ShipmentOut])
def list_shipments(
    status_filter: ShipmentStatus | None = Query(default=None, alias="status"),
    direction: str | None = Query(default=None, pattern="^(incoming|outgoing)$"),
    user: User = Depends(_actor),
    db: Session = Depends(get_db),
) -> list[ShipmentOut]:
    hp = _actor_facility(db, user)
    stmt = select(Shipment)
    if hp is not None:
        if direction == "incoming":
            stmt = stmt.where(Shipment.destination_facility_id == hp.id)
        elif direction == "outgoing":
            stmt = stmt.where(Shipment.origin_facility_id == hp.id)
        else:
            stmt = stmt.where(
                or_(Shipment.origin_facility_id == hp.id, Shipment.destination_facility_id == hp.id)
            )
    if status_filter is not None:
        stmt = stmt.where(Shipment.status == status_filter)
    stmt = stmt.order_by(Shipment.created_at.desc()).limit(200)
    rows = db.scalars(stmt).all()
    return [_to_out(db, s) for s in rows]


@router.get("/by-code/{shipment_code}", response_model=ShipmentOut)
def get_shipment_by_code(
    shipment_code: str, user: User = Depends(_actor), db: Session = Depends(get_db)
) -> ShipmentOut:
    shipment = db.scalar(select(Shipment).where(Shipment.shipment_code == shipment_code))
    if shipment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shipment not found")
    hp = _actor_facility(db, user)
    _assert_is_party(shipment, hp)
    return _to_out(db, shipment)


@router.get("/units/{unit_code}", response_model=UnitTraceOut)
def trace_unit(
    unit_code: str, user: User = Depends(_actor), db: Session = Depends(get_db)
) -> UnitTraceOut:
    """Section 25: look up a single unit by its printed/barcoded code and
    answer its current state/location/shipment/donation reference/
    component/history -- without exposing donor PII (only `donation_id`,
    never the donor's identity)."""
    unit = db.scalar(select(BloodUnit).where(BloodUnit.unit_code == unit_code))
    if unit is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unit not found")
    hp = _actor_facility(db, user)
    if hp is not None and hp.id != unit.hospital_id:
        # A hospital may only trace units currently on its own books -- the
        # source-side unit's own hospital, if this is a destination-side
        # TRANSFERRED_IN row, is reachable via source_unit_id, not this
        # endpoint directly, same as any other cross-facility boundary in
        # this app.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This unit is not on your facility's books",
        )
    hospital = db.get(HospitalProfile, unit.hospital_id)
    source_shipment = db.get(Shipment, unit.source_shipment_id) if unit.source_shipment_id else None
    carrying_shipment = (
        db.get(Shipment, unit.reserved_for_shipment_id) if unit.reserved_for_shipment_id else None
    )
    return UnitTraceOut(
        id=unit.id,
        unit_code=unit.unit_code,
        blood_type=unit.blood_type,
        component=unit.component,
        state=unit.state.value,
        hospital_id=unit.hospital_id,
        hospital_name=hospital.name if hospital else None,
        donation_id=unit.donation_id,
        source_shipment_id=unit.source_shipment_id,
        source_shipment_code=source_shipment.shipment_code if source_shipment else None,
        source_unit_id=unit.source_unit_id,
        carrying_shipment_id=unit.reserved_for_shipment_id,
        carrying_shipment_code=carrying_shipment.shipment_code if carrying_shipment else None,
        received_at=unit.received_at,
        expires_at=unit.expires_at,
    )


@router.get("/{shipment_id}", response_model=ShipmentOut)
def get_shipment(
    shipment_id: uuid.UUID, user: User = Depends(_actor), db: Session = Depends(get_db)
) -> ShipmentOut:
    shipment = db.get(Shipment, shipment_id)
    if shipment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shipment not found")
    hp = _actor_facility(db, user)
    _assert_is_party(shipment, hp)
    return _to_out(db, shipment)


@router.get("/{shipment_id}/timeline", response_model=list[ShipmentEventOut])
def get_shipment_timeline(
    shipment_id: uuid.UUID, user: User = Depends(_actor), db: Session = Depends(get_db)
) -> list[ShipmentEventOut]:
    """Every timestamp here is server-written `created_at` -- never
    client-manufactured (Section 23)."""
    shipment = db.get(Shipment, shipment_id)
    if shipment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shipment not found")
    hp = _actor_facility(db, user)
    _assert_is_party(shipment, hp)
    events = db.scalars(
        select(ShipmentEvent)
        .where(ShipmentEvent.shipment_id == shipment_id)
        .order_by(ShipmentEvent.created_at.asc())
    ).all()
    return list(events)
