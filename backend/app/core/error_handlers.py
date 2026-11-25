"""Phase 17 -- global classification of infrastructure failures.

Before this phase, an unreachable PostgreSQL (`OperationalError`) or an
unreachable Redis (`RedisError`) had no dedicated handling anywhere in the
request path -- FastAPI/Starlette's default `ServerErrorMiddleware` caught
the unhandled exception and returned a generic 500 (safe -- no traceback or
secret ever reaches the client, since the app is not run with debug=True --
but *dishonest*: it is indistinguishable from a genuine application bug).

Part L of the Phase 17 spec explicitly requires distinguishing "500
unexpected server error" from "503 dependency unavailable". This module adds
exactly that distinction, for exactly the two infrastructure dependencies
whose failure mode is unambiguous (a connection/operational-level failure,
not a data/logic error):

  * `sqlalchemy.exc.OperationalError` -- connection refused, admin shutdown,
    statement timeout, server closed the connection, etc. Deliberately NOT
    the broader `DBAPIError`/`SQLAlchemyError` -- those also cover
    `IntegrityError` and other data-conflict exceptions that individual
    routes already classify correctly (400/404/409); reclassifying all of
    those as "dependency unavailable" would be wrong and would hide real
    bugs behind a misleading 503.
  * `redis.exceptions.RedisError` -- covers `ConnectionError`/`TimeoutError`/
    etc. Session validation (`app/services/session_service.py`, used by
    every authenticated request) and rate limiting
    (`app/core/ratelimit.py`, used by auth endpoints) both depend on Redis
    with no in-memory fallback -- deliberately, since a silent fallback to
    an insecure in-memory session/rate-limit store would be exactly the
    "unsafe in-memory implementation" the spec forbids. A Redis outage must
    therefore make the affected request fail loudly and honestly (503),
    never silently succeed as if the session/rate-limit check passed.

Both handlers:
  * never include the original exception's message, SQL text, connection
    string, or any driver-internal detail in the response body -- only a
    fixed, generic, honest sentence;
  * log the exception type and a short context note server-side (for
    diagnosis) without secrets;
  * return 503 with a `Retry-After` header, since this is by definition a
    transient-dependency condition, not a permanent one.

`app.core.graph.GraphUnavailableError` is deliberately NOT handled here: it
is already caught and translated at every real call site
(`graph_service.find_resource_pathways`'s try/except, the resync endpoint's
explicit 503) as designed in Phase 16, so it should never reach this
fallback in practice. It is intentionally left OUT of this module rather
than silently duplicated, so that a future regression which lets it escape
its intended handling surfaces as a clearly-different, unhandled-exception
500 (loud) rather than being quietly absorbed into "yet another 503" here.
"""
from __future__ import annotations

import logging

import redis
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

from app.core import metrics

logger = logging.getLogger("lifelink.errors")

_DB_UNAVAILABLE_DETAIL = "The database is temporarily unavailable. Please try again shortly."
_REDIS_UNAVAILABLE_DETAIL = (
    "A required service is temporarily unavailable. Please try again shortly."
)
_RETRY_AFTER_SECONDS = "5"


async def _operational_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # Starlette only ever dispatches here for an (subclass of) OperationalError
    # -- that's what it's registered for below -- so the narrowing is safe;
    # the param is typed as the broader `Exception` only to match Starlette's
    # own exception-handler signature exactly (it does its own isinstance
    # dispatch against the registered exception class, not against a
    # user-defined Callable signature).
    orig = getattr(exc, "orig", None)
    logger.error(
        "database operational error on %s %s: %s",
        request.method, request.url.path, type(orig).__name__ if orig else type(exc).__name__,
    )
    metrics.inc_db_failure()
    return JSONResponse(
        status_code=503,
        content={"detail": _DB_UNAVAILABLE_DETAIL, "code": "DATABASE_UNAVAILABLE"},
        headers={"Retry-After": _RETRY_AFTER_SECONDS},
    )


async def _redis_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "redis error on %s %s: %s", request.method, request.url.path, type(exc).__name__,
    )
    metrics.inc_redis_failure()
    return JSONResponse(
        status_code=503,
        content={"detail": _REDIS_UNAVAILABLE_DETAIL, "code": "DEPENDENCY_UNAVAILABLE"},
        headers={"Retry-After": _RETRY_AFTER_SECONDS},
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(OperationalError, _operational_error_handler)
    app.add_exception_handler(redis.exceptions.RedisError, _redis_error_handler)
