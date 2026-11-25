"""Confidence-based donor shortlist (2026-10-31).

Goal: when a patient, family member or hospital looks for donors, show a
SMALL, honest list -- who is eligible, who was actually contacted, who
answered, who is confirmed -- instead of a long list of people who may
never answer. Ranking and confirmation are kept strictly apart:

    hard eligibility  ->  ranking score  ->  contact (batches)  ->  response
    (pass/fail, never      (orders the       (bounded, deduped,     (ACCEPTED,
     overridden by a        candidates        with timeouts)         then confirmed
     high score)            only)                                    by a coordinator)

What already existed and is reused, not replaced
------------------------------------------------
The audit brief refers to an "existing five-factor credibility score". No such
score exists in this repository (code, paper, documents and the full git
history were searched on 2026-10-31). What exists is the per-candidate factor
set of the explainable matching engine (`app.core.matching_engine.MatchFactors`)
-- compatibility, availability, donation-interval eligibility, identity
verification, distance, acceptance and availability staleness -- ranked by
four LEXICOGRAPHIC strategies. Those strategies are untouched. This module is
a separate, explicitly named ranking policy, ``CONFIDENCE_SHORTLIST``, that
reuses the same stored signals plus new evidence (response history, explicit
availability confirmations, location age).

Hard filters (a candidate failing any of them is never shortlisted)
-------------------------------------------------------------------
compatible blood group for the requested component; active account;
available (availability state AVAILABLE); donation interval elapsed
(`app.core.eligibility`, jurisdiction policy); no other active accepted
commitment; inside the shared distance policy (`app.core.distance_policy`);
when a deadline is known and a distance was measured, a straight-line travel
estimate (labelled, 45 km/h -- the planner's own assumption) must arrive
before it. Donors already contacted for this request are never invited twice.

Ordering (2026-10-31, second revision)
--------------------------------------
Eligible candidates are ordered by the Unified Donor Priority Score
(``app.core.unified_priority``, model UNIFIED_PRIORITY, versioned and
fingerprinted) by default, or -- when the coordinator selects one -- by any
of the four unchanged lexicographic strategies of ``app.core.matching_engine``
(run through ``matching_engine.rank_candidates`` itself). The unified score
supersedes this module's first CONFIDENCE_SHORTLIST score (five weights
SHORTLIST_WEIGHT_*), which it subsumes. Each contacted row records the
strategy, the model tag and a minimal factor snapshot.

Nothing here is a guarantee: an ACCEPTED or even CONFIRMED donor still needs
the blood centre's own screening, and a high score never means "available".
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import compatibility, distance_policy, matching_engine, unified_priority
from app.core.config import settings
from app.core.eligibility import active_policy, is_eligible
from app.core.emergency_lifecycle import OPEN_STATUSES as EMERGENCY_OPEN
from app.core.request_lifecycle import OPEN_STATUSES as REQUEST_OPEN
from app.core.time import utcnow
from app.models.blood_request import BloodRequest, BloodRequestEvent, DonorResponse
from app.models.donation import Donation
from app.models.donor_contact import DonorContactAttempt
from app.models.emergency import Emergency, EmergencyEvent, EmergencyResponse
from app.models.emergency_pathway import EmergencyPathway, PathwayReservation
from app.models.enums import (
    BloodComponent,
    DonationVerificationStatus,
    DonorContactStatus,
    DonorResponseStatus,
    EmergencyOverrideKind,
    EmergencyPathwayStatus,
    ShortlistSubject,
    Urgency,
)
from app.models.notification import Notification
from app.models.profile import DonorProfile
from app.models.user import User

POLICY_NAME = unified_priority.MODEL_ID  # superseded CONFIDENCE_SHORTLIST (2026-10-31)
ASSUMED_TRAVEL_KMPH = 45.0  # same labelled assumption as the pathway planner
SYNTHETIC_EMAIL_DOMAIN = "synthetic.lifelink.example"

# Operational contact reasons (privacy round 2, 2026-11-03). These describe
# the contact process, not the donor, and are the only free-text reasons a
# patient or family member is shown as written; see shortlists._reason_out.
REASON_PRIOR_ALERT = "already received the open alert"
REASON_TIMEOUT = "no answer before the response timeout"
REASON_OPEN_ALERT = "answered the open alert (not shortlisted)"
REASON_SIM_TIMEOUT = "simulated: no answer (timeout forced by demo simulator)"
OPERATIONAL_REASONS = frozenset(
    {REASON_PRIOR_ALERT, REASON_TIMEOUT, REASON_OPEN_ALERT, REASON_SIM_TIMEOUT})

OUTSTANDING = (DonorContactStatus.QUEUED, DonorContactStatus.AWAITING_RESPONSE)
POSITIVE = (DonorContactStatus.ACCEPTED, DonorContactStatus.CONFIRMED_FOR_COORDINATION)
CLOSED_NEGATIVE = (
    DonorContactStatus.DECLINED, DonorContactStatus.UNAVAILABLE, DonorContactStatus.TIMED_OUT,
    DonorContactStatus.NOTIFICATION_FAILED, DonorContactStatus.WITHDRAWN,
    DonorContactStatus.ELIGIBILITY_RECHECK_REQUIRED,
)


class ShortlistError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Subject (blood request or emergency)
# --------------------------------------------------------------------------- #
@dataclass
class Subject:
    kind: ShortlistSubject
    id: uuid.UUID
    row: BloodRequest | Emergency
    recipient_type: object
    component: BloodComponent
    units_needed: int
    units_fulfilled: int
    units_reserved_inventory: int
    urgency: Urgency
    lat: float | None
    lng: float | None
    city: str | None
    deadline: datetime | None
    is_open: bool
    owner_user_id: uuid.UUID
    distance_override: bool
    broadcast_type: str

    def fk(self) -> dict:
        if self.kind is ShortlistSubject.BLOOD_REQUEST:
            return {"blood_request_id": self.id}
        return {"emergency_id": self.id}

    def contact_filter(self):
        if self.kind is ShortlistSubject.BLOOD_REQUEST:
            return DonorContactAttempt.blood_request_id == self.id
        return DonorContactAttempt.emergency_id == self.id


def load_subject(db: Session, kind: ShortlistSubject, subject_id: uuid.UUID, *,
                 lock: bool = False) -> Subject:
    from app.services import emergency_pathway_service as eps

    if kind is ShortlistSubject.BLOOD_REQUEST:
        req = db.get(BloodRequest, subject_id, with_for_update=lock)
        if req is None:
            raise ShortlistError("not found")
        deadline = None
        if req.needed_by is not None:
            deadline = datetime(req.needed_by.year, req.needed_by.month, req.needed_by.day,
                                23, 59, tzinfo=UTC)
        return Subject(
            kind=kind, id=req.id, row=req, recipient_type=req.blood_type,
            component=BloodComponent.WHOLE_BLOOD, units_needed=req.units_needed,
            units_fulfilled=req.fulfilled_units, units_reserved_inventory=0,
            urgency=req.urgency, lat=req.latitude, lng=req.longitude, city=req.city,
            deadline=deadline, is_open=req.status in REQUEST_OPEN,
            owner_user_id=req.requester_user_id, distance_override=False,
            broadcast_type="blood_request.new",
        )
    em = db.get(Emergency, subject_id, with_for_update=lock)
    if em is None or em.blood_type is None:
        raise ShortlistError("not found")
    reserved = int(db.scalar(
        select(func.coalesce(func.sum(PathwayReservation.units), 0))
        .join(EmergencyPathway, EmergencyPathway.id == PathwayReservation.pathway_id)
        .where(PathwayReservation.emergency_id == em.id, PathwayReservation.active.is_(True),
               EmergencyPathway.status == EmergencyPathwayStatus.ACTIVE)
    ) or 0)
    return Subject(
        kind=kind, id=em.id, row=em, recipient_type=em.blood_type,
        component=em.component or BloodComponent.WHOLE_BLOOD,
        units_needed=em.units_needed or 1, units_fulfilled=0,
        units_reserved_inventory=reserved, urgency=em.urgency, lat=em.latitude,
        lng=em.longitude, city=em.city, deadline=em.deadline,
        is_open=em.status in EMERGENCY_OPEN, owner_user_id=em.created_by_user_id,
        distance_override=eps.active_override(db, em.id, EmergencyOverrideKind.DONOR_DISTANCE),
        broadcast_type="emergency.new",
    )


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
def weights() -> dict[str, float]:
    """Normalised Unified Donor Priority weights (app.core.unified_priority)."""
    return unified_priority.config_from_settings().weights


@dataclass
class History:
    invited: int = 0
    answered: int = 0
    accepted: int = 0
    last_response_at: datetime | None = None
    last_donation_at: datetime | None = None


# --------------------------------------------------------------------------- #
# Candidate evaluation
# --------------------------------------------------------------------------- #
@dataclass
class Candidate:
    donor_user_id: uuid.UUID
    ref: str
    name: str
    blood_type: object
    synthetic: bool
    distance: distance_policy.DistanceAssessment
    travel_minutes: int | None
    priority: unified_priority.PriorityScore
    has_accepted: bool
    last_donation_date: date | None
    identity_verified: bool
    profile_updated_at: datetime | None
    reasons: list[str] = field(default_factory=list)
    rank: int = 0
    # The evidence the score was computed from (kept so the score can be
    # re-derived, e.g. by the offline comparison in
    # scripts/evaluate_ranking_strategies.py).
    inputs: unified_priority.PriorityInputs | None = None

    @property
    def score(self) -> float:
        """The unified priority score on a 0..1 scale (stored per contact)."""
        return round(self.priority.score / 100.0, 4)

    @property
    def components(self) -> dict[str, float | None]:
        return {f.name: f.value for f in self.priority.factors}


@dataclass
class Evaluation:
    subject: Subject
    candidates: list[Candidate]
    excluded: dict[str, int]
    compatible_pool: int
    strategy: unified_priority.RankingStrategy = unified_priority.RankingStrategy.UNIFIED_PRIORITY
    model_tag: str = ""


def _history(db: Session, ids: list[uuid.UUID],
             subject: Subject | None = None) -> dict[uuid.UUID, History]:
    """Past behaviour on OTHER requests/emergencies. The current subject is
    excluded: an answer to it is already `availability` (accepted this
    request), and counting it again in `response`/`acceptance` would reward
    the same evidence twice (UNIFIED_PRIORITY double-counting rule)."""
    out = {i: History() for i in ids}
    if not ids:
        return out
    sid = str(subject.id) if subject is not None else ""
    # Invitations = shortlist contacts + broadcast alerts actually received.
    for uid, n in db.execute(
        select(Notification.user_id, func.count()).where(
            Notification.user_id.in_(ids),
            Notification.type.in_(("blood_request.new", "emergency.new",
                                   "blood_request.shortlist_invite",
                                   "emergency.shortlist_invite")),
            func.coalesce(Notification.data["request_id"].astext, "") != sid,
            func.coalesce(Notification.data["emergency_id"].astext, "") != sid)
        .group_by(Notification.user_id)
    ).all():
        out[uid].invited = int(n)
    for model, col, own in (
            (DonorResponse, DonorResponse.donor_user_id, DonorResponse.request_id),
            (EmergencyResponse, EmergencyResponse.responder_user_id,
             EmergencyResponse.emergency_id)):
        q = select(col, func.count(),
                   func.count().filter(model.status == DonorResponseStatus.ACCEPTED),
                   func.max(model.updated_at)).where(col.in_(ids))
        if subject is not None:
            q = q.where(own != subject.id)
        for uid, n, acc, last in db.execute(q.group_by(col)).all():
            h = out[uid]
            h.answered += int(n)
            h.accepted += int(acc)
            if last is not None and (h.last_response_at is None or last > h.last_response_at):
                h.last_response_at = last
    for uid, last in db.execute(
        select(Donation.donor_user_id, func.max(Donation.created_at))
        .where(Donation.donor_user_id.in_(ids)).group_by(Donation.donor_user_id)
    ).all():
        out[uid].last_donation_at = last
    for h in out.values():  # answers can exceed recorded alerts (e.g. pre-alert data)
        h.invited = max(h.invited, h.answered)
    return out


def _committed_elsewhere(db: Session, ids: list[uuid.UUID], subject: Subject) -> set[uuid.UUID]:
    if not ids:
        return set()
    q1 = (select(DonorResponse.donor_user_id)
          .join(BloodRequest, BloodRequest.id == DonorResponse.request_id)
          .where(DonorResponse.donor_user_id.in_(ids),
                 DonorResponse.status == DonorResponseStatus.ACCEPTED,
                 BloodRequest.status.in_(REQUEST_OPEN)))
    q2 = (select(EmergencyResponse.responder_user_id)
          .join(Emergency, Emergency.id == EmergencyResponse.emergency_id)
          .where(EmergencyResponse.responder_user_id.in_(ids),
                 EmergencyResponse.status == DonorResponseStatus.ACCEPTED,
                 Emergency.status.in_(EMERGENCY_OPEN)))
    if subject.kind is ShortlistSubject.BLOOD_REQUEST:
        q1 = q1.where(BloodRequest.id != subject.id)
    else:
        q2 = q2.where(Emergency.id != subject.id)
    return set(db.scalars(q1).all()) | set(db.scalars(q2).all())


def _accepted_this_subject(db: Session, subject: Subject, ids: list[uuid.UUID]) -> set[uuid.UUID]:
    if not ids:
        return set()
    if subject.kind is ShortlistSubject.BLOOD_REQUEST:
        q = select(DonorResponse.donor_user_id).where(
            DonorResponse.request_id == subject.id, DonorResponse.donor_user_id.in_(ids),
            DonorResponse.status == DonorResponseStatus.ACCEPTED)
    else:
        q = select(EmergencyResponse.responder_user_id).where(
            EmergencyResponse.emergency_id == subject.id,
            EmergencyResponse.responder_user_id.in_(ids),
            EmergencyResponse.status == DonorResponseStatus.ACCEPTED)
    return set(db.scalars(q).all())


def order_candidates(cands: list[Candidate], strategy: unified_priority.RankingStrategy,
                     urgency: Urgency, now: datetime) -> list[Candidate]:
    """Order ELIGIBLE candidates. The four lexicographic strategies run
    through the unchanged ``matching_engine.rank_candidates`` (same inputs as
    the ranking view: the profile's ``updated_at`` is its availability
    timestamp), so their semantics and tie-breaks are exactly theirs."""
    RS = unified_priority.RankingStrategy
    if strategy is RS.UNIFIED_PRIORITY:
        fallback = {c.ref: i for i, c in enumerate(
            order_candidates(cands, RS.TIME_RESILIENCE, urgency, now))}
        return sorted(cands, key=lambda c: (
            0 if c.priority.score_available else 1,
            -c.priority.score if c.priority.score_available else fallback[c.ref],
            c.distance.distance_km if c.distance.distance_km is not None else float("inf"),
            str(c.donor_user_id)))
    inputs = [matching_engine.MatchCandidateInput(
        ref=str(c.donor_user_id), name=None, blood_type=c.blood_type, role="DONOR",
        is_available=True, has_accepted=c.has_accepted, distance_km=c.distance.distance_km,
        last_donation_date=c.last_donation_date, identity_verified=c.identity_verified,
        is_eligible=True, alert_radius_km=None, availability_updated_at=c.profile_updated_at,
        city=None) for c in cands]
    ranked = matching_engine.rank_candidates(
        inputs, strategy=matching_engine.MatchStrategy(strategy.value), urgency=urgency,
        today=now.date(), now=now)
    by_id = {str(c.donor_user_id): c for c in cands}
    return [by_id[r.ref] for r in ranked]


def evaluate(db: Session, subject: Subject, *, now: datetime | None = None,
             strategy: unified_priority.RankingStrategy | None = None) -> Evaluation:
    now = now or utcnow()
    strategy = strategy or unified_priority.RankingStrategy.UNIFIED_PRIORITY
    cfg = unified_priority.config_from_settings()
    compatible = compatibility.compatible_source_types(subject.recipient_type, subject.component)
    rows = db.execute(
        select(DonorProfile, User)
        .join(User, User.id == DonorProfile.user_id)
        .where(DonorProfile.blood_type.in_(compatible), User.is_active.is_(True),
               User.id != subject.owner_user_id)
    ).all()
    excluded: dict[str, int] = {}

    def skip(reason: str) -> None:
        excluded[reason] = excluded.get(reason, 0) + 1

    ids = [u.id for _dp, u in rows]
    committed = _committed_elsewhere(db, ids, subject)
    history = _history(db, ids, subject)
    accepted_here = _accepted_this_subject(db, subject, ids)
    verified_donations = dict(db.execute(
        select(Donation.donor_user_id, func.count()).where(
            Donation.donor_user_id.in_(ids),
            Donation.verification_status == DonationVerificationStatus.VERIFIED)
        .group_by(Donation.donor_user_id)
    ).all()) if ids else {}
    limit = settings.max_donor_distance_km
    out: list[Candidate] = []
    for dp, u in rows:
        # A donor who already answered stays counted as eligible (they were);
        # contacted donors are never invited twice (see contact_next_batch).
        if not dp.is_available:
            skip("unavailable")
            continue
        if not is_eligible(dp.last_donation_date, today=now.date()):
            skip("donation_interval_not_elapsed")
            continue
        if u.id in committed:
            skip("committed_elsewhere")
            continue
        a = distance_policy.assess(
            origin_lat=subject.lat, origin_lng=subject.lng, origin_city=subject.city,
            donor_lat=dp.latitude, donor_lng=dp.longitude,
            donor_shares_location=dp.share_location, donor_city=dp.city,
            donor_alert_radius_km=dp.alert_radius_km, limit_km=limit,
            location_updated_at=dp.location_updated_at,
            stale_after_days=settings.donor_location_stale_days, now=now,
        )
        why = distance_policy.alert_skip_reason(
            a, override=subject.distance_override,
            allow_unscoped=settings.donor_alert_allow_unscoped)
        if why is not None:
            skip(why)
            continue
        travel = None
        if a.distance_km is not None:
            travel = int(round(a.distance_km / ASSUMED_TRAVEL_KMPH * 60))
            if subject.deadline is not None and now + timedelta(minutes=travel) > subject.deadline:
                skip("travel_exceeds_deadline")
                continue
        h = history[u.id]
        pin = unified_priority.PriorityInputs(
            has_accepted_this_subject=u.id in accepted_here,
            availability_confirmed_at=dp.availability_confirmed_at,
            invited=h.invited, answered=h.answered, accepted=h.accepted,
            last_response_at=h.last_response_at, last_donation_recorded_at=h.last_donation_at,
            distance_basis=a.basis.value, distance_km=a.distance_km, distance_limit_km=limit,
            location_age_days=a.location_age_days, identity_verified=bool(dp.identity_verified),
            verified_donations=int(verified_donations.get(u.id, 0)),
        )
        pri = unified_priority.compute(pin, cfg, now=now)
        reasons = [a.describe()]
        if travel is not None:
            reasons.append(f"about {travel} min by road at an assumed {ASSUMED_TRAVEL_KMPH:g} km/h")
        reasons += [f.detail for f in pri.factors if f.name != "distance"]
        out.append(Candidate(
            donor_user_id=u.id, ref=str(u.id)[:8], name=u.full_name, blood_type=dp.blood_type,
            synthetic=u.email.endswith("@" + SYNTHETIC_EMAIL_DOMAIN), distance=a,
            travel_minutes=travel, priority=pri, has_accepted=u.id in accepted_here,
            last_donation_date=dp.last_donation_date,
            identity_verified=bool(dp.identity_verified), profile_updated_at=dp.updated_at,
            reasons=reasons, inputs=pin,
        ))
    out = order_candidates(out, strategy, subject.urgency, now)
    for i, c in enumerate(out, 1):
        c.rank = i
    return Evaluation(subject=subject, candidates=out, excluded=excluded,
                      compatible_pool=len(rows), strategy=strategy, model_tag=cfg.model_tag())


# --------------------------------------------------------------------------- #
# Contact lifecycle
# --------------------------------------------------------------------------- #
def _timeout_minutes(subject: Subject) -> int:
    if subject.urgency == Urgency.CRITICAL:
        return settings.shortlist_response_timeout_minutes_critical
    return settings.shortlist_response_timeout_minutes


def _subject_event(db: Session, subject: Subject, actor: uuid.UUID | None, kind: str, note: str):
    """History row on the request/emergency -- this is also what drives the
    existing real-time (SSE) update to everyone watching it."""
    if subject.kind is ShortlistSubject.BLOOD_REQUEST:
        db.add(BloodRequestEvent(request_id=subject.id, actor_user_id=actor, event_type=kind,
                                 note=note[:1000]))
    else:
        db.add(EmergencyEvent(emergency_id=subject.id, actor_user_id=actor, event_type=kind,
                              note=note[:1000]))


def expire_timeouts(db: Session, subject: Subject, *, now: datetime | None = None) -> int:
    now = now or utcnow()
    rows = db.scalars(select(DonorContactAttempt).where(
        subject.contact_filter(),
        DonorContactAttempt.status == DonorContactStatus.AWAITING_RESPONSE,
        DonorContactAttempt.timeout_at <= now,
    ).with_for_update(skip_locked=True)).all()
    for c in rows:
        c.status = DonorContactStatus.TIMED_OUT
        if not (c.status_reason or "").startswith("simulated"):
            c.status_reason = REASON_TIMEOUT
    if rows:
        _subject_event(db, subject, None, "shortlist_timed_out",
                       f"{len(rows)} contacted donor(s) did not answer in time.")
    db.flush()
    return len(rows)


def counts(db: Session, subject: Subject) -> dict[str, int]:
    by_status = dict(db.execute(
        select(DonorContactAttempt.status, func.count())
        .where(subject.contact_filter()).group_by(DonorContactAttempt.status)
    ).all())
    n = {s.value: int(by_status.get(s, 0)) for s in DonorContactStatus}
    return n


def needed_donors(subject: Subject) -> int:
    return max(0, subject.units_needed - subject.units_fulfilled
               - subject.units_reserved_inventory)


def last_strategy(db: Session, subject: Subject) -> unified_priority.RankingStrategy:
    """The strategy of the subject's most recent contact batch (so automatic
    escalation keeps the coordinator's choice); UNIFIED_PRIORITY by default."""
    val = db.scalar(select(DonorContactAttempt.ranking_strategy).where(
        subject.contact_filter(), DonorContactAttempt.batch >= 1,
        DonorContactAttempt.ranking_strategy.is_not(None))
        .order_by(DonorContactAttempt.batch.desc()).limit(1))
    try:
        return unified_priority.RankingStrategy(val) if val else \
            unified_priority.RankingStrategy.UNIFIED_PRIORITY
    except ValueError:
        return unified_priority.RankingStrategy.UNIFIED_PRIORITY


