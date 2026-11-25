"""Tests for the (dormant-by-default) SMS notification channel.

Three layers, tested at the right altitude for each:
  * app/core/phone.py -- pure phone validation/normalization/masking logic.
  * app/services/sms/msg91.py -- MSG91Provider's HTTP response classification,
    using httpx.MockTransport so NO test here ever makes a real network call
    or could possibly send a real SMS (see MSG91Provider's `transport` param).
  * app/services/sms_service.py + notification_service.notify() -- the
    orchestration/idempotency/gating layer, and proof that SMS being
    disabled (the default in every LifeLink deployment -- see
    LIFELINK_SMS_STATUS.md) never touches the network, never writes an
    audit row, and never affects in-app notifications or email.
"""
from __future__ import annotations

import uuid

import httpx
import pytest

from app.core.config import settings
from app.core.phone import mask_phone, normalize_phone_e164, validate_phone_field
from app.models.enums import SmsFailureCategory, SmsStatus, SmsTemplateKey
from app.models.notification import Notification
from app.models.sms_notification import SmsNotification
from app.models.user import User, UserRole
from app.services import notification_service, sms_service
from app.services.sms import get_sms_provider
from app.services.sms.msg91 import MSG91Provider
from tests.helpers import auth_client, seed_verified_user

# --------------------------------------------------------------------------- #
# app/core/phone.py
# --------------------------------------------------------------------------- #


def test_normalize_phone_e164_bare_indian_number() -> None:
    assert normalize_phone_e164("9876543210") == "+919876543210"


def test_normalize_phone_e164_already_e164() -> None:
    assert normalize_phone_e164("+91 98765 43210") == "+919876543210"


def test_normalize_phone_e164_international() -> None:
    assert normalize_phone_e164("+1 415-555-0132") == "+14155550132"


@pytest.mark.parametrize("value", [None, "", "   "])
def test_normalize_phone_e164_clears_on_blank(value: str | None) -> None:
    assert normalize_phone_e164(value) is None


@pytest.mark.parametrize("value", ["555-1", "not a phone", "123", "0000000000000000"])
def test_normalize_phone_e164_rejects_malformed(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_phone_e164(value)


def test_validate_phone_field_wraps_as_value_error_with_message() -> None:
    with pytest.raises(ValueError, match="not a valid phone number"):
        validate_phone_field("555-1")


def test_mask_phone() -> None:
    masked = mask_phone("+919876543210")
    assert masked is not None
    assert masked.startswith("+91")
    assert masked.endswith("10")
    assert "9876543" not in masked  # the hidden middle digits never appear


def test_mask_phone_none() -> None:
    assert mask_phone(None) is None


# --------------------------------------------------------------------------- #
# app/services/sms/msg91.py -- HTTP response classification (mocked transport)
# --------------------------------------------------------------------------- #


@pytest.fixture
def configured_msg91(monkeypatch: pytest.MonkeyPatch):
    """Real MSG91 config values (never used to make a real call -- every
    test here injects a MockTransport) so `sms_provider_configured` is True
    and MSG91Provider() constructs without raising."""
    monkeypatch.setattr(settings, "msg91_enabled", True)
    monkeypatch.setattr(settings, "msg91_auth_key", "test-auth-key")
    monkeypatch.setattr(settings, "msg91_flow_id", "test-flow-id")
    monkeypatch.setattr(settings, "msg91_sender_id", "LFLINK")
    monkeypatch.setattr(settings, "msg91_dlt_template_id", None)
    monkeypatch.setattr(settings, "msg91_template_flow_map", None)
    yield


def _provider(handler) -> MSG91Provider:
    return MSG91Provider(transport=httpx.MockTransport(handler))


def test_msg91_provider_unavailable_when_not_configured() -> None:
    assert settings.msg91_enabled is False  # test env default
    from app.services.sms import SmsProviderUnavailableError

    with pytest.raises(SmsProviderUnavailableError):
        get_sms_provider()


def test_msg91_send_success(configured_msg91: None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authkey"] == "test-auth-key"
        body = request.read()
        import json

        payload = json.loads(body)
        assert payload["template_id"] == "test-flow-id"
        assert payload["recipients"][0]["mobiles"] == "919876543210"
        return httpx.Response(200, json={"type": "success", "message": "msg-ref-123"})

    provider = _provider(handler)
    result = provider.send_sms(
        to_e164="+919876543210",
        template_key=SmsTemplateKey.BLOOD_REQUEST_FULFILLED,
        variables={},
    )
    assert result.accepted is True
    assert result.provider_message_id == "msg-ref-123"


def test_msg91_send_missing_variables_never_calls_network(configured_msg91: None) -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"type": "success"})  # pragma: no cover

    provider = _provider(handler)
    result = provider.send_sms(
        to_e164="+919876543210",
        template_key=SmsTemplateKey.DONOR_MATCH_ALERT,  # requires blood_type + urgency
        variables={},
    )
    assert called is False
    assert result.accepted is False
    assert result.failure_category == SmsFailureCategory.INVALID_TEMPLATE


