"""Tests for document anonymization map store (memory + Redis + encrypted persistence)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from redis_doubles import redis_unavailable, use_fake_redis
from septum_api.services.anonymization_map import AnonymizationMap
from septum_api.services.document_anon_store import (
    get_document_map,
    pop_document_map,
    set_document_map,
)


@pytest.fixture
def store_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Use a temp directory for encrypted map files."""
    monkeypatch.setenv("ANON_MAP_STORAGE_DIR", str(tmp_path / "anon_maps"))
    import septum_api.services.document_anon_store as store_module

    store_module._ANON_MAP_DIR = tmp_path / "anon_maps"
    return store_module._ANON_MAP_DIR


@pytest.mark.asyncio
@patch("septum_api.services.document_anon_store.redis_set", new_callable=AsyncMock, return_value=False)
@patch("septum_api.services.document_anon_store.redis_get", new_callable=AsyncMock, return_value=None)
@patch("septum_api.services.document_anon_store.redis_delete", new_callable=AsyncMock, return_value=False)
async def test_set_and_get_from_memory(
    _mock_del, _mock_get, _mock_set, store_dir: Path
) -> None:
    """Map is returned from in-memory cache when present."""
    amap = AnonymizationMap(document_id=1, language="tr")
    amap.entity_map["Ahmet Yılmaz"] = "[PERSON_1]"
    await set_document_map(1, amap)
    result = await get_document_map(1)
    assert result is amap
    assert result.entity_map["Ahmet Yılmaz"] == "[PERSON_1]"


@pytest.mark.asyncio
@patch("septum_api.services.document_anon_store.redis_set", new_callable=AsyncMock, return_value=False)
@patch("septum_api.services.document_anon_store.redis_get", new_callable=AsyncMock, return_value=None)
@patch("septum_api.services.document_anon_store.redis_delete", new_callable=AsyncMock, return_value=False)
async def test_get_from_disk_after_memory_cleared(
    _mock_del, _mock_get, _mock_set, store_dir: Path
) -> None:
    """When in-memory is cleared, map is loaded from encrypted file."""
    amap = AnonymizationMap(document_id=42, language="en")
    amap.entity_map["John Doe"] = "[PERSON_1]"
    amap.entity_map["Jane Smith"] = "[PERSON_2]"
    await set_document_map(42, amap)

    import septum_api.services.document_anon_store as store_module

    store_module._document_maps.pop(42, None)

    loaded = await get_document_map(42)
    assert loaded is not None
    assert loaded.entity_map["John Doe"] == "[PERSON_1]"
    assert loaded.entity_map["Jane Smith"] == "[PERSON_2]"
    assert loaded.language == "en"


@pytest.mark.asyncio
@patch("septum_api.services.document_anon_store.redis_set", new_callable=AsyncMock, return_value=False)
@patch("septum_api.services.document_anon_store.redis_get", new_callable=AsyncMock, return_value=None)
@patch("septum_api.services.document_anon_store.redis_delete", new_callable=AsyncMock, return_value=False)
async def test_pop_removes_from_memory_and_disk(
    _mock_del, _mock_get, _mock_set, store_dir: Path
) -> None:
    """pop_document_map removes the map and deletes the encrypted file."""
    amap = AnonymizationMap(document_id=99, language="tr")
    amap.entity_map["Test"] = "[PERSON_1]"
    await set_document_map(99, amap)
    path = store_dir / "99.enc"
    assert path.exists()

    await pop_document_map(99)
    result = await get_document_map(99)
    assert result is None
    assert not path.exists()


@pytest.mark.asyncio
@patch("septum_api.services.document_anon_store.redis_set", new_callable=AsyncMock, return_value=False)
@patch("septum_api.services.document_anon_store.redis_get", new_callable=AsyncMock, return_value=None)
@patch("septum_api.services.document_anon_store.redis_delete", new_callable=AsyncMock, return_value=False)
async def test_blocklist_survives_disk_persistence(
    _mock_del, _mock_get, _mock_set, store_dir: Path
) -> None:
    """token_to_placeholder and blocklist survive encrypted persistence."""
    amap = AnonymizationMap(document_id=50, language="en")
    amap.add_entity("John Smith", "PERSON_NAME")
    original_t2p = dict(amap.token_to_placeholder)
    assert original_t2p, "Blocklist should have entries after adding a person name"
    await set_document_map(50, amap)

    import septum_api.services.document_anon_store as store_module

    store_module._document_maps.pop(50, None)

    loaded = await get_document_map(50)
    assert loaded is not None
    assert loaded.token_to_placeholder == original_t2p
    assert loaded.blocklist == set(original_t2p.keys())
    assert loaded.token_counter.get("PERSON_NAME", 0) == 1


@pytest.mark.asyncio
async def test_redis_tier_used_when_available(store_dir: Path) -> None:
    """When Redis has the data, disk should not be read.

    Redis stores AES-256-GCM ciphertext (matching the disk tier) so
    plaintext PII never traverses the bridge persistence layer.
    """
    import json

    from septum_api.utils.crypto import encrypt

    amap = AnonymizationMap(document_id=200, language="en")
    amap.entity_map["Alice"] = "[PERSON_1]"

    serialized = json.dumps({
        "entity_map": amap.entity_map,
        "language": amap.language,
        "token_to_placeholder": {},
        "token_counter": {},
    }).encode("utf-8")
    ciphertext = encrypt(serialized, associated_data=b"200")

    import septum_api.services.document_anon_store as store_module

    store_module._document_maps.pop(200, None)

    copies = {
        "anon_map:200": store_module._versioned_copy("v-200", ciphertext),
        "anon_map_version:200": b"v-200",
    }

    async def _redis_get(key):
        return copies.get(key)

    with patch("septum_api.services.document_anon_store.redis_get", new=_redis_get), patch(
        "septum_api.services.document_anon_store.redis_configured", return_value=True
    ):
        loaded = await get_document_map(200)

    assert loaded is not None
    assert loaded.entity_map["Alice"] == "[PERSON_1]"
    assert loaded.language == "en"


