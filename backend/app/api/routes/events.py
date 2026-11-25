"""Phase F -- `GET /events/stream`: the authenticated Server-Sent Events
stream of the real-time event layer (see app.services.realtime_service for
creation/publication and app.services.realtime_audience for who may see
what).

Contract with the client:

* Each `event:` is a hint -- `{seq, type, resource_type, resource_id}` --
  to re-fetch that resource through the normal authorized REST API. It is
  never authoritative state; the database is.
* `id:` is the global `seq`. Browsers resend it as `Last-Event-ID` on
  reconnect; the server then replays every event the user may see after
  it, from the database, before going live. If too much was missed, or the
  id is unknown/pruned, it sends `resync` instead: the client re-fetches
  everything it shows.
* `ready` (with `id:` = current head) marks the end of replay; `ping`
  events (also carrying the latest seq as `id:`) keep proxies open and
  advance the client's resume point past events it wasn't shown.
* The session is re-validated every `realtime_session_recheck_seconds`
  WITHOUT sliding its idle timeout; a logged-out, suspended, or expired
  session gets `session_ended` and the stream closes.

Authentication is checked before the stream starts (401 otherwise), with
a short-lived DB session that is closed before streaming -- an open stream
never pins a pooled database connection.

Hardening: ONE Redis subscription per process (per event loop), shared by
every open stream through `_Broker` -- previously one Redis connection per
open stream. A stream registers with the broker (which is already
subscribed) BEFORE the replay snapshot is taken, so nothing published in
between can be missed (anything seen twice is dropped by seq). If a stream
falls too far behind (its queue fills) or the broker loses Redis, the
stream gets `resync` -- the client re-fetches what it shows -- rather than
silently missing events.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid

import redis.asyncio as aioredis
from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from app.api.deps import _load_user_from_request
from app.core.config import settings
from app.db.session import SessionLocal
from app.services import realtime_service

router = APIRouter(prefix="/events", tags=["events"])

_QUEUE_MAX = 1000
_RESYNC = object()  # sentinel placed on a subscriber queue: "you missed events"


class _Broker:
    """One Redis pub/sub connection fanning out to every stream on this
    event loop."""

    def __init__(self) -> None:
        self.subscribers: set[asyncio.Queue] = set()
        self._task: asyncio.Task | None = None
        self._ready = asyncio.Event()

    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAX)
        self.subscribers.add(q)
        if self._task is None or self._task.done():
            self._ready = asyncio.Event()
            self._task = asyncio.create_task(self._reader())
        await asyncio.wait_for(self._ready.wait(), timeout=10)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subscribers.discard(q)

    def _broadcast(self, item) -> None:
        for q in list(self.subscribers):
            try:
                q.put_nowait(item)
            except asyncio.QueueFull:
                # A stream this far behind must not block the others; it
                # is told to resync instead of silently dropping events.
                while not q.empty():
                    q.get_nowait()
                q.put_nowait(_RESYNC)

    async def _reader(self) -> None:
        backoff = 0.5
        while self.subscribers:
            client = aioredis.from_url(settings.redis_url, decode_responses=True)
            pubsub = client.pubsub()
            try:
                await pubsub.subscribe(realtime_service.CHANNEL)
                self._ready.set()
                backoff = 0.5
                while self.subscribers:
                    msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                    if msg is not None and msg.get("type") == "message":
                        self._broadcast(json.loads(msg["data"]))
            except asyncio.CancelledError:
                raise
            except Exception:
                # Redis lost: every stream may have missed events -> resync.
                self._broadcast(_RESYNC)
                self._ready.set()  # don't hang new subscribers; they resync too
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 10.0)
            finally:
                try:
                    await pubsub.aclose()
                finally:
                    await client.aclose()
        self._task = None


_brokers: dict[int, _Broker] = {}


def _broker() -> _Broker:
    loop = asyncio.get_running_loop()
    b = _brokers.get(id(loop))
    if b is None:
        b = _brokers[id(loop)] = _Broker()
    return b


def open_redis_connections_for_streams() -> int:
    """Live Redis pub/sub readers in this process (for tests/metrics)."""
    return sum(1 for b in _brokers.values() if b._task is not None and not b._task.done())


def _identity(request: Request, *, touch: bool) -> tuple[uuid.UUID, str] | None:
    db = SessionLocal()
    try:
        user = _load_user_from_request(request, db, touch=touch)
        return (user.id, user.role.value) if user else None
    finally:
        db.close()


def _snapshot(user_id: uuid.UUID, role: str, after: int | None):
    db = SessionLocal()
    try:
        head = realtime_service.head_seq(db)
        oldest = realtime_service.oldest_seq(db)
        rows = []
        if after is not None and 0 <= after < head:
            rows = realtime_service.replay(
                db, user_id, role, after, head, settings.realtime_replay_limit + 1
            )
        return head, oldest, rows
    finally:
        db.close()


def _frame(event: str, data: dict, seq: int | None = None) -> str:
    out = f"id: {seq}\n" if seq is not None else ""
    return out + f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def _public(seq: int, etype: str, rtype: str, rid) -> dict:
    return {"seq": seq, "type": etype, "resource_type": rtype, "resource_id": str(rid)}


def _parse_after(last_event_id: str | None, since: int | None) -> int | None:
    raw = last_event_id if last_event_id not in (None, "") else since
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return -1  # unparseable -> treated as unknown -> resync
    return value


@router.get("/stream")
async def stream(
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    since: int | None = Query(default=None, ge=0),
) -> StreamingResponse:
    ident = await run_in_threadpool(_identity, request, touch=True)
    if ident is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    user_id, role = ident
    uid = str(user_id)
    after = _parse_after(last_event_id, since)

    async def events():
        broker = _broker()
        queue = await broker.subscribe()
        try:
            head, oldest, rows = await run_in_threadpool(_snapshot, user_id, role, after)
            yield "retry: 3000\n\n"
            if after is not None:
                pruned = oldest is not None and after < oldest - 1
                unknown = after < 0 or after > head
                if unknown or pruned or len(rows) > settings.realtime_replay_limit:
                    yield _frame(
                        "resync",
                        {
                            "head_seq": head,
                            "reason": (
                                "unknown_cursor"
                                if unknown
                                else "pruned"
                                if pruned
                                else "too_many_missed"
                            ),
                        },
                    )
                else:
                    for seq, etype, rtype, rid in rows:
                        yield _frame(etype, _public(seq, etype, rtype, rid), seq)
            last = head
            yield _frame("ready", {"head_seq": head}, head)

            now = time.monotonic()
            next_ping = now + settings.realtime_heartbeat_seconds
            next_check = now + settings.realtime_session_recheck_seconds
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=0.5)
                except TimeoutError:
                    data = None
                if data is _RESYNC:
                    head_now = await run_in_threadpool(_snapshot, user_id, role, None)
                    last = max(last, head_now[0])
                    yield _frame("resync", {"head_seq": head_now[0], "reason": "stream_lagged"})
                elif data is not None:
                    seq = int(data["seq"])
                    if seq > last:
                        last = seq
                        if realtime_service.visible_to(data, uid, role):
                            yield _frame(
                                data["type"],
                                _public(
                                    seq, data["type"], data["resource_type"], data["resource_id"]
                                ),
                                seq,
                            )
                now = time.monotonic()
                if now >= next_check:
                    next_check = now + settings.realtime_session_recheck_seconds
                    still = await run_in_threadpool(_identity, request, touch=False)
                    if still is None or still[0] != user_id:
                        yield _frame("session_ended", {})
                        break
                if now >= next_ping:
                    next_ping = now + settings.realtime_heartbeat_seconds
                    yield _frame("ping", {}, last)
                await asyncio.sleep(0)
        finally:
            broker.unsubscribe(queue)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