def contact_next_batch(
    db: Session, subject: Subject, *, actor_user_id: uuid.UUID | None,
    evaluation: Evaluation | None = None, now: datetime | None = None,
    strategy: unified_priority.RankingStrategy | None = None,
) -> list[DonorContactAttempt]:
    """Contact the next best un-contacted candidates. Bounded: never more
    outstanding invitations than (still needed x SHORTLIST_CONTACT_MULTIPLIER),
    capped at SHORTLIST_MAX_BATCH per call. Never contacts the same donor
    twice for one subject (unique constraint + the subject row lock the
    caller holds). A donor who already received the open alert for this
    subject is tracked without a second notification."""
    from app.services import notification_service

    now = now or utcnow()
    if not subject.is_open:
        raise ShortlistError("This request is no longer open")
    expire_timeouts(db, subject, now=now)
    c = counts(db, subject)
    confirmed = c[DonorContactStatus.CONFIRMED_FOR_COORDINATION.value]
    accepted = c[DonorContactStatus.ACCEPTED.value]
    outstanding = c[DonorContactStatus.AWAITING_RESPONSE.value] + c[DonorContactStatus.QUEUED.value]
    still = max(0, needed_donors(subject) - confirmed - accepted)
    room = min(settings.shortlist_max_batch,
               still * settings.shortlist_contact_multiplier - outstanding)
    if room <= 0:
        return []
    strategy = strategy or last_strategy(db, subject)
    evaluation = evaluation or evaluate(db, subject, now=now, strategy=strategy)
    already = set(db.scalars(select(DonorContactAttempt.donor_user_id)
                             .where(subject.contact_filter())).all())
    picks = [cand for cand in evaluation.candidates if cand.donor_user_id not in already][:room]
    if not picks:
        return []
    batch = int(db.scalar(select(func.coalesce(func.max(DonorContactAttempt.batch), 0))
                          .where(subject.contact_filter())) or 0) + 1
    pick_ids = [p.donor_user_id for p in picks]
    data_key = "request_id" if subject.kind is ShortlistSubject.BLOOD_REQUEST else "emergency_id"
    prior_alert = dict(db.execute(
        select(Notification.user_id, Notification.id).where(
            Notification.user_id.in_(pick_ids), Notification.type == subject.broadcast_type,
            Notification.data[data_key].astext == str(subject.id))
    ).all())
    to_notify = [uid for uid in pick_ids if uid not in prior_alert]
    sent: dict[uuid.UUID, uuid.UUID] = {}
    if to_notify:
        stats: dict[str, int] = {}
        notification_service.notify_matching_donors(
            db, recipient_blood_type=subject.recipient_type,
            type=("blood_request.shortlist_invite" if subject.kind is ShortlistSubject.BLOOD_REQUEST
                  else "emergency.shortlist_invite"),
            title="You are one of a few donors asked to help",
            body=f"{subject.recipient_type.value} · {subject.urgency.value} -- please answer "
                 f"within {_timeout_minutes(subject)} minutes",
            data={data_key: str(subject.id), "shortlist": True},
            urgency=subject.urgency, origin_lat=subject.lat, origin_lng=subject.lng,
            origin_city=subject.city, component=subject.component,
            emergency=subject.kind is ShortlistSubject.EMERGENCY,
            distance_override=subject.distance_override, recipient_user_ids=to_notify,
            notification_ids_out=sent, stats=stats,
        )
    by_id = {p.donor_user_id: p for p in picks}
    created: list[DonorContactAttempt] = []
    timeout_at = now + timedelta(minutes=_timeout_minutes(subject))
    for uid in pick_ids:
        nid = prior_alert.get(uid) or sent.get(uid)
        if nid is None:
            continue  # suppressed by the donor's own preferences (opt-out, cap, urgency)
        p = by_id[uid]
        row = DonorContactAttempt(
            subject=subject.kind, **subject.fk(), donor_user_id=uid,
            status=DonorContactStatus.AWAITING_RESPONSE, batch=batch,
            rank_at_contact=p.rank, score_at_contact=p.score, notification_id=nid,
            ranking_strategy=evaluation.strategy.value, scoring_model=p.priority.model_tag,
            score_snapshot=p.priority.snapshot(),
            contacted_by_user_id=actor_user_id, contacted_at=now, timeout_at=timeout_at,
            status_reason=(REASON_PRIOR_ALERT if uid in prior_alert else None),
        )
        db.add(row)
        created.append(row)
    db.flush()
    suppressed = len(pick_ids) - len(created)
    _subject_event(db, subject, actor_user_id, "shortlist_contacted",
                   f"Batch {batch}: contacted {len(created)} ranked donor(s)"
                   + (f"; {suppressed} skipped by their own alert preferences"
                      if suppressed else "")
                   + f". Answers due within {_timeout_minutes(subject)} min.")
    return created


