from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    admin,
    auth,
    blood_requests,
    certificates,
    donation_review,
    donors,
    emergencies,
    events,
    family,
    forecasts,
    health,
    hospitals,
    matching,
    notifications,
    pathways,
    patients,
    shipments,
    shortlists,
    verifications,
)
from app.core.config import settings
from app.core.error_handlers import register_exception_handlers
from app.core.logging_config import configure_logging
from app.core.request_context import request_id_middleware
from app.core.request_metrics import request_metrics_middleware
from app.core.security_headers import security_headers_middleware

# Phase 28: configured at import time (not inside lifespan) so it applies
# uniformly to the dev `uvicorn app.main:app` command, the test suite, and
# the Phase 27 production entrypoint (app.asgi imports this same module) --
# every code path that imports app.main gets request-ID-correlated logging
# before the first request is ever handled.
configure_logging()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Create the object-storage bucket / local dir if it does not exist yet.
    from app.core import storage

    try:
        storage.ensure_bucket()
    except Exception as exc:  # storage optional at boot; routes surface real errors
        # QA round: this used to be a silent `pass`, so an object store that
        # was down or misconfigured at start-up left no trace in the log.
        # Start-up still continues (uploads return a clean 503 until the
        # store is back), but the operator now sees why.
        import logging

        logging.getLogger("lifelink.storage").warning(
            "object storage not ready at start-up (%s); uploads will fail until it is",
            type(exc).__name__,
        )
    # Hardening: say which mail transport this process will use (the API and
    # the worker log the same line, so a mismatch is visible). Never the
    # password -- see Settings.mail_summary.
    import logging

    logging.getLogger("lifelink.email").info("mail transport: %s", settings.mail_summary())
    yield

    # Close the Neo4j driver (if one was ever constructed) so shutdown
    # doesn't leak open sockets/threads. Safe to call even when the graph
    # engine was never configured/used.
    from app.core import graph

    graph.close_driver()


app = FastAPI(title="LifeLink API", version="0.1.0", lifespan=lifespan)

# Phase 28: registered first so it is the OUTERMOST middleware (Starlette
# runs user middleware in the order added) -- the request ID must be bound
# before every other middleware/handler runs so their log lines (including
# CORS rejections and the security-headers pass) are correlated too, and the
# echoed response header must survive every layer above the router.
app.middleware("http")(request_id_middleware)

# Phase 30: registered immediately after the request-ID middleware so its
# measured duration covers everything below it (security headers, CORS,
# routing, the handler itself) while still having a request ID available
# for the structured access-log line it emits -- see
# app/core/request_metrics.py's own docstring for exactly what is (and is
# not) logged/recorded.
app.middleware("http")(request_metrics_middleware)

# Phase 23: see security_headers_middleware's own docstring for what this
# does and does not add (notably: no CSP -- see that docstring).
app.middleware("http")(security_headers_middleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    # X-Request-Id is sent on every request by the frontend's API client
    # (app/lib/api.ts) for tracing; it must be explicitly allow-listed or the
    # browser's CORS preflight rejects it outright ("Disallowed CORS
    # headers"), which silently breaks every real authenticated request from
    # the actual browser even though it's invisible to tests that talk to
    # the API directly (TestClient/httpx don't enforce CORS).
    allow_headers=["Content-Type", "X-Requested-With", "Authorization", "X-Request-Id"],
)

# Phase 17: classify PostgreSQL/Redis connection-level failures as an honest
# 503 ("dependency unavailable") instead of letting them fall through to
# Starlette's generic 500 -- see app/core/error_handlers.py for the full
# rationale and exactly what is (and deliberately is not) covered.
register_exception_handlers(app)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(patients.router)
app.include_router(donors.router)
app.include_router(family.router)
app.include_router(blood_requests.router)
app.include_router(emergencies.router)
app.include_router(notifications.router)
app.include_router(hospitals.router)
app.include_router(matching.router)
app.include_router(certificates.router)
app.include_router(donation_review.router)
app.include_router(admin.router)
app.include_router(verifications.router)
app.include_router(shipments.router)
app.include_router(events.router)
app.include_router(pathways.router)
app.include_router(forecasts.router)
app.include_router(shortlists.router)


@app.get("/", tags=["meta"])
def root() -> dict[str, str]:
    return {"service": "lifelink-api", "version": "0.1.0", "status": "ok"}
