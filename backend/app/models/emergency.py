from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.models.enums import (
    BloodComponent,
    BloodType,
    DonorResponseStatus,
    EmergencyOverrideKind,
    EmergencyStatus,
    GeocodeSource,
    Urgency,
)
from app.models.user import UserRole


class Emergency(TimestampMixin, Base):
    __tablename__ = "emergencies"
    __table_args__ = (
        # Phase 12: partial unique index -- only enforced when a client
        # actually supplied an idempotency key (most rows won't have one,
        # e.g. every emergency created before this phase). Scoped per
        # creator, not globally, so two different users can't collide on
        # the same client-generated key by coincidence, and re-submitting
        # the exact same (actor, key) pair is what makes creation
        # idempotent -- see app.api.routes.emergencies.create_emergency.
        UniqueConstraint(
            "created_by_user_id", "idempotency_key", name="uq_emergency_idempotency_key"
        ),
        # Gap elimination: structured location. Both coordinates or neither,
        # each within its valid range -- enforced by the database too.
        CheckConstraint(
            "(latitude IS NULL) = (longitude IS NULL)", name="ck_emergency_coords_pair"
        ),
        CheckConstraint(
            "latitude IS NULL OR (latitude >= -90 AND latitude <= 90)",
            name="ck_emergency_latitude_range",
        ),
        CheckConstraint(
            "longitude IS NULL OR (longitude >= -180 AND longitude <= 180)",
            name="ck_emergency_longitude_range",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    patient_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    blood_type: Mapped[BloodType | None] = mapped_column(
        Enum(BloodType, name="blood_type"), nullable=True
    )
    urgency: Mapped[Urgency] = mapped_column(
        Enum(Urgency, name="urgency"), nullable=False, default=Urgency.CRITICAL
    )
    status: Mapped[EmergencyStatus] = mapped_column(
        Enum(EmergencyStatus, name="emergency_status"),
        nullable=False,
        default=EmergencyStatus.ACTIVE,
        index=True,
    )
    location_text: Mapped[str | None] = mapped_column(String(300), nullable=True)
    note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ---- Phase 12 additions (all additive/nullable -- see
    # LIFELINK_PHASE_12_REPORT.md for why each one exists) ----
    units_needed: Mapped[int | None] = mapped_column(Integer, nullable=True, default=1)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Optional: the patient/family may name a specific facility at creation
    # time. Never required -- the emergency is still broadcast to every
    # compatible donor/hospital exactly as before when this is left unset;
    # a hospital only ever becomes formally involved by responding
    # (EmergencyResponse), same as pre-Phase-12.
    target_facility_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("hospital_profiles.id", ondelete="SET NULL"), nullable=True
    )

    # ---- Phase 31 addition ----
    # Set exactly once, the first time any read or mutating request notices
    # that `deadline` has passed while the emergency is still open (see
    # app.services.emergency_lifecycle.flag_deadline_exceeded_if_needed).
    # Deliberately NEVER changes `status` itself -- Section 9 of the Phase 31
    # spec is explicit that a deadline must have a *meaningful* consequence
    # without the system falsely representing itself as making an autonomous
    # clinical/operational decision. This column is that consequence: a
    # durable, queryable, one-time signal (event + audit log + notification)
    # that a human (the emergency's creator, a hospital, an admin) can act on.
    deadline_exceeded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # ---- Gap elimination ----
    # Which blood component is needed. Never interchangeable with another
    # component (app.core.compatibility). Server default WHOLE_BLOOD keeps
    # every pre-existing emergency's meaning (whole blood was the only
    # product before components existed).
    component: Mapped[BloodComponent] = mapped_column(
        Enum(BloodComponent, name="blood_component"),
        nullable=False,
        server_default=BloodComponent.WHOLE_BLOOD.value,
    )
    # Structured location (location_text stays as display text only).
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    geocode_source: Mapped[GeocodeSource | None] = mapped_column(
        Enum(GeocodeSource, name="geocode_source"), nullable=True
    )
    geocode_detail: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # Structured city (the free-text `location_text` stays the address line).
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Deadline monitor idempotency markers (app/workers/deadline_monitor.py):
    # each is set exactly once, by whichever worker wins the row lock.
    deadline_warning_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deadline_escalated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class EmergencyResponse(TimestampMixin, Base):
    """A donor's or hospital's accept/decline against an active emergency."""

    __tablename__ = "emergency_responses"
    __table_args__ = (
        UniqueConstraint("emergency_id", "responder_user_id", name="uq_emergency_response_pair"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    emergency_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("emergencies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    responder_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    responder_role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role"), nullable=False
    )
    status: Mapped[DonorResponseStatus] = mapped_column(
        Enum(DonorResponseStatus, name="donor_response_status"), nullable=False
    )
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)


class EmergencyEvent(Base):
    __tablename__ = "emergency_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    emergency_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("emergencies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )


class EmergencyOverride(Base):
    """An audited, ADMIN-only exception to an emergency's normal policy
    (e.g. rank donors beyond MAX_DONOR_DISTANCE_KM). Visible on the
    emergency, never silent; revoking sets `revoked_at` (rows are kept)."""

    __tablename__ = "emergency_overrides"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    emergency_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("emergencies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[EmergencyOverrideKind] = mapped_column(
        Enum(EmergencyOverrideKind, name="emergency_override_kind"), nullable=False
    )
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
