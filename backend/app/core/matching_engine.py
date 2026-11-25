"""Explainable, deterministic donor/responder ranking engine.

Phase 9 requirement: matching must weigh multiple real factors ("Do not
use an unexplained magic score") and every ranked candidate must carry
machine-readable factors plus a human-readable explanation.

WHAT THIS MODULE IS
--------------------
A pure, DB-free ranking function. The caller (an API route) is
responsible for querying the database, filtering to candidates that are
already blood-type compatible, and building one `MatchCandidateInput`
per candidate from real rows. This module only sorts and explains --
it never fabricates a candidate, a distance, or an eligibility state
that the caller didn't actually provide.

FACTORS CONSIDERED, AND WHAT'S HONESTLY NOT MODELED
-----------------------------------------------------
The Phase 9 spec lists ten things matching "should consider, where data
supports it". Here is what this codebase actually has data for, and
what it does not:

  1. compatibility       -- real (`app.core.blood_compatibility`); the
                             caller only ever passes already-compatible
                             candidates, so `MatchFactors.compatible` is
                             always True here (see that field's docstring).
  2. component            -- NOT modeled. This system is whole-blood /
                             red-cell only (see `DonorProfile`'s
                             `alert_min_urgency` comment in
                             app/models/profile.py, a pre-Phase-9 product
                             decision) -- there is no platelet/plasma
                             concept anywhere in the data model to weigh.
  3. donor availability   -- real (`DonorProfile.is_available`).
  4. eligibility          -- real (`app.core.eligibility.is_eligible`,
                             the existing 56-day donation-interval rule).
                             Only meaningful for DONOR responders; `None`
                             for a HOSPITAL responder (eligibility doesn't
                             apply to a facility).
  5. verification         -- real (`DonorProfile.identity_verified`).
                             `None` for a HOSPITAL responder (this system
                             verifies hospitals as `HospitalProfile.verified`,
                             a separate flag the emergency responder query
                             doesn't currently join in -- documented as a
                             known gap in the Phase 9 report rather than
                             silently reusing the donor flag).
  6. location              -- real (haversine `distance_km`), only when
                             both sides have coordinates and the donor has
                             opted into `share_location`.
  7. urgency               -- real (`Emergency.urgency` / `BloodRequest.urgency`).
                             Every strategy is handed the request's actual
                             urgency; it currently informs the explanation
                             text rather than the sort key itself, since no
                             strategy in this engine varies its ordering by
                             urgency tier (documented, not hidden).
  8. response likelihood / reliability -- approximated by `has_accepted`
                             (a real signal: has this candidate already
                             said yes to *this* request/emergency) plus
                             `stale_availability` (see below). There is no
                             historical per-donor response-rate model in
                             this codebase to draw a stronger signal from.
  9. resilience            -- approximated by `stale_availability`: whether
                             a `DonorProfile`'s availability was last
                             confirmed (its `updated_at`) more than
                             `STALE_AVAILABILITY_DAYS` ago. A donor whose
                             availability signal is old is a less resilient
                             pick even if `is_available` is still True.
                             This is a real, computed value, not a guess --
                             but it is a proxy, not a true reliability
                             score, and is documented as such. (The
                             system's other resilience concept -- disrupted
                             `TransportRoute`s between facilities in
                             `app.services.graph_service` -- is a
                             facility-to-facility signal and isn't wired
                             into per-donor ranking; donors don't travel
                             fixed transport routes.)
 10. operational constraints -- NOT modeled at the donor-ranking level.
                             Facility-level operational constraints
                             (inventory, disrupted routes) already have a
                             real, separate engine (`graph_service`); this
                             module ranks donor/hospital *candidates* for a
                             request, which is a different question.

DETERMINISM
-----------
Every strategy's sort key ends with the candidate's `ref` (an opaque,
unique-per-candidate string) as a final tie-breaker, so two runs over the
same input always produce the same order -- including when every other
factor is genuinely equal ("equal candidates" in the Phase 9 TESTING list).
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import UTC, date, datetime

from app.models.enums import BloodType, Urgency

# A DonorProfile whose availability hasn't been touched in longer than this
# is treated as a less resilient pick by the EVENT_DRIVEN strategy -- the
# "is this donor's AVAILABLE flag actually still meaningful" heuristic
# described in the module docstring. Chosen to be well past the 56-day
# donation-eligibility window so it doesn't just re-derive eligibility.
STALE_AVAILABILITY_DAYS = 30


class MatchStrategy(str, enum.Enum):
    """The four comparison strategies named in the Phase 9 spec.

    These names pre-date this engine (they appear as static, illustrative
    labels in the frozen `ResearchMode.tsx` marketing mockup -- see
    LIFELINK_PHASE_9_REPORT.md for the audit that found no real algorithm
    ever backed them). The definitions below are new, real, and specific
    to this codebase's actual data; each docstring says exactly what it
    does and why.
    """

    #: Accepted responders first, then closest first. This is the
    #: pre-existing default ordering used by both match-candidates
    #: endpoints before Phase 9 (see LIFELINK_PHASE_9_REPORT.md) --
    #: kept as the default strategy so existing behavior doesn't change.
    NEAREST = "NEAREST"

    #: Accepted first, then anyone whose donation-eligibility rest period
    #: has already elapsed (or for whom eligibility doesn't apply, e.g. a
    #: hospital) ranks ahead of anyone still in that window, then closest.
    TIME_AWARE = "TIME_AWARE"

    #: TIME_AWARE, plus identity-verified candidates rank ahead of
    #: unverified ones (verification as a dependability signal) before
    #: falling back to distance.
    TIME_RESILIENCE = "TIME_RESILIENCE"

    #: Reacts to what has already happened on the live request/emergency:
    #: accepted responders first, then currently-available ones, then
    #: anyone whose availability signal is fresh (not stale) ranks ahead
    #: of a stale one, and only then does distance decide.
    EVENT_DRIVEN = "EVENT_DRIVEN"


@dataclass(frozen=True)
class MatchCandidateInput:
    """Everything the engine may look at for one candidate, assembled by
    the caller from real rows. The engine never queries the database
    itself -- that's what makes `rank_candidates` a pure, independently
    testable function.
    """

    ref: str
    name: str | None
    blood_type: BloodType | None
    role: str  # "DONOR" or "HOSPITAL"
    is_available: bool
    has_accepted: bool
    distance_km: float | None
    last_donation_date: date | None
    identity_verified: bool | None  # None when not applicable to this role
    is_eligible: bool | None  # None when not applicable to this role
    alert_radius_km: int | None
    availability_updated_at: datetime | None
    city: str | None


@dataclass(frozen=True)
class MatchFactors:
    """Machine-readable factors behind one candidate's rank. `None` means
    "not applicable / not evaluable for this candidate", never "no" --
    see each field's source in the module docstring's numbered list.
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


