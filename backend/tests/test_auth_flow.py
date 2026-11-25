from __future__ import annotations

from fastapi.testclient import TestClient

from tests.helpers import extract_verification_token, register, verify_user


def test_register_creates_unverified_user_and_sends_email(
    client: TestClient, mailbox: list[dict]
) -> None:
    resp = register(client, email="alice@example.com")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["email"] == "alice@example.com"
    assert body["is_email_verified"] is False
    assert body["role"] == "PATIENT"
    assert "password" not in body and "password_hash" not in body
    assert len(mailbox) == 1
    assert "verify-email?token=" in mailbox[-1]["html"]


def test_duplicate_registration_rejected(client: TestClient) -> None:
    register(client, email="dup@example.com")
    resp = register(client, email="DUP@example.com")  # case-insensitive
    assert resp.status_code == 409


def test_register_rejects_short_password(client: TestClient) -> None:
    resp = register(client, password="short")
    assert resp.status_code == 422


def test_cannot_self_register_as_admin(client: TestClient) -> None:
    resp = register(client, email="wannabe@example.com", role="ADMIN")
    assert resp.status_code == 422


def test_email_verification_flow(client: TestClient, mailbox: list[dict]) -> None:
    register(client, email="bob@example.com")
    token = extract_verification_token(mailbox)

    resp = client.post("/auth/verify-email", json={"token": token})
    assert resp.status_code == 200

    # token is single-use
    resp = client.post("/auth/verify-email", json={"token": token})
    assert resp.status_code == 400


def test_login_sets_cookie_and_me_works(client: TestClient, mailbox: list[dict]) -> None:
    register(client, email="carol@example.com", password="a strong passphrase")
    verify_user(client, mailbox)

    resp = client.post(
        "/auth/login",
        json={"email": "carol@example.com", "password": "a strong passphrase"},
    )
    assert resp.status_code == 200
    assert "lifelink_session" in resp.cookies

    me = client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "carol@example.com"
    assert me.json()["is_email_verified"] is True


def test_login_wrong_password_is_401_generic(client: TestClient) -> None:
    register(client, email="dave@example.com", password="the right password")
    resp = client.post(
        "/auth/login", json={"email": "dave@example.com", "password": "wrong password"}
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid email or password"


def test_logout_clears_session(client: TestClient, mailbox: list[dict]) -> None:
    register(client, email="erin@example.com", password="passphrase one two")
    verify_user(client, mailbox)
    client.post(
        "/auth/login",
        json={"email": "erin@example.com", "password": "passphrase one two"},
    )
    assert client.get("/auth/me").status_code == 200

    resp = client.post("/auth/logout")
    assert resp.status_code == 204
    assert client.get("/auth/me").status_code == 401


def test_me_requires_auth(client: TestClient) -> None:
    assert client.get("/auth/me").status_code == 401


def test_unverified_user_can_still_login_but_flag_is_false(
    client: TestClient, mailbox: list[dict]
) -> None:
    register(client, email="frank@example.com", password="frank passphrase")
    resp = client.post(
        "/auth/login",
        json={"email": "frank@example.com", "password": "frank passphrase"},
    )
    assert resp.status_code == 200
    assert resp.json()["is_email_verified"] is False
