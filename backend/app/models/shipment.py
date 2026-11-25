"""Phase D -- shipment / transport / chain-of-custody models.

Closes a gap Phase 34 explicitly disclosed rather than hid: "ALLOCATE
already means 'physically dispatched' ... there is no shipment-level
receiving confirmation ... anywhere in the product" (see
app/core/blood_unit_lifecycle.py's module docstring). This module adds
that receiving confirmation as a real, backend-enforced lifecycle --
without reopening `BloodUnitState` (still exactly 4 states, ALLOCATED
still terminal there) and without overloading `TransportRoute` (still
exactly OPERATIONAL/DISRUPTED -- a route's physical/operational condition,
kept deliberately separate from any one shipment's own lifecycle, per the
master prompt's own Section 16).

Three tables, deliberately minimal:

  Shipment      -- one specific movement of stock from one facility to
                   another. The thing with a lifecycle (ShipmentStatus).
  ShipmentItem  -- which specific, already-existing `BloodUnit` rows this
                   shipment carries. Does NOT duplicate blood_type/
                   component -- `BloodUnit` is the authoritative source for
                   both; join to it. `is_active` is the one piece of state
                   this table itself owns, enforcing Invariant 23 (a unit
                   belongs to at most one active shipment) via a partial
                   unique index (see the migration), not just application
                   logic.
  ShipmentEvent -- append-only operational timeline, mirroring the
                   existing `BloodRequestEvent`/`EmergencyEvent` pattern
                   exactly (same columns, same "one row per real thing
                   that happened" philosophy) rather than inventing a
                   fourth parallel event-log shape.

Chain-of-custody is NOT a separate, fourth table: `ShipmentEvent` (the
operational timeline) plus the existing global `AuditLog` (written
alongside it by `app.services.shipment_service`, exactly like every other
domain's lifecycle transitions already do) together answer every
chain-of-custody question Phase D's spec asks for (who/what/when/where/
which shipment/which unit/old state/new state/why) -- see
docs/LIFELINK_SHIPMENT_LIFECYCLE.md.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.models.enums import (
    ColdChainStatus,
    ShipmentItemOutcome,
    ShipmentReconciliationStatus,
    ShipmentStatus,
    TelemetrySource,
)


class Shipment(TimestampMixin, Base):
    __tablename__ = "shipments"
    __table_args__ = (
        UniqueConstraint("shipment_code", name="uq_shipments_shipment_code"),
        # Phase 19/26 idempotency precedent, applied a third time: scoped
        # per-creator, exactly like uq_blood_request_idempotency_key /
        # uq_donation_idempotency_key -- not a new mechanism.
        UniqueConstraint(
            "created_by_user_id", "idempotency_key", name="uq_shipment_idempotency_key"
        ),
        Index("ix_shipments_origin_status", "origin_facility_id", "status"),
        Index("ix_shipments_destination_status", "destination_facility_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # Human-readable, PII-free, globally unique -- see
    # shipment_service._generate_shipment_code. Immutable once set.
    shipment_code: Mapped[str] = mapped_column(String(40), nullable=False)
    origin_facility_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hospital_profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    destination_facility_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hospital_profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Optional link to the logistics route being used -- purely
    # informational (which TransportRoute's distance/transit-time/
    # operational-condition applies). A shipment can exist without one
    # (no pre-registered route between these two facilities yet) --
    # Section 16's "route condition is separate from shipment lifecycle"
    # means this is never read as an authority over `status` itself.
    transport_route_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("transport_routes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[ShipmentStatus] = mapped_column(
        Enum(ShipmentStatus, name="shipment_status"),
        nullable=False,
        server_default=ShipmentStatus.CREATED.value,
        index=True,
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # One nullable timestamp per real transition -- mirrors
    # BloodRequest's own _TIMESTAMP_FIELD pattern (fulfilled_at/
    # cancelled_at/expired_at), extended to every shipment transition.
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    in_transit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    arrived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Explicitly labeled an ESTIMATE (Section 17) -- never presented as
    # fact; set at dispatch (optionally updated when a delay is reported).
    # There is no live-GPS/fleet-tracking integration anywhere in this
    # product, so this is always a static estimate, not a real-time ETA.
    estimated_arrival_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # DELAYED is an annotation, not a state (see ShipmentStatus docstring)
    # -- reflects the MOST RECENT delay report; the full history of every
    # delay report lives in ShipmentEvent (SHIPMENT_DELAYED), never
    # overwritten.
    delayed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delay_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    idempotency_payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Audit C21 (2026-10-31): a shipment created by handing off an emergency
    # pathway's reservation carries its origin, so the request -> plan ->
    # units -> shipment -> outcome chain is traceable and a failed shipment
    # can re-plan its emergency (C20).
    emergency_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("emergencies.id", ondelete="SET NULL"), nullable=True, index=True
    )
    pathway_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("emergency_pathways.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Phase E -- failed-shipment reconciliation, a SEPARATE axis from
    # `status` (see ShipmentReconciliationStatus): NULL for every shipment
    # that is not FAILED; set to REQUIRED by the same transaction that
    # moves it to FAILED; changed exactly once, by an explicit, authorized
    # POST /shipments/{id}/reconcile. FAILED's own terminal transition
    # table is untouched.
    reconciliation_status: Mapped[ShipmentReconciliationStatus | None] = mapped_column(
        Enum(ShipmentReconciliationStatus, name="shipment_reconciliation_status"),
        nullable=True, index=True,
    )
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reconciled_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reconciliation_note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # Recorded for REDIRECTED only, and only as the reporter's statement --
    # this system cannot verify where diverted units physically went.
    redirected_to_facility_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("hospital_profiles.id", ondelete="SET NULL"), nullable=True
    )
    # ---- Gap elimination: cold chain ----
    # Acceptable temperature range for everything this shipment carries,
    # fixed at creation from the cited storage ranges of its components
    # (app.core.clinical_sources.TEMPERATURE_RANGE_C).
    temp_min_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    temp_max_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Last evaluation of the readings (also recomputed on every read).
    cold_chain_status: Mapped[ColdChainStatus | None] = mapped_column(
        Enum(ColdChainStatus, name="cold_chain_status"), nullable=True
    )
    cold_chain_evaluated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ShipmentItem(Base):
    """Which specific `BloodUnit` row this shipment carries. One row per
    unit -- a shipment of 6 units is 6 ShipmentItem rows, never a single
    row with a `quantity` column, so every unit stays individually
    traceable (Section 9) through its own row rather than an aggregate
    count that would have to be reconciled against real units separately."""

    __tablename__ = "shipment_items"
    __table_args__ = (
        # Invariant 23 (a BloodUnit belongs to at most one ACTIVE shipment)
        # enforced at the DB level, not just in application code -- a
        # partial unique index on blood_unit_id WHERE is_active, same
        # pattern as `EmergencyPathway.uq_emergency_pathway_one_active` /
        # `VerificationRequest.uq_verification_one_pending_per_kind`:
        # declared here too (not only as the equivalent `op.create_index`
        # in the Alembic migration) so the test suite's
        # `Base.metadata.create_all()` schema (tests/conftest.py) actually
        # creates this constraint as well, not just a real `alembic
        # upgrade`-built database -- otherwise this invariant would be
        # silently unenforced and untestable in every test run.
        Index(
            "uq_shipment_items_active_unit",
            "blood_unit_id",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    shipment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    blood_unit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("blood_units.id"), nullable=False, index=True
    )
    # False once the parent shipment reaches ANY terminal state
    # (RECEIVED/CANCELLED/FAILED) -- set by the same transaction that
    # transitions `Shipment.status`, never by a separate cleanup job. See
    # the partial unique index above for why this column exists at all.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    # ---- Gap elimination: per-unit reconciliation ----
    outcome: Mapped[ShipmentItemOutcome] = mapped_column(
        Enum(ShipmentItemOutcome, name="shipment_item_outcome"),
        nullable=False, server_default=ShipmentItemOutcome.PENDING.value,
    )
    outcome_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    outcome_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # The unit row this item became at the destination / origin (if any).
    resulting_unit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("blood_units.id", ondelete="SET NULL"), nullable=True
    )


class ShipmentEvent(Base):
    """Append-only operational timeline -- identical shape to
    `BloodRequestEvent`/`EmergencyEvent` (same columns, same convention),
    not a new event-log design. Powers GET /shipments/{id}/timeline."""

    __tablename__ = "shipment_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    shipment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True,
    )


class ShipmentTemperatureReading(Base):
    """One cold-chain temperature reading for a shipment. Readings are
    append-only; (shipment, sensor, recorded_at) is unique so a device that
    re-sends a batch cannot create duplicates."""

    __tablename__ = "shipment_temperature_readings"
    __table_args__ = (
        UniqueConstraint(
            "shipment_id", "sensor_id", "recorded_at", name="uq_temperature_reading"
        ),
        Index("ix_temperature_shipment_time", "shipment_id", "recorded_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    shipment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # NULL means the device reported a reading failure (see `error_code`).
    temperature_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    sensor_id: Mapped[str] = mapped_column(String(80), nullable=False)
    source: Mapped[TelemetrySource] = mapped_column(
        Enum(TelemetrySource, name="telemetry_source"), nullable=False
    )
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    submitted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
