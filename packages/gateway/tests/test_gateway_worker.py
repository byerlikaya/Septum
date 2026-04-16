"""Gateway worker queue-backend selection tests."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("septum_queue")

from septum_queue import FileQueueBackend  # noqa: E402

from septum_gateway.worker import _build_queue  # noqa: E402


def test_build_queue_prefers_file_dir_when_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("SEPTUM_QUEUE_URL", raising=False)
    monkeypatch.setenv("SEPTUM_QUEUE_DIR", str(tmp_path))
    queue = _build_queue("foo")
    assert isinstance(queue, FileQueueBackend)
    assert queue.topic == "foo"


def test_build_queue_missing_env_raises_systemexit(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("SEPTUM_QUEUE_URL", raising=False)
    monkeypatch.delenv("SEPTUM_QUEUE_DIR", raising=False)
    with pytest.raises(SystemExit, match="SEPTUM_QUEUE_URL"):
        _build_queue("foo")


def test_build_queue_redis_url_takes_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    pytest.importorskip("redis.asyncio")
    pytest.importorskip("fakeredis")
    import fakeredis.aioredis

    monkeypatch.setenv("SEPTUM_QUEUE_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("SEPTUM_QUEUE_DIR", str(tmp_path))

    # Patch redis.asyncio.Redis.from_url so we don't need a live server.
    import redis.asyncio as redis_asyncio

    monkeypatch.setattr(
        redis_asyncio.Redis, "from_url", lambda *a, **k: fakeredis.aioredis.FakeRedis()
    )

    from septum_queue import RedisStreamsQueueBackend

    queue = _build_queue("foo")
    assert isinstance(queue, RedisStreamsQueueBackend)
    assert queue.topic == "foo"
