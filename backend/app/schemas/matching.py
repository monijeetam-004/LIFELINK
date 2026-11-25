from __future__ import annotations

import uuid
from datetime import date

from pydantic import BaseModel

from app.core.matching_engine import MatchStrategy
from app.models.enums import BloodType, Urgency


class MatchFactorsOut(BaseModel):
    """Machine-readable factors behind one candidate's rank
    (`app.core.matching_engine.MatchFactors`). `None` means "not
    applicable to this candidate", never "no" -- see the engine's module
    docstring for exactly what each field is derived from.

    Deliberately has no `score` field: Phase 9 explicitly rules out "an
    unexplained magic score" -- these factors plus `explanation` are the
    whole of what a rank is based on.
    """

    compatible: bool
    available: bool
    eligible: bool | None
    verified: bool | None
    distance_km: float | None
    within_alert_radius: bool | None
    has_accepted: bool
    days_since_last_donation: int | None
    stale_availability: bool | None
    urgency: Urgency


class MatchCandidate(BaseModel):
    """A factual view of a potential donor for a request. No score is produced."""

    donor_ref: str  # short opaque id (first 8 of user id) — not PII
    donor_name: str | None  # only populated for the request's own manager
    blood_type: BloodType
    is_available: bool
    has_accepted: bool
    distance_km: float | None
    last_donation_date: date | None
    city: str | None
    rank: int
    factors: MatchFactorsOut
    explanation: str


class MatchCandidatesOut(BaseModel):
    request_id: uuid.UUID
    recipient_blood_type: BloodType
    compatible_donor_types: list[BloodType]
    strategy: MatchStrategy
    candidates: list[MatchCandidate]
    # Gap elimination: the donor distance policy that was applied.
    distance_limit_km: float | None = None
    excluded_beyond_limit: int = 0
    unknown_distance: int = 0
    distance_override_active: bool = False


class StrategyCandidateOut(BaseModel):
    """One candidate's rank + explanation under one strategy, for the
    research-mode strategy-comparison endpoints. Deliberately excludes
    `donor_name`: comparison views are always anonymised (see
    `app.api.routes.matching.blood_request_match_strategies`)."""

    donor_ref: str
    blood_type: BloodType | None
    rank: int
    factors: MatchFactorsOut
    explanation: str


class StrategyResultOut(BaseModel):
    strategy: MatchStrategy
    candidates: list[StrategyCandidateOut]


class StrategyComparisonOut(BaseModel):
    """Real output of all four Phase 9 comparison strategies (nearest,
    time-aware, time + resilience, event-driven) run over the same real
    candidate pool -- see `app.core.matching_engine` for the algorithms.
    Backs the authenticated "Strategy Lab" screen; unlike the frozen
    public `ResearchMode` / `MathematicalModel` mockups (kept as-is —
    see LIFELINK_PHASE_9_REPORT.md), every number here comes from an
    actual run of the ranking engine against live data.
    """

    recipient_blood_type: BloodType | None
    strategies: list[StrategyResultOut]


class MapDonorPin(BaseModel):
    """Anonymised, coarse donor location for the live map. No id or name."""

    blood_type: BloodType
    latitude: float
    longitude: float


class MapDataOut(BaseModel):
    center: dict[str, float] | None
    donors: list[MapDonorPin]


class BloodGroupCompatibility(BaseModel):
    """One canonical blood group's full compatibility profile, from the
    central engine (`app.core.blood_compatibility`)."""

    blood_type: BloodType
    ui_label: str
    can_donate_to: list[BloodType]
    can_receive_from: list[BloodType]


class BloodCompatibilityOut(BaseModel):
    """The full authoritative compatibility matrix, served over the wire
    so the frontend never needs its own copy of the table (Phase 9: "Do
    NOT duplicate compatibility logic in ... frontend")."""

    groups: list[BloodGroupCompatibility]
