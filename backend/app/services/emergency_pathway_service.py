"""Versioned emergency pathways: plan, persist, fail over, reserve.

A *plan round* runs the planner (``app.services.pathway_planner``) once and
stores its result as one ACTIVE pathway (the primary) plus up to
``PATHWAY_MAX_BACKUPS`` BACKUP pathways. Every pathway stores its legs
(source facility, units by blood type) and, per leg, the ordered transport
routes it travels (``EmergencyPathwayStep``).

Failure handling is route-exact: when a route is disrupted, only pathways
that actually travel over that route are affected -- a failed first, middle
or last route all count, and a route that merely touches the same facility
does not. An affected ACTIVE pathway is INVALIDATED and the best backup that
is *still valid right now* (routes operational, facility verified, stock
still usable, deadline still reachable, units still compatible) is promoted.
Only when no backup survives is a fresh plan round run.

Rows are never deleted or overwritten: superseded and invalidated pathways
keep their legs, steps and reasons for the audit trail.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import compatibility
from app.core.config import settings
from app.core.emergency_lifecycle import OPEN_STATUSES
from app.core.time import utcnow
from app.models.emergency import Emergency, EmergencyEvent, EmergencyOverride
from app.models.emergency_pathway import (
    EmergencyPathway,
    EmergencyPathwayLeg,
    EmergencyPathwayStep,
    PathwayReservation,
)
from app.models.enums import (
    BloodComponent,
    BloodType,
    EmergencyOverrideKind,
    EmergencyPathwayStatus,
    FacilityType,
    PathwayFeasibility,
    TransportRouteStatus,
    Urgency,
    haversine_km,
)
from app.models.graph_pathway_log import GraphPathwayLog
from app.models.hospital import HospitalProfile
from app.models.transport import TransportRoute
from app.models.user import User
from app.services import audit_service, inventory_service, notification_service
from app.services import pathway_planner as planner
from app.services.inventory_service import InventoryActionError
from app.services.pathway_planner import CandidatePlan, PlanningError, PlanResult

LIVE_STATUSES = (EmergencyPathwayStatus.ACTIVE, EmergencyPathwayStatus.BACKUP)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _next_version(db: Session, emergency_id: uuid.UUID) -> int:
    current_max = db.scalar(
        select(EmergencyPathway.version)
        .where(EmergencyPathway.emergency_id == emergency_id)
        .order_by(EmergencyPathway.version.desc())
        .limit(1)
    )
    return (current_max or 0) + 1


def _next_round(db: Session, emergency_id: uuid.UUID) -> int:
    current = db.scalar(
        select(EmergencyPathway.plan_round)
        .where(EmergencyPathway.emergency_id == emergency_id)
        .order_by(EmergencyPathway.plan_round.desc())
        .limit(1)
    )
    return (current or 0) + 1


def _event(db: Session, em: Emergency, actor: uuid.UUID | None, kind: str, note: str) -> None:
    db.add(
        EmergencyEvent(emergency_id=em.id, actor_user_id=actor, event_type=kind, note=note[:1000])
    )


def active_override(db: Session, emergency_id: uuid.UUID, kind: EmergencyOverrideKind) -> bool:
    return (
        db.scalar(
            select(EmergencyOverride.id)
            .where(
                EmergencyOverride.emergency_id == emergency_id,
                EmergencyOverride.kind == kind,
                EmergencyOverride.revoked_at.is_(None),
            )
            .limit(1)
        )
        is not None
    )


def destination_for(db: Session, em: Emergency) -> HospitalProfile:
    """Where the blood must be delivered: the emergency's target facility,
    or else the nearest verified HOSPITAL to its coordinates."""
    if em.target_facility_id is not None:
        dest = db.get(HospitalProfile, em.target_facility_id)
        if dest is not None:
            return dest
    if em.latitude is not None and em.longitude is not None:
        hospitals = db.scalars(
            select(HospitalProfile)
            .join(User, User.id == HospitalProfile.user_id)
            .where(
                HospitalProfile.verified.is_(True),
                HospitalProfile.facility_type == FacilityType.HOSPITAL,
                HospitalProfile.latitude.is_not(None),
                HospitalProfile.longitude.is_not(None),
                User.is_active.is_(True),
            )
        ).all()
        if hospitals:
            return min(
                hospitals,
                key=lambda h: (
                    haversine_km(em.latitude, em.longitude, h.latitude, h.longitude),
                    str(h.id),
                ),
            )
    raise PlanningError(
        "This emergency needs a target facility or coordinates to plan a delivery pathway"
    )


def plan_request_for(
    db: Session,
    em: Emergency,
    *,
    destination_facility_id: uuid.UUID | None = None,
    excluded_route_ids: frozenset[uuid.UUID] = frozenset(),
    now: datetime | None = None,
    units_needed: int | None = None,
) -> planner.PlanRequest:
    """`units_needed` overrides the emergency's quantity -- used when part of
    the order is already on its way in another shipment (audit C20)."""
    if em.blood_type is None:
        raise PlanningError("This emergency has no blood type on record to search for")
    dest_id = destination_facility_id or destination_for(db, em).id
    return planner.PlanRequest(
        recipient_type=em.blood_type,
        component=em.component or BloodComponent.WHOLE_BLOOD,
        units_needed=units_needed or em.units_needed or 1,
        urgency=em.urgency or Urgency.CRITICAL,
        deadline=em.deadline,
        destination_facility_id=dest_id,
        now=now or utcnow(),
        emergency_id=em.id,
        excluded_route_ids=excluded_route_ids,
        allow_unverified=active_override(db, em.id, EmergencyOverrideKind.UNVERIFIED_FACILITY),
    )


def _persist(
    db: Session,
    em: Emergency,
    result: PlanResult,
    cand: CandidatePlan | None,
    *,
    plan_round: int,
    rank: int,
    status: EmergencyPathwayStatus,
    reason: str,
) -> EmergencyPathway:
    req = result.request
    first = cand.legs[0].option if cand and cand.legs else None
    row = EmergencyPathway(
        emergency_id=em.id,
        version=_next_version(db, em.id),
        status=status,
        origin_facility_id=req.destination_facility_id,
        selected_facility_id=first.facility_id if first else None,
        pathway_source=result.source,
        distance_km=first.distance_km if first else None,
        transit_minutes=first.transit_minutes if first else None,
        hops=first.hops if first else None,
        graph_available=result.graph_available,
        reason=reason[:300],
        plan_round=plan_round,
        backup_rank=rank,
        feasibility=cand.feasibility if cand else PathwayFeasibility.IMPOSSIBLE,
        component=req.component,
        recipient_blood_type=req.recipient_type,
        urgency=req.urgency,
        units_requested=req.units_needed,
        units_planned=cand.units_planned if cand else 0,
        eta_minutes=cand.eta_minutes if cand else None,
        arrival_at=cand.arrival_at if cand else None,
        deadline_at=req.deadline,
        slack_minutes=cand.slack_minutes if cand else None,
        strategy=result.strategy.name,
        explanation=(cand.explanation if cand else []),
        assumptions=result.assumptions,
    )
    db.add(row)
    db.flush()
    for i, leg in enumerate(cand.legs if cand else []):
        o = leg.option
        leg_row = EmergencyPathwayLeg(
            pathway_id=row.id,
            leg_index=i,
            source_facility_id=o.facility_id,
            destination_facility_id=req.destination_facility_id,
            units=leg.units,
            units_by_type={t.value: n for t, n in leg.units_by_type.items()},
            route_basis=o.route_basis,
            node_ids=[str(n) for n in o.node_ids],
            distance_km=float(o.distance_km or 0.0),
            straight_line_km=o.straight_line_km,
            transit_minutes=int(o.transit_minutes or 0),
            eta_minutes=int(o.eta_minutes or 0),
            hops=o.hops,
        )
        db.add(leg_row)
        db.flush()
        for j, step in enumerate(o.steps):
            db.add(
                EmergencyPathwayStep(
                    pathway_id=row.id,
                    leg_id=leg_row.id,
                    step_index=j,
                    route_id=step.route_id,
                    from_facility_id=step.from_facility_id,
                    to_facility_id=step.to_facility_id,
                    distance_km=step.distance_km,
                    transit_minutes=step.transit_minutes,
                    route_status_at_plan=TransportRouteStatus.OPERATIONAL,
                )
            )
    db.flush()
    return row


def legs_of(db: Session, pathway_id: uuid.UUID) -> list[EmergencyPathwayLeg]:
    return list(
        db.scalars(
            select(EmergencyPathwayLeg)
            .where(EmergencyPathwayLeg.pathway_id == pathway_id)
            .order_by(EmergencyPathwayLeg.leg_index)
        ).all()
    )


def steps_of(db: Session, pathway_id: uuid.UUID) -> list[EmergencyPathwayStep]:
    return list(
        db.scalars(
            select(EmergencyPathwayStep).where(EmergencyPathwayStep.pathway_id == pathway_id)
        ).all()
    )


def route_ids_of(db: Session, pathway_id: uuid.UUID) -> list[uuid.UUID]:
    rows = db.execute(
        select(
            EmergencyPathwayStep.route_id,
            EmergencyPathwayLeg.leg_index,
            EmergencyPathwayStep.step_index,
        )
        .join(EmergencyPathwayLeg, EmergencyPathwayLeg.id == EmergencyPathwayStep.leg_id)
        .where(EmergencyPathwayStep.pathway_id == pathway_id)
        .order_by(EmergencyPathwayLeg.leg_index, EmergencyPathwayStep.step_index)
    ).all()
    return [r[0] for r in rows]


# --------------------------------------------------------------------------- #
# Planning
# --------------------------------------------------------------------------- #
def create_plan_round(
    db: Session,
    em: Emergency,
    *,
    requested_by_user_id: uuid.UUID | None,
    reason: str,
    destination_facility_id: uuid.UUID | None = None,
    require_reservation: bool = False,
    units_needed: int | None = None,
) -> tuple[EmergencyPathway, list[EmergencyPathway], PlanResult]:
    """Run the planner and store its result as the emergency's new plan
    round: one ACTIVE primary plus BACKUP alternatives. Every still-live
    pathway of earlier rounds is SUPERSEDED (kept for history).

    Locks the emergency row so two concurrent re-plans serialise instead of
    racing on the one-ACTIVE-pathway unique index.

    Reservations follow the plan: if the superseded primary held units (or
    the caller says the emergency was secured, `require_reservation`), the
    new round is secured too -- see `_secure_round` for what happens when
    the new primary's units cannot be reserved (audit C15).
    """
    db.get(Emergency, em.id, with_for_update=True)
    req = plan_request_for(db, em, destination_facility_id=destination_facility_id,
                           units_needed=units_needed)
    result = planner.plan(db, req)

    db.add(
        GraphPathwayLog(
            requested_by_user_id=requested_by_user_id,
            emergency_id=em.id,
            origin_facility_id=req.destination_facility_id,
            blood_type=req.recipient_type,
            pathway_source=result.source,
            best_distance_km=(
                result.primary.legs[0].option.distance_km if result.primary.legs else None
            ),
            candidate_count=sum(1 for o in result.options if o.rejected_reason is None),
            disrupted_routes_excluded=result.disrupted_routes_excluded,
            graph_available=result.graph_available,
        )
    )

    previous = db.scalars(
        select(EmergencyPathway).where(
            EmergencyPathway.emergency_id == em.id,
            EmergencyPathway.status.in_(LIVE_STATUSES),
        )
    ).all()
    previous_active = [p for p in previous if p.status == EmergencyPathwayStatus.ACTIVE]
    now = utcnow()
    for p in previous:
        p.status = EmergencyPathwayStatus.SUPERSEDED
        p.superseded_reason = reason[:500]
        p.superseded_at = now
    db.flush()

    plan_round = _next_round(db, em.id)
    primary = _persist(
        db,
        em,
        result,
        result.primary if result.primary.legs else None,
        plan_round=plan_round,
        rank=0,
        status=EmergencyPathwayStatus.ACTIVE,
        reason=reason,
    )
    if not result.primary.legs:
        primary.explanation = result.primary.explanation
    backups = [
        _persist(
            db,
            em,
            result,
            b,
            plan_round=plan_round,
            rank=i,
            status=EmergencyPathwayStatus.BACKUP,
            reason=f"{reason} (backup {i})",
        )
        for i, b in enumerate(result.backups, start=1)
    ]
    # Reservations follow the plan: whatever the superseded primary held is
    # released, and the new primary reserves the same way.
    had_reservations = require_reservation
    for old in previous_active:
        if release_pathway_reservations(
            db, old, actor_user_id=requested_by_user_id, reason=f"plan replaced: {reason}"
        ):
            had_reservations = True
    db.flush()
    if had_reservations:
        primary = _secure_round(db, em, primary, backups, actor_user_id=requested_by_user_id)
    db.flush()
    return primary, backups, result


def create_pathway_version(
    db: Session,
    emergency: Emergency,
    origin_facility_id: uuid.UUID,
    *,
    requested_by_user_id: uuid.UUID,
    reason: str,
) -> tuple[EmergencyPathway, PlanResult]:
    """Backward-compatible entry point: plan with `origin_facility_id` as the
    delivery destination and return the new ACTIVE pathway."""
    primary, _backups, result = create_plan_round(
        db,
        emergency,
        requested_by_user_id=requested_by_user_id,
        reason=reason,
        destination_facility_id=origin_facility_id,
    )
    return primary, result


# --------------------------------------------------------------------------- #
# Validation of a stored pathway against the world as it is now
# --------------------------------------------------------------------------- #
def validate_pathway(
    db: Session,
    em: Emergency,
    pathway: EmergencyPathway,
    *,
    now: datetime | None = None,
) -> list[str]:
    """Reasons `pathway` can no longer be used right now (empty = valid).

    Checks every route is still OPERATIONAL, every source facility is still
    verified (unless overridden) and active, every leg's units are still
    compatible and still usable at the recomputed arrival time, and that the
    recomputed arrival still meets the deadline."""
    now = now or utcnow()
    problems: list[str] = []
    legs = legs_of(db, pathway.id)
    if not legs:
        return ["pathway has no legs"]
    route_ids = route_ids_of(db, pathway.id)
    if route_ids:
        statuses = dict(
            db.execute(
                select(TransportRoute.id, TransportRoute.status).where(
                    TransportRoute.id.in_(route_ids)
                )
            ).all()
        )
        for rid in route_ids:
            if statuses.get(rid) != TransportRouteStatus.OPERATIONAL:
                problems.append(f"route {rid} is no longer operational")
    allow_unverified = active_override(db, em.id, EmergencyOverrideKind.UNVERIFIED_FACILITY)
    recipient = em.blood_type
    component = em.component or BloodComponent.WHOLE_BLOOD
    for leg in legs:
        facility = db.get(HospitalProfile, leg.source_facility_id)
        owner = db.get(User, facility.user_id) if facility else None
        if facility is None or owner is None or not owner.is_active:
            problems.append(f"source facility {leg.source_facility_id} is no longer active")
            continue
        if not facility.verified and not allow_unverified:
            problems.append(f"source facility {facility.name or facility.id} is no longer verified")
        arrival = now + timedelta(minutes=leg.eta_minutes)
        if em.deadline is not None and arrival > em.deadline:
            problems.append(
                f"leg from {facility.name or facility.id} would now arrive after the deadline"
            )
        for bt_value in leg.units_by_type:
            bt = BloodType(bt_value)
            if recipient is None or not compatibility.is_compatible(bt, recipient, component):
                problems.append(f"{bt.value} {component.value} is not compatible with the patient")
        usable = usable_units_at(db, facility.id, recipient, component, arrival, em.id)
        for bt_value, n in leg.units_by_type.items():
            if usable.get(BloodType(bt_value), 0) < n:
                problems.append(
                    f"{facility.name or facility.id} no longer has {n} usable {bt_value} "
                    f"{component.value} unit(s) at arrival"
                )
    return problems


def usable_units_at(
    db: Session,
    facility_id: uuid.UUID,
    recipient: BloodType | None,
    component: BloodComponent,
    arrival: datetime,
    emergency_id: uuid.UUID | None,
) -> dict[BloodType, int]:
    if recipient is None:
        return {}
    compat = compatibility.compatible_source_types(recipient, component)
    rows = db.execute(
        planner._USABLE_AT_ARRIVAL_SQL,
        {
            "hids": [str(facility_id)],
            "cutoffs": [arrival],
            "component": component.value,
            "types": [t.value for t in compat],
            "em": str(emergency_id) if emergency_id else None,
        },
    ).all()
    return {BloodType(bt) if not isinstance(bt, BloodType) else bt: int(n) for _h, bt, n in rows}


# --------------------------------------------------------------------------- #
# Failure handling
# --------------------------------------------------------------------------- #
@dataclass
class ReoptimizationOutcome:
    emergency_id: uuid.UUID
    previous_version: int
    new_version: int
    still_reachable: bool
    best_distance_km: float | None
    # BACKUP_PROMOTED | REPLANNED | RECOVERY_REQUIRED | BACKUPS_INVALIDATED | IN_EXECUTION
    kind: str = "REPLANNED"
    invalidated_backups: list[int] = field(default_factory=list)
    rejected_backups: dict[int, list[str]] = field(default_factory=dict)


def _invalidate(p: EmergencyPathway, reason: str, now: datetime) -> None:
    p.status = EmergencyPathwayStatus.INVALIDATED
    p.invalidated_at = now
    p.invalidated_reason = reason[:500]


def _attempt_activation(
    db: Session,
    em: Emergency,
    candidate: EmergencyPathway,
    *,
    demote: EmergencyPathway | None,
    reserve: bool,
    actor_user_id: uuid.UUID | None,
    now: datetime,
) -> str | None:
    """Inside one SAVEPOINT: optionally demote the current ACTIVE row, make
    `candidate` ACTIVE and, if `reserve`, reserve its units. Returns None on
    success. On an InventoryActionError EVERYTHING the attempt did (status
    change, any partial reservation, ledger rows) is rolled back and the
    error text is returned -- so a plan is never left ACTIVE-but-unsecured
    by a failed attempt (audit C15)."""
    savepoint = db.begin_nested()
    try:
        if demote is not None:
            demote.status = EmergencyPathwayStatus.BACKUP
            db.flush()
        if candidate.status != EmergencyPathwayStatus.ACTIVE:
            candidate.status = EmergencyPathwayStatus.ACTIVE
            candidate.promoted_at = now
        db.flush()
        if reserve:
            reserve_pathway(db, em, candidate, actor_user_id=actor_user_id)
        savepoint.commit()
        return None
    except InventoryActionError as exc:
        savepoint.rollback()
        return str(exc)


def _mark_recovery_required(
    db: Session,
    em: Emergency,
    pathway: EmergencyPathway,
    *,
    reason: str,
    actor_user_id: uuid.UUID | None,
) -> None:
    """No candidate plan could be secured. The best plan stays ACTIVE so the
    coordinator can see it, but it is explicitly RECOVERY_REQUIRED (never
    reported as secured), an event and audit record are written, and the
    emergency's coordinator and destination facility are notified."""
    pathway.recovery_required_at = utcnow()
    pathway.recovery_required_reason = reason[:500]
    db.flush()
    _event(db, em, actor_user_id, "pathway_recovery_required",
           f"Pathway v{pathway.version} is active but its units could NOT be reserved "
           f"({reason}). The emergency is not secured; coordinator action needed.")
    audit_service.record(
        db, "emergency.pathway_recovery_required", actor_user_id=actor_user_id,
        target_type="emergency", target_id=em.id,
        meta={"pathway_version": pathway.version, "reason": reason[:300]},
    )
    recipients = {em.created_by_user_id}
    dest = db.get(HospitalProfile, pathway.origin_facility_id)
    if dest is not None:
        recipients.add(dest.user_id)
    for uid in recipients:
        notification_service.notify(
            db, uid, type="emergency.pathway_recovery_required",
            title="Emergency plan needs attention: units could not be reserved",
            body=f"Pathway v{pathway.version} is the best available plan, but its units "
                 "could not be reserved. The emergency is NOT secured.",
            data={"emergency_id": str(em.id), "pathway_version": pathway.version},
        )


