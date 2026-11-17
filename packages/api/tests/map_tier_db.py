"""Throwaway SQLite / PostgreSQL stores for the map fence tests, and a probe for a leaked row lock.

PostgreSQL is used only when ``SEPTUM_TEST_POSTGRES_URL`` points at a
throwaway database (``postgresql+asyncpg``); each store gets a schema of
its own.
"""

from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy import text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

POSTGRES_URL = os.getenv("SEPTUM_TEST_POSTGRES_URL", "")
# How long the probe waits for a lock before it counts the row as held.
PROBE_LOCK_WAIT_SECONDS = 1


class MapTierDb:
    """One store: ``maker`` for the code under test, :meth:`row_writable` for a fresh connection."""

    def __init__(self, dialect: str, engine: AsyncEngine, probe_engine: AsyncEngine) -> None:
        self.dialect = dialect
        self.maker = async_sessionmaker(bind=engine, expire_on_commit=False)
        self._probe = async_sessionmaker(bind=probe_engine, expire_on_commit=False)

    async def row_writable(self, document_id: int) -> bool:
        """Whether another connection can write the document row within :data:`PROBE_LOCK_WAIT_SECONDS`.

        ``False`` means a session left the row (PostgreSQL) or the database
        write lock (SQLite) held, i.e. it did not end its transaction.
        """
        from septum_api.models.document import Document

        async with self._probe() as probe:
            try:
                if self.dialect == "postgresql":
                    await probe.execute(
                        text(f"SET LOCAL lock_timeout = '{PROBE_LOCK_WAIT_SECONDS}s'")
                    )
                await probe.execute(
                    update(Document)
                    .where(Document.id == document_id)
                    .values(chunk_count=Document.chunk_count)
                )
                await probe.commit()
            except DBAPIError:
                await probe.rollback()
                return False
        return True

    async def waiting_on_a_lock(self, statement_prefix: str) -> bool:
        """PostgreSQL only: whether a backend of this database waits on a lock running a statement that starts with ``statement_prefix``."""
        async with self._probe() as probe:
            waiting = await probe.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND wait_event_type = 'Lock' "
                    "AND state = 'active' AND pid <> pg_backend_pid() "
                    "AND query LIKE :prefix"
                ),
                {"prefix": statement_prefix + "%"},
            )
        return bool(waiting)


@asynccontextmanager
async def map_tier_db(dialect: str, tmp_path) -> AsyncIterator[MapTierDb]:
    """A fresh store with every table created; disposed (and its schema dropped) on exit."""
    from septum_api.models import Base

    if dialect == "sqlite":
        from septum_api.database import _sqlite_wal_connect
        from sqlalchemy import event

        url = f"sqlite+aiosqlite:///{tmp_path / 'map_tier.db'}"
        engine = create_async_engine(url)
        # The code under test runs with the production connection settings
        # (WAL, 30 s busy timeout); the probe keeps its short timeout.
        event.listen(engine.sync_engine, "connect", _sqlite_wal_connect)
        probe_engine = create_async_engine(url, connect_args={"timeout": PROBE_LOCK_WAIT_SECONDS})
        schema = None
    else:
        schema = f"map_tier_{uuid.uuid4().hex[:12]}"
        admin = create_async_engine(POSTGRES_URL)
        async with admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        settings = {"server_settings": {"search_path": schema}}
        engine = create_async_engine(POSTGRES_URL, connect_args=settings)
        probe_engine = create_async_engine(POSTGRES_URL, connect_args=settings)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield MapTierDb(dialect, engine, probe_engine)
    finally:
        await probe_engine.dispose()
        await engine.dispose()
        if schema is not None:
            async with admin.begin() as conn:
                await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await admin.dispose()
