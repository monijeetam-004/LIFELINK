"""Unified Donor Priority Score (2026-10-31) -- model UNIFIED_PRIORITY.

Pure model tests (formula, missing data, double counting, ties, config
validation, explanation reconciliation, versioning) and API tests on real
PostgreSQL (eligibility before scoring, the four unchanged strategies as
selectable orderings, persistence of strategy/model on contacts, roles)."""
from __future__ import annotations

import math
import random
import threading
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core import matching_engine as me
from app.core import unified_priority as up
from app.core.config import Settings, settings
from app.db.session import SessionLocal
from app.models.donor_contact import DonorContactAttempt
from app.models.enums import ShortlistSubject, Urgency
from app.services import donor_shortlist_service as svc
from tests.helpers import auth_client
from tests.test_donor_shortlist import _contact, _donor, _hospital, _request, _sl

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
CFG = up.PriorityConfig(weights=up.validate_weights(settings.priority_weights_raw()))


def _x(**kw) -> up.PriorityInputs:
    base = {
        "has_accepted_this_subject": False, "availability_confirmed_at": None, "invited": 0,
        "answered": 0,
        "accepted": 0, "last_response_at": None, "last_donation_recorded_at": None,
        "distance_basis": "MEASURED", "distance_km": 10.0, "distance_limit_km": 50.0,
        "location_age_days": 5, "identity_verified": False, "verified_donations": 0}
    base.update(kw)
    return up.PriorityInputs(**base)


def _f(score: up.PriorityScore, name: str) -> up.FactorResult:
    return next(f for f in score.factors if f.name == name)


# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #
def test_controlled_fixture_selects_the_expected_candidate():
    """B/C: explicit recent availability, a good answer record and a short
    distance beat a closer donor with unknown availability and no history."""
    strong = up.compute(_x(availability_confirmed_at=NOW - timedelta(days=1), invited=6,
                           answered=5, accepted=4, last_response_at=NOW - timedelta(days=3),
                           distance_km=12.0, identity_verified=True, verified_donations=2),
                        CFG, now=NOW)
    unknown = up.compute(_x(distance_km=3.0), CFG, now=NOW)
    assert strong.score > unknown.score
    assert strong.availability_status is up.AvailabilityStatus.CONFIRMED_RECENTLY
    assert 0 <= unknown.score <= 100 and 0 <= strong.score <= 100


def test_unknown_availability_is_missing_never_confirmed():
    """D: never confirmed -> MISSING, contributes 0, status UNCONFIRMED."""
    s = up.compute(_x(), CFG, now=NOW)
    av = _f(s, "availability")
    assert av.value is None and av.status == "MISSING" and av.contribution == 0
    assert s.availability_status is up.AvailabilityStatus.UNCONFIRMED
    stale = up.compute(_x(availability_confirmed_at=NOW - timedelta(days=45)), CFG, now=NOW)
    assert stale.availability_status is up.AvailabilityStatus.CONFIRMED_STALE
    assert _f(stale, "availability").value == pytest.approx(0.3)
    accepted = up.compute(_x(has_accepted_this_subject=True), CFG, now=NOW)
    assert accepted.availability_status is up.AvailabilityStatus.ACCEPTED_THIS_REQUEST


def test_missing_data_policy_neutral_baseline_and_coverage():
    """H: missing factors get the neutral 0.5 as a separate baseline line,
    coverage reports the observed share, and too little evidence makes the
    score unavailable."""
    s = up.compute(_x(), CFG, now=NOW)
    missing = [f for f in s.factors if f.value is None]
    assert {f.name for f in missing} == {"availability", "response", "acceptance", "activity"}
    w_missing = sum(CFG.weights[f.name] for f in missing)
    assert s.missing_baseline == pytest.approx(100 * 0.5 * w_missing, abs=0.01)
    assert s.coverage == pytest.approx(1 - w_missing, abs=1e-3)
    thin = up.compute(_x(distance_basis="UNKNOWN", distance_km=None, location_age_days=None),
                      CFG, now=NOW)
    assert thin.coverage == pytest.approx(CFG.weights["verification"], abs=1e-3)
    assert thin.score_available is False and thin.evidence_level == "INSUFFICIENT"
    # little evidence drifts to the middle -- never "highly reliable"
    one_good = up.compute(_x(distance_basis="UNKNOWN", distance_km=None, location_age_days=None,
                             identity_verified=True, verified_donations=3), CFG, now=NOW)
    assert one_good.score < 60


