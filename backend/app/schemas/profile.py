from __future__ import annotations

import uuid
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from app.core.eligibility import eligible_on as _eligible_on
from app.core.eligibility import is_eligible as _is_eligible
from app.core.phone import validate_phone_field
from app.models.enums import BloodType, DonorAvailabilityState, Urgency


class PatientProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    blood_type: BloodType | None
    date_of_birth: date | None
    phone: str | None
    address: str | None
    city: str | None
    medical_notes: str | None
    emergency_contact_name: str | None
    emergency_contact_phone: str | None


class PatientProfileUpdate(BaseModel):
    blood_type: BloodType | None = None
    date_of_birth: date | None = None
    phone: str | None = Field(default=None, max_length=40)
    address: str | None = Field(default=None, max_length=300)
    city: str | None = Field(default=None, max_length=120)
    medical_notes: str | None = Field(default=None, max_length=2000)
    emergency_contact_name: str | None = Field(default=None, max_length=200)
    emergency_contact_phone: str | None = Field(default=None, max_length=40)

    # Normalized to E.164 (e.g. "+919876543210") on write, or rejected as a
    # 422 if it doesn't parse as a real number -- see app/core/phone.py.
    # Applied to both phone fields: SMS delivery (and the eligibility to
    # even attempt it) depends on a real, deliverable number being on file.
    _normalize_phone = field_validator("phone")(validate_phone_field)
    _normalize_emergency_contact_phone = field_validator("emergency_contact_phone")(
        validate_phone_field
    )


class DonorProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    blood_type: BloodType | None
    phone: str | None
    city: str | None
    last_donation_date: date | None
    is_available: bool
    availability_state: DonorAvailabilityState
    availability_note: str | None
    weight_kg: int | None
    medical_conditions: str | None
    latitude: float | None
    longitude: float | None
    share_location: bool
    identity_verified: bool
    alert_min_urgency: Urgency
    alert_radius_km: int | None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def eligible(self) -> bool:
        """Whether the donation interval has elapsed since the last donation."""
        return _is_eligible(self.last_donation_date)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def eligible_on(self) -> date | None:
        """First date the donor may donate again (``None`` if never donated)."""
        return _eligible_on(self.last_donation_date)


class DonorProfileUpdate(BaseModel):
    blood_type: BloodType | None = None
    phone: str | None = Field(default=None, max_length=40)
    city: str | None = Field(default=None, max_length=120)
    last_donation_date: date | None = None
    availability_note: str | None = Field(default=None, max_length=300)
    weight_kg: int | None = Field(default=None, ge=0, le=500)
    medical_conditions: str | None = Field(default=None, max_length=2000)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    share_location: bool | None = None
    # Alert Watcher preferences.
    alert_min_urgency: Urgency | None = None
    alert_radius_km: int | None = Field(default=None, ge=1, le=500)

    _normalize_phone = field_validator("phone")(validate_phone_field)


class AvailabilityUpdate(BaseModel):
    state: DonorAvailabilityState
    availability_note: str | None = Field(default=None, max_length=300)
