from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.enums import FamilyLinkStatus


class FamilyLinkCreate(BaseModel):
    patient_email: EmailStr
    relationship: str = Field(min_length=1, max_length=60)


class FamilyLinkActionIn(BaseModel):
    """Body for accept/reject/revoke -- every field optional, so an empty
    `{}` (the pre-Phase-13 request shape every existing caller already
    sends) behaves exactly as before."""

    reason: str | None = Field(default=None, max_length=500)


class PersonOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    full_name: str
    email: EmailStr


class FamilyLinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    family_user_id: uuid.UUID
    patient_user_id: uuid.UUID
    relationship: str
    status: FamilyLinkStatus
    requested_by_user_id: uuid.UUID | None
    created_at: datetime
    expires_at: datetime | None = None
    # ---- Phase 13 additions ----
    reason: str | None = None
    responded_at: datetime | None = None
    # Every state this link could legally move to right now, from
    # app.core.family_lifecycle -- the same idea as Phase 10/12's
    # allowed_transitions on BloodRequest/Emergency. Note this is state-
    # machine truth only: whether *this specific caller* may trigger one of
    # these is a separate, per-endpoint check (only the patient may accept/
    # reject; either party may revoke).
    allowed_transitions: list[FamilyLinkStatus] = []
    family: PersonOut | None = None
    patient: PersonOut | None = None
