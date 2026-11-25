"""Background deadline monitor: ``python -m app.workers.deadline_monitor``.

Until now deadline handling was lazy (only noticed when someone read the
emergency). This process checks on a schedule, so a deadline is noticed even
if nobody opens the page:

1. WARNING   -- an open emergency whose deadline is within
                DEADLINE_WARNING_MINUTES (and not yet passed) gets one
                ``deadline_warning`` event + notifications to its creator and
                accepted responders.
2. EXCEEDED  -- an open emergency past its deadline is flagged through the
                same ``emergency_lifecycle.flag_deadline_exceeded_if_needed``
                every read path already uses (one source of truth).
3. ESCALATED -- once exceeded, administrators are notified once
                (``deadline_escalated``). The status is never changed: the
                system is decision support, it does not close emergencies.
4. EXPIRY    -- facilities holding units past their effective expiry (shelf
                life or post-thaw window) are swept, so stock figures stay
                honest without waiting for someone to open the inventory.

Idempotent: each step is guarded by a timestamp column set in the same
transaction as its side effects. Safe to run several copies: rows are taken
with ``FOR UPDATE SKIP LOCKED``, so two monitors never handle the same row.
Configurable: DEADLINE_WARNING_MINUTES, DEADLINE_MONITOR_INTERVAL_SECONDS.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.emergency_lifecycle import OPEN_STATUSES
from app.core.time import utcnow
from app.db.session import SessionLocal
from app.models.blood_unit import BloodUnit
from app.models.emergency import Emergency, EmergencyEvent, EmergencyResponse
from app.models.enums import BloodUnitState, DonorResponseStatus
from app.models.user import User, UserRole
from app.services import audit_service, emergency_lifecycle, inventory_service, notification_service

logger = logging.getLogger("lifelink.deadline_monitor")


@dataclass
class MonitorRun:
    warned: list[str] = field(default_factory=list)
    exceeded: list[str] = field(default_factory=list)
    escalated: list[str] = field(default_factory=list)
    swept_facilities: int = 0
    shortlist_timeouts: int = 0
    audit_checkpoint: str | None = None


def _responders(db: Session, emergency_id) -> list:
    return [
        r
        for (r,) in db.execute(
            select(EmergencyResponse.responder_user_id).where(
                EmergencyResponse.emergency_id == emergency_id,
                EmergencyResponse.status == DonorResponseStatus.ACCEPTED,
            )
        ).all()
    ]


def run_once(db: Session, *, now: datetime | None = None) -> MonitorRun:
    now = now or utcnow()
    run = MonitorRun()
    warn_window = timedelta(minutes=settings.deadline_warning_minutes)

    # 1. warnings
    for em in db.scalars(
        select(Emergency)
        .where(
            Emergency.status.in_(OPEN_STATUSES),
            Emergency.deadline.is_not(None),
            Emergency.deadline > now,
            Emergency.deadline <= now + warn_window,
            Emergency.deadline_warning_sent_at.is_(None),
        )
        .with_for_update(skip_locked=True)
    ).all():
        em.deadline_warning_sent_at = now
        minutes = round((em.deadline - now).total_seconds() / 60)
        db.add(
            EmergencyEvent(
                emergency_id=em.id,
                actor_user_id=None,
                event_type="deadline_warning",
                note=f"Deadline in about {minutes} minute(s).",
            )
        )
        for uid in {em.created_by_user_id, *_responders(db, em.id)}:
            notification_service.notify(
                db,
                uid,
                type="emergency.deadline_warning",
                title="Emergency deadline approaching",
                body=f"About {minutes} minute(s) remain before this emergency's deadline.",
                data={"emergency_id": str(em.id)},
            )
        audit_service.record(
            db,
            "emergency.deadline_warning",
            actor_user_id=None,
            target_type="emergency",
            target_id=em.id,
            meta={"minutes_remaining": minutes},
        )
        run.warned.append(str(em.id))
    db.commit()

    # 2. exceeded (same function the read paths use)
    for em in db.scalars(
        select(Emergency)
        .where(
            Emergency.status.in_(OPEN_STATUSES),
            Emergency.deadline.is_not(None),
            Emergency.deadline < now,
            Emergency.deadline_exceeded_at.is_(None),
        )
        .with_for_update(skip_locked=True)
    ).all():
        emergency_lifecycle.flag_deadline_exceeded_if_needed(db, em)
        run.exceeded.append(str(em.id))
    db.commit()

    # 3. escalation to administrators, once
    admins = [
        u
        for (u,) in db.execute(
            select(User.id).where(User.role == UserRole.ADMIN, User.is_active.is_(True))
        ).all()
    ]
    for em in db.scalars(
        select(Emergency)
        .where(
            Emergency.status.in_(OPEN_STATUSES),
            Emergency.deadline_exceeded_at.is_not(None),
            Emergency.deadline_escalated_at.is_(None),
        )
        .with_for_update(skip_locked=True)
    ).all():
        em.deadline_escalated_at = now
        db.add(
            EmergencyEvent(
                emergency_id=em.id,
                actor_user_id=None,
                event_type="deadline_escalated",
                note=f"Escalated to {len(admins)} administrator(s).",
            )
        )
        for uid in admins:
            notification_service.notify(
                db,
                uid,
                type="emergency.deadline_escalated",
                title="Emergency past its deadline",
                body="An open emergency has passed its deadline and needs attention.",
                data={"emergency_id": str(em.id)},
            )
        audit_service.record(
            db,
            "emergency.deadline_escalated",
            actor_user_id=None,
            target_type="emergency",
            target_id=em.id,
            meta={"admins_notified": len(admins)},
        )
        run.escalated.append(str(em.id))
    db.commit()

    # 4. effective-expiry sweep
    stale = db.scalars(
        select(BloodUnit.hospital_id)
        .where(
            BloodUnit.state.in_([BloodUnitState.AVAILABLE, BloodUnitState.RESERVED]),
            or_(
                and_(BloodUnit.expires_at.is_not(None), BloodUnit.expires_at <= now),
                and_(BloodUnit.thaw_expires_at.is_not(None), BloodUnit.thaw_expires_at <= now),
            ),
        )
        .distinct()
    ).all()
    for hid in stale:
        inventory_service.sweep_all_expired(db, hid)
        run.swept_facilities += 1
    db.commit()

    # 5. confidence shortlist: expire unanswered invitations and escalate to
    #    the next ranked donors (2026-10-31).
    from app.services import donor_shortlist_service

    run.shortlist_timeouts = donor_shortlist_service.sweep_timeouts(db, now=now)
    db.commit()

    # 6. signed audit checkpoint, when configured and due (audit E4).
    run.audit_checkpoint = _maybe_checkpoint(db, now)
    return run


def _maybe_checkpoint(db: Session, now: datetime) -> str | None:
    interval = settings.audit_checkpoint_interval_minutes
    if not settings.audit_checkpoints_enabled or interval <= 0:
        return None
    from app.services import audit_checkpoint_service

    try:
        last = audit_checkpoint_service.last_checkpoint_time()
        if last is not None and now - last < timedelta(minutes=interval):
            return None
        cp = audit_checkpoint_service.write_checkpoint(db)
        return None if cp is None else f"checkpoint at seq {cp.seq}"
    except Exception:  # pragma: no cover - logged; the monitor keeps running
        logger.exception("audit checkpoint failed")
        return "failed"


_stop = False


#: Touched after every successful pass; docker-compose's health check for the
#: deadline-monitor service reads its age (final external validation: the
#: image's HTTP health check made this non-HTTP service report "unhealthy").
HEARTBEAT_ENV = "DEADLINE_MONITOR_HEARTBEAT"
DEFAULT_HEARTBEAT = "/tmp/lifelink-deadline-monitor.heartbeat"  # noqa: S108 - container-local


def _beat() -> None:
    from pathlib import Path

    try:
        Path(os.environ.get(HEARTBEAT_ENV, DEFAULT_HEARTBEAT)).touch()
    except OSError:  # a heartbeat must never break the monitor itself
        logger.warning("could not write the deadline-monitor heartbeat")


def _handle_stop(*_args) -> None:
    global _stop
    _stop = True


def main(argv: list[str] | None = None) -> None:
    from app.core.logging_config import configure_logging

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--once", action="store_true", help="run one pass and exit")
    args = parser.parse_args(argv)
    configure_logging()
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)
    interval = settings.deadline_monitor_interval_seconds
    logger.info(
        "deadline monitor started (interval=%ss, warning=%smin)",
        interval,
        settings.deadline_warning_minutes,
    )
    while not _stop:
        db = SessionLocal()
        try:
            run = run_once(db)
            logger.info(
                "pass: warned=%d exceeded=%d escalated=%d swept=%d",
                len(run.warned),
                len(run.exceeded),
                len(run.escalated),
                run.swept_facilities,
            )
            _beat()
        except Exception:  # keep the loop alive; the next pass retries
            logger.exception("deadline monitor pass failed")
            db.rollback()
        finally:
            db.close()
        if args.once:
            break
        for _ in range(interval):
            if _stop:
                break
            time.sleep(1)


if __name__ == "__main__":
    main()
