"""Tests for document anonymization map store (memory + Redis + encrypted persistence)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
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

    async def _redis_get(key):
        return ciphertext if key == "anon_map:200" else None

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
async def test_failed_redis_write_drops_the_redis_copy(
    mock_del, _mock_get, _mock_set, store_dir: Path
) -> None:
    amap = AnonymizationMap(document_id=62, language="en")
    amap.entity_map["A"] = "[PERSON_1]"

    await set_document_map(62, amap)

    mock_del.assert_awaited_once_with("anon_map:62")


class _ScriptedLock:
    """Redis lock double whose extend/release can be told to fail."""

    def __init__(self, extend_fails: bool = False, release_fails: bool = False) -> None:
        self.extend_fails = extend_fails
        self.release_fails = release_fails

    async def acquire(self) -> bool:
        return True

    async def extend(self, additional_time: float, replace_ttl: bool = False) -> bool:
        from redis.exceptions import LockNotOwnedError

        if self.extend_fails:
            raise LockNotOwnedError("expired")
        return True

    async def release(self) -> None:
        from redis.exceptions import LockNotOwnedError

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
async def test_release_failure_does_not_mask_the_body_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import septum_api.services.document_anon_store as store_module

    _use_redis(monkeypatch, _ScriptedRedis(_ScriptedLock(release_fails=True)))

    with pytest.raises(ValueError, match="body failed"):
        async with store_module.document_map_lock(72):
            raise ValueError("body failed")


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
    import fakeredis
    import septum_api.services.redis_client as redis_module

    client = fakeredis.FakeAsyncRedis()
    monkeypatch.setattr(redis_module, "_get_redis_url", lambda: "redis://fake")
    monkeypatch.setattr(redis_module, "_redis_pool", client)
    monkeypatch.setattr(redis_module, "_redis_unavailable", False)
    return client


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
    import septum_api.services.redis_client as redis_module

    monkeypatch.setattr(redis_module, "_get_redis_url", lambda: "redis://unreachable")
    monkeypatch.setattr(redis_module, "_redis_unavailable", True)
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

    async def _load_then_erased(document_id: int):
        loaded = await real_load(document_id)
        await pop_document_map(document_id)
        return loaded

    monkeypatch.setattr(store_module, "_load_persisted", _load_then_erased)

    assert (await get_document_map(91)) is None
    assert 91 not in store_module._document_maps
    assert (await fake_redis.get("anon_map:91")) is None


async def test_backfill_after_an_erasure_is_deleted_again(store_dir: Path, fake_redis) -> None:
    import septum_api.services.document_anon_store as store_module

    await set_document_map(92, _known(92))
    await fake_redis.delete("anon_map:92")
    await fake_redis.set("anon_map_version:92", b"erased")

    assert (await store_module._load_persisted(92)) is None
    assert (await fake_redis.get("anon_map:92")) is None


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
