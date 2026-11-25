"""Confidence shortlist API shapes (app/api/routes/shortlists.py)."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import BloodType, DonorContactStatus, ShortlistSubject, Urgency


class PriorityFactorOut(BaseModel):
    """One line of the unified priority explanation, generated from the
    actual calculation. ``contribution`` is in score points; a MISSING
    factor contributes 0 (its weight goes to the neutral baseline)."""

    name: str
    label: str
    # Release gate C (2026-11-03): weight, value and contribution are null in
    # SUMMARY explanations (patients, family) -- the weights are restricted
    # configuration and the values are other donors' history.
    weight: float | None
    value: float | None
    contribution: float | None
    status: str  # OBSERVED | COARSE | MISSING
    detail: str  # full text for facility/admin managers; a minimal summary otherwise


class PriorityOut(BaseModel):
    model: str  # UNIFIED_PRIORITY/<version>#<fingerprint>
    score: float  # 0..100 -- a ranking measure, NOT a probability of donation
    score_available: bool
    evidence_coverage: float  # 0..1 share of weight observed
    evidence_level: str  # HIGH | MEDIUM | LOW | INSUFFICIENT
    # score points from the neutral fill of missing factors. Privacy round 2
    # (2026-11-03): null in SUMMARY explanations (patients, family), where
    # `score` is also shown in 10-point bands.
    missing_data_baseline: float | None
    availability_status: str
    factors: list[PriorityFactorOut]


class ShortlistCandidateOut(BaseModel):
    """A ranked, eligible donor who has NOT been contacted yet. A candidate,
    never a confirmed donor."""

    donor_ref: str
    donor_name: str | None  # only for the request's managers
    blood_type: BloodType | None
    rank: int
    score: float  # unified priority score / 100 (kept for compatibility)
    components: dict[str, float | None]  # factor values (None = no data)
    priority: PriorityOut
    next_action: str
    distance_basis: str
    distance_km: float | None
    travel_minutes_estimate: int | None
    location_stale: bool | None
    explanation: list[str]
    synthetic: bool


class ShortlistContactOut(BaseModel):
    id: uuid.UUID
    donor_ref: str
    donor_name: str | None
    blood_type: BloodType | None
    status: DonorContactStatus
    batch: int
    rank_at_contact: int
    score_at_contact: float
    ranking_strategy: str | None = None
    scoring_model: str | None = None
    contacted_at: datetime | None
    timeout_at: datetime | None
    responded_at: datetime | None
    eligibility_rechecked_at: datetime | None
    confirmed_at: datetime | None
    status_reason: str | None
    # In-app notification only: SENT_UNREAD or READ. Delivery to a phone or
    # mailbox is never claimed (no provider delivery receipts are wired).
    notification_state: str | None
    sms_status: str | None
    simulated: bool
    synthetic: bool


class ShortlistCountsOut(BaseModel):
    compatible_pool: int
    eligible_candidates: int
    not_yet_contacted: int
    contacted: int
    awaiting_response: int
    accepted: int
    confirmed_for_coordination: int
    declined: int
    unavailable: int
    timed_out: int
    notification_failed: int
    withdrawn: int
    eligibility_recheck_required: int


class ShortlistOut(BaseModel):
    policy: str
    strategy: str  # the ordering of next_to_contact (UNIFIED_PRIORITY or a matching strategy)
    scoring_model: str  # UNIFIED_PRIORITY/<version>#<config fingerprint>
    explanation_level: str  # FULL (facility/admin) | SUMMARY (patient/family)
    subject: ShortlistSubject
    subject_id: uuid.UUID
    is_open: bool
    urgency: Urgency
    units_needed: int
    units_fulfilled: int
    units_reserved_inventory: int
    # Donors the request requires after fulfilled / inventory-reserved units
    # (the field name is kept for API compatibility; it is NOT the unmet part).
    donors_still_needed: int
    # Donors still missing after confirmed donors are counted.
    remaining_unmet: int
    state: str  # SATISFIED | PARTIAL | AWAITING | NO_ELIGIBLE | NOT_STARTED | CLOSED
    summary: str
    counts: ShortlistCountsOut
    excluded: dict[str, int]
    next_to_contact: list[ShortlistCandidateOut]
    contacts: list[ShortlistContactOut]
    escalation: list[str]
    demo_mode: bool
    disclaimer: str


class ShortlistPolicyOut(BaseModel):
    name: str
    model_version: str
    model_fingerprint: str
    strategies: list[str]
    default_strategy: str
    # Weights and thresholds are shown to facility/admin users only.
    weights: dict[str, float] | None
    min_coverage: float | None
    weights_are_provisional: bool
    score_is_probability: bool
    min_response_samples: int
    contact_multiplier: int
    max_batch: int
    response_timeout_minutes: int
    response_timeout_minutes_critical: int
    max_distance_km: float
    assumed_travel_kmph: float
    eligibility_policy: dict
    disclaimer: str


class DemoSimulateIn(BaseModel):
    """Demo mode only. Each AWAITING synthetic donor answers according to the
    persona in their synthetic e-mail address (sim-accept / sim-decline /
    sim-notnow / sim-silent / sim-slow); `force_timeouts` also expires the
    silent ones now instead of waiting for the real timeout."""

    force_timeouts: bool = True
    note: str | None = Field(default=None, max_length=200)


class DemoSimulateOut(BaseModel):
    simulated: dict[str, int]
    skipped_non_synthetic: int
    shortlist: ShortlistOut