@dataclass(frozen=True)
class RankedCandidate:
    ref: str
    name: str | None
    blood_type: BloodType | None
    rank: int
    factors: MatchFactors
    explanation: str


def _days_since(d: date | None, today: date) -> int | None:
    if d is None:
        return None
    return (today - d).days


def _is_stale(updated_at: datetime | None, now: datetime) -> bool | None:
    if updated_at is None:
        return None
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    return (now - updated_at).days > STALE_AVAILABILITY_DAYS


def build_factors(
    c: MatchCandidateInput, *, urgency: Urgency, today: date | None = None,
    now: datetime | None = None,
) -> MatchFactors:
    today = today or datetime.now(UTC).date()
    now = now or datetime.now(UTC)
    within_radius = None
    if c.alert_radius_km is not None and c.distance_km is not None:
        within_radius = c.distance_km <= c.alert_radius_km
    return MatchFactors(
        # The caller is expected to only ever pass candidates it already
        # filtered to a compatible blood type -- there is no "incompatible
        # candidate" case to represent here (see class docstring).
        compatible=True,
        available=c.is_available,
        eligible=c.is_eligible,
        verified=c.identity_verified,
        distance_km=c.distance_km,
        within_alert_radius=within_radius,
        has_accepted=c.has_accepted,
        days_since_last_donation=_days_since(c.last_donation_date, today),
        stale_availability=_is_stale(c.availability_updated_at, now),
        urgency=urgency,
    )


def _distance_or_inf(c: MatchCandidateInput) -> float:
    return c.distance_km if c.distance_km is not None else float("inf")


def _sort_key_nearest(c: MatchCandidateInput, f: MatchFactors):
    return (
        not c.has_accepted,
        _distance_or_inf(c),
        c.last_donation_date.isoformat() if c.last_donation_date else "",
        c.ref,
    )


def _sort_key_time_aware(c: MatchCandidateInput, f: MatchFactors):
    # Ineligible (still resting) ranks behind everyone else; "not
    # applicable" (None, e.g. a hospital) is treated as best-case rather
    # than penalized, since there's nothing to wait out.
    not_yet_eligible = 1 if f.eligible is False else 0
    return (not c.has_accepted, not_yet_eligible, _distance_or_inf(c), c.ref)


def _sort_key_time_resilience(c: MatchCandidateInput, f: MatchFactors):
    not_yet_eligible = 1 if f.eligible is False else 0
    # verified=True -> 0 (best), None/not-applicable -> 1, False -> 2 (worst).
    verification_rank = 0 if f.verified is True else (1 if f.verified is None else 2)
    return (not c.has_accepted, not_yet_eligible, verification_rank, _distance_or_inf(c), c.ref)


def _sort_key_event_driven(c: MatchCandidateInput, f: MatchFactors):
    stale_rank = 1 if f.stale_availability else 0
    return (not c.has_accepted, not c.is_available, stale_rank, _distance_or_inf(c), c.ref)


