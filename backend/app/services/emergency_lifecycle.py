"""Phase 12 -- DB-side half of the emergency lifecycle.

Pairs with the pure transition table in `app.core.emergency_lifecycle`.
Mirrors `app.services.blood_request_lifecycle` (Phase 10's identical
exercise for `BloodRequest`) exactly: lock the row, validate the
transition, apply it, and write both audit trails this codebase already has
a convention for -- `EmergencyEvent` (the user-facing timeline shown in
`GET /emergencies/{id}`) and `AuditLog` via `audit_service.record` (the
admin-facing security/compliance trail) -- with old state, new state,
actor, timestamp, and reason on every entry.

Before Phase 12, every mutating emergency route fetched its row with a bare
`db.get(Emergency, emergency_id)` -- no lock. Two concurrent requests (e.g.
one user cancelling while another resolves, or two hospital responders both
hitting assistance-found) could both read the same pre-mutation status,
both pass their own inline check, and both "succeed" -- the exact class of
race Phase 10 already closed for `BloodRequest`. `get_for_update` closes it
here the same way.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime

from fastapi import Request
from sqlalchemy.orm import Session

from app.core.emergency_lifecycle import OPEN_STATUSES, validate_transition
from app.core.time import utcnow
from app.models.emergency import Emergency, EmergencyEvent
from app.models.enums import EmergencyStatus
from app.services import audit_service


def get_for_update(db: Session, emergency_id: uuid.UUID) -> Emergency | None:
    """Fetch an emergency row with a `SELECT ... FOR UPDATE` lock held for
    the rest of the current transaction. Every route that can change
    `Emergency.status` -- acknowledge, respond (which can flip
    ACTIVE/ACKNOWLEDGED<->MATCHING), assistance-found, cancel, resolve --
    fetches through this instead of a bare `db.get()`, so two concurrent
    requests touching the same row serialize: the second waits for the
    first's commit (which releases the lock) and then re-validates against
    the now-current status, rather than both reading the same stale status
    and both succeeding."""
    return db.get(Emergency, emergency_id, with_for_update=True)


def flag_deadline_exceeded_if_needed(db: Session, em: Emergency) -> Emergency:
    """Lazily record, once, that an open emergency's `deadline` has passed.

    There is no scheduled job anywhere in this codebase (the same
    architectural fact `blood_request_lifecycle.expire_if_stale` documents
    for `BloodRequest.needed_by`), so -- following that exact precedent --
    this is enforced on read/action instead: every route that fetches an
    emergency calls this immediately afterward, before doing anything else
    with it. An emergency with no `deadline` set never triggers this (there
    is nothing to compare against).

    Deliberately does NOT call `transition()` or otherwise touch
    `em.status` -- Section 9 of the Phase 31 spec is explicit that this
    system is decision-support only and must never falsely represent
    itself as making an autonomous clinical/operational decision. A missed
    deadline does not mean the emergency is resolved, cancelled, or any
    less urgent -- if anything the opposite -- so the only correct
    automatic action is to make the fact itself durable, queryable, and
    visible to the humans who can decide what to do about it: this sets
    `deadline_exceeded_at` once, writes an `EmergencyEvent` (shown in the
    same user-facing timeline every other lifecycle event uses), writes an
    `AuditLog` entry, and sends a best-effort notification to the
    emergency's creator.

    Commits immediately, but ONLY when it actually flags something (the
    common case -- deadline not yet passed, or already flagged -- does not
    commit), so a caller that already holds a `get_for_update` lock on this
    row keeps holding it for the rest of its own transaction unless this
    function actually acted.
    """
    if (
        em.status in OPEN_STATUSES
        and em.deadline is not None
        and em.deadline_exceeded_at is None
        and em.deadline < utcnow()
    ):
        em.deadline_exceeded_at = utcnow()
        db.flush()

        db.add(EmergencyEvent(
            emergency_id=em.id, actor_user_id=None,
            event_type="deadline_exceeded",
            note="Deadline passed without the emergency being resolved or cancelled.",
        ))
        audit_service.record(
            db, "emergency.deadline_exceeded", actor_user_id=None,
            target_type="emergency", target_id=em.id,
            meta={"status": em.status.value, "deadline": em.deadline.isoformat()},
        )

        # Best-effort, matching every other notification call site in this
        # codebase -- a notification failure must never roll back the fact
        # that the deadline was exceeded, and must never block the read/
        # action the caller is actually trying to perform.
        try:
            from app.services import notification_service

            notification_service.notify(
                db, em.created_by_user_id,
                type="emergency.deadline_exceeded",
                title="Emergency deadline passed",
                body="The deadline you set for this emergency has passed and it is still open.",
                data={"emergency_id": str(em.id)},
            )
        except Exception:  # pragma: no cover - notification is best-effort
            # Phase L: best-effort, but never silent.
            logging.getLogger("lifelink.emergency").warning(
                "deadline-exceeded notification failed for emergency %s", em.id, exc_info=True
            )

        db.commit()
        db.refresh(em)
    return em


_TIMESTAMP_FIELD: dict[EmergencyStatus, str] = {
    EmergencyStatus.RESOLVED: "resolved_at",
}


def transition(
    db: Session,
    em: Emergency,
    target: EmergencyStatus,
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
    `app.core.emergency_lifecycle.InvalidTransitionError` (callers turn this
    into an HTTP 409) if `target` isn't reachable from `em.status`.

    `extra_meta`, when given, is merged into the single unified
    `"emergency.transition"` AuditLog entry's `meta` alongside
    old_status/new_status/reason -- e.g. a cancellation that released an
    inventory reservation passes `{"inventory_released": True}` so that
    detail isn't lost.

    Does not commit -- like `blood_request_lifecycle.transition`, the
    caller commits once at the end of the request after any other related
    writes (notifications, pathway re-optimization, inventory, ...).
    """
    validate_transition(em.status, target)
    old = em.status
    when = at or utcnow()

    em.status = target
    field = _TIMESTAMP_FIELD.get(target)
    if field is not None:
        setattr(em, field, when)
    db.flush()

    db.add(EmergencyEvent(
        emergency_id=em.id, actor_user_id=actor_user_id,
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
        db, "emergency.transition", actor_user_id=actor_user_id,
        target_type="emergency", target_id=em.id, request=request,
        meta=meta,
    )
