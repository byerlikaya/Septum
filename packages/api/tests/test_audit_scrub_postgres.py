"""Alembic revision 022's PostgreSQL scrub against a real database.

Runs when ``SEPTUM_TEST_POSTGRES_URL`` points at a throwaway database (CI
sets it); skipped otherwise. The test works in its own schema and drops
it afterwards.
"""

from __future__ import annotations

import importlib.util
import json
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from septum_api.models import Base
from septum_api.models.audit_event import AuditEvent

POSTGRES_URL = os.getenv("SEPTUM_TEST_POSTGRES_URL", "")

pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="SEPTUM_TEST_POSTGRES_URL not set")

_REVISION = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "022_scrub_audit_events_document_name.py"
)


def _revision():
    spec = importlib.util.spec_from_file_location("revision_022", _REVISION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _scrub(db) -> int:
    scrub_postgres = _revision().scrub_postgres
    return await db.run_sync(lambda session: scrub_postgres(session.connection()))


def _event(extra):
    return AuditEvent(
        event_type="pii_detected", document_id=1, regulation_ids=[],
        entity_types_detected={}, entity_count=0, extra=extra,
    )


@pytest.fixture
async def session_maker():
    schema = f"audit_scrub_{uuid.uuid4().hex[:12]}"
    admin = create_async_engine(POSTGRES_URL)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        POSTGRES_URL, connect_args={"server_settings": {"search_path": schema}}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(bind=engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


async def test_postgres_scrub_drops_only_the_name_and_is_idempotent(session_maker):
    async with session_maker() as db:
        assert db.get_bind().dialect.name == "postgresql"
        db.add_all([
            _event({"document_name": "Jane Doe CV.pdf", "source": "chat_query"}),
            _event({"document_name": None, "strategy": "simple"}),
            _event({"source": "document_ingestion"}),
            _event(["document_name"]),
            _event(None),
            _event({"document_name": "x.pdf", "note": "a\u0000b", "z": 1, "a": 2}),
            _event({"note": "a\u0000b"}),
            _event({"z": 1, "document_name": "y.pdf", "a": 2}),
        ])
        await db.commit()

        assert await _scrub(db) == 4
        assert await _scrub(db) == 0
        await db.commit()

        rows = await db.execute(select(AuditEvent.extra).order_by(AuditEvent.id))
        assert list(rows.scalars()) == [
            {"source": "chat_query"},
            {"strategy": "simple"},
            {"source": "document_ingestion"},
            ["document_name"],
            None,
            {"note": "a\u0000b", "z": 1, "a": 2},
            {"note": "a\u0000b"},
            {"z": 1, "a": 2},
        ]
        texts = (await db.execute(text("SELECT extra::text FROM audit_events ORDER BY id"))).scalars().all()
        assert [list(json.loads(texts[i])) for i in (5, 7)] == [["note", "z", "a"], ["z", "a"]]


async def test_postgres_scrub_survives_a_nul_row_that_holds_no_name(session_maker):
    async with session_maker() as db:
        db.add_all([_event({"note": "a\u0000b"}), _event({"document_name": "x.pdf", "k": 1})])
        await db.commit()

        assert await _scrub(db) == 1
        await db.commit()
        rows = await db.execute(select(AuditEvent.extra).order_by(AuditEvent.id))
        assert list(rows.scalars()) == [{"note": "a\u0000b"}, {"k": 1}]


async def test_postgres_scrub_leaves_an_empty_object_when_only_the_name_was_stored(session_maker):
    async with session_maker() as db:
        db.add(_event({"document_name": "x.pdf"}))
        await db.commit()

        assert await _scrub(db) == 1
        await db.commit()
        assert (await db.execute(select(AuditEvent.extra))).scalar_one() == {}
