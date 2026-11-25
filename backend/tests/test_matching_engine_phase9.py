"""PHASE 9 -- explainable matching engine tests.

`app.core.matching_engine` is a pure, DB-free ranking function, so most of
this file exercises it directly with hand-built `MatchCandidateInput`s
(no database needed) -- exactly the point of keeping it pure. A few
tests at the bottom go through the real API endpoints to confirm the
engine is actually wired in (compatibility filtering happens before the
engine ever sees a candidate, factors/explanation reach the client, and
no raw "score" is ever exposed).

Covers every item in the Phase 9 TESTING list: ranking determinism,
unavailable donor, incompatible donor, unverified donor, urgent request,
no candidates, equal candidates, location missing, stale availability.
"""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, timedelta

import pytest

from app.core.matching_engine import (
    STALE_AVAILABILITY_DAYS,
    MatchCandidateInput,
    MatchStrategy,
    rank_candidates,
)
from app.models.enums import BloodType, Urgency
from tests.helpers import auth_client

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_TODAY = _NOW.date()


def _candidate(
    ref: str,
    *,
    role: str = "DONOR",
    is_available: bool = True,
    has_accepted: bool = False,
    distance_km: float | None = None,
    last_donation_date: date | None = None,
    identity_verified: bool | None = True,
    is_eligible: bool | None = True,
    alert_radius_km: int | None = None,
    availability_updated_at: datetime | None = _NOW,
    city: str | None = "Metro",
    blood_type: BloodType | None = BloodType.O_NEG,
    name: str | None = None,
) -> MatchCandidateInput:
    return MatchCandidateInput(
        ref=ref,
        name=name,
        blood_type=blood_type,
        role=role,
        is_available=is_available,
        has_accepted=has_accepted,
        distance_km=distance_km,
        last_donation_date=last_donation_date,
        identity_verified=identity_verified,
        is_eligible=is_eligible,
        alert_radius_km=alert_radius_km,
        availability_updated_at=availability_updated_at,
        city=city,
    )


# ---- No candidates ---------------------------------------------------------


def test_no_candidates_returns_empty_list() -> None:
    for strategy in MatchStrategy:
        assert rank_candidates([], strategy=strategy, urgency=Urgency.ROUTINE) == []


# ---- Ranking determinism + equal candidates --------------------------------


def test_ranking_determinism_across_repeated_runs() -> None:
    candidates = [
        _candidate("ccc", distance_km=5.0),
        _candidate("aaa", distance_km=5.0),
        _candidate("bbb", distance_km=2.0, has_accepted=True),
    ]
    for strategy in MatchStrategy:
        first = [
            c.ref for c in rank_candidates(candidates, strategy=strategy, urgency=Urgency.URGENT)
        ]
        shuffled = candidates[:]
        random.Random(42).shuffle(shuffled)
        second = [
            c.ref for c in rank_candidates(shuffled, strategy=strategy, urgency=Urgency.URGENT)
        ]
        assert first == second, strategy


def test_equal_candidates_are_tie_broken_deterministically_by_ref() -> None:
    """Candidates identical in every factor except their ref must still
    produce one single, stable order -- never depend on input/dict order."""
    identical = [
        _candidate("zzz", distance_km=3.0),
        _candidate("mmm", distance_km=3.0),
        _candidate("aaa", distance_km=3.0),
    ]
    for strategy in MatchStrategy:
        ranked = rank_candidates(identical, strategy=strategy, urgency=Urgency.ROUTINE)
        assert [rc.ref for rc in ranked] == ["aaa", "mmm", "zzz"]
        # Re-running with a different input order gives the same result.
        reordered = list(reversed(identical))
        ranked2 = rank_candidates(reordered, strategy=strategy, urgency=Urgency.ROUTINE)
        assert [rc.ref for rc in ranked2] == ["aaa", "mmm", "zzz"]


# ---- Unavailable donor ------------------------------------------------------


def test_event_driven_ranks_available_donor_ahead_of_unavailable() -> None:
    available = _candidate("avail", is_available=True, distance_km=10.0)
    unavailable = _candidate("unavail", is_available=False, distance_km=1.0)
    ranked = rank_candidates(
        [unavailable, available], strategy=MatchStrategy.EVENT_DRIVEN, urgency=Urgency.CRITICAL
    )
    assert [rc.ref for rc in ranked] == ["avail", "unavail"]
    assert ranked[0].factors.available is True
    assert ranked[1].factors.available is False


def test_unavailable_donor_is_still_ranked_not_dropped() -> None:
    """The engine itself never filters -- dropping unavailable candidates
    is the caller's (route's) job. A candidate handed to the engine is
    always ranked and explained, just possibly ranked last."""
    ranked = rank_candidates(
        [_candidate("x", is_available=False)],
        strategy=MatchStrategy.NEAREST,
        urgency=Urgency.ROUTINE,
    )
    assert len(ranked) == 1
    assert ranked[0].factors.available is False


