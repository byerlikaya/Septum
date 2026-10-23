"""SQLite startup must bring both fresh and pre-existing databases to the ORM schema."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest
import spreadsheet_dedupe_cases as dedupe_cases
import sqlalchemy as sa
from sqlalchemy import event
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


def _audit_event_indexes(db_path: Path) -> dict[str, list[str]]:
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        return {i["name"]: i["column_names"] for i in sa.inspect(engine).get_indexes("audit_events")}
    finally:
        engine.dispose()


async def test_fresh_sqlite_has_audit_events_document_event_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "fresh_index.db"
    await _init_db_at(db_path, monkeypatch)

    indexes = _audit_event_indexes(db_path)
    assert indexes["ix_audit_events_document_event_id"] == ["document_id", "event_type", "id"]
    assert "ix_audit_events_document_id" not in indexes


async def test_existing_sqlite_without_document_event_index_gains_it_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "pre_index.db"
    engine = sa.create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(sa.text("DROP INDEX ix_audit_events_document_event_id"))
        conn.execute(
            sa.text(
                "INSERT INTO audit_events (created_at, event_type, document_id, regulation_ids, "
                "entity_types_detected, entity_count) "
                "VALUES ('2026-01-01', 'document_language_changed', 1, '[]', '{}', 0)"
            )
        )
    engine.dispose()
    assert "ix_audit_events_document_event_id" not in _audit_event_indexes(db_path)

    await _init_db_at(db_path, monkeypatch)
    await _init_db_at(db_path, monkeypatch)

    indexes = _audit_event_indexes(db_path)
    assert indexes["ix_audit_events_document_event_id"] == ["document_id", "event_type", "id"]
    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT COUNT(*) FROM audit_events")).scalar_one() == 1
    engine.dispose()


async def test_existing_sqlite_with_single_column_document_index_loses_it_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "pre_drop.db"
    engine = sa.create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE INDEX ix_audit_events_document_id ON audit_events (document_id)"))
        conn.execute(
            sa.text(
                "INSERT INTO audit_events (created_at, event_type, document_id, regulation_ids, "
                "entity_types_detected, entity_count) "
                "VALUES ('2026-01-01', 'pii_detected', 1, '[]', '{}', 0)"
            )
        )
    engine.dispose()
    assert "ix_audit_events_document_id" in _audit_event_indexes(db_path)

    await _init_db_at(db_path, monkeypatch)
    await _init_db_at(db_path, monkeypatch)

    indexes = _audit_event_indexes(db_path)
    assert "ix_audit_events_document_id" not in indexes
    assert indexes["ix_audit_events_document_event_id"] == ["document_id", "event_type", "id"]
    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT COUNT(*) FROM audit_events")).scalar_one() == 1
    engine.dispose()


@contextmanager
def _counting_dedupe_statements() -> Iterator[list[str]]:
    """Record every spreadsheet dedupe statement any engine executes meanwhile."""
    seen: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany) -> None:
        if statement in database._SPREADSHEET_DEDUPE_STATEMENTS:
            seen.append(statement)

    event.listen(sa.Engine, "before_cursor_execute", _record)
    try:
        yield seen
    finally:
        event.remove(sa.Engine, "before_cursor_execute", _record)


async def test_existing_sqlite_with_duplicate_spreadsheet_rows_is_deduped_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    db_path = tmp_path / "pre_unique_schema.db"
    engine = sa.create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(sa.text("DROP INDEX uq_spreadsheet_schemas_document_id"))
        conn.execute(sa.text("DROP INDEX uq_spreadsheet_columns_schema_id_index"))
        conn.execute(
            sa.text("CREATE INDEX ix_spreadsheet_schemas_document_id ON spreadsheet_schemas (document_id)")
        )
        conn.execute(
            sa.text("CREATE INDEX ix_spreadsheet_columns_schema_id ON spreadsheet_columns (schema_id)")
        )
        dedupe_cases.seed(conn)
    engine.dispose()

    with caplog.at_level("WARNING", logger=database.logger.name), _counting_dedupe_statements() as first_run:
        await _init_db_at(db_path, monkeypatch)
    first_log = caplog.text
    caplog.clear()
    with caplog.at_level("WARNING", logger=database.logger.name), _counting_dedupe_statements() as second_run:
        await _init_db_at(db_path, monkeypatch)

    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        stored = dedupe_cases.stored(conn)
        indexes = dedupe_cases.spreadsheet_indexes(conn)
    engine.dispose()

    assert stored == dedupe_cases.EXPECTED
    assert dedupe_cases.EXPECTED_LOG in first_log
    assert "Spreadsheet schema dedupe" not in caplog.text
    assert len(first_run) == len(database._SPREADSHEET_DEDUPE_STATEMENTS)
    assert second_run == []
    assert indexes == dedupe_cases.UNIQUE_INDEXES


async def test_fresh_sqlite_has_unique_spreadsheet_indexes_without_a_dedupe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "fresh_unique.db"
    with _counting_dedupe_statements() as seen:
        await _init_db_at(db_path, monkeypatch)

    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        indexes = dedupe_cases.spreadsheet_indexes(conn)
    engine.dispose()
    assert indexes == dedupe_cases.UNIQUE_INDEXES
    assert seen == []
