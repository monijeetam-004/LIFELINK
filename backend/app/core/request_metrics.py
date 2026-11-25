"""Phase 30 -- per-request structured access logging + metrics recording.

Separate from app/core/logging_config.py (which only adds the request-ID
field to every existing log call's *formatting*) and from
app/core/request_context.py (which only *binds* the request ID). This
module is the one place that actually emits one structured operational log
line per request and feeds app/core/metrics.py -- the "application logs"
and "operational metrics" layers called out as distinct in the Phase 30
master prompt's own architecture section (Part 1), kept separate from
audit events (app/services/audit_service.py, which records business/
security decisions to the database, not to this logger).

What is logged, and why it's safe:
  * timestamp/level/logger/request_id -- via the existing formatter
    (app/core/logging_config.py); nothing new to configure.
  * method, route (a bounded PATH TEMPLATE, e.g. "/blood-requests/{id}",
    never the literal URL with a real ID/email/token in it -- see
    `_route_label` below), status code, duration_ms.
  * Nothing else. No query string, no request body, no headers, no
    cookies, no email/phone/name/donor/patient/hospital data of any kind.
"""
from __future__ import annotations

import logging
import re
import time
from collections.abc import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import Response

from app.core import metrics

logger = logging.getLogger("lifelink.access")

_ResponseCallable = Callable[[Request], Awaitable[Response]]

# Used only when a request never matched a real route (404s, and a handful
# of edge cases before routing runs) -- collapses UUID/ULID/numeric path
# segments so an attacker probing `/donors/<guess>` or a genuine 404 storm
# can never turn into an unbounded number of distinct metric/log labels.
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_NUMERIC_RE = re.compile(r"^\d+$")


def _normalize_unmatched_path(path: str) -> str:
    segments = [s for s in path.split("/") if s]
    normalized = [
        ":id" if _UUID_RE.match(s) or _NUMERIC_RE.match(s) else s for s in segments
    ]
    return "/" + "/".join(normalized) if normalized else "/"


def _route_label(request: Request) -> str:
    """A bounded route template for this request: the matched Starlette
    route's own path pattern (e.g. "/hospitals/{hospital_id}/inventory")
    when routing succeeded, or a normalized form of the raw path
    (real IDs collapsed to ":id") when it didn't -- e.g. an unmatched
    404. Either way this is safe as both a log field and a metric label:
    it is drawn from a small, fixed set of route definitions plus ":id"
    placeholders, never an arbitrary user-supplied string."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str) and path:
        return path
    return _normalize_unmatched_path(request.url.path)


async def request_metrics_middleware(request: Request, call_next: _ResponseCallable) -> Response:
    start = time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        # An unhandled exception below us (anything not already turned into
        # a Response by a registered exception handler -- see
        # app/core/error_handlers.py, which handles the two infrastructure
        # cases explicitly and lets FastAPI's own default handler produce
        # the generic 500 for everything else) still deserves one access
        # log line with a real duration and status, for the same reason
        # Section 17 of the Phase 30 master prompt asks what an operator
        # needs to investigate an incident -- a request that 500'd with no
        # log line at all is worse than one logged at "500".
        duration = time.monotonic() - start
        route = _route_label(request)
        metrics.record_http_request(request.method, route, 500, duration)
        logger.error(
            "%s %s -> 500 (%.1fms) [unhandled]",
            request.method, route, duration * 1000,
        )
        raise

    duration = time.monotonic() - start
    route = _route_label(request)
    metrics.record_http_request(request.method, route, response.status_code, duration)
    level = logging.WARNING if response.status_code >= 500 else logging.INFO
    logger.log(
        level,
        "%s %s -> %d (%.1fms)",
        request.method, route, response.status_code, duration * 1000,
    )
    return response