def _secure_round(
    db: Session,
    em: Emergency,
    primary: EmergencyPathway,
    backups: list[EmergencyPathway],
    *,
    actor_user_id: uuid.UUID | None,
) -> EmergencyPathway:
    """Reserve the new round's units, falling back to its backups in rank
    order when the primary's units cannot be reserved. Returns the pathway
    that ends up ACTIVE. If none can be reserved, the original primary stays
    ACTIVE and is marked RECOVERY_REQUIRED."""
    if primary.selected_facility_id is None:
        return primary  # nothing reachable: IMPOSSIBLE plan, nothing to reserve
    now = utcnow()
    err = _attempt_activation(db, em, primary, demote=None, reserve=True,
                              actor_user_id=actor_user_id, now=now)
    if err is None:
        return primary
    failures = [f"v{primary.version}: {err}"]
    _event(db, em, actor_user_id, "pathway_reservation_failed",
           f"Pathway v{primary.version}: {err}")
    for b in backups:
        if b.status != EmergencyPathwayStatus.BACKUP:
            continue
        problems = validate_pathway(db, em, b, now=now)
        if problems:
            _invalidate(b, "; ".join(problems), now)
            failures.append(f"v{b.version}: " + "; ".join(problems))
            continue
        err_b = _attempt_activation(db, em, b, demote=primary, reserve=True,
                                    actor_user_id=actor_user_id, now=now)
        if err_b is None:
            _invalidate(primary, f"units could not be reserved: {err}", now)
            db.flush()
            _event(db, em, actor_user_id, "backup_promoted",
                   f"Pathway v{primary.version}'s units could not be reserved ({err}); backup "
                   f"v{b.version} was promoted and reserved instead.")
            return b
        _invalidate(b, f"units could not be reserved: {err_b}", now)
        failures.append(f"v{b.version}: {err_b}")
        _event(db, em, actor_user_id, "pathway_reservation_failed",
               f"Pathway v{b.version}: {err_b}")
    db.flush()
    _mark_recovery_required(db, em, primary, reason="; ".join(failures),
                            actor_user_id=actor_user_id)
    return primary


