"""Outbound email.

In production, messages are enqueued onto RQ and delivered by the worker
(``app.workers.worker``). With ``RQ_SYNC=true`` (dev/tests) they are delivered
inline. Every send is also appended to :data:`sent_messages` for inspection by
the test suite.
"""
from __future__ import annotations

import functools
import logging
import smtplib
import ssl
from email.message import EmailMessage
from html import escape as _esc
from typing import Any

from app.core import metrics
from app.core.config import settings
from app.core.request_context import get_request_id

logger = logging.getLogger("lifelink.email")

# In-memory record of everything sent from THIS process. Used by tests.
sent_messages: list[dict[str, Any]] = []


def _mask_email(address: str) -> str:
    """Phase 30: a correlation-safe stand-in for a real email address in
    log lines -- keeps the domain (useful for spotting "every failure is
    to the same bad domain"-shaped incidents) while dropping everything
    that identifies a specific person, per the master prompt's explicit
    "be careful with email addresses" instruction. Never used for
    anything except a log message; the real address is still what's
    actually used to send."""
    local, _, domain = address.partition("@")
    if not domain:
        return "***"
    return f"{local[:1]}***@{domain}"


@functools.lru_cache(maxsize=1)
def _tls_context() -> ssl.SSLContext:
    """Verifying client context (certificate + hostname), built once."""
    return ssl.create_default_context()


def _smtp_send(to: str, subject: str, html: str) -> None:
    msg = EmailMessage()
    msg["From"] = settings.mail_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content("This message requires an HTML-capable email client.")
    msg.add_alternative(html, subtype="html")

    # Hardening: TLS with certificate + hostname verification. Previously
    # `server.starttls()` was called without a context, which in CPython
    # uses an UNVERIFIED context -- a network attacker could present any
    # certificate and read the Gmail App Password sent by `login()`.
    # The context is built only when TLS is used, and once per process:
    # loading the CA bundle on every send made plain-SMTP sends (Mailpit)
    # ~100 ms slower each, which turned a blood request's inline donor
    # notification fan-out (RQ_SYNC=true) into a 10-40 s request.
    timeout = settings.mail_timeout_seconds
    if settings.mail_use_ssl:
        server: smtplib.SMTP = smtplib.SMTP_SSL(
            settings.mail_host, settings.mail_port, timeout=timeout, context=_tls_context()
        )
    else:
        server = smtplib.SMTP(settings.mail_host, settings.mail_port, timeout=timeout)
    with server:
        if settings.mail_starttls:
            server.starttls(context=_tls_context())
        if settings.mail_username and settings.mail_password:
            server.login(settings.mail_username, settings.mail_password.get_secret_value())
        server.send_message(msg)


def deliver(to: str, subject: str, html: str) -> None:
    """Deliver a message INLINE (``RQ_SYNC=true`` -- dev/tests only; see
    ``_enqueue`` below). The real, out-of-request worker path
    (``app.workers.tasks.send_email``) calls ``_smtp_send`` directly instead
    of this function, precisely so that path keeps failing loudly into RQ's
    own failed-job registry (Phase 17 Part F: a background job's failure
    must not silently disappear).

    This inline path is different: it runs synchronously inside whatever
    request triggered it (registration, password reset, or a notification's
    real-time email echo), all of which happen *before* that request's
    ``db.commit()``. Before Phase 17, an SMTP failure here (mail server
    down, connection refused, timeout) would propagate uncaught and roll
    back an otherwise-successful registration/password-reset/notification
    purely because the mail server was temporarily unreachable -- exactly
    the "a failed email provider must not roll back a completed critical
    operation" case Part G calls out. Fixed the same way
    ``notification_service._maybe_send_email`` already handles this for its
    own call site: never raise, log, and let the primary operation proceed.
    The user simply doesn't get that one email (they can always request a
    fresh verification link / reset code) rather than having their entire
    request fail.
    """
    sent_messages.append({"to": to, "subject": subject, "html": html})
    if settings.is_test:
        return
    try:
        _smtp_send(to, subject, html)
    except Exception:  # noqa: BLE001 -- best-effort inline send, see docstring
        metrics.inc_email_failed()
        # Phase 30: transport-level failure only (a connection reset,
        # timeout, or SMTP auth error) -- distinguished from a successful
        # SMTP transaction below, which itself only proves TRANSPORT
        # success (the message left this process for the configured mail
        # host), never that it reached a real inbox -- see this module's
        # own docstring and docs/OPERATIONS_RUNBOOK.md's SMTP section for
        # why "delivered" is never claimed anywhere in this codebase.
        logger.warning(
            "inline email delivery failed for %s (subject_len=%d)",
            _mask_email(to), len(subject), exc_info=True,
        )
        return
    metrics.inc_email_sent()
    logger.info("inline email transport-succeeded for %s", _mask_email(to))


