from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Certificate(TimestampMixin, Base):
    """A generated PDF certificate for a single donation."""

    __tablename__ = "certificates"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    donation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("donations.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    # Denormalised owner: fast authorization and survives donation edits.
    donor_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    serial: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    # Non-guessable public identifier used by the verification endpoint.
    verify_code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    storage_key: Mapped[str] = mapped_column(String(400), nullable=False)

    # ---- Phase 31 additions ----
    # Domain-correctness fix (LIFELINK_PHASE_31_DOMAIN_AUDIT.md, "Certificate
    # trust model"): before this, once issued, a certificate could never be
    # invalidated -- an admin who discovered a certificate was issued for a
    # donation later found fraudulent or erroneous had no way to say so, and
    # the public /certificates/verify/{code} endpoint had no way to
    # distinguish "never existed" from "existed, no longer trustworthy".
    # Nullable/additive; a certificate with `revoked_at is None` behaves
    # exactly as every certificate did before this phase.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    revocation_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
