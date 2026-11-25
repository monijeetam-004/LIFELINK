from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import DonationVerificationStatus


class DonationCreate(BaseModel):
    donated_on: date
    units: int = Field(default=1, ge=1, le=10)
    hospital_name: str | None = Field(default=None, max_length=200)
    # Phase E: optional structural link to a real HospitalProfile (from
    # the existing hospital directory), distinct from the free-text
    # `hospital_name` above, which stays mandatory-adjacent for display.
    # Omitting this keeps the exact pre-Phase-E behavior (an
    # off-platform/unstructured donation that only an admin can ever
    # verify -- see Donation.hospital_id's own docstring).
    hospital_id: uuid.UUID | None = None
    note: str | None = Field(default=None, max_length=1000)
    blood_request_id: uuid.UUID | None = None
    # Phase 26: optional client-generated idempotency key -- omitted keeps
    # the pre-Phase-26 create-every-time behavior (backward compatible);
    # when supplied, a replay of the same (donor, key) pair with the same
    # payload returns the original donation instead of creating a second
    # one. Same bounds as BloodRequestCreate.idempotency_key (see
    # app/schemas/blood_request.py) for consistency across the codebase.
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)


class DonationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    donor_user_id: uuid.UUID
    blood_request_id: uuid.UUID | None
    units: int
    donated_on: date
    hospital_name: str | None
    hospital_id: uuid.UUID | None = None
    note: str | None
    # Phase E: real backend truth, never inferred -- the frontend must
    # show exactly this, never a generic "logged" label dressed up as
    # confirmed (Section 26).
    verification_status: DonationVerificationStatus = DonationVerificationStatus.DONOR_REPORTED
    verified_by_user_id: uuid.UUID | None = None
    verified_at: datetime | None = None
    verification_note: str | None = None
    created_at: datetime


class DonationReviewIn(BaseModel):
    note: str | None = Field(default=None, max_length=1000)

