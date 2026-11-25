from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AppointmentStatus


class AppointmentCreate(BaseModel):
    """``hospital_id``/``hospital_name``: exactly one must be meaningfully set.

    Enforced in the route handler (not a pydantic validator) so a missing
    facility comes back as a clean 400 rather than a 422 validation error.
    """

    hospital_id: uuid.UUID | None = None
    hospital_name: str | None = Field(default=None, max_length=200)
    preferred_date: date
    note: str | None = Field(default=None, max_length=500)


class AppointmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    donor_user_id: uuid.UUID
    hospital_id: uuid.UUID | None
    hospital_name: str | None
    preferred_date: date
    status: AppointmentStatus
    note: str | None
    created_at: datetime
    updated_at: datetime