def test_stale_location_is_surfaced_in_freshness_only():
    """I/J: a stale location lowers freshness and says so; distance is the
    same value as for a fresh location (no double penalty)."""
    fresh = up.compute(_x(location_age_days=3), CFG, now=NOW)
    stale = up.compute(_x(location_age_days=400), CFG, now=NOW)
    assert _f(stale, "freshness").value == pytest.approx(0.2)
    assert "(stale)" in _f(stale, "freshness").detail
    assert _f(fresh, "distance").value == _f(stale, "distance").value


def test_no_double_counting_between_correlated_factors():
    """J: an availability confirmation is not also 'activity'; accepting this
    request counts once; acceptance is conditional on answering, so answering
    is not rewarded twice; travel time is not a factor of its own."""
    confirmed = up.compute(_x(availability_confirmed_at=NOW - timedelta(days=1)), CFG, now=NOW)
    assert _f(confirmed, "activity").value is None
    acc = up.compute(_x(has_accepted_this_subject=True, location_age_days=None), CFG, now=NOW)
    assert [f.name for f in acc.factors if f.value == 1.0] == ["availability"]
    # Same acceptance RATE with more answers does not raise acceptance.
    a1 = _f(up.compute(_x(invited=10, answered=4, accepted=2), CFG, now=NOW), "acceptance")
    a2 = _f(up.compute(_x(invited=10, answered=8, accepted=4), CFG, now=NOW), "acceptance")
    assert a1.value == pytest.approx(a2.value, abs=0.05)
    assert "travel" not in up.FACTORS and len(set(up.FACTORS)) == len(up.FACTORS)


def test_explanations_reconcile_with_the_score():
    """M: factor contributions plus the missing-data baseline equal the score."""
    rng = random.Random(20261031)
    for _ in range(300):
        inv = rng.randint(0, 12)
        ans = rng.randint(0, inv)
        x = _x(
            has_accepted_this_subject=rng.random() < 0.1,
            availability_confirmed_at=rng.choice([None, NOW - timedelta(days=rng.randint(0, 90))]),
            invited=inv, answered=ans, accepted=rng.randint(0, ans),
            last_response_at=rng.choice([None, NOW - timedelta(days=rng.randint(0, 200))]),
            distance_basis=rng.choice(["MEASURED", "SAME_CITY", "UNKNOWN"]),
            distance_km=rng.uniform(0, 50), location_age_days=rng.choice([None, 2, 60, 300]),
            identity_verified=rng.random() < 0.5, verified_donations=rng.randint(0, 5))
        if x.distance_basis != "MEASURED":
            x = replace(x, distance_km=None)
        s = up.compute(x, CFG, now=NOW)
        assert sum(f.contribution for f in s.factors) + s.missing_baseline == pytest.approx(
            s.score, abs=0.05)
        assert all(f.contribution == 0 for f in s.factors if f.value is None)
        assert 0 <= s.score <= 100


def test_invalid_configuration_is_rejected():
    """L: negative, non-finite, unknown/missing factors and a zero sum."""
    good = settings.priority_weights_raw()
    for bad in ({**good, "distance": -0.1}, {**good, "distance": math.nan},
                {**good, "distance": math.inf}, dict.fromkeys(good, 0.0),
                {k: v for k, v in good.items() if k != "distance"}, {**good, "login": 0.1}):
        with pytest.raises(ValueError):
            up.validate_weights(bad)
    with pytest.raises(ValueError):
        Settings(priority_weight_distance=float("nan"))
    with pytest.raises(ValueError):
        Settings(**{f"priority_weight_{k}": 0.0 for k in good})


def test_model_version_and_fingerprint_trace_weight_changes():
    """N: the model tag carries version and a config fingerprint that
    changes with the weights (and only then)."""
    same = up.PriorityConfig(weights=dict(CFG.weights))
    other = up.PriorityConfig(weights=up.validate_weights({**settings.priority_weights_raw(),
                                                           "distance": 0.4}))
    assert CFG.model_tag().startswith(f"{up.MODEL_ID}/{up.MODEL_VERSION}#")
    assert same.fingerprint() == CFG.fingerprint() != other.fingerprint()
    snap = up.compute(_x(), CFG, now=NOW).snapshot()
    assert snap["model"] == CFG.model_tag() and set(snap["factors"]) == set(up.FACTORS)


