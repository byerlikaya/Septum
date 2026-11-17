"""Contract tests for the optional Redis client accessor."""

from __future__ import annotations

import dataclasses
import inspect

import pytest
from redis_doubles import reset_redis_client

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
    reset_redis_client(monkeypatch)

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
    reset_redis_client(monkeypatch)


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
    assert calls == [
        (
            "redis://fake:6379/0",
            {
                "decode_responses": False,
                "socket_connect_timeout": redis_client.SOCKET_CONNECT_TIMEOUT_SECONDS,
                "socket_timeout": redis_client.SOCKET_TIMEOUT_SECONDS,
            },
        )
    ]


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
    assert redis_client._redis_retry_at is not None
    assert redis_client.get_redis() is None
    assert attempts == 1


def test_get_redis_tries_again_once_the_backoff_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import redis.asyncio as aioredis

    _configure(monkeypatch)
    now = [1000.0]
    monkeypatch.setattr(redis_client.time, "monotonic", lambda: now[0])
    fake = _FakeClient()
    outcomes: list = [ValueError("down"), fake]

    def flaky_from_url(url: str, **kwargs):
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(aioredis, "from_url", flaky_from_url)

    assert redis_client.get_redis() is None
    now[0] += redis_client.RETRY_AFTER_SECONDS - 1
    assert redis_client.get_redis() is None
    assert redis_client.redis_configured() is True
    now[0] += 1
    assert redis_client.get_redis() is fake
    assert redis_client._redis_retry_at is None
    assert outcomes == []


def test_get_redis_short_circuits_when_marked_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import redis.asyncio as aioredis

    _configure(monkeypatch)
    monkeypatch.setattr(redis_client, "_redis_retry_at", float("inf"))

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


