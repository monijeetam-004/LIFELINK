from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.phone import validate_phone_field
from app.models.enums import (
    BloodComponent,
    BloodType,
    HospitalInventoryAction,
    HospitalResponseStatus,
)
from app.schemas.blood_request import BloodRequestOut


class HospitalProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    # Phase 12: the hospital_profiles.id primary key -- distinct from
    # user_id -- is what Emergency.target_facility_id, EmergencyPathway's
    # origin/selected facility columns, and TransportRoute all actually
    # reference. Previously this response only exposed user_id, leaving a
    # hospital's own frontend with no way to learn its own facility id
    # (short of an unreliable name match against /hospitals/directory) in
    # order to call the pathway-selection or target-facility features.
    # Purely additive: every existing consumer of this response is
    # unaffected by one more field appearing on it.
    id: uuid.UUID
    user_id: uuid.UUID
    name: str | None
    license_no: str | None
    phone: str | None
    address: str | None
    city: str | None
    latitude: float | None
    longitude: float | None
    verified: bool


class HospitalDirectoryItem(BaseModel):
    """Minimal facility-picker entry -- name/city/verified only, no contact details."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str | None
    city: str | None
    verified: bool


class HospitalProfileUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    license_no: str | None = Field(default=None, max_length=80)
    phone: str | None = Field(default=None, max_length=40)
    address: str | None = Field(default=None, max_length=300)
    city: str | None = Field(default=None, max_length=120)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)

    _normalize_phone = field_validator("phone")(validate_phone_field)


class InventoryItem(BaseModel):
    blood_type: BloodType
    units: int = Field(ge=0, le=100000)
    # Present (non-null) once the caller is on the reservation-aware API --
    # existing callers of the bulk PUT/GET that predate reservations still
    # get a valid response either way (reserved defaults to 0 for a type
    # with no HospitalInventory row yet).
    reserved_units: int = Field(default=0, ge=0, le=100000)
    available_units: int = Field(default=0, ge=0, le=100000)


class ComponentInventoryItem(BaseModel):
    """Phase G: one real HospitalInventory row -- (blood_type, component).
    The legacy GET /me/inventory grid only ever shows WHOLE_BLOOD (its
    contract predates components); this is the honest view of every
    component a facility actually holds."""

    blood_type: BloodType
    component: BloodComponent
    units: int
    reserved_units: int
    available_units: int


class InventoryUpdate(BaseModel):
    items: list[InventoryItem]


class InventoryActionIn(BaseModel):
    """Body for the single-blood-type action endpoints (add/reserve/
    allocate/release/expire). `units` is always a positive delta -- what
    changed, not an absolute count (RECONCILE, which is absolute, has its
    own schema below).

    Phase 35: `component` defaults to WHOLE_BLOOD -- every pre-existing
    caller that never sends this field keeps behaving exactly as before
    (the only component this product ever tracked). `donation_id` is only
    meaningful on the ADD action (ignored elsewhere) -- see
    `inventory_service.add_stock`'s docstring for the honesty boundary on
    what this link does and does not verify.
    """

    blood_type: BloodType
    units: int = Field(gt=0, le=100000)
    request_id: uuid.UUID | None = None
    note: str | None = Field(default=None, max_length=300)
    component: BloodComponent = BloodComponent.WHOLE_BLOOD
    donation_id: uuid.UUID | None = None


class InventoryReconcileIn(BaseModel):
    blood_type: BloodType
    units: int = Field(ge=0, le=100000)
    note: str | None = Field(default=None, max_length=300)
    component: BloodComponent = BloodComponent.WHOLE_BLOOD


class HospitalInventoryLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    blood_type: BloodType
    action: HospitalInventoryAction
    delta_units: int
    delta_reserved: int
    resulting_units: int
    resulting_reserved: int
    request_id: uuid.UUID | None
    note: str | None
    created_at: datetime


class HospitalRouteOut(BaseModel):
    """One transport link touching this hospital -- the same relational
    TransportRoute data admins manage (app/api/routes/admin.py), exposed
    read-only here so a hospital can see what actually connects to its own
    facility instead of only the graph-ranked search results."""

    id: uuid.UUID
    partner_facility_id: uuid.UUID
    partner_facility_name: str | None
    partner_facility_city: str | None
    distance_km: float
    transit_minutes: int
    status: str
    disrupted_reason: str | None


class HospitalActivityItemOut(BaseModel):
    """One entry in the merged, real recent-activity feed
    (GET /hospitals/me/activity): inventory-log rows and blood-request
    events for requests visible to this hospital, interleaved by time.
    `kind` distinguishes which real source table the row came from -- this
    endpoint never invents a unified "activity" table of its own."""

    kind: str  # "inventory" | "request"
    created_at: datetime
    summary: str
    request_id: uuid.UUID | None = None
    blood_type: BloodType | None = None


class HospitalRequestItem(BloodRequestOut):
    """An incoming request as seen by a hospital."""

    hospital_status: HospitalResponseStatus | None = None
    distance_km: float | None = None


_RESPOND_ALLOWED_STATUSES = frozenset(
    {
        HospitalResponseStatus.ACKNOWLEDGED,
        HospitalResponseStatus.PREPARING,
        HospitalResponseStatus.DISPATCHED,
        HospitalResponseStatus.DECLINED,
    }
)


class HospitalRespondIn(BaseModel):
    status: HospitalResponseStatus
    note: str | None = Field(default=None, max_length=500)

    @field_validator("status")
    @classmethod
    def _no_side_effect_statuses_here(
        cls, value: HospitalResponseStatus
    ) -> HospitalResponseStatus:
        # ALLOCATED/FULFILLED have real side effects (inventory deduction,
        # and for FULFILLED, transitioning the shared BloodRequestStatus)
        # that this free-form status endpoint must never trigger implicitly
        # -- only POST .../allocate and .../fulfill can set them.
        if value not in _RESPOND_ALLOWED_STATUSES:
            raise ValueError(
                f"{value.value} cannot be set here -- use the dedicated "
                "allocate/fulfill endpoint instead"
            )
        return value


class HospitalRequestResponseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    request_id: uuid.UUID
    status: HospitalResponseStatus
    note: str | None
    units_allocated: int = 0
    created_at: datetime
    updated_at: datetime