def started(db: Session, subject: Subject) -> bool:
    return bool(db.scalar(select(func.count()).select_from(DonorContactAttempt).where(
        subject.contact_filter(), DonorContactAttempt.batch >= 1)))


def maybe_escalate(db: Session, subject: Subject) -> list[DonorContactAttempt]:
    """After a decline / timeout: contact the next ranked donors, but only
    for a request whose shortlist was started, and only up to the bound."""
    if not (settings.shortlist_auto_escalate and subject.is_open and started(db, subject)):
        return []
    try:
        return contact_next_batch(db, subject, actor_user_id=None)
    except ShortlistError:
        return []


def sweep_timeouts(db: Session, *, now: datetime | None = None) -> int:
    """Deadline-monitor hook: expire overdue invitations everywhere and
    escalate the affected requests/emergencies."""
    now = now or utcnow()
    due = db.execute(select(DonorContactAttempt.subject, DonorContactAttempt.blood_request_id,
                            DonorContactAttempt.emergency_id).where(
        DonorContactAttempt.status == DonorContactStatus.AWAITING_RESPONSE,
        DonorContactAttempt.timeout_at <= now).distinct()).all()
    total = 0
    for kind, rid, eid in due:
        try:
            subject = load_subject(db, kind, rid or eid, lock=True)
        except ShortlistError:
            continue
        n = expire_timeouts(db, subject, now=now)
        total += n
        if n:
            maybe_escalate(db, subject)
    return total


