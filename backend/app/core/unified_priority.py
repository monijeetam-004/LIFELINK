"""Unified Donor Priority Score -- model ``UNIFIED_PRIORITY`` (2026-10-31).

A ranking score for donors who have ALREADY passed every hard eligibility
check (``app.services.donor_shortlist_service.evaluate``). It orders them;
it never admits or excludes anyone, and it is NOT a probability of donation.
"92 / 100" means "ranks highly under the configured, provisional criteria",
nothing more. See docs/UNIFIED_DONOR_PRIORITY.md for the factor inventory.

Pure and DB-free: the caller assembles ``PriorityInputs`` from real rows.

Formula
-------
    score = 100 * ( sum_{observed i} w_i * s_i  +  sum_{missing j} w_j * 0.5 )

* ``w`` are the configured weights, validated (finite, >= 0, positive sum)
  and normalised to sum to 1; ``s_i`` is each factor's value in [0, 1].
* A factor with no evidence is MISSING. It contributes nothing of its own;
  its weight is filled with the neutral value 0.5 (the "missing-data
  baseline"), so a donor with little evidence drifts to 50 -- never towards
  "highly reliable" -- and the baseline is reported as its own line.
  Renormalising over observed factors only was rejected: it would let a
  donor with one good signal outrank well-documented donors.
* ``coverage`` = the share of weight that was observed. Below
  ``PRIORITY_MIN_COVERAGE`` the score is reported as unavailable and the
  caller orders the candidate with the TIME_RESILIENCE fallback.
* Range: 0..100. Explanation lines always sum to the score (rounding aside).

Factors (each defined once -- see "double counting" below)
-----------------------------------------------------------
availability  explicit willingness: accepted THIS request -> 1.0; availability
              explicitly confirmed <= 7 d -> 1.0, linear to 0.6 at 30 d, stale
              (> 30 d, the EVENT_DRIVEN threshold) -> 0.3; never confirmed ->
              MISSING ("unknown", never shown as available).
response      invitations answered / invitations received, shrunk towards 0.5
              below ``min_response_samples``; no invitation ever -> MISSING.
acceptance    accepted / answered (conditional on answering), shrunk the same
              way; never answered -> MISSING.
distance      1 - d / limit for a measured distance (inclusive limit);
              same city without coordinates -> 0.5 (coarse); unknown -> MISSING.
activity      recency of a response or a recorded donation (<= 7 d -> 1.0,
              linear to 0.1 at the activity window). Sign-ins and availability
              confirmations are NOT activity here. None -> MISSING.
freshness     age of the shared location: <= stale/3 -> 1.0, <= stale -> 0.7,
              older -> 0.2; no measured location or no timestamp -> MISSING.
verification  0.6 if identity verified + up to 0.4 for facility-verified
              donations (3 or more = full). Always observed.

Double counting avoided
-----------------------
* travel time is distance / 45 km/h: the same evidence, so it is a hard
  deadline filter only, not a second score;
* a stale location lowers ``freshness`` only (not also ``distance``);
* an availability confirmation counts in ``availability`` only (not also
  ``activity``); accepting this request counts in ``availability`` only;
* ``acceptance`` is conditional on answering, so answering is rewarded once;
* eligibility (TIME_AWARE) and the availability flag (EVENT_DRIVEN) are hard
  filters before scoring, never scored again;
* days since the last donation (NEAREST's tie-break) is not scored: once
  the interval has elapsed it says nothing about willingness.

Tie-breaking: higher score, then scored before unscored, then shorter known
distance (unknown last), then donor id -- deterministic for one snapshot.
"""
from __future__ import annotations

import enum
import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime

MODEL_ID = "UNIFIED_PRIORITY"
MODEL_VERSION = "1.0.0"
NEUTRAL = 0.5

