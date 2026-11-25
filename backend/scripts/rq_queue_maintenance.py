"""Phase 29 -- explicit, safe RQ queue maintenance and reporting.

Direct response to Phase 28's finding (repeated here, worse, on this
session's own Redis instance: 8,893 queued email jobs found accumulated
with `ttl == -1`, i.e. no expiry at all, before this phase's TTL fix in
app/services/email_service.py landed) that the RQ email queue had no bound
and could accumulate an arbitrarily large backlog.

This script does NOT run automatically as part of any deployment, CI job,
or startup path -- per the master prompt's explicit instruction ("Do not
blindly delete jobs as part of deployment. If cleanup is needed, make it
explicit and safe"), it is an operator-invoked tool only.

Usage:
    python -m scripts.rq_queue_maintenance --report
        Default mode (also what running with no flags does). Prints queue
        depth, failed-job count, started-job count, and the oldest queued
        job's age. Makes NO changes. Safe to run at any time, including
        against a live production queue.

    python -m scripts.rq_queue_maintenance --purge-failed --older-than-days N --confirm
        Removes jobs from FailedJobRegistry older than N days. Requires
        --confirm (a bare --purge-failed without it only prints what WOULD
        be removed -- a dry run). Never touches queued or started jobs --
        only jobs RQ has already given up on retrying (see
        Settings.email_job_max_retries), which by definition need a human
        or a re-enqueue, not automatic silent deletion of unseen failures.

    python -m scripts.rq_queue_maintenance --purge-stale-queued --older-than-days N --confirm
        Removes jobs still sitting UNSTARTED in the queue older than N
        days. Same --confirm/dry-run behavior. This is a manual,
        explicit version of what the new `ttl=` on enqueue (see
        Settings.email_job_queue_ttl_seconds) now does automatically going
        forward for NEW jobs -- this flag exists only to let an operator
        clear an EXISTING backlog that predates the TTL fix, on purpose,
        with the count reviewed first.

This script never reads or prints job payloads (email addresses, subjects,
HTML bodies) -- only counts, ages, and job IDs.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.workers.queue import QueueHealth, email_queue  # noqa: E402


def _report() -> QueueHealth:
    from app.workers.queue import email_queue_health

    status = email_queue_health()
    print(f"RQ email queue ({email_queue.name!r}) status: {status['status'].upper()}")
    print(f"  queued:  {status['queued']}")
    print(f"  started: {status['started']}")
    print(f"  failed:  {status['failed']}")
    print(f"  workers: {status['workers']}")
    age = status["oldest_queued_job_age_seconds"]
    if age is not None:
        print(f"  oldest queued job age: {age:,.0f}s ({age / 3600:,.1f}h)")
    else:
        print("  oldest queued job age: n/a (queue empty)")
    return status


def _purge_failed(older_than_days: float, confirm: bool) -> None:
    registry = email_queue.failed_job_registry
    cutoff = time.time() - older_than_days * 86400
    job_ids = registry.get_job_ids()
    to_remove = []
    for job_id in job_ids:
        job = email_queue.fetch_job(job_id)
        if job is None:
            continue
        enqueued_at = job.enqueued_at
        if enqueued_at is None:
            continue
        ts = enqueued_at.timestamp() if enqueued_at.tzinfo is None else enqueued_at.timestamp()
        if ts < cutoff:
            to_remove.append(job_id)

    print(f"FailedJobRegistry: {len(job_ids)} total, {len(to_remove)} older than "
          f"{older_than_days} day(s).")
    if not to_remove:
        return
    if not confirm:
        print("Dry run (no --confirm passed) -- nothing removed. "
              "Re-run with --confirm to actually delete these jobs.")
        return
    for job_id in to_remove:
        registry.remove(job_id, delete_job=True)
    print(f"Removed {len(to_remove)} failed job(s).")


def _purge_stale_queued(older_than_days: float, confirm: bool) -> None:
    cutoff = time.time() - older_than_days * 86400
    job_ids = email_queue.job_ids
    to_remove = []
    for job_id in job_ids:
        job = email_queue.fetch_job(job_id)
        if job is None or job.enqueued_at is None:
            continue
        ts = job.enqueued_at.timestamp()
        if ts < cutoff:
            to_remove.append(job_id)

    print(f"Queued: {len(job_ids)} total, {len(to_remove)} older than "
          f"{older_than_days} day(s).")
    if not to_remove:
        return
    if not confirm:
        print("Dry run (no --confirm passed) -- nothing removed. "
              "Re-run with --confirm to actually delete these jobs.")
        return
    for job_id in to_remove:
        job = email_queue.fetch_job(job_id)
        if job is not None:
            email_queue.remove(job)
            # Queue.remove only pops the job ID off the queue's Redis LIST
            # (the wait line); it deliberately does not also delete the
            # job's own Redis hash (RQ keeps that around in case another
            # registry still references it). This tool's whole point is
            # actually freeing the memory a stale backlog holds, so it
            # explicitly deletes the hash too.
            job.delete()
    print(f"Removed {len(to_remove)} stale queued job(s).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="store_true", help="Print queue stats (default).")
    parser.add_argument("--purge-failed", action="store_true")
    parser.add_argument("--purge-stale-queued", action="store_true")
    parser.add_argument("--older-than-days", type=float, default=7.0)
    parser.add_argument("--confirm", action="store_true", help="Actually delete (else dry-run).")
    args = parser.parse_args()

    if args.purge_failed:
        _purge_failed(args.older_than_days, args.confirm)
    elif args.purge_stale_queued:
        _purge_stale_queued(args.older_than_days, args.confirm)
    else:
        _report()


if __name__ == "__main__":
    main()
