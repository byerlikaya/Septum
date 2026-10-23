"""Alembic revision 022's PostgreSQL scrub against a real database.

Runs when ``SEPTUM_TEST_POSTGRES_URL`` points at a throwaway database (CI
sets it); skipped otherwise. Each test works in its own schema (or its own
database) and drops it afterwards. Rows are inserted as raw json text so
the bytes PostgreSQL holds, including escapes its json functions cannot
read, are exact.
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from pathlib import Path

import pytest
from audit_scrub_cases import MASKED_QUERY_ROWS, REWRITTEN, SCRUB_ROWS
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from septum_api.models import Base

POSTGRES_URL = os.getenv("SEPTUM_TEST_POSTGRES_URL", "")

pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="SEPTUM_TEST_POSTGRES_URL not set")

_VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
_DOCUMENT_NAME = "022_scrub_audit_events_document_name.py"
_MASKED_QUERY = "023_scrub_audit_events_masked_query.py"


def _revision(filename: str):
    spec = importlib.util.spec_from_file_location(filename.removesuffix(".py"), _VERSIONS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _scrub(db, batch: int | None = None, filename: str = _DOCUMENT_NAME) -> int:
    module = _revision(filename)
    if batch is not None:
        module._BATCH = batch
    return await db.run_sync(lambda session: module.scrub_postgres(session.connection()))


async def _insert(db, extras: list[str]) -> None:
    for extra in extras:
        await db.execute(
            text(
                "INSERT INTO audit_events (created_at, event_type, regulation_ids, "
                "entity_types_detected, entity_count, extra) "
                "VALUES (now(), 'pii_detected', '[]', '{}', 0, CAST(:extra AS json))"
            ),
            {"extra": extra},
        )
    await db.commit()


async def _stored(db) -> list[str]:
    rows = await db.execute(text("SELECT CAST(extra AS TEXT) FROM audit_events ORDER BY id"))
    return list(rows.scalars())


async def _make_tables(engine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@pytest.fixture
async def session_maker():
    schema = f"audit_scrub_{uuid.uuid4().hex[:12]}"
    admin = create_async_engine(POSTGRES_URL)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        POSTGRES_URL, connect_args={"server_settings": {"search_path": schema}}
    )
    await _make_tables(engine)
    try:
        yield async_sessionmaker(bind=engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


@pytest.mark.parametrize("batch", [None, 2])
async def test_postgres_scrub_drops_only_the_name_and_is_idempotent(session_maker, batch):
    async with session_maker() as db:
        assert db.get_bind().dialect.name == "postgresql"
        await _insert(db, [stored for stored, _, _ in SCRUB_ROWS])

        assert await _scrub(db, batch) == REWRITTEN
        await db.commit()
        after_first = await _stored(db)
        assert await _scrub(db, batch) == 0
        await db.commit()

        assert after_first == [postgres for _, postgres, _ in SCRUB_ROWS]
        assert await _stored(db) == after_first


def _nested(depth: int) -> str:
    return "[" * depth + "]" * depth


async def test_postgres_scrub_collapses_duplicate_keys_and_handles_deep_rows(session_maker):
    """Duplicates keep the last value (SQLite differs, so not shared); depth 2000 is scrubbed."""
    async with session_maker() as db:
        await _insert(db, [
            '{"a": 1, "document_name": "x", "a": 2}',
            '{"document_name": "x", "d": ' + _nested(2_000) + "}",
        ])

        assert await _scrub(db) == 2
        await db.commit()
        assert await _stored(db) == ['{"a": 2}', '{"d": ' + _nested(2_000) + "}"]


async def test_postgres_scrub_aborts_on_a_row_beyond_the_parser_depth_limit(session_maker):
    """12,000 levels: PostgreSQL stores it (~16,000 by default), Python's json parser
    does not (~10,000). That parser limit is the only one left; the error names the row id only."""
    async with session_maker() as db:
        await _insert(db, ['{"document_name": "Jane Doe CV.pdf", "d": ' + _nested(12_000) + "}"])

        with pytest.raises(RuntimeError) as raised:
            await _scrub(db)
        message = str(raised.value)
        assert message.startswith("audit event ") and "re-run the upgrade" in message
        assert "Jane" not in message


@pytest.mark.parametrize("batch", [None, 2])
async def test_postgres_masked_query_scrub_after_022_and_idempotent(session_maker, batch):
    """022 then 023, as on a real upgrade: no retired key is left, everything else stays."""
    async with session_maker() as db:
        await _insert(db, [stored for stored, _, _ in MASKED_QUERY_ROWS])

        await _scrub(db, batch)
        rewritten = await _scrub(db, batch, _MASKED_QUERY)
        await db.commit()
        after_first = await _stored(db)
        assert await _scrub(db, batch, _MASKED_QUERY) == 0
        await db.commit()

        assert rewritten == 12
        assert after_first == [postgres for _, postgres, _ in MASKED_QUERY_ROWS]
        assert await _stored(db) == after_first


async def test_postgres_masked_query_scrub_aborts_beyond_the_parser_depth_limit(session_maker):
    async with session_maker() as db:
        await _insert(db, ['{"masked_query": "Hi Jane", "d": ' + _nested(12_000) + "}"])

        with pytest.raises(RuntimeError) as raised:
            await _scrub(db, filename=_MASKED_QUERY)
        message = str(raised.value)
        assert message.startswith("audit event ") and "re-run the upgrade" in message
        assert "Jane" not in message


async def test_postgres_scrub_works_on_a_latin1_database():
    name = f"audit_scrub_latin1_{uuid.uuid4().hex[:12]}"
    admin = create_async_engine(POSTGRES_URL, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(
            f"CREATE DATABASE \"{name}\" ENCODING 'LATIN1' LC_COLLATE 'C' LC_CTYPE 'C' TEMPLATE template0"
        ))
    engine = create_async_engine(make_url(POSTGRES_URL).set(database=name))
    try:
        await _make_tables(engine)
        async with async_sessionmaker(bind=engine)() as db:
            await _insert(db, ['{"document_name": "x", "v": "\\u4e2d\\ud83d\\ude00", "w": "é"}'])

            assert await _scrub(db) == 1
            await db.commit()
            assert await _stored(db) == ['{"v": "\\u4e2d\\ud83d\\ude00", "w": "\\u00e9"}']
    finally:
        await engine.dispose()
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE "{name}"'))
        await admin.dispose()