def record_response(
    db: Session, kind: ShortlistSubject, subject_id: uuid.UUID, donor_user_id: uuid.UUID,
    action: DonorResponseStatus, *, was_accepted: bool, simulated: bool = False,
) -> DonorContactAttempt:
    """Called by the existing respond endpoints (after their own eligibility,
    availability and double-booking checks passed). Creates the contact row
    for a donor who answered the open alert without a personal invitation."""
    now = utcnow()
    flt = (DonorContactAttempt.blood_request_id == subject_id
           if kind is ShortlistSubject.BLOOD_REQUEST
           else DonorContactAttempt.emergency_id == subject_id)
    row = db.scalar(select(DonorContactAttempt).where(
        flt, DonorContactAttempt.donor_user_id == donor_user_id).with_for_update())
    if row is None:
        fk = ({"blood_request_id": subject_id} if kind is ShortlistSubject.BLOOD_REQUEST
              else {"emergency_id": subject_id})
        row = DonorContactAttempt(subject=kind, **fk, donor_user_id=donor_user_id,
                                  status=DonorContactStatus.QUEUED, batch=0,
                                  rank_at_contact=0, score_at_contact=0.0,
                                  status_reason=REASON_OPEN_ALERT)
        db.add(row)
    elif row.status is DonorContactStatus.ELIGIBILITY_RECHECK_REQUIRED:
        # Privacy round 2: a failed re-check reason describes the donor and
        # belongs to that status only. Leaving it (withdrawal, a new answer)
        # clears it, so it is never shown under another status; the reason
        # is kept in the staff-only "shortlist.confirmation" audit entry.
        row.status_reason = None
    if action == DonorResponseStatus.ACCEPTED:
        row.status = DonorContactStatus.ACCEPTED
        row.eligibility_rechecked_at = now  # the respond endpoint just re-checked it
    elif was_accepted:
        row.status = DonorContactStatus.WITHDRAWN
    elif action == DonorResponseStatus.NOT_NOW:
        row.status = DonorContactStatus.UNAVAILABLE
    else:
        row.status = DonorContactStatus.DECLINED
    row.responded_at = now
    row.simulated = simulated
    db.flush()
    return row


