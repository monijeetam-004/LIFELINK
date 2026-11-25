"""Phase 11 -- independent per-role registration limits + Gmail SMTP config.

Two independent things are tested here:

1. `RegistrationCounter` / `_reserve_registration_slot` (app.services.auth_service):
   four SEPARATE self-registration caps (PATIENT/FAMILY/DONOR/HOSPITAL), not
   one shared pool, enforced with real Postgres row locking. Registering
   100 distinct real users per role just to test the boundary would make
   this file slow for no real benefit, so most tests here prime a role's
   counter directly (via `db_session`, the same pattern
   `tests/test_family.py::test_expired_link` uses to force `FamilyLink`
   into an expired state) and then exercise the real HTTP endpoint at the
   boundary -- the code path under test is identical either way, since
   `_reserve_registration_slot` has no idea whether the count in front of it
   came from 99 prior real registrations or from a test fixture. One test
   (`test_real_sequential_registrations_increment_the_counter`) still drives
   several real registrations end-to-end to prove the increment itself
   works, not just the boundary check.

2. `email_service._smtp_send` already being a generic SMTP client (Gmail
   needs no new code, only configuration) -- these tests exercise it
   directly with a fake `smtplib.SMTP`, never a real socket, and never
   contact Gmail (ENVIRONMENT=test also makes `deliver()` skip
   `_smtp_send` entirely for the normal registration/resend/reset flows,
   which is exercised in `test_auth_flow.py` and unaffected by this file).
"""
from __future__ import annotations

import threading

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config import Settings
from app.models.registration_counter import RegistrationCounter
from app.services import email_service
from tests.helpers import auth_client, register

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _set_counter(db_session, role: str, count: int) -> None:
    stmt = (
        pg_insert(RegistrationCounter)
        .values(role=role, count=count)
        .on_conflict_do_update(index_elements=["role"], set_={"count": count})
    )
    db_session.execute(stmt)
    db_session.commit()


def _get_counter(db_session, role: str) -> int:
    row = db_session.scalar(select(RegistrationCounter).where(RegistrationCounter.role == role))
    return row.count if row is not None else 0


def _register(client, role: str, email: str):
    return register(client, email=email, role=role)


# --------------------------------------------------------------------------- #
# A-D: per-role boundary (99 -> 100 succeeds, 100 -> 101 fails)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("role", ["PATIENT", "FAMILY", "DONOR", "HOSPITAL"])
def test_role_allows_the_100th_registration(client, db_session, role) -> None:
    _set_counter(db_session, role, 99)
    resp = _register(client, role, f"num100-{role.lower()}@example.com")
    assert resp.status_code == 201, resp.text
    assert resp.json()["role"] == role
    assert _get_counter(db_session, role) == 100


@pytest.mark.parametrize("role", ["PATIENT", "FAMILY", "DONOR", "HOSPITAL"])
def test_role_rejects_the_101st_registration(client, db_session, role) -> None:
    _set_counter(db_session, role, 100)
    resp = _register(client, role, f"num101-{role.lower()}@example.com")
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    # The message names the *specific* role and the limit -- the spec is
    # explicit that a generic "maximum 100 accounts reached" would wrongly
    # imply a single global pool, which this is not.
    assert role in detail
    assert "100" in detail
    # The exact count -- no user was created, no counter drift.
    assert _get_counter(db_session, role) == 100


# --------------------------------------------------------------------------- #
# E: independence across roles
# --------------------------------------------------------------------------- #


def test_one_role_at_capacity_does_not_block_the_others(client, db_session) -> None:
    _set_counter(db_session, "PATIENT", 100)
    _set_counter(db_session, "FAMILY", 0)
    _set_counter(db_session, "DONOR", 0)
    _set_counter(db_session, "HOSPITAL", 0)

    assert _register(client, "PATIENT", "blocked-patient@example.com").status_code == 409
    assert _register(client, "DONOR", "still-open-donor@example.com").status_code == 201
    assert _register(client, "FAMILY", "still-open-family@example.com").status_code == 201
    assert _register(client, "HOSPITAL", "still-open-hospital@example.com").status_code == 201

    # PATIENT is still exactly at its cap -- the other three succeeding
    # didn't touch it, and it didn't touch them.
    assert _get_counter(db_session, "PATIENT") == 100
    assert _get_counter(db_session, "DONOR") == 1
    assert _get_counter(db_session, "FAMILY") == 1
    assert _get_counter(db_session, "HOSPITAL") == 1


