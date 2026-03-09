"""Tests for document anonymization map store (memory + encrypted persistence)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.services.anonymization_map import AnonymizationMap
from app.services.document_anon_store import (
    get_document_map,
    pop_document_map,
    set_document_map,
)


@pytest.fixture
def store_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Use a temp directory for encrypted map files."""
    monkeypatch.setenv("ANON_MAP_STORAGE_DIR", str(tmp_path / "anon_maps"))
    # Force module to re-read the env by re-importing or patching the path
    import app.services.document_anon_store as store_module

    store_module._ANON_MAP_DIR = tmp_path / "anon_maps"
    return store_module._ANON_MAP_DIR


def test_set_and_get_from_memory(store_dir: Path) -> None:
    """Map is returned from in-memory cache when present."""
    amap = AnonymizationMap(document_id=1, language="tr")
    amap.entity_map["Ahmet Yılmaz"] = "[PERSON_1]"
    set_document_map(1, amap)
    assert get_document_map(1) is amap
    assert get_document_map(1).entity_map["Ahmet Yılmaz"] == "[PERSON_1]"


def test_get_from_disk_after_memory_cleared(store_dir: Path) -> None:
    """When in-memory is cleared, map is loaded from encrypted file."""
    amap = AnonymizationMap(document_id=42, language="en")
    amap.entity_map["John Doe"] = "[PERSON_1]"
    amap.entity_map["Jane Smith"] = "[PERSON_2]"
    set_document_map(42, amap)

    # Simulate another worker/restart: clear in-memory only.
    import app.services.document_anon_store as store_module

    store_module._document_maps.pop(42, None)

    loaded = get_document_map(42)
    assert loaded is not None
    assert loaded.entity_map["John Doe"] == "[PERSON_1]"
    assert loaded.entity_map["Jane Smith"] == "[PERSON_2]"
    assert loaded.language == "en"


def test_pop_removes_from_memory_and_disk(store_dir: Path) -> None:
    """pop_document_map removes the map and deletes the encrypted file."""
    amap = AnonymizationMap(document_id=99, language="tr")
    amap.entity_map["Test"] = "[PERSON_1]"
    set_document_map(99, amap)
    path = store_dir / "99.enc"
    assert path.exists()

    pop_document_map(99)
    assert get_document_map(99) is None
    assert not path.exists()
