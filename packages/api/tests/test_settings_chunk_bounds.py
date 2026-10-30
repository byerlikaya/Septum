"""Chunk settings stay within what ingestion and the approval flow can handle."""

from __future__ import annotations

import asyncio
import contextlib
import os
import sqlite3
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from pydantic import ValidationError
from septum_api.database import build_default_app_settings
from septum_api.models import Base
from septum_api.models.settings import AppSettings
from septum_api.routers import settings as settings_router
from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from septum_api.request_limits import (
    MAX_PRE_APPROVED_CHUNK_CHARS,
    MIN_CHUNK_SIZE,
    MIN_PDF_CHUNK_SIZE,
)
from septum_api.routers.settings import SettingsUpdatePayload

POSTGRES_URL = os.getenv("SEPTUM_TEST_POSTGRES_URL", "")

TEXT_SIZE_LIMITS = {
    "chunk_size": MIN_CHUNK_SIZE,
    "pdf_chunk_size": MIN_PDF_CHUNK_SIZE,
}
WINDOW_FIELDS = ["audio_chunk_size", "spreadsheet_chunk_size"]
CHUNK_FIELDS = [*TEXT_SIZE_LIMITS, *WINDOW_FIELDS, "chunk_overlap"]


@pytest.mark.parametrize("field,minimum", TEXT_SIZE_LIMITS.items())
def test_text_chunk_sizes_below_the_floor_are_refused(field: str, minimum: int) -> None:
    with pytest.raises(ValidationError):
        SettingsUpdatePayload(**{field: minimum - 1})


@pytest.mark.parametrize("field,minimum", TEXT_SIZE_LIMITS.items())
def test_text_chunk_sizes_within_bounds_are_accepted(field: str, minimum: int) -> None:
    for value in (minimum, MAX_PRE_APPROVED_CHUNK_CHARS):
        assert getattr(SettingsUpdatePayload(**{field: value}), field) == value


@pytest.mark.parametrize("field", [*TEXT_SIZE_LIMITS, *WINDOW_FIELDS])
def test_chunk_sizes_above_the_approval_limit_are_refused(field: str) -> None:
    with pytest.raises(ValidationError):
        SettingsUpdatePayload(**{field: MAX_PRE_APPROVED_CHUNK_CHARS + 1})


@pytest.mark.parametrize("field", WINDOW_FIELDS)
def test_window_sizes_keep_their_floor_of_one(field: str) -> None:
    assert getattr(SettingsUpdatePayload(**{field: 1}), field) == 1
    with pytest.raises(ValidationError):
        SettingsUpdatePayload(**{field: 0})