def _cand(i: int, score_inputs: up.PriorityInputs, km=None):
    import uuid as _uuid

    from app.core import distance_policy as dp
    a = dp.DistanceAssessment(dp.DistanceBasis.MEASURED if km is not None else
                              dp.DistanceBasis.SAME_CITY, km, True, None, 1, False)
    return svc.Candidate(
        donor_user_id=_uuid.UUID(int=i), ref=f"{i:08d}", name=None, blood_type=None,
        synthetic=False, distance=a, travel_minutes=None,
        priority=up.compute(score_inputs, CFG, now=NOW), has_accepted=False,
        last_donation_date=None, identity_verified=False, profile_updated_at=NOW)


def test_equal_scores_break_ties_deterministically():
    """K: equal score -> shorter known distance -> donor id."""
    same = _x(distance_basis="SAME_CITY", distance_km=None, location_age_days=None)
    cands = [_cand(3, same), _cand(1, same), _cand(2, same)]
    order = svc.order_candidates(cands, up.RankingStrategy.UNIFIED_PRIORITY, Urgency.URGENT, NOW)
    assert [c.ref for c in order] == ["00000001", "00000002", "00000003"]
    assert [c.ref for c in svc.order_candidates(list(reversed(cands)),
                                                up.RankingStrategy.UNIFIED_PRIORITY,
                                                Urgency.URGENT, NOW)] == [c.ref for c in order]


def test_unscored_candidates_follow_the_scored_ones():
    """H: a candidate whose score is unavailable is never ranked above a
    scored one on a made-up number; it falls back to TIME_RESILIENCE order."""
    scored = _cand(9, _x(), km=40.0)
    thin = _cand(1, _x(distance_basis="UNKNOWN", distance_km=None, location_age_days=None,
                       identity_verified=True, verified_donations=3))
    assert thin.priority.score_available is False
    order = svc.order_candidates([thin, scored], up.RankingStrategy.UNIFIED_PRIORITY,
                                 Urgency.URGENT, NOW)
    assert [c.ref for c in order] == [scored.ref, thin.ref]


def test_the_four_strategies_order_exactly_as_the_matching_engine():
    """A: selecting an original strategy reproduces matching_engine's order
    (same keys, same tie-breaks) on the eligible candidates."""
    rng = random.Random(7)
    cands = []
    for i in range(1, 40):
        c = _cand(i, _x(), km=rng.choice([None, rng.uniform(0, 50)]))
        cands.append(replace(c, has_accepted=rng.random() < 0.2,
                             identity_verified=rng.random() < 0.5,
                             last_donation_date=rng.choice(
                                 [None, date(2025, 1, rng.randint(1, 28))]),
                             profile_updated_at=NOW - timedelta(days=rng.randint(0, 90))))
    for strat in me.MatchStrategy:
        inputs = [me.MatchCandidateInput(
            ref=str(c.donor_user_id), name=None, blood_type=None, role="DONOR",
            is_available=True, has_accepted=c.has_accepted, distance_km=c.distance.distance_km,
            last_donation_date=c.last_donation_date, identity_verified=c.identity_verified,
            is_eligible=True, alert_radius_km=None, availability_updated_at=c.profile_updated_at,
            city=None) for c in cands]
        expected = [r.ref for r in me.rank_candidates(inputs, strategy=strat, now=NOW,
                                                      today=NOW.date())]
        got = [str(c.donor_user_id) for c in svc.order_candidates(
            cands, up.RankingStrategy(strat.value), Urgency.URGENT, NOW)]
        assert got == expected, strat


def test_matching_engine_strategy_keys_are_unchanged():
    """A (regression): the four original sort keys still produce their
    documented orders on a fixed fixture."""
    def c(ref, **kw):
        base = {"ref": ref, "name": None, "blood_type": None, "role": "DONOR", "is_available": True,
                    "has_accepted": False, "distance_km": None, "last_donation_date": None,
                    "identity_verified": None, "is_eligible": True, "alert_radius_km": None,
                    "availability_updated_at": NOW, "city": None}
        base.update(kw)
        return me.MatchCandidateInput(**base)

    pool = [c("a", distance_km=5.0, is_eligible=False),
            c("b", distance_km=9.0, identity_verified=True),
            c("c", distance_km=2.0, availability_updated_at=NOW - timedelta(days=60)),
            c("d", distance_km=20.0, has_accepted=True),
            c("e", is_available=False, distance_km=1.0)]
    order = {s: [r.ref for r in me.rank_candidates(pool, strategy=s, now=NOW, today=NOW.date())]
             for s in me.MatchStrategy}
    assert order[me.MatchStrategy.NEAREST] == ["d", "e", "c", "a", "b"]
    assert order[me.MatchStrategy.TIME_AWARE] == ["d", "e", "c", "b", "a"]
    assert order[me.MatchStrategy.TIME_RESILIENCE] == ["d", "b", "e", "c", "a"]
    assert order[me.MatchStrategy.EVENT_DRIVEN] == ["d", "a", "b", "c", "e"]


