from __future__ import annotations

from tests.helpers import auth_client


def test_get_preferences_returns_sensible_defaults() -> None:
    c, _ = auth_client("PATIENT")
    resp = c.get("/notifications/preferences")
    assert resp.status_code == 200
    body = resp.json()
    assert body["email_enabled"] is True
    assert body["sms_enabled"] is False
    assert body["emergency_alerts_enabled"] is True
    assert body["digest_frequency"] == "REALTIME"
    assert body["quiet_hours_start"] is None
    assert body["quiet_hours_end"] is None


def test_put_preferences_partial_update_only_touches_sent_fields() -> None:
    c, _ = auth_client("DONOR")
    c.put(
        "/notifications/preferences",
        json={
            "email_enabled": False,
            "digest_frequency": "DAILY",
            "quiet_hours_start": "22:00",
            "quiet_hours_end": "07:00",
        },
    )

    resp = c.put("/notifications/preferences", json={"sms_enabled": True})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Field explicitly updated in this call.
    assert body["sms_enabled"] is True
    # Fields set earlier and not sent this time are untouched.
    assert body["email_enabled"] is False
    assert body["digest_frequency"] == "DAILY"
    assert body["quiet_hours_start"] == "22:00"
    assert body["quiet_hours_end"] == "07:00"
    # Untouched field still at its default.
    assert body["emergency_alerts_enabled"] is True

    persisted = c.get("/notifications/preferences").json()
    assert persisted == body


def test_put_preferences_can_clear_quiet_hours_with_explicit_null() -> None:
    c, _ = auth_client("PATIENT")
    c.put("/notifications/preferences", json={"quiet_hours_start": "22:00"})
    resp = c.put("/notifications/preferences", json={"quiet_hours_start": None})
    assert resp.status_code == 200, resp.text
    assert resp.json()["quiet_hours_start"] is None


def test_put_preferences_invalid_digest_frequency_rejected() -> None:
    c, _ = auth_client("PATIENT")
    resp = c.put("/notifications/preferences", json={"digest_frequency": "HOURLY"})
    assert resp.status_code == 422


def test_put_preferences_invalid_quiet_hours_format_rejected() -> None:
    c, _ = auth_client("PATIENT")
    resp = c.put("/notifications/preferences", json={"quiet_hours_start": "10pm"})
    assert resp.status_code == 422

    resp2 = c.put("/notifications/preferences", json={"quiet_hours_end": "9:00"})
    assert resp2.status_code == 422


def test_preferences_are_per_user() -> None:
    a, _ = auth_client("PATIENT")
    b, _ = auth_client("DONOR")

    a.put("/notifications/preferences", json={"email_enabled": False, "sms_enabled": True})

    a_prefs = a.get("/notifications/preferences").json()
    b_prefs = b.get("/notifications/preferences").json()

    assert a_prefs["email_enabled"] is False
    assert a_prefs["sms_enabled"] is True
    # B's row is untouched / still default.
    assert b_prefs["email_enabled"] is True
    assert b_prefs["sms_enabled"] is False
    assert a_prefs["user_id"] != b_prefs["user_id"]


def test_preferences_require_auth() -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    anon = TestClient(app, base_url="http://testserver")
    assert anon.get("/notifications/preferences").status_code == 401
    anon.headers["Origin"] = "http://localhost:5173"
    assert anon.put("/notifications/preferences", json={"email_enabled": False}).status_code == 401
