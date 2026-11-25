"""Phase 7 -- production authentication hardening: the spec's explicit TEST list.

Coverage map (so this file doesn't duplicate what already exists elsewhere):
  - invalid password            -> tests/test_auth_flow.py::test_login_wrong_password_is_401_generic
  - wrong role                  -> tests/test_rate_limit_and_authz.py::test_wrong_role_gets_403
                                    and the full parametrized 403 matrix in tests/test_admin.py
  - expired token (email verify)      -> test_expired_email_verification_token_rejected (below)
  - expired token (reset OTP)         -> test_expired_password_reset_otp_rejected (below)
  - revoked token (session replay)    -> test_revoked_session_replay_after_logout_rejected,
                                          test_revoked_session_replay_after_password_reset_rejected
  - suspended account (fresh login)   -> test_suspended_account_rejects_fresh_login
                                          (an *already logged in* session getting rejected
                                          after suspension is covered by tests/test_admin.py)
  - reset token reuse                 -> test_reset_password_otp_cannot_be_reused
  - reset token expiration            -> test_expired_password_reset_otp_rejected
  - session expiration                -> test_session_expires_past_absolute_ttl_cap
  - concurrent sessions per policy    -> test_concurrent_sessions_are_independent
"""
from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.redis import redis_client
from app.core.time import utcnow
from app.main import app
from app.models.email_verification_token import EmailVerificationToken
from app.models.password_reset_otp import PasswordResetOTP
from tests.helpers import auth_client, extract_otp, extract_verification_token, register


def _fresh_client() -> TestClient:
    c = TestClient(app, base_url="http://testserver")
    c.headers["Origin"] = "http://localhost:5173"
    return c


# --------------------------------------------------------------------------- #
# Expired tokens
# --------------------------------------------------------------------------- #
def test_expired_email_verification_token_rejected(client: TestClient, mailbox, db_session) -> None:
    register(client, email="expiredverify@example.com")
    token = extract_verification_token(mailbox)

    row = db_session.query(EmailVerificationToken).one()
    row.expires_at = utcnow() - timedelta(seconds=1)
    db_session.commit()

    resp = client.post("/auth/verify-email", json={"token": token})
    assert resp.status_code == 400


def test_expired_password_reset_otp_rejected(client: TestClient, mailbox, db_session) -> None:
    register(client, email="expiredreset@example.com")
    mailbox.clear()
    client.post("/auth/forgot-password", json={"email": "expiredreset@example.com"})
    otp = extract_otp(mailbox)

    row = db_session.query(PasswordResetOTP).one()
    row.expires_at = utcnow() - timedelta(seconds=1)
    db_session.commit()

    resp = client.post(
        "/auth/reset-password",
        json={
            "email": "expiredreset@example.com",
            "otp": otp,
            "new_password": "does not matter here",
        },
    )
    assert resp.status_code == 400


# --------------------------------------------------------------------------- #
# Reset token (OTP) reuse
# --------------------------------------------------------------------------- #
def test_reset_password_otp_cannot_be_reused(client: TestClient, mailbox) -> None:
    register(client, email="reuseotp@example.com", password="original passphrase one")
    mailbox.clear()
    client.post("/auth/forgot-password", json={"email": "reuseotp@example.com"})
    otp = extract_otp(mailbox)

    first = client.post(
        "/auth/reset-password",
        json={
            "email": "reuseotp@example.com",
            "otp": otp,
            "new_password": "brand new passphrase one",
        },
    )
    assert first.status_code == 200, first.text

    # Same OTP, second attempt: the row is now consumed, so this must fail
    # exactly like an invalid code would (no distinct "already used" leak).
    second = client.post(
        "/auth/reset-password",
        json={
            "email": "reuseotp@example.com",
            "otp": otp,
            "new_password": "yet another passphrase",
        },
    )
    assert second.status_code == 400

    # Confirm it's truly a no-op: the password from the *first* reset still works.
    fresh = _fresh_client()
    resp = fresh.post(
        "/auth/login",
        json={"email": "reuseotp@example.com", "password": "brand new passphrase one"},
    )
    assert resp.status_code == 200


# --------------------------------------------------------------------------- #
# Revoked session replay
# --------------------------------------------------------------------------- #
def test_revoked_session_replay_after_logout_rejected(client: TestClient, mailbox) -> None:
    register(client, email="replaylogout@example.com", password="a solid passphrase")
    from tests.helpers import verify_user

    verify_user(client, mailbox)
    client.post(
        "/auth/login",
        json={"email": "replaylogout@example.com", "password": "a solid passphrase"},
    )
    raw_sid = client.cookies.get(settings.session_cookie_name)
    assert raw_sid is not None

    assert client.post("/auth/logout").status_code == 204

    # An attacker who captured the raw cookie value before logout must not be
    # able to replay it after the legitimate user logs out.
    replay = _fresh_client()
    replay.cookies.set(settings.session_cookie_name, raw_sid)
    assert replay.get("/auth/me").status_code == 401


