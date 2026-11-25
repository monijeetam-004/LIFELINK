from __future__ import annotations

import uuid
from datetime import timedelta
from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, require_roles, verify_origin
from app.core import family_lifecycle
from app.core.config import settings
from app.core.family_lifecycle import InvalidTransitionError
from app.core.ratelimit import enforce_rate_limit
from app.core.time import utcnow
from app.models.enums import FamilyLinkStatus
from app.models.family import FamilyLink
from app.models.user import User, UserRole
from app.schemas.family import FamilyLinkActionIn, FamilyLinkCreate, FamilyLinkOut, PersonOut
from app.services import audit_service, notification_service
from app.services import family_lifecycle as family_lifecycle_service
from app.services.domain_service import get_user_by_email

router = APIRouter(prefix="/family", tags=["family"])

# How long an unanswered link request stays actionable before it lazily
# expires. There's no scheduled job in this system, so expiry is enforced
# on read/action instead (see `_expire_if_stale`), not by a background task.
# (Phase 13 non-negotiable rule 10: documented here, not faked with a
# scheduler that doesn't exist -- see LIFELINK_PHASE_13_REPORT.md section 10
# for the residual limitation this implies.)
_LINK_REQUEST_TTL = timedelta(days=30)


def _expire_if_stale(db: Session, link: FamilyLink) -> FamilyLink:
    """Flip a PENDING link past its `expires_at` to EXPIRED, in place.

    Called before anything reads or acts on a link so an old, ignored
    request never silently stays "actionable" forever. No human actor (this
    is a lazy, system-triggered transition -- see
    `app.services.family_lifecycle.transition`'s `actor_user_id=None`), but
    still goes through the same lifecycle service as every other transition
    for a single, consistent audit trail, and now also notifies the
    requester for real (Phase 13 -- previously silent).
    """
    if (
        link.status == FamilyLinkStatus.PENDING
        and link.expires_at is not None
        and link.expires_at <= utcnow()
    ):
        family_lifecycle_service.transition(
            db, link, FamilyLinkStatus.EXPIRED,
            actor_user_id=None, action="family.link_expired",
        )
        link.expires_at = None
        notification_service.notify(
            db, link.family_user_id, type="family.link_expired",
            title="Family access request expired",
            body="Your family-access request went unanswered and has expired. "
                 "You can send a new request at any time.",
            data={"link_id": str(link.id)},
        )
        db.commit()
    return link


def _hydrate(db: Session, link: FamilyLink) -> FamilyLinkOut:
    fam = db.get(User, link.family_user_id)
    pat = db.get(User, link.patient_user_id)
    out = FamilyLinkOut.model_validate(link)
    out.family = PersonOut.model_validate(fam) if fam else None
    out.patient = PersonOut.model_validate(pat) if pat else None
    out.allowed_transitions = family_lifecycle.allowed_transitions(link.status)
    return out


