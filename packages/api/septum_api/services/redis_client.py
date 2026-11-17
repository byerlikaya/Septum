"""Optional Redis client for Septum.

Provides a shared async Redis connection pool. When REDIS_URL is not set,
all operations gracefully return None or silently skip writes, so the
rest of the application operates without Redis.
"""

from __future__ import annotations

import logging
import time
from typing import Awaitable, Optional, TypeVar

from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

T = TypeVar("T")

logger = logging.getLogger(__name__)

# Bounds on one Redis call, so a stalled or unreachable server fails the
# call instead of hanging a request, a map lock holder or an erasure.
SOCKET_CONNECT_TIMEOUT_SECONDS = 5.0
SOCKET_TIMEOUT_SECONDS = 5.0
# After the client could not be created, or two calls in a row could not
# reach the server, callers get ``None`` for this long, so a down server
# costs one timeout per window instead of one per call.
RETRY_AFTER_SECONDS = 30.0
RETRY_AFTER_HEADERS = {"Retry-After": str(int(RETRY_AFTER_SECONDS))}
FAILURES_BEFORE_RETRY_WINDOW = 2

_redis_pool: Optional[object] = None
# ``time.monotonic()`` before which Redis is not tried again; ``None`` while
# nothing has failed.
_redis_retry_at: Optional[float] = None
# Calls in a row that could not reach the server; any answered call resets it.
_consecutive_failures = 0


def _unreachable(exc: BaseException) -> bool:
    """Whether ``exc`` means the server could not be reached, as opposed to a refused command."""
    return isinstance(exc, (OSError, TimeoutError, RedisConnectionError, RedisTimeoutError))


def _note_failure(exc: BaseException) -> None:
    """Count a call that could not reach Redis; the second in a row starts the retry window.

    Other errors (a refused command) are not counted. During the window
    :func:`get_redis` returns ``None`` while :func:`redis_configured` stays
    true.
    """
    global _redis_retry_at, _consecutive_failures

    if not _unreachable(exc):
        return
    _consecutive_failures += 1
    if _consecutive_failures >= FAILURES_BEFORE_RETRY_WINDOW:
        _redis_retry_at = time.monotonic() + RETRY_AFTER_SECONDS


def _note_success() -> None:
    """Reset the count of failed calls after Redis answered one."""
    global _consecutive_failures

    _consecutive_failures = 0


async def _tracked(awaitable: Awaitable[T]) -> T:
    """Await one Redis call and record whether the server answered; its error propagates."""
    try:
        result = await awaitable
    except Exception as exc:
        _note_failure(exc)
        raise
    _note_success()
    return result


def _get_redis_url() -> str | None:
    from ..bootstrap import get_config
    url = get_config().redis_url
    return url or None


def redis_configured() -> bool:
    """Return True when a Redis URL is configured, reachable or not.

    Callers that must not treat an unreachable Redis as "no Redis" (map
    erasure, cross-worker map versioning) check this next to
    :func:`get_redis`.
    """
    return _get_redis_url() is not None


def get_redis():
    """Return an async Redis client, or None if REDIS_URL is not set.

    Also ``None`` for :data:`RETRY_AFTER_SECONDS` after creating the client
    failed or calls found the server unreachable (:func:`_note_failure`);
    the next call after that tries again. :func:`redis_configured` stays
    true meanwhile, so callers that must not mistake an unavailable Redis
    for none (map erasure, map writes, the map lock) keep failing closed
    until it is back.
    """
    global _redis_pool, _redis_retry_at

    if _redis_retry_at is not None and time.monotonic() < _redis_retry_at:
        return None

    url = _get_redis_url()
    if not url:
        return None

    if _redis_pool is not None:
        return _redis_pool

    try:
        import redis.asyncio as aioredis

        _redis_pool = aioredis.from_url(
            url,
            decode_responses=False,
            socket_connect_timeout=SOCKET_CONNECT_TIMEOUT_SECONDS,
            socket_timeout=SOCKET_TIMEOUT_SECONDS,
        )
        _redis_retry_at = None
        return _redis_pool
    except Exception as exc:
        logger.warning("Failed to create Redis connection pool: %s", type(exc).__name__)
        _redis_retry_at = time.monotonic() + RETRY_AFTER_SECONDS
        return None


async def redis_get(key: str) -> bytes | None:
    """Get a value from Redis, returning None on any failure."""
    client = get_redis()
    if client is None:
        return None
    try:
        return await _tracked(client.get(key))
    except Exception as exc:
        logger.warning("Redis GET failed for key=%s: %s", key, type(exc).__name__)
        return None


async def redis_set(key: str, value: bytes, ttl: int | None = None) -> bool:
    """Set a value in Redis, returning False on any failure."""
    client = get_redis()
    if client is None:
        return False
    try:
        await _tracked(client.set(key, value, ex=ttl or None))
    except Exception as exc:
        logger.warning("Redis SET failed for key=%s: %s", key, type(exc).__name__)
        return False
    return True


async def redis_set_if_absent(key: str, value: bytes, ttl: int | None = None) -> bool:
    """Set ``key`` only if it does not exist (expiring after ``ttl`` seconds if given); False if not set or on failure."""
    client = get_redis()
    if client is None:
        return False
    try:
        return bool(await _tracked(client.set(key, value, nx=True, ex=ttl or None)))
    except Exception as exc:
        logger.warning("Redis SET NX failed for key=%s: %s", key, type(exc).__name__)
        return False


async def redis_delete(*keys: str) -> bool:
    """Delete ``keys`` from Redis in one DEL command; False unless the command landed."""
    client = get_redis()
    if client is None:
        return False
    try:
        await _tracked(client.delete(*keys))
    except Exception as exc:
        logger.warning("Redis DELETE failed for key=%s: %s", " ".join(keys), type(exc).__name__)
        return False
    return True


async def redis_ping() -> bool:
    """Check if Redis is reachable."""
    client = get_redis()
    if client is None:
        return False
    try:
        return await _tracked(client.ping())
    except Exception:
        return False
