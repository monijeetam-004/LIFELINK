from __future__ import annotations

from fastapi import HTTPException, status

from app.core.redis import redis_client


class RateLimitExceeded(HTTPException):
    def __init__(self, retry_after: int) -> None:
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
            headers={"Retry-After": str(retry_after)},
        )
        self.code = "RATE_LIMITED"


def enforce_rate_limit(scope: str, key: str, limit: int, window_seconds: int) -> None:
    """Fixed-window counter. Raises 429 once ``limit`` is exceeded in the window."""
    redis_key = f"rl:{scope}:{key}"
    current = redis_client.incr(redis_key)
    if current == 1:
        redis_client.expire(redis_key, window_seconds)
    if current > limit:
        ttl = redis_client.ttl(redis_key)
        raise RateLimitExceeded(retry_after=ttl if ttl and ttl > 0 else window_seconds)


def reset_rate_limit(scope: str, key: str) -> None:
    redis_client.delete(f"rl:{scope}:{key}")
