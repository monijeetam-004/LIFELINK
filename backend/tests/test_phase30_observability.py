"""Phase 30 -- monitoring, structured logging, and operational observability.

Correctness of the underlying failure-handling behavior (DB/Redis outage ->
honest 503, storage outage -> clean 503/404, Neo4j fallback, etc.) is
already covered by tests/test_phase17_resilience.py and
tests/test_phase20_secure_file_storage.py and is NOT re-tested here -- this
file covers only what Phase 30 actually added: the metrics registry
(app/core/metrics.py), the access-log/metrics middleware
(app/core/request_metrics.py), the RQ queue-health status classification
and its configurable thresholds (app/workers/queue.py), the admin-only
metrics endpoint, the new admin-health fields (version, db_pool, rq
status/workers), the readiness endpoint's new `dependency_states`, and the
email/storage observability logging added this phase (masked addresses,
no storage keys logged).
"""
from __future__ import annotations

import logging
import time

from fastapi.testclient import TestClient
from rq import Queue, SimpleWorker

from app.api.routes import admin as admin_routes
from app.core import metrics
from app.core.config import settings
from app.core.redis import rq_redis
from app.main import app
from app.workers.queue import _queue_status, email_queue_health
from tests.helpers import auth_client

if not any(getattr(r, "path", "").startswith("/admin") for r in app.router.routes):
    app.include_router(admin_routes.router)


# --------------------------------------------------------------------------- #
# Metrics registry
# --------------------------------------------------------------------------- #
def test_metrics_records_bounded_route_label_not_raw_url(client: TestClient) -> None:
    metrics.reset_for_tests()
    client, _ = auth_client("PATIENT")
    r = client.get("/health")
    assert r.status_code == 200

    snap = metrics.snapshot()
    routes = {row["route"] for row in snap["http_requests"]}
    # The matched route's own path TEMPLATE, never a URL with a real ID.
    assert "/health" in routes


def test_metrics_normalizes_unmatched_uuid_path_to_bounded_label() -> None:
    metrics.reset_for_tests()
    anon = TestClient(app, base_url="http://testserver")
    r = anon.get("/donors/11111111-1111-1111-1111-111111111111/not-a-real-subroute")
    assert r.status_code == 404

    snap = metrics.snapshot()
    routes = {row["route"] for row in snap["http_requests"]}
    # The UUID-looking segment is collapsed -- never present verbatim as a
    # distinct label (that would be unbounded cardinality over time).
    assert not any("11111111-1111-1111-1111-111111111111" in r for r in routes)
    assert any(":id" in r for r in routes)


def test_metrics_counts_401_and_403_as_auth_authz_failures() -> None:
    metrics.reset_for_tests()
    anon = TestClient(app, base_url="http://testserver")
    anon.headers["Origin"] = "http://localhost:5173"
    r = anon.get("/patients/me")
    assert r.status_code == 401

    patient, _ = auth_client("PATIENT")
    r2 = patient.get("/admin/users")
    assert r2.status_code == 403

    snap = metrics.snapshot()
    assert snap["counters"]["auth_failures_total"] >= 1
    assert snap["counters"]["authz_failures_total"] >= 1


def test_metrics_snapshot_never_contains_email_addresses_or_secrets() -> None:
    metrics.reset_for_tests()
    client, email = auth_client("PATIENT")
    client.get("/patients/me")
    snap = metrics.snapshot()
    text = str(snap)
    assert email not in text
    assert "password" not in text.lower()
    assert settings.secret_key not in text


def test_admin_metrics_endpoint_requires_admin(client: TestClient) -> None:
    anon = TestClient(app, base_url="http://testserver")
    anon.headers["Origin"] = "http://localhost:5173"
    assert anon.get("/admin/system/metrics").status_code == 401

    patient, _ = auth_client("PATIENT")
    assert patient.get("/admin/system/metrics").status_code == 403

    admin, _ = auth_client("ADMIN")
    r = admin.get("/admin/system/metrics")
    assert r.status_code == 200
    body = r.json()
    assert "http_requests" in body
    assert "counters" in body


