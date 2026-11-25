"""Phase 10 -- DB-side half of the blood request lifecycle.

Pairs with the pure transition table in `app.core.request_lifecycle`. This
module is what routes actually call: it locks the row, validates the
transition, applies it (status + the matching timestamp column), and writes
both audit trails this codebase already has a convention for --
`BloodRequestEvent` (the user-facing timeline shown in
`GET /blood-requests/{id}`) and `AuditLog` via `audit_service.record` (the
admin-facing security/compliance trail) -- with old state, new state, actor,
timestamp (from `AuditLog.created_at`), reason, and request ID on every
entry, per the Phase 10 spec's AUDIT section.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from fastapi import Request
from sqlalchemy.orm import Session

from app.core.request_lifecycle import OPEN_STATUSES, validate_transition
from app.core.time import utcnow
from app.models.blood_request import BloodRequest, BloodRequestEvent
from app.models.enums import BloodRequestStatus
from app.services import audit_service


def get_for_update(db: Session, request_id: uuid.UUID) -> BloodRequest | None:
    """Fetch a request row with a `SELECT ... FOR UPDATE` lock held for the
    rest of the current transaction.

    Every endpoint that can change `BloodRequest.status` -- cancel, fulfill
    (both the requester-facing and hospital-facing paths), respond (which
    can flip PENDING<->MATCHING), allocate (which only checks the status
    but must not race a concurrent fulfil/cancel), and the admin
    intervention endpoint -- fetches through this instead of a bare
    `db.get()`, so two concurrent requests touching the same row serialize:
    the second waits for the first's commit (which releases the lock) and
    then re-validates against the now-current status, rather than both
    reading the same stale status and both succeeding (the exact "two
    hospitals fulfilling the same request" / "two users cancelling
    simultaneously" / "repeated allocation" races the Phase 10 spec calls
    out).
    """
    return db.get(BloodRequest, request_id, with_for_update=True)


def expire_if_stale(
    db: Session, req: BloodRequest, *, today: date | None = None
) -> BloodRequest:
    """Lazily flip an open request whose `needed_by` has passed to EXPIRED.

    There is no scheduled job in this system (the same architectural fact
    documented on `FamilyLink`'s `_expire_if_stale` in
    `app/api/routes/family.py`), so -- following that exact precedent --
    expiry is enforced on read/action instead: every route that fetches a
    request calls this immediately afterward, before doing anything else
    with it. A request with no `needed_by` set never auto-expires (there is
    nothing to compare against); that's a real, intentional scope limit,
    not an oversight -- see the Phase 10 report.

    Commits immediately, but ONLY when a transition actually happens (the
    common case -- nothing to expire -- does not commit), so a caller that
    already holds a `get_for_update` lock on this row keeps holding it for
    the rest of its own transaction unless this function actually ended
    the request's lifecycle.
    """
    if (
        req.status in OPEN_STATUSES
        and req.needed_by is not None
        and req.needed_by < (today or utcnow().date())
    ):
        transition(
            db, req, BloodRequestStatus.EXPIRED,
            actor_user_id=None, reason="needed_by date passed with no fulfilment",
            event_type="expired", event_note="Automatically expired: needed-by date passed",
        )
        db.commit()
        db.refresh(req)
    return req


_TIMESTAMP_FIELD: dict[BloodRequestStatus, str] = {
    BloodRequestStatus.FULFILLED: "fulfilled_at",
    BloodRequestStatus.CANCELLED: "cancelled_at",
    BloodRequestStatus.EXPIRED: "expired_at",
}


def transition(
    db: Session,
    req: BloodRequest,
    target: BloodRequestStatus,
    *,
    actor_user_id: uuid.UUID | None,
    reason: str | None = None,
    request: Request | None = None,
    event_type: str | None = None,
    event_note: str | None = None,
    at: datetime | None = None,
    extra_meta: dict | None = None,
) -> None:
    """Validate and apply one transition. Raises
    `app.core.request_lifecycle.InvalidTransitionError` (callers turn this
    into an HTTP 409) if `target` isn't reachable from `req.status`.

    `extra_meta`, when given, is merged into the single unified
    `"blood_request.transition"` AuditLog entry's `meta` alongside
    old_status/new_status/reason -- e.g. `fulfill_request` passes
    `{"donations_recorded": len(accepted)}` so that detail isn't lost now
    that the old, separate `"blood_request.fulfilled"` AuditLog action has
    been folded into this one shared transition record.

    Does not commit -- like every other mutation in this codebase's routes,
    the caller commits once at the end of the request after any other
    related writes (notifications, donation records, inventory, ...),
    except `expire_if_stale` above, which is its own complete unit of work
    and commits itself for the reason explained there.
    """
    validate_transition(req.status, target)
    old = req.status
    when = at or utcnow()

    req.status = target
    field = _TIMESTAMP_FIELD.get(target)
    if field is not None:
        setattr(req, field, when)
    db.flush()

    db.add(BloodRequestEvent(
        request_id=req.id, actor_user_id=actor_user_id,
        event_type=event_type or target.value.lower(),
        note=event_note or reason,
    ))
    meta = {
        "old_status": old.value,
        "new_status": target.value,
        "reason": reason,
    }
    if extra_meta:
        meta.update(extra_meta)
    audit_service.record(
        db, "blood_request.transition", actor_user_id=actor_user_id,
        target_type="blood_request", target_id=req.id, request=request,
        meta=meta,
    )
