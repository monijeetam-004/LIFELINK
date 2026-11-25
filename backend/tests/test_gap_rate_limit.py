"""The login rate limiters, at strict values (the suite-wide RL_LOGIN_IP
relaxation in conftest.py exists only because every TestClient shares one
source address)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from tests.helpers import seed_verified_user


def test_login_ip_limit_returns_429_with_retry_after(monkeypatch):
    monkeypatch.setattr(settings, "rl_login_ip", (3, 300))
    c = TestClient(app, base_url="http://testserver")
    for i in range(3):
        seed_verified_user(f"rl-{i}@example.com")
        assert (
            c.post(
                "/auth/login",
                json={"email": f"rl-{i}@example.com", "password": "a decent passphrase"},
            ).status_code
            == 200
        )
    seed_verified_user("rl-x@example.com")
    r = c.post("/auth/login", json={"email": "rl-x@example.com", "password": "a decent passphrase"})
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0


def test_login_account_limit_blocks_password_guessing():
    seed_verified_user("victim@example.com")
    c = TestClient(app, base_url="http://testserver")
    limit = settings.rl_login_account[0]
    for _ in range(limit):
        assert (
            c.post(
                "/auth/login", json={"email": "victim@example.com", "password": "wrong password!"}
            ).status_code
            == 401
        )
    r = c.post(
        "/auth/login", json={"email": "victim@example.com", "password": "a decent passphrase"}
    )
    assert r.status_code == 429  # even the right password is refused while locked


def test_spoofed_x_forwarded_for_cannot_bypass_the_login_ip_limit(monkeypatch):
    """S12: a client rotating X-Forwarded-For used to get a fresh per-IP
    budget on every request. Only the ASGI server may resolve proxies."""
    monkeypatch.setattr(settings, "rl_login_ip", (3, 300))
    c = TestClient(app, base_url="http://testserver")
    codes = []
    for i in range(5):
        seed_verified_user(f"xff-{i}@example.com")
        codes.append(
            c.post(
                "/auth/login",
                json={"email": f"xff-{i}@example.com", "password": "a decent passphrase"},
                headers={"X-Forwarded-For": f"203.0.113.{i}"},
            ).status_code
        )
    assert codes == [200, 200, 200, 429, 429]


def test_audit_records_do_not_trust_a_client_supplied_forwarded_header():
    from sqlalchemy import select

    from app.db.session import SessionLocal
    from app.models.audit_log import AuditLog

    seed_verified_user("xff-audit@example.com")
    c = TestClient(app, base_url="http://testserver")
    c.post(
        "/auth/login",
        json={"email": "xff-audit@example.com", "password": "a decent passphrase"},
        headers={"X-Forwarded-For": "198.51.100.7"},
    )
    with SessionLocal() as db:
        ips = set(db.scalars(select(AuditLog.ip)).all())
    assert "198.51.100.7" not in ips
