from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import NotificationDigestFrequency

_QUIET_HOURS_PATTERN = r"^\d{2}:\d{2}$"


class NotificationPreferenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    email_enabled: bool
    sms_enabled: bool
    emergency_alerts_enabled: bool
    digest_frequency: NotificationDigestFrequency
    quiet_hours_start: str | None
    quiet_hours_end: str | None


class NotificationPreferenceUpdate(BaseModel):
    email_enabled: bool | None = None
    sms_enabled: bool | None = None
    emergency_alerts_enabled: bool | None = None
    digest_frequency: NotificationDigestFrequency | None = None
    quiet_hours_start: str | None = Field(default=None, pattern=_QUIET_HOURS_PATTERN)
    quiet_hours_end: str | None = Field(default=None, pattern=_QUIET_HOURS_PATTERN)