FACTORS: tuple[str, ...] = (
    "availability", "response", "acceptance", "distance", "activity", "freshness",
    "verification",
)
FACTOR_LABELS = {
    "availability": "Explicit availability / willingness",
    "response": "Response reliability",
    "acceptance": "Acceptance when answering",
    "distance": "Distance (travel feasibility)",
    "activity": "Recent meaningful activity",
    "freshness": "Location freshness",
    "verification": "Verification",
}
# Shown to patients and family members instead of the detailed text (no
# counts, no dates: they are other people's histories).
FACTOR_SUMMARIES = {
    "availability": "how recently the donor said they are available",
    "response": "how often the donor answers requests",
    "acceptance": "how often the donor accepts when answering",
    "distance": "how far the donor is",
    "activity": "how recently the donor responded or donated",
    "freshness": "how current the donor's shared location is",
    "verification": "identity and donation verification",
}

AVAILABILITY_FRESH_DAYS = 7
AVAILABILITY_STALE_DAYS = 30  # = matching_engine.STALE_AVAILABILITY_DAYS


class RankingStrategy(str, enum.Enum):
    """What orders the eligible shortlist. The four lexicographic
    strategies are the unchanged ``app.core.matching_engine`` ones."""

    UNIFIED_PRIORITY = "UNIFIED_PRIORITY"
    NEAREST = "NEAREST"
    TIME_AWARE = "TIME_AWARE"
    TIME_RESILIENCE = "TIME_RESILIENCE"
    EVENT_DRIVEN = "EVENT_DRIVEN"


class AvailabilityStatus(str, enum.Enum):
    ACCEPTED_THIS_REQUEST = "ACCEPTED_THIS_REQUEST"  # willing; not confirmed or donated
    CONFIRMED_RECENTLY = "CONFIRMED_RECENTLY"
    CONFIRMED_STALE = "CONFIRMED_STALE"
    UNCONFIRMED = "UNCONFIRMED"  # marked available, never explicitly confirmed


def validate_weights(raw: dict[str, float]) -> dict[str, float]:
    """Return the normalised weights, or raise ValueError -- never a silent
    fix-up. Unknown or missing factors, non-finite or negative values and a
    zero sum are all rejected."""
    if set(raw) != set(FACTORS):
        raise ValueError(
            f"Unified priority weights must name exactly {sorted(FACTORS)}; got {sorted(raw)}")
    for k, v in raw.items():
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
            raise ValueError(f"PRIORITY_WEIGHT_{k.upper()} must be a finite number")
        if v < 0:
            raise ValueError(f"PRIORITY_WEIGHT_{k.upper()} must not be negative")
    total = float(sum(raw.values()))
    if total <= 0:
        raise ValueError("At least one PRIORITY_WEIGHT_* must be positive")
    return {k: raw[k] / total for k in FACTORS}


@dataclass(frozen=True)
class PriorityConfig:
    weights: dict[str, float]  # normalised
    min_response_samples: int = 3
    activity_window_days: int = 30
    location_stale_days: int = 90
    min_coverage: float = 0.30

    def fingerprint(self) -> str:
        blob = json.dumps({
            "model": MODEL_ID, "version": MODEL_VERSION,
            "weights": {k: round(self.weights[k], 6) for k in FACTORS},
            "min_response_samples": self.min_response_samples,
            "activity_window_days": self.activity_window_days,
            "location_stale_days": self.location_stale_days,
            "min_coverage": round(self.min_coverage, 6),
            "availability_days": [AVAILABILITY_FRESH_DAYS, AVAILABILITY_STALE_DAYS],
        }, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:12]

    def model_tag(self) -> str:
        return f"{MODEL_ID}/{MODEL_VERSION}#{self.fingerprint()}"


def config_from_settings() -> PriorityConfig:
    from app.core.config import settings

    return PriorityConfig(
        weights=validate_weights(settings.priority_weights_raw()),
        min_response_samples=settings.shortlist_min_response_samples,
        activity_window_days=settings.shortlist_activity_window_days,
        location_stale_days=settings.donor_location_stale_days,
        min_coverage=settings.priority_min_coverage,
    )


