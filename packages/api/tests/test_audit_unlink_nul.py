"""``forget_user_in_audit_events`` on ``extra`` values SQL cannot read safely.

Runs on SQLite and, when ``SEPTUM_TEST_POSTGRES_URL`` is set, on PostgreSQL
in a throwaway schema; no stored value may fail a user deletion.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from septum_api.models import Base
from septum_api.models.audit_event import AuditEvent
from septum_api.models.user import User
from septum_api.routers.users import delete_user
from septum_api.services.audit_logger import build_document_event, forget_user_in_audit_events
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

POSTGRES_URL = os.getenv("SEPTUM_TEST_POSTGRES_URL", "")

LANGUAGE_CHANGED = "document_language_changed"
NUL_NAME = "scan\x00ned.pdf"
SURROGATE_NAME = "scan\udc80ned.pdf"


@asynccontextmanager
async def _sqlite_engine(tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'audit.db'}")
    try:
        yield engine
    finally:
        await engine.dispose()


@asynccontextmanager
async def _postgres_engine() -> AsyncIterator[AsyncEngine]:
    schema = f"audit_unlink_nul_{uuid.uuid4().hex[:12]}"
    admin = create_async_engine(POSTGRES_URL)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        POSTGRES_URL, connect_args={"server_settings": {"search_path": schema}}
    )
    try:
        yield engine
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


@pytest.fixture(params=["sqlite", "postgresql"])
async def session_maker(request, tmp_path: Path):
    if request.param == "postgresql" and not POSTGRES_URL:
        pytest.skip("SEPTUM_TEST_POSTGRES_URL not set")
    engines = _sqlite_engine(tmp_path) if request.param == "sqlite" else _postgres_engine()
    async with engines as engine:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield async_sessionmaker(bind=engine, expire_on_commit=False)


def _event(event_type: str, user_id: object, name: str | None = None) -> AuditEvent:
    extra = {"user_id": user_id, "previous_override": None, "override": "de"}
    if name is not None:
        extra["name"] = name
    return build_document_event(document_id=1, event_type=event_type, extra=extra)


# Lists of pairs, not dicts, so the assertions also pin the key order.
def _items(user_id: object, name: str | None = None) -> list[tuple]:
    pairs = [("user_id", user_id), ("previous_override", None), ("override", "de")]
    return pairs + ([("name", name)] if name is not None else [])


async def _extras(db) -> list[list[tuple]]:
    rows = await db.execute(select(AuditEvent.extra).order_by(AuditEvent.id))
    return [list(extra.items()) for extra in rows.scalars()]


async def _texts(db) -> list[str]:
    rows = await db.execute(text("SELECT CAST(extra AS TEXT) FROM audit_events ORDER BY id"))
    return list(rows.scalars())


async def test_mixed_rows_are_unlinked_in_one_call_and_the_rest_left_byte_for_byte(session_maker):
    rows = [
        (LANGUAGE_CHANGED, 7, NUL_NAME, True),
        (LANGUAGE_CHANGED, 7, SURROGATE_NAME, True),
        (LANGUAGE_CHANGED, 7, None, True),
        (LANGUAGE_CHANGED, "7", None, True),
        (LANGUAGE_CHANGED, 8, NUL_NAME, False),
        (LANGUAGE_CHANGED, 8, SURROGATE_NAME, False),
        (LANGUAGE_CHANGED, 17, None, False),
        (LANGUAGE_CHANGED, "abc", None, False),
        (LANGUAGE_CHANGED, 7.0, None, False),
        (LANGUAGE_CHANGED, True, None, False),
        ("document_uploaded", 7, NUL_NAME, False),
        ("document_uploaded", 7, SURROGATE_NAME, False),
    ]
    async with session_maker() as db:
        db.add_all([_event(event_type, user_id, name) for event_type, user_id, name, _ in rows])
        await db.commit()
        before = await _texts(db)

        assert await forget_user_in_audit_events(db, 7) == 4
        await db.commit()

        assert await _extras(db) == [
            _items(None if unlinked else user_id, name) for _, user_id, name, unlinked in rows
        ]
        after = await _texts(db)
        kept = [index for index, row in enumerate(rows) if not row[3]]
        assert [after[i] for i in kept] == [before[i] for i in kept]
        assert await forget_user_in_audit_events(db, 7) == 0
        assert await _texts(db) == after


async def test_a_user_named_nowhere_changes_nothing(session_maker):
    async with session_maker() as db:
        db.add_all([_event(LANGUAGE_CHANGED, 8, NUL_NAME), _event(LANGUAGE_CHANGED, 8)])
        await db.commit()
        before = await _texts(db)

        assert await forget_user_in_audit_events(db, 7) == 0
        await db.commit()

        assert await _texts(db) == before


async def test_deleting_a_user_named_in_unreadable_rows_succeeds(session_maker):
    async with session_maker() as db:
        admin = User(email="admin@example.com", hashed_password="h", role="admin", is_active=True)
        leaving = User(email="leaving@example.com", hashed_password="h", role="viewer", is_active=True)
        db.add_all([admin, leaving])
        await db.commit()
        db.add_all([
            _event(LANGUAGE_CHANGED, leaving.id, NUL_NAME),
            _event(LANGUAGE_CHANGED, leaving.id, SURROGATE_NAME),
            _event(LANGUAGE_CHANGED, "abc"),
            _event(LANGUAGE_CHANGED, admin.id),
        ])
        await db.commit()

        await delete_user(user_id=leaving.id, current_user=admin, db=db)

        assert await db.scalar(select(User.id).where(User.id == leaving.id)) is None
        assert await _extras(db) == [
            _items(None, NUL_NAME), _items(None, SURROGATE_NAME), _items("abc"), _items(admin.id),
        ]
