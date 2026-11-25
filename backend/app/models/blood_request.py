from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.models.enums import BloodRequestStatus, BloodType, DonorResponseStatus, Urgency


class BloodRequest(TimestampMixin, Base):
    __tablename__ = "blood_requests"
    __table_args__ = (
        # Phase 19: mirrors the Phase 12 emergency-idempotency design
        # (uq_emergency_idempotency_key) -- scoped per requester, not
        # global, and a partial-effective constraint in practice since
        # Postgres treats every NULL idempotency_key as distinct, so the
        # many pre-existing rows with no key at all are unaffected. See
        # app.api.routes.blood_requests.create_request and
        # LIFELINK_PHASE_19_IDEMPOTENCY_AUDIT.md.
        UniqueConstraint(
            "requester_user_id", "idempotency_key", name="uq_blood_request_idempotency_key"
        ),
        # Phase E: DB-level defense-in-depth for the fulfilment invariant
        # (0 <= fulfilled_units <= units_needed), the same philosophy as
        # HospitalInventory's own non-negative/bounded CHECK constraints --
        # application logic (app.core.request_fulfillment) never attempts
        # to violate this, but a future bug or a direct-SQL mistake is
        # still caught by Postgres itself, not just trusted code.
        CheckConstraint("fulfilled_units >= 0", name="ck_blood_request_fulfilled_non_negative"),
        CheckConstraint(
            "fulfilled_units <= units_needed", name="ck_blood_request_fulfilled_not_over"
        ),
        # Hardening (measured, docs/LIFELINK_HARDENING_REPORT.md): the admin
        # request list is "newest N" and analytics counts the last N days;
        # both were a full scan + sort that grows with request history.
        Index("ix_blood_requests_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # Who filed the request (a PATIENT or a FAMILY member acting for them).
    requester_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The patient the blood is for.
    patient_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    blood_type: Mapped[BloodType] = mapped_column(
        Enum(BloodType, name="blood_type"), nullable=False, index=True
    )
    units_needed: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    # Phase E: the single source of truth for how much of this request has
    # actually been met -- see app.core.request_fulfillment. Updated only
    # under this row's own `SELECT ... FOR UPDATE` lock, in the same
    # transaction as any `status` recomputation it implies. Never derived
    # by summing something else at read time (HospitalRequestResponse.
    # units_allocated is a per-hospital breakdown that must always sum to
    # this value -- tested, not just asserted -- never the other way
    # around). CHECK constraints (migration) enforce
    # 0 <= fulfilled_units <= units_needed at the database level, the same
    # defense-in-depth philosophy as every other non-negative/bounded
    # quantity column in this codebase.
    fulfilled_units: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    urgency: Mapped[Urgency] = mapped_column(
        Enum(Urgency, name="urgency"), nullable=False, default=Urgency.ROUTINE
    )
    status: Mapped[BloodRequestStatus] = mapped_column(
        Enum(BloodRequestStatus, name="blood_request_status"),
        nullable=False,
        default=BloodRequestStatus.PENDING,
        index=True,
    )
    hospital_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    needed_by: Mapped[date | None] = mapped_column(Date, nullable=True)
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Phase 10: formalizing the lifecycle -- set exactly once, the moment
    # the request transitions into CANCELLED / EXPIRED, mirroring the
    # pre-existing `fulfilled_at` pattern rather than inventing a new one.
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Phase 19: client-generated idempotency key (opaque, bounded, scoped
    # to requester_user_id -- see __table_args__ above) plus a SHA-256 hex
    # digest of the semantically meaningful request fields, used only to
    # detect a same-key/different-payload replay (see create_request).
    # Never the raw payload -- nothing sensitive is duplicated here beyond
    # what this row's own columns already store.
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    idempotency_payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class BloodRequestEvent(Base):
    """Append-only tracking timeline for a blood request."""

    __tablename__ = "blood_request_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("blood_requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )


class DonorResponse(TimestampMixin, Base):
    """A donor's accept/decline against a specific blood request."""

    __tablename__ = "donor_responses"
    __table_args__ = (
        UniqueConstraint("request_id", "donor_user_id", name="uq_donor_response_pair"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("blood_requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    donor_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[DonorResponseStatus] = mapped_column(
        Enum(DonorResponseStatus, name="donor_response_status"), nullable=False
    )
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