@pytest.mark.asyncio
async def test_an_unreachable_redis_is_not_tried_again_within_the_window(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Two refused connections in a row start the window; erasure, map writes and the map lock then fail at once."""
    import socket

    from septum_api.services import document_anon_store
    from septum_api.services.anonymization_map import AnonymizationMap

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    _configure(monkeypatch, f"redis://127.0.0.1:{port}/0")
    monkeypatch.setattr(document_anon_store, "_ANON_MAP_DIR", tmp_path / "maps")

    assert await redis_client.redis_get("k") is None
    assert redis_client._redis_retry_at is None
    assert await redis_client.redis_get("k") is None
    assert redis_client._redis_retry_at is not None
    assert redis_client.get_redis() is None
    assert redis_client.redis_configured() is True

    with pytest.raises(document_anon_store.DocumentMapErasureError):
        await document_anon_store.pop_document_map(1)
    with pytest.raises(document_anon_store.DocumentMapCacheError):
        await document_anon_store.set_document_map(1, AnonymizationMap(document_id=1, language="en"))
    with pytest.raises(document_anon_store.DocumentMapLockTimeout):
        async with document_anon_store.document_map_lock(1):
            pass


def _unreachable_errors() -> list:
    from redis.exceptions import ConnectionError as RedisConnectionError
    from redis.exceptions import TimeoutError as RedisTimeoutError

    return [
        RedisConnectionError("refused"),
        RedisTimeoutError("read timed out"),
        OSError(111, "refused"),
        TimeoutError(),
    ]


class _FailingClient:
    """Answers every call with ``error`` while it is set, and normally once it is cleared."""

    def __init__(self, error: BaseException | None) -> None:
        self.error = error

    def _answer(self, value):
        if self.error is not None:
            raise self.error
        return value

    async def get(self, key):
        return self._answer(None)

    async def set(self, key, value, **kwargs):
        return self._answer(True)

    async def delete(self, key):
        return self._answer(1)

    async def ping(self):
        return self._answer(True)


_HELPERS = {
    "get": lambda: redis_client.redis_get("k"),
    "set": lambda: redis_client.redis_set("k", b"v"),
    "set_if_absent": lambda: redis_client.redis_set_if_absent("k", b"v"),
    "delete": lambda: redis_client.redis_delete("k"),
    "ping": lambda: redis_client.redis_ping(),
}


def _use_client(monkeypatch: pytest.MonkeyPatch, client) -> None:
    _configure(monkeypatch)
    monkeypatch.setattr(redis_client, "_redis_pool", client)


@pytest.mark.asyncio
@pytest.mark.parametrize("helper", sorted(_HELPERS))
@pytest.mark.parametrize("error_index", range(4))
async def test_every_helper_opens_the_window_on_the_second_unreachable_call(
    monkeypatch: pytest.MonkeyPatch, helper: str, error_index: int
) -> None:
    client = _FailingClient(_unreachable_errors()[error_index])
    _use_client(monkeypatch, client)

    assert not await _HELPERS[helper]()
    assert redis_client._redis_retry_at is None
    assert redis_client.get_redis() is client
    assert not await _HELPERS[helper]()
    assert redis_client._redis_retry_at is not None
    assert redis_client.get_redis() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("helper", sorted(_HELPERS))
async def test_a_refused_command_never_opens_the_window(
    monkeypatch: pytest.MonkeyPatch, helper: str
) -> None:
    from redis.exceptions import ResponseError

    _use_client(monkeypatch, _FailingClient(ResponseError("WRONGTYPE")))

    for _ in range(3):
        assert not await _HELPERS[helper]()
    assert redis_client._redis_retry_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize("helper", sorted(_HELPERS))
async def test_an_answered_call_resets_the_count(
    monkeypatch: pytest.MonkeyPatch, helper: str
) -> None:
    from redis.exceptions import ConnectionError as RedisConnectionError

    client = _FailingClient(RedisConnectionError("blip"))
    _use_client(monkeypatch, client)

    await _HELPERS[helper]()
    client.error = None
    await _HELPERS[helper]()
    client.error = RedisConnectionError("blip")
    await _HELPERS[helper]()
    assert redis_client._redis_retry_at is None


class _FailingLock:
    def __init__(self, acquire=None, extend=None, release=None) -> None:
        self.errors = {"acquire": acquire, "extend": extend, "release": release}

    async def _call(self, name: str, value):
        if self.errors[name] is not None:
            raise self.errors[name]
        return value

    async def acquire(self) -> bool:
        return await self._call("acquire", True)

    async def extend(self, *args, **kwargs) -> bool:
        return await self._call("extend", True)

    async def release(self) -> None:
        return await self._call("release", None)


class _LockClient(_FailingClient):
    def __init__(self, lock: _FailingLock) -> None:
        super().__init__(None)
        self._lock = lock

    def lock(self, name: str, **kwargs):
        return self._lock


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["acquire", "extend", "release"])
async def test_every_map_lock_call_counts_an_unreachable_redis(
    monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    from redis.exceptions import ConnectionError as RedisConnectionError
    from septum_api.services import document_anon_store

    lock = _FailingLock(**{path: RedisConnectionError("refused")})
    client = _LockClient(lock)
    _use_client(monkeypatch, client)
    monkeypatch.setattr(document_anon_store, "get_redis", redis_client.get_redis)

    if path == "acquire":
        with pytest.raises(document_anon_store.DocumentMapLockTimeout) as excinfo:
            async with document_anon_store.document_map_lock(1):
                pass
        assert isinstance(excinfo.value.__cause__, RedisConnectionError)
    elif path == "extend":
        async with document_anon_store.document_map_lock(1) as held:
            with pytest.raises(RedisConnectionError):
                await held.ensure_held()
            assert redis_client._consecutive_failures == 1
        lock.errors["release"] = RedisConnectionError("refused")
        async with document_anon_store.document_map_lock(1):
            pass
    else:
        async with document_anon_store.document_map_lock(1):
            pass
    assert redis_client._redis_retry_at is None
    client.error = RedisConnectionError("refused")
    assert await redis_client.redis_get("k") is None
    assert redis_client._redis_retry_at is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["extend", "release"])
async def test_a_successful_lock_call_resets_the_failure_count(
    monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    from redis.exceptions import ConnectionError as RedisConnectionError
    from septum_api.services import document_anon_store

    client = _LockClient(_FailingLock())
    _use_client(monkeypatch, client)
    monkeypatch.setattr(document_anon_store, "get_redis", redis_client.get_redis)

    async def one_unreachable_call() -> None:
        client.error = RedisConnectionError("refused")
        await redis_client.redis_get("k")
        client.error = None

    async with document_anon_store.document_map_lock(1) as held:
        await one_unreachable_call()
        assert redis_client._consecutive_failures == 1
        if path == "extend":
            await held.ensure_held()
            assert redis_client._consecutive_failures == 0
            await one_unreachable_call()
            assert redis_client._redis_retry_at is None
    if path == "release":
        assert redis_client._consecutive_failures == 0
        await one_unreachable_call()
        assert redis_client._redis_retry_at is None


class _RecordingClient:
    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.deleted: list[tuple] = []

    async def delete(self, *keys):
        self.deleted.append(keys)
        if self.error is not None:
            raise self.error
        return len(keys)


@pytest.mark.asyncio
async def test_redis_delete_sends_one_command_with_every_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _RecordingClient()
    _use_client(monkeypatch, client)

    assert await redis_client.redis_delete("a", "b") is True
    assert client.deleted == [("a", "b")]


@pytest.mark.asyncio
async def test_redis_delete_reports_and_counts_a_failed_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from redis.exceptions import ConnectionError as RedisConnectionError

    client = _RecordingClient(RedisConnectionError("refused"))
    _use_client(monkeypatch, client)

    assert await redis_client.redis_delete("a", "b") is False
    assert client.deleted == [("a", "b")]
    assert redis_client._consecutive_failures == 1
