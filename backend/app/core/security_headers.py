from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import Response

from app.core.config import settings

_ResponseCallable = Callable[[Request], Awaitable[Response]]


async def security_headers_middleware(request: Request, call_next: _ResponseCallable) -> Response:
    """Adds the small set of security response headers that are safe to set
    unconditionally on every response, with no risk of breaking an existing
    feature.

    Phase 23: this backend previously set none of these at all. Deliberately
    NOT included here: a Content-Security-Policy. The frontend SPA loads
    OpenStreetMap/Leaflet map tiles and marker imagery from external hosts
    and (per the artifact/asset conventions elsewhere in this codebase) may
    load fonts from Google Fonts -- a real CSP restrictive enough to matter
    would need to be built and tested against the actual set of external
    origins the frontend legitimately calls, which is a frontend-owned,
    asset-inventory exercise, not something to guess at from the backend in
    this pass. Shipping a wrong/guessed CSP is worse than shipping none: it
    would silently break the map/font UI Phase 22 just finished hardening,
    which is the one class of regression the Phase 23 master prompt
    explicitly forbids. See LIFELINK_PHASE_23_SECURITY_AUDIT.md's security
    headers section for the full reasoning and the documented follow-up.
    """
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    # This API is never meant to be framed by anything -- it's a JSON API,
    # not a page designed for embedding, so denying framing outright (rather
    # than the narrower SAMEORIGIN) has no legitimate use case to protect.
    response.headers.setdefault("X-Frame-Options", "DENY")
    # Cross-origin requests still get a real, useful referrer (the origin),
    # but never the full path/query of an internal API URL.
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    # This is a JSON API with no legitimate use of the browser features
    # Permissions-Policy gates -- an explicit, restrictive default-deny.
    response.headers.setdefault(
        "Permissions-Policy",
        "geolocation=(), camera=(), microphone=(), payment=(), usb=()",
    )
    # HSTS only makes sense -- and is only safe -- once the deployment is
    # actually serving over HTTPS with Secure cookies; sending it over plain
    # HTTP (this sandbox, most local dev) makes an unverifiable promise the
    # connection can't back up. Mirrors the existing is_production /
    # session_cookie_secure gating pattern in app/core/config.py.
    if settings.is_production and settings.session_cookie_secure:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=63072000; includeSubDomains"
        )
    return response