def _enqueue(to: str, subject: str, html: str) -> None:
    if settings.rq_sync:
        deliver(to, subject, html)
        return
    from rq import Retry

    from app.workers.queue import email_queue

    # Phase 18: give the worker job an actual retry policy (see
    # Settings.email_job_max_retries) so a transient SMTP failure gets a
    # second chance instead of landing in FailedJobRegistry after exactly
    # one attempt -- previously there was no retry configured here at all.
    job = email_queue.enqueue(
        "app.workers.tasks.send_email",
        to,
        subject,
        html,
        # QA fix: EMAIL_JOB_MAX_RETRIES=0 ("no retries") used to raise
        # ValueError from rq.Retry(max=0) inside the request -> HTTP 500 on
        # registration. 0 now means exactly one attempt.
        retry=(
            Retry(max=settings.email_job_max_retries)
            if settings.email_job_max_retries > 0 else None
        ),
        # Phase 29: bound how long this job can live in Redis in every
        # state (queued/succeeded/failed) -- see Settings.email_job_*_ttl_
        # seconds' own docstring for why (Phase 28's 148,965-stale-job
        # finding; this session independently reproduced an 8,893-job
        # backlog with ttl=-1 i.e. no expiry at all before this fix).
        ttl=settings.email_job_queue_ttl_seconds,
        result_ttl=settings.email_job_result_ttl_seconds,
        failure_ttl=settings.email_job_failure_ttl_seconds,
        # Phase I: carry the originating HTTP request's id into the job so
        # the worker's log lines for it share that id (tasks.py restores it
        # into the logging context). In job meta, not the call args, so the
        # task signature -- and any job already queued -- is unchanged.
        meta={"request_id": get_request_id()},
        # Phase I: RQ defaults the description to the call repr (recipient
        # address + body excerpt); it is stored in Redis and shown by RQ
        # tooling, so keep it free of personal data.
        description="send_email",
    )
    # Phase 30: ENQUEUED is deliberately a distinct, weaker claim than SENT
    # -- see this module's own docstring. This only proves the job was
    # accepted by Redis/RQ, never that app.workers.tasks.send_email has run
    # yet (that job-level SENT/FAILED distinction is logged from tasks.py,
    # by the worker process, once it actually runs).
    metrics.inc_email_enqueued()
    logger.info("email job %s enqueued for %s", job.id, _mask_email(to))


def send_verification_email(to: str, full_name: str, token: str) -> None:
    # Phase 23: full_name is user-supplied free text (set at registration and
    # editable afterwards) interpolated into an HTML email body -- previously
    # unescaped, so a name like `<img src=x onerror=...>` would execute as
    # real HTML in any client that renders it. `link` is not user input (built
    # from server config + a `secrets.token_urlsafe` value, which is URL-safe
    # base64 and contains no HTML metacharacters), so it is not escaped here.
    html = f"""
    <p>Hi {_esc(full_name)},</p>
    <p>Welcome to LifeLink. Please confirm your email address:</p>
    <p><a href="{settings.frontend_base_url}/verify-email?token={token}">Verify my email</a></p>
    <p>This link expires in {settings.email_verification_ttl_seconds // 3600} hours.
       If you did not create an account, you can ignore this message.</p>
    """
    _enqueue(to, "Verify your LifeLink email", html)


def send_notification_email(to: str, full_name: str, title: str, body: str | None) -> None:
    """Real-time email echo of an in-app notification.

    Only called by ``notification_service.notify`` when the recipient's
    :class:`~app.models.notification_preference.NotificationPreference` has
    ``email_enabled`` true and ``digest_frequency`` set to ``REALTIME`` (or the
    event is an emergency alert, which always sends regardless of digest
    setting). Batched ``DAILY`` digests and quiet-hours suppression are not
    implemented in this phase -- the preference is stored and read, but no
    scheduled digest job exists yet; ``DAILY``/``OFF`` simply suppress the
    immediate send.
    """
    # Phase 23: `title`/`body` often echo free text supplied by a *different*
    # user than the recipient (an emergency's location note, a blood
    # request's reason, a family-link display name, etc. -- see
    # notification_service.notify call sites), and were previously
    # interpolated into this HTML email with zero escaping: a crafted
    # title/body/full_name could inject arbitrary markup (including a fake
    # link or script-bearing tag in an HTML-rendering mail client) into an
    # email delivered to someone who never typed it. All three are escaped;
    # the subject line passed to `_enqueue` is plain-text (email headers, not
    # HTML) and is intentionally left as-is.
    html = f"""
    <p>Hi {_esc(full_name)},</p>
    <p style="font-size:16px;font-weight:bold">{_esc(title)}</p>
    {f'<p>{_esc(body)}</p>' if body else ""}
    <p style="color:#888;font-size:12px">
      You're receiving this because real-time email notifications are enabled in your
      LifeLink account settings. You can change this any time.
    </p>
    """
    _enqueue(to, title, html)


def send_password_reset_otp(to: str, full_name: str, otp: str) -> None:
    # `otp` is server-generated (`security.generate_otp`, digits only) and
    # needs no escaping; `full_name` is user-supplied, see note above.
    minutes = settings.password_reset_otp_ttl_seconds // 60
    html = f"""
    <p>Hi {_esc(full_name)},</p>
    <p>Your LifeLink password reset code is:</p>
    <p style="font-size:22px;font-weight:bold;letter-spacing:3px">{otp}</p>
    <p>It expires in {minutes} minutes. If you did not request a password reset,
       no action is needed and your password stays unchanged.</p>
    """
    _enqueue(to, "Your LifeLink password reset code", html)