def test_chained_independence_all_four_roles(client, db_session) -> None:
    """DONOR maxed -> FAMILY still works. FAMILY maxed -> HOSPITAL still
    works. HOSPITAL maxed -> PATIENT still works (with capacity)."""
    _set_counter(db_session, "DONOR", 100)
    assert _register(client, "FAMILY", "chain-family@example.com").status_code == 201

    _set_counter(db_session, "FAMILY", 100)
    assert _register(client, "HOSPITAL", "chain-hospital@example.com").status_code == 201

    _set_counter(db_session, "HOSPITAL", 100)
    assert _register(client, "PATIENT", "chain-patient@example.com").status_code == 201

    assert _get_counter(db_session, "DONOR") == 100
    assert _get_counter(db_session, "FAMILY") == 100
    assert _get_counter(db_session, "HOSPITAL") == 100
    assert _get_counter(db_session, "PATIENT") == 1


# --------------------------------------------------------------------------- #
# F: ADMIN stays forbidden and untouched by any of this
# --------------------------------------------------------------------------- #


def test_admin_self_registration_still_rejected_regardless_of_counters(client, db_session) -> None:
    # Even with every self-registerable role wide open, ADMIN must still
    # fail at the schema-validation layer (422), same as before Phase 11.
    for role in ("PATIENT", "FAMILY", "DONOR", "HOSPITAL"):
        _set_counter(db_session, role, 0)
    resp = register(client, email="wannabe-admin@example.com", role="ADMIN")
    assert resp.status_code == 422

    # No ADMIN counter row was ever created -- confirms ADMIN never
    # consumes (or even touches) any of the four limits.
    row = db_session.scalar(select(RegistrationCounter).where(RegistrationCounter.role == "ADMIN"))
    assert row is None


def test_admin_login_is_unaffected_by_registration_counters(db_session) -> None:
    """Login never touches `_reserve_registration_slot` at all -- an ADMIN
    account (created directly, the same way `scripts/seed_demo_data.py`
    creates the real deployment's demo admin -- never through
    `POST /auth/register`) can log in and reach /auth/me regardless of
    what any of the four counters currently hold."""
    for role in ("PATIENT", "FAMILY", "DONOR", "HOSPITAL"):
        _set_counter(db_session, role, 100)  # all four maxed out
    admin_client, _ = auth_client("ADMIN")
    assert admin_client.get("/auth/me").json()["role"] == "ADMIN"