@pytest.mark.parametrize("value", [-1, MAX_PRE_APPROVED_CHUNK_CHARS // 2 + 1])
def test_out_of_range_overlap_is_refused(value: int) -> None:
    with pytest.raises(ValidationError):
        SettingsUpdatePayload(chunk_overlap=value)


@pytest.mark.parametrize("value", [0, 200, MAX_PRE_APPROVED_CHUNK_CHARS // 2])
def test_in_range_overlap_is_accepted(value: int) -> None:
    assert SettingsUpdatePayload(chunk_overlap=value).chunk_overlap == value


@pytest.mark.parametrize("field", CHUNK_FIELDS)
def test_explicit_null_chunk_field_is_refused(field: str) -> None:
    with pytest.raises(ValidationError):
        SettingsUpdatePayload(**{field: None})


def test_omitted_chunk_fields_stay_unset() -> None:
    assert SettingsUpdatePayload().model_dump(exclude_unset=True) == {}


async def _patch(client: AsyncClient, body: dict):
    return await client.patch("/api/settings", json=body)


async def test_oversized_chunk_size_is_422_over_http(router_client: AsyncClient) -> None:
    resp = await _patch(router_client, {"chunk_size": MAX_PRE_APPROVED_CHUNK_CHARS + 1})
    assert resp.status_code == 422


async def test_null_chunk_size_is_422_over_http(router_client: AsyncClient) -> None:
    resp = await _patch(router_client, {"chunk_size": None})
    assert resp.status_code == 422


async def test_overlap_above_half_the_stored_chunk_size_is_422(router_client: AsyncClient) -> None:
    await _patch(router_client, {"chunk_size": 800, "chunk_overlap": 200})

    resp = await _patch(router_client, {"chunk_overlap": 401})

    assert resp.status_code == 422
    assert (await _patch(router_client, {})).json()["chunk_overlap"] == 200


async def test_chunk_size_below_twice_the_stored_overlap_is_422(router_client: AsyncClient) -> None:
    await _patch(router_client, {"chunk_size": 800, "chunk_overlap": 200})

    resp = await _patch(router_client, {"chunk_size": 399})

    assert resp.status_code == 422
    assert (await _patch(router_client, {})).json()["chunk_size"] == 800


async def test_largest_window_with_half_overlap_is_accepted(router_client: AsyncClient) -> None:
    body = {
        "chunk_size": MAX_PRE_APPROVED_CHUNK_CHARS,
        "chunk_overlap": MAX_PRE_APPROVED_CHUNK_CHARS // 2,
    }
    resp = await _patch(router_client, body)

    assert resp.status_code == 200
    assert resp.json()["chunk_size"] == body["chunk_size"]
    assert resp.json()["chunk_overlap"] == body["chunk_overlap"]


async def test_overlap_and_size_changed_together_are_checked_together(
    router_client: AsyncClient,
) -> None:
    await _patch(router_client, {"chunk_size": 800, "chunk_overlap": 200})

    resp = await _patch(router_client, {"chunk_size": 2000, "chunk_overlap": 1000})

    assert resp.status_code == 200


@pytest.fixture
def rival_settings_write(tmp_path: Path):
    """Commit ``sql`` from another connection just before the request's ``UPDATE app_settings``."""
    installed: list = []

    def install(sql: str) -> list[bool]:
        fired: list[bool] = []

        def before(conn, cursor, statement, parameters, context, executemany):
            if fired or not statement.lstrip().upper().startswith("UPDATE APP_SETTINGS"):
                return
            fired.append(True)
            with contextlib.closing(sqlite3.connect(tmp_path / "router_test.db", timeout=1)) as rival:
                rival.execute(sql)
                rival.commit()

        event.listen(Engine, "before_cursor_execute", before)
        installed.append(before)
        return fired

    yield install
    for listener in installed:
        event.remove(Engine, "before_cursor_execute", listener)


async def test_overlap_racing_a_committed_chunk_size_change_is_422(
    router_client: AsyncClient, tmp_path: Path, rival_settings_write
) -> None:
    await _patch(router_client, {"chunk_size": 800, "chunk_overlap": 200})
    fired = rival_settings_write("UPDATE app_settings SET chunk_size = 100")

    resp = await _patch(router_client, {"chunk_overlap": 99})

    assert fired
    assert resp.status_code == 422
    with contextlib.closing(sqlite3.connect(tmp_path / "router_test.db")) as conn:
        stored = conn.execute("SELECT chunk_size, chunk_overlap FROM app_settings").fetchone()
    assert stored == (100, 200)


async def test_chunk_size_racing_a_committed_overlap_change_is_422(
    router_client: AsyncClient, tmp_path: Path, rival_settings_write
) -> None:
    await _patch(router_client, {"chunk_size": 20_000, "chunk_overlap": 50})
    fired = rival_settings_write("UPDATE app_settings SET chunk_overlap = 99")

    resp = await _patch(router_client, {"chunk_size": 100})

    assert fired
    assert resp.status_code == 422
    with contextlib.closing(sqlite3.connect(tmp_path / "router_test.db")) as conn:
        stored = conn.execute("SELECT chunk_size, chunk_overlap FROM app_settings").fetchone()
    assert stored == (20_000, 99)


@pytest.fixture
async def postgres_session_maker():
    """Yield a READ COMMITTED session maker bound to a throwaway schema."""
    schema = f"chunk_window_{uuid.uuid4().hex[:12]}"
    admin = create_async_engine(POSTGRES_URL)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        POSTGRES_URL,
        isolation_level="READ COMMITTED",
        connect_args={"server_settings": {"search_path": schema}},
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


@pytest.mark.skipif(not POSTGRES_URL, reason="SEPTUM_TEST_POSTGRES_URL not set")
async def test_racing_size_and_overlap_patches_on_postgres_keep_a_valid_window(
    postgres_session_maker, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with postgres_session_maker() as db:
        db.add(build_default_app_settings())
        await db.commit()
        settings = await db.get(AppSettings, 1)
        settings.chunk_size, settings.chunk_overlap = 20_000, 50
        await db.commit()

    both_validated = asyncio.Barrier(2)
    real_check = settings_router._require_chunk_overlap_within_half

    async def write_after_both_validate(db, settings_id):
        await both_validated.wait()
        return await real_check(db, settings_id)

    monkeypatch.setattr(
        settings_router, "_require_chunk_overlap_within_half", write_after_both_validate
    )

    async def patch(body: dict) -> int:
        async with postgres_session_maker() as db:
            try:
                await settings_router.update_settings_endpoint(SettingsUpdatePayload(**body), db, None)
            except HTTPException as exc:
                return exc.status_code
            return 200

    codes = await asyncio.gather(patch({"chunk_size": 100}), patch({"chunk_overlap": 99}))

    assert sorted(codes) == [200, 422]
    async with postgres_session_maker() as db:
        stored = await db.get(AppSettings, 1)
        assert stored.chunk_overlap <= stored.chunk_size // 2
