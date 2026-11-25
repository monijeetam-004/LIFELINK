"""Audit finding D4 (2026-10-31): one donor-distance policy for blood-request
alerts, SOS alerts, ranking and the shortlist (app/core/distance_policy.py).

The audit found that blood-request alerts ignored MAX_DONOR_DISTANCE_KM and
that a request without a city reached every compatible donor nationwide.
These tests pin the corrected behaviour: below / at / above the limit,
missing coordinates on either side, stale locations, the opt-in unscoped
fallback, and the admin override (permission, reason, audit)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core import distance_policy as dpol
from app.core.config import settings
from app.db.session import SessionLocal
from app.models.audit_log import AuditLog
from tests.helpers import auth_client

KM_PER_DEG_LAT = 6371.0 * 3.141592653589793 / 180
LAT0, LON0 = 12.0, 77.0


# --------------------------------------------------------------------------- #
# Pure policy
# --------------------------------------------------------------------------- #
def _assess(**kw):
    base = {
        "origin_lat": LAT0, "origin_lng": LON0, "origin_city": "Metro",
        "donor_lat": None, "donor_lng": None, "donor_shares_location": False, "donor_city": None,
        "donor_alert_radius_km": None, "limit_km": 50.0,
    }
    base.update(kw)
    return dpol.assess(**base)


def _at_km(km: float) -> dict:
    return {"donor_lat": LAT0 + km / KM_PER_DEG_LAT, "donor_lng": LON0,
            "donor_shares_location": True}


def test_boundary_below_at_and_above_the_limit_is_inclusive():
    below, at, above = _assess(**_at_km(49.9)), _assess(**_at_km(50.0)), _assess(**_at_km(50.2))
    assert [a.basis for a in (below, at, above)] == [dpol.DistanceBasis.MEASURED] * 3
    assert dpol.alert_skip_reason(below) is None
    assert dpol.alert_skip_reason(at) is None
    assert dpol.alert_skip_reason(above) == "beyond_distance_limit"
    assert dpol.alert_skip_reason(above, override=True) is None


def test_missing_location_is_never_read_as_nearby():
    unknown = _assess(donor_city="Elsewhere")
    assert unknown.basis is dpol.DistanceBasis.UNKNOWN and unknown.distance_km is None
    assert dpol.alert_skip_reason(unknown) == "location_unknown"
    # coordinates present but not shared count as unknown too
    hidden = _assess(donor_lat=LAT0, donor_lng=LON0, donor_shares_location=False)
    assert hidden.basis is dpol.DistanceBasis.UNKNOWN
    same_city = _assess(donor_city="  metro ")
    assert same_city.basis is dpol.DistanceBasis.SAME_CITY
    assert _assess(donor_city="Metro, Some State").basis is dpol.DistanceBasis.SAME_CITY
    assert dpol.alert_skip_reason(same_city) is None
    assert "exact distance unknown" in same_city.describe()


def test_request_without_any_location_alerts_nobody_unless_explicitly_allowed():
    a = _assess(origin_lat=None, origin_lng=None, origin_city=None, **_at_km(1))
    assert a.basis is dpol.DistanceBasis.NO_REQUEST_LOCATION
    assert dpol.alert_skip_reason(a) == "no_request_location"
    assert dpol.alert_skip_reason(a, allow_unscoped=True) is None
    assert dpol.alert_skip_reason(a, override=True) is None


def test_personal_alert_radius_holds_even_under_an_admin_override():
    a = _assess(donor_alert_radius_km=5, **_at_km(10))
    assert dpol.alert_skip_reason(a, override=True) == "outside_alert_radius"


def test_stale_location_is_measured_but_labelled():
    now = datetime.now(UTC)
    a = _assess(location_updated_at=now - timedelta(days=200), now=now, **_at_km(10))
    assert a.basis is dpol.DistanceBasis.MEASURED and a.location_stale is True
    assert "200 days ago" in a.describe()
    fresh = _assess(location_updated_at=now - timedelta(days=3), now=now, **_at_km(10))
    assert fresh.location_stale is False


# --------------------------------------------------------------------------- #
# Through the API
# --------------------------------------------------------------------------- #
def _donor(lat=None, city=None):
    c, _ = auth_client("DONOR")
    body = {"blood_type": "O_NEG", "city": city}
    if lat is not None:
        body.update(latitude=lat, longitude=LON0, share_location=True)
    assert c.put("/donors/me", json=body).status_code == 200
    return c


def _types(c) -> list[str]:
    return [n["type"] for n in c.get("/notifications").json()]


def test_blood_request_alerts_apply_the_distance_limit():
    near = _donor(LAT0 + 10 / KM_PER_DEG_LAT, city="Elsewhere")
    at = _donor(LAT0 + 50 / KM_PER_DEG_LAT, city="Elsewhere")
    above = _donor(LAT0 + 50.2 / KM_PER_DEG_LAT, city="Metro")  # same city, but measured
    unlocated_same_city = _donor(city="Metro")
    unlocated_elsewhere = _donor(city="Elsewhere")
    h, _ = auth_client("HOSPITAL")
    assert h.put("/hospitals/me", json={"name": "Metro General", "city": "Metro",
                                        "latitude": LAT0, "longitude": LON0}).status_code == 200
    r = h.post("/blood-requests", json={"blood_type": "O_NEG", "urgency": "URGENT"})
    assert r.status_code == 201, r.text
    assert "blood_request.new" in _types(near)
    assert "blood_request.new" in _types(at)
    assert "blood_request.new" not in _types(above)
    assert "blood_request.new" in _types(unlocated_same_city)
    assert "blood_request.new" not in _types(unlocated_elsewhere)
    db = SessionLocal()
    try:
        row = db.scalars(select(AuditLog).where(AuditLog.action == "blood_request.created")).one()
        skips = row.meta["donor_alert_skips"]
        assert skips["beyond_distance_limit"] == 1 and skips["location_unknown"] == 1
        assert row.meta["notified_donors"] == 3
    finally:
        db.close()


def test_request_without_city_or_coordinates_is_not_broadcast(monkeypatch):
    anywhere = _donor(LAT0, city="Metro")
    patient, _ = auth_client("PATIENT")
    rid = patient.post("/blood-requests", json={"blood_type": "O_NEG"}).json()["id"]
    assert "blood_request.new" not in _types(anywhere)
    events = [e["event_type"] for e in patient.get(f"/blood-requests/{rid}").json()["events"]]
    assert "donor_alerts_withheld" in events

    # The documented, explicitly configured fallback re-enables it.
    monkeypatch.setattr(settings, "donor_alert_allow_unscoped", True)
    patient.post("/blood-requests", json={"blood_type": "O_NEG", "note": "second"})
    assert "blood_request.new" in _types(anywhere)


def test_sos_without_location_alerts_no_donor_and_says_why():
    donor = _donor(LAT0, city="Metro")
    patient, _ = auth_client("PATIENT")
    eid = patient.post("/emergencies", json={"blood_type": "O_NEG"}).json()["id"]
    assert "emergency.new" not in _types(donor)
    events = [e["event_type"] for e in patient.get(f"/emergencies/{eid}").json()["events"]]
    assert "donor_alerts_withheld" in events
    eid2 = patient.post("/emergencies", json={"blood_type": "O_NEG", "city": "Metro"}).json()["id"]
    assert eid2 and "emergency.new" in _types(donor)


def test_ranking_override_is_admin_only_needs_a_reason_and_is_audited():
    _donor(LAT0 + 80 / KM_PER_DEG_LAT, city="Metro")
    h, _ = auth_client("HOSPITAL")
    h.put("/hospitals/me", json={"name": "Metro General", "city": "Metro",
                                 "latitude": LAT0, "longitude": LON0})
    rid = h.post("/blood-requests", json={"blood_type": "O_NEG"}).json()["id"]
    base = f"/blood-requests/{rid}/match-candidates"
    assert h.get(base).json()["excluded_beyond_limit"] == 1
    assert h.get(base, params={"override_distance_limit": True}).status_code == 403
    admin, _ = auth_client("ADMIN")
    assert admin.get(base, params={"override_distance_limit": True}).status_code == 422
    r = admin.get(base, params={"override_distance_limit": True,
                                "override_reason": "rural area, few donors"})
    assert r.status_code == 200 and r.json()["excluded_beyond_limit"] == 0
    db = SessionLocal()
    try:
        row = db.scalars(select(AuditLog).where(
            AuditLog.action == "matching.distance_limit_overridden")).one()
        assert row.meta["reason"] == "rural area, few donors"
    finally:
        db.close()