def test_msg91_send_auth_failure(configured_msg91: None) -> None:
    provider = _provider(lambda r: httpx.Response(401, json={"message": "invalid authkey"}))
    result = provider.send_sms(
        to_e164="+919876543210", template_key=SmsTemplateKey.BLOOD_REQUEST_FULFILLED, variables={}
    )
    assert result.accepted is False
    assert result.failure_category == SmsFailureCategory.AUTH_FAILURE


def test_msg91_send_rate_limited(configured_msg91: None) -> None:
    provider = _provider(lambda r: httpx.Response(429, json={"message": "too many requests"}))
    result = provider.send_sms(
        to_e164="+919876543210", template_key=SmsTemplateKey.BLOOD_REQUEST_FULFILLED, variables={}
    )
    assert result.accepted is False
    assert result.failure_category == SmsFailureCategory.RATE_LIMITED


def test_msg91_send_5xx(configured_msg91: None) -> None:
    provider = _provider(lambda r: httpx.Response(503, text="upstream down"))
    result = provider.send_sms(
        to_e164="+919876543210", template_key=SmsTemplateKey.BLOOD_REQUEST_FULFILLED, variables={}
    )
    assert result.accepted is False
    assert result.failure_category == SmsFailureCategory.PROVIDER_5XX


def test_msg91_send_invalid_recipient_4xx(configured_msg91: None) -> None:
    provider = _provider(
        lambda r: httpx.Response(400, json={"message": "invalid mobile number"})
    )
    result = provider.send_sms(
        to_e164="+919876543210", template_key=SmsTemplateKey.BLOOD_REQUEST_FULFILLED, variables={}
    )
    assert result.accepted is False
    assert result.failure_category == SmsFailureCategory.INVALID_RECIPIENT


def test_msg91_send_application_level_error_on_200(configured_msg91: None) -> None:
    """MSG91's Flow API can report failure via `type: error` even on HTTP 200."""
    provider = _provider(
        lambda r: httpx.Response(200, json={"type": "error", "message": "template not approved"})
    )
    result = provider.send_sms(
        to_e164="+919876543210", template_key=SmsTemplateKey.BLOOD_REQUEST_FULFILLED, variables={}
    )
    assert result.accepted is False
    assert result.failure_category == SmsFailureCategory.INVALID_TEMPLATE


def test_msg91_send_timeout(configured_msg91: None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    provider = _provider(handler)
    result = provider.send_sms(
        to_e164="+919876543210", template_key=SmsTemplateKey.BLOOD_REQUEST_FULFILLED, variables={}
    )
    assert result.accepted is False
    assert result.failure_category == SmsFailureCategory.TIMEOUT


def test_msg91_per_template_flow_id_override(configured_msg91: None, monkeypatch) -> None:
    """MSG91_TEMPLATE_FLOW_MAP lets a specific template use its own
    DLT-approved Flow ID; a template not present in the map still falls
    back to the global MSG91_FLOW_ID (see flow_id_for())."""
    import json as _json

    monkeypatch.setattr(
        settings,
        "msg91_template_flow_map",
        _json.dumps({SmsTemplateKey.BLOOD_REQUEST_FULFILLED.value: "override-flow-id"}),
    )
    seen_flow_ids = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = _json.loads(request.read())
        seen_flow_ids.append(payload["template_id"])
        return httpx.Response(200, json={"type": "success"})

    provider = _provider(handler)
    provider.send_sms(
        to_e164="+919876543210", template_key=SmsTemplateKey.BLOOD_REQUEST_FULFILLED, variables={}
    )
    provider.send_sms(
        to_e164="+919876543210", template_key=SmsTemplateKey.BLOOD_REQUEST_CANCELLED, variables={}
    )
    assert seen_flow_ids == ["override-flow-id", "test-flow-id"]


def test_msg91_no_flow_id_configured_for_template(
    configured_msg91: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deployment can be "configured" (real auth key + sender id) while
    still having no Flow ID -- global or per-template -- for a particular
    template. That template just isn't sendable yet, reported honestly."""
    monkeypatch.setattr(settings, "msg91_flow_id", None)
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"type": "success"})  # pragma: no cover

    provider = _provider(handler)
    result = provider.send_sms(
        to_e164="+919876543210", template_key=SmsTemplateKey.BLOOD_REQUEST_FULFILLED, variables={}
    )
    assert called is False
    assert result.accepted is False
    assert result.failure_category == SmsFailureCategory.INVALID_TEMPLATE


