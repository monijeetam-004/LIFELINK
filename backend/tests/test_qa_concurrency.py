"""Final QA -- two concurrency gaps not covered by earlier race tests:
duplicate-click on "mark notification read" and the same email being
registered twice at the same instant. Real threads, real PostgreSQL."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.main import app
from app.models.notification import Notification
from app.models.user import User
from tests.helpers import auth_client

ORIGIN = {"Origin": "http://localhost:5173"}


def _race(n, fn):
    barrier = threading.Barrier(n)

    def run(i):
        barrier.wait()
        return fn(i)

    with ThreadPoolExecutor(n) as pool:
        return list(pool.map(run, range(n)))


def test_double_click_mark_read_is_idempotent_and_never_500() -> None:
    donor, _ = auth_client("DONOR")
    patient, _ = auth_client("PATIENT")
    # Audit D4 (2026-10-31): alerts apply the distance policy; same city.
    donor.put("/donors/me", json={"blood_type": "O_NEG", "is_available": True, "city": "Metro"})
    patient.post("/blood-requests", json={"blood_type": "O_NEG", "units_needed": 1,
                                          "city": "Metro"})
    notes = donor.get("/notifications").json()
    assert notes, "a matching donor is notified of the request"
    nid = notes[0]["id"]
    cookie = donor.cookies

    def click(_):
        c = TestClient(app, base_url="http://testserver", cookies=cookie)
        return c.post(f"/notifications/{nid}/read", headers=ORIGIN).status_code

    codes = _race(8, click)
    assert set(codes) == {200}, codes
    db = SessionLocal()
    try:
        read_at = db.scalar(select(Notification.read_at).where(Notification.id == nid))
    finally:
        db.close()
    assert read_at is not None
    assert donor.get("/notifications/unread-count").json()["unread"] == len(notes) - 1


def test_same_email_registered_twice_concurrently_creates_one_account() -> None:
    email = "race.register@example.com"

    def register(_):
        c = TestClient(app, base_url="http://testserver")
        return c.post("/auth/register", headers=ORIGIN, json={
            "full_name": "Race", "email": email, "password": "Race-register-2026",
            "role": "DONOR"}).status_code

    codes = _race(6, register)
    assert codes.count(201) == 1, codes
    # 429: the per-IP registration rate limit may also refuse some -- fine;
    # what must never happen is a 500 or a second account.
    assert all(c in (201, 409, 429) for c in codes), codes
    db = SessionLocal()
    try:
        assert db.scalar(select(func.count()).select_from(User).where(User.email == email)) == 1
    finally:
        db.close()