@dataclass(frozen=True)
class PriorityInputs:
    """Real, already-loaded evidence for one eligible donor."""

    has_accepted_this_subject: bool
    availability_confirmed_at: datetime | None
    invited: int
    answered: int
    accepted: int
    last_response_at: datetime | None
    last_donation_recorded_at: datetime | None
    distance_basis: str  # distance_policy.DistanceBasis value
    distance_km: float | None
    distance_limit_km: float
    location_age_days: int | None
    identity_verified: bool
    verified_donations: int


@dataclass(frozen=True)
class FactorResult:
    name: str
    label: str
    weight: float  # normalised
    value: float | None  # None = missing
    contribution: float  # points of the 0..100 score; 0 when missing
    status: str  # OBSERVED | COARSE | MISSING
    detail: str  # full text (request managers with a facility/admin role)
    summary: str  # minimal text (patients and family members)


@dataclass(frozen=True)
class PriorityScore:
    score: float  # 0..100
    score_available: bool
    coverage: float  # 0..1, share of weight observed
    evidence_level: str  # HIGH | MEDIUM | LOW | INSUFFICIENT
    missing_baseline: float  # points contributed by the neutral fill of missing factors
    availability_status: AvailabilityStatus
    factors: list[FactorResult] = field(default_factory=list)
    model_tag: str = ""

    def snapshot(self) -> dict:
        """Minimal, reproducible record for a contact decision: factor values
        only -- no names, dates, coordinates or free text."""
        return {
            "model": self.model_tag, "score": self.score, "coverage": self.coverage,
            "available": self.score_available,
            "factors": {f.name: (None if f.value is None else round(f.value, 4))
                        for f in self.factors},
        }


def _days(ts: datetime | None, now: datetime) -> float | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return max(0.0, (now - ts).total_seconds() / 86400)


def _shrunk(num: int, den: int, m: int) -> float:
    return max(0.0, min(1.0, (num + NEUTRAL * m) / (den + m)))


def _availability(x: PriorityInputs, now: datetime):
    if x.has_accepted_this_subject:
        return (1.0, "OBSERVED", AvailabilityStatus.ACCEPTED_THIS_REQUEST,
                "accepted this request (willing; not yet confirmed or donated)")
    d = _days(x.availability_confirmed_at, now)
    if d is None:
        return (None, "MISSING", AvailabilityStatus.UNCONFIRMED,
                "availability never explicitly confirmed (unknown, not assumed)")
    if d <= AVAILABILITY_FRESH_DAYS:
        return 1.0, "OBSERVED", AvailabilityStatus.CONFIRMED_RECENTLY, \
            f"confirmed available {int(d)} day(s) ago"
    if d <= AVAILABILITY_STALE_DAYS:
        v = 1.0 - 0.4 * (d - AVAILABILITY_FRESH_DAYS) / (AVAILABILITY_STALE_DAYS
                                                        - AVAILABILITY_FRESH_DAYS)
        return v, "OBSERVED", AvailabilityStatus.CONFIRMED_RECENTLY, \
            f"confirmed available {int(d)} day(s) ago"
    return 0.3, "OBSERVED", AvailabilityStatus.CONFIRMED_STALE, \
        f"availability last confirmed {int(d)} days ago (stale)"


