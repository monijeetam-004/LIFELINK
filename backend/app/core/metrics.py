"""Phase 30 -- lightweight, in-process operational metrics.

This is deliberately NOT a monitoring platform: no Prometheus client, no
external exporter, no push gateway, no new service to run or credential to
manage. It is a small, thread-safe, in-memory counter/gauge registry, read
by the admin-only ``GET /admin/system/metrics`` endpoint
(app/api/routes/admin.py) as a JSON snapshot. Per the Phase 30 master
prompt's own instruction ("do NOT introduce a huge monitoring platform
merely for appearance"), this is sized to answer the specific questions
Section 0 of that prompt lists (is the app healthy, are requests failing,
are auth failures increasing, ...) and nothing more.

Design constraints, all deliberate:

  * Counters only increment; gauges are read on demand (RQ queue depth is
    read live from Redis by app/workers/queue.py -- it is not duplicated
    here as a stale in-memory copy).
  * Every label set is a small, FIXED, enumerated vocabulary (HTTP method,
    a bounded route template, a 2xx/4xx/5xx status class, a named
    dependency/operation). Nothing here is ever labeled with a user ID, an
    email address, a request ID, or a raw URL containing an ID -- the
    Phase 30 master prompt explicitly forbids high-cardinality labels, and
    an unbounded label set is also how an in-memory metrics dict grows
    without bound over a long-running process's lifetime.
  * Process-local, in-memory, reset on restart. This is the same honest
    limitation every other Phase 28/29 "local" check has: it answers "is
    this instance healthy right now / since it started", not "what
    happened across every instance over the last 30 days" -- a real
    metrics backend (Prometheus, CloudWatch, ...) is what a production
    deployment would scrape this snapshot into, and Section 24 of the
    Phase 30 master prompt is explicit that whether one is actually
    deployed here is NOT-LIVE-VERIFIED, not invented.
  * Thread-safe: uuvicorn/anyio runs synchronous route handlers on a
    worker threadpool (confirmed by Phase 24's own investigation), so
    concurrent requests genuinely call these functions from different
    threads at once. A single `threading.Lock` guards every mutation; at
    the request rates this app has ever been measured at (Phase 24/27 load
    tests, low hundreds of req/s peak) this is not a measured contention
    point, and this module does nothing per-call more expensive than a
    dict increment under the lock.
"""
from __future__ import annotations

import threading
from typing import Literal

StatusClass = Literal["2xx", "3xx", "4xx", "5xx", "other"]

_lock = threading.Lock()

# {(method, route, status_class): count}
_http_requests: dict[tuple[str, str, str], int] = {}
# {route: (count, total_seconds, max_seconds)}
_http_latency: dict[str, tuple[int, float, float]] = {}

_counters: dict[str, int] = {
    "auth_failures_total": 0,
    "authz_failures_total": 0,
    "db_failures_total": 0,
    "redis_failures_total": 0,
    "neo4j_failures_total": 0,
    "email_enqueued_total": 0,
    "email_sent_total": 0,
    "email_failed_total": 0,
    "storage_failures_total": 0,
}

# Bounded: only these exact operation names are ever used as a label --
# never a storage key/path (see app/core/storage.py's own call sites).
_STORAGE_OPS = ("put", "get", "delete", "ensure_bucket", "presign")
_storage_failures_by_op: dict[str, int] = dict.fromkeys(_STORAGE_OPS, 0)

_process_start_monotonic = None


def status_class(status_code: int) -> StatusClass:
    if 200 <= status_code < 300:
        return "2xx"
    if 300 <= status_code < 400:
        return "3xx"
    if 400 <= status_code < 500:
        return "4xx"
    if 500 <= status_code < 600:
        return "5xx"
    return "other"


def record_http_request(method: str, route: str, status_code: int, duration_seconds: float) -> None:
    cls = status_class(status_code)
    with _lock:
        key = (method, route, cls)
        _http_requests[key] = _http_requests.get(key, 0) + 1
        if status_code == 401:
            _counters["auth_failures_total"] += 1
        elif status_code == 403:
            _counters["authz_failures_total"] += 1
        count, total, mx = _http_latency.get(route, (0, 0.0, 0.0))
        _http_latency[route] = (
            count + 1,
            total + duration_seconds,
            max(mx, duration_seconds),
        )


def _inc(name: str, amount: int = 1) -> None:
    with _lock:
        _counters[name] = _counters.get(name, 0) + amount


def inc_db_failure() -> None:
    _inc("db_failures_total")


def inc_redis_failure() -> None:
    _inc("redis_failures_total")


def inc_neo4j_failure() -> None:
    _inc("neo4j_failures_total")


def inc_email_enqueued() -> None:
    _inc("email_enqueued_total")


def inc_email_sent() -> None:
    _inc("email_sent_total")


def inc_email_failed() -> None:
    _inc("email_failed_total")


def inc_storage_failure(operation: str) -> None:
    # Fall back to "unknown" rather than accepting an arbitrary string as a
    # label -- keeps the label set bounded even if a future call site
    # passes something unexpected.
    op = operation if operation in _STORAGE_OPS else "unknown"
    with _lock:
        _counters["storage_failures_total"] += 1
        _storage_failures_by_op[op] = _storage_failures_by_op.get(op, 0) + 1


def snapshot() -> dict[str, object]:
    """A full, JSON-serializable point-in-time view, for the admin metrics
    endpoint. Never includes anything beyond the bounded counters/labels
    defined in this module -- no request IDs, no user data."""
    with _lock:
        http_requests = [
            {"method": m, "route": r, "status_class": c, "count": n}
            for (m, r, c), n in sorted(_http_requests.items())
        ]
        http_latency = [
            {
                "route": route,
                "count": count,
                "avg_ms": round((total / count) * 1000, 2) if count else 0.0,
                "max_ms": round(mx * 1000, 2),
            }
            for route, (count, total, mx) in sorted(_http_latency.items())
        ]
        counters = dict(_counters)
        storage_by_op = dict(_storage_failures_by_op)
    return {
        "http_requests": http_requests,
        "http_latency": http_latency,
        "counters": counters,
        "storage_failures_by_operation": storage_by_op,
    }


def reset_for_tests() -> None:
    """Test-only: clears all in-memory state so a test can assert on an
    exact delta rather than a cumulative process-lifetime total."""
    with _lock:
        _http_requests.clear()
        _http_latency.clear()
        for k in _counters:
            _counters[k] = 0
        for k in _storage_failures_by_op:
            _storage_failures_by_op[k] = 0
