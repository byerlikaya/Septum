"""Startup seeding syncs built-in regulation ``entity_types`` with their packs."""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

import pytest
from septum_api.database import _builtin_regulations, _seed_defaults
from septum_api.models import Base
from septum_api.models.regulation import RegulationRuleset
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

CUSTOM_ID = "custom_ruleset"
CUSTOM_TYPES = ["EMPLOYEE_ID"]


@pytest.fixture
async def session_maker(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Isolated per-test SQLite database with the full schema created."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'seed.db'}"
    engine = create_async_engine(db_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(bind=engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _load(
    sm: async_sessionmaker[AsyncSession], reg_id: str
) -> RegulationRuleset:
    async with sm() as session:
        return await session.get_one(RegulationRuleset, reg_id)


async def _seed_stale_install(
    sm: async_sessionmaker[AsyncSession],
) -> tuple[str, list[str], str]:
    """Seed, then simulate an older install: a built-in row lacks a type."""
    await _seed_defaults(sm)
    target = _builtin_regulations()[0]
    pack_types = list(target.entity_types)
    dropped = pack_types[-1]
    async with sm() as session:
        row = await session.get(RegulationRuleset, target.id)
        row.entity_types = [t for t in pack_types if t != dropped]
        row.is_active = not row.is_active
        row.custom_notes = "user note"
        session.add(
            RegulationRuleset(
                id=CUSTOM_ID,
                display_name="Custom",
                region="Internal",
                entity_types=list(CUSTOM_TYPES),
                is_builtin=False,
                is_active=True,
            )
        )
        await session.commit()
    return target.id, pack_types, dropped


async def test_stale_builtin_row_regains_pack_types(session_maker) -> None:
    reg_id, pack_types, dropped = await _seed_stale_install(session_maker)
    before = await _load(session_maker, reg_id)
    assert dropped not in before.entity_types

    await _seed_defaults(session_maker)

    after = await _load(session_maker, reg_id)
    assert after.entity_types == pack_types
    assert after.is_active == before.is_active
    assert after.custom_notes == "user note"


async def test_custom_ruleset_untouched(session_maker) -> None:
    await _seed_stale_install(session_maker)

    await _seed_defaults(session_maker)

    custom = await _load(session_maker, CUSTOM_ID)
    assert custom.entity_types == CUSTOM_TYPES
    assert custom.is_active is True
    assert custom.is_builtin is False


async def test_second_seed_run_writes_nothing_to_rulesets(session_maker) -> None:
    await _seed_stale_install(session_maker)
    await _seed_defaults(session_maker)
    first = {r.id: list(r.entity_types) for r in await _all(session_maker)}

    statements: list[str] = []
    sync_engine = session_maker.kw["bind"].sync_engine

    def _capture(_conn, _cursor, statement, *_args) -> None:
        statements.append(statement)

    event.listen(sync_engine, "before_cursor_execute", _capture)
    try:
        await _seed_defaults(session_maker)
    finally:
        event.remove(sync_engine, "before_cursor_execute", _capture)

    writes = [
        s for s in statements
        if "regulation_rulesets" in s
        and s.lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE"))
    ]
    assert writes == []
    assert {r.id: list(r.entity_types) for r in await _all(session_maker)} == first


async def _all(sm: async_sessionmaker[AsyncSession]) -> list[RegulationRuleset]:
    async with sm() as session:
        result = await session.execute(select(RegulationRuleset))
        return list(result.scalars().all())