@router.get("/links", response_model=list[FamilyLinkOut])
def list_links(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[FamilyLinkOut]:
    rows = db.scalars(
        select(FamilyLink)
        .where(
            or_(
                FamilyLink.family_user_id == user.id,
                FamilyLink.patient_user_id == user.id,
            )
        )
        .order_by(FamilyLink.created_at.desc())
    ).all()
    for link in rows:
        _expire_if_stale(db, link)
    return [_hydrate(db, link) for link in rows]


@router.post("/links", response_model=FamilyLinkOut, status_code=status.HTTP_201_CREATED)
def request_link(
    payload: FamilyLinkCreate,
    request_: None = Depends(verify_origin),
    user: User = Depends(require_roles(UserRole.FAMILY)),
    db: Session = Depends(get_db),
) -> FamilyLinkOut:
    # Phase 13: caps repeated requests -- also the practical mitigation for
    # probing arbitrary emails through this endpoint's exists/doesn't-exist
    # response difference (see LIFELINK_PHASE_13_REPORT.md section 21).
    enforce_rate_limit(
        "family_link_request:account", str(user.id), *settings.rl_family_link_request_account
    )

    patient = get_user_by_email(db, payload.patient_email)
    # Phase 13: a suspended patient account gets the exact same "not found"
    # response as a genuinely nonexistent email -- distinguishing the two
    # would let a FAMILY caller learn which registered emails have since
    # been suspended, a real (if narrow) enumeration signal this phase
    # closes rather than leaves in place.
    if patient is None or patient.role != UserRole.PATIENT or not patient.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No patient with that email"
        )
    if patient.id == user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot link to yourself"
        )

    # Locks the row for the rest of this transaction if one already exists,
    # so a concurrent re-request for the same ended relationship can't race
    # past this check (see app.services.family_lifecycle's module docstring).
    existing = family_lifecycle_service.get_pair_for_update(db, user.id, patient.id)
    if existing is not None:
        _expire_if_stale(db, existing)
    # PENDING, ACCEPTED: a real, currently-live link already exists -- reject
    # the duplicate outright. REJECTED, REVOKED, EXPIRED: the old link ended;
    # re-requesting reopens the same row (below) rather than losing its history
    # (the full sequence of past decisions stays auditable via AuditLog --
    # see LIFELINK_PHASE_13_REPORT.md section 5).
    if existing is not None and existing.status in family_lifecycle.LIVE_STATUSES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Link already exists")

    expires_at = utcnow() + _LINK_REQUEST_TTL

    def _reopen(row: FamilyLink) -> FamilyLink:
        # Takes the specific row to reopen as a parameter (rather than
        # closing over the outer `existing`), so the IntegrityError-recovery
        # path below can reopen the row it just re-fetched, not whatever
        # `existing` happened to be when this request started.
        family_lifecycle_service.transition(
            db, row, FamilyLinkStatus.PENDING,
            actor_user_id=user.id, action="family.link_requested",
            reason=None, extra_meta={"relationship": payload.relationship},
        )
        row.relationship = payload.relationship
        row.requested_by_user_id = user.id
        row.expires_at = expires_at
        row.responded_at = None
        return row

    def _create_new() -> FamilyLink:
        new_link = FamilyLink(
            family_user_id=user.id,
            patient_user_id=patient.id,
            relationship=payload.relationship,
            status=FamilyLinkStatus.PENDING,
            requested_by_user_id=user.id,
            expires_at=expires_at,
        )
        db.add(new_link)
        return new_link

    if existing is not None:
        link = _reopen(existing)
    else:
        # Race-safe insert for a genuinely new (family, patient) pair: two
        # near-simultaneous first-time requests for the same pair both see
        # `existing is None` under the lock above (there's no row yet to
        # lock), so the real DB-level backstop is the SAVEPOINT here --
        # identical pattern (and identical care about ordering) as the
        # idempotent-emergency-creation fix in Phase 12: the object is
        # created and added to the session *inside* the `try`, after
        # `begin_nested()`, since `begin_nested()` itself autoflushes any
        # already-pending object while establishing the SAVEPOINT -- adding
        # it beforehand would let a genuine race's IntegrityError escape
        # uncaught, outside this `except`.
        savepoint = db.begin_nested()
        try:
            link = _create_new()
            db.flush()
            savepoint.commit()
            audit_service.record(
                db, "family.link_requested", actor_user_id=user.id,
                target_type="family_link", target_id=link.id,
                meta={"old_status": None, "new_status": FamilyLinkStatus.PENDING.value,
                      "relationship": payload.relationship},
            )
        except IntegrityError:
            savepoint.rollback()
            winner = family_lifecycle_service.get_pair_for_update(db, user.id, patient.id)
            if winner is None:
                raise  # pragma: no cover - constraint violation implies a row exists
            _expire_if_stale(db, winner)
            if winner.status in family_lifecycle.LIVE_STATUSES:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail="Link already exists"
                ) from None
            link = _reopen(winner)

    notification_service.notify(
        db, patient.id, type="family.link_requested",
        title="Family access request",
        body=f"{user.full_name} asked to manage your requests as your {payload.relationship}.",
        data={"link_id": str(link.id), "family_user_id": str(user.id)},
    )
    db.commit()
    return _hydrate(db, link)


def _get_link_for_action(db: Session, link_id: uuid.UUID) -> FamilyLink:
    link = family_lifecycle_service.get_for_update(db, link_id)
    if link is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Link not found")
    return _expire_if_stale(db, link)


