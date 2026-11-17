"""Shared doubles for the optional Redis client."""

from __future__ import annotations

from typing import Any

import pytest


def reset_redis_client(monkeypatch: pytest.MonkeyPatch, client: Any = None) -> None:
    """Give the shared Redis client ``client`` as its pool (none: built on demand) and no failure history."""
    import septum_api.services.redis_client as redis_module

    monkeypatch.setattr(redis_module, "_redis_pool", client)
    monkeypatch.setattr(redis_module, "_redis_retry_at", None)
    monkeypatch.setattr(redis_module, "_consecutive_failures", 0)


def use_redis(monkeypatch: pytest.MonkeyPatch, url: str, client: Any = None) -> None:
    """Configure Redis at ``url``, reachable through ``client`` (none: a real pool on first use)."""
    import septum_api.services.redis_client as redis_module

    monkeypatch.setattr(redis_module, "_get_redis_url", lambda: url)
    reset_redis_client(monkeypatch, client)


def use_fake_redis(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Configure a reachable Redis backed by fakeredis and return its client."""
    import fakeredis

    client = fakeredis.FakeAsyncRedis()
    use_redis(monkeypatch, "redis://fake", client)
    return client


def redis_unavailable(
    monkeypatch: pytest.MonkeyPatch, url: str = "redis://unreachable"
) -> None:
    """A configured Redis inside its retry window: :func:`get_redis` gives ``None``, but it is configured."""
    import septum_api.services.redis_client as redis_module

    use_redis(monkeypatch, url)
    monkeypatch.setattr(redis_module, "_redis_retry_at", float("inf"))
