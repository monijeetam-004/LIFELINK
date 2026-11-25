"""DEMO_MODE response simulator for SYNTHETIC donors only (2026-10-31).

Synthetic donors are created by scripts/seed_shortlist_demo.py with e-mail
addresses ``sim-<persona>-<n>@synthetic.lifelink.example`` (the ``.example``
top-level domain is reserved by RFC 2606 and never resolves, so no mail can
be delivered; their phone numbers are left empty, so no SMS can be sent).
The persona decides how the simulator answers an invitation:

    accept   -> ACCEPTED (through the real respond code path)
    decline  -> DECLINED
    notnow   -> NOT_NOW ("unavailable")
    silent   -> no answer; with force_timeouts the invitation is expired now
    slow     -> no answer on this run (answers ACCEPT on a later run)

A real donor is never touched (they are counted as skipped). Every answer
recorded here is labelled ``simulated`` on the contact row and in the audit
log. Simulated acceptance demonstrates the workflow; it says nothing about
real-world donor availability.
"""
from __future__ import annotations

import re

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.time import utcnow
from app.models.donor_contact import DonorContactAttempt
from app.models.enums import DonorContactStatus, DonorResponseStatus, ShortlistSubject
from app.models.user import User
from app.schemas.blood_request import RespondIn
from app.services import donor_shortlist_service as svc

_PERSONA = re.compile(r"^sim-(accept|decline|notnow|silent|slow)-", re.I)


def persona_of(email: str) -> str | None:
    if not email.endswith("@" + svc.SYNTHETIC_EMAIL_DOMAIN):
        return None
    m = _PERSONA.match(email)
    return m.group(1).lower() if m else "accept"


def simulate(db: Session, subject: svc.Subject, *, force_timeouts: bool, actor: User) -> dict:
    if not settings.demo_mode or settings.is_production:  # defence in depth
        raise RuntimeError("demo simulator disabled")
    from app.api.routes.blood_requests import respond_to_request
    from app.api.routes.emergencies import respond_to_emergency

    out = {"accepted": 0, "declined": 0, "not_now": 0, "timed_out": 0, "no_answer_yet": 0,
           "refused_by_checks": 0, "skipped_non_synthetic": 0}
    awaiting = list(db.scalars(select(DonorContactAttempt).where(
        subject.contact_filter(),
        DonorContactAttempt.status == DonorContactStatus.AWAITING_RESPONSE,
    ).order_by(DonorContactAttempt.batch, DonorContactAttempt.rank_at_contact)).all())
    for c in awaiting:
        donor = db.get(User, c.donor_user_id)
        persona = persona_of(donor.email) if donor else None
        if persona is None:
            out["skipped_non_synthetic"] += 1
            continue
        prior_runs = (c.status_reason or "").count("[sim-wait]")
        if persona == "slow" and prior_runs == 0:
            c.status_reason = ((c.status_reason or "") + " [sim-wait]").strip()
            out["no_answer_yet"] += 1
            continue
        if persona == "silent":
            if force_timeouts:
                c.timeout_at = utcnow()
                c.status_reason = svc.REASON_SIM_TIMEOUT
            else:
                out["no_answer_yet"] += 1
            continue
        action = {"accept": DonorResponseStatus.ACCEPTED, "slow": DonorResponseStatus.ACCEPTED,
                  "decline": DonorResponseStatus.DECLINED,
                  "notnow": DonorResponseStatus.NOT_NOW}[persona]
        payload = RespondIn(action=action, note="simulated response (demo mode)")
        try:
            if subject.kind is ShortlistSubject.BLOOD_REQUEST:
                respond_to_request(subject.id, payload, None, user=donor, db=db)
            else:
                respond_to_emergency(subject.id, payload, None, user=donor, db=db)
        except HTTPException as exc:  # the real eligibility / commitment checks said no
            db.rollback()
            out["refused_by_checks"] += 1
            c = db.get(DonorContactAttempt, c.id)
            c.status_reason = f"simulated accept refused by real checks: {exc.detail}"[:300]
            db.commit()
            continue
        row = db.get(DonorContactAttempt, c.id)
        row.simulated = True
        db.commit()
        out[{"accept": "accepted", "slow": "accepted", "decline": "declined",
             "notnow": "not_now"}[persona]] += 1
    db.flush()
    fresh = svc.load_subject(db, subject.kind, subject.id, lock=True)
    out["timed_out"] = svc.expire_timeouts(db, fresh)
    if out["timed_out"]:
        for c in db.scalars(select(DonorContactAttempt).where(
                fresh.contact_filter(), DonorContactAttempt.status == DonorContactStatus.TIMED_OUT,
                DonorContactAttempt.status_reason.like("simulated:%"))).all():
            c.simulated = True
        svc.maybe_escalate(db, fresh)
    return out
