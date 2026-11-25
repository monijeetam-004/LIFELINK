"""Confidence-based donor shortlist (2026-10-31) -- real PostgreSQL, real API.

Covers: hard eligibility before ranking, the score's handling of missing /
small-sample data, bounded batched contact without duplicates, the response
lifecycle (accept / decline / not-now / withdraw / timeout), coordinator
confirmation with an eligibility re-check, the honest counts and states for
0 / 1 / 2 / 3+ donors, access control and privacy, the shortlist alert mode,
and the DEMO_MODE simulator (synthetic donors only)."""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.donor_contact import DonorContactAttempt
from app.models.notification import Notification
from app.services import donor_shortlist_service as svc
from tests.helpers import auth_client

KM_PER_DEG_LAT = 6371.0 * 3.141592653589793 / 180
LAT0, LON0 = 12.0, 77.0


def _donor(km: float | None = 5.0, *, blood="O_NEG", city="Metro", email=None, name=None,
           **fields):
    c, em = auth_client("DONOR", email=email, name=name)
    body = {"blood_type": blood, "city": city, **fields}
    if km is not None:
        body.update(latitude=LAT0 + km / KM_PER_DEG_LAT, longitude=LON0, share_location=True)
    r = c.put("/donors/me", json=body)
    assert r.status_code == 200, r.text
    return c, em


def _hospital():
    h, _ = auth_client("HOSPITAL", name="Metro General")
    assert h.put("/hospitals/me", json={"name": "Metro General", "city": "Metro",
                                        "latitude": LAT0, "longitude": LON0}).status_code == 200
    return h


def _request(h, units=1, urgency="URGENT", **extra) -> str:
    r = h.post("/blood-requests", json={"blood_type": "O_NEG", "units_needed": units,
                                        "urgency": urgency, **extra})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _sl(c, rid):
    r = c.get(f"/blood-requests/{rid}/shortlist")
    assert r.status_code == 200, r.text
    return r.json()


def _contact(c, rid):
    r = c.post(f"/blood-requests/{rid}/shortlist/contact-next")
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------------------- #
# Pure scoring
# --------------------------------------------------------------------------- #
def test_no_history_is_neutral_and_small_samples_are_shrunk():
    """Ported 2026-10-31 from the superseded CONFIDENCE_SHORTLIST helpers to
    the Unified Donor Priority model (same intent): no history is MISSING
    (unknown, never 'unreliable'); a small sample is shrunk towards 0.5."""
    from app.core import unified_priority as up

    cfg = up.config_from_settings()

    def resp(invited, answered):
        x = up.PriorityInputs(
            has_accepted_this_subject=False, availability_confirmed_at=None, invited=invited,
            answered=answered, accepted=0, last_response_at=None, last_donation_recorded_at=None,
            distance_basis="MEASURED", distance_km=5.0, distance_limit_km=50,
            location_age_days=1, identity_verified=False, verified_donations=0)
        return next(f for f in up.compute(x, cfg).factors if f.name == "response").value

    assert resp(0, 0) is None
    one_yes, one_no, many_no = resp(1, 1), resp(1, 0), resp(20, 0)
    assert 0.5 < one_yes < 0.7 and 0.3 < one_no < 0.5  # a single answer moves it a little
    assert many_no < 0.1


def test_activity_and_weights():
    from app.core import unified_priority as up

    cfg = up.config_from_settings()
    now = datetime.now(UTC)

    def act(last):
        x = up.PriorityInputs(
            has_accepted_this_subject=False, availability_confirmed_at=None, invited=0,
            answered=0, accepted=0, last_response_at=last, last_donation_recorded_at=None,
            distance_basis="MEASURED", distance_km=5.0, distance_limit_km=50,
            location_age_days=1, identity_verified=False, verified_donations=0)
        return next(f for f in up.compute(x, cfg, now=now).factors if f.name == "activity").value

    assert act(None) is None  # no activity on file is missing, not "inactive"
    assert act(now - timedelta(days=2)) == 1.0
    assert act(now - timedelta(days=90)) == 0.1
    w = svc.weights()
    assert abs(sum(w.values()) - 1.0) < 1e-9 and w["availability"] == pytest.approx(0.25)


