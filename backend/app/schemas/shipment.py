from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    BloodComponent,
    BloodType,
    ColdChainStatus,
    ShipmentItemOutcome,
    ShipmentReconciliationStatus,
    ShipmentStatus,
    TelemetrySource,
)


class ShipmentLineIn(BaseModel):
    """One (blood_type, component, units) line of a shipment being
    created -- the same "count, not raw unit IDs" shape every other
    inventory-mutating endpoint in this codebase already uses
    (InventoryActionIn); the server picks the actual, specific FEFO units
    via `inventory_service.reserve_stock`, exactly as a RESERVE action
    already does."""

    blood_type: BloodType
    units: int = Field(gt=0, le=10000)
    component: BloodComponent = BloodComponent.WHOLE_BLOOD


class ShipmentCreateIn(BaseModel):
    origin_facility_id: uuid.UUID
    destination_facility_id: uuid.UUID
    items: list[ShipmentLineIn] = Field(min_length=1, max_length=20)
    # Optional, purely informational -- which registered TransportRoute
    # (app/models/transport.py) this shipment is using, if any. Never an
    # authority over the shipment's own lifecycle (Section 16).
    transport_route_id: uuid.UUID | None = None
    note: str | None = Field(default=None, max_length=1000)
    # Same idempotent-create pattern as Emergency/BloodRequest/Donation --
    # see Shipment.idempotency_key.
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)


class ShipmentActionIn(BaseModel):
    """Body shared by every shipment state-transition action that isn't
    creation itself (dispatch/mark-in-transit/mark-arrived/receive/cancel/
    fail) -- just an optional free-text reason/note, the same minimal
    shape `InventoryActionIn.note` already uses."""

    reason: str | None = Field(default=None, max_length=500)


class ShipmentReconcileIn(BaseModel):
    """Phase E -- the one explicit decision about a FAILED shipment's
    stranded units. See app.api.routes.shipments.reconcile_shipment for
    the full safety policy."""

    outcome: ShipmentReconciliationStatus
    # Mandatory: a reconciliation is a human attestation; it must say why.
    note: str = Field(min_length=10, max_length=1000)
    # RETURNED_TO_ORIGIN only: every unit code the shipment carried, echoed
    # back as confirmation that each one physically came back.
    unit_codes: list[str] = Field(default_factory=list, max_length=10000)
    # RETURNED_TO_ORIGIN only: explicit attestation that storage conditions
    # were maintained throughout. Without it units are never restocked.
    cold_chain_maintained: bool = False
    # REDIRECTED only: where the reporter says the units went (recorded,
    # not verified).
    redirected_to_facility_id: uuid.UUID | None = None
    # Gap elimination: cold chain.
    temp_min_c: float | None = None
    temp_max_c: float | None = None
    cold_chain_status: ColdChainStatus | None = None
    cold_chain_evaluated_at: datetime | None = None


class ShipmentDelayIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
    # Optional revised ETA -- explicitly an estimate (Section 17); omit to
    # just record that a delay happened without revising the prior ETA.
    new_estimated_arrival_at: datetime | None = None


class ShipmentItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    blood_unit_id: uuid.UUID
    is_active: bool
    # Filled in from the joined BloodUnit -- not duplicated storage, see
    # ShipmentItem's own docstring.
    unit_code: str | None = None
    blood_type: BloodType | None = None
    component: BloodComponent | None = None
    # Gap elimination: what happened to THIS unit at receipt/reconciliation.
    outcome: ShipmentItemOutcome = ShipmentItemOutcome.PENDING
    outcome_reason: str | None = None
    outcome_at: datetime | None = None
    resulting_unit_id: uuid.UUID | None = None


class ShipmentEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    event_type: str
    note: str | None
    actor_user_id: uuid.UUID | None
    created_at: datetime