def confirm(
    db: Session, subject: Subject, contact: DonorContactAttempt, *, actor_user_id: uuid.UUID,
) -> DonorContactAttempt:
    """Coordinator confirms an ACCEPTED donor for coordination after
    re-checking eligibility, availability and other commitments NOW. Fails
    closed to ELIGIBILITY_RECHECK_REQUIRED."""
    from app.services import donor_commitment

    if contact.status != DonorContactStatus.ACCEPTED:
        raise ShortlistError("Only a donor who accepted can be confirmed")
    dp = db.scalar(select(DonorProfile).where(DonorProfile.user_id == contact.donor_user_id))
    problems = []
    if dp is None or not dp.is_available:
        problems.append("no longer marked available")
    elif not is_eligible(dp.last_donation_date):
        problems.append("donation interval not elapsed")
    kw = ({"exclude_request_id": subject.id} if subject.kind is ShortlistSubject.BLOOD_REQUEST
          else {"exclude_emergency_id": subject.id})
    conflict = donor_commitment.find_conflicting_commitment(db, contact.donor_user_id, **kw)
    if conflict:
        problems.append(conflict)
    now = utcnow()
    contact.eligibility_rechecked_at = now
    if problems:
        contact.status = DonorContactStatus.ELIGIBILITY_RECHECK_REQUIRED
        contact.status_reason = "; ".join(problems)[:300]
    else:
        contact.status = DonorContactStatus.CONFIRMED_FOR_COORDINATION
        contact.confirmed_at = now
        contact.confirmed_by_user_id = actor_user_id
    db.flush()
    # Privacy round 2 (2026-11-03): the request/emergency event history is
    # read by patients, family members and -- for emergencies -- other
    # donors, and it drives the live updates. It therefore says only THAT a
    # confirmation did not go through, never which donor or why. The reason
    # stays on the contact row (shown to facility/admin users only) and in
    # the staff-only audit entry written by the confirm route. Neither note
    # carries a donor reference: the audit entry has the contact id.
    if problems:
        note = ("A contacted donor could not be confirmed for coordination; "
                "a coordinator must follow up (details are visible to facility staff).")
    else:
        note = "A contacted donor was confirmed for coordination."
    _subject_event(db, subject, actor_user_id, "shortlist_confirmation", note)
    return contact


