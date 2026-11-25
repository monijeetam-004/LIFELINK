"""Production hardening -- real-time layer.

1. Family-link and certificate changes are evented, with audiences that
   mirror the REST read rules (family links: the two parties only;
   certificates: the donor + ADMIN).
2. Publication runs on the per-process background publisher, not in the
   request thread; a backlog left without any further write (crash, Redis
   outage) is drained by the publisher's periodic run.
3. The SSE endpoint shares ONE Redis subscription per process across all
   open streams; a stream that falls too far behind is told to resync; a
   lost Redis connection makes subscribers resync instead of hanging.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from datetime import date

from sqlalchemy import insert, select

from app.api.routes import events as events_route
from app.core.config import settings
from app.db.session import SessionLocal, engine
from app.models.realtime_event import RealtimeEvent
from app.models.user import User
from app.services import realtime_service
from tests.helpers import auth_client
from tests.test_phase_f_realtime_stream import Stream, _login, server  # noqa: F401

ORIGIN = {"Origin": "http://localhost:5173"}


def _events(resource_type: str) -> list[RealtimeEvent]:
    realtime_service.flush()
    db = SessionLocal()
    try:
        rows = list(
            db.scalars(
                select(RealtimeEvent)
                .where(RealtimeEvent.resource_type == resource_type)
                .order_by(RealtimeEvent.seq)
            ).all()
        )
        for r in rows:
            db.expunge(r)
        return rows
    finally:
        db.close()


def _uid(email: str) -> uuid.UUID:
    db = SessionLocal()
    try:
        return db.scalar(select(User.id).where(User.email == email.lower()))
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# 1. New event types
# --------------------------------------------------------------------------- #
def test_family_link_changes_are_evented_to_the_two_parties_only() -> None:
    family, femail = auth_client("FAMILY")
    patient, pemail = auth_client("PATIENT")
    outsider, oemail = auth_client("PATIENT")
    r = family.post("/family/links", json={"patient_email": pemail, "relationship": "son"})
    assert r.status_code == 201, r.text
    link_id = r.json()["id"]
    assert patient.post(f"/family/links/{link_id}/accept", json={}).status_code == 200

    evs = [e for e in _events("family_link") if str(e.resource_id) == link_id]
    assert len(evs) == 2  # request, accept
    for e in evs:
        assert e.event_type == "family_link.updated"
        assert set(e.audience_user_ids) == {_uid(femail), _uid(pemail)}
        assert _uid(oemail) not in e.audience_user_ids
        assert e.audience_roles == []


def test_certificate_issue_and_revoke_are_evented_to_donor_and_admin() -> None:
    donor, demail = auth_client("DONOR")
    admin, _ = auth_client("ADMIN")
    donor.put("/donors/me", json={"blood_type": "O_NEG"})
    d = donor.post(
        "/donors/me/donations",
        json={"donated_on": date.today().isoformat(), "units": 1, "hospital_name": "Metro"},
    )
    assert admin.post(f"/donations/{d.json()['id']}/verify", json={}).status_code == 200
    cert = donor.post("/certificates", json={"donation_id": d.json()["id"]})
    assert cert.status_code == 201, cert.text
    cid = cert.json()["id"]
    r = admin.post(f"/admin/certificates/{cid}/revoke", json={"reason": "issued in error"})
    assert r.status_code == 200, r.text

    evs = [e for e in _events("certificate") if str(e.resource_id) == cid]
    assert len(evs) == 2  # issue, revoke
    for e in evs:
        assert e.audience_user_ids == [_uid(demail)]
        assert e.audience_roles == ["ADMIN"]


# --------------------------------------------------------------------------- #
# 2. Background publisher
# --------------------------------------------------------------------------- #
def test_publication_runs_on_the_background_thread_not_the_request(monkeypatch) -> None:
    assert settings.realtime_publish_mode == "background"
    threads: list[str] = []
    real = realtime_service.publish_pending_now

    def spy(bind):
        threads.append(threading.current_thread().name)
        return real(bind)

    monkeypatch.setattr(realtime_service, "publish_pending_now", spy)
    patient, _ = auth_client("PATIENT")
    r = patient.post("/blood-requests", json={"blood_type": "O_NEG", "units_needed": 1})
    assert r.status_code == 201
    assert realtime_service.flush()
    assert threads and set(threads) == {"lifelink-realtime-publisher"}
    evs = [e for e in _events("blood_request") if str(e.resource_id) == r.json()["id"]]
    assert evs and evs[0].published_at is not None


def test_backlog_without_any_further_write_is_drained_by_the_periodic_run(monkeypatch) -> None:
    monkeypatch.setattr(settings, "realtime_publisher_interval_seconds", 0.2)
    realtime_service.publisher.wake()  # make sure the thread is running
    rid = uuid.uuid4()
    with engine.begin() as conn:  # a committed event nobody woke the publisher for
        conn.execute(
            insert(RealtimeEvent),
            [{"id": uuid.uuid4(), "event_type": "notification.created",
              "resource_type": "notification", "resource_id": rid}],
        )
    deadline = time.time() + 5
    published = None
    while time.time() < deadline:
        db = SessionLocal()
        try:
            published = db.scalar(
                select(RealtimeEvent.published_at).where(RealtimeEvent.resource_id == rid)
            )
        finally:
            db.close()
        if published is not None:
            break
        time.sleep(0.1)
    assert published is not None


# --------------------------------------------------------------------------- #
# 3. Shared SSE subscription
# --------------------------------------------------------------------------- #
def test_many_open_streams_share_one_redis_subscription(server) -> None:  # noqa: F811
    clients = [_login(server, "PATIENT")[0] for _ in range(4)]
    streams = [Stream(c) for c in clients]
    try:
        for s in streams:
            s.until(lambda e, d: e == "ready")
        assert events_route.open_redis_connections_for_streams() == 1
        # and live delivery still reaches the right stream
        r = clients[0].post("/blood-requests", json={"blood_type": "AB_POS", "units_needed": 1})
        rid = r.json()["id"]
        streams[0].until(lambda e, d: d.get("resource_id") == rid)
    finally:
        for s in streams:
            s._stop.set()
        for c in clients:
            c.close()


def test_lagging_stream_gets_resync_and_others_are_not_blocked() -> None:
    async def scenario():
        broker = events_route._Broker()
        slow: asyncio.Queue = asyncio.Queue(maxsize=2)
        fast: asyncio.Queue = asyncio.Queue(maxsize=100)
        broker.subscribers = {slow, fast}
        for i in range(5):
            broker._broadcast({"seq": i + 1})
        return [slow.get_nowait() for _ in range(slow.qsize())], fast.qsize()

    slow_items, fast_count = asyncio.run(scenario())
    assert events_route._RESYNC in slow_items
    assert fast_count == 5


def test_lost_redis_makes_subscribers_resync_instead_of_hanging(monkeypatch) -> None:
    monkeypatch.setattr(settings, "redis_url", "redis://127.0.0.1:1/0")  # nothing listens

    async def scenario():
        broker = events_route._Broker()
        q = await asyncio.wait_for(broker.subscribe(), timeout=5)
        item = await asyncio.wait_for(q.get(), timeout=5)
        broker.unsubscribe(q)
        if broker._task:
            broker._task.cancel()
        return item

    assert asyncio.run(scenario()) is events_route._RESYNC