# --------------------------------------------------------------------------- #
# G: concurrency -- same role, near the limit
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("role", ["PATIENT", "FAMILY", "DONOR", "HOSPITAL"])
def test_concurrent_same_role_registrations_never_exceed_the_cap(client, db_session, role) -> None:
    """count = 99, two simultaneous registrations for the SAME role ->
    exactly one succeeds, the other gets 409, final count is exactly 100."""
    _set_counter(db_session, role, 99)

    results: list[int] = []
    barrier = threading.Barrier(2)

    def _attempt(email: str) -> None:
        from fastapi.testclient import TestClient

        from app.main import app

        local_client = TestClient(app, base_url="http://testserver")
        barrier.wait()
        r = local_client.post(
            "/auth/register",
            json={
                "full_name": "Race User",
                "email": email,
                "password": "correct horse battery",
                "role": role,
            },
        )
        results.append(r.status_code)

    threads = [
        threading.Thread(target=_attempt, args=(f"race-a-{role.lower()}@example.com",)),
        threading.Thread(target=_attempt, args=(f"race-b-{role.lower()}@example.com",)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == [201, 409], results
    assert _get_counter(db_session, role) == 100


# --------------------------------------------------------------------------- #
# One real end-to-end pass (not primed state) proving the increment itself
# works from actual registrations, not just the boundary check.
# --------------------------------------------------------------------------- #


def test_real_sequential_registrations_increment_the_counter(client, db_session) -> None:
    assert _get_counter(db_session, "DONOR") == 0
    for i in range(5):
        resp = _register(client, "DONOR", f"real-seq-{i}@example.com")
        assert resp.status_code == 201, resp.text
    assert _get_counter(db_session, "DONOR") == 5


# --------------------------------------------------------------------------- #
# MAX_ACCOUNTS configuration
# --------------------------------------------------------------------------- #


def test_max_accounts_is_configurable() -> None:
    s = Settings(max_accounts=3)
    assert s.max_accounts == 3


@pytest.mark.parametrize("bad_value", [0, -1])
def test_max_accounts_rejects_non_positive_values(bad_value) -> None:
    with pytest.raises(ValueError):
        Settings(max_accounts=bad_value)


def test_max_accounts_rejects_non_integer_strings() -> None:
    with pytest.raises(ValueError):
        Settings(max_accounts="abc")


def test_lower_max_accounts_is_actually_enforced(client, db_session, monkeypatch) -> None:
    """A smaller configured limit changes the effective cap end to end --
    not just the config object in isolation."""
    from app.services import auth_service

    monkeypatch.setattr(auth_service.settings, "max_accounts", 2)
    _set_counter(db_session, "PATIENT", 0)

    assert _register(client, "PATIENT", "small-cap-1@example.com").status_code == 201
    assert _register(client, "PATIENT", "small-cap-2@example.com").status_code == 201
    resp = _register(client, "PATIENT", "small-cap-3@example.com")
    assert resp.status_code == 409
    assert "2" in resp.json()["detail"]


# --------------------------------------------------------------------------- #
# Gmail SMTP configuration (no real network call, ever)
# --------------------------------------------------------------------------- #


class _FakeSMTP:
    """Stands in for smtplib.SMTP -- records what would have happened
    without opening a socket or contacting any real mail server."""

    instances: list[_FakeSMTP] = []

    def __init__(self, host, port, timeout=10):
        self.host = host
        self.port = port
        self.starttls_called = False
        self.login_args: tuple[str, str] | None = None
        self.sent = []
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        self.starttls_called = True
        self.tls_context = context

    def login(self, username, password):
        self.login_args = (username, password)

    def send_message(self, msg):
        self.sent.append(msg)


def test_gmail_shaped_settings_load_correctly() -> None:
    s = Settings(
        mail_host="smtp.gmail.com",
        mail_port=587,
        mail_username="lifelink.sender@gmail.com",
        mail_password="not-a-real-app-password",
        mail_from="lifelink.sender@gmail.com",
        mail_starttls=True,
    )
    assert s.mail_host == "smtp.gmail.com"
    assert s.mail_port == 587
    assert s.mail_starttls is True
    assert s.mail_username == "lifelink.sender@gmail.com"
    assert s.mail_from == "lifelink.sender@gmail.com"


def test_smtp_send_uses_starttls_and_login_when_gmail_configured(monkeypatch) -> None:
    _FakeSMTP.instances.clear()
    fake_settings = Settings(
        mail_host="smtp.gmail.com", mail_port=587, mail_starttls=True,
        mail_username="lifelink.sender@gmail.com", mail_password="fake-app-password",
        mail_from="lifelink.sender@gmail.com",
    )
    monkeypatch.setattr(email_service, "settings", fake_settings)
    monkeypatch.setattr(email_service.smtplib, "SMTP", _FakeSMTP)

    email_service._smtp_send("someone@example.com", "Subject", "<p>hi</p>")

    assert len(_FakeSMTP.instances) == 1
    conn = _FakeSMTP.instances[0]
    assert conn.host == "smtp.gmail.com" and conn.port == 587
    assert conn.starttls_called is True
    # QA hardening: STARTTLS must use a VERIFYING context (cert + hostname).
    import ssl

    assert conn.tls_context is not None
    assert conn.tls_context.verify_mode == ssl.CERT_REQUIRED
    assert conn.tls_context.check_hostname is True
    assert conn.login_args == ("lifelink.sender@gmail.com", "fake-app-password")
    assert len(conn.sent) == 1


def test_smtp_send_skips_starttls_and_login_for_mailpit(monkeypatch) -> None:
    """Mailpit (no username/password, no STARTTLS) must keep working exactly
    as before -- Gmail is an additional configuration, not a replacement."""
    _FakeSMTP.instances.clear()
    mailpit_settings = Settings(
        mail_host="localhost", mail_port=1025, mail_starttls=False,
        mail_username=None, mail_password=None, mail_from="no-reply@lifelink.local",
    )
    monkeypatch.setattr(email_service, "settings", mailpit_settings)
    monkeypatch.setattr(email_service.smtplib, "SMTP", _FakeSMTP)

    email_service._smtp_send("someone@example.com", "Subject", "<p>hi</p>")

    conn = _FakeSMTP.instances[0]
    assert conn.host == "localhost" and conn.port == 1025
    assert conn.starttls_called is False
    assert conn.login_args is None


def test_smtp_credentials_never_appear_in_a_send_failure(monkeypatch) -> None:
    """If the SMTP server rejects the login, the raised exception must never
    contain the app password."""
    secret = "super-secret-app-password-xyz"

    class _FailingSMTP(_FakeSMTP):
        def login(self, username, password):
            raise OSError(f"535 5.7.8 Authentication failed for {username}")

    fake_settings = Settings(
        mail_host="smtp.gmail.com", mail_port=587, mail_starttls=True,
        mail_username="lifelink.sender@gmail.com", mail_password=secret,
        mail_from="lifelink.sender@gmail.com",
    )
    monkeypatch.setattr(email_service, "settings", fake_settings)
    monkeypatch.setattr(email_service.smtplib, "SMTP", _FailingSMTP)

    with pytest.raises(OSError) as exc_info:
        email_service._smtp_send("someone@example.com", "Subject", "<p>hi</p>")

    assert secret not in str(exc_info.value)
    assert secret not in repr(exc_info.value)