# ---- Unverified donor --------------------------------------------------------


def test_time_resilience_ranks_verified_ahead_of_unverified() -> None:
    verified = _candidate("verified", identity_verified=True, distance_km=8.0)
    unverified = _candidate("unverified", identity_verified=False, distance_km=1.0)
    ranked = rank_candidates(
        [unverified, verified], strategy=MatchStrategy.TIME_RESILIENCE, urgency=Urgency.URGENT
    )
    assert [rc.ref for rc in ranked] == ["verified", "unverified"]
    assert ranked[0].factors.verified is True
    assert ranked[1].factors.verified is False
    assert "identity-verified" in ranked[0].explanation
    assert "not identity-verified" in ranked[1].explanation


def test_unverified_does_not_affect_nearest_or_time_aware_ordering() -> None:
    """Verification only matters under TIME_RESILIENCE -- NEAREST and
    TIME_AWARE are documented as distance/eligibility-driven, not
    verification-driven, so an unverified-but-closer donor still wins
    there. This pins down that the four strategies are genuinely
    different algorithms, not the same sort with cosmetic renames."""
    verified_far = _candidate("verified_far", identity_verified=True, distance_km=8.0)
    unverified_near = _candidate("unverified_near", identity_verified=False, distance_km=1.0)
    for strategy in (MatchStrategy.NEAREST, MatchStrategy.TIME_AWARE):
        ranked = rank_candidates(
            [verified_far, unverified_near], strategy=strategy, urgency=Urgency.ROUTINE
        )
        assert [rc.ref for rc in ranked] == ["unverified_near", "verified_far"], strategy


# ---- Eligibility (time-awareness) -------------------------------------------


def test_time_aware_ranks_eligible_donor_ahead_of_ineligible() -> None:
    eligible = _candidate("eligible", is_eligible=True, distance_km=9.0)
    ineligible = _candidate("ineligible", is_eligible=False, distance_km=1.0)
    ranked = rank_candidates(
        [ineligible, eligible], strategy=MatchStrategy.TIME_AWARE, urgency=Urgency.URGENT
    )
    assert [rc.ref for rc in ranked] == ["eligible", "ineligible"]
    assert "not yet eligible" in ranked[1].explanation


def test_eligibility_not_applicable_is_treated_as_best_case() -> None:
    """A HOSPITAL responder has `is_eligible=None` (doesn't apply) -- it
    should not be penalized as if it were "not yet eligible"."""
    hospital = _candidate(
        "hosp", role="HOSPITAL", is_eligible=None, distance_km=5.0, blood_type=None
    )
    ineligible_donor = _candidate("ineligible", is_eligible=False, distance_km=1.0)
    ranked = rank_candidates(
        [ineligible_donor, hospital], strategy=MatchStrategy.TIME_AWARE, urgency=Urgency.ROUTINE
    )
    assert ranked[0].ref == "hosp"


# ---- Location missing --------------------------------------------------------


def test_location_missing_sorts_after_known_distance_and_radius_is_unknown() -> None:
    known = _candidate("known", distance_km=50.0, alert_radius_km=25)
    unknown = _candidate("unknown", distance_km=None, alert_radius_km=25)
    for strategy in MatchStrategy:
        ranked = rank_candidates([unknown, known], strategy=strategy, urgency=Urgency.ROUTINE)
        refs = [rc.ref for rc in ranked]
        assert refs.index("known") < refs.index("unknown"), strategy
    solo = rank_candidates([unknown], strategy=MatchStrategy.NEAREST, urgency=Urgency.ROUTINE)
    assert solo[0].factors.distance_km is None
    assert solo[0].factors.within_alert_radius is None
    assert "km away" not in solo[0].explanation


def test_within_alert_radius_factor() -> None:
    inside = _candidate("inside", distance_km=5.0, alert_radius_km=10)
    outside = _candidate("outside", distance_km=50.0, alert_radius_km=10)
    ranked = rank_candidates(
        [inside, outside], strategy=MatchStrategy.NEAREST, urgency=Urgency.ROUTINE
    )
    by_ref = {rc.ref: rc for rc in ranked}
    assert by_ref["inside"].factors.within_alert_radius is True
    assert by_ref["outside"].factors.within_alert_radius is False
    assert "within the configured alert radius" in by_ref["inside"].explanation
    assert "outside the configured alert radius" in by_ref["outside"].explanation


# ---- Stale availability ------------------------------------------------------


