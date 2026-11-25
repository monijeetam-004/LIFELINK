"""Persistent in-app notifications."""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import compatibility, distance_policy
from app.core.eligibility import is_eligible
from app.models.enums import (
    BloodComponent,
    BloodType,
    NotificationDigestFrequency,
    Urgency,
    urgency_at_least,
)
from app.models.notification import Notification
from app.models.notification_preference import NotificationPreference
from app.models.profile import DonorProfile
from app.models.user import User

_EMERGENCY_TYPE_PREFIXES = ("emergency.",)


def _parse_hhmm(value: str | None) -> time | None:
    if not value:
        return None
    try:
        hh, mm = value.split(":")
        return time(int(hh), int(mm))
    except (ValueError, TypeError):  # malformed value shouldn't ever reach here
        return None  # pragma: no cover - schema already validates "HH:MM"


def _in_quiet_hours(pref: NotificationPreference, *, now: datetime | None = None) -> bool:
    """True when `now` (server local time) falls inside the user's configured
    quiet-hours window. Supports a window that wraps past midnight
    (e.g. 22:00-07:00)."""
    start = _parse_hhmm(pref.quiet_hours_start)
    end = _parse_hhmm(pref.quiet_hours_end)
    if start is None or end is None:
        return False
    current = (now or datetime.now()).time()
    if start <= end:
        return start <= current < end
    return current >= start or current < end


def notify(
    db: Session,
    user_id: uuid.UUID,
    *,
    type: str,
    title: str,
    body: str | None = None,
    data: dict[str, Any] | None = None,
    sms_variables: dict[str, str] | None = None,
) -> Notification:
    """Create the one in-app Notification every event guarantees, then let
    each *optional* channel below decide independently whether it also
    applies. Business/domain code (blood_requests.py, emergencies.py, ...)
    only ever calls this one function -- it is never coupled to a specific
    channel, and it stays fully functional (in-app always fires) regardless
    of which, if any, of the channels below are configured:

        notify()
          |
          +---- in-app Notification row  -- always created
          +---- _maybe_send_email()      -- if email configured/enabled
          +---- _maybe_send_sms()        -- if SMS configured/enabled (dormant by default)

    ``sms_variables`` is an optional extension point for callers that want
    to supply the structured values a mapped SMS template needs (see
    app/services/sms/templates.py); most call sites don't pass it, which is
    fine -- SMS is disabled by default in this deployment (see
    LIFELINK_SMS_PROVIDER.md), and even when enabled, a template missing
    its required variables just fails that one send honestly (recorded on
    its SmsNotification audit row) rather than guessing or sending broken
    content.
    """
    n = Notification(
        user_id=user_id, type=type, title=title, body=body, data=data or {}
    )
    db.add(n)
    db.flush()
    pref = db.scalar(
        select(NotificationPreference).where(NotificationPreference.user_id == user_id)
    )
    _maybe_send_email(db, user_id, pref, type=type, title=title, body=body)
    _maybe_send_sms(
        db, user_id, pref, type=type, notification_id=n.id, sms_variables=sms_variables
    )
    return n


def _maybe_send_email(
    db: Session,
    user_id: uuid.UUID,
    pref: NotificationPreference | None,
    *,
    type: str,
    title: str,
    body: str | None,
    user: User | None = None,
) -> None:
    """Echo this notification by email when the recipient's preferences call for it.

    Real-time send only (see email_service.send_notification_email docstring for
    what's not implemented yet). Never raises -- a preference lookup or email
    failure must not roll back the notification transaction.

    `user`: Phase 27 perf fix (see notify_matching_donors' docstring) -- when
    the caller has already loaded the recipient in bulk, pass it here to skip
    this function's own `db.get(User, ...)` round trip. Single-recipient
    callers (the plain `notify()` path) leave this None and it's fetched here
    exactly as before -- behavior for every existing call site is unchanged.
    """
    try:
        # Defaults (email_enabled=True, digest=REALTIME) apply when no row exists yet.
        email_enabled = pref.email_enabled if pref else True
        is_realtime = (
            pref.digest_frequency == NotificationDigestFrequency.REALTIME if pref else True
        )
        is_emergency = type.startswith(_EMERGENCY_TYPE_PREFIXES)
        emergency_enabled = pref.emergency_alerts_enabled if pref else True
        should_send = email_enabled and (is_realtime or (is_emergency and emergency_enabled))
        if not should_send:
            return
        # Quiet hours hold back non-emergency email only -- emergency alerts
        # are never suppressed (documented on NotificationPreference itself).
        if pref is not None and not is_emergency and _in_quiet_hours(pref):
            return
        if user is None:
            user = db.get(User, user_id)
        if user is None:
            return
        from app.services import email_service

        email_service.send_notification_email(user.email, user.full_name, title, body)
    except Exception:  # pragma: no cover - notification email is best-effort
        return


