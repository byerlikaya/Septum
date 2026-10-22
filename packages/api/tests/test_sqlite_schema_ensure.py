"""SQLite startup must bring both fresh and pre-existing databases to the ORM schema."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import septum_api.database as database
from septum_api.models import Base
from septum_api.models.document import Document


async def _init_db_at(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setattr(database, "_engine", engine)
    monkeypatch.setattr(database, "_session_maker", async_sessionmaker(bind=engine, expire_on_commit=False))
    try:
        await database.init_db()
    finally:
        await engine.dispose()


def _document_columns(db_path: Path) -> dict[str, dict]:
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        return {c["name"]: c for c in sa.inspect(engine).get_columns("documents")}
    finally:
        engine.dispose()


async def test_fresh_sqlite_has_sanitized_entity_types(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "fresh.db"
    await _init_db_at(db_path, monkeypatch)

    assert _document_columns(db_path)["sanitized_entity_types"]["nullable"] is True


async def test_existing_sqlite_without_column_gains_it_and_keeps_rows_null(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "legacy.db"
    engine = sa.create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(sa.text("ALTER TABLE documents DROP COLUMN sanitized_entity_types"))
        conn.execute(
            sa.text(
                "INSERT INTO documents (filename, original_filename, file_type, file_format, "
                "detected_language, uploaded_at, encrypted_path, chunk_count, entity_count, "
                "ingestion_status, file_size_bytes, active_regulation_ids) "
                "VALUES ('a', 'a', 'text/plain', 'txt', 'en', '2026-01-01', 'p', 0, 0, "
                "'completed', 1, '[]')"
            )
        )
    engine.dispose()
    assert "sanitized_entity_types" not in _document_columns(db_path)

    await _init_db_at(db_path, monkeypatch)
    await _init_db_at(db_path, monkeypatch)

    assert _document_columns(db_path)["sanitized_entity_types"]["nullable"] is True
    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        values = conn.execute(sa.text("SELECT sanitized_entity_types FROM documents")).all()
    engine.dispose()
    assert values == [(None,)]


async def test_existing_sqlite_without_policy_digest_gains_it_and_keeps_rows_null(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "pre_digest.db"
    engine = sa.create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(sa.text("ALTER TABLE documents DROP COLUMN sanitized_policy_digest"))
        conn.execute(
            sa.text(
                "INSERT INTO documents (filename, original_filename, file_type, file_format, "
                "detected_language, uploaded_at, encrypted_path, chunk_count, entity_count, "
                "ingestion_status, file_size_bytes, active_regulation_ids) "
                "VALUES ('a', 'a', 'text/plain', 'txt', 'en', '2026-01-01', 'p', 0, 0, "
                "'completed', 1, '[]')"
            )
        )
    engine.dispose()

    await _init_db_at(db_path, monkeypatch)
    await _init_db_at(db_path, monkeypatch)

    assert _document_columns(db_path)["sanitized_policy_digest"]["nullable"] is True
    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        values = conn.execute(sa.text("SELECT sanitized_policy_digest FROM documents")).all()
    engine.dispose()
    assert values == [(None,)]


async def test_existing_sqlite_without_sanitized_language_gains_it_and_round_trips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "pre_language.db"
    engine = sa.create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(sa.text("ALTER TABLE documents DROP COLUMN sanitized_language"))
        conn.execute(
            sa.text(
                "INSERT INTO documents (filename, original_filename, file_type, file_format, "
                "detected_language, uploaded_at, encrypted_path, chunk_count, entity_count, "
                "ingestion_status, file_size_bytes, active_regulation_ids) "
                "VALUES ('a', 'a', 'text/plain', 'txt', 'en', '2026-01-01', 'p', 0, 0, "
                "'completed', 1, '[]')"
            )
        )
    engine.dispose()
    assert "sanitized_language" not in _document_columns(db_path)

    await _init_db_at(db_path, monkeypatch)
    await _init_db_at(db_path, monkeypatch)

    assert _document_columns(db_path)["sanitized_language"]["nullable"] is True
    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        values = conn.execute(sa.text("SELECT sanitized_language FROM documents")).all()
    engine.dispose()
    assert values == [(None,)]

    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_maker = async_sessionmaker(bind=async_engine, expire_on_commit=False)
    try:
        async with session_maker() as session:
            document = await session.get(Document, 1)
            assert document is not None and document.sanitized_language is None
            document.sanitized_language = "zh"
            await session.commit()
        async with session_maker() as session:
            assert (await session.get(Document, 1)).sanitized_language == "zh"
    finally:
        await async_engine.dispose()