def test_revoked_session_replay_after_password_reset_rejected(
    client: TestClient, mailbox
) -> None:
    from tests.helpers import verify_user

    register(client, email="replayreset@example.com", password="the original passphrase")
    verify_user(client, mailbox)
    client.post(
        "/auth/login",
        json={"email": "replayreset@example.com", "password": "the original passphrase"},
    )
    raw_sid = client.cookies.get(settings.session_cookie_name)
    assert raw_sid is not None

    mailbox.clear()
    client.post("/auth/forgot-password", json={"email": "replayreset@example.com"})
    otp = extract_otp(mailbox)
    resp = client.post(
        "/auth/reset-password",
        json={
            "email": "replayreset@example.com",
            "otp": otp,
            "new_password": "the new post-reset passphrase",
        },
    )
    assert resp.status_code == 200

    # The session that existed at reset time was mass-revoked -- replaying its
    # raw cookie value on a brand new client must not authenticate.
    replay = _fresh_client()
    replay.cookies.set(settings.session_cookie_name, raw_sid)
    assert replay.get("/auth/me").status_code == 401


# --------------------------------------------------------------------------- #
# Suspended account: fresh login attempt (not just an in-flight session)
# --------------------------------------------------------------------------- #
def test_suspended_account_rejects_fresh_login(client: TestClient, mailbox) -> None:
    from tests.helpers import verify_user

    register(client, email="suspendedlogin@example.com", password="a normal passphrase")
    verify_user(client, mailbox)

    admin, _ = auth_client("ADMIN")
    users = admin.get("/admin/users", params={"query": "suspendedlogin@example.com"}).json()
    target_id = next(u["id"] for u in users["items"] if u["email"] == "suspendedlogin@example.com")
    resp = admin.post(
        f"/admin/users/{target_id}/suspend", json={"reason": "test suspension for hardening"}
    )
    assert resp.status_code == 200, resp.text

    # The account was never logged in on `client` before the suspension --
    # this is a *fresh* login attempt with fully correct credentials.
    fresh = _fresh_client()
    resp = fresh.post(
        "/auth/login",
        json={"email": "suspendedlogin@example.com", "password": "a normal passphrase"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "This account has been disabled"


# --------------------------------------------------------------------------- #
# Session expiration: absolute lifetime cap
# --------------------------------------------------------------------------- #
def test_session_expires_past_absolute_ttl_cap(client: TestClient, mailbox) -> None:
    from tests.helpers import verify_user

    register(client, email="absolutecap@example.com", password="a normal passphrase two")
    verify_user(client, mailbox)
    client.post(
        "/auth/login",
        json={"email": "absolutecap@example.com", "password": "a normal passphrase two"},
    )
    assert client.get("/auth/me").status_code == 200

    sid = client.cookies.get(settings.session_cookie_name)
    assert sid is not None

    # Backdate this session's issued_at past the absolute cap, simulating a
    # session id kept continuously "warm" (touched) by an attacker for far
    # longer than any legitimate session should live.
    too_old = utcnow() - timedelta(seconds=settings.session_absolute_ttl_seconds + 60)
    redis_client.hset(f"session:{sid}", "issued_at", too_old.isoformat())

    resp = client.get("/auth/me")
    assert resp.status_code == 401

    # The absolute-cap rejection also deletes the now-expired session record.
    assert client.get("/auth/me").status_code == 401


# --------------------------------------------------------------------------- #
# Concurrent sessions
# --------------------------------------------------------------------------- #
def test_concurrent_sessions_are_independent(client: TestClient, mailbox) -> None:
    """Policy: unlimited concurrent sessions per user; each is independently
    valid until its own logout/revocation event (no single-session-per-user
    enforcement). Logging in again does not invalidate a prior session, and
    logging out of one session does not affect a sibling session."""
    from tests.helpers import verify_user

    register(client, email="concurrent@example.com", password="a shared passphrase")
    verify_user(client, mailbox)

    session_a = _fresh_client()
    session_b = _fresh_client()
    login_payload = {"email": "concurrent@example.com", "password": "a shared passphrase"}
    assert session_a.post("/auth/login", json=login_payload).status_code == 200
    assert session_b.post("/auth/login", json=login_payload).status_code == 200

    # Both sessions are simultaneously valid.
    assert session_a.get("/auth/me").status_code == 200
    assert session_b.get("/auth/me").status_code == 200

    # Logging out of session A does not touch session B.
    assert session_a.post("/auth/logout").status_code == 204
    assert session_a.get("/auth/me").status_code == 401
    assert session_b.get("/auth/me").status_code == 200