def fail_over(
    db: Session,
    em: Emergency,
    failed: EmergencyPathway,
    *,
    reason: str,
    actor_user_id: uuid.UUID | None,
    require_reservation: bool = False,
    units_needed: int | None = None,
) -> ReoptimizationOutcome:
    """The ACTIVE pathway `failed` can no longer be used. Invalidate it,
    promote the best backup that is still valid right now, or -- if none
    is -- run a fresh plan round. Reservations follow the plan.

    Audit C15 (2026-10-31): when the failed plan held reservations (or the
    caller says the emergency was secured, e.g. its shipment failed), a
    backup is promoted ONLY together with a successful reservation of its
    units, in one SAVEPOINT. A backup whose units cannot be reserved is
    invalidated and the next one is tried; if none works, a fresh plan round
    is made and secured the same way; if that fails too, the best plan is
    ACTIVE but marked RECOVERY_REQUIRED and the coordinator is notified.
    Before, the backup was promoted first and a reservation failure only
    left an event behind, so an unsecured plan looked secured."""
    now = utcnow()
    previous_version = failed.version
    had_reservations = release_pathway_reservations(
        db, failed, actor_user_id=actor_user_id, reason=f"pathway failed: {reason}"
    ) or require_reservation
    _invalidate(failed, reason, now)
    db.flush()

    rejected: dict[int, list[str]] = {}
    backups = db.scalars(
        select(EmergencyPathway)
        .where(
            EmergencyPathway.emergency_id == em.id,
            EmergencyPathway.status == EmergencyPathwayStatus.BACKUP,
        )
        .order_by(EmergencyPathway.plan_round.desc(), EmergencyPathway.backup_rank)
    ).all()
    if units_needed is not None and units_needed != (em.units_needed or 1):
        # Backups were planned for the full quantity; part of the order is
        # still on its way, so a fresh round for the remainder is planned.
        for b in backups:
            rejected[b.version] = [f"planned for a different quantity (now {units_needed})"]
            _invalidate(b, f"superseded: only {units_needed} unit(s) still needed", now)
        backups = []
    for b in backups:
        problems = validate_pathway(db, em, b, now=now)
        if problems:
            rejected[b.version] = problems
            _invalidate(b, "; ".join(problems), now)
            continue
        err = _attempt_activation(db, em, b, demote=None, reserve=had_reservations,
                                  actor_user_id=actor_user_id, now=now)
        if err is not None:
            rejected[b.version] = [f"units could not be reserved: {err}"]
            _invalidate(b, f"units could not be reserved: {err}", now)
            _event(db, em, actor_user_id, "pathway_reservation_failed",
                   f"Backup v{b.version} not promoted: {err}")
            db.flush()
            continue
        _event(
            db,
            em,
            actor_user_id,
            "backup_promoted",
            f"Pathway v{previous_version} failed ({reason}); backup v{b.version} promoted "
            "after re-validation (routes, verification, stock, deadline, compatibility)"
            + (" and its units were reserved." if had_reservations else "."),
        )
        return ReoptimizationOutcome(
            emergency_id=em.id,
            previous_version=previous_version,
            new_version=b.version,
            still_reachable=True,
            best_distance_km=b.distance_km,
            kind="BACKUP_PROMOTED",
            rejected_backups=rejected,
        )

    primary, _backups, result = create_plan_round(
        db,
        em,
        requested_by_user_id=actor_user_id,
        reason=f"replan after failure: {reason}",
        destination_facility_id=failed.origin_facility_id,
        require_reservation=had_reservations,
        units_needed=units_needed,
    )
    recovery = primary.recovery_required_at is not None
    _event(
        db,
        em,
        actor_user_id,
        "pathway_replanned",
        f"Pathway v{previous_version} failed ({reason}); no valid backup remained, so a new "
        f"plan was made (v{primary.version}, "
        f"{primary.feasibility.value if primary.feasibility else 'n/a'})"
        + ("; its units could NOT be reserved -- recovery required." if recovery else "."),
    )
    return ReoptimizationOutcome(
        emergency_id=em.id,
        previous_version=previous_version,
        new_version=primary.version,
        still_reachable=primary.selected_facility_id is not None,
        best_distance_km=primary.distance_km,
        kind="RECOVERY_REQUIRED" if recovery else "REPLANNED",
        rejected_backups=rejected,
    )


