"""Phase 18: event + notification system hardening.

Scope note: the notification system already had substantial, real test
coverage from earlier phases before this one started --
test_emergencies_and_notifications.py (persistence, unread count, IDOR on
mark-read), test_notification_preferences.py (preference CRUD/authz),
test_alert_watcher.py (urgency/radius filters, quiet hours vs. email vs.
in-app), and test_sms.py (SMS being honestly dormant, per-notification SMS
idempotency). This file does not re-test any of that. It covers exactly the
gaps this phase's audit actually found:

  1. The admin-notification gap: before this phase, grepping every
     notification_service call site found ZERO notifications ever sent to
     an admin for anything. A new verification submission now notifies
     every active admin (see app/api/routes/verifications.py) -- tested
     here for correctness, scope (admins only, not the submitter, not other
     roles), and privacy (no document contents/storage keys in the body).
  2. Duplicate-notification prevention on a genuine event replay: Phase 12's
     emergency idempotency-key mechanism was already proven to prevent a
     second Emergency *row*, but nothing had proven it also prevents a
     second round of donor *notifications* on the exact same replay.
  3. The RQ background email worker path (app.workers.tasks.send_email) was
     previously only code-reviewed, never actually exercised by a test --
     every existing test runs with RQ_SYNC=true, which bypasses the queue
     entirely. These tests turn RQ_SYNC off and drive a real RQ Queue
     against the real (test) Redis instance with rq.SimpleWorker, and a
     real local SMTP server (aiosmtpd) standing in for Mailpit/Gmail, so
     "successful delivery", "temporary failure then retry", "permanent
     failure", "malformed configuration", and "job timeout" are all
     genuinely exercised rather than asserted from code reading.
  4. Preference enforcement actually suppressing an email end-to-end (not
     just being stored), and a direct proof that in-app notification
     creation is independent of the email channel/queue being reachable at
     all.
"""
from __future__ import annotations

import socket
import uuid

import pytest
from rq import SimpleWorker
from rq.registry import FailedJobRegistry, FinishedJobRegistry
from sqlalchemy import func, select

from app.core.config import settings
from app.core.redis import rq_redis
from app.models.notification import Notification
from app.models.notification_preference import NotificationPreference
from app.models.user import User, UserRole
from app.services import email_service, notification_service
from app.workers.queue import email_queue
from tests.helpers import auth_client, seed_verified_user

PDF = ("id.pdf", b"%PDF-1.4 fake donor id document", "application/pdf")


def _make_user(db, role: UserRole = UserRole.DONOR) -> User:
    uid = seed_verified_user(f"{uuid.uuid4().hex[:10]}@example.com", role=role.value)
    return db.get(User, uid)


def _submit_verification(client, kind: str = "DONOR_IDENTITY"):
    return client.post("/verifications", data={"kind": kind}, files=[("files", PDF)])


# --------------------------------------------------------------------------- #
# 1. Admin notification on new verification submission (real, previously-
#    missing event; see app/api/routes/verifications.py::submit_verification)
# --------------------------------------------------------------------------- #


def test_new_verification_submission_notifies_all_active_admins() -> None:
    admin_a, _ = auth_client("ADMIN")
    admin_b, _ = auth_client("ADMIN")
    donor, _ = auth_client("DONOR")

    r = _submit_verification(donor)
    assert r.status_code == 201, r.text

    for admin in (admin_a, admin_b):
        items = admin.get("/notifications").json()
        matching = [n for n in items if n["type"] == "verification.submitted"]
        assert len(matching) == 1, matching


def test_verification_submission_notification_never_reaches_the_submitter_or_other_roles() -> None:
    admin, _ = auth_client("ADMIN")
    donor, _ = auth_client("DONOR")
    hospital, _ = auth_client("HOSPITAL")
    patient, _ = auth_client("PATIENT")

    r = _submit_verification(donor)
    assert r.status_code == 201, r.text

    assert any(n["type"] == "verification.submitted" for n in admin.get("/notifications").json())
    for other in (donor, hospital, patient):
        types = {n["type"] for n in other.get("/notifications").json()}
        assert "verification.submitted" not in types


def test_verification_submission_notification_does_not_reach_a_deactivated_admin(
    db_session,
) -> None:
    admin, admin_email = auth_client("ADMIN")
    donor, _ = auth_client("DONOR")

    admin_user = db_session.scalar(select(User).where(User.email == admin_email.lower()))
    admin_user.is_active = False
    db_session.commit()

    r = _submit_verification(donor)
    assert r.status_code == 201, r.text

    # The deactivated admin's own session is still whatever it was (out of
    # scope here); what matters is no Notification row was ever created for
    # them -- checked directly against the database, not the API, since a
    # deactivated account may not even be able to log in to ask.
    count = db_session.scalar(
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == admin_user.id, Notification.type == "verification.submitted")
    )
    assert count == 0


