"""Phase F -- real-time event layer: creation, ordering, publication,
authorization, replay. (Live SSE streaming over a real HTTP server is in
tests/test_phase_f_realtime_stream.py.)

Everything here runs against the real Postgres test database and real
Redis; nothing is mocked except where a test deliberately simulates a
Redis outage.
"""

from __future__ import annotations

import json
import threading
import time
import uuid

import pytest
import redis
from sqlalchemy import func, select

from app.api.routes import blood_requests as br_routes
from app.api.routes import emergencies as em_routes
from app.core.config import settings
from app.db.session import SessionLocal, engine
from app.models.blood_request import BloodRequest, BloodRequestEvent
from app.models.emergency import Emergency
from app.models.realtime_event import RealtimeEvent
from app.models.user import User, UserRole
from app.services import realtime_service
from app.services.realtime_audience import audience_for
from tests.helpers import auth_client


def _rows(resource_type: str | None = None, resource_id=None) -> list[RealtimeEvent]:
    # Hardening: publication now runs on a background thread after commit;
    # wait for it before inspecting outbox state (a sync point, not a change
    # to any expectation).
    realtime_service.flush()
    db = SessionLocal()
    try:
        stmt = select(RealtimeEvent).order_by(RealtimeEvent.seq.nulls_last())
        if resource_type:
            stmt = stmt.where(RealtimeEvent.resource_type == resource_type)
        if resource_id:
            stmt = stmt.where(RealtimeEvent.resource_id == uuid.UUID(str(resource_id)))
        rows = list(db.scalars(stmt).all())
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


def _hospital(city="Metro", name=None, stock=0):
    c, email = auth_client("HOSPITAL")
    c.put("/hospitals/me", json={"name": name or f"H-{uuid.uuid4().hex[:6]}", "city": city})
    if stock:
        c.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": stock})
    return c, email