def _deny_link_action(
    db: Session, link: FamilyLink, user: User, *, action: str, detail: str
) -> NoReturn:
    """A real link, being acted on by someone who isn't a party to it in the
    way this action requires -- unlike a plain 404 for a nonexistent link,
    this is always a genuine authorization violation, so it's unconditionally
    audited."""
    audit_service.record(
        db, "family_link.access_denied", actor_user_id=user.id,
        target_type="family_link", target_id=link.id,
        meta={"attempted_action": action},
    )
    db.commit()
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def _conflict_from_invalid_transition(exc: InvalidTransitionError) -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=f"Link is {exc.current.value.lower()}, cannot be moved to "
               f"{exc.target.value.lower()}",
    ) from exc


@router.post("/links/{link_id}/accept", response_model=FamilyLinkOut)
def accept_link(
    link_id: uuid.UUID,
    payload: FamilyLinkActionIn = FamilyLinkActionIn(),
    _: None = Depends(verify_origin),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FamilyLinkOut:
    link = _get_link_for_action(db, link_id)
    if link.patient_user_id != user.id:
        _deny_link_action(db, link, user, action="accept", detail="Not your request to accept")
    try:
        family_lifecycle_service.transition(
            db, link, FamilyLinkStatus.ACCEPTED,
            actor_user_id=user.id, action="family.link_accepted", reason=payload.reason,
        )
    except InvalidTransitionError as exc:
        _conflict_from_invalid_transition(exc)
    link.expires_at = None
    notification_service.notify(
        db, link.family_user_id, type="family.link_accepted",
        title="Family access granted",
        body=f"{user.full_name} accepted your request to manage their blood requests.",
        data={"link_id": str(link.id), "patient_user_id": str(user.id)},
    )
    db.commit()
    return _hydrate(db, link)


@router.post("/links/{link_id}/reject", response_model=FamilyLinkOut)
def reject_link(
    link_id: uuid.UUID,
    payload: FamilyLinkActionIn = FamilyLinkActionIn(),
    _: None = Depends(verify_origin),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FamilyLinkOut:
    """The patient declines a pending family-link request.

    Distinct from `revoke`: this is the patient's explicit "no" to a request
    that was never granted, kept as its own status (REJECTED) so the audit
    trail and history show a decline separately from an access grant later
    being taken away.
    """
    link = _get_link_for_action(db, link_id)
    if link.patient_user_id != user.id:
        _deny_link_action(db, link, user, action="reject", detail="Not your request to reject")
    try:
        family_lifecycle_service.transition(
            db, link, FamilyLinkStatus.REJECTED,
            actor_user_id=user.id, action="family.link_rejected", reason=payload.reason,
        )
    except InvalidTransitionError as exc:
        _conflict_from_invalid_transition(exc)
    link.expires_at = None
    notification_service.notify(
        db, link.family_user_id, type="family.link_rejected",
        title="Family access request declined",
        body=f"{user.full_name} declined your request to manage their blood requests."
             + (f" Reason: {payload.reason}" if payload.reason else ""),
        data={"link_id": str(link.id), "patient_user_id": str(user.id)},
    )
    db.commit()
    return _hydrate(db, link)


@router.post("/links/{link_id}/revoke", response_model=FamilyLinkOut)
def revoke_link(
    link_id: uuid.UUID,
    payload: FamilyLinkActionIn = FamilyLinkActionIn(),
    _: None = Depends(verify_origin),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FamilyLinkOut:
    link = _get_link_for_action(db, link_id)
    if user.id not in (link.family_user_id, link.patient_user_id):
        _deny_link_action(db, link, user, action="revoke", detail="Not part of this link")
    try:
        family_lifecycle_service.transition(
            db, link, FamilyLinkStatus.REVOKED,
            actor_user_id=user.id, action="family.link_revoked", reason=payload.reason,
        )
    except InvalidTransitionError as exc:
        _conflict_from_invalid_transition(exc)
    link.expires_at = None
    other = link.patient_user_id if user.id == link.family_user_id else link.family_user_id
    notification_service.notify(
        db, other, type="family.link_revoked", title="Family access ended",
        body=f"{user.full_name} ended a family access link."
             + (f" Reason: {payload.reason}" if payload.reason else ""),
        data={"link_id": str(link.id)},
    )
    db.commit()
    return _hydrate(db, link)
