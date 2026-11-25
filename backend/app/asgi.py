"""Production ASGI entrypoint.

Phase 27 Part E note on terminology: the phase brief that drove this file
describes a "Flask application factory" / "WSGI" entrypoint. This codebase
is FastAPI on uvicorn -- an ASGI application, not WSGI, and it already uses
a factory-shaped pattern (``app.main`` builds and configures the single
``FastAPI()`` instance at import time; there is nothing a literal
"application factory function" would add here that isn't already done).
Rather than force a Flask-specific shape onto a different framework, this
file honors the *intent* behind that request -- a distinct, production-
oriented entrypoint, separate from the developer convenience command in
README.md, that is safe to point a real ASGI server at -- using this
codebase's actual stack.

What "production entrypoint" means here, concretely:

  * A single importable ASGI callable (``application``, the conventional
    name most ASGI servers' docs use, alongside ``app`` for uvicorn's own
    convenience) -- point any ASGI server at ``app.asgi:application``.
  * Zero hardcoded secrets or environment assumptions: this module does not
    read a single environment variable itself. Every runtime setting (host,
    port, worker count, log level, and of course SECRET_KEY, DATABASE_URL,
    REDIS_URL, cookie flags, ...) flows through ``app.core.config.Settings``
    exactly as it does for the dev server -- see that module's own
    fail-closed validators (``_validate_secret_key``, ``_validate_cookie_
    security``) for the settings that refuse to boot at all in production
    with an insecure value, rather than silently running unsafely.
  * Graceful shutdown: this reuses ``app.main``'s existing ``lifespan``
    context manager unchanged (it already closes the Neo4j driver on
    shutdown -- see app/main.py). A production ASGI server (uvicorn,
    hypercorn, or uvicorn-worker-based gunicorn) delivers SIGTERM to the
    process, which uvicorn/Starlette turn into an orderly "stop accepting
    new connections, let in-flight requests finish, then run the lifespan
    shutdown phase" sequence -- no extra signal handling is added or
    needed here.
  * The dev server (``uvicorn app.main:app --reload``, documented in
    README.md) is completely untouched by this file -- it imports the same
    underlying ``app`` object, so there is exactly one FastAPI application
    definition, never two to keep in sync; this module only adds the
    production-safe way to *run* it.

How to run this in production (see scripts/run_production.sh for a
runnable wrapper that reads WEB_CONCURRENCY/HOST/PORT/LOG_LEVEL from the
environment with the same defaults documented there):

    uvicorn app.asgi:application \\
        --host 0.0.0.0 --port 8000 \\
        --workers "$WEB_CONCURRENCY" \\
        --log-level "$LOG_LEVEL" \\
        --no-reload \\
        --proxy-headers     # trusted proxies: FORWARDED_ALLOW_IPS env (default 127.0.0.1)

``--workers`` here is uvicorn's own multi-process manager (no gunicorn
dependency needed); Phase 27's own load-test evidence (see
LIFELINK_PHASE_27_ACCESSIBILITY_SCALABILITY_REPORT.md's Scalability
Investigation section) found this deployment's real bottleneck under load
was a connection-pool-exhausting N+1 query bug, not CPU/worker-process
capacity (a live py-spy/top profile during a reproduced outage showed 0%
CPU and every busy thread blocked on the DB pool checkout, not doing
CPU-bound work) -- so a higher ``--workers`` count is offered here as a
standard, low-risk production capability (more OS-level fault isolation,
one slow worker doesn't wedge every request), not asserted to be a
performance fix; the actual fix that phase measured and shipped is in
``app.services.notification_service.notify_matching_donors``.

Note on multi-worker deployments and this app's pool: each worker process
gets its OWN copy of the SQLAlchemy engine (and its own connection pool)
from ``app.db.session`` on import, since that module is re-imported fresh
in each forked/spawned worker process -- so ``pool_size`` there is a
*per-worker* ceiling, and total Postgres connections scale with
``--workers`` * pool_size. Operators raising --workers in front of a
connection-limited Postgres instance should size ``max_connections``
(and/or front it with a pooler such as PgBouncer) accordingly; this is
called out explicitly rather than left as a surprise.
"""
from __future__ import annotations

from app.main import app as application

# Also exported as `app` -- uvicorn's own `--factory`-less default target
# name, and what every other reference to this application in this
# codebase (tests, the dev command) already calls it. `application` above
# is the conventional name most ASGI server docs (and the phase brief's
# WSGI-derived language) expect; both names point at the exact same
# object, so there is still only one FastAPI app in this process.
app = application
