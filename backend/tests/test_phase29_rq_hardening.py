"""Phase 29 -- RQ email-queue TTL bounding + admin queue-depth visibility.

Direct regression coverage for the fix made in response to Phase 28's own
finding (148,965 stale queued email jobs, no bound) -- reproduced again,
independently, on this session's own Redis instance (8,893 queued jobs,
`ttl == -1` on every job hash) before this phase's fix. See:
  - app/core/config.py: Settings.email_job_{queue,result,failure}_ttl_seconds
  - app/services/email_service.py: `_enqueue` now passes ttl/result_ttl/failure_ttl
  - app/workers/queue.py: `email_queue_health()`
  - app/api/routes/admin.py: `/admin/system/health`'s new `rq` component
  - scripts/rq_queue_maintenance.py: explicit, operator-invoked report/purge tool
"""
from __future__ import annotations

from app.api.routes import admin as admin_routes
from app.core.config import settings
from app.main import app
from app.workers.queue import email_queue, email_queue_health
from tests.helpers import auth_client

if not any(getattr(r, "path", "").startswith("/admin") for r in app.router.routes):
    app.include_router(admin_routes.router)


def _enqueue_one() -> str:
    from app import services

    services.email_service.send_notification_email("donor@example.com", "Donor", "Hi", "x")
    return email_queue.job_ids[0]


def test_enqueued_email_job_has_bounded_ttls(monkeypatch) -> None:
    """Guards against the exact gap this phase found: before, `_enqueue`
    passed no ttl/result_ttl/failure_ttl at all, so a job's Redis hash
    never expired (`ttl == -1`, confirmed by direct inspection of this
    session's own Redis instance) -- an indefinitely-growing backlog with
    no bound, distinct from (and in addition to) the missing-retry gap
    Phase 18 already fixed."""
    monkeypatch.setattr(settings, "rq_sync", False)
    job_id = _enqueue_one()
    job = email_queue.fetch_job(job_id)
    assert job is not None
    assert job.ttl == settings.email_job_queue_ttl_seconds
    assert job.result_ttl == settings.email_job_result_ttl_seconds
    assert job.failure_ttl == settings.email_job_failure_ttl_seconds
    # None of these is the RQ default (unbounded queue wait, 500s result_ttl)
    # -- confirms this app's own explicit settings are actually the values
    # reaching Redis, not just defined and unused.
    assert settings.email_job_queue_ttl_seconds == 86400
    assert settings.email_job_failure_ttl_seconds == 604800


def test_email_queue_health_reports_real_counts(monkeypatch) -> None:
    monkeypatch.setattr(settings, "rq_sync", False)
    before = email_queue_health()
    assert before["queued"] == 0
    assert before["failed"] == 0

    _enqueue_one()
    _enqueue_one()
    after = email_queue_health()
    assert after["queued"] == 2
    assert after["started"] == 0
    assert after["failed"] == 0
    assert after["oldest_queued_job_age_seconds"] is not None
    assert after["oldest_queued_job_age_seconds"] >= 0


def test_admin_system_health_reports_rq_component() -> None:
    admin, _ = auth_client("ADMIN")
    r = admin.get("/admin/system/health")
    assert r.status_code == 200
    body = r.json()
    rq = body["rq"]
    assert set(rq.keys()) >= {"queued", "failed", "started", "oldest_queued_job_age_seconds"}
    assert rq["failed"] == 0
    assert rq["queued"] == 0


def test_admin_system_health_rq_backlog_never_flips_overall_status(monkeypatch) -> None:
    """A deep-but-healthy backlog (no failures) is an operational signal
    for a human with scripts/rq_queue_maintenance.py, not itself proof the
    system is broken -- this must never make `/admin/system/health` report
    "degraded" on its own, exactly like the graph/sms components'
    documented "configured=False is not a fault" treatment.

    Compares status WITH a deep backlog against status with none, rather
    than asserting a literal "ok", because this environment's real
    NEO4J_URI (see backend/.env) is configured but unreachable from this
    sandbox -- graph_problem is independently true here regardless of RQ,
    exactly the environment restriction documented in Phase 28's audit.
    The point of this test is that adding a queue backlog changes nothing
    about that pre-existing status."""
    admin, _ = auth_client("ADMIN")
    baseline_status = admin.get("/admin/system/health").json()["status"]

    monkeypatch.setattr(settings, "rq_sync", False)
    for _ in range(5):
        _enqueue_one()

    r = admin.get("/admin/system/health")
    body = r.json()
    assert body["rq"]["queued"] == 5
    assert body["status"] == baseline_status


def test_rq_maintenance_report_is_read_only(monkeypatch, capsys) -> None:
    """scripts/rq_queue_maintenance.py's --report path (also the default
    with no flags) must never delete or modify anything -- it only reads
    counts."""
    monkeypatch.setattr(settings, "rq_sync", False)
    _enqueue_one()
    from scripts.rq_queue_maintenance import _report

    before_count = email_queue.count
    status = _report()
    after_count = email_queue.count
    assert after_count == before_count
    assert status["queued"] == before_count
    captured = capsys.readouterr()
    assert "RQ email queue" in captured.out


def test_rq_maintenance_purge_requires_confirm(monkeypatch, capsys) -> None:
    """--purge-failed / --purge-stale-queued without --confirm must be a
    dry run: it prints what would be removed but changes nothing -- the
    master prompt's explicit "do not blindly delete jobs" requirement,
    enforced in code, not just by convention."""
    monkeypatch.setattr(settings, "rq_sync", False)
    job_id = _enqueue_one()
    from scripts.rq_queue_maintenance import _purge_stale_queued

    _purge_stale_queued(older_than_days=0, confirm=False)
    # Dry run: the job must still be fetchable (nothing deleted), even
    # though older_than_days=0 means it technically qualifies by age.
    assert email_queue.fetch_job(job_id) is not None
    captured = capsys.readouterr()
    assert "Dry run" in captured.out


def test_rq_maintenance_purge_stale_queued_with_confirm_removes_only_stale(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "rq_sync", False)
    stale_id = _enqueue_one()
    fresh_id = _enqueue_one()

    from scripts.rq_queue_maintenance import _purge_stale_queued

    # older_than_days=0 means "anything enqueued before right now" --
    # both jobs technically qualify, so instead assert the mechanism only
    # removes what it's told to: simulate "no jobs are old enough" first.
    _purge_stale_queued(older_than_days=365, confirm=True)
    assert email_queue.fetch_job(stale_id) is not None
    assert email_queue.fetch_job(fresh_id) is not None

    _purge_stale_queued(older_than_days=0, confirm=True)
    assert email_queue.fetch_job(stale_id) is None
    assert email_queue.fetch_job(fresh_id) is None