def _maybe_send_sms(
    db: Session,
    user_id: uuid.UUID,
    pref: NotificationPreference | None,
    *,
    type: str,
    notification_id: uuid.UUID,
    sms_variables: dict[str, str] | None,
    user: User | None = None,
) -> None:
    """SMS is OFF by default in every LifeLink deployment (MSG91_ENABLED is
    unset/false -- see app/core/config.py). This function's very first line
    checks that and returns immediately: no DB query, no import of the SMS
    package, no network call, nothing recorded. SMS being disabled can
    never slow down, fail, or otherwise affect creating a notification --
    see LIFELINK_SMS_STATUS.md.

    When it IS enabled (a deployment that has genuinely configured MSG91),
    this applies the same shape of preference gating email already does:
    per-user opt-in (NotificationPreference.sms_enabled, default False) and
    quiet hours for non-emergency events; emergency alerts are never held
    back by quiet hours, matching email's rule. Never raises -- exactly
    like _maybe_send_email, an SMS failure must never roll back the
    notification transaction or be visible to the caller as an error.

    `user`: same Phase 27 perf-fix bulk-prefetch parameter as
    `_maybe_send_email` above -- optional, backward compatible.
    """
    from app.core.config import settings

    # SMS attempts happen only when a provider was deliberately chosen:
    # MSG91 switched on (even if misconfigured -- that is then recorded as
    # PROVIDER_NOT_CONFIGURED), or the development console provider.
    if not (settings.msg91_enabled or settings.sms_provider.strip().lower() == "console"):
        return
    try:
        sms_enabled = pref.sms_enabled if pref else False
        if not sms_enabled:
            return
        is_emergency = type.startswith(_EMERGENCY_TYPE_PREFIXES)
        if pref is not None and not is_emergency and _in_quiet_hours(pref):
            return
        if user is None:
            user = db.get(User, user_id)
        if user is None:
            return
        from app.services import sms_service

        sms_service.send_for_event(
            db, user, event_type=type, notification_id=notification_id,
            sms_variables=sms_variables,
        )
    except Exception:  # pragma: no cover - SMS is best-effort, like email
        logging.getLogger("lifelink.sms").exception("SMS dispatch raised unexpectedly")
        return