# --------------------------------------------------------------------------- #
# Hard eligibility comes first
# --------------------------------------------------------------------------- #
def test_hard_filters_exclude_and_a_high_score_never_overrides_them():
    h = _hospital()
    good, _ = _donor(3)
    _donor(3, blood="A_POS")  # incompatible: not even in the pool
    unavailable, _ = _donor(3)
    unavailable.put("/donors/me/availability", json={"state": "UNAVAILABLE"})
    recent, _ = _donor(1, last_donation_date=(date.today() - timedelta(days=10)).isoformat())
    _donor(80)  # beyond the 50 km limit
    _donor(None, city="Elsewhere")  # location unknown
    rid = _request(h)
    s = _sl(h, rid)
    assert s["counts"]["eligible_candidates"] == 1
    ex = s["excluded"]
    assert ex["unavailable"] == 1 and ex["donation_interval_not_elapsed"] == 1
    assert ex["beyond_distance_limit"] == 1 and ex["location_unknown"] == 1
    # The recently donated donor is the CLOSEST and would score highest on
    # distance -- they are still not a candidate.
    assert [c["distance_km"] for c in s["next_to_contact"]] == [3.0]


def test_committed_elsewhere_and_travel_beyond_the_deadline_are_excluded():
    h = _hospital()
    busy, _ = _donor(2)
    other = _request(h, note="first")
    assert busy.post(f"/blood-requests/{other}/respond",
                     json={"action": "ACCEPTED"}).status_code == 200
    _donor(45)  # 60 min at the assumed 45 km/h
    p, _ = auth_client("PATIENT")
    eid = p.post("/emergencies", json={
        "blood_type": "O_NEG", "latitude": LAT0, "longitude": LON0, "city": "Metro",
        "deadline": (datetime.now(UTC) + timedelta(minutes=30)).isoformat()}).json()["id"]
    s = p.get(f"/emergencies/{eid}/shortlist").json()
    assert s["excluded"].get("committed_elsewhere") == 1
    assert s["excluded"].get("travel_exceeds_deadline") == 1


# --------------------------------------------------------------------------- #
# Contact, responses, confirmation
# --------------------------------------------------------------------------- #
def test_contact_is_bounded_deduplicated_and_tracks_open_alert_recipients():
    h = _hospital()
    donors = [_donor(1 + i) for i in range(8)]
    rid = _request(h, units=2)  # broadcast mode: all 8 got the open alert
    s = _contact(h, rid)
    # 2 donors needed x multiplier 2 = 4 outstanding invitations at most
    assert s["counts"]["contacted"] == 4 and s["counts"]["awaiting_response"] == 4
    assert all(c["status_reason"] == "already received the open alert" for c in s["contacts"])
    db = SessionLocal()
    try:
        # no second notification for donors who already had the open alert
        assert db.scalar(select(Notification.id).where(
            Notification.type == "blood_request.shortlist_invite")) is None
    finally:
        db.close()
    again = _contact(h, rid)  # nothing more until someone answers or times out
    assert again["counts"]["contacted"] == 4
    assert len({c["donor_ref"] for c in again["contacts"]}) == 4
    assert donors


def test_response_lifecycle_escalation_and_confirmation():
    h = _hospital()
    clients = [_donor(1 + i, name=f"Donor {i}") for i in range(6)]
    rid = _request(h, units=1)
    s = _contact(h, rid)
    assert s["counts"]["contacted"] == 2
    by_ref = {str(uuid.UUID(c.get("/auth/me").json()["id"]))[:8]: c for c, _ in clients}
    first, second = s["contacts"][0], s["contacts"][1]
    # first declines -> escalation contacts the next ranked donor
    by_ref[first["donor_ref"]].post(f"/blood-requests/{rid}/respond", json={"action": "DECLINED"})
    s = _sl(h, rid)
    assert s["counts"]["declined"] == 1 and s["counts"]["contacted"] == 3
    # second accepts -> ACCEPTED, not yet confirmed
    by_ref[second["donor_ref"]].post(f"/blood-requests/{rid}/respond", json={"action": "ACCEPTED"})
    s = _sl(h, rid)
    acc = next(c for c in s["contacts"] if c["donor_ref"] == second["donor_ref"])
    assert acc["status"] == "ACCEPTED" and acc["eligibility_rechecked_at"]
    assert s["counts"]["confirmed_for_coordination"] == 0 and s["state"] == "PARTIAL"
    # only the request's managers can confirm
    other, _ = auth_client("PATIENT")
    assert other.post(f"/blood-requests/{rid}/shortlist/contacts/{acc['id']}/confirm"
                      ).status_code == 404
    s = h.post(f"/blood-requests/{rid}/shortlist/contacts/{acc['id']}/confirm").json()
    assert s["counts"]["confirmed_for_coordination"] == 1
    assert s["state"] == "SATISFIED" and s["remaining_unmet"] == 0
    # confirming a non-accepted contact is refused
    pend = next(c for c in s["contacts"] if c["status"] == "AWAITING_RESPONSE")
    assert h.post(f"/blood-requests/{rid}/shortlist/contacts/{pend['id']}/confirm"
                  ).status_code == 409


