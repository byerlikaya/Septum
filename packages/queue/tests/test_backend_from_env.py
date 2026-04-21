"""``backend_from_env`` tests covering file/Redis dispatch + missing-env error."""

from __future__ import annotations

from pathlib import Path

import pytest

from septum_queue import FileQueueBackend, backend_from_env


def test_file_backend_when_queue_dir_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("SEPTUM_QUEUE_URL", raising=False)
    monkeypatch.setenv("SEPTUM_QUEUE_DIR", str(tmp_path))
    queue = backend_from_env("foo")
    assert isinstance(queue, FileQueueBackend)
    assert queue.topic == "foo"


def test_missing_env_raises_systemexit(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("SEPTUM_QUEUE_URL", raising=False)
    monkeypatch.delenv("SEPTUM_QUEUE_DIR", raising=False)
    with pytest.raises(SystemExit, match="SEPTUM_QUEUE_URL"):
        backend_from_env("foo")


def test_redis_url_takes_precedence_over_queue_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    pytest.importorskip("redis.asyncio")
    pytest.importorskip("fakeredis")

    import fakeredis.aioredis

    # Patch the low-level redis client so the test does not need a live server.
    # Scoped to RedisStreamsQueueBackend's own construction path rather than
    # mutating redis.asyncio.Redis globally.
    from septum_queue import RedisStreamsQueueBackend

    def _fake_from_url(cls, url, *, topic, **kwargs):
        return cls(fakeredis.aioredis.FakeRedis(), topic=topic)

    monkeypatch.setattr(
        RedisStreamsQueueBackend,
        "from_url",
        classmethod(_fake_from_url),
    )

    monkeypatch.setenv("SEPTUM_QUEUE_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("SEPTUM_QUEUE_DIR", str(tmp_path))

    queue = backend_from_env("foo")
    assert isinstance(queue, RedisStreamsQueueBackend)
    assert queue.topic == "foo"