# --------------------------------------------------------------------------- #
# API (real PostgreSQL)
# --------------------------------------------------------------------------- #
def test_strategy_is_selectable_validated_and_recorded_on_contacts():
    h = _hospital()
    for i in range(4):
        _donor(2 + i * 3)
    rid = _request(h, units=1)
    for strat in up.RankingStrategy:
        r = h.get(f"/blood-requests/{rid}/shortlist", params={"strategy": strat.value})
        assert r.status_code == 200, r.text
        assert r.json()["strategy"] == strat.value
        assert r.json()["scoring_model"].startswith("UNIFIED_PRIORITY/")
    assert h.get(f"/blood-requests/{rid}/shortlist",
                 params={"strategy": "MAGIC_SCORE"}).status_code == 422
    s = h.post(f"/blood-requests/{rid}/shortlist/contact-next",
               params={"strategy": "NEAREST"}).json()
    assert all(c["ranking_strategy"] == "NEAREST" for c in s["contacts"])
    assert all(c["scoring_model"].startswith("UNIFIED_PRIORITY/") for c in s["contacts"])
    assert s["strategy"] == "NEAREST" and _sl(h, rid)["strategy"] == "NEAREST"
    with SessionLocal() as db:
        rows = db.scalars(select(DonorContactAttempt).where(
            DonorContactAttempt.blood_request_id == rid)).all()
        assert rows and all(set(r.score_snapshot["factors"]) == set(up.FACTORS) for r in rows)


def test_ineligible_donor_is_excluded_whatever_its_evidence():
    """F/G: a donor inside the donation interval, or beyond 50 km, is never a
    candidate and is never alerted, however good the rest of their record."""
    h = _hospital()
    resting, _ = _donor(1, identity_verified=True,
                        last_donation_date=(date.today() - timedelta(days=10)).isoformat())
    far, _ = _donor(80)
    ok, _ = _donor(4)
    rid = _request(h, units=1, city="Metro")
    s = _sl(h, rid)
    assert s["counts"]["eligible_candidates"] == 1
    assert s["excluded"].get("donation_interval_not_elapsed") == 1
    assert s["excluded"].get("beyond_distance_limit") == 1
    for c in (resting, far):
        assert not [n for n in c.get("/notifications").json()
                    if (n.get("data") or {}).get("request_id") == rid]


def test_high_scoring_non_responder_is_never_counted_as_confirmed():
    """E/P: the top-ranked donor times out; the shortage stays visible."""
    h = _hospital()
    _donor(1)
    _donor(2)
    rid = _request(h, units=1, urgency="CRITICAL")
    s = _contact(h, rid)
    top = min(s["contacts"], key=lambda c: c["rank_at_contact"])
    with SessionLocal() as db:
        db.query(DonorContactAttempt).filter(DonorContactAttempt.id == top["id"]).update(
            {"timeout_at": datetime.now(UTC) - timedelta(minutes=1)})
        db.commit()
    s = _sl(h, rid)
    row = next(c for c in s["contacts"] if c["id"] == top["id"])
    assert row["status"] == "TIMED_OUT"
    assert s["counts"]["confirmed_for_coordination"] == 0 and s["remaining_unmet"] == 1


def test_explanation_detail_depends_on_role_and_policy_hides_weights():
    """T: facility users see factor detail; the requesting patient sees a
    minimal summary with no counts; scoring internals only for facility/admin."""
    patient, _ = auth_client("PATIENT")
    _donor(3)
    r = patient.post("/blood-requests", json={"blood_type": "O_NEG", "units_needed": 1,
                                              "city": "Metro"})
    rid = r.json()["id"]
    s = patient.get(f"/blood-requests/{rid}/shortlist").json()
    assert s["explanation_level"] == "SUMMARY"
    for cand in s["next_to_contact"]:
        # no counts, ages or distances -- other people's histories
        assert not any(ch.isdigit() for f in cand["priority"]["factors"] for ch in f["detail"])
    assert patient.get("/shortlist/policy").json()["weights"] is None
    admin, _ = auth_client("ADMIN")
    s = admin.get(f"/blood-requests/{rid}/shortlist").json()
    assert s["explanation_level"] == "FULL"
    pol = admin.get("/shortlist/policy").json()
    assert pol["weights"]["availability"] == pytest.approx(0.25)
    assert pol["score_is_probability"] is False


