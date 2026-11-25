"""Server-side session store backed by Redis.

The browser only ever holds an opaque session id in an httpOnly cookie. All
session state lives here. Password resets call :func:`delete_user_sessions` to
invalidate every active session for a user.
"""
from __future__ import annotations

import uuid
from typing import cast

from app.core.config import settings
from app.core.redis import redis_client
from app.core.security import generate_token
from app.core.time import utcnow

_SESSION = "session:"
_USER_SESSIONS = "user_sessions:"


def create_session(user_id: uuid.UUID, ip: str | None, user_agent: str | None) -> str:
    sid = generate_token()
    ttl = settings.session_ttl_seconds
    pipe = redis_client.pipeline()
    pipe.hset(
        f"{_SESSION}{sid}",
        mapping={
            "user_id": str(user_id),
            "issued_at": utcnow().isoformat(),
            "ip": ip or "",
            "user_agent": (user_agent or "")[:400],
        },
    )
    pipe.expire(f"{_SESSION}{sid}", ttl)
    pipe.sadd(f"{_USER_SESSIONS}{user_id}", sid)
    pipe.expire(f"{_USER_SESSIONS}{user_id}", ttl)
    pipe.execute()
    return sid


def get_session(sid: str) -> dict[str, str] | None:
    # decode_responses=True on the client guarantees str keys/values at runtime.
    data = cast("dict[str, str]", redis_client.hgetall(f"{_SESSION}{sid}"))
    return data or None


def touch_session(sid: str, user_id: uuid.UUID) -> None:
    """Slide the idle-timeout window forward on an authenticated request.

    Only extends the Redis TTL (how much longer this session survives
    inactivity) -- it never changes the session's stored `issued_at`, which
    is what the absolute-lifetime cap in app/api/deps.py measures from. An
    always-active session is never logged out by the idle timeout, but it
    still cannot outlive `session_absolute_ttl_seconds` from its original
    login.
    """
    ttl = settings.session_ttl_seconds
    pipe = redis_client.pipeline()
    pipe.expire(f"{_SESSION}{sid}", ttl)
    pipe.expire(f"{_USER_SESSIONS}{user_id}", ttl)
    pipe.execute()


def delete_session(sid: str) -> None:
    data = cast("dict[str, str]", redis_client.hgetall(f"{_SESSION}{sid}"))
    user_id = data.get("user_id")
    pipe = redis_client.pipeline()
    pipe.delete(f"{_SESSION}{sid}")
    if user_id:
        pipe.srem(f"{_USER_SESSIONS}{user_id}", sid)
    pipe.execute()


def delete_user_sessions(user_id: uuid.UUID) -> int:
    set_key = f"{_USER_SESSIONS}{user_id}"
    sids = cast("set[str]", redis_client.smembers(set_key))
    pipe = redis_client.pipeline()
    for sid in sids:
        pipe.delete(f"{_SESSION}{sid}")
    pipe.delete(set_key)
    pipe.execute()
    return len(sids)