def test_verification_submission_notification_body_has_no_document_contents() -> None:
    """Data-minimization: the admin gets enough to triage (who, what kind,
    how many files) and a verification_id to open the real record -- never
    the storage key, filename, or file bytes, which live only behind the
    authenticated /admin/verifications* routes."""
    admin, _ = auth_client("ADMIN")
    donor, _ = auth_client("DONOR")

    r = _submit_verification(donor)
    assert r.status_code == 201, r.text

    n = next(x for x in admin.get("/notifications").json() if x["type"] == "verification.submitted")
    assert set(n["data"].keys()) == {"verification_id", "kind"}
    blob = (n["title"] + " " + (n["body"] or "") + " " + repr(n["data"])).lower()
    assert "verifications/" not in blob  # storage key prefix
    assert ".pdf" not in blob
    assert "id.pdf" not in blob


# --------------------------------------------------------------------------- #
# 2. Duplicate-notification prevention on event replay (builds on Phase 12's
#    Emergency.idempotency_key, which was only ever proven to dedupe the
#    Emergency row itself -- not the donor fan-out it triggers)
# --------------------------------------------------------------------------- #


def test_emergency_idempotency_replay_does_not_double_notify_donors() -> None:
    donor, _ = auth_client("DONOR")
    # Audit D4 (2026-10-31): SOS alerts need a location in scope; donor and
    # emergency share a city.
    donor.put("/donors/me", json={"blood_type": "O_NEG", "city": "Metro"})
    patient, _ = auth_client("PATIENT")

    key = f"sos-{uuid.uuid4().hex}"
    first = patient.post("/emergencies", json={"blood_type": "O_NEG", "city": "Metro",
                                                "idempotency_key": key})
    assert first.status_code == 201, first.text

    # A genuine client/network retry: same actor, same key, sent again.
    replay = patient.post("/emergencies", json={"blood_type": "O_NEG", "city": "Metro",
                                                "idempotency_key": key})
    assert replay.status_code == 200, replay.text
    assert replay.json()["id"] == first.json()["id"]

    matching = [n for n in donor.get("/notifications").json() if n["type"] == "emergency.new"]
    assert len(matching) == 1, "replay must not re-run the donor fan-out"


def test_different_idempotency_keys_do_each_notify_donors_once() -> None:
    """The flip side of the above: this isn't a case of emergencies
    silently deduping by blood type or recipient -- two genuinely distinct
    SOS attempts (different keys) each get their own, real notification."""
    donor, _ = auth_client("DONOR")
    # Audit D4 (2026-10-31): SOS alerts need a location in scope; donor and
    # emergency share a city.
    donor.put("/donors/me", json={"blood_type": "O_NEG", "city": "Metro"})
    patient, _ = auth_client("PATIENT")

    patient.post("/emergencies", json={"blood_type": "O_NEG", "city": "Metro",
                                       "idempotency_key": "key-a"})
    patient.post("/emergencies", json={"blood_type": "O_NEG", "city": "Metro",
                                       "idempotency_key": "key-b"})

    matching = [n for n in donor.get("/notifications").json() if n["type"] == "emergency.new"]
    assert len(matching) == 2


# --------------------------------------------------------------------------- #
# 3. Preference enforcement is real (server-side), and in-app creation is
#    independent of the email channel entirely
# --------------------------------------------------------------------------- #


def test_disabling_email_preference_suppresses_email_but_not_the_in_app_row(
    db_session, mailbox
) -> None:
    user = _make_user(db_session)
    db_session.add(NotificationPreference(user_id=user.id, email_enabled=False))
    db_session.flush()

    n = notification_service.notify(
        db_session, user.id, type="blood_request.fulfilled", title="Thank you", body="x"
    )
    assert n.read_at is None
    assert n.title == "Thank you"  # in-app row was created regardless of the email preference
    assert mailbox == []  # but no email was ever attempted