# --------------------------------------------------------------------------- #
# RQ queue-health status thresholds -- explicit, configurable, not magic
# --------------------------------------------------------------------------- #
def test_rq_queue_status_healthy_when_shallow_and_no_failures() -> None:
    assert _queue_status(queued=0, failed=0, oldest_age=None, worker_count=1) == "healthy"
    assert _queue_status(queued=5, failed=0, oldest_age=10.0, worker_count=1) == "healthy"


def test_rq_queue_status_warning_on_any_failed_job() -> None:
    assert _queue_status(queued=0, failed=1, oldest_age=None, worker_count=1) == "warning"


def test_rq_queue_status_warning_at_configured_depth_threshold() -> None:
    threshold = settings.rq_queue_depth_warning_threshold
    above = _queue_status(queued=threshold + 1, failed=0, oldest_age=None, worker_count=1)
    below = _queue_status(queued=threshold - 1, failed=0, oldest_age=None, worker_count=1)
    assert above == "warning"
    assert below == "healthy"


def test_rq_queue_status_critical_at_configured_critical_depth() -> None:
    threshold = settings.rq_queue_depth_critical_threshold
    assert _queue_status(queued=threshold, failed=0, oldest_age=None, worker_count=1) == "critical"


def test_rq_queue_status_critical_when_jobs_queued_but_no_workers() -> None:
    """A queue with jobs and zero registered workers is the single most
    direct, actionable "nothing is draining this backlog" signal -- see
    docs/OPERATIONS_RUNBOOK.md's RQ worker outage entry."""
    assert _queue_status(queued=1, failed=0, oldest_age=None, worker_count=0) == "critical"
    # Empty queue with no workers is not itself a problem worth escalating.
    assert _queue_status(queued=0, failed=0, oldest_age=None, worker_count=0) == "healthy"


def test_rq_queue_status_warning_on_stale_oldest_job() -> None:
    stale_age = settings.rq_stale_queued_warning_seconds + 1
    assert _queue_status(queued=1, failed=0, oldest_age=stale_age, worker_count=1) == "warning"


def test_email_queue_health_includes_workers_and_status(monkeypatch) -> None:
    monkeypatch.setattr(settings, "rq_sync", False)
    status = email_queue_health()
    assert "workers" in status
    assert "status" in status
    assert status["status"] in ("healthy", "warning", "critical")


# --------------------------------------------------------------------------- #
# Stale-job prevention -- a REAL controlled demonstration, not just a TTL
# assertion (that already exists in test_phase29_rq_hardening.py). This
# enqueues a job with a deliberately tiny queue_ttl and confirms RQ itself
# actually discards it once that TTL elapses, reproducing (at a scale that
# fits in a test run) the exact mechanism that bounds Phase 28's
# 148,965-stale-job finding from ever recurring.
# --------------------------------------------------------------------------- #
def test_stale_queued_job_is_actually_discarded_after_its_ttl_elapses() -> None:
    """A REAL, controlled demonstration (not just a settings/TTL-value
    assertion -- that already exists in test_phase29_rq_hardening.py's
    `test_enqueued_email_job_has_bounded_ttls`) that RQ's own `ttl`
    mechanism genuinely discards a job that has waited past its bound,
    reproducing -- at a scale that fits in a test run -- the exact
    mechanism that prevents Phase 28's 148,965-stale-job finding from
    recurring. `ttl` is enforced when a WORKER dequeues the job (RQ skips
    an expired job rather than running it), not merely by reading queue
    counts, so this uses a real `rq.SimpleWorker` in burst mode -- the
    same real-worker pattern test_phase18_notifications.py already
    established -- instead of a mocked queue."""
    tiny_ttl_queue = Queue(settings.rq_queue_name, connection=rq_redis)
    job = tiny_ttl_queue.enqueue(
        "app.workers.tasks.send_email",
        "stale-demo@example.com",
        "subject",
        "<p>html</p>",
        ttl=1,  # seconds -- deliberately tiny for a fast, real test
    )
    assert tiny_ttl_queue.fetch_job(job.id) is not None
    assert job.id in tiny_ttl_queue.job_ids

    time.sleep(1.5)
    # Still physically present in the queue list until something actually
    # dequeues it and checks its ttl -- confirms this isn't a lazy-read
    # illusion, it genuinely requires the mechanism below.
    assert job.id in tiny_ttl_queue.job_ids

    SimpleWorker([tiny_ttl_queue], connection=rq_redis).work(burst=True)

    assert job.id not in tiny_ttl_queue.job_ids
    assert tiny_ttl_queue.fetch_job(job.id) is None