def test_concurrent_contact_requests_never_duplicate_contacts():
    """O: two simultaneous contact-next calls create one bounded set."""
    h = _hospital()
    for i in range(8):
        _donor(1 + i)
    rid = _request(h, units=1)
    results = []

    def go():
        results.append(h.post(f"/blood-requests/{rid}/shortlist/contact-next").status_code)

    threads = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert all(code == 200 for code in results)
    with SessionLocal() as db:
        rows = db.scalars(select(DonorContactAttempt).where(
            DonorContactAttempt.blood_request_id == rid)).all()
    donors = [r.donor_user_id for r in rows]
    assert len(donors) == len(set(donors))
    assert len(rows) <= settings.shortlist_contact_multiplier


def test_service_scores_every_eligible_candidate_with_the_versioned_model():
    h = _hospital()
    _donor(3)
    rid = _request(h, units=1)
    with SessionLocal() as db:
        subject = svc.load_subject(db, ShortlistSubject.BLOOD_REQUEST, rid)
        ev = svc.evaluate(db, subject)
        assert ev.strategy is up.RankingStrategy.UNIFIED_PRIORITY
        assert ev.model_tag == up.config_from_settings().model_tag()
        assert all(c.priority.model_tag == ev.model_tag for c in ev.candidates)


def test_answering_this_request_is_not_counted_again_as_history():
    """J (service level): accepting the CURRENT request moves `availability`
    to ACCEPTED_THIS_REQUEST but must not also raise `response`/`acceptance`
    -- those use other requests only."""
    h = _hospital()
    d, _ = _donor(3)
    old = _request(h, units=1, note="earlier request")
    assert d.post(f"/blood-requests/{old}/respond", json={"action": "DECLINED"}).status_code == 200
    rid = _request(h, units=1, note="current request")
    me_id = d.get("/auth/me").json()["id"]

    def inputs():
        with SessionLocal() as db:
            sub = svc.load_subject(db, ShortlistSubject.BLOOD_REQUEST, __import__("uuid").UUID(rid))
            ev = svc.evaluate(db, sub)
            c = next(c for c in ev.candidates if str(c.donor_user_id) == me_id)
            return c.inputs, c.priority

    before, p_before = inputs()
    assert (before.invited, before.answered, before.accepted) == (1, 1, 0)  # the earlier one
    assert d.post(f"/blood-requests/{rid}/respond", json={"action": "ACCEPTED"}).status_code == 200
    after, p_after = inputs()
    assert (after.invited, after.answered, after.accepted) == (1, 1, 0)
    assert after.has_accepted_this_subject
    assert p_after.availability_status is up.AvailabilityStatus.ACCEPTED_THIS_REQUEST
    assert _f(p_after, "response").value == _f(p_before, "response").value
    assert _f(p_after, "acceptance").value == _f(p_before, "acceptance").value


def test_summary_viewers_get_no_weights_history_values_precise_distance_or_reasons():
    """Release gate C (2026-11-03): enforced server-side. A patient (or
    family member) managing a request sees the score, evidence level and
    plain-language factor summaries -- but no weights (restricted
    configuration), no factor values (other donors' history), no full-
    precision distance and no donor-specific re-check reason. A hospital or
    admin user sees the detail, with distances to 0.1 km."""
    patient, _ = auth_client("PATIENT")
    d, _ = _donor(3.456)
    _donor(7.891)
    rid = patient.post("/blood-requests", json={
        "blood_type": "O_NEG", "units_needed": 1, "city": "Metro",
        "latitude": 12.0, "longitude": 77.0}).json()["id"]
    s = patient.get(f"/blood-requests/{rid}/shortlist").json()
    assert s["explanation_level"] == "SUMMARY" and s["next_to_contact"]
    for cand in s["next_to_contact"]:
        assert cand["components"] == {}
        for f in cand["priority"]["factors"]:
            assert f["weight"] is None and f["value"] is None and f["contribution"] is None
        # A patient's blood request has no coordinates, so there is no measured
        # distance here; measured distances by role are asserted on an SOS in
        # test_shortlist_privacy_round2.py (privacy round 2).
        assert cand["distance_km"] is None and cand["travel_minutes_estimate"] is None
        assert not any(ch.isdigit() for e in cand["explanation"][1:] for ch in e)
    # a failed re-check: the patient sees only that it did not pass
    _contact(patient, rid)
    assert d.post(f"/blood-requests/{rid}/respond", json={"action": "ACCEPTED"}).status_code == 200
    d.put("/donors/me", json={"last_donation_date": date.today().isoformat()})
    acc = next(c for c in _sl(patient, rid)["contacts"] if c["status"] == "ACCEPTED")
    s = patient.post(f"/blood-requests/{rid}/shortlist/contacts/{acc['id']}/confirm").json()
    row = next(c for c in s["contacts"] if c["id"] == acc["id"])
    assert row["status"] == "ELIGIBILITY_RECHECK_REQUIRED"
    assert row["status_reason"] == "the eligibility re-check did not pass"
    admin, _ = auth_client("ADMIN")
    s = admin.get(f"/blood-requests/{rid}/shortlist").json()
    assert s["explanation_level"] == "FULL"
    row = next(c for c in s["contacts"] if c["id"] == acc["id"])
    assert "interval" in row["status_reason"]
    for cand in s["next_to_contact"]:
        assert set(cand["components"]) == set(up.FACTORS)
        assert all(f["weight"] is not None for f in cand["priority"]["factors"])
        if cand["distance_km"] is not None:
            assert cand["distance_km"] == round(cand["distance_km"], 1)


