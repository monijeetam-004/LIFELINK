"""Phase I -- demo activity seed (requests, emergencies, notifications).

Runs the real seed functions against the test database: the demo
accounts, then the API-driven activity twice. The second run must change
nothing (the API's own idempotency keys + state checks).
"""

from __future__ import annotations

import random

from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.models.blood_request import BloodRequest, DonorResponse
from app.models.emergency import Emergency
from app.models.enums import EmergencyStatus
from app.models.notification import Notification
from scripts import seed_demo_data as seed


def _counts(db) -> tuple[int, int, int, int]:
    return (
        db.scalar(select(func.count()).select_from(BloodRequest)),
        db.scalar(select(func.count()).select_from(Emergency)),
        db.scalar(select(func.count()).select_from(DonorResponse)),
        db.scalar(select(func.count()).select_from(Notification)),
    )


def test_demo_activity_is_real_and_idempotent() -> None:
    db = SessionLocal()
    try:
        seed.seed_demo_accounts(db, random.Random(seed.SEED))
        first = seed.seed_demo_activity()
        after_first = _counts(db)
        second = seed.seed_demo_activity()
        db.expire_all()
        after_second = _counts(db)

        assert first == second
        assert after_first == after_second
        requests, emergencies, responses, notifications = after_second
        assert (requests, emergencies, responses) == (1, 1, 1)
        assert notifications >= 3  # produced by the real flows, not inserted

        req = db.scalar(select(BloodRequest))
        assert req.idempotency_key == "demo-seed-blood-request-1"
        assert "Demo data" in (req.note or "")
        em = db.scalar(select(Emergency))
        assert em.status == EmergencyStatus.ACKNOWLEDGED
        assert "Demo data" in (em.note or "")
    finally:
        db.close()
