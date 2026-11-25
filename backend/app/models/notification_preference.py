from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.models.enums import NotificationDigestFrequency


class NotificationPreference(TimestampMixin, Base):
    """Per-user channel preferences for the notification system.

    In-app notifications (Notification model) are always created regardless of
    these settings -- they are the one channel LifeLink guarantees. These
    preferences control the email digest cadence and are read by the email
    worker (app/workers/tasks.py) before it sends anything non-critical.
    Emergency alerts are never suppressed by digest settings.
    """

    __tablename__ = "notification_preferences"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False, index=True
    )
    email_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    sms_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    emergency_alerts_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    digest_frequency: Mapped[NotificationDigestFrequency] = mapped_column(
        Enum(NotificationDigestFrequency, name="notification_digest_frequency"),
        nullable=False,
        server_default=NotificationDigestFrequency.REALTIME.value,
    )
    quiet_hours_start: Mapped[str | None] = mapped_column(String(5), nullable=True)  # "HH:MM"
    quiet_hours_end: Mapped[str | None] = mapped_column(String(5), nullable=True)  # "HH:MM"
