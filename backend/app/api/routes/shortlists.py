"""Confidence-based donor shortlist endpoints (2026-10-31).

One shared service (app/services/donor_shortlist_service.py) behind every
path a patient, family member or hospital uses to look for donors: blood
requests and SOS emergencies alike. Read access follows the request's or
emergency's own visibility rules; donor names are shown only to the people
who manage it; exact donor coordinates, phone numbers and medical notes are
never returned.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, verify_origin
from app.core import unified_priority
from app.core.config import settings
from app.models.blood_request import BloodRequest
from app.models.donor_contact import DonorContactAttempt
from app.models.emergency import Emergency
from app.models.enums import DonorContactStatus, ShortlistSubject
from app.models.notification import Notification
from app.models.sms_notification import SmsNotification
from app.models.user import User, UserRole
from app.schemas.shortlist import (
    DemoSimulateIn,
    DemoSimulateOut,
    PriorityFactorOut,
    PriorityOut,
    ShortlistCandidateOut,
    ShortlistContactOut,
    ShortlistCountsOut,
    ShortlistOut,
    ShortlistPolicyOut,
)
from app.services import audit_service
from app.services import donor_shortlist_service as svc
from app.services.domain_service import has_accepted_link

router = APIRouter(tags=["shortlist"])

# How many not-yet-contacted candidates the requester sees: a short list,
# sized by what is still needed -- never the whole eligible pool.
def _preview_size(needed: int) -> int:
    return max(0, min(settings.shortlist_max_batch, needed * settings.shortlist_contact_multiplier))


# --------------------------------------------------------------------------- #
# Access
# --------------------------------------------------------------------------- #
def _request_access(db: Session, req: BloodRequest, user: User) -> tuple[bool, bool]:
    """(can_view, is_manager)."""
    if user.role == UserRole.ADMIN or user.id in (req.requester_user_id, req.patient_user_id):
        return True, True
    if user.role == UserRole.FAMILY and has_accepted_link(db, user.id, req.patient_user_id):
        return True, True
    from app.api.routes.matching import _hospital_sees_request

    if user.role == UserRole.HOSPITAL and _hospital_sees_request(db, req, user):
        return True, False
    return False, False


def _emergency_access(db: Session, em: Emergency, user: User) -> tuple[bool, bool]:
    from app.api.routes.emergencies import _can_view

    manager = user.role == UserRole.ADMIN or user.id == em.created_by_user_id or (
        em.patient_user_id is not None and user.id == em.patient_user_id)
    if not manager and em.patient_user_id and user.role == UserRole.FAMILY:
        manager = has_accepted_link(db, user.id, em.patient_user_id)
    if manager:
        return True, True
    if user.role == UserRole.DONOR:
        return False, False  # a donor never sees the other candidates
    return _can_view(db, em, user), False


def _load(db: Session, kind: ShortlistSubject, subject_id: uuid.UUID, user: User, *,
          lock: bool = False, need_manager: bool = False) -> tuple[svc.Subject, bool]:
    row = db.get(BloodRequest if kind is ShortlistSubject.BLOOD_REQUEST else Emergency,
                 subject_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    can_view, manager = (_request_access(db, row, user) if kind is ShortlistSubject.BLOOD_REQUEST
                         else _emergency_access(db, row, user))
    if not can_view:
        audit_service.record(db, "shortlist.access_denied", actor_user_id=user.id,
                             target_type=kind.value.lower(), target_id=row.id,
                             meta={"actor_role": user.role.value})
        db.commit()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if need_manager and not manager:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Only the people managing this request can do this")
    try:
        return svc.load_subject(db, kind, subject_id, lock=lock), manager
    except svc.ShortlistError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found") from exc


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
def _full_detail(user: User | None) -> bool:
    """Facility and admin users see factor-level detail (counts, ages);
    patients and family members see a minimal summary -- other people's
    response histories are not theirs to read."""
    return user is not None and user.role in (UserRole.HOSPITAL, UserRole.ADMIN)


SUMMARY_SCORE_BAND = 10.0


def _score_out(score_0_100: float, full: bool) -> float:
    """Privacy round 2 (2026-11-03): patients and family see the priority in
    10-point bands. An exact score, together with the published weights and
    the factor statuses, lets a viewer back out the distance term (0.4 points
    per km) and parts of the response history. Ordering always uses the
    exact score; only the displayed number is coarsened."""
    if full:
        return score_0_100
    return float(round(score_0_100 / SUMMARY_SCORE_BAND) * SUMMARY_SCORE_BAND)


def _priority_out(p: unified_priority.PriorityScore, full: bool) -> PriorityOut:
    return PriorityOut(
        model=p.model_tag, score=_score_out(p.score, full), score_available=p.score_available,
        evidence_coverage=p.coverage, evidence_level=p.evidence_level,
        missing_data_baseline=p.missing_baseline if full else None,
        availability_status=p.availability_status.value,
        factors=[PriorityFactorOut(
            name=f.name, label=f.label, weight=f.weight if full else None,
            value=f.value if full else None,
            contribution=f.contribution if full else None, status=f.status,
            detail=f.detail if full else f.summary) for f in p.factors],
    )


def _coarse_describe(a) -> str:
    if a.distance_km is None:
        return a.describe()
    return f"about {_distance_out(a.distance_km, False):g} km away" + (
        " (location may be out of date)" if a.location_stale else "")


def _distance_out(km: float | None, full: bool) -> float | None:
    """Release gate C (2026-11-03): never a full-precision distance. 0.1 km
    for facility and admin users, whole kilometres (at least 1) for everyone
    else, so that distances from several requests cannot pin down a donor."""
    if km is None:
        return None
    return round(km, 1) if full else float(max(1, round(km)))


def _travel_out(km: float | None, minutes: int | None, full: bool) -> int | None:
    """Privacy round 2: the travel estimate is distance / 45 km/h, so in whole
    minutes it would reveal the distance to 0.75 km -- finer than the whole-km
    distance shown to patients and family. For them it is derived from the
    ROUNDED distance, so it carries no extra precision."""
    if full or minutes is None or km is None:
        return minutes
    whole_km = float(max(1, round(km)))  # = _distance_out(km, False)
    return int(round(whole_km / svc.ASSUMED_TRAVEL_KMPH * 60))


_SIM_MARKER = "[sim-wait]"
RECHECK_GENERIC = "the eligibility re-check did not pass"
SIM_REFUSED_PREFIX = "simulated accept refused by real checks"
SIM_REFUSED_GENERIC = "simulated acceptance refused by the eligibility checks"
RESTRICTED_GENERIC = "details are visible to facility staff"


def _reason_out(reason: str | None, contact_status: DonorContactStatus,
                full: bool) -> str | None:
    """Privacy round 2 (2026-11-03). Facility/admin users see the stored
    reason (minus the demo simulator's internal "[sim-wait]" marker, which is
    bookkeeping, not a reason). For patients and family members:

    * the LABEL is chosen by the contact's status: an ELIGIBILITY_RECHECK_REQUIRED
      contact always reads "the eligibility re-check did not pass", whatever
      the stored wording, and no other status ever gets that label;
    * the operational reasons (alert already received, timeout, answered the
      open alert, simulator timeout) are shown as written;
    * a simulated acceptance refused by the real checks carries the check's
      own message, so it is replaced by a generic sentence;
    * any other text under any other status fails closed to a neutral
      "details are visible to facility staff" -- e.g. a re-check reason left
      on a row written before re-check reasons were cleared on withdrawal."""
    if reason is None:
        return None
    text = reason.replace(_SIM_MARKER, "").strip() or None
    if full or text is None:
        return text
    if contact_status is DonorContactStatus.ELIGIBILITY_RECHECK_REQUIRED:
        return RECHECK_GENERIC
    if text in svc.OPERATIONAL_REASONS:
        return text
    if text.startswith(SIM_REFUSED_PREFIX):
        return SIM_REFUSED_GENERIC
    return RESTRICTED_GENERIC


def build_out(db: Session, subject: svc.Subject, *, manager: bool, user: User | None = None,
              strategy: unified_priority.RankingStrategy | None = None) -> ShortlistOut:
    strategy = strategy or svc.last_strategy(db, subject)
    full = _full_detail(user)
    ev = svc.evaluate(db, subject, strategy=strategy)
    contacts = list(db.scalars(select(DonorContactAttempt).where(subject.contact_filter())
                               .order_by(DonorContactAttempt.batch,
                                         DonorContactAttempt.rank_at_contact)).all())
    users = {u.id: u for u in db.scalars(select(User).where(
        User.id.in_([c.donor_user_id for c in contacts]))).all()} if contacts else {}
    from app.models.profile import DonorProfile

    blood = dict(db.execute(select(DonorProfile.user_id, DonorProfile.blood_type).where(
        DonorProfile.user_id.in_(list(users)))).all()) if users else {}
    nids = [c.notification_id for c in contacts if c.notification_id]
    read = dict(db.execute(select(Notification.id, Notification.read_at).where(
        Notification.id.in_(nids))).all()) if nids else {}
    sms = dict(db.execute(select(SmsNotification.notification_id, SmsNotification.status).where(
        SmsNotification.notification_id.in_(nids))).all()) if nids else {}
    contacted_ids = {c.donor_user_id for c in contacts}
    c = svc.counts(db, subject)
    accepted = c["ACCEPTED"]
    confirmed = c["CONFIRMED_FOR_COORDINATION"]
    awaiting = c["AWAITING_RESPONSE"] + c["QUEUED"]
    needed = svc.needed_donors(subject)
    remaining = max(0, needed - confirmed)
    pending = [cand for cand in ev.candidates if cand.donor_user_id not in contacted_ids]
    preview = pending[:_preview_size(max(0, needed - confirmed - accepted))]

    escalation: list[str] = []
    if not subject.is_open:
        state = "CLOSED"
    elif needed == 0 or remaining == 0:
        state = "SATISFIED"
    elif not ev.candidates and not contacts:
        state = "NO_ELIGIBLE"
        escalation.append("No eligible donor was found within the distance limit. Escalate to "
                          "blood banks (emergency pathway / hospital alerts) or ask an "
                          "administrator for an audited distance override.")
    elif not contacts:
        state = "NOT_STARTED"
    elif confirmed > 0 or accepted > 0:
        state = "PARTIAL"
    else:
        state = "AWAITING"
    if c["TIMED_OUT"]:
        escalation.append(f"{c['TIMED_OUT']} contacted donor(s) did not answer in time.")
    if remaining and subject.is_open and pending and awaiting < remaining:
        escalation.append(f"{len(pending)} more eligible donor(s) can be contacted.")
    if remaining and subject.is_open and not pending and awaiting == 0 and contacts:
        escalation.append("Every eligible donor has been contacted; escalate to blood banks.")

    # `needed` is the number of donors the request requires after units already
    # fulfilled or reserved from inventory; `remaining` subtracts confirmed
    # donors. (Wording fixed 2026-10-31: this said "still needed" for the
    # total, which read as unmet even after every donor was confirmed.)
    summary = (f"{confirmed} confirmed and {accepted} accepted (awaiting confirmation) of "
               f"{needed} donor(s) required; {awaiting} awaiting an answer; "
               f"{len(ev.candidates)} eligible candidate(s) found.")
    if state == "PARTIAL" and remaining:
        summary += f" The request is still {remaining} donor(s) short."

    def name(uid):
        return users[uid].full_name if manager and uid in users else None

    return ShortlistOut(
        policy=svc.POLICY_NAME, strategy=ev.strategy.value, scoring_model=ev.model_tag,
        explanation_level="FULL" if full else "SUMMARY",
        subject=subject.kind, subject_id=subject.id,
        is_open=subject.is_open, urgency=subject.urgency, units_needed=subject.units_needed,
        units_fulfilled=subject.units_fulfilled,
        units_reserved_inventory=subject.units_reserved_inventory,
        donors_still_needed=needed, remaining_unmet=remaining, state=state, summary=summary,
        counts=ShortlistCountsOut(
            compatible_pool=ev.compatible_pool, eligible_candidates=len(ev.candidates),
            not_yet_contacted=len(pending), contacted=len(contacts), awaiting_response=awaiting,
            accepted=accepted, confirmed_for_coordination=confirmed, declined=c["DECLINED"],
            unavailable=c["UNAVAILABLE"], timed_out=c["TIMED_OUT"],
            notification_failed=c["NOTIFICATION_FAILED"], withdrawn=c["WITHDRAWN"],
            eligibility_recheck_required=c["ELIGIBILITY_RECHECK_REQUIRED"],
        ),
        excluded=ev.excluded,
        next_to_contact=[
            ShortlistCandidateOut(
                donor_ref=cand.ref, donor_name=cand.name if manager else None,
                blood_type=cand.blood_type, rank=cand.rank,
                score=round(_score_out(cand.priority.score, full) / 100.0, 4),
                components=cand.components if full else {},
                priority=_priority_out(cand.priority, full),
                next_action=unified_priority.next_action(cand.priority.availability_status,
                                                         contacted=False),
                distance_basis=cand.distance.basis.value,
                distance_km=_distance_out(cand.distance.distance_km, full),
                travel_minutes_estimate=_travel_out(cand.distance.distance_km,
                                                    cand.travel_minutes, full),
                location_stale=cand.distance.location_stale,
                explanation=(cand.reasons if full else [_coarse_describe(cand.distance)]),
                synthetic=cand.synthetic,
            ) for cand in preview
        ],
        contacts=[
            ShortlistContactOut(
                id=ct.id, donor_ref=str(ct.donor_user_id)[:8], donor_name=name(ct.donor_user_id),
                blood_type=blood.get(ct.donor_user_id), status=ct.status, batch=ct.batch,
                rank_at_contact=ct.rank_at_contact,
                score_at_contact=(ct.score_at_contact if full else round(
                    _score_out(ct.score_at_contact * 100.0, False) / 100.0, 4)),
                ranking_strategy=ct.ranking_strategy, scoring_model=ct.scoring_model,
                contacted_at=ct.contacted_at, timeout_at=ct.timeout_at,
                responded_at=ct.responded_at,
                eligibility_rechecked_at=ct.eligibility_rechecked_at,
                confirmed_at=ct.confirmed_at,
                status_reason=_reason_out(ct.status_reason, ct.status, full),
                notification_state=(None if ct.notification_id is None else
                                    ("READ" if read.get(ct.notification_id) else "SENT_UNREAD")),
                sms_status=(sms[ct.notification_id].value if ct.notification_id in sms else None),
                simulated=ct.simulated,
                synthetic=(ct.donor_user_id in users and users[ct.donor_user_id].email.endswith(
                    "@" + svc.SYNTHETIC_EMAIL_DOMAIN)),
            ) for ct in contacts
        ],
        escalation=escalation, demo_mode=settings.demo_mode,
        disclaimer=svc.policy_snapshot()["disclaimer"],
    )


def _get(kind: ShortlistSubject, subject_id: uuid.UUID, user: User, db: Session,
         strategy: unified_priority.RankingStrategy | None = None) -> ShortlistOut:
    subject, manager = _load(db, kind, subject_id, user)
    if subject.is_open and manager:
        if svc.expire_timeouts(db, subject):
            svc.maybe_escalate(db, subject)
        db.commit()
    return build_out(db, subject, manager=manager, user=user, strategy=strategy)


def _contact(kind, subject_id, user, db, strategy=None) -> ShortlistOut:
    subject, manager = _load(db, kind, subject_id, user, lock=True, need_manager=True)
    strategy = strategy or svc.last_strategy(db, subject)
    try:
        created = svc.contact_next_batch(db, subject, actor_user_id=user.id, strategy=strategy)
    except svc.ShortlistError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    # The decision boundary is audited (not every page render): which
    # strategy and which model/config produced this batch. Per-donor factor
    # snapshots are on the contact rows themselves.
    audit_service.record(db, "shortlist.contacted", actor_user_id=user.id,
                         target_type=kind.value.lower(), target_id=subject.id,
                         meta={"contacted": len(created), "strategy": strategy.value,
                               "scoring_model": unified_priority.config_from_settings()
                               .model_tag()})
    db.commit()
    return build_out(db, subject, manager=manager, user=user, strategy=strategy)


def _confirm(kind, subject_id, contact_id, user, db) -> ShortlistOut:
    subject, manager = _load(db, kind, subject_id, user, lock=True, need_manager=True)
    contact = db.get(DonorContactAttempt, contact_id, with_for_update=True)
    if contact is None or (contact.blood_request_id or contact.emergency_id) != subject.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    try:
        svc.confirm(db, subject, contact, actor_user_id=user.id)
    except svc.ShortlistError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    # Staff-only record (the audit log is admin-readable): a failed re-check
    # keeps its reason here, while the request's public event history does
    # not (privacy round 2, 2026-11-03).
    meta = {"contact_id": str(contact.id), "result": contact.status.value}
    if contact.status is DonorContactStatus.ELIGIBILITY_RECHECK_REQUIRED:
        meta["reason"] = contact.status_reason
    audit_service.record(db, "shortlist.confirmation", actor_user_id=user.id,
                         target_type=kind.value.lower(), target_id=subject.id, meta=meta)
    db.commit()
    return build_out(db, subject, manager=manager, user=user)


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@router.get("/shortlist/policy", response_model=ShortlistPolicyOut)
def shortlist_policy(user: User = Depends(get_current_user)) -> ShortlistPolicyOut:
    snap = svc.policy_snapshot()
    if not _full_detail(user):  # scoring internals for facility/admin users only
        snap["weights"] = None
        snap["min_coverage"] = None
    return ShortlistPolicyOut(**snap)


_STRATEGY_Q = Query(default=None, description=(
    "UNIFIED_PRIORITY (default) or one of the four matching-engine strategies: NEAREST, "
    "TIME_AWARE, TIME_RESILIENCE, EVENT_DRIVEN. Omitted: the strategy of the last contact "
    "batch, else UNIFIED_PRIORITY. Unknown values are rejected (422)."))


@router.get("/blood-requests/{request_id}/shortlist", response_model=ShortlistOut)
def blood_request_shortlist(request_id: uuid.UUID, user: User = Depends(get_current_user),
                            db: Session = Depends(get_db),
                            strategy: unified_priority.RankingStrategy | None = _STRATEGY_Q
                            ) -> ShortlistOut:
    return _get(ShortlistSubject.BLOOD_REQUEST, request_id, user, db, strategy)


@router.post("/blood-requests/{request_id}/shortlist/contact-next", response_model=ShortlistOut)
def blood_request_contact_next(request_id: uuid.UUID, _: None = Depends(verify_origin),
                               user: User = Depends(get_current_user),
                               db: Session = Depends(get_db),
        strategy: unified_priority.RankingStrategy | None = _STRATEGY_Q) -> ShortlistOut:
    return _contact(ShortlistSubject.BLOOD_REQUEST, request_id, user, db, strategy)


@router.post("/blood-requests/{request_id}/shortlist/contacts/{contact_id}/confirm",
             response_model=ShortlistOut)
def blood_request_confirm(request_id: uuid.UUID, contact_id: uuid.UUID,
                          _: None = Depends(verify_origin),
                          user: User = Depends(get_current_user),
                          db: Session = Depends(get_db)) -> ShortlistOut:
    return _confirm(ShortlistSubject.BLOOD_REQUEST, request_id, contact_id, user, db)


@router.get("/emergencies/{emergency_id}/shortlist", response_model=ShortlistOut)
def emergency_shortlist(emergency_id: uuid.UUID, user: User = Depends(get_current_user),
                        db: Session = Depends(get_db),
                        strategy: unified_priority.RankingStrategy | None = _STRATEGY_Q
                        ) -> ShortlistOut:
    return _get(ShortlistSubject.EMERGENCY, emergency_id, user, db, strategy)


@router.post("/emergencies/{emergency_id}/shortlist/contact-next", response_model=ShortlistOut)
def emergency_contact_next(emergency_id: uuid.UUID, _: None = Depends(verify_origin),
                           user: User = Depends(get_current_user),
                           db: Session = Depends(get_db),
        strategy: unified_priority.RankingStrategy | None = _STRATEGY_Q) -> ShortlistOut:
    return _contact(ShortlistSubject.EMERGENCY, emergency_id, user, db, strategy)


@router.post("/emergencies/{emergency_id}/shortlist/contacts/{contact_id}/confirm",
             response_model=ShortlistOut)
def emergency_confirm(emergency_id: uuid.UUID, contact_id: uuid.UUID,
                      _: None = Depends(verify_origin),
                      user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)) -> ShortlistOut:
    return _confirm(ShortlistSubject.EMERGENCY, emergency_id, contact_id, user, db)


@router.post("/demo/shortlist/{subject}/{subject_id}/simulate", response_model=DemoSimulateOut)
def demo_simulate(subject: ShortlistSubject, subject_id: uuid.UUID, payload: DemoSimulateIn,
                  _: None = Depends(verify_origin), user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)) -> DemoSimulateOut:
    """DEMO_MODE only (refused in production by configuration validation),
    ADMIN only, SYNTHETIC donors only. Records simulated answers through the
    same respond code path a real donor uses; every simulated row is
    labelled `simulated`."""
    from app.services import demo_simulator

    if not settings.demo_mode or settings.is_production:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")
    subj, _m = _load(db, subject, subject_id, user)
    result = demo_simulator.simulate(db, subj, force_timeouts=payload.force_timeouts,
                                     actor=user)
    audit_service.record(db, "demo.shortlist_simulated", actor_user_id=user.id,
                         target_type=subject.value.lower(), target_id=subject_id,
                         meta={k: v for k, v in result.items() if k != "skipped_non_synthetic"})
    db.commit()
    subj = svc.load_subject(db, subject, subject_id)
    return DemoSimulateOut(simulated={k: v for k, v in result.items()
                                      if k != "skipped_non_synthetic"},
                           skipped_non_synthetic=result["skipped_non_synthetic"],
                           shortlist=build_out(db, subj, manager=True, user=user))


__all__ = ["router", "build_out", "DonorContactStatus"]
