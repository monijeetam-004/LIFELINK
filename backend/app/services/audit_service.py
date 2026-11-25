from __future__ import annotations

import uuid
from typing import Any

from fastapi import Request
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.core.audit_chain import normalize_meta
from app.core.time import utcnow
from app.models.audit_log import AuditLog

# Phase E: set on a Session whenever it adds an audit row, consumed by the
# after_commit hook below to seal those rows into the hash chain.
_PENDING_KEY = "audit_chain_pending"

# Keys that must never be written to an audit record.
_FORBIDDEN_META_KEYS = {"password", "new_password", "otp", "token", "password_hash"}


def client_ip(request: Request | None) -> str | None:
    """The peer address as resolved by the ASGI server.

    Security (final verification S12): this used to read the raw
    X-Forwarded-For header itself. Any client could then pick its own "IP",
    which bypassed every per-IP rate limit (login, registration) and forged
    the IP in audit records and sessions. Proxy headers are now honoured
    only by uvicorn's ``--proxy-headers``, and only from the proxies listed
    in its ``FORWARDED_ALLOW_IPS`` (default 127.0.0.1). uvicorn then rewrites
    ``request.client`` itself, so this function sees the real client exactly
    when the request came through a trusted proxy.
    """
    if request is None:
        return None
    return request.client.host if request.client else None


def user_agent(request: Request | None) -> str | None:
    if request is None:
        return None
    return request.headers.get("user-agent")


def record(
    db: Session,
    action: str,
    *,
    actor_user_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: uuid.UUID | None = None,
    request: Request | None = None,
    meta: dict[str, Any] | None = None,
) -> AuditLog:
    safe_meta = {k: v for k, v in (meta or {}).items() if k not in _FORBIDDEN_META_KEYS}
    entry = AuditLog(
        # Phase E: explicit, per-entry timestamp (previously the column's
        # server default `now()`, which is the *transaction* start time --
        # every entry in one transaction tied). The chain seals in
        # (created_at, id) order, so distinct timestamps keep intra-
        # transaction order meaningful.
        created_at=utcnow(),
        action=action,
        actor_user_id=actor_user_id,
        target_type=target_type,
        target_id=target_id,
        ip=client_ip(request),
        user_agent=(user_agent(request) or None),
        # Phase E: stored exactly as it will later be hashed.
        meta=normalize_meta(safe_meta),
    )
    db.add(entry)
    db.flush()
    db.info[_PENDING_KEY] = True
    return entry


_READY_KEY = "audit_chain_seal_ready"


@event.listens_for(Session, "after_commit")
def _mark_ready_after_commit(session: Session) -> None:
    """Phase E: the transaction that wrote audit rows has committed -- mark
    them ready to seal. Sealing itself waits for `after_transaction_end`
    (below): during `after_commit` SQLAlchemy still holds this session's
    pooled connection, and opening a second one for the sealer from here
    was found (by tests/test_phase_e_audit_chain.py's concurrency test) to
    exhaust the pool under load -- every request holding one connection
    while waiting for a second is a pool deadlock."""
    if session.info.pop(_PENDING_KEY, False):
        session.info[_READY_KEY] = True


@event.listens_for(Session, "after_transaction_end")
def _seal_after_transaction_end(session: Session, transaction) -> None:
    """Runs after the root transaction has returned its connection to the
    pool, so the sealer's own short transaction never needs a second
    connection while this request still holds one. See
    app.services.audit_chain_service's module docstring for why sealing is
    deliberately NOT done inside the business transaction. Never raises."""
    if transaction.parent is not None or not session.info.pop(_READY_KEY, False):
        return
    bind = session.get_bind()
    from app.services.audit_chain_service import seal_pending_now

    seal_pending_now(bind)
