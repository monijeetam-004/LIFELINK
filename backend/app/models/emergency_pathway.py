"""Phase 12 -- versioned resource-pathway history for an emergency.

`GraphPathwayLog` (app/models/graph_pathway_log.py) is, and remains, a
fire-and-forget audit trail of every ad hoc pathway *query* -- it has no
concept of "the currently active plan for this emergency" and this module
does not change that in any way (still written exactly as before, at the
same two call sites).

`EmergencyPathway` is the genuinely new thing the Phase 12 spec asks for:
a *persisted, versioned* resource pathway selection for one emergency, built
on top of the existing, unmodified `graph_service.find_resource_pathways()`.
Selecting a pathway (first time) creates version 1; a later re-optimization
(e.g. after `POST /admin/transport-routes/{id}/disrupt` finds this
emergency was relying on the disrupted route) creates version 2, marking
version 1 SUPERSEDED -- never overwritten, never deleted, so the full
v1 -> v2 -> v3 history stays auditable (`app.services.emergency_pathway_service`).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.models.enums import (
    BloodComponent,
    BloodType,
    EmergencyPathwayStatus,
    GraphPathwaySource,
    PathwayFeasibility,
    RouteBasis,
    TransportRouteStatus,
    Urgency,
)


class EmergencyPathway(TimestampMixin, Base):
    __tablename__ = "emergency_pathways"
    __table_args__ = (
        # At most one ACTIVE row per emergency at a time -- a partial unique
        # index (Postgres-specific syntax below) rather than a plain
        # UniqueConstraint on (emergency_id, status), since SUPERSEDED rows
        # for the same emergency must be allowed to accumulate freely (v1,
        # v2, v3 are all SUPERSEDED except the newest).
        Index(
            "uq_emergency_pathway_one_active",
            "emergency_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    emergency_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("emergencies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[EmergencyPathwayStatus] = mapped_column(
        Enum(EmergencyPathwayStatus, name="emergency_pathway_status"),
        nullable=False,
        default=EmergencyPathwayStatus.ACTIVE,
        index=True,
    )
    origin_facility_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hospital_profiles.id", ondelete="CASCADE"), nullable=False
    )
    # The top-ranked candidate facility this pathway version selected --
    # nullable because an honest "no viable pathway exists right now" is a
    # real, reportable outcome (see graph_service.find_resource_pathways
    # returning an empty candidate list), not an error to hide.
    selected_facility_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("hospital_profiles.id", ondelete="SET NULL"), nullable=True
    )
    pathway_source: Mapped[GraphPathwaySource] = mapped_column(
        Enum(GraphPathwaySource, name="graph_pathway_source"), nullable=False
    )
    distance_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    transit_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hops: Mapped[int | None] = mapped_column(Integer, nullable=True)
    graph_available: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    # Why this version was created: "initial_selection",
    # "transport_disruption", "manual_reoptimization", etc.
    reason: Mapped[str] = mapped_column(String(300), nullable=False)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # ---- Gap elimination: what the plan actually promises ----
    # A plan round is one planning run: one ACTIVE (primary) row plus up to
    # PATHWAY_MAX_BACKUPS BACKUP rows sharing the same round number.
    plan_round: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    # 0 for the primary, 1..N for backups (in promotion order).
    backup_rank: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    feasibility: Mapped[PathwayFeasibility | None] = mapped_column(
        Enum(PathwayFeasibility, name="pathway_feasibility"), nullable=True
    )
    component: Mapped[BloodComponent | None] = mapped_column(
        Enum(BloodComponent, name="blood_component"), nullable=True
    )
    recipient_blood_type: Mapped[BloodType | None] = mapped_column(
        Enum(BloodType, name="blood_type"), nullable=True
    )
    urgency: Mapped[Urgency | None] = mapped_column(Enum(Urgency, name="urgency"), nullable=True)
    units_requested: Mapped[int | None] = mapped_column(Integer, nullable=True)
    units_planned: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Expected minutes from planning time until the LAST leg arrives
    # (legs travel in parallel), including handling time.
    eta_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    arrival_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # deadline - arrival, in minutes (negative = late). None without a deadline.
    slack_minutes: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Which deterministic ranking strategy chose this plan (by urgency).
    strategy: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Plain-language reasons, in order -- built from the plan's own values.
    explanation: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Planning assumptions in force when this plan was made
    # (handling minutes, risk margin, max hops) -- so a later config change
    # never rewrites what an old plan meant.
    assumptions: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    invalidated_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Audit C15 (2026-10-31): set when this ACTIVE plan should hold a
    # reservation (the emergency's previous plan had one) but none of the
    # candidate plans could be reserved. The plan is then reported as
    # RECOVERY_REQUIRED, never as secured, until a reservation succeeds.
    recovery_required_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    recovery_required_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<EmergencyPathway emergency={self.emergency_id} v{self.version} {self.status.value}>"
        )


class EmergencyPathwayLeg(Base):
    """One source facility's contribution to a pathway. A single-facility
    plan has one leg; a multi-facility plan has one leg per source. Legs
    travel in parallel to the same destination."""

    __tablename__ = "emergency_pathway_legs"
    __table_args__ = (
        UniqueConstraint("pathway_id", "leg_index", name="uq_pathway_leg_index"),
        CheckConstraint("units > 0", name="ck_pathway_leg_units_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    pathway_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("emergency_pathways.id", ondelete="CASCADE"), nullable=False, index=True
    )
    leg_index: Mapped[int] = mapped_column(Integer, nullable=False)
    source_facility_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hospital_profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    destination_facility_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hospital_profiles.id", ondelete="CASCADE"), nullable=False
    )
    units: Mapped[int] = mapped_column(Integer, nullable=False)
    # {"O_NEG": 2, "O_POS": 1} -- which compatible blood types this leg
    # takes, in preference order; each was compatible when planned.
    units_by_type: Mapped[dict] = mapped_column(JSON, nullable=False)
    route_basis: Mapped[RouteBasis] = mapped_column(
        Enum(RouteBasis, name="route_basis"), nullable=False
    )
    # Ordered facility ids, source first, destination last.
    node_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    distance_km: Mapped[float] = mapped_column(Float, nullable=False)
    straight_line_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    transit_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    eta_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    hops: Mapped[int] = mapped_column(Integer, nullable=False)


class EmergencyPathwayStep(Base):
    """One transport route used by a pathway leg, in travel order. This is
    what makes route-failure handling exact: "which active pathways use
    route R" is an indexed lookup on `route_id`, so a failure in the MIDDLE
    of a path triggers replanning and a route that merely touches the same
    facility does not."""

    __tablename__ = "emergency_pathway_steps"
    __table_args__ = (
        UniqueConstraint("leg_id", "step_index", name="uq_pathway_step_index"),
        Index("ix_pathway_steps_route", "route_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    pathway_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("emergency_pathways.id", ondelete="CASCADE"), nullable=False, index=True
    )
    leg_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("emergency_pathway_legs.id", ondelete="CASCADE"), nullable=False
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    route_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("transport_routes.id", ondelete="CASCADE"), nullable=False
    )
    from_facility_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hospital_profiles.id", ondelete="CASCADE"), nullable=False
    )
    to_facility_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hospital_profiles.id", ondelete="CASCADE"), nullable=False
    )
    distance_km: Mapped[float] = mapped_column(Float, nullable=False)
    transit_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    # The route's status when the plan was made (always OPERATIONAL --
    # disrupted routes are never planned over), kept for the audit trail.
    route_status_at_plan: Mapped[TransportRouteStatus] = mapped_column(
        Enum(TransportRouteStatus, name="transport_route_status"), nullable=False
    )


class PathwayReservation(Base):
    """Units reserved at a leg's source facility on behalf of a pathway.
    Lets failover release exactly what a superseded plan held and reserve
    what the new plan needs, without guessing from the inventory ledger."""

    __tablename__ = "pathway_reservations"
    __table_args__ = (CheckConstraint("units > 0", name="ck_pathway_reservation_units_positive"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    pathway_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("emergency_pathways.id", ondelete="CASCADE"), nullable=False, index=True
    )
    leg_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("emergency_pathway_legs.id", ondelete="CASCADE"), nullable=False
    )
    emergency_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("emergencies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    facility_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hospital_profiles.id", ondelete="CASCADE"), nullable=False
    )
    blood_type: Mapped[BloodType] = mapped_column(
        Enum(BloodType, name="blood_type"), nullable=False
    )
    component: Mapped[BloodComponent] = mapped_column(
        Enum(BloodComponent, name="blood_component"), nullable=False
    )
    units: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    release_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # Audit C21 (2026-10-31): the reservation was not released but handed to
    # this shipment (the units stayed RESERVED throughout).
    handed_off_shipment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("shipments.id", ondelete="SET NULL"), nullable=True, index=True
    )
