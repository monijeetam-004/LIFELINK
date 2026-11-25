from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Enum, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.models.enums import BloodType, DonorAvailabilityState, Urgency


class PatientProfile(TimestampMixin, Base):
    __tablename__ = "patient_profiles"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False, index=True
    )
    blood_type: Mapped[BloodType | None] = mapped_column(
        Enum(BloodType, name="blood_type"), nullable=True
    )
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    address: Mapped[str | None] = mapped_column(String(300), nullable=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    medical_notes: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    emergency_contact_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    emergency_contact_phone: Mapped[str | None] = mapped_column(String(40), nullable=True)


class DonorProfile(TimestampMixin, Base):
    __tablename__ = "donor_profiles"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False, index=True
    )
    blood_type: Mapped[BloodType | None] = mapped_column(
        Enum(BloodType, name="blood_type"), nullable=True, index=True
    )
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    last_donation_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # `is_available` stays the single boolean every matching/notification query
    # already filters on (app/services/notification_service.py, matching.py,
    # emergencies.py, ...); `availability_state` is the donor-facing three-way
    # selector layered on top of it -- AVAILABLE keeps is_available True,
    # UNAVAILABLE and NOT_NOW both set it False (see update_availability()).
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    availability_state: Mapped[DonorAvailabilityState] = mapped_column(
        Enum(DonorAvailabilityState, name="donor_availability_state"),
        nullable=False,
        server_default=DonorAvailabilityState.AVAILABLE.value,
    )
    availability_note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    weight_kg: Mapped[int | None] = mapped_column(nullable=True)
    medical_conditions: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    # Optional coarse location, only used when the donor opts in.
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    share_location: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    # Set only by an admin approving a VerificationRequest(kind=DONOR_IDENTITY).
    identity_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    # --- Alert Watcher preferences (Phase 4) ---------------------------------
    # Real, donor-configurable filters on top of the automatic blood-type
    # compatibility check every matching path already does. There's no
    # per-blood-group/"component" selector here: a donor has exactly one blood
    # type, the system doesn't model donation components (whole blood only),
    # and compatibility is already computed automatically -- a selector for
    # either would just be decoration with nothing real behind it.
    alert_min_urgency: Mapped[Urgency] = mapped_column(
        Enum(Urgency, name="urgency"), nullable=False, server_default=Urgency.ROUTINE.value
    )
    alert_radius_km: Mapped[int | None] = mapped_column(nullable=True)
    # Audit 2026-10-31: when the donor last set/confirmed their shared
    # location (NULL = unknown age), and when they last explicitly confirmed
    # their availability. Used by the distance policy (stale locations) and
    # the confidence shortlist (meaningful activity). A login is NOT activity.
    location_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    availability_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
