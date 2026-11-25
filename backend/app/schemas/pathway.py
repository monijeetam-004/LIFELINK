"""API shapes for the pathway engine: stored plans (with every leg and every
route step) and dry-run planner results (with every evaluated facility,
including rejected ones and why)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.models.emergency_pathway import EmergencyPathway
from app.models.enums import (
    BloodComponent,
    BloodType,
    EmergencyPathwayStatus,
    GraphPathwaySource,
    PathwayFeasibility,
    Urgency,
)
from app.models.hospital import HospitalProfile


class PathwayStepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    step_index: int
    route_id: uuid.UUID
    from_facility_id: uuid.UUID
    to_facility_id: uuid.UUID
    distance_km: float
    transit_minutes: int
    route_status_at_plan: str
    route_status_now: str | None = None


class PathwayLegOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    leg_index: int
    source_facility_id: uuid.UUID
    source_facility_name: str | None = None
    destination_facility_id: uuid.UUID
    units: int
    units_by_type: dict[str, int]
    route_basis: str
    node_ids: list[uuid.UUID]
    distance_km: float
    straight_line_km: float | None
    transit_minutes: int
    eta_minutes: int
    hops: int
    steps: list[PathwayStepOut] = []


class PathwayShipmentOut(BaseModel):
    id: uuid.UUID
    shipment_code: str
    status: str
    origin_facility_id: uuid.UUID
    destination_facility_id: uuid.UUID
    units: int


class StoredPathwayOut(BaseModel):
    """A stored pathway (primary, backup, or historical) in full."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    version: int
    plan_round: int
    backup_rank: int
    status: EmergencyPathwayStatus
    feasibility: PathwayFeasibility | None
    origin_facility_id: uuid.UUID  # the delivery destination
    selected_facility_id: uuid.UUID | None
    pathway_source: GraphPathwaySource
    graph_available: bool
    component: BloodComponent | None
    recipient_blood_type: BloodType | None
    urgency: Urgency | None
    units_requested: int | None
    units_planned: int | None
    distance_km: float | None
    transit_minutes: int | None
    hops: int | None
    eta_minutes: int | None
    arrival_at: datetime | None
    deadline_at: datetime | None
    slack_minutes: float | None
    strategy: str | None
    explanation: list[str] | None
    assumptions: dict | None
    reason: str
    created_at: datetime
    promoted_at: datetime | None
    superseded_at: datetime | None
    superseded_reason: str | None
    invalidated_at: datetime | None
    invalidated_reason: str | None
    reserved_units: int = 0
    # Audit C15/C21 (2026-10-31): what securing state the plan is in.
    #   RESERVED           its units are reserved for the emergency
    #   HANDED_OFF         its reservation moved, unreleased, into shipment(s)
    #   RECOVERY_REQUIRED  it should be secured but its units could not be reserved
    #   UNRESERVED         nothing reserved (normal before a coordinator reserves)
    reservation_status: str = "UNRESERVED"
    recovery_required_at: datetime | None = None
    recovery_required_reason: str | None = None
    shipments: list[PathwayShipmentOut] = []
    legs: list[PathwayLegOut] = []


def stored_pathway_out(db: Session, p: EmergencyPathway) -> StoredPathwayOut:
    from sqlalchemy import func, select

    from app.models.emergency_pathway import PathwayReservation
    from app.models.transport import TransportRoute
    from app.services.emergency_pathway_service import legs_of, steps_of

    out = StoredPathwayOut.model_validate(p)
    steps = steps_of(db, p.id)
    statuses = (
        dict(
            db.execute(
                select(TransportRoute.id, TransportRoute.status).where(
                    TransportRoute.id.in_([s.route_id for s in steps])
                )
            ).all()
        )
        if steps
        else {}
    )
    by_leg: dict[uuid.UUID, list] = {}
    for s in sorted(steps, key=lambda s: s.step_index):
        by_leg.setdefault(s.leg_id, []).append(s)
    legs = []
    for leg in legs_of(db, p.id):
        facility = db.get(HospitalProfile, leg.source_facility_id)
        lo = PathwayLegOut.model_validate(leg)
        lo.route_basis = leg.route_basis.value
        lo.source_facility_name = facility.name if facility else None
        lo.steps = []
        for s in by_leg.get(leg.id, []):
            so = PathwayStepOut.model_validate(s)
            so.route_status_at_plan = s.route_status_at_plan.value
            current = statuses.get(s.route_id)
            so.route_status_now = current.value if current is not None else None
            lo.steps.append(so)
        legs.append(lo)
    out.legs = legs
    out.reserved_units = int(
        db.scalar(
            select(func.coalesce(func.sum(PathwayReservation.units), 0)).where(
                PathwayReservation.pathway_id == p.id, PathwayReservation.active.is_(True)
            )
        )
        or 0
    )
    from app.models.shipment import Shipment, ShipmentItem

    ships = db.scalars(
        select(Shipment).where(Shipment.pathway_id == p.id).order_by(Shipment.created_at)
    ).all()
    out.shipments = [
        PathwayShipmentOut(
            id=sh.id, shipment_code=sh.shipment_code, status=sh.status.value,
            origin_facility_id=sh.origin_facility_id,
            destination_facility_id=sh.destination_facility_id,
            units=int(db.scalar(select(func.count()).select_from(ShipmentItem).where(
                ShipmentItem.shipment_id == sh.id)) or 0),
        )
        for sh in ships
    ]
    if p.recovery_required_at is not None:
        out.reservation_status = "RECOVERY_REQUIRED"
    elif out.shipments:
        # Any reservation still held is the destination's own local stock.
        out.reservation_status = "HANDED_OFF"
    elif out.reserved_units > 0:
        out.reservation_status = "RESERVED"
    else:
        out.reservation_status = "UNRESERVED"
    return out


