"""Per-client rate limits for endpoints that spend external quota.

Fixed one-minute windows counted in Redis (shared by all workers). If Redis is
unreachable, counting falls back to this process for a minute before Redis is
tried again, so a Redis outage never blocks requests.
"""

import logging
import time
from collections.abc import Awaitable, Callable

import redis.asyncio as redis
from fastapi import HTTPException, Request

from app.config import settings

logger = logging.getLogger(__name__)

WINDOW_S = 60


class RateLimiter:
    def __init__(self, url: str | None = None) -> None:
        self._redis = redis.from_url(url or settings.redis_url, socket_connect_timeout=0.5)
        self._memory: dict[str, int] = {}
        self._redis_down_until = 0.0

    async def hit(self, key: str, limit: int) -> tuple[bool, int]:
        """Count one request; returns (allowed, seconds until the window resets)."""
        window = int(time.time() // WINDOW_S)
        retry_after = WINDOW_S - int(time.time() % WINDOW_S)
        bucket = f"{key}:{window}"
        count = None
        if time.monotonic() >= self._redis_down_until:
            try:
                async with self._redis.pipeline(transaction=True) as pipe:
                    count, _ = await pipe.incr(bucket).expire(bucket, WINDOW_S * 2).execute()
            except (redis.RedisError, OSError) as exc:
                self._redis_down_until = time.monotonic() + WINDOW_S
                logger.warning("Rate limiter: Redis unavailable, counting in-process: %s", exc)
        if count is None:
            self._memory = {k: v for k, v in self._memory.items() if k.endswith(f":{window}")}
            count = self._memory[bucket] = self._memory.get(bucket, 0) + 1
        return count <= limit, retry_after

    async def close(self) -> None:
        await self._redis.aclose()


limiter = RateLimiter()


def rate_limit(name: str, setting: str) -> Callable[[Request], Awaitable[None]]:
    """FastAPI dependency: at most `settings.<setting>` requests per minute per client IP."""

    async def check(request: Request) -> None:
        if not settings.rate_limit_enabled:
            return
        client = request.client.host if request.client else "unknown"
        allowed, retry_after = await limiter.hit(f"rl:{name}:{client}", getattr(settings, setting))
        if not allowed:
            raise HTTPException(
                429,
                f"Too many requests. Try again in {retry_after} s.",
                headers={"Retry-After": str(retry_after)},
            )

    return check