def _affected_by_route(db: Session, route_id: uuid.UUID) -> list[EmergencyPathway]:
    # IN (subquery) rather than JOIN + DISTINCT: the pathway row has JSON
    # columns, which PostgreSQL cannot compare for DISTINCT.
    uses_route = select(EmergencyPathwayStep.pathway_id).where(
        EmergencyPathwayStep.route_id == route_id
    )
    return list(
        db.scalars(
            select(EmergencyPathway)
            .join(Emergency, Emergency.id == EmergencyPathway.emergency_id)
            .where(
                EmergencyPathway.id.in_(uses_route),
                EmergencyPathway.status.in_(LIVE_STATUSES),
                Emergency.status.in_(OPEN_STATUSES),
            )
            .order_by(EmergencyPathway.version)
        ).all()
    )


def _handle_affected(
    db: Session,
    affected: list[EmergencyPathway],
    *,
    reason: str,
    event_type: str,
    actor_user_id: uuid.UUID | None,
) -> list[ReoptimizationOutcome]:
    outcomes: list[ReoptimizationOutcome] = []
    by_emergency: dict[uuid.UUID, list[EmergencyPathway]] = {}
    for p in affected:
        by_emergency.setdefault(p.emergency_id, []).append(p)
    now = utcnow()
    for emergency_id in sorted(by_emergency, key=str):
        em = db.get(Emergency, emergency_id, with_for_update=True)
        pathways = by_emergency[emergency_id]
        for p in pathways:
            db.refresh(p)
        active = [p for p in pathways if p.status == EmergencyPathwayStatus.ACTIVE]
        invalidated_backups = []
        for p in pathways:
            if p.status == EmergencyPathwayStatus.BACKUP:
                _invalidate(p, reason, now)
                invalidated_backups.append(p.version)
        _event(db, em, actor_user_id, event_type, reason)
        if not active:
            outcomes.append(
                ReoptimizationOutcome(
                    emergency_id=em.id,
                    previous_version=0,
                    new_version=0,
                    still_reachable=True,
                    best_distance_km=None,
                    kind="BACKUPS_INVALIDATED",
                    invalidated_backups=invalidated_backups,
                )
            )
            continue
        from app.services import pathway_handoff_service

        live = pathway_handoff_service.live_shipments_of(db, active[0].id)
        if live:
            # Audit C21/C20: the plan is already executing -- its units are in
            # shipments. Re-planning silently around blood that may be on the
            # road would double-commit stock; the coordinator decides, and
            # failing/cancelling the shipment is what triggers re-planning.
            codes = ", ".join(s.shipment_code for s in live)
            _event(db, em, actor_user_id, "disruption_during_execution",
                   f"{reason}. Pathway v{active[0].version} is already executing in "
                   f"shipment(s) {codes}; not re-planned automatically. Fail or cancel the "
                   "affected shipment to re-plan.")
            notification_service.notify(
                db, em.created_by_user_id, type="emergency.pathway_disrupted_in_execution",
                title="Emergency shipment route disrupted",
                body=f"A route on the active delivery plan failed while shipment(s) {codes} "
                     "are executing it. Check with the origin facility.",
                data={"emergency_id": str(em.id), "pathway_version": active[0].version},
            )
            outcomes.append(ReoptimizationOutcome(
                emergency_id=em.id, previous_version=active[0].version,
                new_version=active[0].version, still_reachable=True,
                best_distance_km=active[0].distance_km, kind="IN_EXECUTION",
                invalidated_backups=invalidated_backups,
            ))
            continue
        outcome = fail_over(db, em, active[0], reason=reason, actor_user_id=actor_user_id)
        outcome.invalidated_backups = invalidated_backups
        db.add(
            EmergencyEvent(
                emergency_id=em.id,
                actor_user_id=actor_user_id,
                event_type="pathway_reoptimized",
                note=(
                    f"Pathway re-optimized (v{outcome.previous_version} -> v{outcome.new_version}, "
                    f"{outcome.kind}): "
                    + (
                        f"best source {outcome.best_distance_km} km away"
                        if outcome.still_reachable
                        else "no viable pathway currently reachable"
                    )
                ),
            )
        )
        body = "A failure affected your emergency's delivery plan. " + (
            f"LifeLink switched to pathway v{outcome.new_version}."
            if outcome.still_reachable
            else "No alternative is currently reachable -- coordinators have been notified."
        )
        notification_service.notify(
            db,
            em.created_by_user_id,
            type="emergency.pathway_reoptimized",
            title="Emergency route update",
            body=body,
            data={"emergency_id": str(em.id), "pathway_version": outcome.new_version},
        )
        outcomes.append(outcome)
    db.flush()
    return outcomes


