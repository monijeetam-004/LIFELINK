from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.models.enums import FamilyLinkStatus


class FamilyLink(TimestampMixin, Base):
    """A FAMILY user's authorisation to act on behalf of a PATIENT user."""

    __tablename__ = "family_links"
    __table_args__ = (
        UniqueConstraint("family_user_id", "patient_user_id", name="uq_family_link_pair"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    family_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    patient_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    relationship: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[FamilyLinkStatus] = mapped_column(
        Enum(FamilyLinkStatus, name="family_link_status"),
        nullable=False,
        default=FamilyLinkStatus.PENDING,
    )
    requested_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Set whenever the link is (re-)opened as PENDING; cleared once it leaves
    # PENDING. A stale, un-actioned request is lazily flipped to EXPIRED the
    # next time it's read or acted on (see app/api/routes/family.py).
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # ---- Phase 13 additions ----
    # Optional free-text reason attached to whichever transition most
    # recently set it (accept/reject/revoke) -- see
    # app.services.family_lifecycle.transition. Never required; overwritten
    # (not appended) on each transition, since the full history of who said
    # what and when already lives in the append-only, tamper-evident AuditLog trail via
    # audit_service.record, not on this row.
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Set when a PENDING request is actioned (accepted or rejected) --
    # mirrors Emergency.resolved_at's "when did this stop being open" idea.
    # Left untouched by revoke/expiry (their own moment is `updated_at`).
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