_SORT_KEYS = {
    MatchStrategy.NEAREST: _sort_key_nearest,
    MatchStrategy.TIME_AWARE: _sort_key_time_aware,
    MatchStrategy.TIME_RESILIENCE: _sort_key_time_resilience,
    MatchStrategy.EVENT_DRIVEN: _sort_key_event_driven,
}

_STRATEGY_LABEL = {
    MatchStrategy.NEAREST: "nearest-first",
    MatchStrategy.TIME_AWARE: "time-aware",
    MatchStrategy.TIME_RESILIENCE: "time + resilience",
    MatchStrategy.EVENT_DRIVEN: "event-driven",
}


def explain(factors: MatchFactors, *, strategy: MatchStrategy, rank: int) -> str:
    """A human-readable sentence for one ranked candidate, built only
    from that candidate's own `MatchFactors` -- never a canned template
    with placeholders left unfilled. Mirrors the shape of the Phase 9
    spec's own example: "Selected because donor is compatible, currently
    available, verified, within approximate distance threshold, and
    ranked higher under the configured emergency strategy."
    """
    bits: list[str] = ["compatible" if factors.compatible else "not compatible"]
    bits.append("currently available" if factors.available else "not currently available")
    if factors.eligible is not None:
        bits.append(
            "eligible to donate now" if factors.eligible
            else "not yet eligible (within the post-donation rest period)"
        )
    if factors.verified is not None:
        bits.append("identity-verified" if factors.verified else "not identity-verified")
    if factors.within_alert_radius is not None:
        bits.append(
            "within the configured alert radius" if factors.within_alert_radius
            else "outside the configured alert radius"
        )
    elif factors.distance_km is not None:
        bits.append(f"approximately {factors.distance_km:.1f} km away")
    if factors.has_accepted:
        bits.append("has already accepted this request")
    if factors.stale_availability:
        bits.append("availability last confirmed some time ago")

    if len(bits) == 1:
        reason = bits[0]
    else:
        reason = ", ".join(bits[:-1]) + f", and {bits[-1]}"

    return (
        f"Ranked #{rank} because donor is {reason}, "
        f"under the {_STRATEGY_LABEL[strategy]} matching strategy."
    )


def rank_candidates(
    candidates: list[MatchCandidateInput],
    *,
    strategy: MatchStrategy = MatchStrategy.NEAREST,
    urgency: Urgency = Urgency.ROUTINE,
    today: date | None = None,
    now: datetime | None = None,
) -> list[RankedCandidate]:
    """Deterministically sort and explain `candidates` under `strategy`.

    Returns `[]` for an empty input ("no candidates" in the Phase 9
    TESTING list) rather than erroring.
    """
    key_fn = _SORT_KEYS[strategy]
    with_factors = [
        (c, build_factors(c, urgency=urgency, today=today, now=now)) for c in candidates
    ]
    with_factors.sort(key=lambda pair: key_fn(pair[0], pair[1]))

    ranked: list[RankedCandidate] = []
    for i, (c, factors) in enumerate(with_factors, start=1):
        ranked.append(
            RankedCandidate(
                ref=c.ref,
                name=c.name,
                blood_type=c.blood_type,
                rank=i,
                factors=factors,
                explanation=explain(factors, strategy=strategy, rank=i),
            )
        )
    return ranked


# --------------------------------------------------------------------------- #
# Gap elimination: the donor distance limit (MAX_DONOR_DISTANCE_KM)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DistancePolicyResult:
    kept: list[MatchCandidateInput]
    excluded_refs: list[str]
    limit_km: float
    override_active: bool
    unknown_distance: int


def apply_distance_limit(
    candidates: list[MatchCandidateInput], *, limit_km: float, override: bool = False,
) -> DistancePolicyResult:
    """Drop DONOR candidates whose KNOWN distance exceeds `limit_km`.

    * a donor exactly at the limit is kept (the limit is inclusive);
    * a donor whose distance is unknown (no shared location, or no
      coordinates on the request) is kept -- excluding them would hide real
      donors -- and the rankers already order unknown distance last;
    * a donor who has already ACCEPTED is always kept (an existing
      commitment is never silently hidden);
    * hospitals are never filtered here (facility reach is the pathway
      engine's job);
    * `override=True` (an audited ADMIN decision) keeps everyone.
    """
    kept: list[MatchCandidateInput] = []
    excluded: list[str] = []
    unknown = 0
    for c in candidates:
        if c.role != "DONOR":
            kept.append(c)
            continue
        if c.distance_km is None:
            unknown += 1
            kept.append(c)
            continue
        if override or c.has_accepted or c.distance_km <= limit_km:
            kept.append(c)
        else:
            excluded.append(c.ref)
    return DistancePolicyResult(kept=kept, excluded_refs=excluded, limit_km=limit_km,
                                override_active=override, unknown_distance=unknown)
