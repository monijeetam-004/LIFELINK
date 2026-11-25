from __future__ import annotations

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.core.redis import redis_client
from app.db.session import engine

router = APIRouter(tags=["health"])


def _check_db() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001 - report, don't crash the probe
        return False


def _check_redis() -> bool:
    try:
        return bool(redis_client.ping())
    except Exception:  # noqa: BLE001
        return False


@router.get("/health")
def health(response: Response) -> dict[str, object]:
    """Original combined health check, unchanged since Phase 17/23. Kept
    exactly as-is (same shape, same DB+Redis-required semantics) for
    backward compatibility with every existing caller and test
    (tests/test_health.py, tests/test_phase23_security_hardening.py) --
    see `/health/live` and `/health/ready` below for the Phase 28
    liveness/readiness split a real orchestrator should use instead.
    """
    db_ok = _check_db()
    redis_ok = _check_redis()

    ok = db_ok and redis_ok
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if ok else "degraded", "db": db_ok, "redis": redis_ok}


@router.get("/health/live")
def liveness() -> dict[str, object]:
    """Phase 28 -- liveness probe: "is the application process alive and
    able to handle an HTTP request at all?"

    Deliberately checks NOTHING beyond the process itself responding --
    no database, no Redis, no external dependency of any kind. This is the
    signal a container orchestrator (Kubernetes, ECS, ...) should use to
    decide whether to restart the container: a slow/unreachable database
    is not a reason to kill and restart a perfectly healthy process (that
    would compound an outage by also churning restarts), it's a reason to
    stop *routing traffic* to it -- which is exactly what `/health/ready`
    below is for. Always 200 if this handler runs at all.
    """
    return {"status": "alive"}


@router.get("/health/ready")
def readiness(response: Response) -> dict[str, object]:
    """Phase 28 -- readiness probe: "can this instance safely receive
    normal traffic right now?"

    Checks the two HARD dependencies this app has no in-memory fallback
    for -- PostgreSQL and Redis (session validation and rate limiting both
    depend on Redis with no safe fallback; see
    app/core/error_handlers.py's own docstring for why that's deliberate)
    -- and fails (503) if either is down, exactly like the original
    `/health`. Two lightweight queries (`SELECT 1`, `PING`); this does not
    add meaningful load even polled frequently by an orchestrator.

    Neo4j, object storage, and email are reported as informational status
    alongside the required checks, but never flip readiness to failed on
    their own: all three are architected to degrade gracefully rather than
    take the app down (haversine-distance fallback for the graph engine,
    honest 503 from the specific endpoint that needed storage, best-effort
    non-blocking email) -- see app/core/graph.py, app/core/storage.py,
    app/services/notification_service.py. Making readiness fail because an
    explicitly-optional dependency is unset or unreachable would be
    dishonest to the architecture and would needlessly pull a perfectly
    capable instance out of rotation. No live I/O is performed against
    storage/Neo4j here (that already exists, authenticated, at GET
    /admin/system/health) -- only cheap, non-blocking configuration state,
    so this stays safe to poll frequently.
    """
    db_ok = _check_db()
    redis_ok = _check_redis()
    required_ok = db_ok and redis_ok
    if not required_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    from app.core import graph as graph_core
    from app.core.config import settings
    from app.core.storage import storage_backend

    # Phase 30: a small, fixed vocabulary (Section 9 of the master prompt),
    # added as a NEW `dependency_states` field rather than changing the
    # shape of the existing `required`/`optional` booleans -- every prior
    # caller/test of this endpoint (tests/test_health.py) keeps working
    # unchanged. "Configuration exists" is never reported as "AVAILABLE"
    # for Neo4j here: it is NOT_CONFIGURED (no credentials set) or
    # CONFIGURED_NOT_PROBED (configured, but this cheap/frequent-poll
    # endpoint deliberately does not perform a live network check here --
    # that already exists at GET /admin/system/health, see that endpoint's
    # own docstring for why readiness intentionally stays non-probing for
    # optional dependencies).
    neo4j_state = "CONFIGURED_NOT_PROBED" if graph_core.graph_enabled() else "NOT_CONFIGURED"
    storage_state = "CONFIGURED_NOT_PROBED" if storage_backend() == "s3" else "AVAILABLE"
    email_state = "CONFIGURED_NOT_PROBED" if settings.mail_username else "NOT_CONFIGURED"

    # Audit E4 (2026-10-31): say plainly whether the audit trail has
    # tamper evidence beyond internal consistency. Never exposes the key.
    from app.services import audit_checkpoint_service

    try:
        checkpoints = audit_checkpoint_service.status()
    except Exception:  # pragma: no cover - unreadable checkpoint storage
        checkpoints = {"enabled": settings.audit_checkpoints_enabled,
                       "required": settings.audit_checkpoint_required,
                       "warning": "checkpoint log could not be read"}
    if settings.audit_checkpoint_required and checkpoints.get("warning"):
        # Fail closed: a deployment that requires tamper evidence is not
        # ready while its checkpoint log is unreadable or does not verify.
        required_ok = False
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "ready" if required_ok else "not_ready",
        "audit_checkpoints": checkpoints,
        "required": {"db": db_ok, "redis": redis_ok},
        "optional": {
            "neo4j": {"configured": graph_core.graph_enabled()},
            "storage": {"backend": storage_backend()},
            # `authenticated` (not "configured"/"enabled" like the other
            # two): email always has *some* host configured (mail_host
            # defaults to a local dev catcher, see .env.example) and always
            # works in that sense, so the only fact worth surfacing here is
            # whether it's set up with real SMTP credentials (a real Gmail
            # account, e.g.) versus the unauthenticated local-dev default.
            "email": {
                "authenticated": bool(settings.mail_username),
                "provider": settings.mail_provider,
            },
        },
        "dependency_states": {
            "db": "AVAILABLE" if db_ok else "UNAVAILABLE",
            "redis": "AVAILABLE" if redis_ok else "UNAVAILABLE",
            "neo4j": neo4j_state,
            "storage": storage_state,
            "email": email_state,
        },
    }
