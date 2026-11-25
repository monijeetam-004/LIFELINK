from __future__ import annotations

from fastapi.testclient import TestClient

from tests.helpers import extract_otp, register, verify_user


def _register_verified_logged_in(client: TestClient, mailbox: list[dict], email: str) -> None:
    register(client, email=email, password="original passphrase")
    verify_user(client, mailbox)
    client.post("/auth/login", json={"email": email, "password": "original passphrase"})


def test_forgot_password_is_generic_and_sends_otp(
    client: TestClient, mailbox: list[dict]
) -> None:
    register(client, email="gwen@example.com")
    mailbox.clear()

    resp = client.post("/auth/forgot-password", json={"email": "gwen@example.com"})
    assert resp.status_code == 202
    assert "If an account exists" in resp.json()["message"]
    assert len(mailbox) == 1
    assert extract_otp(mailbox).isdigit()


def test_forgot_password_unknown_email_still_202_no_email(
    client: TestClient, mailbox: list[dict]
) -> None:
    resp = client.post("/auth/forgot-password", json={"email": "nobody@example.com"})
    assert resp.status_code == 202
    assert mailbox == []


def test_reset_password_changes_password_and_invalidates_sessions(
    client: TestClient, mailbox: list[dict]
) -> None:
    _register_verified_logged_in(client, mailbox, "hank@example.com")
    assert client.get("/auth/me").status_code == 200  # session active

    mailbox.clear()
    client.post("/auth/forgot-password", json={"email": "hank@example.com"})
    otp = extract_otp(mailbox)

    resp = client.post(
        "/auth/reset-password",
        json={
            "email": "hank@example.com",
            "otp": otp,
            "new_password": "brand new passphrase",
        },
    )
    assert resp.status_code == 200

    # Existing session is now invalid.
    assert client.get("/auth/me").status_code == 401
    # Old password no longer works.
    assert (
        client.post(
            "/auth/login",
            json={"email": "hank@example.com", "password": "original passphrase"},
        ).status_code
        == 401
    )
    # New password works.
    assert (
        client.post(
            "/auth/login",
            json={"email": "hank@example.com", "password": "brand new passphrase"},
        ).status_code
        == 200
    )


def test_reset_password_wrong_otp_rejected(client: TestClient, mailbox: list[dict]) -> None:
    register(client, email="ivy@example.com")
    mailbox.clear()
    client.post("/auth/forgot-password", json={"email": "ivy@example.com"})

    resp = client.post(
        "/auth/reset-password",
        json={"email": "ivy@example.com", "otp": "000000", "new_password": "nope nope nope"},
    )
    assert resp.status_code == 400


def test_verify_email_resend(client: TestClient, mailbox: list[dict]) -> None:
    register(client, email="jack@example.com")
    mailbox.clear()
    resp = client.post("/auth/verify-email/resend", json={"email": "jack@example.com"})
    assert resp.status_code == 202
    assert len(mailbox) == 1
