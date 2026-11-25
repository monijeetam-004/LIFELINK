"""SMS notification channel -- orchestration layer.

This is the SMS half of the same job app/services/email_service.py does for
email: given an event that notification_service.notify() already decided is
worth telling someone about, decide whether it should ALSO go out as an SMS,
and if so, attempt it and record what happened. It is deliberately never
called except from notification_service._maybe_send_sms(), which is itself a
no-op unless SMS is fully configured and enabled -- see that function's
docstring for the exact gate. Nothing here ever executes in a deployment
that hasn't set MSG91_ENABLED=true with real, working credentials.

Design mirrors two existing "optional external dependency" patterns in this
codebase on purpose, rather than inventing a third shape:
  * app/core/graph.py's GraphUnavailableError / graph_health() -- "not
    configured" is a normal state, never faked as success.
  * app/core/storage.py's storage_backend() -- one function decides which
    backend (if any) is active; callers don't each re-implement that check.

Every call is idempotent per Notification: a second attempt for the same
notification_id updates the existing SmsNotification row (via the
``uq_sms_notification_per_notification`` constraint) rather than creating a
duplicate send record. Retryable failures are re-attempted automatically
with exponential backoff via the RQ scheduler (_schedule_retry_if_retryable).
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.phone import mask_phone, normalize_phone_e164
from app.core.time import utcnow
from app.models.enums import SmsFailureCategory, SmsStatus
from app.models.sms_notification import SmsNotification
from app.models.user import User
from app.services.domain_service import resolve_user_phone
from app.services.sms import SmsProviderUnavailableError, get_sms_provider, sms_provider_configured
from app.services.sms.templates import template_for_event, validate_variables

logger = logging.getLogger("lifelink.sms")


def sms_health() -> dict[str, Any]:
    """Real (but non-probing) status used by GET /admin/system/health's
    `sms` field. Never sends a real SMS to check -- MSG91 has no dedicated
    "ping" endpoint, and burning a real message just to render an admin
    dashboard would be its own kind of dishonesty. So this reports
    configuration state plus recent send history, not live reachability."""
    from app.core.config import settings

    if not settings.msg91_enabled:
        return {"configured": False, "status": "disabled", "provider": "MSG91"}
    if not sms_provider_configured():
        return {
            "configured": False,
            "status": "disabled",
            "provider": "MSG91",
            "reason": "MSG91_ENABLED is true but auth key / flow id / sender id are not all set",
        }
    return {"configured": True, "status": "configured", "provider": "MSG91"}


def recent_failure_count(db: Session, *, limit: int = 20) -> int:
    """How many of the last `limit` SMS attempts failed. Admin-health only;
    never triggers a send."""
    rows = db.scalars(
        select(SmsNotification.status).order_by(SmsNotification.created_at.desc()).limit(limit)
    ).all()
    return sum(1 for s in rows if s == SmsStatus.FAILED)


# Synthetic seed/demo account domains (RFC 2606 reserved names).
SYNTHETIC_ACCOUNT_DOMAINS = ("@lifelink-demo.example.com", "@synthetic.lifelink.example")


def send_for_event(
    db: Session,
    user: User,
    *,
    event_type: str,
    notification_id: uuid.UUID,
    sms_variables: dict[str, str] | None = None,
    related_type: str | None = None,
    related_id: uuid.UUID | None = None,
) -> SmsNotification | None:
    """Attempt (or re-attempt) the SMS for one already-created Notification.

    Returns the SmsNotification audit row, or None when this event type has
    no SMS template mapped at all (nothing to record). Every other outcome
    -- no phone on file, provider unconfigured, validation failure, a real
    provider error -- is recorded on the row rather than raised, so a
    caller failing to send SMS never breaks the notification itself (the
    in-app row and any email already succeeded before this is ever
    reached).
    """
    template_key = template_for_event(event_type)
    if template_key is None:
        return None

    existing = db.scalar(
        select(SmsNotification).where(SmsNotification.notification_id == notification_id)
    )
    if existing is not None and existing.status == SmsStatus.SENT:
        return existing  # already delivered -- idempotent, do not resend

    raw_phone = resolve_user_phone(db, user)
    # 2026-10-31: the synthetic seed accounts (scripts/seed_demo_data.py's
    # 1,000 donors and the shortlist demo donors) carry random, real-looking
    # phone numbers that may belong to real people. They must never be
    # texted, whatever provider is configured: treat them as having no phone.
    if user.email.lower().endswith(SYNTHETIC_ACCOUNT_DOMAINS):
        raw_phone = None
    phone_e164 = None
    if raw_phone:
        try:
            phone_e164 = normalize_phone_e164(raw_phone)
        except ValueError:
            phone_e164 = None

    row = existing or SmsNotification(
        notification_id=notification_id,
        recipient_user_id=user.id,
        event_type=event_type,
        template_key=template_key,
        related_type=related_type,
        related_id=related_id,
    )
    row.recipient_phone_masked = mask_phone(phone_e164)

    if not phone_e164:
        _fail(row, SmsFailureCategory.NO_PHONE_ON_FILE, "no valid phone number on file")
        db.add(row)
        db.flush()
        return row

    variables = sms_variables or {}
    var_error = validate_variables(template_key, variables)
    if var_error:
        _fail(row, SmsFailureCategory.INVALID_TEMPLATE, var_error)
        db.add(row)
        db.flush()
        return row

    try:
        provider = get_sms_provider()
    except SmsProviderUnavailableError as exc:
        _fail(row, SmsFailureCategory.PROVIDER_NOT_CONFIGURED, str(exc))
        db.add(row)
        db.flush()
        return row

    row.retry_count = (existing.retry_count + 1) if existing is not None else 0
    try:
        result = provider.send_sms(
            to_e164=phone_e164, template_key=template_key, variables=variables
        )
    except Exception as exc:  # noqa: BLE001 - a provider bug must never break notify()
        logger.exception("SMS provider raised unexpectedly")
        _fail(row, SmsFailureCategory.UNKNOWN, f"provider raised {type(exc).__name__}")
        db.add(row)
        db.flush()
        return row

    row.provider = provider.name[:20]
    if result.logged_only:
        row.status = SmsStatus.LOGGED_ONLY
        row.sent_at = None
        row.failed_at = None
        row.failure_category = None
        row.failure_detail = (result.failure_detail or "")[:300] or None
    elif result.accepted:
        row.status = SmsStatus.SENT
        row.sent_at = utcnow()
        row.failed_at = None
        row.provider_message_id = result.provider_message_id
        row.failure_category = None
        row.failure_detail = None
    else:
        _fail(
            row,
            result.failure_category or SmsFailureCategory.UNKNOWN,
            result.failure_detail,
        )
    db.add(row)
    db.flush()
    _schedule_retry_if_retryable(row)
    return row


def _schedule_retry_if_retryable(row: SmsNotification) -> bool:
    """Gap elimination: retryable failures (timeout, provider 5xx, rate
    limit) are re-attempted automatically with exponential backoff --
    SMS_RETRY_BASE_SECONDS * 2^retry_count -- up to SMS_MAX_RETRIES, through
    the RQ scheduler. The row stays PENDING while a retry is scheduled and
    becomes FAILED when retries are exhausted. With RQ_SYNC (tests/dev
    without a worker) nothing is scheduled and the failure stands."""
    from datetime import timedelta

    from app.core.config import settings
    from app.models.enums import SMS_RETRYABLE_CATEGORIES

    if row.status != SmsStatus.FAILED or row.failure_category not in SMS_RETRYABLE_CATEGORIES:
        return False
    if row.retry_count >= settings.sms_max_retries or settings.rq_sync:
        return False
    try:
        from app.workers.queue import sms_queue
        from app.workers.tasks import retry_sms

        delay = settings.sms_retry_base_seconds * (2 ** row.retry_count)
        sms_queue.enqueue_in(timedelta(seconds=delay), retry_sms, str(row.notification_id))
    except Exception:  # noqa: BLE001 - Redis down: the failure is recorded as is
        logger.warning("could not schedule SMS retry for %s", row.notification_id)
        return False
    row.status = SmsStatus.PENDING
    return True


def _fail(row: SmsNotification, category: SmsFailureCategory, detail: str | None) -> None:
    row.status = SmsStatus.FAILED
    row.failed_at = utcnow()
    row.failure_category = category
    row.failure_detail = (detail or "")[:300] or None