def handle_route_disrupted(
    db: Session,
    route: TransportRoute,
    *,
    admin_user_id: uuid.UUID | None,
) -> list[ReoptimizationOutcome]:
    """React to `route` becoming DISRUPTED. Only pathways that travel over
    this exact route are affected (first, middle or last step alike); an
    unrelated route -- even one touching the same facility -- changes
    nothing."""
    affected = _affected_by_route(db, route.id)
    reason = f"transport route {route.id} disrupted" + (
        f": {route.disrupted_reason}" if route.disrupted_reason else ""
    )
    return _handle_affected(
        db, affected, reason=reason, event_type="transport_disrupted", actor_user_id=admin_user_id
    )


def handle_routes_disrupted(
    db: Session,
    routes: list[TransportRoute],
    *,
    admin_user_id: uuid.UUID | None,
) -> list[ReoptimizationOutcome]:
    """Several routes failed at once (handled as one event per emergency, so
    a backup that uses any of the failed routes is never promoted)."""
    ids = {r.id for r in routes}
    affected: dict[uuid.UUID, EmergencyPathway] = {}
    for rid in ids:
        for p in _affected_by_route(db, rid):
            affected[p.id] = p
    reason = f"{len(ids)} transport route(s) disrupted"
    return _handle_affected(
        db,
        list(affected.values()),
        reason=reason,
        event_type="transport_disrupted",
        actor_user_id=admin_user_id,
    )


