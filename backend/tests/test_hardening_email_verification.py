"""Production hardening -- email verification, end to end and server-enforced.

Registration -> verification email (real message in the test mailbox) ->
single-use, expiring, hashed token -> account becomes usable. Before this
hardening the backend did not require a verified address for anything:
only the frontend redirected unverified users. Now every authenticated
route except "who am I", sign out and change password answers 403 for an
unverified account.
"""

from __future__ import annotations

from datetime import timedelta

from app.core.security import sign
from app.core.time import utcnow
from app.models.email_verification_token import EmailVerificationToken
from tests.helpers import extract_verification_token, register

ORIGIN = {"Origin": "http://localhost:5173"}
PW = "a fine passphrase here"


def _register_and_login(client, mailbox, email: str, role: str = "DONOR") -> str:
    client.headers.update(ORIGIN)
    assert register(client, email=email, password=PW, role=role).status_code == 201
    token = extract_verification_token(mailbox)
    assert client.post("/auth/login", json={"email": email, "password": PW}).status_code == 200
    return token


def test_unverified_account_is_blocked_server_side_until_verified(client, mailbox) -> None:
    token = _register_and_login(client, mailbox, "unverified@example.com")

    me = client.get("/auth/me")
    assert me.status_code == 200 and me.json()["is_email_verified"] is False
    blocked = client.get("/donors/me")
    assert blocked.status_code == 403
    assert blocked.headers.get("X-LifeLink-Reason") == "email-unverified"
    assert client.get("/notifications").status_code == 403
    assert (
        client.post(
            "/donors/me/donations", json={"donated_on": "2026-01-01", "units": 1}
        ).status_code
        == 403
    )

    assert client.post("/auth/verify-email", json={"token": token}).status_code == 200
    assert client.get("/donors/me").status_code == 200  # same session, now activated
    assert client.get("/auth/me").json()["is_email_verified"] is True


def test_unverified_account_may_still_sign_out_and_change_password(client, mailbox) -> None:
    _register_and_login(client, mailbox, "stillallowed@example.com")
    r = client.post(
        "/auth/change-password",
        json={"current_password": PW, "new_password": "another fine passphrase"},
    )
    assert r.status_code == 200, r.text
    assert client.post("/auth/logout").status_code in (200, 204)


def test_token_is_single_use_hashed_and_expiring(client, mailbox, db_session) -> None:
    token = _register_and_login(client, mailbox, "single@example.com")
    row = db_session.query(EmailVerificationToken).one()
    assert row.token_hash == sign(token) and row.token_hash != token  # stored hashed, never raw
    assert client.post("/auth/verify-email", json={"token": token}).status_code == 200
    reuse = client.post("/auth/verify-email", json={"token": token})
    assert reuse.status_code == 400


def test_expired_and_invalid_tokens_are_rejected_and_do_not_activate(
    client, mailbox, db_session
) -> None:
    token = _register_and_login(client, mailbox, "expired@example.com")
    row = db_session.query(EmailVerificationToken).one()
    row.expires_at = utcnow() - timedelta(seconds=1)
    db_session.commit()
    assert client.post("/auth/verify-email", json={"token": token}).status_code == 400
    assert client.post("/auth/verify-email", json={"token": "not-a-real-token"}).status_code == 400
    assert client.get("/auth/me").json()["is_email_verified"] is False
    assert client.get("/donors/me").status_code == 403


def test_resend_supersedes_the_previous_token(client, mailbox) -> None:
    first = _register_and_login(client, mailbox, "resend@example.com")
    r = client.post("/auth/verify-email/resend", json={"email": "resend@example.com"})
    assert r.status_code == 202
    second = extract_verification_token(mailbox)
    assert second != first
    assert client.post("/auth/verify-email", json={"token": first}).status_code == 400
    assert client.post("/auth/verify-email", json={"token": second}).status_code == 200


def test_resend_is_generic_for_unknown_and_already_verified_accounts(client, mailbox) -> None:
    client.headers.update(ORIGIN)
    before = len(mailbox)
    r = client.post("/auth/verify-email/resend", json={"email": "nobody-here@example.com"})
    assert r.status_code == 202
    assert len(mailbox) == before  # no email, same answer: no account enumeration

    token = _register_and_login(client, mailbox, "already@example.com")
    client.post("/auth/verify-email", json={"token": token})
    count = len(mailbox)
    r2 = client.post("/auth/verify-email/resend", json={"email": "already@example.com"})
    assert r2.status_code == 202 and r2.json()["message"] == r.json()["message"]
    assert len(mailbox) == count


def test_verification_link_points_at_the_frontend_and_carries_no_session(client, mailbox) -> None:
    _register_and_login(client, mailbox, "link@example.com")
    html = mailbox[-1]["html"]
    assert "/verify-email?token=" in html
    assert "lifelink_session" not in html
