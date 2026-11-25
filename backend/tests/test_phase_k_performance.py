"""Phase K -- regression tests for the performance findings.

Each guards a defect measured on the Phase K volume database
(docs/LIFELINK_PHASE_K_PERFORMANCE_REPORT.md):

  1. Real-time publisher drained only one batch (500 events) per commit:
     a larger burst stayed unsequenced/unpublished until unrelated later
     commits happened.
  2. Real-time retention never ran in ordinary traffic (trigger was
     `events sequenced in this run % 97 == 0`).
  3. GET /donors/me/requests issued 3 queries per open request.
  4. GET /emergencies/active issued 2 queries per open emergency.

(3) and (4) are asserted as "statement count does not grow with the number
of rows", the same style as Phase 27's bounded fan-out test.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, insert, select, update

from app.core.time import utcnow
from app.db.session import SessionLocal, engine
from app.models.realtime_event import RealtimeEvent
from app.services import realtime_service
from tests.helpers import auth_client
from tests.test_phase27_notification_scaling import count_queries


def _counts():
    db = SessionLocal()
    try:
        unseq = db.scalar(
            select(func.count()).select_from(RealtimeEvent).where(RealtimeEvent.seq.is_(None))
        )
        pending = db.scalar(
            select(func.count())
            .select_from(RealtimeEvent)
            .where(RealtimeEvent.seq.is_not(None), RealtimeEvent.published_at.is_(None))
        )
        total = db.scalar(select(func.count()).select_from(RealtimeEvent))
        return unseq, pending, total
    finally:
        db.close()


def _insert_raw_events(n: int, **values) -> None:
    rows = [
        {
            "id": uuid.uuid4(),
            "event_type": "notification.created",
            "resource_type": "notification",
            "resource_id": uuid.uuid4(),
            **values,
        }
        for _ in range(n)
    ]
    with engine.begin() as conn:
        conn.execute(insert(RealtimeEvent), rows)


@pytest.fixture
def inline_publisher(monkeypatch):
    """These tests drive the publisher directly; keep the background thread's
    periodic run out of the way (it only publishes in background mode)."""
    from app.core.config import settings

    realtime_service.flush()
    monkeypatch.setattr(settings, "realtime_publish_mode", "inline")


def test_publisher_drains_a_burst_larger_than_one_batch(inline_publisher) -> None:
    burst = realtime_service._BATCH * 2 + 37
    _insert_raw_events(burst)  # committed without the ORM hook: a pure backlog
    assert _counts()[0] == burst

    sequenced, published = realtime_service.publish_pending_now(engine)

    assert (sequenced, published) == (burst, burst)
    assert _counts()[:2] == (0, 0)
    db = SessionLocal()
    try:
        seqs = sorted(db.scalars(select(RealtimeEvent.seq)).all())
    finally:
        db.close()
    assert seqs == list(range(1, burst + 1))  # still gap-free and ordered


def test_retention_runs_in_ordinary_single_event_traffic(monkeypatch, inline_publisher) -> None:
    monkeypatch.setattr(realtime_service, "PRUNE_EVERY", 5)
    # 20 old, already-published events (past retention) ...
    _insert_raw_events(20)
    realtime_service.publish_pending_now(engine)
    old = utcnow() - timedelta(hours=realtime_service.settings.realtime_retention_hours + 1)
    with engine.begin() as conn:
        conn.execute(update(RealtimeEvent).values(created_at=old))
    # ... and one old event that was NEVER published: retention must not drop it.
    _insert_raw_events(1, created_at=old)

    # Ordinary traffic: one event per commit, as a single API write produces.
    for _ in range(6):
        _insert_raw_events(1)
        realtime_service.publish_pending_now(engine)

    db = SessionLocal()
    try:
        remaining_old = db.scalar(
            select(func.count()).select_from(RealtimeEvent).where(RealtimeEvent.created_at <= old)
        )
        recent = db.scalar(
            select(func.count()).select_from(RealtimeEvent).where(RealtimeEvent.created_at > old)
        )
    finally:
        db.close()
    # The old never-published one was published by the first run after it was
    # inserted, so it is old AND published -> pruned too. Nothing recent is.
    assert remaining_old == 0
    assert recent == 6


def test_retention_never_deletes_unpublished_events(monkeypatch, inline_publisher) -> None:
    monkeypatch.setattr(realtime_service, "PRUNE_EVERY", 1)
    old = utcnow() - timedelta(hours=realtime_service.settings.realtime_retention_hours + 1)
    with engine.connect() as conn:
        realtime_service._prune(conn)
        conn.commit()
    _insert_raw_events(3, created_at=old)  # unsequenced, unpublished
    with engine.connect() as conn:
        realtime_service._prune(conn)
        conn.commit()
    assert _counts() == (3, 0, 3)


def _donor_feed_statements(open_requests: int) -> int:
    donor, _ = auth_client("DONOR")
    donor.put("/donors/me", json={"blood_type": "O_NEG", "is_available": True})
    patient, _ = auth_client("PATIENT")
    for i in range(open_requests):
        r = patient.post(
            "/blood-requests",
            json={
                "blood_type": "A_POS",
                "units_needed": 1,
                "idempotency_key": f"k-{uuid.uuid4()}-{i}",
            },
        )
        assert r.status_code == 201, r.text
    with count_queries() as stmts:
        resp = donor.get("/donors/me/requests")
        assert resp.status_code == 200
    assert len(resp.json()) >= open_requests
    return len(stmts)


def test_donor_feed_statement_count_does_not_grow_with_open_requests() -> None:
    small = _donor_feed_statements(2)
    large = _donor_feed_statements(9)
    assert large == small, (small, large)


def _active_emergency_statements(n: int) -> int:
    donor, _ = auth_client("DONOR")
    donor.put("/donors/me", json={"blood_type": "O_NEG", "is_available": True})
    for _ in range(n):
        patient, _ = auth_client("PATIENT")
        r = patient.post("/emergencies", json={"blood_type": "A_POS", "location_text": "Metro"})
        assert r.status_code == 201, r.text
    with count_queries() as stmts:
        resp = donor.get("/emergencies/active")
        assert resp.status_code == 200
    assert len(resp.json()) >= n
    return len(stmts)


def test_active_emergencies_statement_count_does_not_grow_with_emergencies() -> None:
    small = _active_emergency_statements(2)
    large = _active_emergency_statements(7)
    assert large == small, (small, large)
