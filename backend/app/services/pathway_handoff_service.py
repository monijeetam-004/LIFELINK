"""Plan -> shipment hand-off and shipment-failure recovery for emergencies
(audit findings C21 and C20, 2026-10-31).

Before: the clean-start workflow RELEASED the pathway's reservation and the
hospital then created an unrelated shipment that reserved units again. The
units were briefly free for anyone, and the shipment carried no reference to
the emergency it served; a failed shipment was reconciled but its emergency
was never re-planned.

Now:

* ``hand_off`` turns an ACTIVE, reserved pathway into one shipment per leg in
  ONE transaction. Each leg's units move from the emergency's reservation to
  the shipment's (``inventory_service.transfer_reservation_to_shipment``)
  and stay RESERVED throughout. The shipment records ``emergency_id`` and
  ``pathway_id``; each pathway reservation records the shipment it went to.
  Calling it again returns the same shipments (idempotent). If anything
  fails, nothing is changed (the caller rolls back).
* ``handle_linked_shipment_outcome`` runs when a linked shipment FAILS (in
  transit) or is CANCELLED (before dispatch). The failed shipment's own
  units are left to the existing reconciliation (they are never silently
  returned to stock); the emergency, if still open, is re-planned for the
  units still missing and the new plan must be secured
  (``fail_over(..., require_reservation=True)``) -- or it is marked
  RECOVERY_REQUIRED. A repeated callback finds the pathway no longer ACTIVE
  and changes nothing.
* A transport disruption on a pathway that is already executing (live
  shipments) does not silently re-plan around blood that is physically on
  the road; the coordinator is told, and failing/cancelling the shipment is
  what triggers re-planning.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.emergency_lifecycle import OPEN_STATUSES
from app.core.time import utcnow
from app.models.blood_unit import BloodUnit
from app.models.emergency import Emergency
from app.models.emergency_pathway import EmergencyPathway, PathwayReservation
from app.models.enums import (
    BloodComponent,
    ColdChainStatus,
    EmergencyPathwayStatus,
    ShipmentStatus,
)
from app.models.hospital import HospitalProfile
from app.models.shipment import Shipment, ShipmentEvent, ShipmentItem
from app.services import (
    audit_service,
    cold_chain_service,
    inventory_service,
    notification_service,
    shipment_service,
)
from app.services import emergency_pathway_service as eps

# A shipment in one of these states still carries (or will carry) the units.
LIVE_SHIPMENT_STATUSES = (
    ShipmentStatus.CREATED,
    ShipmentStatus.DISPATCHED,
    ShipmentStatus.IN_TRANSIT,
    ShipmentStatus.ARRIVED,
)


class HandoffError(Exception):
    """The hand-off cannot proceed (409 to the API caller)."""


def shipments_of(db: Session, pathway_id: uuid.UUID) -> list[Shipment]:
    return list(db.scalars(
        select(Shipment).where(Shipment.pathway_id == pathway_id).order_by(Shipment.created_at)
    ).all())


def live_shipments_of(db: Session, pathway_id: uuid.UUID) -> list[Shipment]:
    return [s for s in shipments_of(db, pathway_id) if s.status in LIVE_SHIPMENT_STATUSES]


def _event(db: Session, shipment: Shipment, actor: uuid.UUID | None, kind: str, note: str):
    db.add(ShipmentEvent(shipment_id=shipment.id, actor_user_id=actor, event_type=kind,
                         note=note[:1000]))


def hand_off(
    db: Session, em: Emergency, pathway: EmergencyPathway, *, actor_user_id: uuid.UUID | None,
) -> tuple[list[Shipment], bool]:
    """Create the pathway's shipments from its reservation. Returns
    (shipments, created_now). The caller must hold the emergency row lock."""
    existing = shipments_of(db, pathway.id)
    if existing:
        return existing, False
    if pathway.status != EmergencyPathwayStatus.ACTIVE:
        raise HandoffError("Only the ACTIVE pathway can be handed off")
    if pathway.recovery_required_at is not None:
        raise HandoffError("This pathway is not secured (recovery required); reserve units first")
    reservations = list(db.scalars(
        select(PathwayReservation).where(
            PathwayReservation.pathway_id == pathway.id, PathwayReservation.active.is_(True))
    ).all())
    if not reservations:
        raise HandoffError("Reserve the pathway's units before handing it off to a shipment")
    problems = eps.validate_pathway(db, em, pathway)
    if problems:
        raise HandoffError("The active pathway is no longer valid: " + "; ".join(problems))

    component = em.component or BloodComponent.WHOLE_BLOOD
    try:
        temp_min, temp_max = cold_chain_service.range_for_components({component})
    except cold_chain_service.TelemetryError as exc:  # pragma: no cover - single component
        raise HandoffError(str(exc)) from exc

    by_leg: dict[uuid.UUID, list[PathwayReservation]] = {}
    for r in reservations:
        by_leg.setdefault(r.leg_id, []).append(r)
    steps = eps.steps_of(db, pathway.id)
    now = utcnow()
    created: list[Shipment] = []
    for leg in eps.legs_of(db, pathway.id):
        if leg.source_facility_id == leg.destination_facility_id:
            # Units already at the destination: nothing to ship. Their
            # reservation stays with the emergency (still RESERVED there).
            continue
        leg_res = by_leg.get(leg.id, [])
        if not leg_res:
            raise HandoffError(f"Leg {leg.leg_index} of pathway v{pathway.version} holds no "
                               "reservation; re-reserve the pathway first")
        leg_steps = [s for s in steps if s.leg_id == leg.id]
        arrival = now + timedelta(minutes=leg.eta_minutes)
        shipment = Shipment(
            shipment_code=shipment_service.generate_shipment_code(),
            origin_facility_id=leg.source_facility_id,
            destination_facility_id=leg.destination_facility_id,
            transport_route_id=leg_steps[0].route_id if len(leg_steps) == 1 else None,
            status=ShipmentStatus.CREATED,
            created_by_user_id=actor_user_id,
            note=f"Emergency pathway v{pathway.version}, leg {leg.leg_index}: hand-off",
            temp_min_c=temp_min,
            temp_max_c=temp_max,
            cold_chain_status=ColdChainStatus.NO_DATA,
            estimated_arrival_at=arrival,
            emergency_id=em.id,
            pathway_id=pathway.id,
        )
        db.add(shipment)
        db.flush()
        units: list[BloodUnit] = []
        for r in leg_res:
            units += inventory_service.transfer_reservation_to_shipment(
                db, r.facility_id, r.blood_type, r.units,
                emergency_id=em.id, shipment_id=shipment.id, actor_user_id=actor_user_id,
                component=r.component, usable_until=arrival,
                note=f"Pathway v{pathway.version} reservation handed to {shipment.shipment_code}",
            )
            r.active = False
            r.released_at = now
            r.release_reason = f"handed off to shipment {shipment.shipment_code} (not released)"
            r.handed_off_shipment_id = shipment.id
        db.add_all([ShipmentItem(shipment_id=shipment.id, blood_unit_id=u.id) for u in units])
        _event(db, shipment, actor_user_id, "created",
               f"Created by hand-off of emergency pathway v{pathway.version}: {len(units)} "
               "unit(s) transferred from the emergency's reservation without release")
        audit_service.record(
            db, "shipment.created", actor_user_id=actor_user_id,
            target_type="shipment", target_id=shipment.id,
            meta={"origin_facility_id": str(shipment.origin_facility_id),
                  "destination_facility_id": str(shipment.destination_facility_id),
                  "units": len(units), "emergency_id": str(em.id),
                  "pathway_version": pathway.version, "handoff": True},
        )
        for fid in {shipment.origin_facility_id, shipment.destination_facility_id}:
            hp = db.get(HospitalProfile, fid)
            if hp is not None:
                notification_service.notify(
                    db, hp.user_id, type="shipment.created",
                    title=f"Emergency shipment {shipment.shipment_code}",
                    body=f"{len(units)} reserved unit(s) for an emergency are ready to dispatch.",
                    data={"shipment_id": str(shipment.id),
                          "shipment_code": shipment.shipment_code,
                          "emergency_id": str(em.id)},
                )
        created.append(shipment)
    if not created:
        raise HandoffError("Every unit of this plan is already at the destination; "
                           "there is nothing to ship")
    db.flush()
    eps._event(db, em, actor_user_id, "pathway_handed_off",
               f"Pathway v{pathway.version} handed off to "
               + ", ".join(s.shipment_code for s in created)
               + " -- reservations transferred, never released.")
    audit_service.record(
        db, "emergency.pathway_handed_off", actor_user_id=actor_user_id,
        target_type="emergency", target_id=em.id,
        meta={"pathway_version": pathway.version,
              "shipments": [s.shipment_code for s in created]},
    )
    return created, True


def _units_still_coming(db: Session, pathway_id: uuid.UUID, exclude: uuid.UUID) -> int:
    live = [s.id for s in live_shipments_of(db, pathway_id) if s.id != exclude]
    if not live:
        return 0
    return int(db.scalar(
        select(func.count()).select_from(ShipmentItem).where(
            ShipmentItem.shipment_id.in_(live), ShipmentItem.is_active.is_(True))
    ) or 0)


def handle_linked_shipment_outcome(
    db: Session, shipment: Shipment, *, outcome: str, reason: str | None,
    actor_user_id: uuid.UUID | None,
) -> eps.ReoptimizationOutcome | None:
    """`outcome` is "failed" or "cancelled". Returns the re-planning outcome,
    or None when nothing was re-planned (and records why on the shipment)."""
    if shipment.emergency_id is None or shipment.pathway_id is None:
        return None
    em = db.get(Emergency, shipment.emergency_id, with_for_update=True)
    if em is None or em.status not in OPEN_STATUSES:
        _event(db, shipment, actor_user_id, "emergency_not_replanned",
               "The linked emergency is no longer open; nothing re-planned.")
        return None
    pathway = db.get(EmergencyPathway, shipment.pathway_id)
    if pathway is None or pathway.status != EmergencyPathwayStatus.ACTIVE:
        _event(db, shipment, actor_user_id, "emergency_not_replanned",
               "The emergency's plan has already changed since this shipment was created; "
               "nothing re-planned again.")
        return None
    still_coming = _units_still_coming(db, pathway.id, exclude=shipment.id)
    needed = (em.units_needed or 1) - still_coming
    if needed <= 0:
        _event(db, shipment, actor_user_id, "emergency_not_replanned",
               "Other shipments of the same plan still carry every unit needed.")
        return None
    why = f"shipment {shipment.shipment_code} {outcome}" + (f": {reason}" if reason else "")
    result = eps.fail_over(
        db, em, pathway, reason=why, actor_user_id=actor_user_id, require_reservation=True,
        units_needed=needed if still_coming else None,
    )
    _event(db, shipment, actor_user_id, "emergency_replanned",
           f"Emergency re-planned for {needed} unit(s): {result.kind}, pathway "
           f"v{result.previous_version} -> v{result.new_version}.")
    notification_service.notify(
        db, em.created_by_user_id, type="emergency.pathway_reoptimized",
        title="Emergency delivery update",
        body=(f"Shipment {shipment.shipment_code} {outcome}. LifeLink re-planned "
              f"{needed} unit(s): pathway v{result.new_version} ({result.kind})."),
        data={"emergency_id": str(em.id), "pathway_version": result.new_version},
    )
    return result
