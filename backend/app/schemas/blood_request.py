from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.enums import BloodRequestStatus, BloodType, DonorResponseStatus, Urgency


class BloodRequestCreate(BaseModel):
    blood_type: BloodType
    units_needed: int = Field(default=1, ge=1, le=20)
    urgency: Urgency = Urgency.ROUTINE
    hospital_name: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=1000)
    needed_by: date | None = None
    # FAMILY only: file on behalf of a linked patient. Omit to file for self.
    patient_email: EmailStr | None = None
    # Phase 19: optional client-generated idempotency key -- omitted keeps
    # today's create-every-time behavior (backward compatible); when
    # supplied, a replay of the same (requester, key) pair with the same
    # payload returns the original request instead of creating a second
    # one. Same bounds as Emergency.idempotency_key (see
    # app/schemas/emergency.py) for consistency across the codebase.
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)


class DonorResponseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    donor_user_id: uuid.UUID
    status: DonorResponseStatus
    note: str | None
    created_at: datetime
    donor_name: str | None = None
    donor_blood_type: BloodType | None = None


class BloodRequestEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    event_type: str
    note: str | None
    actor_user_id: uuid.UUID | None
    created_at: datetime


class BloodRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    requester_user_id: uuid.UUID
    patient_user_id: uuid.UUID
    patient_name: str | None = None
    requester_name: str | None = None
    # True when a FAMILY member filed this on the patient's behalf (requester
    # and patient are different accounts) -- lets the UI show
    # "Requested by family member" instead of guessing from the two ids.
    filed_by_family: bool = False
    blood_type: BloodType
    units_needed: int
    # Phase E: the single source of truth for how much of this request has
    # actually been met (app.core.request_fulfillment) -- always consistent
    # with `status` (PARTIALLY_FULFILLED iff 0 < fulfilled_units < units_needed,
    # FULFILLED iff fulfilled_units == units_needed). The frontend computes
    # "remaining" as units_needed - fulfilled_units rather than trusting a
    # separately-sent number, per Section 26 -- never display "Fulfilled"
    # for a request that is only partially met.
    fulfilled_units: int = 0
    urgency: Urgency
    status: BloodRequestStatus
    hospital_name: str | None
    city: str | None
    note: str | None
    needed_by: date | None
    fulfilled_at: datetime | None
    # Phase 10: set exactly once, the moment the request transitions into
    # CANCELLED / EXPIRED -- mirrors fulfilled_at above.
    cancelled_at: datetime | None = None
    expired_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    accepted_count: int = 0
    # Phase 10: the state machine's own answer to "what can happen next",
    # from app.core.request_lifecycle.allowed_transitions -- lets clients
    # render valid actions without hardcoding the transition table
    # themselves. Empty for a terminal request.
    allowed_transitions: list[BloodRequestStatus] = []


class BloodRequestDetailOut(BloodRequestOut):
    events: list[BloodRequestEventOut] = []
    responses: list[DonorResponseOut] = []
    my_response: DonorResponseOut | None = None


class DonorOpenRequestOut(BloodRequestOut):
    """An open request as seen by a donor, with their own response (if any)."""

    my_response: DonorResponseOut | None = None


class DonorMatchOut(BloodRequestOut):
    """A request the donor has responded to — their match/commitment history.

    Present for every status (open and closed) so the donor keeps a record of
    what they committed to and how it ended.
    """

    # Always populated by the route; optional only so ``model_validate`` on the
    # bare request row succeeds before the field is filled in.
    my_response: DonorResponseOut | None = None
    distance_km: float | None = None


class RespondIn(BaseModel):
    action: DonorResponseStatus
    note: str | None = Field(default=None, max_length=500)


class CancelIn(BaseModel):
    reason: str | None = Field(default=None, max_length=500)