def notify_matching_donors(
    db: Session,
    *,
    recipient_blood_type: BloodType,
    type: str,
    title: str,
    body: str | None = None,
    data: dict[str, Any] | None = None,
    city: str | None = None,
    exclude_user_id: uuid.UUID | None = None,
    urgency: Urgency | None = None,
    origin_lat: float | None = None,
    origin_lng: float | None = None,
    component: BloodComponent = BloodComponent.WHOLE_BLOOD,
    max_distance_km: float | None = None,
    emergency: bool = False,
    stats: dict[str, int] | None = None,
    origin_city: str | None = None,
    distance_override: bool = False,
    recipient_user_ids: list[uuid.UUID] | None = None,
    notification_ids_out: dict[uuid.UUID, uuid.UUID] | None = None,
) -> int:
    """Notify available donors whose blood type is compatible with the recipient.

    Two real Alert Watcher preferences are applied on top of the automatic
    blood-type-compatibility filter, both read straight off the donor's own
    DonorProfile:
      - `alert_min_urgency`: skip donors who only want to hear about requests
        at or above a chosen urgency (defaults to ROUTINE -- everything).
      - `alert_radius_km`: skip donors outside their chosen radius, but only
        when there's a real distance to compute (both sides have coordinates
        and the donor has opted into `share_location`); a donor who hasn't
        shared a location can't have a real radius applied to them, so they're
        notified regardless rather than silently dropped.

    Phase 27 perf fix: this used to call the single-recipient `notify()`
    helper once per matching donor -- for a real 129-donor O_NEG match, that
    was 129 * (INSERT+flush, SELECT preference, SELECT user, +Redis enqueue)
    = 500+ sequential round trips, all held open on the SAME connection/
    transaction for the entire loop (confirmed via pg_stat_activity +
    py-spy: threads were blocked on the connection-pool queue, not CPU-bound,
    overturning Phase 24/25's "CPU/thread-scheduling capacity" theory). Under
    concurrent load this starved the whole 15-slot pool and took down
    unrelated endpoints (reproduced with `scripts/phase27_loadtest.py mixed
    25 10`). Fixed by batching every DB round trip that doesn't need to be
    per-donor:
      1. one query for all matching DonorProfiles (unchanged),
      2. one INSERT for all Notification rows (db.add_all + single flush),
      3. one SELECT for all NotificationPreference rows (IN-list),
      4. one SELECT for all User rows (IN-list),
    then a pure-Python loop (zero further DB calls) that calls
    _maybe_send_email/_maybe_send_sms per donor with everything pre-fetched.
    Eligibility/quiet-hours/preference logic itself is untouched -- only the
    round-trip count changed, not the rules.

    Gap elimination (emergency alerts):
      - compatibility comes from the central service for the requested
        `component` (plasma follows plasma rules, never red-cell rules);
      - `urgency` IS now passed for emergencies too, so a donor's
        `alert_min_urgency` (critical-only / urgent+critical / everything)
        is respected for SOS alerts as well;
      - `emergency=True` also honours `emergency_alerts_enabled=False`
        (opt-out) and caps alerts at DONOR_EMERGENCY_ALERTS_PER_HOUR per
        donor per rolling hour;
      - `max_distance_km` (MAX_DONOR_DISTANCE_KM) skips donors whose KNOWN
        distance exceeds it; a donor with no shared location is still
        alerted (their distance is unknown, not large).
    `stats`, when given, is filled with how many donors each rule skipped.

    Audit finding D4 (2026-10-31): the distance rule now comes from ONE place,
    `app.core.distance_policy`, for blood-request alerts and SOS alerts
    alike. `max_distance_km=None` means "use MAX_DONOR_DISTANCE_KM" -- there
    is no longer a caller that alerts donors without the limit. A donor with
    no usable location is alerted only when they are recorded in the same
    city as the request; a request with neither coordinates nor a city
    alerts nobody unless DONOR_ALERT_ALLOW_UNSCOPED is explicitly enabled.
    `distance_override` is an audited ADMIN decision (emergency
    DONOR_DISTANCE override) and is the only way past the limit.
    `recipient_user_ids`, when given, restricts the alert to those donors
    (the confidence shortlist contacts candidates in bounded batches); every
    other rule still applies to them.
    """
    from app.core.config import settings

    counts = stats if stats is not None else {}
    for key in ("ineligible", "below_urgency_preference", "outside_alert_radius",
                "beyond_distance_limit", "location_unknown", "no_request_location",
                "emergency_alerts_disabled", "frequency_capped"):
        counts.setdefault(key, 0)
    compatible = compatibility.compatible_source_types(recipient_blood_type, component)
    stmt = (
        select(DonorProfile)
        .join(User, User.id == DonorProfile.user_id)
        .where(
            DonorProfile.is_available.is_(True),
            DonorProfile.blood_type.in_(compatible),
            User.is_active.is_(True),
        )
    )
    if city:
        stmt = stmt.where(DonorProfile.city == city)
    if exclude_user_id:
        stmt = stmt.where(DonorProfile.user_id != exclude_user_id)
    if recipient_user_ids is not None:
        if not recipient_user_ids:
            return 0
        stmt = stmt.where(DonorProfile.user_id.in_(recipient_user_ids))
    limit_km = settings.max_donor_distance_km if max_distance_km is None else max_distance_km

    candidates = list(db.scalars(stmt).all())
    pref_by_candidate: dict[uuid.UUID, NotificationPreference] = {}
    recent_by_user: dict[uuid.UUID, int] = {}
    if emergency and candidates:
        ids = [dp.user_id for dp in candidates]
        pref_by_candidate = {p.user_id: p for p in db.scalars(
            select(NotificationPreference).where(NotificationPreference.user_id.in_(ids))
        ).all()}
        since = datetime.now(UTC) - timedelta(hours=1)
        recent_by_user = dict(db.execute(
            select(Notification.user_id, func.count())
            .where(Notification.user_id.in_(ids), Notification.type == type,
                   Notification.created_at >= since)
            .group_by(Notification.user_id)
        ).all())

    matched: list[DonorProfile] = []
    for dp in candidates:
        # Phase 31 domain-correctness fix: a donor who donated recently and
        # is not yet eligible again (app.core.eligibility.is_eligible, the
        # 56-day interval) was previously still alerted here even though
        # they cannot legally accept -- respond_to_request's own accept
        # gate already blocks them at that point (blood_requests.py), so
        # the only effect of leaving this unfiltered was wasted donor
        # attention and a false sense of urgency on an alert the recipient
        # can't act on. This does not invent a new medical rule -- it is
        # the same interval already enforced at accept-time, applied
        # consistently at notify-time too.
        if not is_eligible(dp.last_donation_date):
            counts["ineligible"] += 1
            continue
        if urgency is not None and not urgency_at_least(urgency, dp.alert_min_urgency):
            counts["below_urgency_preference"] += 1
            continue
        assessment = distance_policy.assess(
            origin_lat=origin_lat, origin_lng=origin_lng,
            origin_city=origin_city if origin_city is not None else city,
            donor_lat=dp.latitude, donor_lng=dp.longitude,
            donor_shares_location=dp.share_location, donor_city=dp.city,
            donor_alert_radius_km=dp.alert_radius_km, limit_km=limit_km,
            location_updated_at=dp.location_updated_at,
            stale_after_days=settings.donor_location_stale_days,
        )
        skip = distance_policy.alert_skip_reason(
            assessment, override=distance_override,
            allow_unscoped=settings.donor_alert_allow_unscoped,
        )
        if skip is not None:
            counts[skip] += 1
            continue
        if emergency:
            pref = pref_by_candidate.get(dp.user_id)
            if pref is not None and not pref.emergency_alerts_enabled:
                counts["emergency_alerts_disabled"] += 1
                continue
            if recent_by_user.get(dp.user_id, 0) >= settings.donor_emergency_alerts_per_hour:
                counts["frequency_capped"] += 1
                continue
        matched.append(dp)

    if not matched:
        return 0

    donor_user_ids = [dp.user_id for dp in matched]

    # 1 INSERT for every in-app Notification row instead of one per donor.
    notifications = [
        Notification(user_id=uid, type=type, title=title, body=body, data=data or {})
        for uid in donor_user_ids
    ]
    db.add_all(notifications)
    db.flush()
    notification_by_user = {n.user_id: n for n in notifications}
    if notification_ids_out is not None:
        notification_ids_out.update({uid: n.id for uid, n in notification_by_user.items()})

    # 1 SELECT for every donor's NotificationPreference instead of one per donor.
    prefs = db.scalars(
        select(NotificationPreference).where(
            NotificationPreference.user_id.in_(donor_user_ids)
        )
    ).all()
    pref_by_user = {p.user_id: p for p in prefs}

    # 1 SELECT for every donor's User row instead of relying on
    # _maybe_send_email/_maybe_send_sms's internal per-donor db.get(User, ...).
    users = db.scalars(select(User).where(User.id.in_(donor_user_ids))).all()
    user_by_id = {u.id: u for u in users}

    # Everything below is pure Python -- no further DB round trips.
    for uid in donor_user_ids:
        pref = pref_by_user.get(uid)
        user = user_by_id.get(uid)
        _maybe_send_email(db, uid, pref, type=type, title=title, body=body, user=user)
        _maybe_send_sms(
            db, uid, pref, type=type,
            notification_id=notification_by_user[uid].id,
            sms_variables=None, user=user,
        )
    return len(matched)