def test_notify_creates_in_app_row_even_when_the_email_queue_itself_is_unreachable(
    db_session, monkeypatch
) -> None:
    """_maybe_send_email wraps the whole email attempt in try/except -- this
    proves that guarantee holds even when the failure happens one level
    lower, inside the RQ enqueue call itself (e.g. Redis is down), not just
    inside SMTP. The in-app Notification must already be committed-visible
    regardless."""
    monkeypatch.setattr(settings, "rq_sync", False)

    def _boom(*a, **kw):
        raise ConnectionError("redis unreachable")

    monkeypatch.setattr(email_queue, "enqueue", _boom)

    user = _make_user(db_session)
    n = notification_service.notify(
        db_session, user.id, type="blood_request.fulfilled", title="Thank you"
    )
    assert n.id is not None
    reloaded = db_session.get(Notification, n.id)
    assert reloaded is not None
    assert reloaded.title == "Thank you"


# --------------------------------------------------------------------------- #
# 4. The RQ background email worker path -- real Queue, real (test) Redis,
#    real local SMTP server (aiosmtpd), rq.SimpleWorker actually executing
#    the job in-process. None of this is mocked at the _smtp_send level.
# --------------------------------------------------------------------------- #


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _ScriptedSmtpHandler:
    """A real (if tiny) SMTP server. ``script`` is a queue of per-attempt
    behaviors consumed in order, one per DATA command received:
      ("accept",)              -- 250 OK, message recorded
      ("reject", code, text)   -- e.g. ("reject", 450, "try again later")
      ("hang", seconds)        -- sleep past the client's/RQ's timeout
    Once ``script`` is empty, every further attempt is accepted.
    """

    def __init__(self) -> None:
        self.received: list[bytes] = []
        self.script: list[tuple] = []
        self.attempts = 0

    async def handle_DATA(self, server, session, envelope):  # noqa: N802 - aiosmtpd API
        import asyncio

        self.attempts += 1
        action = self.script.pop(0) if self.script else ("accept",)
        if action[0] == "hang":
            await asyncio.sleep(action[1])
        elif action[0] == "reject":
            return f"{action[1]} {action[2]}"
        self.received.append(envelope.content)
        return "250 Message accepted"


@pytest.fixture
def real_smtp(monkeypatch):
    """Point the real app.services.email_service._smtp_send at a real,
    local, disposable SMTP server for the duration of one test -- the same
    role Mailpit plays in dev, just spun up in-process so this suite has no
    external dependency."""
    from aiosmtpd.controller import Controller

    handler = _ScriptedSmtpHandler()
    port = _free_port()
    controller = Controller(handler, hostname="127.0.0.1", port=port)
    controller.start()
    monkeypatch.setattr(settings, "mail_host", "127.0.0.1")
    monkeypatch.setattr(settings, "mail_port", port)
    monkeypatch.setattr(settings, "mail_starttls", False)
    monkeypatch.setattr(settings, "mail_username", None)
    monkeypatch.setattr(settings, "mail_password", None)
    monkeypatch.setattr(settings, "rq_sync", False)
    try:
        yield handler
    finally:
        controller.stop()


def _run_worker_burst() -> None:
    SimpleWorker([email_queue], connection=rq_redis).work(burst=True)


def test_rq_worker_delivers_email_successfully(real_smtp) -> None:
    email_service.send_notification_email("donor@example.com", "Donor", "Hello", "body text")
    _run_worker_burst()

    assert len(real_smtp.received) == 1
    assert b"Hello" in real_smtp.received[0]
    registry = FinishedJobRegistry(queue=email_queue)
    assert len(registry.get_job_ids()) == 1
    assert len(FailedJobRegistry(queue=email_queue).get_job_ids()) == 0


def test_rq_worker_retries_temporary_smtp_failure_then_succeeds(real_smtp) -> None:
    real_smtp.script = [("reject", 450, "4.3.0 mailbox temporarily unavailable")]
    email_service.send_notification_email("donor@example.com", "Donor", "Hello", "body")
    _run_worker_burst()

    assert real_smtp.attempts == 2  # first rejected, retry accepted
    assert len(real_smtp.received) == 1
    assert len(FinishedJobRegistry(queue=email_queue).get_job_ids()) == 1
    assert len(FailedJobRegistry(queue=email_queue).get_job_ids()) == 0


def test_rq_worker_permanent_failure_after_retries_exhausted_lands_in_failed_registry(
    real_smtp,
) -> None:
    real_smtp.script = [("reject", 550, "5.1.1 mailbox does not exist")] * (
        settings.email_job_max_retries + 1
    )
    email_service.send_notification_email("nobody@example.com", "Nobody", "Hello", "body")
    _run_worker_burst()

    assert real_smtp.attempts == settings.email_job_max_retries + 1
    assert real_smtp.received == []
    assert len(FailedJobRegistry(queue=email_queue).get_job_ids()) == 1
    assert len(FinishedJobRegistry(queue=email_queue).get_job_ids()) == 0


