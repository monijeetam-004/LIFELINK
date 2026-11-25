"""Phase I -- the originating request id follows an email job into the worker.

Real RQ queue on real Redis, real `rq.SimpleWorker` in burst mode (the
same class `app/workers/worker.py` runs, minus the fork); only the SMTP
transport is replaced, so nothing leaves the machine.
"""

from __future__ import annotations

import logging

from rq import SimpleWorker

from app.core.config import settings
from app.core.redis import rq_redis
from app.services import email_service
from app.workers.queue import email_queue
from tests.helpers import seed_verified_user


def test_request_id_reaches_the_worker_log_lines(client, monkeypatch, caplog) -> None:
    monkeypatch.setattr(settings, "rq_sync", False)
    sent: list[str] = []
    monkeypatch.setattr(email_service, "_smtp_send", lambda to, subject, html: sent.append(to))
    email_queue.empty()
    seed_verified_user("corr@example.com", "a decent passphrase", "DONOR")

    caplog.set_level(logging.INFO)
    r = client.post(
        "/auth/forgot-password",
        json={"email": "corr@example.com"},
        headers={"Origin": "http://localhost:5173", "X-Request-Id": "corr-phase-i-42"},
    )
    assert r.status_code in (200, 202), r.text
    assert r.headers["X-Request-Id"] == "corr-phase-i-42"

    jobs = email_queue.get_jobs()
    assert len(jobs) == 1
    assert jobs[0].meta["request_id"] == "corr-phase-i-42"

    caplog.clear()
    SimpleWorker([email_queue], connection=rq_redis).work(burst=True)
    assert sent == ["corr@example.com"]
    sent_lines = [
        r for r in caplog.records if r.name == "lifelink.email" and "sent for" in r.getMessage()
    ]
    assert len(sent_lines) == 1
    assert sent_lines[0].request_id == "corr-phase-i-42"


def test_job_without_request_meta_still_runs_with_placeholder(monkeypatch, caplog) -> None:
    """Jobs enqueued before this change (no meta) must still be processed."""
    sent: list[str] = []
    monkeypatch.setattr(email_service, "_smtp_send", lambda to, subject, html: sent.append(to))
    email_queue.empty()
    email_queue.enqueue("app.workers.tasks.send_email", "old@example.com", "s", "<p>h</p>")
    caplog.set_level(logging.INFO)
    SimpleWorker([email_queue], connection=rq_redis).work(burst=True)
    assert sent == ["old@example.com"]
    line = [
        r for r in caplog.records if r.name == "lifelink.email" and "sent for" in r.getMessage()
    ][0]
    assert line.request_id == "-"


def test_worker_entrypoint_configures_logging_and_hides_job_descriptions(monkeypatch) -> None:
    """The real `python -m app.workers.worker` entrypoint (Worker.work
    stubbed so it returns): app logging is installed and RQ is told not to
    log job descriptions."""
    import app.core.logging_config as lc
    from app.workers import worker as worker_mod

    seen = {}

    def fake_work(self, **kw):
        seen["log_job_description"] = self.log_job_description
        seen["queues"] = [q.name for q in self.queues]

    calls = []
    monkeypatch.setattr(lc, "_configured", False)
    monkeypatch.setattr(worker_mod, "configure_logging", lambda: calls.append(1))
    monkeypatch.setattr(worker_mod.Worker, "work", fake_work)
    worker_mod.main()
    assert calls == [1]
    assert seen["log_job_description"] is False
    assert seen["queues"] == [settings.rq_queue_name, settings.rq_sms_queue_name]


def test_enqueued_job_description_carries_no_personal_data(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "rq_sync", False)
    email_queue.empty()
    seed_verified_user("pii@example.com", "a decent passphrase", "DONOR")
    client.post(
        "/auth/forgot-password",
        json={"email": "pii@example.com"},
        headers={"Origin": "http://localhost:5173"},
    )
    job = email_queue.get_jobs()[0]
    assert job.description == "send_email"
    assert "pii@example.com" not in job.description
    email_queue.empty()
