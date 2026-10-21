"""First-admin creation under real PostgreSQL concurrency.

Runs when ``SEPTUM_TEST_POSTGRES_URL`` points at a throwaway database (CI
sets it); skipped otherwise. Each test works in its own schema and drops
it afterwards, so the shared database stays as the migration test left it.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from septum_api.models import Base
from septum_api.models.user import User
from septum_api.routers.auth import _insert_first_admin
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

POSTGRES_URL = os.getenv("SEPTUM_TEST_POSTGRES_URL", "")

pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="SEPTUM_TEST_POSTGRES_URL not set")

CONTENDERS = 5


@pytest.fixture
async def schema_engine_factory():
    """Yield a factory for engines bound to a fresh schema with the ORM tables."""
    schema = f"first_admin_{uuid.uuid4().hex[:12]}"
    admin = create_async_engine(POSTGRES_URL)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engines = []

    def factory(isolation_level: str):
        engine = create_async_engine(
            POSTGRES_URL,
            isolation_level=isolation_level,
            connect_args={"server_settings": {"search_path": schema}},
        )
        engines.append(engine)
        return engine

    setup = factory("READ COMMITTED")
    async with setup.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield factory
    finally:
        for engine in engines:
            await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


@pytest.mark.parametrize("isolation_level", ["READ COMMITTED", "REPEATABLE READ"])
async def test_parallel_first_admins_create_exactly_one(schema_engine_factory, isolation_level):
    engine = schema_engine_factory(isolation_level)
    session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    all_read = asyncio.Barrier(CONTENDERS)

    async def contender(index: int) -> bool:
        async with session_maker() as db:
            # Same read the setup gate does first; under REPEATABLE READ it
            # would pin a snapshot in which the table is still empty.
            await db.execute(select(func.count()).select_from(User))
            await all_read.wait()
            created = await _insert_first_admin(db, f"admin{index}@example.com", "h")
            await db.commit()
            return created

    results = await asyncio.gather(*(contender(i) for i in range(CONTENDERS)))

    assert results.count(True) == 1
    async with session_maker() as db:
        assert (await db.execute(select(func.count()).select_from(User))).scalar_one() == 1
