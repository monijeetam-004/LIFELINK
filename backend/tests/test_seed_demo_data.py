"""Idempotency test for scripts/seed_demo_data.py.

Runs the REAL seed script's `run()` function twice against the disposable
test database (not a copy or a mock) and asserts the second run creates
zero new rows anywhere it touches. Uses a monkeypatched, smaller
DONOR_COUNT so this stays fast in CI while exercising the exact same code
path a full 1000-donor run would -- the row-count math scales the same way
regardless of N. A full-scale run (DONOR_COUNT=1000) was also executed
manually against a real Postgres database during development, confirmed
`created` counts on the first run and `created: 0` / all `updated` on an
immediate second run, with identical row counts before and after -- see
the phase report for those numbers.
"""

from __future__ import annotations

from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.models.donation import Donation
from app.models.family import FamilyLink
from app.models.hospital import HospitalInventory, HospitalProfile
from app.models.profile import DonorProfile
from app.models.transport import TransportRoute
from app.models.user import User
from scripts import seed_demo_data


def _counts(db) -> dict[str, int]:
    return {
        "users": db.scalar(select(func.count()).select_from(User)) or 0,
        "donor_profiles": db.scalar(select(func.count()).select_from(DonorProfile)) or 0,
        "hospital_profiles": db.scalar(select(func.count()).select_from(HospitalProfile)) or 0,
        "hospital_inventory": db.scalar(select(func.count()).select_from(HospitalInventory)) or 0,
        "transport_routes": db.scalar(select(func.count()).select_from(TransportRoute)) or 0,
        "donations": db.scalar(select(func.count()).select_from(Donation)) or 0,
        "family_links": db.scalar(select(func.count()).select_from(FamilyLink)) or 0,
    }


def test_seed_is_idempotent(monkeypatch) -> None:
    # Small but non-trivial N: enough donors to exercise every branch
    # (donated-before / never-donated, available / not, verified / not)
    # deterministically, without the full 1000-row runtime.
    monkeypatch.setattr(seed_demo_data, "DONOR_COUNT", 25)

    db = SessionLocal()
    try:
        first = seed_demo_data.run(db)
        assert first["donors"]["created"] == 25
        assert first["facilities"]["created"] == 60
        assert first["routes"]["created"] > 0

        after_first = _counts(db)
        # 25 bulk donors + 60 bulk facilities + 5 demo accounts (one per role).
        assert after_first["users"] == 25 + 60 + 5

        second = seed_demo_data.run(db)
        assert second["donors"]["created"] == 0
        assert second["donors"]["updated"] == 25
        assert second["facilities"]["created"] == 0
        assert second["routes"]["created"] == 0

        after_second = _counts(db)
        assert after_second == after_first, (
            "re-running the seed script must not change row counts anywhere"
        )
    finally:
        db.close()


def test_seed_marks_every_account_as_synthetic(monkeypatch) -> None:
    monkeypatch.setattr(seed_demo_data, "DONOR_COUNT", 10)
    db = SessionLocal()
    try:
        seed_demo_data.run(db)
        non_synthetic = db.scalar(
            select(func.count())
            .select_from(User)
            .where(~User.email.like(f"%@{seed_demo_data.SYNTHETIC_DOMAIN_NAMED}"))
        )
        assert non_synthetic == 0
    finally:
        db.close()


def test_seed_demo_accounts_can_log_in(monkeypatch) -> None:
    """The demo accounts' password must actually work through the real
    HTTP login endpoint, not just exist as a row in the database."""
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(seed_demo_data, "DONOR_COUNT", 5)
    db = SessionLocal()
    try:
        seed_demo_data.run(db)
    finally:
        db.close()

    client = TestClient(app, base_url="http://testserver")
    client.headers["Origin"] = "http://localhost:5173"
    for role_email in [
        f"demo.patient@{seed_demo_data.SYNTHETIC_DOMAIN_NAMED}",
        f"demo.admin@{seed_demo_data.SYNTHETIC_DOMAIN_NAMED}",
        f"demo.hospital@{seed_demo_data.SYNTHETIC_DOMAIN_NAMED}",
    ]:
        r = client.post(
            "/auth/login", json={"email": role_email, "password": seed_demo_data.DEMO_PASSWORD}
        )
        assert r.status_code == 200, f"demo login failed for {role_email}: {r.text}"
        me = client.get("/auth/me")
        assert me.status_code == 200
        assert me.json()["email"] == role_email
        client.post("/auth/logout")


def test_demo_route_disruptions_are_reproducible_across_fresh_databases(monkeypatch) -> None:
    """Which routes the seed disrupts must depend only on stable natural keys
    (the endpoints' e-mails), never on the random UUIDs a fresh database
    assigns. Before this was fixed, three fresh seeds disrupted 6, 7 and 11
    of the same 85 routes."""
    k = seed_demo_data.demo_disruption_key
    assert k("a@x", "b@x") == k("b@x", "a@x")  # endpoint order is irrelevant
    assert k("a@x", "b@x") == k("a@x", "b@x")
    monkeypatch.setattr(seed_demo_data, "DONOR_COUNT", 5)
    db = SessionLocal()
    try:
        seed_demo_data.run(db)
        rows = db.execute(
            select(
                TransportRoute.status,
                TransportRoute.origin_facility_id,
                TransportRoute.destination_facility_id,
            )
        ).all()
        email_of = dict(
            db.execute(
                select(HospitalProfile.id, User.email).join(
                    User, User.id == HospitalProfile.user_id
                )
            ).all()
        )
        for status, o, d in rows:
            expected = k(email_of[o], email_of[d]) % 10 == 0
            assert (status.value == "DISRUPTED") == expected
    finally:
        db.close()
