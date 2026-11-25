# BASE_IMAGE is overridable only so the image can be verified in an
# environment without registry access (see docs/final-verification/
# DOCKER_SETUP.md); production builds use the default.
ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/venv

# Hardening: install the exact, hash-verified versions from the lockfile
# (requirements.lock is generated from requirements.txt -- see
# docs/LIFELINK_DEPENDENCIES.md for the update procedure).
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes --no-deps -r requirements.lock

COPY . .

# Phase 28: run as a non-root user. The base image has no unprivileged user
# by default, so every process in this container (uvicorn, the RQ worker)
# previously ran as root with no reason to -- this app never binds a
# privileged (<1024) port itself in a container context (8000 is exposed,
# mapped externally by whatever runs the container) and needs no root
# capability at runtime. `chown` only what the app actually reads/writes
# (the source tree, for byte-compiled __pycache__ files) rather than
# widening this beyond what's needed.
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app \
    && mkdir -p /var/lib/lifelink-audit \
    && chown appuser:appuser /var/lib/lifelink-audit
USER appuser

EXPOSE 8000

# Liveness, not readiness (see app/api/routes/health.py's own docstring for
# why that distinction matters): this only proves the process can serve an
# HTTP request at all, matching what a container runtime's own health
# signal should gate a *restart* on. A real orchestrator's readiness gate
# (removing this instance from load-balancer rotation without restarting
# it) should poll /health/ready separately, over the network, not via this
# directive. No curl/wget in this slim image -- Python's stdlib is already
# present and sufficient, so nothing extra is installed just for this.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/health/live', timeout=2)" || exit 1

# Phase 27's production entrypoint (app.asgi:application -- see that
# module's own docstring), run directly here rather than via
# scripts/run_production.sh: that script's HOST/PORT/WEB_CONCURRENCY/
# LOG_LEVEL env-var defaults are for a bare-metal/VM single-box deployment
# outside a container, where EXPOSE/port-mapping and an orchestrator's own
# replica count don't already exist -- inside a container those same knobs
# are more naturally set via `docker run -e WEB_CONCURRENCY=... --entrypoint
# scripts/run_production.sh` (or the orchestrator's own equivalent) when a
# deployment actually wants to override them, without baking a second,
# slightly-different default set into the image's own CMD. This default CMD
# is single-process (no --workers flag = uvicorn's own default of 1), which
# is the right default for a container that an orchestrator scales by
# running more container replicas, not by adding uvicorn worker processes
# inside one container.
#
# docker-compose.yml (this repo's local dev/E2E compose file) overrides
# this CMD entirely with its own dev command (`--reload` against
# app.main:app, plus `alembic upgrade head` first) -- this default CMD is
# only what actually runs for a plain `docker run` or an orchestrator that
# doesn't override it, i.e. exactly the production path.
#
# QA round (2026-10-20): --timeout-graceful-shutdown 5, the same bound as
# scripts/run_production.sh. Without it, a SIGTERM'd container holding an
# open Server-Sent-Events stream kept "waiting for connections to close"
# (30 s measured), longer than Docker's default 10 s stop timeout, so the
# runtime SIGKILLed the process and the lifespan shutdown never ran. Clients
# reconnect to the replacement and replay what they missed (Last-Event-ID).
CMD ["uvicorn", "app.asgi:application", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--timeout-graceful-shutdown", "5"]