@pytest.mark.asyncio
async def test_redis_payload_is_ciphertext_not_plaintext(store_dir: Path) -> None:
    """Verify set_document_map writes AES-GCM ciphertext to Redis.

    Pinning this regression: Redis used to receive plaintext JSON which
    exposed every original PII value to anyone with Redis read access
    (RDB/AOF backups, MITM, compromised infrastructure).
    """
    captured: dict[str, bytes] = {}

    async def fake_redis_set(key, value, ttl=None):
        captured[key] = value
        return True

    amap = AnonymizationMap(document_id=999, language="en")
    amap.entity_map["Alice"] = "[PERSON_1]"
    amap.entity_map["alice@example.com"] = "[EMAIL_1]"

    with patch(
        "septum_api.services.document_anon_store.redis_set",
        new=fake_redis_set,
    ), patch(
        "septum_api.services.document_anon_store.redis_delete",
        new_callable=AsyncMock,
        return_value=True,
    ), patch(
        "septum_api.services.document_anon_store.redis_configured",
        return_value=True,
    ):
        await set_document_map(999, amap)

    payload = captured["anon_map:999"]
    assert b"Alice" not in payload
    assert b"alice@example.com" not in payload
    assert b"PERSON_1" not in payload  # placeholders also encrypted


@pytest.mark.asyncio
@patch("septum_api.services.document_anon_store.redis_set", new_callable=AsyncMock, return_value=False)
@patch("septum_api.services.document_anon_store.redis_get", new_callable=AsyncMock, return_value=None)
@patch("septum_api.services.document_anon_store.redis_delete", new_callable=AsyncMock, return_value=False)
async def test_graceful_degradation_without_redis(
    _mock_del, _mock_get, _mock_set, store_dir: Path
) -> None:
    """System works correctly when Redis is unavailable."""
    amap = AnonymizationMap(document_id=300, language="en")
    amap.entity_map["Bob"] = "[PERSON_1]"

    await set_document_map(300, amap)

    import septum_api.services.document_anon_store as store_module

    store_module._document_maps.pop(300, None)

    loaded = await get_document_map(300)
    assert loaded is not None
    assert loaded.entity_map["Bob"] == "[PERSON_1]"


class _FakeRedisLock:
    def __init__(self, events: list[str], acquired: bool) -> None:
        self._events = events
        self._acquired = acquired

    async def acquire(self) -> bool:
        self._events.append("acquire")
        return self._acquired

    async def release(self) -> None:
        self._events.append("release")


class _FakeRedis:
    def __init__(self, acquired: bool = True) -> None:
        self.events: list[str] = []
        self.lock_args: list[tuple[str, dict]] = []
        self._acquired = acquired

    def lock(self, name: str, **kwargs):
        self.lock_args.append((name, kwargs))
        return _FakeRedisLock(self.events, self._acquired)


@pytest.mark.asyncio
async def test_map_lock_holds_a_redis_lock_when_redis_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import septum_api.services.document_anon_store as store_module

    fake = _FakeRedis()

    def _get_redis():
        return fake

    monkeypatch.setattr(store_module, "get_redis", _get_redis)

    async with store_module.document_map_lock(7):
        fake.events.append("body")

    assert fake.events == ["acquire", "body", "release"]
    name, kwargs = fake.lock_args[0]
    assert name == "anon_map_lock:7"
    assert kwargs["timeout"] > 0 and kwargs["blocking_timeout"] > 0


@pytest.mark.asyncio
async def test_map_lock_raises_when_redis_lock_is_not_acquired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import septum_api.services.document_anon_store as store_module

    fake = _FakeRedis(acquired=False)

    def _get_redis():
        return fake

    monkeypatch.setattr(store_module, "get_redis", _get_redis)

    with pytest.raises(store_module.DocumentMapLockTimeout):
        async with store_module.document_map_lock(8):
            pytest.fail("body must not run without the lock")


@pytest.mark.asyncio
@patch("septum_api.services.document_anon_store.redis_set", new_callable=AsyncMock, return_value=False)
@patch("septum_api.services.document_anon_store.redis_get", new_callable=AsyncMock, return_value=None)
@patch("septum_api.services.document_anon_store.redis_delete", new_callable=AsyncMock, return_value=False)
async def test_persisted_read_bypasses_stale_memory(
    _mock_del, _mock_get, _mock_set, store_dir: Path
) -> None:
    import septum_api.services.document_anon_store as store_module

    stale = AnonymizationMap(document_id=60, language="en")
    stale.entity_map["A"] = "[PERSON_1]"
    newer = AnonymizationMap(document_id=60, language="en")
    newer.entity_map.update({"A": "[PERSON_1]", "B": "[PERSON_2]"})
    await set_document_map(60, newer)
    store_module._document_maps[60] = stale

    assert (await get_document_map(60)) is stale
    fresh = await store_module.load_persisted_document_map(60)

    assert fresh.entity_map == newer.entity_map
    assert (await get_document_map(60)) is fresh