# --------------------------------------------------------------------------- #
# app/services/sms_service.py -- orchestration / idempotency
# --------------------------------------------------------------------------- #


def _make_user(db, role: UserRole = UserRole.DONOR) -> User:
    uid = seed_verified_user(f"{uuid.uuid4().hex[:10]}@example.com", role=role.value)
    return db.get(User, uid)


def test_send_for_event_no_template_mapped_returns_none(db_session) -> None:
    user = _make_user(db_session)
    n = Notification(user_id=user.id, type="account.password_changed", title="x")
    db_session.add(n)
    db_session.flush()
    result = sms_service.send_for_event(
        db_session, user, event_type="account.password_changed", notification_id=n.id
    )
    assert result is None


def test_send_for_event_no_phone_on_file_records_failure(db_session) -> None:
    user = _make_user(db_session)  # DonorProfile not created -> no phone
    n = Notification(user_id=user.id, type="blood_request.fulfilled", title="x")
    db_session.add(n)
    db_session.flush()
    row = sms_service.send_for_event(
        db_session, user, event_type="blood_request.fulfilled", notification_id=n.id
    )
    assert row is not None
    assert row.status == SmsStatus.FAILED
    assert row.failure_category == SmsFailureCategory.NO_PHONE_ON_FILE
    assert row.recipient_phone_masked is None


def test_send_for_event_provider_not_configured(db_session) -> None:
    from app.models.profile import DonorProfile

    user = _make_user(db_session)
    db_session.add(DonorProfile(user_id=user.id, phone="+919876543210"))
    db_session.flush()
    n = Notification(user_id=user.id, type="blood_request.fulfilled", title="x")
    db_session.add(n)
    db_session.flush()

    assert settings.msg91_enabled is False
    row = sms_service.send_for_event(
        db_session, user, event_type="blood_request.fulfilled", notification_id=n.id
    )
    assert row is not None
    assert row.status == SmsStatus.FAILED
    assert row.failure_category == SmsFailureCategory.PROVIDER_NOT_CONFIGURED
    # Phone is masked in the audit trail -- never stored/logged in full.
    assert row.recipient_phone_masked != "+919876543210"


def test_send_for_event_idempotent_per_notification(
    db_session, configured_msg91: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.models.profile import DonorProfile
    from app.services import sms as sms_pkg

    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={"type": "success", "message": "ref-1"})

    monkeypatch.setattr(sms_pkg, "get_sms_provider", lambda: _provider(handler))
    monkeypatch.setattr("app.services.sms_service.get_sms_provider", lambda: _provider(handler))

    user = _make_user(db_session)
    db_session.add(DonorProfile(user_id=user.id, phone="+919876543210"))
    db_session.flush()
    n = Notification(user_id=user.id, type="blood_request.fulfilled", title="x")
    db_session.add(n)
    db_session.flush()

    row1 = sms_service.send_for_event(
        db_session, user, event_type="blood_request.fulfilled", notification_id=n.id
    )
    assert row1.status == SmsStatus.SENT
    assert call_count == 1

    # Re-attempting the SAME notification does not re-send -- exactly one
    # SmsNotification row exists (uq_sms_notification_per_notification),
    # and an already-SENT row is returned as-is without calling the
    # provider again.
    row2 = sms_service.send_for_event(
        db_session, user, event_type="blood_request.fulfilled", notification_id=n.id
    )
    assert row2.id == row1.id
    assert call_count == 1
    rows = (
        db_session.query(SmsNotification)
        .filter(SmsNotification.notification_id == n.id)
        .all()
    )
    assert len(rows) == 1


def test_sms_health_disabled_by_default() -> None:
    assert settings.msg91_enabled is False
    health = sms_service.sms_health()
    assert health["configured"] is False
    assert health["status"] == "disabled"