def test_withdrawal_not_now_and_recheck_failure():
    h = _hospital()
    a, _ = _donor(1)
    b, _ = _donor(2)
    rid = _request(h, units=2)
    _contact(h, rid)
    a.post(f"/blood-requests/{rid}/respond", json={"action": "ACCEPTED"})
    a.post(f"/blood-requests/{rid}/respond", json={"action": "DECLINED"})
    b.post(f"/blood-requests/{rid}/respond", json={"action": "ACCEPTED"})
    s = _sl(h, rid)
    statuses = sorted(c["status"] for c in s["contacts"])
    assert statuses == ["ACCEPTED", "WITHDRAWN"]
    # b donates elsewhere before the coordinator confirms -> recheck fails
    b.put("/donors/me", json={"last_donation_date": date.today().isoformat()})
    acc = next(c for c in s["contacts"] if c["status"] == "ACCEPTED")
    s = h.post(f"/blood-requests/{rid}/shortlist/contacts/{acc['id']}/confirm").json()
    row = next(c for c in s["contacts"] if c["id"] == acc["id"])
    assert row["status"] == "ELIGIBILITY_RECHECK_REQUIRED"
    assert "interval" in row["status_reason"]


def test_timeout_marks_unanswered_and_escalates():
    h = _hospital()
    for i in range(4):
        _donor(1 + i)
    rid = _request(h, units=1)
    _contact(h, rid)
    db = SessionLocal()
    try:
        db.execute(update(DonorContactAttempt).values(
            timeout_at=datetime.now(UTC) - timedelta(minutes=1)))
        db.commit()
    finally:
        db.close()
    s = _sl(h, rid)
    assert s["counts"]["timed_out"] == 2
    assert s["counts"]["awaiting_response"] == 2  # next batch contacted automatically
    assert any("did not answer" in e for e in s["escalation"])


def test_no_eligible_donors_is_an_honest_empty_state():
    h = _hospital()
    _donor(200)
    rid = _request(h)
    s = _sl(h, rid)
    assert s["state"] == "NO_ELIGIBLE" and s["counts"]["eligible_candidates"] == 0
    assert s["next_to_contact"] == [] and s["counts"]["confirmed_for_coordination"] == 0
    assert any("No eligible donor" in e for e in s["escalation"])


# --------------------------------------------------------------------------- #
# Access and privacy
# --------------------------------------------------------------------------- #
def test_access_control_and_privacy():
    h = _hospital()
    donor, _ = _donor(3, name="Private Person")
    p, p_email = auth_client("PATIENT")
    rid = p.post("/blood-requests", json={"blood_type": "O_NEG", "city": "Metro"}).json()["id"]
    mine = _sl(p, rid)
    assert mine["next_to_contact"][0]["donor_name"] == "Private Person"
    other_hospital = _sl(h, rid)  # same city: may see the list, never the names
    assert other_hospital["next_to_contact"][0]["donor_name"] is None
    assert donor.get(f"/blood-requests/{rid}/shortlist").status_code == 404
    assert h.post(f"/blood-requests/{rid}/shortlist/contact-next").status_code == 403
    body = str(mine)
    assert "latitude" not in body and "phone" not in body and "medical" not in body


# --------------------------------------------------------------------------- #
# Shortlist alert mode
# --------------------------------------------------------------------------- #
def test_shortlist_mode_contacts_only_the_first_batch(monkeypatch):
    monkeypatch.setattr(settings, "donor_alert_mode", "shortlist")
    h = _hospital()
    clients = [_donor(1 + i)[0] for i in range(7)]
    rid = _request(h, units=1)
    got = sum("blood_request.shortlist_invite" in [n["type"] for n in c.get(
        "/notifications").json()] for c in clients)
    assert got == 2  # not 7
    assert all("blood_request.new" not in [n["type"] for n in c.get("/notifications").json()]
               for c in clients)
    assert _sl(h, rid)["counts"]["awaiting_response"] == 2


# --------------------------------------------------------------------------- #
# Demo simulator
# --------------------------------------------------------------------------- #
def _synthetic(persona: str, n: int, km: float, **fields):
    return _donor(km, email=f"sim-{persona}-{n}@{svc.SYNTHETIC_EMAIL_DOMAIN}",
                  name=f"[SYNTHETIC] {persona} {n}", **fields)


