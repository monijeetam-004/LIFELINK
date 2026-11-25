from __future__ import annotations

import redis

from app.core.config import settings

# Phase 28: both clients get an explicit connect/socket timeout -- without
# one, redis-py blocks indefinitely on a hung/unreachable Redis instead of
# raising the RedisError this app already classifies as an honest 503 (see
# app/core/error_handlers.py). Session validation and rate limiting (the
# two hard, no-in-memory-fallback Redis dependencies on the request path)
# must fail fast, not hang a request forever.
#
# `rq_redis` sharing this same socket_timeout with RQ's own blocking
# dequeue (BLPOP-style) is safe by construction, not by luck: RQ's own
# Worker.dequeue_job_and_maintain_ttl inspects the connection's configured
# socket_timeout and raises it itself if it's smaller than what RQ's own
# blocking wait needs (see rq/worker/base.py) -- it only ever widens a too-
# small value, never leaves a real blocking wait vulnerable to a spurious
# timeout. So this value is a safe floor for the plain request/response
# commands (session/rate-limit) it's actually meant to bound, without
# breaking RQ's longer blocking waits on the same client.
_REDIS_TIMEOUT_KWARGS = {
    "socket_connect_timeout": settings.redis_connect_timeout_seconds,
    "socket_timeout": settings.redis_socket_timeout_seconds,
}

# Text client: session store, rate-limit counters (decode_responses=True).
redis_client: redis.Redis = redis.Redis.from_url(
    settings.redis_url, decode_responses=True, **_REDIS_TIMEOUT_KWARGS
)

# Binary client: RQ requires bytes responses.
rq_redis: redis.Redis = redis.Redis.from_url(
    settings.redis_url, decode_responses=False, **_REDIS_TIMEOUT_KWARGS
)
