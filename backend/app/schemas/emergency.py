from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from app.core.matching_engine import MatchStrategy
from app.models.enums import (
    BloodComponent,
    BloodType,
    DonorResponseStatus,
    EmergencyPathwayStatus,
    EmergencyStatus,
    GeocodeSource,
    GraphPathwaySource,
    PathwayFeasibility,
    Urgency,
)
from app.models.user import UserRole
from app.schemas.matching import MatchFactorsOut


class EmergencyCreate(BaseModel):
    blood_type: BloodType | None = None
    urgency: Urgency = Urgency.CRITICAL
    location_text: str | None = Field(default=None, max_length=300)
    note: str | None = Field(default=None, max_length=1000)
    patient_email: EmailStr | None = None
    # ---- Phase 12 additions (all optional -- omitting any of these
    # reproduces pre-Phase-12 behavior exactly) ----
    units_needed: int | None = Field(default=1, gt=0, le=50)
    deadline: datetime | None = None
    # Client-generated once per SOS button-press and resent unchanged on
    # any retry (network retry, double-click, browser resubmission) --
    # see Emergency.idempotency_key and create_emergency's replay handling.
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)
    # Optional: name a specific facility up front. Validated for real
    # (must exist, must be `verified`) when supplied; the emergency is
    # still broadcast to every compatible donor/hospital exactly as
    # before regardless of whether this is set.
    target_facility_id: uuid.UUID | None = None
    # ---- Gap elimination ----
    # The component needed (never substituted by another component).
    component: BloodComponent = BloodComponent.WHOLE_BLOOD
    # Structured location. Both coordinates or neither; each within range.
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    city: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def _coords_pair(self) -> EmergencyCreate:
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be given together")
        return self


class EmergencyEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    event_type: str
    note: str | None
    actor_user_id: uuid.UUID | None
    created_at: datetime


class EmergencyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_by_user_id: uuid.UUID
    patient_user_id: uuid.UUID | None
    created_by_name: str | None = None
    patient_name: str | None = None
    # True when a FAMILY member started this on the patient's behalf --
    # lets the UI show "Requested by family member" without guessing.
    filed_by_family: bool = False
    blood_type: BloodType | None
    urgency: Urgency
    status: EmergencyStatus
    location_text: str | None
    note: str | None
    resolved_at: datetime | None
    created_at: datetime
    updated_at: datetime
    # ---- Phase 12 additions ----
    units_needed: int | None = None
    deadline: datetime | None = None
    target_facility_id: uuid.UUID | None = None
    # ---- Gap elimination ----
    component: BloodComponent = BloodComponent.WHOLE_BLOOD
    latitude: float | None = None
    longitude: float | None = None
    city: str | None = None
    geocode_source: GeocodeSource | None = None
    geocode_detail: str | None = None
    deadline_warning_sent_at: datetime | None = None
    deadline_escalated_at: datetime | None = None
    # Computed on read, never stored -- see app.api.routes.emergencies._slack_seconds.
    # Positive: time remaining. Zero: exactly at deadline. Negative: overdue.
    # None when there is no deadline to measure against. Never clamped.
    slack_seconds: float | None = None
    # ---- Phase 31 addition ----
    # Populated straight from the model column (see Emergency.deadline_exceeded_at)
    # via `model_validate`'s from_attributes -- set exactly once, the first time
    # any read or mutating request notices `deadline` has passed on a still-open
    # emergency (app.services.emergency_lifecycle.flag_deadline_exceeded_if_needed).
    # None means either no deadline was ever set, or it hasn't passed yet -- never
    # inferred here from `slack_seconds` being negative, since that would make the
    # flag racy with whichever route the frontend happened to call last.
    deadline_exceeded_at: datetime | None = None
    # Every state this emergency could legally move to right now (empty for
    # a terminal emergency) -- from app.core.emergency_lifecycle, the same
    # idea as Phase 10's BloodRequest response shape.
    allowed_transitions: list[EmergencyStatus] = []
    # Set only on the just-created response, and only when this creation
    # looked like it might be a second SOS for the same patient/creator
    # without a matching idempotency key -- see create_emergency. Never
    # blocks creation; purely informational for the frontend to warn with.
    possible_duplicate_of: uuid.UUID | None = None


class EmergencyResponderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    responder_user_id: uuid.UUID
    responder_role: UserRole
    status: DonorResponseStatus
    note: str | None
    created_at: datetime
    # Filled in for the emergency's own creator only.
    responder_name: str | None = None
    # Set only when this response just attempted an inventory action (a
    # HOSPITAL responder accepting/withdrawing against an emergency that
    # has both blood_type and units_needed set) -- honestly reports what
    # actually happened, including a failure, never a fabricated success.
    inventory_note: str | None = None


class EmergencyPathwayOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    version: int
    status: EmergencyPathwayStatus
    origin_facility_id: uuid.UUID
    selected_facility_id: uuid.UUID | None
    pathway_source: GraphPathwaySource
    distance_km: float | None
    transit_minutes: int | None
    hops: int | None
    graph_available: bool
    reason: str
    superseded_at: datetime | None
    superseded_reason: str | None
    created_at: datetime
    # ---- Gap elimination ----
    plan_round: int = 1
    backup_rank: int = 0
    feasibility: PathwayFeasibility | None = None
    units_requested: int | None = None
    units_planned: int | None = None
    eta_minutes: int | None = None
    arrival_at: datetime | None = None
    deadline_at: datetime | None = None
    slack_minutes: float | None = None
    strategy: str | None = None
    explanation: list[str] | None = None
    invalidated_at: datetime | None = None
    invalidated_reason: str | None = None
    promoted_at: datetime | None = None


class EmergencyPathwaySelectIn(BaseModel):
    # The facility the blood must be delivered to (the requesting
    # hospital). Optional for admins: defaults to the emergency's target
    # facility or the nearest verified hospital to its coordinates.
    origin_facility_id: uuid.UUID | None = None


class EmergencyDetailOut(EmergencyOut):
    events: list[EmergencyEventOut] = []
    responders: list[EmergencyResponderOut] = []
    accepted_count: int = 0
    my_response: EmergencyResponderOut | None = None
    # The emergency's current pathway plan, if one has ever been selected --
    # None (never fabricated) when no pathway has been chosen yet, or when
    # the last attempt genuinely found nothing reachable.
    current_pathway: EmergencyPathwayOut | None = None
    pathway_history: list[EmergencyPathwayOut] = []
    # Gap elimination: the stored backups of the current plan round, and
    # any audited policy overrides on this emergency.
    backup_pathways: list[EmergencyPathwayOut] = []
    overrides: list[dict] = []


class EmergencyActionIn(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


class EmergencyRespondIn(BaseModel):
    action: DonorResponseStatus
    note: str | None = Field(default=None, max_length=500)


class EmergencyOpenOut(EmergencyOut):
    """An active emergency as offered to a candidate donor / hospital."""

    accepted_count: int = 0
    my_response: EmergencyResponderOut | None = None
    distance_km: float | None = None


class EmergencyMatchResponder(BaseModel):
    responder_ref: str
    responder_name: str | None
    role: UserRole
    blood_type: BloodType | None
    is_available: bool
    has_accepted: bool
    distance_km: float | None
    city: str | None
    rank: int
    factors: MatchFactorsOut
    explanation: str
    # Hospitals only: whether the facility is verified (unverified
    # facilities are never offered as responders).
    facility_verified: bool | None = None


class EmergencyMatchOut(BaseModel):
    emergency_id: uuid.UUID
    recipient_blood_type: BloodType | None
    compatible_donor_types: list[BloodType]
    strategy: MatchStrategy
    candidates: list[EmergencyMatchResponder]
    component: BloodComponent = BloodComponent.WHOLE_BLOOD
    distance_limit_km: float | None = None
    excluded_beyond_limit: int = 0
    unknown_distance: int = 0
    distance_override_active: bool = False
    emergency_has_coordinates: bool = False
