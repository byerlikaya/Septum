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

    with patch("septum_api.services.document_anon_store.redis_get", new_callable=AsyncMock, return_value=ciphertext):
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
