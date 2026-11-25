from __future__ import annotations

import logging

from rq import get_current_job

from app.core import metrics
from app.core.request_context import request_id_var
from app.services import email_service

logger = logging.getLogger("lifelink.email")


def send_email(to: str, subject: str, html: str) -> None:
    """RQ job: deliver a single email via SMTP.

    Phase 30: this is the worker-process half of the ENQUEUED/SENT/FAILED
    distinction described in app/services/email_service.py's own
    docstring -- the enqueue side (email_service._enqueue) already logs
    ENQUEUED when the job is accepted by Redis; this logs SENT (meaning:
    the SMTP transaction to the configured mail host completed without
    error -- never "arrived in a real inbox", which no SMTP client can
    prove) or FAILED (this attempt's transport error; RQ's own configured
    `Retry(max=...)` policy -- see Settings.email_job_max_retries --
    decides whether a further attempt happens, unchanged by this phase).
    `get_current_job()` gives the same RQ job ID email_service._enqueue
    already logged at enqueue time, so the two log lines correlate without
    needing to log the recipient address in full.
    """
    job = get_current_job()
    job_id = job.id if job is not None else "-"
    # Phase I: the originating request's id (set by email_service._enqueue)
    # becomes this job's logging context, so `[<request-id>]` on the API's
    # "enqueued" line and on this worker's "sent"/"failed" line match.
    request_id = (job.meta or {}).get("request_id", "-") if job is not None else "-"
    token = request_id_var.set(request_id)
    try:
        _send(job_id, to, subject, html)
    finally:
        request_id_var.reset(token)


def _send(job_id: str, to: str, subject: str, html: str) -> None:
    try:
        email_service._smtp_send(to, subject, html)
    except Exception:
        metrics.inc_email_failed()
        logger.warning(
            "email job %s failed for %s",
            job_id, email_service._mask_email(to), exc_info=True,
        )
        raise
    metrics.inc_email_sent()
    logger.info("email job %s sent for %s", job_id, email_service._mask_email(to))


def rescan_documents() -> dict[str, int]:
    """RQ job (hardening): retry the malware scan of quarantined documents.
    Scheduled by the upload path whenever a scan errored; also safe to run
    on a timer. Re-schedules itself while anything is still SCAN_FAILED."""
    from app.db.session import SessionLocal
    from app.services import document_quarantine

    db = SessionLocal()
    try:
        counts = document_quarantine.rescan_pending(db)
    finally:
        db.close()
    logging.getLogger("lifelink.scanner").info("document rescan: %s", counts)
    if counts.get("SCAN_FAILED"):
        document_quarantine.schedule_rescan()
    return counts


def retry_sms(notification_id: str) -> str:
    """Scheduled re-attempt of one SMS (see sms_service._schedule_retry_if_retryable)."""
    import uuid as _uuid

    from app.db.session import SessionLocal
    from app.models.notification import Notification
    from app.models.user import User
    from app.services import sms_service

    db = SessionLocal()
    try:
        n = db.get(Notification, _uuid.UUID(notification_id))
        user = db.get(User, n.user_id) if n else None
        if n is None or user is None:
            return "gone"
        row = sms_service.send_for_event(db, user, event_type=n.type, notification_id=n.id)
        db.commit()
        return row.status.value if row else "no-template"
    finally:
        db.close()