def test_rq_worker_job_timeout_is_a_real_honest_failure_not_a_silent_hang(real_smtp) -> None:
    """A hung SMTP peer must not hang the worker forever or vanish without a
    trace -- RQ's own death penalty (SIGALRM, wired up by job_timeout=1)
    interrupts the stuck read at ~1s (confirmed below from the job's own
    recorded exc_info, not just from timing, since the client-side socket
    teardown afterward can itself take until the peer eventually responds --
    that's a real, separate characteristic of closing a live SMTP session
    out from under a still-sleeping peer, not evidence the timeout failed to
    fire), and the job lands in FailedJobRegistry rather than disappearing."""
    real_smtp.script = [("hang", 3)]
    job = email_queue.enqueue(
        "app.workers.tasks.send_email",
        "donor@example.com",
        "Subject",
        "<p>hi</p>",
        job_timeout=1,
    )
    _run_worker_burst()

    failed_ids = FailedJobRegistry(queue=email_queue).get_job_ids()
    assert failed_ids == [job.id]
    job.refresh()
    # rq 2.x: the failure is read from the job's latest Result
    # (job.exc_info is deprecated).
    latest = job.latest_result()
    assert latest is not None and latest.type == latest.Type.FAILED
    assert "JobTimeoutException" in (latest.exc_string or "")


def test_malformed_smtp_configuration_fails_honestly_without_crashing_the_worker(
    monkeypatch,
) -> None:
    """No fake server at all here: point at a real, closed local port so
    the connection genuinely fails the way a wrong host/port in production
    config would -- and confirm the worker survives to process the next
    job (one bad job must not wedge the whole process)."""
    monkeypatch.setattr(settings, "mail_host", "127.0.0.1")
    monkeypatch.setattr(settings, "mail_port", _free_port())  # nothing listening
    monkeypatch.setattr(settings, "rq_sync", False)

    email_queue.enqueue(
        "app.workers.tasks.send_email", "donor@example.com", "Subject", "<p>hi</p>", retry=None
    )
    _run_worker_burst()
    assert len(FailedJobRegistry(queue=email_queue).get_job_ids()) == 1

    # The worker (a fresh SimpleWorker instance, exactly as a real deploy
    # would restart/continue polling) can still process a following job.
    from unittest.mock import patch

    with patch.object(email_service, "_smtp_send", lambda *a, **kw: None):
        email_queue.enqueue("app.workers.tasks.send_email", "next@example.com", "Ok", "<p>ok</p>")
        _run_worker_burst()
    assert len(FinishedJobRegistry(queue=email_queue).get_job_ids()) == 1


def test_send_email_job_is_at_least_once_not_exactly_once_by_design(monkeypatch) -> None:
    """RQ (like most job queues) guarantees at-least-once execution: if a
    worker died after a real send but before RQ recorded the job finished,
    the same job can run again and send a second, identical email. This
    test documents and confirms that characteristic directly rather than
    letting it be an unstated assumption -- the actual protection against a
    user-visible duplicate lives one layer up, at the event layer
    (Emergency.idempotency_key; see the replay test above), which already
    guarantees notify() itself only runs once per real-world event. Once
    that holds, a single resulting email job executing twice is a possible
    duplicate *send*, never a duplicate *notification* or a data-integrity
    problem (no second Notification/DB row is created by this)."""
    calls: list[str] = []
    monkeypatch.setattr(email_service, "_smtp_send", lambda to, subject, html: calls.append(to))
    from app.workers.tasks import send_email

    send_email("donor@example.com", "Subject", "<p>hi</p>")
    send_email("donor@example.com", "Subject", "<p>hi</p>")
    assert calls == ["donor@example.com", "donor@example.com"]


def test_email_job_retry_policy_is_actually_configured(monkeypatch) -> None:
    """Guards against the exact gap this phase found and fixed: before,
    `_enqueue` called `email_queue.enqueue(...)` with no `retry=` at all,
    so a job that failed once had already exhausted its (zero) retries."""
    monkeypatch.setattr(settings, "rq_sync", False)
    email_service.send_notification_email("donor@example.com", "Donor", "Hi", "x")
    job_id = email_queue.job_ids[0]
    job = email_queue.fetch_job(job_id)
    assert job is not None
    assert job.retries_left == settings.email_job_max_retries