def test_stale_availability_factor_computed_from_updated_at() -> None:
    fresh = _candidate("fresh", availability_updated_at=_NOW - timedelta(days=1))
    stale = _candidate(
        "stale", availability_updated_at=_NOW - timedelta(days=STALE_AVAILABILITY_DAYS + 5)
    )
    unknown = _candidate("unknown_ts", availability_updated_at=None)
    ranked = rank_candidates(
        [fresh, stale, unknown],
        strategy=MatchStrategy.NEAREST,
        urgency=Urgency.ROUTINE,
        now=_NOW,
    )
    by_ref = {rc.ref: rc for rc in ranked}
    assert by_ref["fresh"].factors.stale_availability is False
    assert by_ref["stale"].factors.stale_availability is True
    assert by_ref["unknown_ts"].factors.stale_availability is None


def test_event_driven_ranks_fresh_availability_ahead_of_stale() -> None:
    stale = _candidate(
        "stale",
        distance_km=1.0,
        availability_updated_at=_NOW - timedelta(days=STALE_AVAILABILITY_DAYS + 1),
    )
    fresh = _candidate("fresh", distance_km=100.0, availability_updated_at=_NOW - timedelta(days=1))
    ranked = rank_candidates(
        [stale, fresh],
        strategy=MatchStrategy.EVENT_DRIVEN,
        urgency=Urgency.CRITICAL,
        now=_NOW,
    )
    assert [rc.ref for rc in ranked] == ["fresh", "stale"]
    assert "availability last confirmed some time ago" in ranked[1].explanation


# ---- Urgent request ----------------------------------------------------------


@pytest.mark.parametrize("urgency", list(Urgency))
def test_factors_carry_the_requests_actual_urgency(urgency: Urgency) -> None:
    """Every candidate's factors report the real urgency of the request
    they were ranked for -- not a hardcoded default. No strategy in this
    engine varies the sort order by urgency tier (documented in the
    engine's module docstring); this test pins down what IS real: the
    urgency value itself is genuine and passed through untouched."""
    ranked = rank_candidates([_candidate("x")], strategy=MatchStrategy.NEAREST, urgency=urgency)
    assert ranked[0].factors.urgency == urgency


# ---- No unexplained magic score ---------------------------------------------


def test_ranked_candidate_has_no_score_field() -> None:
    ranked = rank_candidates(
        [_candidate("x")], strategy=MatchStrategy.NEAREST, urgency=Urgency.ROUTINE
    )
    assert not hasattr(ranked[0], "score")
    assert "score" not in ranked[0].factors.__dict__


def test_every_candidate_has_rank_and_nonempty_explanation() -> None:
    candidates = [
        _candidate("a", distance_km=1.0),
        _candidate("b", distance_km=2.0, has_accepted=True),
    ]
    for strategy in MatchStrategy:
        ranked = rank_candidates(candidates, strategy=strategy, urgency=Urgency.URGENT)
        ranks = [rc.rank for rc in ranked]
        assert ranks == sorted(ranks) == list(range(1, len(candidates) + 1))
        for rc in ranked:
            assert rc.explanation
            assert "compatible" in rc.explanation


# ---- Strategies are genuinely different algorithms --------------------------


def test_the_four_strategies_can_disagree_on_the_same_input() -> None:
    """Construct one candidate pool where NEAREST, TIME_AWARE/EVENT_DRIVEN,
    and TIME_RESILIENCE pick different #1s -- proof these are real,
    distinct algorithms and not the same ordering wearing four different
    names ("Results must be generated by actual algorithms", Phase 9
    RESEARCH MODE). TIME_AWARE and EVENT_DRIVEN happen to agree here
    (neither strategy's key includes verification, so with no ineligible
    candidate left in contention they both fall back to distance) --
    that's a real property of those two algorithms on this input, not a
    sign they're the same algorithm (see the unverified-vs-verified and
    stale-vs-fresh tests above, which show each considers different
    things TIME_RESILIENCE/EVENT_DRIVEN specifically do not share)."""
    near_ineligible_unverified_stale = _candidate(
        "near",
        distance_km=1.0,
        is_eligible=False,
        identity_verified=False,
        availability_updated_at=_NOW - timedelta(days=STALE_AVAILABILITY_DAYS + 10),
        is_available=True,
    )
    far_eligible_unverified_fresh = _candidate(
        "far_eligible",
        distance_km=100.0,
        is_eligible=True,
        identity_verified=False,
        availability_updated_at=_NOW - timedelta(days=1),
        is_available=True,
    )
    far_eligible_verified_fresh = _candidate(
        "far_verified",
        distance_km=120.0,
        is_eligible=True,
        identity_verified=True,
        availability_updated_at=_NOW - timedelta(days=1),
        is_available=True,
    )
    # Deliberately the worst distance of all: proves an unavailable
    # candidate never wins NEAREST/TIME_AWARE/TIME_RESILIENCE purely on
    # distance (those strategies don't check availability at all -- only
    # EVENT_DRIVEN excludes it ahead of distance).
    far_unavailable = _candidate(
        "far_unavailable",
        distance_km=200.0,
        is_eligible=True,
        identity_verified=True,
        availability_updated_at=_NOW - timedelta(days=1),
        is_available=False,
    )
    pool = [
        near_ineligible_unverified_stale,
        far_eligible_unverified_fresh,
        far_eligible_verified_fresh,
        far_unavailable,
    ]
    winners = {
        strategy: rank_candidates(pool, strategy=strategy, urgency=Urgency.CRITICAL, now=_NOW)[
            0
        ].ref
        for strategy in MatchStrategy
    }
    assert winners[MatchStrategy.NEAREST] == "near"
    assert winners[MatchStrategy.TIME_AWARE] == "far_eligible"
    assert winners[MatchStrategy.TIME_RESILIENCE] == "far_verified"
    assert winners[MatchStrategy.EVENT_DRIVEN] == "far_eligible"
    # Not every strategy agrees with every other -- the whole point.
    assert len(set(winners.values())) >= 3