@pytest.mark.asyncio
@patch("septum_api.services.document_anon_store.redis_set", new_callable=AsyncMock, return_value=True)
@patch("septum_api.services.document_anon_store.redis_get", new_callable=AsyncMock, return_value=None)
@patch("septum_api.services.document_anon_store.redis_delete", new_callable=AsyncMock, return_value=False)
async def test_disk_write_failure_raises_and_leaves_other_tiers_untouched(
    _mock_del, _mock_get, mock_set, store_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import septum_api.services.document_anon_store as store_module

    monkeypatch.setattr(store_module, "_map_path", lambda _id: store_dir / "no" / "such" / "x.enc")
    amap = AnonymizationMap(document_id=61, language="en")
    amap.entity_map["A"] = "[PERSON_1]"

    with pytest.raises(OSError):
        await set_document_map(61, amap)

    assert 61 not in store_module._document_maps
    mock_set.assert_not_called()


@pytest.mark.asyncio
@patch("septum_api.services.document_anon_store.redis_set", new_callable=AsyncMock, return_value=False)
@patch("septum_api.services.document_anon_store.redis_get", new_callable=AsyncMock, return_value=None)
@patch("septum_api.services.document_anon_store.redis_delete", new_callable=AsyncMock, return_value=True)
@patch("septum_api.services.document_anon_store.redis_configured", return_value=True)
async def test_failed_redis_write_drops_the_redis_copy(
    _configured, mock_del, _mock_get, mock_set, store_dir: Path
) -> None:
    amap = AnonymizationMap(document_id=62, language="en")
    amap.entity_map["A"] = "[PERSON_1]"

    await set_document_map(62, amap)

    mock_set.assert_awaited_once()
    deleted = [call.args for call in mock_del.await_args_list]
    assert deleted[-1] == ("anon_map:62", "anon_map_version:62")


class _ScriptedLock:
    """Redis lock double whose extend/release can be told to fail."""

    def __init__(
        self,
        extend_fails: bool = False,
        release_fails: bool = False,
        release_error: BaseException | None = None,
    ) -> None:
        self.extend_fails = extend_fails
        self.release_fails = release_fails
        self.release_error = release_error

    async def acquire(self) -> bool:
        return True

    async def extend(self, additional_time: float, replace_ttl: bool = False) -> bool:
        from redis.exceptions import LockNotOwnedError

        if self.extend_fails:
            raise LockNotOwnedError("expired")
        return True

    async def release(self) -> None:
        from redis.exceptions import LockNotOwnedError

        if self.release_error is not None:
            raise self.release_error
        if self.release_fails:
            raise LockNotOwnedError("expired")


class _ScriptedRedis:
    def __init__(self, lock: _ScriptedLock) -> None:
        self._lock = lock

    def lock(self, name: str, **kwargs):
        return self._lock


def _use_redis(monkeypatch: pytest.MonkeyPatch, client) -> None:
    import septum_api.services.document_anon_store as store_module

    def _get_redis():
        return client

    monkeypatch.setattr(store_module, "get_redis", _get_redis)


@pytest.mark.asyncio
async def test_lost_lock_is_reported_before_the_write(monkeypatch: pytest.MonkeyPatch) -> None:
    import septum_api.services.document_anon_store as store_module

    _use_redis(monkeypatch, _ScriptedRedis(_ScriptedLock(extend_fails=True)))

    with pytest.raises(store_module.DocumentMapLockLost):
        async with store_module.document_map_lock(70) as lock:
            await lock.ensure_held()


@pytest.mark.asyncio
async def test_held_lock_passes_ensure_held(monkeypatch: pytest.MonkeyPatch) -> None:
    import septum_api.services.document_anon_store as store_module

    _use_redis(monkeypatch, _ScriptedRedis(_ScriptedLock()))

    async with store_module.document_map_lock(71) as lock:
        await lock.ensure_held()


@pytest.mark.asyncio
@pytest.mark.parametrize("body_fails", [False, True])
async def test_a_release_that_cannot_reach_redis_neither_raises_nor_masks_the_body(
    body_fails: bool, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The body's outcome stands (a committed delete stays a success); the lock expires on its own."""
    import septum_api.services.document_anon_store as store_module
    from redis.exceptions import ConnectionError as RedisConnectionError

    monkeypatch.setattr(store_module, "redis_configured", lambda: True)
    _use_redis(
        monkeypatch,
        _ScriptedRedis(_ScriptedLock(release_error=RedisConnectionError("reset"))),
    )
    with caplog.at_level("ERROR", logger=store_module.__name__):
        if body_fails:
            with pytest.raises(ValueError, match="body failed"):
                async with store_module.document_map_lock(74):
                    raise ValueError("body failed")
        else:
            async with store_module.document_map_lock(74):
                pass
    assert "document_id=74: ConnectionError" in caplog.text


@pytest.mark.asyncio
async def test_a_configured_redis_without_a_client_refuses_the_map_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import septum_api.services.document_anon_store as store_module

    monkeypatch.setattr(store_module, "redis_configured", lambda: True)
    _use_redis(monkeypatch, None)
    with pytest.raises(store_module.DocumentMapLockTimeout):
        async with store_module.document_map_lock(75):
            pass


@pytest.mark.asyncio
async def test_release_failure_does_not_mask_the_body_error(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import septum_api.services.document_anon_store as store_module

    _use_redis(monkeypatch, _ScriptedRedis(_ScriptedLock(release_fails=True)))

    with caplog.at_level("ERROR", logger=store_module.__name__):
        with pytest.raises(ValueError, match="body failed"):
            async with store_module.document_map_lock(72):
                raise ValueError("body failed")
    assert [record.getMessage() for record in caplog.records] == [
        "Anonymization map lock of document_id=72 expired before release"
    ]


@pytest.mark.asyncio
async def test_a_cancellation_during_release_is_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import septum_api.services.document_anon_store as store_module

    _use_redis(
        monkeypatch, _ScriptedRedis(_ScriptedLock(release_error=asyncio.CancelledError()))
    )

    with pytest.raises(asyncio.CancelledError):
        async with store_module.document_map_lock(76):
            pass


@pytest.mark.asyncio
async def test_release_failure_after_a_clean_body_is_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import septum_api.services.document_anon_store as store_module

    _use_redis(monkeypatch, _ScriptedRedis(_ScriptedLock(release_fails=True)))

    with pytest.raises(store_module.DocumentMapLockLost):
        async with store_module.document_map_lock(73):
            pass


@pytest.mark.asyncio
@patch("septum_api.services.document_anon_store.redis_set", new_callable=AsyncMock, return_value=True)
@patch("septum_api.services.document_anon_store.redis_delete", new_callable=AsyncMock, return_value=True)
async def test_locked_read_uses_disk_not_redis(
    _mock_del, _mock_set, store_dir: Path
) -> None:
    import septum_api.services.document_anon_store as store_module
    from septum_api.utils.crypto import encrypt

    older = AnonymizationMap(document_id=74, language="en")
    older.entity_map["A"] = "[PERSON_1]"
    newer = AnonymizationMap(document_id=74, language="en")
    newer.entity_map.update({"A": "[PERSON_1]", "B": "[PERSON_2]"})
    await set_document_map(74, newer)
    stale_redis = encrypt(store_module._serialize(older), associated_data=b"74")

    with patch(
        "septum_api.services.document_anon_store.redis_get",
        new_callable=AsyncMock,
        return_value=stale_redis,
    ) as mock_get:
        fresh = await store_module.load_persisted_document_map(74)

    assert fresh.entity_map == newer.entity_map
    mock_get.assert_not_awaited()


@pytest.mark.asyncio
@patch("septum_api.services.document_anon_store.redis_set", new_callable=AsyncMock, return_value=False)
@patch("septum_api.services.document_anon_store.redis_get", new_callable=AsyncMock, return_value=None)
@patch("septum_api.services.document_anon_store.redis_delete", new_callable=AsyncMock, return_value=False)
async def test_pop_raises_and_logs_when_the_file_cannot_be_removed(
    _mock_del, _mock_get, _mock_set, store_dir: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    import septum_api.services.document_anon_store as store_module

    undeletable = store_dir.parent / "undeletable"
    undeletable.mkdir(parents=True)
    monkeypatch.setattr(store_module, "_map_path", lambda _id: undeletable)
    store_module._document_maps[75] = AnonymizationMap(document_id=75, language="en")

    with caplog.at_level("ERROR", logger=store_module.__name__):
        with pytest.raises(store_module.DocumentMapErasureError):
            await pop_document_map(75)

    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert errors and "document_id=75" in errors[0].getMessage()
    assert 75 not in store_module._document_maps


@pytest.mark.asyncio
@patch("septum_api.services.document_anon_store.redis_delete", new_callable=AsyncMock, return_value=False)
async def test_pop_raises_when_the_redis_copy_survives(
    _mock_del, store_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import septum_api.services.document_anon_store as store_module

    _use_redis(monkeypatch, object())

    with pytest.raises(store_module.DocumentMapErasureError, match="redis"):
        await pop_document_map(76)


class _Worker:
    """One API worker's memory tier; the persistent tiers stay shared."""

    def __init__(self) -> None:
        self.maps: dict = {}
        self.versions: dict = {}

    def activate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import septum_api.services.document_anon_store as store_module

        monkeypatch.setattr(store_module, "_document_maps", self.maps)
        monkeypatch.setattr(store_module, "_map_versions", self.versions)


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch):
    """A configured, reachable Redis backed by fakeredis."""
    return use_fake_redis(monkeypatch)


def _known(document_id: int, name: str = "Alice") -> AnonymizationMap:
    amap = AnonymizationMap(document_id=document_id, language="en")
    amap.entity_map[name] = "[PERSON_1]"
    return amap


@pytest.mark.parametrize("with_redis", [False, True])
async def test_delete_on_one_worker_invalidates_another_workers_memory(
    with_redis: bool, store_dir: Path, monkeypatch: pytest.MonkeyPatch, request
) -> None:
    if with_redis:
        request.getfixturevalue("fake_redis")
    worker_a, worker_b = _Worker(), _Worker()
    worker_a.activate(monkeypatch)
    await set_document_map(80, _known(80))
    worker_b.activate(monkeypatch)
    assert (await get_document_map(80)) is not None
    assert 80 in worker_b.maps

    worker_a.activate(monkeypatch)
    await pop_document_map(80)

    worker_b.activate(monkeypatch)
    assert (await get_document_map(80)) is None
    assert 80 not in worker_b.maps


@pytest.mark.parametrize("with_redis", [False, True])
async def test_rewrite_on_one_worker_refreshes_another_workers_memory(
    with_redis: bool, store_dir: Path, monkeypatch: pytest.MonkeyPatch, request
) -> None:
    if with_redis:
        request.getfixturevalue("fake_redis")
    worker_a, worker_b = _Worker(), _Worker()
    worker_a.activate(monkeypatch)
    await set_document_map(81, _known(81, "Alice"))
    worker_b.activate(monkeypatch)
    first = await get_document_map(81)
    assert (await get_document_map(81)) is first

    worker_a.activate(monkeypatch)
    await set_document_map(81, _known(81, "Bob"))

    worker_b.activate(monkeypatch)
    assert list((await get_document_map(81)).entity_map) == ["Bob"]


async def test_tombstone_ignores_a_redis_copy_written_back_after_erasure(
    store_dir: Path, fake_redis
) -> None:
    await set_document_map(82, _known(82))
    stale_copy = await fake_redis.get("anon_map:82")

    await pop_document_map(82)
    await fake_redis.set("anon_map:82", stale_copy)

    assert (await get_document_map(82)) is None
    assert (await fake_redis.get("anon_map_version:82")) == b"erased"


async def test_legacy_map_without_a_version_is_cached_after_one_load(
    store_dir: Path, fake_redis
) -> None:
    import septum_api.services.document_anon_store as store_module

    await set_document_map(83, _known(83))
    await fake_redis.delete("anon_map_version:83")
    store_module._document_maps.pop(83, None)

    loaded = await get_document_map(83)

    assert loaded is not None
    assert (await fake_redis.get("anon_map_version:83")) is not None
    assert (await get_document_map(83)) is loaded


async def test_pop_fails_when_redis_is_configured_but_unavailable(
    store_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import septum_api.services.document_anon_store as store_module

    redis_unavailable(monkeypatch)
    store_module._document_maps[84] = _known(84)

    with pytest.raises(store_module.DocumentMapErasureError, match="redis"):
        await pop_document_map(84)
    assert 84 not in store_module._document_maps


@patch("septum_api.services.document_anon_store.redis_get", new_callable=AsyncMock, return_value=None)
async def test_missing_map_debug_log_has_no_path(
    _mock_get, store_dir: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    import septum_api.services.document_anon_store as store_module

    monkeypatch.setattr(store_module, "redis_configured", lambda: True)

    with caplog.at_level("DEBUG", logger=store_module.__name__):
        assert (await get_document_map(85)) is None

    assert "document_id=85" in caplog.text
    assert str(store_dir) not in caplog.text
    assert ".enc" not in caplog.text


@patch("septum_api.services.document_anon_store.redis_set", new_callable=AsyncMock, return_value=False)
@patch("septum_api.services.document_anon_store.redis_get", new_callable=AsyncMock, return_value=None)
@patch("septum_api.services.document_anon_store.redis_delete", new_callable=AsyncMock, return_value=False)
async def test_map_file_is_created_owner_only(_d, _g, _s, store_dir: Path) -> None:
    await set_document_map(86, _known(86))

    assert ((store_dir / "86.enc").stat().st_mode & 0o777) == 0o600
    assert list(store_dir.glob("*.tmp")) == []


@patch("septum_api.services.document_anon_store.redis_set", new_callable=AsyncMock, return_value=False)
@patch("septum_api.services.document_anon_store.redis_get", new_callable=AsyncMock, return_value=None)
@patch("septum_api.services.document_anon_store.redis_delete", new_callable=AsyncMock, return_value=False)
async def test_pop_removes_orphaned_temp_files_of_that_document_only(
    _d, _g, _s, store_dir: Path
) -> None:
    await set_document_map(87, _known(87))
    orphan = store_dir / "87.enc.deadbeef.tmp"
    orphan.write_bytes(b"ciphertext")
    other = store_dir / "870.enc.deadbeef.tmp"
    other.write_bytes(b"ciphertext")

    await pop_document_map(87)

    assert not orphan.exists()
    assert not (store_dir / "87.enc").exists()
    assert other.exists()


@patch("septum_api.services.document_anon_store.redis_set", new_callable=AsyncMock, return_value=False)
@patch("septum_api.services.document_anon_store.redis_get", new_callable=AsyncMock, return_value=None)
@patch("septum_api.services.document_anon_store.redis_delete", new_callable=AsyncMock, return_value=False)
async def test_pop_fails_when_an_orphaned_temp_file_survives(
    _d, _g, _s, store_dir: Path
) -> None:
    import septum_api.services.document_anon_store as store_module

    store_dir.mkdir(parents=True, exist_ok=True)
    (store_dir / "88.enc.deadbeef.tmp").mkdir()

    with pytest.raises(store_module.DocumentMapErasureError, match="disk"):
        await pop_document_map(88)


def test_startup_sweep_removes_only_stale_temp_files(store_dir: Path) -> None:
    import os
    import time

    import septum_api.services.document_anon_store as store_module

    store_dir.mkdir(parents=True, exist_ok=True)
    stale = store_dir / "89.enc.old.tmp"
    stale.write_bytes(b"x")
    old = time.time() - store_module._STALE_TMP_SECONDS - 10
    os.utime(stale, (old, old))
    fresh = store_dir / "90.enc.new.tmp"
    fresh.write_bytes(b"x")
    kept_map = store_dir / "89.enc"
    kept_map.write_bytes(b"x")
    os.utime(kept_map, (old, old))

    assert store_module.sweep_stale_temp_files() == 1

    assert not stale.exists()
    assert fresh.exists()
    assert kept_map.exists()


async def test_reader_racing_an_erasure_does_not_cache_or_backfill_the_map(
    store_dir: Path, fake_redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    import septum_api.services.document_anon_store as store_module

    await set_document_map(91, _known(91))
    await fake_redis.delete("anon_map_version:91")
    store_module._forget(91)
    real_load = store_module._load_persisted

    async def _load_then_erased(document_id: int, version):
        loaded = await real_load(document_id, version)
        await pop_document_map(document_id)
        return loaded

    monkeypatch.setattr(store_module, "_load_persisted", _load_then_erased)

    assert (await get_document_map(91)) is None
    assert 91 not in store_module._document_maps
    assert (await fake_redis.get("anon_map:91")) is None


async def test_a_disk_read_after_an_erasure_returns_nothing_and_writes_no_redis_copy(
    store_dir: Path, fake_redis
) -> None:
    import septum_api.services.document_anon_store as store_module

    await set_document_map(92, _known(92))
    await fake_redis.delete("anon_map:92")
    await fake_redis.set("anon_map_version:92", b"erased")

    await store_module._load_persisted(92, None)
    assert (await fake_redis.get("anon_map:92")) is None
    assert (await get_document_map(92)) is None


async def test_an_erasure_landing_during_the_disk_read_returns_nothing_and_writes_no_redis_copy(
    store_dir: Path, fake_redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    import septum_api.services.document_anon_store as store_module

    await set_document_map(93, _known(93))
    await fake_redis.delete("anon_map:93")
    real_load = store_module._load_persisted

    async def _load_then_erase(document_id: int, version):
        loaded = await real_load(document_id, version)
        await fake_redis.set("anon_map_version:93", b"erased")
        return loaded

    monkeypatch.setattr(store_module, "_load_persisted", _load_then_erase)

    store_module._forget(93)

    assert (await get_document_map(93)) is None
    assert (await fake_redis.get("anon_map:93")) is None
    assert 93 not in store_module._document_maps


async def test_a_read_racing_a_newer_write_never_leaves_the_older_map_in_redis(
    store_dir: Path, fake_redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reader whose Redis copy expired reads disk; another worker's newer write lands right after."""
    import septum_api.services.document_anon_store as store_module

    reader, writer, later = _Worker(), _Worker(), _Worker()
    writer.activate(monkeypatch)
    await set_document_map(94, _known(94, "Alice"))
    await fake_redis.delete("anon_map:94")
    newer = _known(94, "Alice")
    newer.entity_map["Bob"] = "[PERSON_2]"
    real_read, real_get, real_set = (
        store_module._load_from_disk, store_module.redis_get, store_module.redis_set
    )
    race = {"read": False, "fired": False}

    def _read(document_id: int):
        race["read"] = True
        return real_read(document_id)

    async def _newer_write_first() -> None:
        if race["read"] and not race["fired"]:
            race["fired"] = True
            writer.activate(monkeypatch)
            await set_document_map(94, newer)
            reader.activate(monkeypatch)

    async def _get(key):
        await _newer_write_first()
        return await real_get(key)

    async def _set(key, value, ttl=None):
        await _newer_write_first()
        return await real_set(key, value, ttl=ttl)

    monkeypatch.setattr(store_module, "_load_from_disk", _read)
    monkeypatch.setattr(store_module, "redis_get", _get)
    monkeypatch.setattr(store_module, "redis_set", _set)
    reader.activate(monkeypatch)
    assert "Bob" not in (await get_document_map(94)).entity_map
    assert race["fired"]
    monkeypatch.setattr(store_module, "_load_from_disk", real_read)
    monkeypatch.setattr(store_module, "redis_get", real_get)
    monkeypatch.setattr(store_module, "redis_set", real_set)

    later.activate(monkeypatch)
    assert "Bob" in (await get_document_map(94)).entity_map
    reader.activate(monkeypatch)
    assert "Bob" in (await get_document_map(94)).entity_map


async def test_a_write_cut_short_after_the_disk_write_leaves_no_older_map_trusted(
    store_dir: Path, fake_redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A warm worker never keeps the older map once the disk holds a newer one, even if the writer stops there."""
    import septum_api.services.document_anon_store as store_module

    writer, warm = _Worker(), _Worker()
    writer.activate(monkeypatch)
    await set_document_map(95, _known(95, "Alice"))
    warm.activate(monkeypatch)
    assert "Alice" in (await get_document_map(95)).entity_map
    newer = _known(95, "Alice")
    newer.entity_map["Bob"] = "[PERSON_2]"
    real_replace = store_module._replace_atomically
    seen_mid_write: list = []

    def _replace_then_stop(path, data):
        real_replace(path, data)
        seen_mid_write.append(asyncio.ensure_future(_warm_read()))
        raise asyncio.CancelledError()

    async def _warm_read():
        warm.activate(monkeypatch)
        return await get_document_map(95)

    monkeypatch.setattr(store_module, "_replace_atomically", _replace_then_stop)
    writer.activate(monkeypatch)
    with pytest.raises(asyncio.CancelledError):
        await set_document_map(95, newer)
    monkeypatch.setattr(store_module, "_replace_atomically", real_replace)

    assert "Bob" in (await seen_mid_write[0]).entity_map
    warm.activate(monkeypatch)
    assert "Bob" in (await get_document_map(95)).entity_map


async def test_a_write_that_can_neither_replace_nor_drop_the_redis_copy_raises(
    store_dir: Path, fake_redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    import septum_api.services.document_anon_store as store_module

    await set_document_map(96, _known(96))

    async def _fails(*_args, **_kwargs):
        return False

    monkeypatch.setattr(store_module, "redis_set", _fails)
    monkeypatch.setattr(store_module, "redis_delete", _fails)
    with pytest.raises(store_module.DocumentMapCacheError):
        await set_document_map(96, _known(96, "Bob"))


async def test_a_write_whose_redis_copy_fails_but_was_dropped_succeeds_without_a_version(
    store_dir: Path, fake_redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    import septum_api.services.document_anon_store as store_module

    await set_document_map(97, _known(97))

    async def _fails(*_args, **_kwargs):
        return False

    monkeypatch.setattr(store_module, "redis_set", _fails)
    await set_document_map(97, _known(97, "Bob"))

    assert await fake_redis.get("anon_map:97") is None
    assert await fake_redis.get("anon_map_version:97") is None
    assert 97 not in store_module._document_maps


@pytest.mark.parametrize("with_redis", [False, True])
async def test_a_read_racing_an_erasure_returns_nothing_and_caches_nothing(
    with_redis: bool, store_dir: Path, monkeypatch: pytest.MonkeyPatch, request
) -> None:
    """The version is read before the load; an erasure during the load must still win."""
    import septum_api.services.document_anon_store as store_module

    if with_redis:
        request.getfixturevalue("fake_redis")
    writer, reader = _Worker(), _Worker()
    writer.activate(monkeypatch)
    await set_document_map(98, _known(98))
    real_load = store_module._load_persisted

    async def _load_then_erased(document_id: int, version):
        loaded = await real_load(document_id, version)
        writer.activate(monkeypatch)
        await pop_document_map(document_id)
        reader.activate(monkeypatch)
        return loaded

    monkeypatch.setattr(store_module, "_load_persisted", _load_then_erased)
    reader.activate(monkeypatch)

    assert (await get_document_map(98)) is None
    assert 98 not in reader.maps


def _ciphertext(anon_map: AnonymizationMap) -> bytes:
    import septum_api.services.document_anon_store as store_module
    from septum_api.utils.crypto import encrypt

    data = store_module._serialize(anon_map)
    return encrypt(data, associated_data=str(anon_map.document_id).encode("utf-8"))


def _with_bob(document_id: int) -> AnonymizationMap:
    anon_map = _known(document_id, "Alice")
    anon_map.entity_map["Bob"] = "[PERSON_2]"
    return anon_map


async def test_a_claim_on_the_old_file_made_during_a_write_is_dropped_after_the_disk_write(
    store_dir: Path, fake_redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the drops before and after the disk write land; the Redis writes and the last drop fail."""
    import septum_api.services.document_anon_store as store_module

    writer, reader = _Worker(), _Worker()
    writer.activate(monkeypatch)
    await set_document_map(110, _known(110, "Alice"))
    real_drop, real_set = store_module._drop_redis_copy, store_module.redis_set
    real_replace = store_module._replace_atomically
    phase = {"disk_written": False, "set_tried": False}

    def _replace(path, data):
        stat_result = real_replace(path, data)
        phase["disk_written"] = True
        return stat_result

    async def _drop(document_id: int) -> bool:
        if phase["set_tried"]:
            return False
        dropped = await real_drop(document_id)
        if not phase["disk_written"]:
            reader.activate(monkeypatch)
            assert "Bob" not in (await get_document_map(document_id)).entity_map
            writer.activate(monkeypatch)
        return dropped

    async def _set_fails(key, value, ttl=None):
        phase["set_tried"] = True
        return False

    monkeypatch.setattr(store_module, "_replace_atomically", _replace)
    monkeypatch.setattr(store_module, "_drop_redis_copy", _drop)
    monkeypatch.setattr(store_module, "redis_set", _set_fails)
    await set_document_map(110, _with_bob(110))
    monkeypatch.setattr(store_module, "_replace_atomically", real_replace)
    monkeypatch.setattr(store_module, "_drop_redis_copy", real_drop)
    monkeypatch.setattr(store_module, "redis_set", real_set)

    reader.activate(monkeypatch)
    assert "Bob" in (await get_document_map(110)).entity_map


async def test_a_redis_write_cut_short_while_the_drops_failed_drops_the_keys_on_the_way_out(
    store_dir: Path, fake_redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Redis kept the old copy (both drops failed); the map lands, the version write is cancelled."""
    import septum_api.services.document_anon_store as store_module

    writer, warm = _Worker(), _Worker()
    writer.activate(monkeypatch)
    await set_document_map(111, _known(111, "Alice"))
    warm.activate(monkeypatch)
    assert "Bob" not in (await get_document_map(111)).entity_map
    real_drop, real_set = store_module._drop_redis_copy, store_module.redis_set
    drops: list = []

    async def _drop(document_id: int) -> bool:
        drops.append(document_id)
        return False if len(drops) <= 2 else await real_drop(document_id)

    async def _set(key, value, ttl=None):
        if key.startswith("anon_map_version:"):
            raise asyncio.CancelledError()
        return await real_set(key, value, ttl=ttl)

    monkeypatch.setattr(store_module, "_drop_redis_copy", _drop)
    monkeypatch.setattr(store_module, "redis_set", _set)
    writer.activate(monkeypatch)
    with pytest.raises(asyncio.CancelledError):
        await set_document_map(111, _with_bob(111))
    monkeypatch.setattr(store_module, "_drop_redis_copy", real_drop)
    monkeypatch.setattr(store_module, "redis_set", real_set)

    warm.activate(monkeypatch)
    assert "Bob" in (await get_document_map(111)).entity_map


async def test_a_first_drop_alone_does_not_make_a_failed_write_clean(
    store_dir: Path, fake_redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A version claimed between the first drop and the disk write survives unless a later drop lands."""
    import septum_api.services.document_anon_store as store_module

    await set_document_map(115, _known(115))
    real_drop = store_module._drop_redis_copy
    drops: list = []

    async def _only_the_first(document_id: int) -> bool:
        drops.append(document_id)
        return await real_drop(document_id) if len(drops) == 1 else False

    async def _set_fails(key, value, ttl=None):
        return False

    monkeypatch.setattr(store_module, "_drop_redis_copy", _only_the_first)
    monkeypatch.setattr(store_module, "redis_set", _set_fails)
    with pytest.raises(store_module.DocumentMapCacheError):
        await set_document_map(115, _with_bob(115))


async def test_a_drop_that_removes_the_map_but_not_its_version_does_not_count(
    store_dir: Path, fake_redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    import septum_api.services.document_anon_store as store_module

    await set_document_map(112, _known(112))

    async def _set_fails(key, value, ttl=None):
        return False

    async def _only_the_map_key(*keys):
        return all(key.startswith("anon_map:") for key in keys)

    monkeypatch.setattr(store_module, "redis_set", _set_fails)
    monkeypatch.setattr(store_module, "redis_delete", _only_the_map_key)
    with pytest.raises(store_module.DocumentMapCacheError):
        await set_document_map(112, _with_bob(112))


async def test_a_version_is_claimed_before_the_file_is_read(
    store_dir: Path, fake_redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A writer that dies after its disk write and its second drop leaves no claim covering the old file."""
    import septum_api.services.document_anon_store as store_module

    reader = _Worker()
    await set_document_map(113, _known(113, "Alice"))
    await fake_redis.delete("anon_map:113", "anon_map_version:113")
    real_load = store_module._load_persisted
    died: list = []

    async def _load_while_a_writer_dies(document_id: int, version):
        loaded = await real_load(document_id, version)
        if not died:
            died.append(True)
            store_module._replace_atomically(
                store_module._map_path(document_id), _ciphertext(_with_bob(document_id))
            )
            await store_module._drop_redis_copy(document_id)
        return loaded

    monkeypatch.setattr(store_module, "_load_persisted", _load_while_a_writer_dies)
    reader.activate(monkeypatch)
    assert "Bob" not in (await get_document_map(113)).entity_map
    assert "Bob" in (await get_document_map(113)).entity_map


async def test_a_redis_copy_landing_after_a_newer_write_is_not_trusted(
    store_dir: Path, fake_redis
) -> None:
    """A client-side-timed-out SET of the older copy lands after the newer version was set."""
    import septum_api.services.document_anon_store as store_module

    await set_document_map(116, _known(116, "Alice"))
    late_copy = await fake_redis.get("anon_map:116")
    await set_document_map(116, _with_bob(116))
    await fake_redis.set("anon_map:116", late_copy)
    store_module._forget(116)

    assert "Bob" in (await get_document_map(116)).entity_map


async def test_a_redis_copy_from_before_versioned_copies_counts_as_absent(
    store_dir: Path, fake_redis
) -> None:
    import septum_api.services.document_anon_store as store_module

    await set_document_map(117, _with_bob(117))
    await fake_redis.set("anon_map:117", _ciphertext(_known(117, "Alice")))
    store_module._forget(117)

    assert "Bob" in (await get_document_map(117)).entity_map


async def test_the_final_drop_survives_a_second_cancellation(
    store_dir: Path, fake_redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The writer is cancelled while its Redis write hangs, then again while its final drop waits."""
    import septum_api.services.document_anon_store as store_module

    await set_document_map(118, _known(118, "Alice"))
    real_drop = store_module._drop_redis_copy
    hang = asyncio.Event()
    final_drop_waiting = asyncio.Event()
    release_final_drop = asyncio.Event()
    final_drop_done = asyncio.Event()
    drops: list = []

    async def _drop(document_id: int) -> bool:
        drops.append(document_id)
        if len(drops) <= 2:
            return False
        final_drop_waiting.set()
        await release_final_drop.wait()
        dropped = await real_drop(document_id)
        final_drop_done.set()
        return dropped

    async def _set_hangs(key, value, ttl=None):
        await hang.wait()
        return True

    monkeypatch.setattr(store_module, "_drop_redis_copy", _drop)
    monkeypatch.setattr(store_module, "redis_set", _set_hangs)
    writer = asyncio.create_task(set_document_map(118, _with_bob(118)))
    while len(drops) < 2:
        await asyncio.sleep(0)
    await asyncio.sleep(0)
    writer.cancel()
    await final_drop_waiting.wait()
    writer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await writer
    release_final_drop.set()
    await asyncio.wait_for(final_drop_done.wait(), timeout=5)

    assert await fake_redis.get("anon_map:118") is None
    assert await fake_redis.get("anon_map_version:118") is None


async def test_a_reader_claim_expires_with_the_map_copy(store_dir: Path, fake_redis) -> None:
    import septum_api.services.document_anon_store as store_module

    await set_document_map(114, _known(114))
    await fake_redis.delete("anon_map_version:114")
    store_module._forget(114)

    assert (await get_document_map(114)) is not None
    assert 0 < await fake_redis.ttl("anon_map_version:114") <= store_module._REDIS_TTL


@patch("septum_api.services.document_anon_store.redis_get", new_callable=AsyncMock, return_value=None)
async def test_persisted_read_without_a_disk_file_ignores_the_memory_copy(
    _g, store_dir: Path
) -> None:
    import septum_api.services.document_anon_store as store_module

    store_module._document_maps[93] = _known(93)

    assert (await store_module.load_persisted_document_map(93)) is None


@pytest.mark.asyncio
async def test_map_write_locks_are_released_when_idle() -> None:
    import septum_api.services.document_anon_store as store_module

    for document_id in range(900, 920):
        async with store_module.document_map_lock(document_id):
            pass
    assert all(doc_id not in store_module._map_write_locks for doc_id in range(900, 920))
