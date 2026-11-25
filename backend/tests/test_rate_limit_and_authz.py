from __future__ import annotations

from fastapi import Depends
from fastapi.testclient import TestClient

from app.api.deps import get_current_user, require_roles
from app.main import app
from app.models.user import User, UserRole
from tests.helpers import register, seed_verified_user, verify_user

# --- probe routes exercising the auth dependencies ----------------------- #


@app.get("/_test/any-authed")
def _any_authed(user: User = Depends(get_current_user)) -> dict:
    return {"ok": True}


@app.get("/_test/admin-area")
def _admin_area(user: User = Depends(require_roles(UserRole.ADMIN))) -> dict:
    return {"ok": True, "role": user.role.value}


def _login(client: TestClient, mailbox: list[dict], email: str, role: str) -> None:
    if role in {"PATIENT", "FAMILY", "DONOR", "HOSPITAL"}:
        register(client, email=email, password="a decent passphrase", role=role)
        verify_user(client, mailbox)
    else:  # ADMIN and any non-self-registerable role
        seed_verified_user(email, "a decent passphrase", role)
    client.post(
        "/auth/login", json={"email": email, "password": "a decent passphrase"}
    )


# --- rate limiting ------------------------------------------------------- #


def test_login_rate_limited_per_account(client: TestClient) -> None:
    register(client, email="kim@example.com", password="the real passphrase")
    codes = [
        client.post(
            "/auth/login", json={"email": "kim@example.com", "password": "bad"}
        ).status_code
        for _ in range(7)
    ]
    assert codes.count(401) == 5  # settings.rl_login_account = (5, 300)
    assert codes[-1] == 429


def test_register_rate_limited_per_ip(client: TestClient) -> None:
    codes = [register(client, email=f"user{i}@example.com").status_code for i in range(7)]
    assert 429 in codes  # settings.rl_register_ip = (5, 3600)


# --- authorization ----------------------------------------------------- #


def test_unauthenticated_gets_401(client: TestClient) -> None:
    assert client.get("/_test/any-authed").status_code == 401
    assert client.get("/_test/admin-area").status_code == 401


def test_wrong_role_gets_403(client: TestClient, mailbox: list[dict]) -> None:
    _login(client, mailbox, "pat@example.com", "PATIENT")
    assert client.get("/_test/any-authed").status_code == 200
    assert client.get("/_test/admin-area").status_code == 403


def test_correct_role_allowed(client: TestClient, mailbox: list[dict]) -> None:
    _login(client, mailbox, "adm@example.com", "ADMIN")
    resp = client.get("/_test/admin-area")
    assert resp.status_code == 200
    assert resp.json()["role"] == "ADMIN"
