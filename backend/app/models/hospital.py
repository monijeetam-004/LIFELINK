from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.models.enums import (
    BloodComponent,
    BloodType,
    FacilityType,
    HospitalInventoryAction,
    HospitalResponseStatus,
)


class HospitalProfile(TimestampMixin, Base):
    __tablename__ = "hospital_profiles"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False, index=True
    )
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    license_no: Mapped[str | None] = mapped_column(String(80), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    address: Mapped[str | None] = mapped_column(String(300), nullable=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    facility_type: Mapped[FacilityType] = mapped_column(
        Enum(FacilityType, name="facility_type"),
        nullable=False,
        server_default=FacilityType.HOSPITAL.value,
    )


class HospitalInventory(TimestampMixin, Base):
    __tablename__ = "hospital_inventory"
    __table_args__ = (
        # Phase 35: component joins the key -- this row's (hospital_id,
        # blood_type) always implicitly meant "...and WHOLE_BLOOD" before
        # this column existed; now that's explicit, and a hospital can
        # have a separate row per (blood_type, component) combination.
        UniqueConstraint(
            "hospital_id", "blood_type", "component", name="uq_hospital_inventory_type_component"
        ),
        # Belt-and-suspenders against negative or over-reserved stock: the
        # service layer (app/services/inventory_service.py) already checks
        # every mutation before writing, but a DB-level CHECK means "never
        # allow negative inventory" holds even against a bug or a future
        # direct-SQL mistake, not just app-level discipline.
        CheckConstraint("units >= 0", name="ck_hospital_inventory_units_non_negative"),
        CheckConstraint(
            "reserved_units >= 0", name="ck_hospital_inventory_reserved_non_negative"
        ),
        CheckConstraint(
            "reserved_units <= units", name="ck_hospital_inventory_reserved_le_units"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hospital_profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    blood_type: Mapped[BloodType] = mapped_column(
        Enum(BloodType, name="blood_type"), nullable=False
    )
    # Phase 35: defaults to WHOLE_BLOOD -- the only thing this row ever
    # represented before this column existed. The existing GET/PUT
    # /hospitals/me/inventory contract only ever reads/writes the
    # WHOLE_BLOOD row for each blood type (see inventory_service.py), so
    # every pre-existing client is unaffected.
    component: Mapped[BloodComponent] = mapped_column(
        Enum(BloodComponent, name="blood_component"),
        nullable=False,
        server_default=BloodComponent.WHOLE_BLOOD.value,
    )
    units: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # Units already on hand (counted in `units`) but earmarked for a
    # specific request via the RESERVE action -- not yet physically
    # dispatched. `units - reserved_units` is what's actually free to
    # promise to a new request (see inventory_service.available_units).
    reserved_units: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")


class HospitalInventoryLog(TimestampMixin, Base):
    """Append-only audit trail for every inventory mutation.

    One row per action (ADD/RESERVE/ALLOCATE/RELEASE/EXPIRE/RECONCILE),
    recording the signed delta applied and the resulting totals, so a
    hospital's "recent activity" view and any future reconciliation dispute
    can be answered from real, append-only history (never edited by the app;
    not DB-enforced immutable) rather than only the
    current snapshot in HospitalInventory.
    """

    __tablename__ = "hospital_inventory_logs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hospital_profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    blood_type: Mapped[BloodType] = mapped_column(
        Enum(BloodType, name="blood_type"), nullable=False
    )
    # Phase 35: which (blood_type, component) row this entry logs against --
    # see HospitalInventory.component.
    component: Mapped[BloodComponent] = mapped_column(
        Enum(BloodComponent, name="blood_component"),
        nullable=False,
        server_default=BloodComponent.WHOLE_BLOOD.value,
    )
    action: Mapped[HospitalInventoryAction] = mapped_column(
        Enum(HospitalInventoryAction, name="hospital_inventory_action"), nullable=False
    )
    delta_units: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    delta_reserved: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    resulting_units: Mapped[int] = mapped_column(Integer, nullable=False)
    resulting_reserved: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    request_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("blood_requests.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Phase 12: a parallel trace column for emergency-triggered reservations.
    # `request_id` above is FK'd specifically to `blood_requests.id` and
    # cannot carry an Emergency's id -- see
    # app.services.inventory_service's `emergency_id` kwarg on
    # reserve_stock/release_reservation/allocate_stock, which is what
    # populates this. A given log row is written from exactly one call
    # site, so at most one of request_id/emergency_id is ever set.
    emergency_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("emergencies.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Phase D: a third parallel trace column, same pattern as emergency_id
    # above -- populated only by a RESERVE/RELEASE/ALLOCATE call made on a
    # shipment's behalf (app.services.shipment_service). At most one of
    # request_id/emergency_id/shipment_id is ever set on a given row.
    shipment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("shipments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)


class HospitalRequestResponse(TimestampMixin, Base):
    """A hospital's handling status for an incoming blood request."""

    __tablename__ = "hospital_request_responses"
    __table_args__ = (
        UniqueConstraint("hospital_id", "request_id", name="uq_hospital_request_response"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    hospital_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hospital_profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("blood_requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[HospitalResponseStatus] = mapped_column(
        Enum(HospitalResponseStatus, name="hospital_response_status"), nullable=False
    )
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Phase E: this hospital's own cumulative contribution toward the
    # request's `BloodRequest.fulfilled_units` -- a per-hospital
    # breakdown for traceability ("who gave how much"), never the
    # authoritative total itself. Incremented once per successful
    # `allocate_to_request` call from THIS hospital, under the request's
    # own row lock, in the same transaction as `BloodRequest.fulfilled_units`
    # -- the two must always agree (summed across every hospital's row for
    # a given request) and this is tested, not just asserted.
    units_allocated: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
