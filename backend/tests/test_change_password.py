from __future__ import annotations

from fastapi.testclient import TestClient

from tests.helpers import _PW, auth_client


def test_change_password_wrong_current_password_rejected() -> None:
    c, email = auth_client("PATIENT")

    resp = c.post(
        "/auth/change-password",
        json={"current_password": "totally wrong", "new_password": "a brand new passphrase"},
    )
    assert resp.status_code == 401

    # Password is unchanged -- old password still logs in, new one does not.
    fresh = TestClient(c.app, base_url="http://testserver")
    fresh.headers["Origin"] = "http://localhost:5173"
    assert fresh.post("/auth/login", json={"email": email, "password": _PW}).status_code == 200

    fresh2 = TestClient(c.app, base_url="http://testserver")
    fresh2.headers["Origin"] = "http://localhost:5173"
    assert (
        fresh2.post(
            "/auth/login", json={"email": email, "password": "a brand new passphrase"}
        ).status_code
        == 401
    )


def test_change_password_success_rotates_session_and_password() -> None:
    c, email = auth_client("PATIENT")
    old_cookie = c.cookies.get("lifelink_session")
    assert old_cookie is not None

    resp = c.post(
        "/auth/change-password",
        json={"current_password": _PW, "new_password": "a brand new passphrase"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["email"] == email

    new_cookie = c.cookies.get("lifelink_session")
    assert new_cookie is not None
    assert new_cookie != old_cookie

    # The client's own (now-rotated) cookie still works.
    assert c.get("/auth/me").status_code == 200

    # The OLD cookie no longer authenticates.
    old_client = TestClient(c.app, base_url="http://testserver")
    old_client.headers["Origin"] = "http://localhost:5173"
    old_client.cookies.set("lifelink_session", old_cookie)
    assert old_client.get("/auth/me").status_code == 401

    # Logging in with the new password succeeds, the old password fails.
    login_client = TestClient(c.app, base_url="http://testserver")
    login_client.headers["Origin"] = "http://localhost:5173"
    assert (
        login_client.post(
            "/auth/login", json={"email": email, "password": "a brand new passphrase"}
        ).status_code
        == 200
    )
    login_client2 = TestClient(c.app, base_url="http://testserver")
    login_client2.headers["Origin"] = "http://localhost:5173"
    assert (
        login_client2.post(
            "/auth/login", json={"email": email, "password": _PW}
        ).status_code
        == 401
    )


def test_change_password_rate_limited_after_repeated_wrong_attempts() -> None:
    c, _email = auth_client("PATIENT")

    # rl_change_password_account default is (5, 3600) -- exhaust it.
    last_status = None
    for _ in range(10):
        resp = c.post(
            "/auth/change-password",
            json={"current_password": "nope nope nope", "new_password": "another passphrase"},
        )
        last_status = resp.status_code
        if last_status == 429:
            break
    assert last_status == 429


def test_change_password_requires_auth() -> None:
    from app.main import app

    anon = TestClient(app, base_url="http://testserver")
    anon.headers["Origin"] = "http://localhost:5173"
    resp = anon.post(
        "/auth/change-password",
        json={"current_password": "whatever", "new_password": "another passphrase"},
    )
    assert resp.status_code == 401
