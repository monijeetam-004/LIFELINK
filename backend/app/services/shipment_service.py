"""Phase D -- DB-side half of the shipment lifecycle.

Pairs with the pure transition table in `app.core.shipment_lifecycle`,
exactly the way `app.services.blood_request_lifecycle` pairs with
`app.core.request_lifecycle`: this module is the one place that locks the
`Shipment` row, validates the transition, applies it (status + the
matching timestamp column), and writes both audit trails this codebase
already has a convention for -- `ShipmentEvent` (the shipment's own
user-facing timeline, same shape as `BloodRequestEvent`/`EmergencyEvent`)
and `AuditLog` via `audit_service.record` (the admin-facing trail), with
old state, new state, actor, and shipment id on every entry.

The higher-level orchestration for each real action (create/dispatch/
mark-in-transit/report-delay/mark-arrived/receive/cancel/fail) -- which
also has to call `app.services.inventory_service` for the real inventory
effect and `app.services.notification_service` for the real notification --
lives in `app.api.routes.shipments`, exactly the way BloodRequest's own
higher-level actions (`allocate_to_request`, `fulfill_from_hospital`) live
directly in `app.api.routes.hospitals` rather than in
`blood_request_lifecycle` itself. This module only holds the two primitives
every one of those actions needs.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import Request
from sqlalchemy.orm import Session

from app.core.shipment_lifecycle import validate_transition
from app.core.time import utcnow
from app.models.enums import ShipmentStatus
from app.models.shipment import Shipment, ShipmentEvent
from app.services import audit_service


def get_for_update(db: Session, shipment_id: uuid.UUID) -> Shipment | None:
    """Fetch a shipment row with a `SELECT ... FOR UPDATE` lock held for
    the rest of the current transaction.

    Every endpoint that can change `Shipment.status` -- dispatch, mark
    in-transit, report a delay (not a status change itself, but must not
    race a concurrent transition out of a delayable state), mark arrived,
    receive, cancel, fail -- fetches through this instead of a bare
    `db.get()`, so two concurrent actions on the same shipment serialize:
    the second waits for the first's commit (which releases the lock) and
    then re-validates against the now-current status, rather than both
    reading the same stale status and both succeeding. This is what makes
    races A/B/C from the Phase D spec (two simultaneous dispatches, two
    simultaneous receipts, receipt-vs-cancellation) resolve to exactly one
    winner and a clean 409 for the loser -- the same "repeated allocation"
    discipline `blood_request_lifecycle.get_for_update` already applies to
    `BloodRequest`.
    """
    return db.get(Shipment, shipment_id, with_for_update=True)


def get_by_code_for_update(db: Session, shipment_code: str) -> Shipment | None:
    from sqlalchemy import select

    return db.scalar(
        select(Shipment).where(Shipment.shipment_code == shipment_code).with_for_update()
    )


_TIMESTAMP_FIELD: dict[ShipmentStatus, str] = {
    ShipmentStatus.DISPATCHED: "dispatched_at",
    ShipmentStatus.IN_TRANSIT: "in_transit_at",
    ShipmentStatus.ARRIVED: "arrived_at",
    ShipmentStatus.RECEIVED: "received_at",
    ShipmentStatus.CANCELLED: "cancelled_at",
    ShipmentStatus.FAILED: "failed_at",
}


def transition(
    db: Session,
    shipment: Shipment,
    target: ShipmentStatus,
    *,
    actor_user_id: uuid.UUID | None,
    reason: str | None = None,
    request: Request | None = None,
    event_type: str | None = None,
    event_note: str | None = None,
    at: datetime | None = None,
    extra_meta: dict | None = None,
) -> None:
    """Validate and apply one shipment status transition.

    Raises `app.core.shipment_lifecycle.InvalidShipmentTransitionError`
    (callers turn this into an HTTP 409) if `target` isn't reachable from
    `shipment.status` -- including a same-state call, which this codebase
    always treats as an error rather than an idempotent no-op (see
    `blood_unit_lifecycle.validate_transition`'s identical rule; Section 11's
    idempotency is handled one level up, on the *action* -- e.g. a second
    identical POST .../dispatch on an already-DISPATCHED shipment is still
    a 409 here, exactly like re-ALLOCATEing an already-ALLOCATED BloodUnit
    would be).

    Writes exactly one `ShipmentEvent` (this shipment's own timeline) and
    one `AuditLog` entry (via `audit_service.record`) per call -- the two
    existing trails this codebase already uses for every other domain's
    lifecycle transitions, not a third, shipment-specific mechanism.

    Does not commit -- the caller (an `app.api.routes.shipments` action)
    commits once at the end of its own request, after any inventory
    mutation and notification calls it also makes in the same transaction.
    """
    validate_transition(shipment.status, target)
    old = shipment.status
    when = at or utcnow()

    shipment.status = target
    field = _TIMESTAMP_FIELD.get(target)
    if field is not None:
        setattr(shipment, field, when)
    db.flush()

    db.add(ShipmentEvent(
        shipment_id=shipment.id,
        actor_user_id=actor_user_id,
        event_type=event_type or target.value.lower(),
        note=event_note or reason,
    ))
    meta = {
        "old_status": old.value,
        "new_status": target.value,
        "reason": reason,
    }
    if extra_meta:
        meta.update(extra_meta)
    audit_service.record(
        db, "shipment.transition", actor_user_id=actor_user_id,
        target_type="shipment", target_id=shipment.id, request=request,
        meta=meta,
    )


def generate_shipment_code() -> str:
    """A human-readable, PII-free, globally-unique shipment identifier --
    no donor/patient identity, no blood group, no medical info (Section 10).
    Same trust model as `inventory_service._generate_unit_code`: collision
    probability with a random 10-hex-char suffix is astronomically low, and
    the DB's own `uq_shipments_shipment_code` constraint -- not this
    function -- is the real guarantee."""
    return f"SHP-{uuid.uuid4().hex[:10].upper()}"
