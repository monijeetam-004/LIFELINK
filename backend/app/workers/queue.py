from __future__ import annotations

import time
from typing import Literal, TypedDict

from rq import Queue, Worker

from app.core.config import settings
from app.core.redis import rq_redis


class QueueHealth(TypedDict):
    queued: int
    failed: int
    started: int
    oldest_queued_job_age_seconds: float | None
    workers: int
    status: Literal["healthy", "warning", "critical"]

email_queue = Queue(settings.rq_queue_name, connection=rq_redis)
# Kept as a separate logical queue from `email_queue` so an SMS backlog or
# an MSG91 outage can never delay email delivery. The same worker process
# listens to both (app/workers/worker.py) -- this isn't a second queueing
# system, just a second named queue on the existing Redis/RQ infrastructure.
sms_queue = Queue(settings.rq_sms_queue_name, connection=rq_redis)


def _queue_status(
    queued: int, failed: int, oldest_age: float | None, worker_count: int
) -> Literal["healthy", "warning", "critical"]:
    """Phase 30: explicit, documented thresholds (Settings.rq_queue_depth_*,
    Settings.rq_stale_queued_warning_seconds -- see their own docstrings
    for where each number comes from), not a bare unexplained magic value.

    `critical` fires on either a genuinely deep backlog OR jobs queued with
    zero workers running to drain them (a worker outage is itself the
    single most direct cause of a growing backlog -- see
    docs/OPERATIONS_RUNBOOK.md's "RQ worker outage" entry). `warning` fires
    on a shallower backlog, any failed job at all (an operator should at
    least look, even if it's one job), or a stale-but-not-yet-critical
    oldest-queued-job age.
    """
    if queued >= settings.rq_queue_depth_critical_threshold:
        return "critical"
    if queued > 0 and worker_count == 0:
        return "critical"
    if failed > 0:
        return "warning"
    if queued >= settings.rq_queue_depth_warning_threshold:
        return "warning"
    if oldest_age is not None and oldest_age >= settings.rq_stale_queued_warning_seconds:
        return "warning"
    return "healthy"


def email_queue_health() -> QueueHealth:
    """Phase 29/30: read-only queue/backlog visibility for the email queue
    -- see AdminRqHealthOut's own docstring for why this exists (Phase 28's
    148,965-stale-job finding). A handful of cheap Redis reads (LLEN + two
    registry counts + a worker-set lookup), no job payloads ever read.
    `oldest_queued_job_age_seconds` is best-effort: it reads only the head
    of the queue (the next job a worker would pick up), which is enough to
    answer "how long has the oldest waiting job been waiting" without an
    O(n) scan of the whole backlog. `workers` is the count of RQ worker
    processes currently registered against this Redis connection (any
    queue, not just this one -- app/workers/worker.py's single worker
    process listens to both the email and SMS queues), via RQ's own
    worker registry (a Redis set, not a scan of running processes).
    """
    queued = email_queue.count
    failed = len(email_queue.failed_job_registry)
    started = len(email_queue.started_job_registry)
    worker_count = len(Worker.all(connection=rq_redis))

    oldest_age: float | None = None
    job_ids = email_queue.job_ids[:1]
    if job_ids:
        job = email_queue.fetch_job(job_ids[0])
        if job is not None and job.enqueued_at is not None:
            enqueued_at = job.enqueued_at
            if enqueued_at.tzinfo is None:
                oldest_age = time.time() - enqueued_at.timestamp()
            else:
                import datetime as _dt

                oldest_age = (_dt.datetime.now(_dt.UTC) - enqueued_at).total_seconds()

    return {
        "queued": queued,
        "failed": failed,
        "started": started,
        "oldest_queued_job_age_seconds": oldest_age,
        "workers": worker_count,
        "status": _queue_status(queued, failed, oldest_age, worker_count),
    }
