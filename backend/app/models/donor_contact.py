"""Confidence-shortlist contact attempts (audit follow-up, 2026-10-31).

One row per (request or emergency, donor) that LifeLink actually contacted
from a shortlist. Candidates that were ranked but never contacted are not
stored -- they are recomputed on every read from live data.
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
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.models.enums import DonorContactStatus, ShortlistSubject


class DonorContactAttempt(TimestampMixin, Base):
    __tablename__ = "donor_contact_attempts"
    __table_args__ = (
        UniqueConstraint("blood_request_id", "donor_user_id", name="uq_contact_request_donor"),
        UniqueConstraint("emergency_id", "donor_user_id", name="uq_contact_emergency_donor"),
        CheckConstraint(
            "(blood_request_id IS NOT NULL AND emergency_id IS NULL AND subject = 'BLOOD_REQUEST')"
            " OR (emergency_id IS NOT NULL AND blood_request_id IS NULL AND subject = 'EMERGENCY')",
            name="ck_contact_exactly_one_subject",
        ),
        Index("ix_contact_status_timeout", "status", "timeout_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    subject: Mapped[ShortlistSubject] = mapped_column(
        Enum(ShortlistSubject, name="shortlist_subject"), nullable=False
    )
    blood_request_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("blood_requests.id", ondelete="CASCADE"), nullable=True, index=True
    )
    emergency_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("emergencies.id", ondelete="CASCADE"), nullable=True, index=True
    )
    donor_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[DonorContactStatus] = mapped_column(
        Enum(DonorContactStatus, name="donor_contact_status"), nullable=False
    )
    batch: Mapped[int] = mapped_column(Integer, nullable=False)
    rank_at_contact: Mapped[int] = mapped_column(Integer, nullable=False)
    score_at_contact: Mapped[float] = mapped_column(Float, nullable=False)
    notification_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("notifications.id", ondelete="SET NULL"), nullable=True
    )
    contacted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    contacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timeout_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    eligibility_rechecked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    status_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # True only for responses recorded by the DEMO_MODE simulator for a
    # synthetic donor. Never set for a real donor's own answer.
    simulated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    # Unified Donor Priority (2026-10-31): how this donor was ranked when
    # contacted -- strategy, model tag (id/version#config fingerprint) and a
    # minimal factor-value snapshot (no names, dates or coordinates). NULL on
    # rows contacted before the model existed (CONFIDENCE_SHORTLIST score).
    ranking_strategy: Mapped[str | None] = mapped_column(String(32), nullable=True)
    scoring_model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    score_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
