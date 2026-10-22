"""``forget_user_in_audit_events`` against real PostgreSQL (JSONB branch).

Runs when ``SEPTUM_TEST_POSTGRES_URL`` points at a throwaway database (CI
sets it); skipped otherwise. Each test works in its own schema and drops
it afterwards.
"""

from __future__ import annotations

import os
import uuid

import pytest
from septum_api.models import Base
from septum_api.models.audit_event import AuditEvent
from septum_api.services.audit_logger import build_document_event, forget_user_in_audit_events
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

POSTGRES_URL = os.getenv("SEPTUM_TEST_POSTGRES_URL", "")

pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="SEPTUM_TEST_POSTGRES_URL not set")


@pytest.fixture
async def session_maker():
    schema = f"audit_unlink_{uuid.uuid4().hex[:12]}"
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


async def test_postgres_unlink_nulls_only_the_leaving_users_language_events(session_maker):
    async with session_maker() as db:
        assert db.get_bind().dialect.name == "postgresql"
        for user_id in (7, 8):
            db.add(build_document_event(
                document_id=1, event_type="document_language_changed",
                extra={"user_id": user_id, "previous_override": None, "override": "de"},
            ))
        db.add(build_document_event(
            document_id=1, event_type="document_uploaded", extra={"user_id": 7},
        ))
        await db.commit()

        assert await forget_user_in_audit_events(db, 7) == 1
        await db.commit()

        rows = await db.execute(select(AuditEvent).order_by(AuditEvent.id))
        assert [(e.event_type, e.extra) for e in rows.scalars()] == [
            ("document_language_changed", {"user_id": None, "previous_override": None, "override": "de"}),
            ("document_language_changed", {"user_id": 8, "previous_override": None, "override": "de"}),
            ("document_uploaded", {"user_id": 7}),
        ]
        assert await forget_user_in_audit_events(db, 7) == 0