# --------------------------------------------------------------------------- #
# Dry-run planner output
# --------------------------------------------------------------------------- #
class FacilityOptionOut(BaseModel):
    facility_id: uuid.UUID
    facility_name: str | None
    city: str | None
    verified: bool
    route_basis: str | None
    node_ids: list[uuid.UUID]
    route_ids: list[uuid.UUID]
    distance_km: float | None
    straight_line_km: float | None
    transit_minutes: int | None
    eta_minutes: int | None
    slack_minutes: float | None
    usable_by_type: dict[str, int]
    usable_total: int
    rejected_reason: str | None


class PlanLegOut(BaseModel):
    facility_id: uuid.UUID
    facility_name: str | None
    units: int
    units_by_type: dict[str, int]
    route_basis: str | None
    route_ids: list[uuid.UUID]
    node_ids: list[uuid.UUID]
    eta_minutes: int | None
    distance_km: float | None


class CandidatePlanOut(BaseModel):
    feasibility: PathwayFeasibility
    units_requested: int
    units_planned: int
    eta_minutes: int | None
    arrival_at: datetime | None
    slack_minutes: float | None
    explanation: list[str]
    legs: list[PlanLegOut]


class PlanResultOut(BaseModel):
    strategy: str
    strategy_description: str
    strategy_keys: list[str]
    pathway_source: GraphPathwaySource
    graph_available: bool
    degraded_reason: str | None
    recipient_blood_type: BloodType
    component: BloodComponent
    units_needed: int
    urgency: Urgency
    deadline: datetime | None
    destination_facility_id: uuid.UUID
    compatible_types: list[BloodType]
    disrupted_routes_excluded: int
    simulated_failed_route_ids: list[uuid.UUID] = []
    assumptions: dict
    primary: CandidatePlanOut
    backups: list[CandidatePlanOut]
    options: list[FacilityOptionOut]
    persisted: bool = False


def _option_out(o) -> FacilityOptionOut:
    return FacilityOptionOut(
        facility_id=o.facility_id,
        facility_name=o.facility_name,
        city=o.city,
        verified=o.verified,
        route_basis=o.route_basis.value if o.route_basis else None,
        node_ids=list(o.node_ids),
        route_ids=o.route_ids,
        distance_km=o.distance_km,
        straight_line_km=o.straight_line_km,
        transit_minutes=o.transit_minutes,
        eta_minutes=o.eta_minutes,
        slack_minutes=o.slack_minutes,
        usable_by_type={t.value: n for t, n in o.usable_by_type.items()},
        usable_total=o.usable_total,
        rejected_reason=o.rejected_reason,
    )


def _candidate_out(c) -> CandidatePlanOut:
    return CandidatePlanOut(
        feasibility=c.feasibility,
        units_requested=c.units_requested,
        units_planned=c.units_planned,
        eta_minutes=c.eta_minutes,
        arrival_at=c.arrival_at,
        slack_minutes=c.slack_minutes,
        explanation=list(c.explanation),
        legs=[
            PlanLegOut(
                facility_id=leg.option.facility_id,
                facility_name=leg.option.facility_name,
                units=leg.units,
                units_by_type={t.value: n for t, n in leg.units_by_type.items()},
                route_basis=leg.option.route_basis.value if leg.option.route_basis else None,
                route_ids=leg.option.route_ids,
                node_ids=list(leg.option.node_ids),
                eta_minutes=leg.option.eta_minutes,
                distance_km=leg.option.distance_km,
            )
            for leg in c.legs
        ],
    )


def plan_result_out(result, *, simulated_failed: list[uuid.UUID] | None = None) -> PlanResultOut:
    r = result.request
    return PlanResultOut(
        strategy=result.strategy.name,
        strategy_description=result.strategy.description,
        strategy_keys=list(result.strategy.keys),
        pathway_source=result.source,
        graph_available=result.graph_available,
        degraded_reason=result.degraded_reason,
        recipient_blood_type=r.recipient_type,
        component=r.component,
        units_needed=r.units_needed,
        urgency=r.urgency,
        deadline=r.deadline,
        destination_facility_id=r.destination_facility_id,
        compatible_types=list(result.compatible_types),
        disrupted_routes_excluded=result.disrupted_routes_excluded,
        simulated_failed_route_ids=list(simulated_failed or []),
        assumptions=result.assumptions,
        primary=_candidate_out(result.primary),
        backups=[_candidate_out(b) for b in result.backups],
        options=[_option_out(o) for o in result.options],
    )


class PathwaySimulateIn(BaseModel):
    """Dry run of the planner -- nothing is stored or reserved."""

    recipient_blood_type: BloodType
    component: BloodComponent = BloodComponent.WHOLE_BLOOD
    units_needed: int = Field(default=1, ge=1, le=50)
    urgency: Urgency = Urgency.CRITICAL
    deadline_minutes: int | None = Field(default=None, ge=0, le=7 * 24 * 60)
    destination_facility_id: uuid.UUID
    failed_route_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    max_backups: int | None = Field(default=None, ge=0, le=10)


class EmergencyOverrideIn(BaseModel):
    kind: str = Field(pattern="^(DONOR_DISTANCE|UNVERIFIED_FACILITY)$")
    reason: str = Field(min_length=10, max_length=500)


class EmergencyOverrideOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    emergency_id: uuid.UUID
    kind: str
    reason: str
    created_by_user_id: uuid.UUID | None
    created_at: datetime
    revoked_at: datetime | None
    revoked_by_user_id: uuid.UUID | None
