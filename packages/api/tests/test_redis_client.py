"""Contract tests for the optional Redis client accessor."""

from __future__ import annotations

import dataclasses
import inspect

import pytest

from septum_api import bootstrap
from septum_api.services import redis_client


def test_get_redis_is_a_plain_function() -> None:
    """Callers use ``get_redis()`` without ``await``; it must not be a coroutine."""
    assert not inspect.iscoroutinefunction(redis_client.get_redis)


def test_get_redis_returns_none_without_a_configured_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setattr(
        bootstrap,
        "_cached_config",
        dataclasses.replace(bootstrap.get_config(), redis_url=""),
    )
    monkeypatch.setattr(redis_client, "_redis_pool", None)
    monkeypatch.setattr(redis_client, "_redis_unavailable", False)

    assert redis_client.redis_configured() is False
    assert redis_client.get_redis() is None


class _FakeClient:
    def __init__(self, store: dict[str, bytes] | None = None) -> None:
        self.store = store or {}

    async def get(self, key: str) -> bytes | None:
        return self.store.get(key)


def _configure(monkeypatch: pytest.MonkeyPatch, url: str = "redis://fake:6379/0") -> None:
    monkeypatch.setattr(
        bootstrap,
        "_cached_config",
        dataclasses.replace(bootstrap.get_config(), redis_url=url),
    )
    monkeypatch.setattr(redis_client, "_redis_pool", None)
    monkeypatch.setattr(redis_client, "_redis_unavailable", False)


def test_get_redis_creates_pool_once_and_caches_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import redis.asyncio as aioredis

    _configure(monkeypatch)
    calls: list[tuple[str, dict]] = []
    fake = _FakeClient()

    def fake_from_url(url: str, **kwargs):
        calls.append((url, kwargs))
        return fake

    monkeypatch.setattr(aioredis, "from_url", fake_from_url)

    assert redis_client.get_redis() is fake
    assert redis_client.get_redis() is fake
    assert calls == [("redis://fake:6379/0", {"decode_responses": False})]


def test_get_redis_marks_unavailable_when_pool_creation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import redis.asyncio as aioredis

    _configure(monkeypatch)
    attempts = 0

    def failing_from_url(url: str, **kwargs):
        nonlocal attempts
        attempts += 1
        raise ValueError("bad url")

    monkeypatch.setattr(aioredis, "from_url", failing_from_url)

    assert redis_client.get_redis() is None
    assert redis_client._redis_unavailable is True
    assert redis_client.get_redis() is None
    assert attempts == 1


def test_get_redis_short_circuits_when_marked_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import redis.asyncio as aioredis

    _configure(monkeypatch)
    monkeypatch.setattr(redis_client, "_redis_unavailable", True)

    def must_not_be_called(url: str, **kwargs):
        raise AssertionError("from_url must not be called")

    monkeypatch.setattr(aioredis, "from_url", must_not_be_called)

    assert redis_client.get_redis() is None


@pytest.mark.asyncio
async def test_redis_get_uses_client_without_awaiting_get_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    monkeypatch.setattr(redis_client, "_redis_pool", _FakeClient({"k": b"v"}))

    assert await redis_client.redis_get("k") == b"v"
    assert await redis_client.redis_get("missing") is None