def _request(patient, units=2, city="Metro"):
    r = patient.post(
        "/blood-requests", json={"blood_type": "O_NEG", "city": city, "units_needed": units}
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


# --------------------------------------------------------------------------- #
# Creation: same transaction, deduplicated, never for rolled-back work
# --------------------------------------------------------------------------- #
def test_business_change_creates_sequenced_published_event() -> None:
    patient, _ = auth_client("PATIENT")
    rid = _request(patient)
    rows = _rows("blood_request", rid)
    assert len(rows) == 1
    ev = rows[0]
    assert ev.event_type == "blood_request.updated"
    assert ev.seq is not None and ev.published_at is not None


def test_one_event_per_resource_per_transaction() -> None:
    hosp, _ = _hospital(stock=5)
    patient, _ = auth_client("PATIENT")
    rid = _request(patient, units=3)
    before = len(_rows("blood_request", rid))
    hosp.post(f"/hospitals/me/requests/{rid}/allocate", json={"blood_type": "O_NEG", "units": 3})
    # the allocation wrote several history rows about this request in one
    # commit (transition event + allocation) -> exactly one new event
    assert len(_rows("blood_request", rid)) == before + 1
    assert len(_rows("inventory")) >= 2  # the add + the allocate, separate commits


def test_rolled_back_change_creates_no_event() -> None:
    patient, _ = auth_client("PATIENT")
    rid = _request(patient)
    before = len(_rows("blood_request", rid))
    db = SessionLocal()
    try:
        db.add(BloodRequestEvent(request_id=uuid.UUID(rid), event_type="note", note="x"))
        db.flush()
        db.rollback()
    finally:
        db.close()
    realtime_service.publish_pending_now(engine)
    assert len(_rows("blood_request", rid)) == before
    db = SessionLocal()
    try:
        assert (
            db.scalar(
                select(func.count()).select_from(RealtimeEvent).where(RealtimeEvent.seq.is_(None))
            )
            == 0
        )
    finally:
        db.close()


def test_notification_read_emits_update_for_owner_only() -> None:
    donor, demail = auth_client("DONOR")
    donor.put("/donors/me", json={"blood_type": "O_NEG", "city": "Metro"})
    patient, _ = auth_client("PATIENT")
    _request(patient)
    notes = donor.get("/notifications").json()
    assert notes, "matching donor should have been notified"
    nid = notes[0]["id"]
    donor.post(f"/notifications/{nid}/read")
    rows = _rows("notification", nid)
    assert [r.event_type for r in rows] == ["notification.created", "notification.updated"]
    for r in rows:
        assert r.audience_user_ids == [_uid(demail)] and r.audience_roles == []


# --------------------------------------------------------------------------- #
# Authorization: audience mirrors (never exceeds) REST visibility
# --------------------------------------------------------------------------- #
def test_blood_request_audience_isolation() -> None:
    patient, pemail = auth_client("PATIENT")
    other_patient, oemail = auth_client("PATIENT")
    hosp_same, hs = _hospital(city="Metro")
    hosp_other, ho = _hospital(city="Elsewhere")
    rid = _request(patient)
    ev = _rows("blood_request", rid)[-1]
    aud = set(ev.audience_user_ids)
    assert _uid(pemail) in aud and _uid(hs) in aud
    assert _uid(oemail) not in aud and _uid(ho) not in aud
    assert ev.audience_roles == ["ADMIN"]


def test_shipment_and_inventory_audience_is_party_hospitals_only() -> None:
    from tests.test_phase_d_shipment import _create_shipment, _two_facilities

    db_ = SessionLocal()
    try:
        origin, oid, dest, did = _two_facilities(db_)
    finally:
        db_.close()
    stranger, semail = _hospital(city="Metro")
    origin.post("/hospitals/me/inventory/add", json={"blood_type": "O_NEG", "units": 2})
    sid = _create_shipment(origin, oid, did, units=1).json()["id"]
    ev = _rows("shipment", sid)[-1]
    assert _uid(semail) not in set(ev.audience_user_ids)
    assert len(ev.audience_user_ids) == 2
    inv = list(_rows("inventory", oid))[-1]
    assert len(inv.audience_user_ids) == 1 and _uid(semail) not in inv.audience_user_ids


def _population():
    """A mixed population of real users for the subset property tests."""
    users = {}
    for role in ("PATIENT", "PATIENT", "FAMILY", "FAMILY", "DONOR", "DONOR", "ADMIN"):
        c, email = auth_client(role)
        users.setdefault(role, []).append((c, email))
    for city in ("Metro", "Elsewhere"):
        users.setdefault("HOSPITAL", []).append(_hospital(city=city))
    return users


def test_audience_never_exceeds_rest_visibility_for_requests_and_emergencies() -> None:
    users = _population()
    patient, pemail = users["PATIENT"][0]
    fam, femail = users["FAMILY"][0]
    # family links to patient (accepted)
    r = fam.post("/family/links", json={"patient_email": pemail, "relationship": "son"})
    assert r.status_code in (200, 201), r.text
    link_id = r.json()["id"]
    assert patient.post(f"/family/links/{link_id}/accept").status_code == 200
    donor, demail = users["DONOR"][0]
    donor.put("/donors/me", json={"blood_type": "O_NEG", "city": "Metro"})
    rid = _request(patient)
    donor.post(f"/blood-requests/{rid}/respond", json={"action": "ACCEPTED"})
    eid = patient.post(
        "/emergencies",
        json={
            "blood_type": "O_NEG",
            "location_text": "Metro central",
            "urgency": "CRITICAL",
        },
    ).json()["id"]

    db = SessionLocal()
    try:
        req = db.get(BloodRequest, uuid.UUID(rid))
        em = db.get(Emergency, uuid.UUID(eid))
        for resource, obj, can_view in (
            ("blood_request", req, br_routes._can_view),
            ("emergency", em, em_routes._can_view),
        ):
            ids, roles = audience_for(db, resource, obj.id)
            assert roles == ["ADMIN"]
            assert ids, resource
            for uid in ids:
                u = db.get(User, uid)
                assert u.role == UserRole.ADMIN or can_view(db, obj, u), (resource, u.email)
            # and the linked family member + responding donor are included
            assert _uid(femail) in ids
            if resource == "blood_request":
                assert _uid(demail) in ids
            # unrelated users are not
            assert _uid(users["PATIENT"][1][1]) not in ids
            assert _uid(users["FAMILY"][1][1]) not in ids
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Ordering: gap-free seq in publish order, even under concurrency
# --------------------------------------------------------------------------- #
def test_concurrent_writers_get_gap_free_ordered_publication() -> None:
    sub = redis.Redis.from_url(settings.redis_url, decode_responses=True).pubsub()
    sub.subscribe(realtime_service.CHANNEL)
    sub.get_message(timeout=1)  # subscribe confirmation
    received: list[int] = []
    stop = threading.Event()

    def listen():
        while not stop.is_set():
            m = sub.get_message(ignore_subscribe_messages=True, timeout=0.2)
            if m:
                received.append(json.loads(m["data"])["seq"])

    t = threading.Thread(target=listen)
    t.start()
    patients = [auth_client("PATIENT")[0] for _ in range(8)]
    barrier = threading.Barrier(len(patients))
    errors = []

    def go(p):
        try:
            barrier.wait()
            for _ in range(3):
                _request(p)
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    ts = [threading.Thread(target=go, args=(p,)) for p in patients]
    for x in ts:
        x.start()
    for x in ts:
        x.join()
    time.sleep(1.0)
    stop.set()
    t.join()
    sub.close()
    assert not errors
    seqs = [r.seq for r in _rows()]
    assert seqs == list(range(1, len(seqs) + 1)), "seq must be gap-free"
    assert all(r.published_at is not None for r in _rows())
    assert received == sorted(received), "publication must be in seq order"
    assert len(received) == len(set(received))
    # every blood_request event of this burst was published
    assert len(_rows("blood_request")) == 24


def test_redis_outage_keeps_events_and_republishes_in_order(monkeypatch) -> None:
    class Down:
        def pipeline(self, **kw):
            raise redis.ConnectionError("down")

    monkeypatch.setattr(realtime_service, "_redis", lambda: Down())
    patient, _ = auth_client("PATIENT")
    rid = _request(patient)
    stuck = _rows("blood_request", rid)[-1]
    assert stuck.seq is not None and stuck.published_at is None
    monkeypatch.undo()

    sub = redis.Redis.from_url(settings.redis_url, decode_responses=True).pubsub()
    sub.subscribe(realtime_service.CHANNEL)
    sub.get_message(timeout=1)
    _request(patient)  # next commit's publisher run flushes the backlog first
    got = []
    deadline = time.time() + 3
    while time.time() < deadline:
        m = sub.get_message(ignore_subscribe_messages=True, timeout=0.2)
        if m:
            got.append(json.loads(m["data"])["seq"])
    sub.close()
    assert stuck.seq in got and got == sorted(got)
    assert all(r.published_at is not None for r in _rows())


# --------------------------------------------------------------------------- #
# Replay
# --------------------------------------------------------------------------- #
def test_replay_is_cursor_bounded_and_audience_filtered() -> None:
    a, aemail = auth_client("PATIENT")
    b, bemail = auth_client("PATIENT")
    ra1 = _request(a)
    _request(b)
    ra2 = _request(a)
    db = SessionLocal()
    try:
        head = realtime_service.head_seq(db)
        rows = realtime_service.replay(db, _uid(aemail), "PATIENT", 0, head, 100)
        ids = [str(r.resource_id) for r in rows if r.resource_type == "blood_request"]
        assert ids == [ra1, ra2]
        first = rows[0].seq
        later = realtime_service.replay(db, _uid(aemail), "PATIENT", first, head, 100)
        assert [str(r.resource_id) for r in later if r.resource_type == "blood_request"] == [ra2]
        admin_rows = realtime_service.replay(db, uuid.uuid4(), "ADMIN", 0, head, 100)
        assert {str(r.resource_id) for r in admin_rows if r.resource_type == "blood_request"} >= {
            ra1,
            ra2,
        }
    finally:
        db.close()


@pytest.mark.parametrize(
    "message,uid,role,expected",
    [
        ({"audience_user_ids": ["u1"], "audience_roles": ["ADMIN"]}, "u1", "PATIENT", True),
        ({"audience_user_ids": ["u1"], "audience_roles": ["ADMIN"]}, "u2", "PATIENT", False),
        ({"audience_user_ids": [], "audience_roles": ["ADMIN"]}, "u2", "ADMIN", True),
        ({"audience_user_ids": ["u1"], "audience_roles": []}, "u2", "ADMIN", False),
    ],
)
def test_visible_to(message, uid, role, expected) -> None:
    assert realtime_service.visible_to(message, uid, role) is expected


def test_channel_is_namespaced_by_database() -> None:
    """Pub/sub ignores Redis db numbers; two environments sharing one Redis
    must not see each other's seqs."""
    from sqlalchemy.engine import make_url

    assert (
        realtime_service.CHANNEL == f"lifelink:realtime:{make_url(settings.database_url).database}"
    )
    assert realtime_service.CHANNEL.endswith(":lifelink_test")


def test_publication_cost_is_independent_of_fanout(monkeypatch) -> None:
    """Notifying 15 donors must not cost more SQL statements than notifying
    3 (Phase 27's bounded fan-out property, preserved with real-time events:
    the publisher batches audience lookups and seq assignment). Runs the
    publisher inline so its statements are counted with the request's."""
    monkeypatch.setattr(settings, "realtime_publish_mode", "inline")
    from tests.test_phase27_notification_scaling import count_queries

    def run(n: int) -> int:
        for _ in range(n):
            d, _ = auth_client("DONOR")
            # Audit D4 (2026-10-31): alerts apply the distance policy; same city.
            d.put("/donors/me", json={"blood_type": "O_NEG", "is_available": True,
                                      "city": "Metro"})
        patient, _ = auth_client("PATIENT")
        with count_queries() as stmts:
            r = patient.post("/blood-requests", json={"blood_type": "O_NEG", "units_needed": 1,
                                                      "city": "Metro"})
            assert r.status_code == 201
        notified = len(_rows("notification"))
        return len(stmts), notified

    small, n_small = run(3)
    db = SessionLocal()
    try:
        from sqlalchemy import text as _t

        db.execute(_t("TRUNCATE realtime_events, notifications CASCADE"))
        db.commit()
    finally:
        db.close()
    large, n_large = run(15)
    assert n_large > n_small >= 3
    assert large == small, (small, large)