def handle_facility_unverified(
    db: Session,
    facility_id: uuid.UUID,
    *,
    admin_user_id: uuid.UUID | None,
) -> list[ReoptimizationOutcome]:
    """A facility lost its verification: live pathways sourcing from it
    (unless the emergency has an UNVERIFIED_FACILITY override) fail over.
    Historical pathways are untouched."""
    sources_from = select(EmergencyPathwayLeg.pathway_id).where(
        EmergencyPathwayLeg.source_facility_id == facility_id
    )
    affected = list(
        db.scalars(
            select(EmergencyPathway)
            .join(Emergency, Emergency.id == EmergencyPathway.emergency_id)
            .where(
                EmergencyPathway.id.in_(sources_from),
                EmergencyPathway.status.in_(LIVE_STATUSES),
                Emergency.status.in_(OPEN_STATUSES),
            )
            .order_by(EmergencyPathway.version)
        ).all()
    )
    affected = [
        p
        for p in affected
        if not active_override(db, p.emergency_id, EmergencyOverrideKind.UNVERIFIED_FACILITY)
    ]
    return _handle_affected(
        db,
        affected,
        reason=f"source facility {facility_id} lost verification",
        event_type="facility_unverified",
        actor_user_id=admin_user_id,
    )


# --------------------------------------------------------------------------- #
# Reservations
# --------------------------------------------------------------------------- #
def reserve_pathway(
    db: Session,
    em: Emergency,
    pathway: EmergencyPathway,
    *,
    actor_user_id: uuid.UUID | None,
) -> list[PathwayReservation]:
    """Reserve, at each leg's source facility, exactly the units the leg
    plans to take (by blood type). All-or-nothing: if any reservation fails
    the whole transaction is rolled back by the caller's error handling
    (InventoryActionError propagates). Units already reserved for this
    pathway are not reserved twice."""
    if pathway.status != EmergencyPathwayStatus.ACTIVE:
        raise InventoryActionError("Only the ACTIVE pathway can be reserved")
    component = em.component or BloodComponent.WHOLE_BLOOD
    existing = db.scalars(
        select(PathwayReservation).where(
            PathwayReservation.pathway_id == pathway.id,
            PathwayReservation.active.is_(True),
        )
    ).all()
    if existing:
        return list(existing)
    created: list[PathwayReservation] = []
    now = utcnow()
    for leg in legs_of(db, pathway.id):
        arrival = now + timedelta(minutes=leg.eta_minutes)
        for bt_value, n in leg.units_by_type.items():
            bt = BloodType(bt_value)
            # Only units still in date when this leg's delivery arrives.
            inventory_service.reserve_stock(
                db,
                leg.source_facility_id,
                bt,
                int(n),
                actor_user_id=actor_user_id,
                emergency_id=em.id,
                component=component,
                usable_until=arrival,
                note=f"Reserved for emergency pathway v{pathway.version}",
            )
            res = PathwayReservation(
                pathway_id=pathway.id,
                leg_id=leg.id,
                emergency_id=em.id,
                facility_id=leg.source_facility_id,
                blood_type=bt,
                component=component,
                units=int(n),
                created_by_user_id=actor_user_id,
            )
            db.add(res)
            created.append(res)
    if pathway.recovery_required_at is not None:
        pathway.recovery_required_at = None
        pathway.recovery_required_reason = None
    db.flush()
    _event(
        db,
        em,
        actor_user_id,
        "pathway_reserved",
        f"Reserved {sum(r.units for r in created)} unit(s) for pathway v{pathway.version}",
    )
    audit_service.record(
        db,
        "emergency.pathway_reserved",
        actor_user_id=actor_user_id,
        target_type="emergency",
        target_id=em.id,
        meta={
            "pathway_version": pathway.version,
            "reservations": [
                {
                    "facility_id": str(r.facility_id),
                    "blood_type": r.blood_type.value,
                    "units": r.units,
                }
                for r in created
            ],
        },
    )
    return created