# ---- API-level: incompatible donor never reaches the engine -----------------


def test_incompatible_donor_excluded_from_strategy_comparison() -> None:
    hosp, _ = auth_client("HOSPITAL")
    hosp.put("/hospitals/me", json={"name": "St Mercy", "city": "Metro"})
    patient, _ = auth_client("PATIENT")

    donor_ok, _ = auth_client("DONOR")
    donor_ok.put("/donors/me", json={"blood_type": "O_NEG", "city": "Metro"})
    donor_bad, _ = auth_client("DONOR")
    donor_bad.put(
        "/donors/me", json={"blood_type": "A_POS", "city": "Metro"}
    )  # incompatible with O_NEG need

    rid = patient.post(
        "/blood-requests",
        json={
            "blood_type": "O_NEG",
            "city": "Metro",
            "hospital_name": "St Mercy",
        },
    ).json()["id"]

    body = patient.get(f"/blood-requests/{rid}/match-strategies").json()
    assert len(body["strategies"]) == 4
    for result in body["strategies"]:
        blood_types = {c["blood_type"] for c in result["candidates"]}
        assert "A_POS" not in blood_types
        assert blood_types <= {"O_NEG"}
        for c in result["candidates"]:
            assert "score" not in c
            assert "score" not in c["factors"]
            assert c["explanation"]


def test_emergency_match_strategies_excludes_incompatible_and_exposes_factors() -> None:
    patient, _ = auth_client("PATIENT")
    donor_ok, _ = auth_client("DONOR")
    donor_ok.put("/donors/me", json={"blood_type": "O_NEG", "city": "Metro"})
    donor_bad, _ = auth_client("DONOR")
    donor_bad.put("/donors/me", json={"blood_type": "A_POS", "city": "Metro"})

    eid = patient.post(
        "/emergencies", json={"blood_type": "O_NEG", "location_text": "Metro central"}
    ).json()["id"]

    body = patient.get(f"/emergencies/{eid}/match-strategies").json()
    assert len(body["strategies"]) == 4
    for result in body["strategies"]:
        blood_types = {c["blood_type"] for c in result["candidates"] if c["blood_type"]}
        assert "A_POS" not in blood_types
        for c in result["candidates"]:
            assert "score" not in c
            assert "score" not in c["factors"]
            assert c["explanation"]


def test_match_candidates_default_strategy_is_nearest_and_exposes_factors() -> None:
    hosp, _ = auth_client("HOSPITAL")
    hosp.put("/hospitals/me", json={"name": "General", "city": "Metro"})
    patient, _ = auth_client("PATIENT")
    donor, _ = auth_client("DONOR")
    donor.put("/donors/me", json={"blood_type": "O_NEG", "city": "Metro"})

    rid = patient.post(
        "/blood-requests",
        json={
            "blood_type": "O_NEG",
            "city": "Metro",
            "hospital_name": "General",
        },
    ).json()["id"]
    body = patient.get(f"/blood-requests/{rid}/match-candidates").json()
    assert body["strategy"] == "NEAREST"
    assert len(body["candidates"]) == 1
    c = body["candidates"][0]
    assert c["rank"] == 1
    assert c["factors"]["compatible"] is True
    assert "score" not in c
    assert "score" not in c["factors"]
    assert c["explanation"]

    # An explicit alternate strategy is honored via the query param.
    body2 = patient.get(f"/blood-requests/{rid}/match-candidates?strategy=EVENT_DRIVEN").json()
    assert body2["strategy"] == "EVENT_DRIVEN"