def compute(x: PriorityInputs, cfg: PriorityConfig, *, now: datetime | None = None
            ) -> PriorityScore:
    now = now or datetime.now(UTC)
    m = cfg.min_response_samples
    raw: dict[str, tuple[float | None, str, str]] = {}

    av, av_status, av_state, av_text = _availability(x, now)
    raw["availability"] = (av, av_status, av_text)

    if x.invited <= 0:
        raw["response"] = (None, "MISSING", "no invitation on record (unknown, not unreliable)")
    else:
        note = f"answered {x.answered} of {x.invited} invitation(s)"
        if x.invited < m:
            note += f" (fewer than {m}: shrunk towards neutral)"
        raw["response"] = (_shrunk(x.answered, x.invited, m), "OBSERVED", note)

    if x.answered <= 0:
        raw["acceptance"] = (None, "MISSING", "never answered a request (unknown)")
    else:
        note = f"accepted {x.accepted} of {x.answered} answered request(s)"
        if x.answered < m:
            note += f" (fewer than {m}: shrunk towards neutral)"
        raw["acceptance"] = (_shrunk(x.accepted, x.answered, m), "OBSERVED", note)

    if x.distance_basis == "MEASURED" and x.distance_km is not None:
        lim = x.distance_limit_km
        v = max(0.0, 1.0 - x.distance_km / lim) if lim > 0 else 0.0
        raw["distance"] = (v, "OBSERVED", f"{x.distance_km:.1f} km away (limit {lim:g} km)")
    elif x.distance_basis == "SAME_CITY":
        raw["distance"] = (0.5, "COARSE", "same city, exact distance unknown")
    else:
        raw["distance"] = (None, "MISSING", "distance unknown")

    last = max([t for t in (x.last_response_at, x.last_donation_recorded_at) if t is not None],
               default=None, key=lambda t: t if t.tzinfo else t.replace(tzinfo=UTC))
    d = _days(last, now)
    if d is None:
        raw["activity"] = (None, "MISSING", "no response or recorded donation on file")
    else:
        w = max(cfg.activity_window_days, AVAILABILITY_FRESH_DAYS + 1)
        if d <= AVAILABILITY_FRESH_DAYS:
            v = 1.0
        elif d >= w:
            v = 0.1
        else:
            v = 1.0 - 0.9 * (d - AVAILABILITY_FRESH_DAYS) / (w - AVAILABILITY_FRESH_DAYS)
        raw["activity"] = (v, "OBSERVED", f"last responded or donated {int(d)} day(s) ago")

    if x.distance_basis == "MEASURED" and x.location_age_days is not None:
        s = cfg.location_stale_days
        age = x.location_age_days
        v = 1.0 if age <= s / 3 else (0.7 if age <= s else 0.2)
        raw["freshness"] = (v, "OBSERVED", f"location shared {age} day(s) ago"
                            + (" (stale)" if age > s else ""))
    else:
        raw["freshness"] = (None, "MISSING", "no dated, shared location")

    v = (0.6 if x.identity_verified else 0.0) + 0.4 * min(x.verified_donations, 3) / 3
    raw["verification"] = (v, "OBSERVED", ", ".join(
        ["identity verified" if x.identity_verified else "identity not verified"]
        + ([f"{x.verified_donations} facility-verified donation(s)"]
           if x.verified_donations else [])))

    factors: list[FactorResult] = []
    observed_w = 0.0
    missing_w = 0.0
    total = 0.0
    for name in FACTORS:
        w = cfg.weights[name]
        value, status, text = raw[name]
        if value is None:
            missing_w += w
            contribution = 0.0
        else:
            observed_w += w
            contribution = 100.0 * w * value
            total += contribution
        factors.append(FactorResult(
            name=name, label=FACTOR_LABELS[name], weight=round(w, 4),
            value=None if value is None else round(value, 4),
            contribution=round(contribution, 2), status=status, detail=text,
            summary=FACTOR_SUMMARIES[name] + (" (no data)" if value is None else ""),
        ))
    baseline = 100.0 * NEUTRAL * missing_w
    coverage = round(observed_w, 4)
    level = ("HIGH" if coverage >= 0.8 else "MEDIUM" if coverage >= 0.5
             else "LOW" if coverage >= cfg.min_coverage else "INSUFFICIENT")
    return PriorityScore(
        score=round(total + baseline, 2), score_available=coverage >= cfg.min_coverage,
        coverage=coverage, evidence_level=level, missing_baseline=round(baseline, 2),
        availability_status=av_state, factors=factors, model_tag=cfg.model_tag(),
    )


def next_action(status: AvailabilityStatus, *, contacted: bool) -> str:
    if contacted:
        return "await the donor's answer"
    if status is AvailabilityStatus.ACCEPTED_THIS_REQUEST:
        return "confirm after the eligibility re-check"
    return "contact (invitation)"
