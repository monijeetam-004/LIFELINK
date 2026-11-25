#!/usr/bin/env bash
# Phase 27 Part E -- production launcher, separate from the dev command in
# README.md (`uvicorn app.main:app --reload`). Points a real ASGI server at
# app.asgi:application (see that module's own docstring for the full
# rationale: this app is FastAPI/ASGI, not Flask/WSGI, and this file
# honors the "distinct production entrypoint" intent using the real stack
# rather than forcing a Flask-specific shape onto it).
#
# Every runtime knob below is env-driven with a documented default; nothing
# here hardcodes a secret, a host, or an environment assumption -- and
# app.core.config.Settings' own fail-closed validators (_validate_secret_key,
# _validate_cookie_security) separately refuse to boot at all in production
# with an insecure SECRET_KEY or cookie config, regardless of how this
# script is invoked.
#
# Usage:
#   ENVIRONMENT=production SECRET_KEY=... DATABASE_URL=... \
#     ./scripts/run_production.sh
#
# Optional overrides (all have safe defaults for a single-box deployment):
#   HOST            bind address                        (default: 0.0.0.0)
#   PORT            bind port                            (default: 8000)
#   WEB_CONCURRENCY uvicorn worker process count         (default: 1)
#   LOG_LEVEL       uvicorn log level                    (default: info)
#
# Graceful shutdown: uvicorn's own SIGTERM/SIGINT handling stops accepting
# new connections, drains in-flight requests, then runs app.main's lifespan
# shutdown phase (closes the Neo4j driver) before exiting -- nothing extra
# is added here. Send SIGTERM (`kill <pid>`, or whatever your process
# supervisor / container runtime sends on stop) rather than SIGKILL to get
# this behavior.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
WEB_CONCURRENCY="${WEB_CONCURRENCY:-1}"
LOG_LEVEL="${LOG_LEVEL:-info}"
# QA fix: open Server-Sent-Event streams kept a SIGTERM'd worker alive
# ("Waiting for connections to close") for ~20 s, so a deploy/restart left
# streams attached to a draining process. Bound it; clients reconnect to the
# new process and replay what they missed via Last-Event-ID.
GRACEFUL_SHUTDOWN_SECONDS="${GRACEFUL_SHUTDOWN_SECONDS:-5}"

exec python3 -m uvicorn app.asgi:application \
    --host "$HOST" \
    --port "$PORT" \
    --workers "$WEB_CONCURRENCY" \
    --log-level "$LOG_LEVEL" \
    --timeout-graceful-shutdown "$GRACEFUL_SHUTDOWN_SECONDS" \
    --proxy-headers
# Proxy headers (X-Forwarded-For/-Proto) are trusted ONLY from the addresses in
# FORWARDED_ALLOW_IPS (uvicorn reads it from the environment; default
# 127.0.0.1). Set it to your load balancer's address(es). Never "*" unless
# every request reaches this process through a proxy that overwrites the
# header: otherwise clients can spoof their IP (security audit S12).
