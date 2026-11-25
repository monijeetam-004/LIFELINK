from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.models.enums import SmsFailureCategory, SmsStatus, SmsTemplateKey


class SmsNotification(TimestampMixin, Base):
    """Auditable record of one SMS delivery attempt (and its retries).

    Exactly one row per in-app Notification that was also eligible for SMS
    (the ``uq_sms_notification_per_notification`` constraint enforces this),
    which is what makes retries idempotent: a retry looks up this row by
    ``notification_id`` and updates it in place rather than ever inserting a
    second attempt for the same event -- see app/services/sms_service.py.

    Never stores: provider credentials, or the full phone number (only
    ``recipient_phone_masked``, via app/core/phone.mask_phone). SMS body
    content itself also isn't persisted here -- only the template key and
    the structured variables used to fill it -- since a template's rendered
    text can carry the same patient/medical detail the rest of the app is
    careful not to over-expose.
    """

    __tablename__ = "sms_notifications"
    __table_args__ = (
        UniqueConstraint("notification_id", name="uq_sms_notification_per_notification"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    notification_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("notifications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    recipient_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    template_key: Mapped[SmsTemplateKey] = mapped_column(
        Enum(SmsTemplateKey, name="sms_template_key"), nullable=False
    )
    recipient_phone_masked: Mapped[str | None] = mapped_column(String(20), nullable=True)
    channel: Mapped[str] = mapped_column(String(10), nullable=False, default="SMS")
    provider: Mapped[str] = mapped_column(String(20), nullable=False, default="MSG91")
    status: Mapped[SmsStatus] = mapped_column(
        Enum(SmsStatus, name="sms_status"), nullable=False, default=SmsStatus.PENDING
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    provider_message_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    failure_category: Mapped[SmsFailureCategory | None] = mapped_column(
        Enum(SmsFailureCategory, name="sms_failure_category"), nullable=True
    )
    failure_detail: Mapped[str | None] = mapped_column(String(300), nullable=True)
    related_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    related_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
