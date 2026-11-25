"""Alert Watcher preferences: alert_min_urgency, alert_radius_km, quiet hours.

These are real, donor-configurable filters layered on top of the automatic
blood-type-compatibility check every matching path already does -- see
notification_service.notify_matching_donors and _in_quiet_hours.
"""
from __future__ import annotations

import datetime as dt
from unittest.mock import patch

from app.services.notification_service import _in_quiet_hours, _parse_hhmm
from tests.helpers import auth_client

# Audit D4 (2026-10-31): donor alerts follow app/core/distance_policy.py -- a
# donor without a shared location is alerted only when recorded in the same
# city as the request, and a request with no location alerts nobody. These
# tests are about urgency, radius and quiet-hours preferences, so the donors,
# hospitals and SOS calls below share one city.
CITY = "Testville"


def _ready_donor(blood_type: str = "O_NEG", **profile_fields):
    c, email = auth_client("DONOR")
    payload = {"blood_type": blood_type, "city": CITY, **profile_fields}
    r = c.put("/donors/me", json=payload)
    assert r.status_code == 200, r.text
    return c, email


def _hospital(lat: float, lng: float, name: str = "Metro General"):
    c, email = auth_client("HOSPITAL")
    r = c.put("/hospitals/me", json={"name": name, "latitude": lat, "longitude": lng,
                                      "city": CITY})
    assert r.status_code == 200, r.text
    return c, email


def _file_request(hospital, blood_type: str = "O_NEG", urgency: str = "ROUTINE") -> str:
    r = hospital.post(
        "/blood-requests", json={"blood_type": blood_type, "urgency": urgency}
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _notified_types(client) -> set[str]:
    return {n["type"] for n in client.get("/notifications").json()}


# --- alert_min_urgency --------------------------------------------------


def test_alert_min_urgency_filters_out_lower_urgency_requests() -> None:
    picky, _ = _ready_donor(alert_min_urgency="CRITICAL")
    default, _ = _ready_donor()  # ROUTINE threshold (default) -- hears everything
    hospital, _ = _hospital(0.0, 0.0)

    _file_request(hospital, urgency="ROUTINE")
    assert "blood_request.new" not in _notified_types(picky)
    assert "blood_request.new" in _notified_types(default)

    _file_request(hospital, urgency="CRITICAL")
    assert "blood_request.new" in _notified_types(picky)


def test_alert_min_urgency_does_not_suppress_emergency_sos() -> None:
    """An emergency SOS is never held back by a donor's general urgency
    threshold, the same 'never suppressed' treatment quiet hours already
    give emergency email."""
    picky, _ = _ready_donor(alert_min_urgency="CRITICAL")
    patient, _ = auth_client("PATIENT")

    r = patient.post("/emergencies", json={"blood_type": "O_NEG", "note": "urgent",
                                                     "city": CITY})
    assert r.status_code == 201, r.text

    assert "emergency.new" in _notified_types(picky)


# --- alert_radius_km -----------------------------------------------------


def test_alert_radius_km_filters_by_distance() -> None:
    hospital, _ = _hospital(0.0, 0.0)

    nearby, _ = _ready_donor(
        latitude=0.01, longitude=0.01, share_location=True, alert_radius_km=50
    )
    far_with_radius, _ = _ready_donor(
        latitude=40.0, longitude=40.0, share_location=True, alert_radius_km=50
    )
    far_no_radius_set, _ = _ready_donor(
        latitude=40.0, longitude=40.0, share_location=True
    )
    far_no_location_shared, _ = _ready_donor(
        latitude=40.0, longitude=40.0, share_location=False, alert_radius_km=50
    )
    elsewhere_no_location_shared, _ = _ready_donor(
        latitude=40.0, longitude=40.0, share_location=False, city="Elsewhere"
    )

    _file_request(hospital)

    assert "blood_request.new" in _notified_types(nearby)
    assert "blood_request.new" not in _notified_types(far_with_radius)
    # Audit D4 (2026-10-31) -- these two assertions changed on purpose. Before,
    # blood-request alerts ignored MAX_DONOR_DISTANCE_KM, so a donor ~6,000 km
    # away with no personal radius was alerted; now the shared limit applies.
    assert "blood_request.new" not in _notified_types(far_no_radius_set)
    # Not sharing a location no longer means "alert regardless": this donor is
    # recorded in the same city as the hospital, so the city-level rule
    # (labelled SAME_CITY, never a distance) is what admits them.
    assert "blood_request.new" in _notified_types(far_no_location_shared)
    # ...while an unlocated donor in another city is never treated as nearby.
    assert "blood_request.new" not in _notified_types(elsewhere_no_location_shared)


# --- quiet hours -----------------------------------------------------------


def test_in_quiet_hours_pure_logic() -> None:
    class _Pref:
        quiet_hours_start = "22:00"
        quiet_hours_end = "07:00"

    pref = _Pref()
    assert _in_quiet_hours(pref, now=dt.datetime(2026, 1, 1, 23, 30)) is True
    assert _in_quiet_hours(pref, now=dt.datetime(2026, 1, 1, 3, 0)) is True
    assert _in_quiet_hours(pref, now=dt.datetime(2026, 1, 1, 12, 0)) is False

    class _NoPrefs:
        quiet_hours_start = None
        quiet_hours_end = None

    assert _in_quiet_hours(_NoPrefs(), now=dt.datetime(2026, 1, 1, 23, 30)) is False


def test_parse_hhmm() -> None:
    assert _parse_hhmm("07:05") == dt.time(7, 5)
    assert _parse_hhmm(None) is None
    assert _parse_hhmm("not-a-time") is None


def test_quiet_hours_suppress_non_emergency_email_but_not_in_app(mailbox) -> None:
    donor, donor_email = _ready_donor()
    donor.put(
        "/notifications/preferences",
        json={"quiet_hours_start": "22:00", "quiet_hours_end": "07:00"},
    )
    hospital, _ = _hospital(0.0, 0.0)

    with patch("app.services.notification_service.datetime") as mock_dt:
        mock_dt.now.return_value = dt.datetime(2026, 1, 1, 23, 0)
        _file_request(hospital)

    # in-app notification is never suppressed by quiet hours
    assert "blood_request.new" in _notified_types(donor)
    # but the email echo was held back
    assert not any(m["to"] == donor_email for m in mailbox)


def test_quiet_hours_never_suppress_emergency_email(mailbox) -> None:
    donor, donor_email = _ready_donor()
    donor.put(
        "/notifications/preferences",
        json={"quiet_hours_start": "22:00", "quiet_hours_end": "07:00"},
    )
    patient, _ = auth_client("PATIENT")

    with patch("app.services.notification_service.datetime") as mock_dt:
        mock_dt.now.return_value = dt.datetime(2026, 1, 1, 23, 0)
        r = patient.post("/emergencies", json={"blood_type": "O_NEG", "note": "urgent",
                                                     "city": CITY})
        assert r.status_code == 201, r.text

    assert "emergency.new" in _notified_types(donor)
    assert any(m["to"] == donor_email for m in mailbox)