class UnitTraceOut(BaseModel):
    """Answer to "where is unit X now" (Section 9/25) -- current state,
    which shipment (if any) carried/is carrying it, who/where it was
    received, and its donation reference, all without exposing any donor
    PII (only `donation_id`, never the donor's identity -- see
    `BloodUnit.donation_id`'s own honesty-boundary docstring)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    unit_code: str
    blood_type: BloodType
    component: BloodComponent
    state: str
    hospital_id: uuid.UUID
    hospital_name: str | None = None
    donation_id: uuid.UUID | None
    # Set only for a unit created by a shipment's receipt.
    source_shipment_id: uuid.UUID | None
    source_shipment_code: str | None = None
    source_unit_id: uuid.UUID | None
    # The shipment currently carrying this unit (its own origin-side row),
    # if any -- distinct from source_shipment_id above, which is the
    # shipment that *delivered* a destination-side unit.
    carrying_shipment_id: uuid.UUID | None = None
    carrying_shipment_code: str | None = None
    received_at: datetime
    expires_at: datetime | None


class ShipmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    shipment_code: str
    origin_facility_id: uuid.UUID
    destination_facility_id: uuid.UUID
    origin_facility_name: str | None = None
    destination_facility_name: str | None = None
    transport_route_id: uuid.UUID | None
    status: ShipmentStatus
    created_by_user_id: uuid.UUID | None
    dispatched_at: datetime | None
    in_transit_at: datetime | None
    arrived_at: datetime | None
    received_at: datetime | None
    cancelled_at: datetime | None
    failed_at: datetime | None
    estimated_arrival_at: datetime | None
    delayed_at: datetime | None
    delay_reason: str | None
    cancel_reason: str | None
    failure_reason: str | None
    note: str | None
    created_at: datetime
    updated_at: datetime
    # Every state this shipment could legally move to right now (empty for
    # a terminal shipment) -- app.core.shipment_lifecycle, same idea as
    # every other domain's response shape in this codebase.
    allowed_transitions: list[ShipmentStatus] = []
    # Route condition (OPERATIONAL/DISRUPTED), looked up separately and
    # kept explicitly distinct from `status` above (Section 16) -- never
    # merged into the shipment's own lifecycle.
    route_status: str | None = None
    route_disrupted_reason: str | None = None
    items: list[ShipmentItemOut] = []
    # Phase E -- NULL unless FAILED; see ShipmentReconciliationStatus.
    reconciliation_status: ShipmentReconciliationStatus | None = None
    reconciled_at: datetime | None = None
    reconciled_by_user_id: uuid.UUID | None = None
    # Audit C21 (2026-10-31): set when the shipment came from an emergency
    # pathway hand-off.
    emergency_id: uuid.UUID | None = None
    pathway_id: uuid.UUID | None = None
    reconciliation_note: str | None = None
    redirected_to_facility_id: uuid.UUID | None = None
    # Gap elimination: cold chain.
    temp_min_c: float | None = None
    temp_max_c: float | None = None
    cold_chain_status: ColdChainStatus | None = None
    cold_chain_evaluated_at: datetime | None = None


# --------------------------------------------------------------------------- #
# Gap elimination: per-unit receipt outcomes and cold-chain telemetry
# --------------------------------------------------------------------------- #
_RECEIPT_OUTCOMES = "^(RECEIVED_OK|QUARANTINED|DAMAGED|SPOILED|LOST)$"


class ItemOutcomeIn(BaseModel):
    unit_code: str = Field(min_length=1, max_length=40)
    outcome: str = Field(pattern=_RECEIPT_OUTCOMES)
    reason: str | None = Field(default=None, max_length=500)


class ShipmentReceiveIn(BaseModel):
    """Receipt. Units not listed in `items` are RECEIVED_OK -- unless the
    shipment's cold chain shows an excursion, sensor failure or data gap,
    in which case they are QUARANTINED for review (never silently stocked)."""
    reason: str | None = Field(default=None, max_length=500)
    items: list[ItemOutcomeIn] = Field(default_factory=list, max_length=10000)


class TemperatureReadingIn(BaseModel):
    recorded_at: datetime
    temperature_c: float | None = Field(default=None, ge=-273.15, le=1000)
    sensor_id: str = Field(min_length=1, max_length=80)
    error_code: str | None = Field(default=None, max_length=80)


class TelemetryIn(BaseModel):
    source: TelemetrySource = TelemetrySource.SENSOR
    readings: list[TemperatureReadingIn] = Field(min_length=1, max_length=5000)


class TemperatureReadingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    recorded_at: datetime
    temperature_c: float | None
    sensor_id: str
    source: TelemetrySource
    error_code: str | None


class ExcursionOut(BaseModel):
    start: datetime
    end: datetime
    readings: int
    min_c: float
    max_c: float


class ColdChainReportOut(BaseModel):
    status: ColdChainStatus
    readings: int
    valid_readings: int
    failed_readings: int
    min_c: float | None
    max_c: float | None
    range_min_c: float | None
    range_max_c: float | None
    excursions: list[ExcursionOut]
    gaps_minutes: list[float]
    findings: list[str]
    sources: dict[str, int]


class TelemetryOut(BaseModel):
    report: ColdChainReportOut
    readings: list[TemperatureReadingOut]
    inserted: int | None = None
    duplicates: int | None = None


class TelemetrySimulateIn(BaseModel):
    """Development-only demo data generator (labelled SIMULATED)."""
    minutes: int = Field(default=120, ge=1, le=7 * 24 * 60)
    interval_minutes: int = Field(default=10, ge=1, le=240)
    excursion_at_minute: int | None = Field(default=None, ge=0)
    excursion_c: float | None = Field(default=None, ge=-90, le=60)
    sensor_failure_at_minute: int | None = Field(default=None, ge=0)
    seed: int = 7
