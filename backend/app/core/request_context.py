"""Phase 28 -- per-request correlation ID.

The frontend's API client already attaches a fresh `X-Request-Id` to every
outgoing request and surfaces it in its own error UI ("so a report of 'it
broke' can be correlated with backend logs" -- see frontend/src/lib/api.ts's
own docstring) -- but until this phase, the backend never read that header
at all: it wasn't logged, wasn't threaded through the request, and wasn't
echoed back. A user reporting "it broke, here's the ID I saw" gave an
operator nothing to grep the backend logs for. This module closes that gap
with the smallest mechanism that reaches every existing log call site for
free: a single ContextVar, set once per request by the middleware below,
read by a logging.Filter (see app/core/logging_config.py) that every
handler already has installed -- so `logger.info(...)`,
`logger.error(...)`, etc. anywhere in this codebase (existing call sites
included, with zero changes to any of them) automatically get the current
request's ID in their formatted output.
"""
from __future__ import annotations

import re
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar

from starlette.requests import Request
from starlette.responses import Response

_REQUEST_ID_HEADER = "X-Request-Id"
# A client-supplied X-Request-Id is trusted only as a correlation label
# (never used for auth/lookup), but an unbounded or malformed value could
# still pollute log lines or -- if some future handler were less careful --
# be used to inject extra fields. Accept anything that looks like a
# reasonable opaque token (the frontend sends crypto.randomUUID(), so this
# is generous, not a UUID-only check that would reject other legitimate
# clients/tools); anything else is replaced with a freshly generated ID
# rather than rejected outright, since a malformed correlation header is
# not a reason to fail the request itself.
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


def get_request_id() -> str:
    """Current request's correlation ID, or "-" outside a request context
    (a background job, a startup/shutdown hook, a plain script)."""
    return request_id_var.get()


_ResponseCallable = Callable[[Request], Awaitable[Response]]


async def request_id_middleware(request: Request, call_next: _ResponseCallable) -> Response:
    """Binds a request ID for the duration of this request (reusing the
    client-supplied `X-Request-Id` when present and well-formed, else
    generating one) and echoes it back as a response header, so a client-
    side error report and a backend log line can be matched up either
    direction."""
    incoming = request.headers.get(_REQUEST_ID_HEADER)
    request_id = incoming if incoming and _VALID_REQUEST_ID.match(incoming) else str(uuid.uuid4())
    token = request_id_var.set(request_id)
    try:
        response = await call_next(request)
    finally:
        request_id_var.reset(token)
    response.headers[_REQUEST_ID_HEADER] = request_id
    return response
