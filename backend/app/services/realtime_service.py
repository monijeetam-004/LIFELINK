"""Phase F -- real-time event layer: reliable creation, ordered publication,
authorized replay.

    DATABASE TRANSACTION ──(same tx)──► realtime_events row (outbox)
           │ commit
           ▼
    publisher (after the transaction ended, own connection,
               pg_advisory_lock): assign seq in order, compute audience,
               COMMIT, then PUBLISH to Redis in seq order
           ▼
    /events/stream (SSE, one Redis subscription per connection):
               filter by audience, drop seq <= last sent, heartbeat,
               periodic session re-validation
           ▼
    browser: treats each event as "re-fetch X" through the normal API

Why each piece:

* **Outbox in the same transaction** -- an event can never be emitted for
  a change that later rolls back, and a committed change can never lose
  its event. Rows are added by a `before_flush` listener that maps the
  history rows every mutation in this codebase already writes
  (BloodRequestEvent, EmergencyEvent, ShipmentEvent, HospitalInventoryLog,
  Notification) plus explicit Donation/VerificationRequest changes, so no
  route had to be edited and a new mutation that follows the existing
  "write a history row" convention is covered automatically.
* **Publish only after the transaction has ENDED** (`after_transaction_end`
  on the root transaction) -- the same hook, for the same pool-deadlock
  reason, as the Phase E audit sealer (see LIFELINK_AUDIT_CHAIN.md).
* **seq assigned under a session-level advisory lock that is held until
  the Redis publish completes** -- publication is globally in seq order,
  so a live client can simply drop anything <= the last seq it sent, and
  `Last-Event-ID` replay ("everything after N") is exact: every seq <= the
  head was assigned by an already-committed transaction.
* **Redis down** -- rows keep their seq with `published_at` NULL; the next
  publisher run re-publishes them first, in order (clients dedupe by seq),
  and reconnecting clients replay from the database regardless.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import timedelta

from sqlalchemy import Connection, and_, delete, event, inspect, select, text, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.time import utcnow
from app.models.blood_request import BloodRequestEvent
from app.models.certificate import Certificate
from app.models.donation import Donation
from app.models.emergency import EmergencyEvent
from app.models.family import FamilyLink
from app.models.hospital import HospitalInventoryLog
from app.models.notification import Notification
from app.models.realtime_event import RealtimeEvent
from app.models.shipment import ShipmentEvent
from app.models.verification import VerificationRequest

log = logging.getLogger("lifelink.realtime")

def _channel_name() -> str:
    """Redis pub/sub channels are global to a Redis server -- they ignore
    the numbered database a client selects. `seq` is only meaningful within
    ONE Postgres database, so the channel is namespaced by it: two
    environments (dev + tests, staging + e2e) sharing one Redis must never
    see each other's sequence numbers (a foreign, larger seq would make a
    stream drop its own real events as "already seen"). Found while running
    the backend suite next to a live e2e server on the same Redis."""
    from sqlalchemy.engine import make_url

    db_name = make_url(settings.database_url).database or "default"
    return f"lifelink:realtime:{db_name}"


CHANNEL = _channel_name()
#: Distinct from the audit chain's key (app.core.audit_chain.ADVISORY_LOCK_KEY).
ADVISORY_LOCK_KEY = 0x4C4C5245414C54  # "LLREALT"
_PENDING = "realtime_pending"
_READY = "realtime_ready"
_QUEUED = "realtime_queued_keys"
_BATCH = 500
PRUNE_EVERY = 500  # Phase K: retention runs once per this many sequenced events
_T = RealtimeEvent.__table__


# --------------------------------------------------------------------------- #
# 1. Creation -- inside the business transaction
# --------------------------------------------------------------------------- #

def queue_event(
    session: Session, event_type: str, resource_type: str, resource_id: uuid.UUID,
) -> None:
    """Add an outbox row to `session`'s current transaction. Deduplicated
    per transaction: ten history rows about one request in one commit are
    one "blood_request.updated" event."""
    if resource_id is None:
        return
    keys: set = session.info.setdefault(_QUEUED, set())
    key = (event_type, resource_type, resource_id)
    if key in keys:
        return
    keys.add(key)
    session.add(RealtimeEvent(
        event_type=event_type, resource_type=resource_type, resource_id=resource_id,
    ))
    session.info[_PENDING] = True


def _attr_changed(obj, name: str) -> bool:
    return inspect(obj).attrs[name].history.has_changes()


@event.listens_for(Session, "before_flush")
def _collect(session: Session, flush_context, instances) -> None:
    for obj in list(session.new):
        if isinstance(obj, BloodRequestEvent):
            queue_event(session, "blood_request.updated", "blood_request", obj.request_id)
        elif isinstance(obj, EmergencyEvent):
            queue_event(session, "emergency.updated", "emergency", obj.emergency_id)
        elif isinstance(obj, ShipmentEvent):
            queue_event(session, "shipment.updated", "shipment", obj.shipment_id)
        elif isinstance(obj, HospitalInventoryLog):
            queue_event(session, "inventory.updated", "inventory", obj.hospital_id)
        elif isinstance(obj, Notification):
            if obj.id is None:
                obj.id = uuid.uuid4()
            queue_event(session, "notification.created", "notification", obj.id)
        elif isinstance(obj, Donation):
            if obj.id is None:
                obj.id = uuid.uuid4()
            queue_event(session, "donation.updated", "donation", obj.id)
        elif isinstance(obj, VerificationRequest):
            if obj.id is None:
                obj.id = uuid.uuid4()
            queue_event(session, "verification.updated", "verification", obj.id)
        # Hardening: family-link and certificate changes were not evented.
        elif isinstance(obj, FamilyLink):
            if obj.id is None:
                obj.id = uuid.uuid4()
            queue_event(session, "family_link.updated", "family_link", obj.id)
        elif isinstance(obj, Certificate):
            if obj.id is None:
                obj.id = uuid.uuid4()
            queue_event(session, "certificate.updated", "certificate", obj.id)
    for obj in list(session.dirty):
        if isinstance(obj, Notification) and _attr_changed(obj, "read_at"):
            queue_event(session, "notification.updated", "notification", obj.id)
        elif isinstance(obj, Donation) and _attr_changed(obj, "verification_status"):
            queue_event(session, "donation.updated", "donation", obj.id)
        elif isinstance(obj, VerificationRequest) and _attr_changed(obj, "status"):
            queue_event(session, "verification.updated", "verification", obj.id)
        elif isinstance(obj, FamilyLink) and _attr_changed(obj, "status"):
            queue_event(session, "family_link.updated", "family_link", obj.id)
        elif isinstance(obj, Certificate) and _attr_changed(obj, "revoked_at"):
            queue_event(session, "certificate.updated", "certificate", obj.id)


@event.listens_for(Session, "after_commit")
def _mark_ready(session: Session) -> None:
    session.info.pop(_QUEUED, None)
    if session.info.pop(_PENDING, False):
        session.info[_READY] = True


@event.listens_for(Session, "after_rollback")
def _forget(session: Session) -> None:
    # The rolled-back outbox rows are gone with the transaction; nothing to publish.
    session.info.pop(_QUEUED, None)
    session.info.pop(_PENDING, None)


@event.listens_for(Session, "after_transaction_end")
def _publish_after_end(session: Session, transaction) -> None:
    if transaction.parent is not None or not session.info.pop(_READY, False):
        return
    if settings.realtime_publish_mode == "inline":
        publish_pending_now(session.get_bind())
    else:
        publisher.wake(session.get_bind())


class _BackgroundPublisher:
    """Hardening: publication off the request path.

    Before, every committing request ran the publisher itself (sequence,
    audience, Redis publish) before returning -- about +10 ms per write, and
    a second pooled DB connection per concurrent writer. Now a committed
    transaction only WAKES this per-process daemon thread, which drains the
    whole backlog with one connection. Ordering is unchanged (the same
    advisory-locked `publish_pending` runs; several processes' publishers
    simply take turns). The thread also wakes every
    `realtime_publisher_interval_seconds` while in background mode, so events
    left behind by a crashed process or a Redis outage are published without
    waiting for the next write. `flush()` waits until everything committed
    so far has been through a publisher run (used by tests and at exit)."""

    def __init__(self) -> None:
        import threading

        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._done = threading.Condition(self._lock)
        self._requested = 0
        self._completed = 0
        self._thread = None
        self._bind = None

    def _ensure_thread(self) -> None:
        import threading

        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(
                target=self._run, name="lifelink-realtime-publisher", daemon=True
            )
            self._thread.start()

    def wake(self, bind=None) -> None:
        with self._lock:
            if bind is not None:
                self._bind = bind
            self._requested += 1
            self._ensure_thread()
        self._wake.set()

    def flush(self, timeout: float = 15.0, bind=None) -> bool:
        """Block until a publisher run that STARTED after this call has
        finished. Returns False on timeout."""
        import time

        with self._lock:
            if bind is not None:
                self._bind = bind
            self._requested += 1
            target = self._requested
            self._ensure_thread()
        self._wake.set()
        deadline = time.monotonic() + timeout
        with self._done:
            while self._completed < target:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._done.wait(remaining)
        return True

    def _run(self) -> None:
        while True:
            self._wake.wait(timeout=settings.realtime_publisher_interval_seconds)
            self._wake.clear()
            with self._lock:
                target = self._requested
                bind = self._bind
            if bind is None:
                from app.db.session import engine as bind
            if target > self._completed or settings.realtime_publish_mode != "inline":
                publish_pending_now(bind)
            with self._done:
                self._completed = max(self._completed, target)
                self._done.notify_all()


publisher = _BackgroundPublisher()


def flush(timeout: float = 15.0) -> bool:
    return publisher.flush(timeout)


def _flush_at_exit() -> None:  # pragma: no cover - interpreter shutdown
    # Only if a commit is still waiting to be published by this process.
    if publisher._thread is not None and publisher._requested > publisher._completed:
        publisher.flush(timeout=5.0)


import atexit  # noqa: E402

atexit.register(_flush_at_exit)


# --------------------------------------------------------------------------- #
# 2. Publication -- after the business transaction has ended
# --------------------------------------------------------------------------- #

def _redis():
    from app.core.redis import redis_client
    return redis_client


def _message(row) -> str:
    return json.dumps({
        "seq": row.seq,
        "type": row.event_type,
        "resource_type": row.resource_type,
        "resource_id": str(row.resource_id),
        "audience_user_ids": [str(u) for u in (row.audience_user_ids or [])],
        "audience_roles": list(row.audience_roles or []),
    })


def _assign(conn: Connection) -> tuple[int, int]:
    """Give every committed, unsequenced row the next seq and its audience.
    Caller holds the advisory lock and an open transaction on `conn`.
    Returns (rows sequenced, head seq before this run).
    Batched: one audience query per resource type for notifications and
    one executemany UPDATE, so the statement count does not grow with the
    number of events (a request that notifies 1,000 donors creates 1,000
    notification events)."""
    from sqlalchemy import bindparam

    from app.services.realtime_audience import audience_batch

    head = conn.execute(select(_T.c.seq).where(_T.c.seq.is_not(None))
                        .order_by(_T.c.seq.desc()).limit(1)).scalar()
    seq = head or 0
    rows = conn.execute(
        select(_T.c.id, _T.c.resource_type, _T.c.resource_id)
        .where(_T.c.seq.is_(None)).order_by(_T.c.created_at, _T.c.id).limit(_BATCH)
    ).all()
    if not rows:
        return 0, seq
    by_type: dict[str, list] = {}
    for row in rows:
        by_type.setdefault(row.resource_type, []).append(row.resource_id)
    with Session(bind=conn) as lookup:
        audiences = {
            rtype: audience_batch(lookup, rtype, ids) for rtype, ids in by_type.items()
        }
    params = []
    for row in rows:
        seq += 1
        users, roles = audiences[row.resource_type][row.resource_id]
        params.append({"_id": row.id, "_seq": seq, "_users": users, "_roles": roles})
    conn.execute(
        update(_T).where(_T.c.id == bindparam("_id")).values(
            seq=bindparam("_seq"), audience_user_ids=bindparam("_users"),
            audience_roles=bindparam("_roles"),
        ),
        params,
    )
    return len(rows), head or 0


def _unpublished(conn: Connection):
    return conn.execute(
        select(_T).where(_T.c.seq.is_not(None), _T.c.published_at.is_(None))
        .order_by(_T.c.seq).limit(_BATCH * 4)
    ).all()


def _send(rows) -> None:
    r = _redis()
    pipe = r.pipeline(transaction=False)
    for row in rows:
        pipe.publish(CHANNEL, _message(row))
    pipe.execute()


def _prune(conn: Connection) -> None:
    cutoff = utcnow() - timedelta(hours=settings.realtime_retention_hours)
    conn.execute(delete(_T).where(and_(_T.c.created_at < cutoff, _T.c.published_at.is_not(None))))


def publish_pending(conn: Connection) -> tuple[int, int]:
    """Assign + publish under a session-level advisory lock held across the
    Redis publish, so publication is globally in seq order. Returns
    (sequenced, published).

    Two short transactions: (1) lock, sequence + audience, read what still
    needs publishing, COMMIT -- the seqs are durable before anyone can see
    them; then PUBLISH to Redis; (2) mark published, unlock, COMMIT. (A
    first version used five commits per run; consolidating them is the
    Phase F performance fix measured in the Phase F report.)"""
    conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": ADVISORY_LOCK_KEY})
    locked = True
    try:
        sequenced, head_before = _assign(conn)
        rows = _unpublished(conn)
        conn.commit()
        published = 0
        if rows:
            try:
                _send(rows)
                published = len(rows)
            except Exception:  # Redis unavailable -- rows stay unpublished, replay still works
                log.warning("realtime publish to Redis failed; will retry on next run",
                            exc_info=True)
        if published:
            conn.execute(update(_T).where(_T.c.id.in_([row.id for row in rows]))
                         .values(published_at=utcnow()))
        # Phase K: retention. The previous trigger was `sequenced % 97 == 0`
        # -- the number of events sequenced IN THIS RUN -- which is 1-3 for an
        # ordinary write, so in normal traffic pruning never ran and the
        # table grew without bound. Now: prune whenever the sequence crosses
        # a multiple of PRUNE_EVERY (once per PRUNE_EVERY events).
        if sequenced and (head_before // PRUNE_EVERY) != ((head_before + sequenced) // PRUNE_EVERY):
            _prune(conn)
        conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": ADVISORY_LOCK_KEY})
        locked = False
        conn.commit()
        return sequenced, published
    finally:
        if locked:
            conn.rollback()
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": ADVISORY_LOCK_KEY})
            conn.commit()


# Phase K: upper bound on publisher runs per committing transaction. Each
# run sequences up to _BATCH events and publishes up to _BATCH * 4.
_MAX_RUNS = 200


def publish_pending_now(bind) -> tuple[int, int]:
    """Never raises into a request whose transaction has already committed.

    Phase K: drains the WHOLE backlog, not one batch. A single run handles
    at most _BATCH events, and before this fix a transaction that created
    more (an emergency notifying every compatible donor, a bulk load) left
    the remainder unsequenced until some later, unrelated commit happened
    to run the publisher -- live updates for those users could lag
    indefinitely (found in the Phase K volume database: 172,106 events
    never sequenced). Runs repeat while a run was full; each run is still
    its own short lock-protected transaction, so ordering is unchanged."""
    engine = getattr(bind, "engine", bind)
    total_seq = total_pub = 0
    try:
        with engine.connect() as conn:
            for _ in range(_MAX_RUNS):
                sequenced, published = publish_pending(conn)
                total_seq += sequenced
                total_pub += published
                if sequenced < _BATCH and published < _BATCH * 4:
                    break
            else:
                log.warning("realtime publisher hit %d runs; remaining backlog "
                            "drains on the next commit", _MAX_RUNS)
        return total_seq, total_pub
    except Exception:  # pragma: no cover - defensive
        log.warning("realtime publisher failed; events remain queued", exc_info=True)
        return total_seq, total_pub


# --------------------------------------------------------------------------- #
# 3. Delivery helpers -- used by the SSE endpoint
# --------------------------------------------------------------------------- #

def visible_to(message: dict, user_id: str, role: str) -> bool:
    return (user_id in message.get("audience_user_ids", ())
            or role in message.get("audience_roles", ()))


def head_seq(db: Session) -> int:
    return db.execute(select(_T.c.seq).where(_T.c.seq.is_not(None))
                      .order_by(_T.c.seq.desc()).limit(1)).scalar() or 0


def oldest_seq(db: Session) -> int | None:
    return db.execute(select(_T.c.seq).where(_T.c.seq.is_not(None))
                      .order_by(_T.c.seq.asc()).limit(1)).scalar()


def replay(db: Session, user_id: uuid.UUID, role: str, after_seq: int, up_to: int, limit: int):
    """Events in (after_seq, up_to] visible to this user, in seq order.
    Filtered in SQL with the same stored audience the live stream used."""
    return db.execute(
        select(_T.c.seq, _T.c.event_type, _T.c.resource_type, _T.c.resource_id)
        .where(
            _T.c.seq > after_seq, _T.c.seq <= up_to,
            (_T.c.audience_user_ids.any(user_id)) | (_T.c.audience_roles.any(role)),
        )
        .order_by(_T.c.seq).limit(limit)
    ).all()