def test_simulator_is_off_by_default_admin_only_and_synthetic_only(monkeypatch):
    h = _hospital()
    _synthetic("accept", 1, 1)
    real, _ = _donor(2)
    rid = _request(h, units=1)
    _contact(h, rid)
    admin, _ = auth_client("ADMIN")
    url = f"/demo/shortlist/BLOOD_REQUEST/{rid}/simulate"
    assert admin.post(url, json={}).status_code == 404  # DEMO_MODE off
    monkeypatch.setattr(settings, "demo_mode", True)
    assert h.post(url, json={}).status_code == 403
    r = admin.post(url, json={})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["simulated"]["accepted"] == 1 and out["skipped_non_synthetic"] == 1
    rows = out["shortlist"]["contacts"]
    sim = next(c for c in rows if c["synthetic"])
    assert sim["status"] == "ACCEPTED" and sim["simulated"] is True
    assert next(c for c in rows if not c["synthetic"])["status"] == "AWAITING_RESPONSE"
    assert real


@pytest.mark.parametrize("scenario", ["A", "B", "C", "D"])
def test_demo_scenarios(monkeypatch, scenario):
    monkeypatch.setattr(settings, "demo_mode", True)
    h = _hospital()
    admin, _ = auth_client("ADMIN")
    if scenario == "A":  # three needed, three accept
        for i in range(3):
            _synthetic("accept", i, 1 + i)
        units = 3
    elif scenario == "B":  # many eligible, only two accept
        for i in range(2):
            _synthetic("accept", i, 1 + i)
        for i in range(13):
            _synthetic("decline" if i % 2 else "silent", i, 3 + i)
        units = 3
    elif scenario == "C":  # nobody eligible
        _synthetic("accept", 1, 120)  # beyond the limit
        _synthetic("accept", 2, 2, last_donation_date=date.today().isoformat())
        units = 1
    else:  # D: the top-ranked donor never answers
        _synthetic("silent", 1, 0.5)
        _synthetic("accept", 2, 9)
        units = 1
    rid = _request(h, units=units)
    url = f"/demo/shortlist/BLOOD_REQUEST/{rid}/simulate"
    if scenario == "C":
        s = _sl(h, rid)
        assert s["state"] == "NO_ELIGIBLE" and s["counts"]["confirmed_for_coordination"] == 0
        return
    s = _contact(h, rid)
    for _ in range(6):  # simulate, escalate, simulate ... until settled
        s = admin.post(url, json={}).json()["shortlist"]
        if s["counts"]["awaiting_response"] == 0:
            break
    for c in s["contacts"]:
        if c["status"] == "ACCEPTED":
            s = h.post(f"/blood-requests/{rid}/shortlist/contacts/{c['id']}/confirm").json()
    if scenario == "A":
        assert s["counts"]["confirmed_for_coordination"] == 3 and s["state"] == "SATISFIED"
        assert s["counts"]["eligible_candidates"] >= 3
    elif scenario == "B":
        assert s["counts"]["eligible_candidates"] == 15
        assert s["counts"]["confirmed_for_coordination"] == 2 and s["remaining_unmet"] == 1
        assert s["state"] == "PARTIAL" and "1 donor(s) short" in s["summary"]
    else:
        top = min(s["contacts"], key=lambda c: c["rank_at_contact"])
        assert top["status"] == "TIMED_OUT" and top["rank_at_contact"] == 1
        assert s["counts"]["confirmed_for_coordination"] == 1


def test_emergency_shortlist_contact_accept_confirm_and_policy_endpoint():
    """The SOS path through the API (the route log of the 2026-10-31 full run
    showed no 2xx for these three operations)."""
    p, _ = auth_client("PATIENT")
    pol = p.get("/shortlist/policy")
    assert pol.status_code == 200, pol.text
    assert pol.json()["name"] == svc.POLICY_NAME
    a, _ = _donor(2)
    b, _ = _donor(4)
    _donor(80)  # beyond the 50 km limit: never a candidate
    eid = p.post("/emergencies", json={
        "blood_type": "O_NEG", "units_needed": 1, "latitude": LAT0, "longitude": LON0,
        "city": "Metro"}).json()["id"]
    r = p.post(f"/emergencies/{eid}/shortlist/contact-next")
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["counts"]["eligible_candidates"] == 2 and s["counts"]["contacted"] == 2
    assert s["excluded"].get("beyond_distance_limit") == 1
    assert a.post(f"/emergencies/{eid}/respond", json={"action": "ACCEPTED"}).status_code == 200
    s = p.get(f"/emergencies/{eid}/shortlist").json()
    acc = next(c for c in s["contacts"] if c["status"] == "ACCEPTED")
    assert s["counts"]["confirmed_for_coordination"] == 0  # accepted is not confirmed
    r = p.post(f"/emergencies/{eid}/shortlist/contacts/{acc['id']}/confirm")
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["counts"]["confirmed_for_coordination"] == 1
    assert s["state"] == "SATISFIED" and s["remaining_unmet"] == 0