def policy_snapshot() -> dict:
    pol = active_policy()
    cfg = unified_priority.config_from_settings()
    return {
        "name": POLICY_NAME,
        "model_version": unified_priority.MODEL_VERSION,
        "model_fingerprint": cfg.fingerprint(),
        "strategies": [s.value for s in unified_priority.RankingStrategy],
        "default_strategy": unified_priority.RankingStrategy.UNIFIED_PRIORITY.value,
        "weights": cfg.weights,
        "min_coverage": cfg.min_coverage,
        "weights_are_provisional": True,
        "score_is_probability": False,
        "min_response_samples": settings.shortlist_min_response_samples,
        "contact_multiplier": settings.shortlist_contact_multiplier,
        "max_batch": settings.shortlist_max_batch,
        "response_timeout_minutes": settings.shortlist_response_timeout_minutes,
        "response_timeout_minutes_critical": settings.shortlist_response_timeout_minutes_critical,
        "max_distance_km": settings.max_donor_distance_km,
        "assumed_travel_kmph": ASSUMED_TRAVEL_KMPH,
        "eligibility_policy": {"code": pol.code, "interval_days": pol.interval_days,
                               "citation": pol.citation, "note": pol.note,
                               "clinical_review_required": pol.clinical_review_required},
        "disclaimer": ("Ranking is not confirmation, and the priority score is a ranking measure, "
                       "not a probability of donation. An accepted or confirmed donor still "
                       "needs the blood centre's screening; no candidate is guaranteed to "
                       "donate."),
    }
