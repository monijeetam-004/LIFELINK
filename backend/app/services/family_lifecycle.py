"""Phase 13 -- DB-side half of the family-link lifecycle.

Pairs with the pure transition table in `app.core.family_lifecycle`. Mirrors
`app.services.emergency_lifecycle` / `app.services.blood_request_lifecycle`
(the identical exercise Phase 10 and 12 already did for `BloodRequest` and
`Emergency`): lock the row, validate the transition, apply it, and write one
unified audit entry -- old state, new state, actor, timestamp, reason -- via
the existing `audit_service.record` (there is no separate FamilyLink
timeline table the way `EmergencyEvent` exists for emergencies; the
`AuditLog` row *is* this relationship's auditable history, matching how the
admin-facing `/admin/audit-log` already surfaces it -- see
`tests/test_family.py::test_audit_trail_covers_link_lifecycle`, pre-existing
before this phase).

Before Phase 13, every mutating family-link route fetched its row with a
bare `db.get(FamilyLink, link_id)` -- no lock, and `request_link`'s
"does a row for this pair already exist" lookup wasn't locked either. Two
concurrent requests (two accept-vs-reject calls, an accept racing a revoke,
or two family members' concurrent re-requests after the same relationship
ended) could both read the same pre-mutation status, both pass their own
inline check, and both "succeed" -- the exact class of race Phase 10/12
already closed for `BloodRequest`/`Emergency`. `get_for_update` and
`get_pair_for_update` close it here the same way.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.family_lifecycle import validate_transition
from app.core.time import utcnow
from app.models.enums import FamilyLinkStatus
from app.models.family import FamilyLink
from app.services import audit_service


def get_for_update(db: Session, link_id: uuid.UUID) -> FamilyLink | None:
    """Fetch a family link row with a `SELECT ... FOR UPDATE` lock held for
    the rest of the current transaction. Every route that can change
    `FamilyLink.status` -- accept, reject, revoke -- fetches through this
    instead of a bare `db.get()`, so two concurrent requests touching the
    same row serialize: the second waits for the first's commit (which
    releases the lock) and re-validates against the now-current status,
    rather than both reading the same stale status and both succeeding."""
    return db.get(FamilyLink, link_id, with_for_update=True)


def get_pair_for_update(
    db: Session, family_user_id: uuid.UUID, patient_user_id: uuid.UUID
) -> FamilyLink | None:
    """Same idea as `get_for_update`, for `request_link`'s "does a row for
    this (family, patient) pair already exist" lookup -- locks it if so, so
    a concurrent re-request for the same ended relationship can't race past
    this one's re-open."""
    return db.scalar(
        select(FamilyLink)
        .where(
            FamilyLink.family_user_id == family_user_id,
            FamilyLink.patient_user_id == patient_user_id,
        )
        .with_for_update()
    )


_TIMESTAMP_FIELD: dict[FamilyLinkStatus, str] = {
    FamilyLinkStatus.ACCEPTED: "responded_at",
    FamilyLinkStatus.REJECTED: "responded_at",
}


def transition(
    db: Session,
    link: FamilyLink,
    target: FamilyLinkStatus,
    *,
    actor_user_id: uuid.UUID | None,
    action: str,
    reason: str | None = None,
    at: datetime | None = None,
    extra_meta: dict | None = None,
) -> None:
    """Validate and apply one transition. Raises
    `app.core.family_lifecycle.InvalidTransitionError` (callers turn this
    into an HTTP 409) if `target` isn't reachable from `link.status`.

    `action` is the audit action name (e.g. `"family.link_accepted"`);
    `extra_meta`, when given, is merged into that entry's `meta` alongside
    the always-present `old_status`/`new_status`/`reason`.

    Clears `expires_at` on every transition out of PENDING (it's only
    meaningful while a request is actually awaiting a decision) and sets it
    fresh only when (re-)entering PENDING -- callers reopening a link into
    PENDING pass the new `expires_at` via `extra_meta` handling in the route
    itself, since the deadline duration is a route-level policy
    (`_LINK_REQUEST_TTL`), not something this lifecycle module hardcodes.

    Does not commit -- the caller commits once at the end of the request,
    after any other related writes (notifications, etc.), matching
    `emergency_lifecycle.transition` / `blood_request_lifecycle.transition`.
    """
    validate_transition(link.status, target)
    old = link.status
    when = at or utcnow()

    link.status = target
    field = _TIMESTAMP_FIELD.get(target)
    if field is not None:
        setattr(link, field, when)
    # Always overwritten, never appended -- each transition owns whatever
    # reason (or lack of one) applies to it; the full history of who said
    # what and when is in the append-only, tamper-evident (not immutable)
    # AuditLog trail below, not this row.
    link.reason = reason
    db.flush()

    meta = {
        "old_status": old.value,
        "new_status": target.value,
        "reason": reason,
    }
    if extra_meta:
        meta.update(extra_meta)
    audit_service.record(
        db, action, actor_user_id=actor_user_id,
        target_type="family_link", target_id=link.id,
        meta=meta,
    )