# --------------------------------------------------------------------------- #
# Admin health: version + db_pool + rq status fields
# --------------------------------------------------------------------------- #
def test_admin_system_health_includes_version_and_db_pool() -> None:
    admin, _ = auth_client("ADMIN")
    r = admin.get("/admin/system/health")
    assert r.status_code == 200
    body = r.json()
    assert body["version"]["app_version"]
    assert "git_commit_sha" in body["version"]
    assert set(body["db_pool"].keys()) == {"size", "checked_out", "overflow"}
    assert set(body["rq"].keys()) >= {"workers", "status"}


# --------------------------------------------------------------------------- #
# Readiness: new dependency_states field, additive (existing shape unchanged
# -- see tests/test_health.py for the pre-existing assertions this must not
# break).
# --------------------------------------------------------------------------- #
def test_readiness_reports_dependency_states_enum(client: TestClient) -> None:
    resp = client.get("/health/ready")
    assert resp.status_code == 200
    body = resp.json()
    states = body["dependency_states"]
    assert states["db"] == "AVAILABLE"
    assert states["redis"] == "AVAILABLE"
    # Real state, never "configuration exists" quietly relabeled as
    # "AVAILABLE" -- this sandbox's Neo4j is either unconfigured or
    # configured-but-unprobed-here, never claimed reachable by this cheap
    # endpoint (see health.py's own docstring for why it doesn't probe).
    assert states["neo4j"] in ("NOT_CONFIGURED", "CONFIGURED_NOT_PROBED")


# --------------------------------------------------------------------------- #
# Storage observability: failures counted/logged WITHOUT the object key
# --------------------------------------------------------------------------- #
def test_storage_put_failure_increments_metric_and_never_logs_the_key(monkeypatch, caplog) -> None:
    from app.core import storage

    metrics.reset_for_tests()
    secret_key = "verifications/some-user-id/some-req-id/passport_jane_doe.pdf"

    def _boom(*args, **kwargs):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(storage, "_local_path", _boom)
    with caplog.at_level(logging.WARNING, logger="lifelink.storage"):
        try:
            storage.put_bytes(secret_key, b"data")
        except OSError:
            pass

    assert metrics.snapshot()["counters"]["storage_failures_total"] >= 1
    for record in caplog.records:
        assert secret_key not in record.getMessage()
        assert "jane_doe" not in record.getMessage()


def test_storage_object_not_found_is_not_counted_as_a_failure() -> None:
    from app.core import storage

    metrics.reset_for_tests()
    try:
        storage.get_bytes("this/key/genuinely/does/not/exist.pdf")
    except storage.StorageObjectNotFoundError:
        pass
    assert metrics.snapshot()["counters"]["storage_failures_total"] == 0


# --------------------------------------------------------------------------- #
# Email observability: enqueue is logged as ENQUEUED (distinct from SENT),
# and the recipient address is masked in the log line, never printed raw.
# --------------------------------------------------------------------------- #
def test_email_enqueue_logs_masked_address_not_raw_and_increments_metric(
    monkeypatch, caplog
) -> None:
    from app import services

    monkeypatch.setattr(settings, "rq_sync", False)
    metrics.reset_for_tests()
    to = "jane.doe@example.com"
    with caplog.at_level(logging.INFO, logger="lifelink.email"):
        services.email_service.send_notification_email(to, "Jane", "Title", "Body")

    assert metrics.snapshot()["counters"]["email_enqueued_total"] >= 1
    enqueue_lines = [r.getMessage() for r in caplog.records if "enqueued" in r.getMessage()]
    assert enqueue_lines, "expected an ENQUEUED log line"
    for line in enqueue_lines:
        assert to not in line
        assert "j***@example.com" in line


def test_mask_email_keeps_domain_drops_local_part() -> None:
    from app.services.email_service import _mask_email

    assert _mask_email("jane.doe@example.com") == "j***@example.com"
    assert _mask_email("not-an-email") == "***"
