"""Optional Redis client for Septum.

Provides a shared async Redis connection pool. When REDIS_URL is not set,
all operations gracefully return None or silently skip writes, so the
rest of the application operates without Redis.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

_redis_pool: Optional[object] = None
_redis_unavailable = False


def _get_redis_url() -> str | None:
    from ..bootstrap import get_config
    url = get_config().redis_url
    return url or None


async def get_redis():
    """Return an async Redis client, or None if REDIS_URL is not set."""
    global _redis_pool, _redis_unavailable

    if _redis_unavailable:
        return None

    url = _get_redis_url()
    if not url:
        return None

    if _redis_pool is not None:
        return _redis_pool

    try:
        import redis.asyncio as aioredis

        _redis_pool = aioredis.from_url(url, decode_responses=False)
        return _redis_pool
    except Exception:
        logger.warning("Failed to create Redis connection pool", exc_info=True)
        _redis_unavailable = True
        return None


async def redis_get(key: str) -> bytes | None:
    """Get a value from Redis, returning None on any failure."""
    client = await get_redis()
    if client is None:
        return None
    try:
        return await client.get(key)
    except Exception:
        logger.warning("Redis GET failed for key=%s", key, exc_info=True)
        return None


async def redis_set(key: str, value: bytes, ttl: int | None = None) -> bool:
    """Set a value in Redis, returning False on any failure."""
    client = await get_redis()
    if client is None:
        return False
    try:
        if ttl:
            await client.setex(key, ttl, value)
        else:
            await client.set(key, value)
        return True
    except Exception:
        logger.warning("Redis SET failed for key=%s", key, exc_info=True)
        return False


async def redis_delete(key: str) -> bool:
    """Delete a key from Redis, returning False on any failure."""
    client = await get_redis()
    if client is None:
        return False
    try:
        await client.delete(key)
        return True
    except Exception:
        logger.warning("Redis DELETE failed for key=%s", key, exc_info=True)
        return False


async def redis_ping() -> bool:
    """Check if Redis is reachable."""
    client = await get_redis()
    if client is None:
        return False
    try:
        return await client.ping()
    except Exception:
        return False