def test_sms_health_configured(configured_msg91: None) -> None:
    health = sms_service.sms_health()
    assert health["configured"] is True
    assert health["status"] == "configured"


# --------------------------------------------------------------------------- #
# notification_service.notify() -- the channel-agnostic entry point
# --------------------------------------------------------------------------- #


def test_notify_never_touches_sms_when_disabled(db_session) -> None:
    """The literal, load-bearing guarantee behind LIFELINK_SMS_STATUS.md:
    with SMS disabled (the default), notify() creates the in-app row and
    never creates an SmsNotification audit row at all -- not even a
    'skipped' one -- for any event, including emergency.new."""
    assert settings.msg91_enabled is False
    user = _make_user(db_session)
    n = notification_service.notify(
        db_session, user.id, type="emergency.new", title="EMERGENCY", body="O_NEG"
    )
    assert n.id is not None
    rows = (
        db_session.query(SmsNotification)
        .filter(SmsNotification.notification_id == n.id)
        .all()
    )
    assert rows == []


def test_notify_sends_sms_when_fully_enabled_and_opted_in(
    db_session, configured_msg91: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.models.notification_preference import NotificationPreference
    from app.models.profile import DonorProfile
    from app.services import sms as sms_pkg

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"type": "success", "message": "ref-2"})

    monkeypatch.setattr(sms_pkg, "get_sms_provider", lambda: _provider(handler))
    monkeypatch.setattr("app.services.sms_service.get_sms_provider", lambda: _provider(handler))

    user = _make_user(db_session)
    db_session.add(DonorProfile(user_id=user.id, phone="+919876543210"))
    db_session.add(NotificationPreference(user_id=user.id, sms_enabled=True))
    db_session.flush()

    n = notification_service.notify(
        db_session, user.id, type="blood_request.fulfilled", title="Thank you"
    )
    row = (
        db_session.query(SmsNotification)
        .filter(SmsNotification.notification_id == n.id)
        .one()
    )
    assert row.status == SmsStatus.SENT


def test_notify_does_not_send_sms_when_user_has_not_opted_in(
    db_session, configured_msg91: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even with the provider fully configured, a user whose
    NotificationPreference.sms_enabled is False (the column default) gets
    no SMS -- opt-in, not opt-out."""
    from app.models.profile import DonorProfile
    from app.services import sms as sms_pkg

    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"type": "success"})  # pragma: no cover

    monkeypatch.setattr(sms_pkg, "get_sms_provider", lambda: _provider(handler))
    monkeypatch.setattr("app.services.sms_service.get_sms_provider", lambda: _provider(handler))

    user = _make_user(db_session)
    db_session.add(DonorProfile(user_id=user.id, phone="+919876543210"))
    db_session.flush()

    n = notification_service.notify(
        db_session, user.id, type="blood_request.fulfilled", title="Thank you"
    )
    assert called is False
    rows = (
        db_session.query(SmsNotification)
        .filter(SmsNotification.notification_id == n.id)
        .all()
    )
    assert rows == []


def test_emergency_creation_works_with_sms_disabled() -> None:
    """End-to-end, through the real API: SMS is disabled (default test
    config) and emergency creation, donor alerting, and in-app
    notifications all still work exactly as they did before SMS existed
    in this codebase."""
    assert settings.msg91_enabled is False
    patient, _ = auth_client("PATIENT")
    donor, _ = auth_client("DONOR")
    # Audit D4 (2026-10-31): an SOS needs a location in scope to alert anyone.
    donor.put("/donors/me", json={"blood_type": "O_NEG", "city": "Metro"})

    r = patient.post("/emergencies", json={"blood_type": "O_NEG", "location_text": "Metro ER",
                                           "city": "Metro"})
    assert r.status_code == 201
    assert r.json()["status"] == "ACTIVE"
    assert any(n["type"] == "emergency.new" for n in donor.get("/notifications").json())


def test_notification_preferences_response_has_no_sms_delivery_claim() -> None:
    """The preferences API must never assert SMS was/will be delivered --
    it only ever reports the user's stored preference bit, which the
    frontend (Settings.tsx) does not expose as a control to normal users
    (see LIFELINK_SMS_STATUS.md). This just pins the honest contract: the
    field is a plain stored boolean, nothing more, and defaults off."""
    c, _ = auth_client("PATIENT")
    body = c.get("/notifications/preferences").json()
    assert body["sms_enabled"] is False
