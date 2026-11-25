from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.models.enums import DonationVerificationStatus


class Donation(TimestampMixin, Base):
    __tablename__ = "donations"
    __table_args__ = (
        # Phase 26: mirrors the Phase 19 blood_request idempotency design
        # (uq_blood_request_idempotency_key) exactly -- scoped per donor, not
        # global (two different donors may coincidentally pick the same
        # client-generated key without colliding), and a partial-effective
        # constraint in practice since Postgres treats every NULL
        # idempotency_key as distinct, so the many pre-existing rows with no
        # key at all are unaffected. See app.api.routes.donors.log_donation
        # and LIFELINK_PHASE_26_DONATION_FRONTEND_AUDIT.md.
        UniqueConstraint(
            "donor_user_id", "idempotency_key", name="uq_donation_idempotency_key"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    donor_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    blood_request_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("blood_requests.id", ondelete="SET NULL"), nullable=True, index=True
    )
    units: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    donated_on: Mapped[date] = mapped_column(Date, nullable=False)
    hospital_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # Phase 26: client-generated idempotency key (opaque, bounded, scoped to
    # donor_user_id -- see __table_args__ above) plus a SHA-256 hex digest of
    # the semantically meaningful donation fields, used only to detect a
    # same-key/different-payload replay (see log_donation). Never the raw
    # payload -- nothing sensitive is duplicated here beyond what this row's
    # own columns already store. Optional/nullable: omitting it keeps the
    # pre-Phase-26 create-every-time behavior, fully backward compatible.
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    idempotency_payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Phase E -- see app.models.enums.DonationVerificationStatus for the
    # full policy. DONOR_REPORTED is the server_default so every
    # pre-Phase-E row (and every new row logged the pre-existing way)
    # keeps its exact pre-existing meaning: an unconfirmed self-report,
    # nothing more, nothing less.
    verification_status: Mapped[DonationVerificationStatus] = mapped_column(
        Enum(DonationVerificationStatus, name="donation_verification_status"),
        nullable=False,
        server_default=DonationVerificationStatus.DONOR_REPORTED.value,
        index=True,
    )
    # Nullable, structural link to a real HospitalProfile -- distinct from
    # the pre-existing free-text `hospital_name` (which stays exactly as
    # it was, for display/backward compatibility). Only a hospital tied to
    # THIS column (or an admin) may verify/reject this donation -- see
    # app.api.routes.donors.verify_donation. A donation logged with no
    # `hospital_id` (every historical row, and any new one where the donor
    # didn't pick a real facility from the directory) can only ever be
    # reviewed by an admin -- this system has no way to honestly attribute
    # review authority over unstructured free text to one specific
    # facility, so it does not guess.
    hospital_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("hospital_profiles.id", ondelete="SET NULL"), nullable=True, index=True
    )
    verified_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verification_note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