def test_score_range_coverage_and_reconciliation_hold_for_random_inputs():
    """Release gate E: on 3,000 seeded random inputs (including empty
    histories, missing distances, stale or absent locations) the score stays
    in 0..100, coverage in 0..1, the explanation lines add up to the score,
    and "unscored" happens exactly below the minimum coverage."""
    rng = random.Random(20261103)
    for _ in range(3000):
        invited = rng.choice([0, 0, 1, 2, 5, 40])
        answered = rng.randint(0, invited)
        x = _x(
            has_accepted_this_subject=rng.random() < 0.1,
            availability_confirmed_at=rng.choice([None, NOW - timedelta(days=rng.randint(0, 400))]),
            invited=invited, answered=answered, accepted=rng.randint(0, answered),
            last_response_at=rng.choice([None, NOW - timedelta(days=rng.randint(0, 900))]),
            last_donation_recorded_at=rng.choice([None, NOW - timedelta(days=rng.randint(0, 900))]),
            distance_basis=rng.choice(["MEASURED", "SAME_CITY", "UNKNOWN"]),
            distance_km=rng.choice([None, rng.uniform(0, 50)]),
            location_age_days=rng.choice([None, rng.randint(0, 800)]),
            identity_verified=rng.random() < 0.5, verified_donations=rng.randint(0, 6))
        p = up.compute(x, CFG, now=NOW)
        assert 0.0 <= p.score <= 100.0
        assert 0.0 <= p.coverage <= 1.0
        lines = sum(f.contribution for f in p.factors) + p.missing_baseline
        assert abs(lines - p.score) <= 0.05
        assert p.score_available == (p.coverage >= CFG.min_coverage)
        for f in p.factors:
            assert (f.value is None) == (f.status == "MISSING")
            assert f.value is None or 0.0 <= f.value <= 1.0
        if x.availability_confirmed_at is None and not x.has_accepted_this_subject:
            assert p.availability_status is up.AvailabilityStatus.UNCONFIRMED


def test_ranking_is_read_only_and_never_changes_any_state():
    """Release gate E: computing and ordering the shortlist (every mode)
    writes nothing -- no contact, notification, response or status change."""
    from sqlalchemy import func as sa_func

    from app.models.blood_request import BloodRequest, DonorResponse
    from app.models.notification import Notification

    h = _hospital()
    for km in (1, 2, 3):
        _donor(km)
    rid = _request(h, units=2)

    def snapshot(db):
        return (db.scalar(select(sa_func.count()).select_from(DonorContactAttempt)),
                db.scalar(select(sa_func.count()).select_from(Notification)),
                db.scalar(select(sa_func.count()).select_from(DonorResponse)),
                db.get(BloodRequest, __import__("uuid").UUID(rid)).status)

    with SessionLocal() as db:
        before = snapshot(db)
        sub = svc.load_subject(db, ShortlistSubject.BLOOD_REQUEST, __import__("uuid").UUID(rid))
        for mode in up.RankingStrategy:
            ev = svc.evaluate(db, sub, strategy=mode)
            assert len(ev.candidates) == 3
        assert not db.new and not db.dirty and not db.deleted
        db.rollback()
    with SessionLocal() as db:
        assert snapshot(db) == before