def release_pathway_reservations(
    db: Session,
    pathway: EmergencyPathway,
    *,
    actor_user_id: uuid.UUID | None,
    reason: str,
) -> bool:
    """Release every active reservation `pathway` holds. Returns whether
    there was anything to release."""
    active = db.scalars(
        select(PathwayReservation).where(
            PathwayReservation.pathway_id == pathway.id,
            PathwayReservation.active.is_(True),
        )
    ).all()
    if not active:
        return False
    now = utcnow()
    for r in active:
        try:
            inventory_service.release_reservation(
                db,
                r.facility_id,
                r.blood_type,
                r.units,
                actor_user_id=actor_user_id,
                emergency_id=r.emergency_id,
                component=r.component,
                note=reason[:300],
            )
        except InventoryActionError as exc:
            # The units are no longer RESERVED (e.g. already allocated or
            # expired by the sweep). The reservation row is closed either
            # way, and the reason says why -- never reported as a release.
            reason = f"{reason} (nothing left to release: {exc})"
        r.active = False
        r.released_at = now
        r.release_reason = reason[:300]
    db.flush()
    return True


def config_snapshot() -> dict:
    """The planning assumptions currently configured (for the UI)."""
    return {
        "handling_minutes": settings.pathway_handling_minutes,
        "deadline_risk_minutes": settings.pathway_deadline_risk_minutes,
        "max_hops": settings.pathway_max_hops,
        "max_backups": settings.pathway_max_backups,
        "max_donor_distance_km": settings.max_donor_distance_km,
        "hospital_alert_radius_km": settings.hospital_alert_radius_km,
        "straight_line_speed_kmph": planner.ASSUMED_ROAD_SPEED_KMPH,
        "strategies": {
            u.value: {
                "name": s.name,
                "objective": s.objective,
                "keys": list(s.keys),
                "description": s.description,
            }
            for u, s in planner.STRATEGIES.items()
        },
    }
