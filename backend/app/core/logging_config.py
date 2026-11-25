"""Phase 28 -- structured logging with per-request correlation IDs.

Before this phase, this codebase's ~30+ existing ``logger.info/warning/
error(...)`` call sites (app/core/error_handlers.py, app/core/graph.py,
app/services/*, app/workers/*, ...) relied entirely on whatever root logging
config the process happened to start with (typically uvicorn's own bare
defaults) -- no consistent format, no timestamps guaranteed, and, critically,
no way to tell which log lines belonged to the same HTTP request. A user
reporting "it broke, here's the request ID my browser showed me"
(frontend/src/lib/api.ts already surfaces one) gave an operator nothing to
grep for.

``configure_logging()`` installs one root handler with one formatter that
includes the current request's correlation ID (via a ``logging.Filter`` that
reads ``app.core.request_context.get_request_id()``) in every record --
reaching every existing call site with zero changes to any of them, because
Python's logging propagates from every named logger up to root by default.

Explicitly NOT covered by this module (see Section 15 of the Phase 28
master prompt): this only changes *formatting*, never *what* gets logged.
No call site here is changed to dump request bodies, and a repository-wide
grep (re-run as part of this task) confirms no log call anywhere in app/
references a password/OTP/secret/token/credential value.
"""
from __future__ import annotations

import logging
import logging.config

from app.core.config import settings
from app.core.request_context import get_request_id


class RequestIdFilter(logging.Filter):
    """Injects the current request's correlation ID (or "-" outside a
    request context) into every LogRecord as ``record.request_id``, so a
    single formatter string can include it regardless of which logger --
    uvicorn's, sqlalchemy's, or this app's own -- emitted the record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        return True


_LOG_FORMAT = "%(asctime)s %(levelname)-8s [%(request_id)s] %(name)s: %(message)s"


def build_logging_config(level: str) -> dict:
    """Returns a ``logging.config.dictConfig``-compatible dict. Separated
    from ``configure_logging()`` so tests can assert on the config shape
    without mutating real global logging state."""
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "request_id": {"()": RequestIdFilter},
        },
        "formatters": {
            "default": {"format": _LOG_FORMAT},
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "default",
                "filters": ["request_id"],
            },
        },
        "root": {
            "handlers": ["console"],
            "level": level,
        },
        # uvicorn's own loggers install their own handlers/propagate=False
        # by default in some configurations; explicitly route them through
        # the same request-id-aware handler instead of leaving them on
        # uvicorn's bare pre-configured formatting.
        "loggers": {
            "uvicorn": {"handlers": ["console"], "level": level, "propagate": False},
            "uvicorn.error": {"handlers": ["console"], "level": level, "propagate": False},
            "uvicorn.access": {"handlers": ["console"], "level": level, "propagate": False},
        },
    }


_configured = False


def configure_logging() -> None:
    """Idempotent: safe to call multiple times (e.g. once at module import
    for `app.asgi`'s production entrypoint, and again defensively from
    `app.main`) without installing duplicate handlers."""
    global _configured
    if _configured:
        return
    logging.config.dictConfig(build_logging_config(settings.log_level))
    _configured = True
