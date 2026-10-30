"""Characterization of the orphan sweep's listing failures (pins behaviour before a refactor).

Every disk and cache listing is replaced by an in-memory fake, so the sweep
never touches the real storage directories.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from septum_api.models import Base
from septum_api.services import orphan_artifacts
from septum_api.services.document_anon_store import DocumentMapErasureError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class _ListingFailure:
    """Stands in for a store whose listing raises ``OSError``."""

    def stored_document_ids(self) -> set[int]:
        raise PermissionError("denied")


class _Empty:
    def stored_document_ids(self) -> set[int]:
        return set()


@pytest.fixture
async def session_maker(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'sweep.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def quiet_listings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(orphan_artifacts, "VectorStore", _Empty)
    monkeypatch.setattr(orphan_artifacts, "BM25Retriever", _Empty)
    monkeypatch.setattr(orphan_artifacts, "disk_map_document_ids", lambda: set())
    monkeypatch.setattr(orphan_artifacts, "redis_map_document_ids", AsyncMock(return_value=set()))


async def test_failed_disk_listing_is_counted_and_the_sweep_goes_on(
    session_maker, tmp_path: Path, quiet_listings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(orphan_artifacts, "VectorStore", _ListingFailure)

    result = await orphan_artifacts.sweep_orphan_artifacts(session_maker, tmp_path)

    assert result.failures == 1
    assert result.documents == 0
    assert result.source_files == 0
    assert result.held_back == 0


async def test_failed_cache_listing_is_counted_and_the_sweep_goes_on(
    session_maker, tmp_path: Path, quiet_listings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        orphan_artifacts,
        "redis_map_document_ids",
        AsyncMock(side_effect=DocumentMapErasureError("cache unreachable")),
    )

    result = await orphan_artifacts.sweep_orphan_artifacts(session_maker, tmp_path)

    assert result.failures == 1
    assert result.documents == 0


async def test_unreadable_source_directory_is_counted_as_a_failure(
    session_maker, tmp_path: Path, quiet_listings
) -> None:
    not_a_directory = tmp_path / "plain-file"
    not_a_directory.write_bytes(b"x")

    result = await orphan_artifacts.sweep_orphan_artifacts(session_maker, not_a_directory)

    assert result.failures == 1
    assert result.source_files == 0


async def test_aged_source_file_alone_holds_back_an_empty_table(
    session_maker, tmp_path: Path, quiet_listings
) -> None:
    source = tmp_path / ("0" * 32)
    source.write_bytes(b"encrypted")
    aged = time.time() - 3600
    os.utime(source, (aged, aged))

    result = await orphan_artifacts.sweep_orphan_artifacts(session_maker, tmp_path)

    assert result.held_back == 1
    assert result.source_files == 0
    assert source.exists()


async def test_missing_source_directory_is_not_a_failure(
    session_maker, tmp_path: Path, quiet_listings
) -> None:
    result = await orphan_artifacts.sweep_orphan_artifacts(session_maker, tmp_path / "absent")

    assert result.failures == 0
    assert result.documents == 0
    assert result.source_files == 0
