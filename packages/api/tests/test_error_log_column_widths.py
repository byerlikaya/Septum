"""Error log values longer than their columns are cut, never fail the insert.

SQLite (always) checks the stored lengths. PostgreSQL, which enforces
``VARCHAR(n)``, runs when ``SEPTUM_TEST_POSTGRES_URL`` points at a throwaway
database; each test works in its own schema and drops it afterwards.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from septum_api.database import get_db
from septum_api.main import app
from septum_api.models import Base
from septum_api.models.error_log import ErrorLog
from septum_api.services.error_logger import (
    log_backend_error,
    log_backend_message,
    log_frontend_error,
)
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

POSTGRES_URL = os.getenv("SEPTUM_TEST_POSTGRES_URL", "")
LONG_PATH = "/api/" + "p" * 5000
LONG_AGENT = "a" * 5000
LONG_REQUEST_ID = "r" * 5000


def _request() -> Request:
    return Request({
        "type": "http",
        "method": "GET",
        "path": LONG_PATH,
        "raw_path": LONG_PATH.encode(),
        "root_path": "",
        "query_string": b"",
        "scheme": "http",
        "server": ("test", 80),
        "client": ("198.51.100.7", 1234),
        "headers": [(b"user-agent", LONG_AGENT.encode()), (b"x-request-id", LONG_REQUEST_ID.encode())],
    })


@pytest.fixture
async def sqlite_sessions(tmp_path: Path) -> AsyncIterator[async_sessionmaker]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'widths.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(bind=engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def postgres_sessions() -> AsyncIterator[async_sessionmaker]:
    if not POSTGRES_URL:
        pytest.skip("SEPTUM_TEST_POSTGRES_URL not set")
    schema = f"error_log_widths_{uuid.uuid4().hex[:12]}"
    admin = create_async_engine(POSTGRES_URL)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        POSTGRES_URL, connect_args={"server_settings": {"search_path": schema}}
    )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield async_sessionmaker(bind=engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


async def _write_every_kind(session_maker: async_sessionmaker) -> list[ErrorLog]:
    request = _request()
    async with session_maker() as db:
        await log_backend_error(db, request, RuntimeError("boom"), level="ERROR" * 10)
    async with session_maker() as db:
        await log_backend_message(db, request, "handled", method="M" * 100)
    async with session_maker() as db:
        await log_frontend_error(
            db, message="m", stack_trace=None, route=LONG_PATH, user_agent=LONG_AGENT
        )

    async def _override():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = _override
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, client=("198.51.100.8", 1)), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/error-logs/frontend",
                json={"message": "m", "route": LONG_PATH},
                headers={"User-Agent": LONG_AGENT},
            )
        assert resp.status_code == 204
    finally:
        app.dependency_overrides.pop(get_db, None)

    async with session_maker() as db:
        return list((await db.execute(select(ErrorLog).order_by(ErrorLog.id))).scalars())


def _assert_cut(rows: list[ErrorLog]) -> None:
    assert len(rows) == 4
    assert [row.path for row in rows[:2]] == ["<unmatched>"] * 2
    assert all(len(row.path) == 512 for row in rows[2:])
    for row in rows:
        assert len(row.user_agent) == 512
        assert len(row.level) <= 16
    assert all(len(row.request_id) == 128 for row in rows[:2])
    assert len(rows[1].method) == 16


async def test_long_values_are_cut_to_the_column_widths(sqlite_sessions):
    _assert_cut(await _write_every_kind(sqlite_sessions))


async def test_long_values_insert_on_postgres(postgres_sessions):
    _assert_cut(await _write_every_kind(postgres_sessions))


async def _write_with_nul(session_maker: async_sessionmaker) -> ErrorLog:
    async with session_maker() as db:
        await log_frontend_error(
            db,
            message="be\x00fore",
            stack_trace="st\x00ack",
            route="/r\x00oute",
            user_agent="ag\x00ent",
        )
    async with session_maker() as db:
        return (await db.execute(select(ErrorLog))).scalar_one()


def _assert_no_nul(row: ErrorLog) -> None:
    assert (row.message, row.stack_trace, row.path, row.user_agent) == (
        "before", "stack", "/route", "agent"
    )


async def test_nul_characters_are_removed(sqlite_sessions):
    _assert_no_nul(await _write_with_nul(sqlite_sessions))


async def test_nul_characters_insert_on_postgres(postgres_sessions):
    _assert_no_nul(await _write_with_nul(postgres_sessions))


async def test_backend_path_is_the_route_template(sqlite_sessions):
    from fastapi.routing import APIRoute

    async def endpoint(document_id: int) -> None:
        """Stand-in route."""

    request = _request()
    request.scope["route"] = APIRoute("/api/documents/{document_id}", endpoint)
    async with sqlite_sessions() as db:
        await log_backend_message(db, request, "handled")
    async with sqlite_sessions() as db:
        row = (await db.execute(select(ErrorLog))).scalar_one()
    assert row.path == "/api/documents/{document_id}"


class TestUnauthenticatedRejections:
    """401/403 without a resolved identity are counted, not stored."""

    @pytest.fixture
    def stored(self, sqlite_sessions, monkeypatch):
        """Point the exception handler's error log at a fresh database; return a row counter."""
        from septum_api import main
        from sqlalchemy import func

        monkeypatch.setattr(main, "engine_is_ready", lambda: True)
        monkeypatch.setattr(main, "get_session_maker", lambda: sqlite_sessions)

        async def _override():
            async with sqlite_sessions() as session:
                yield session

        monkeypatch.setitem(app.dependency_overrides, get_db, _override)

        async def count() -> int:
            async with sqlite_sessions() as db:
                return (await db.execute(select(func.count()).select_from(ErrorLog))).scalar_one()

        return count

    @staticmethod
    def _counter(status: str) -> float:
        from prometheus_client import REGISTRY

        return REGISTRY.get_sample_value(
            "septum_unauthenticated_rejections_total", {"status": status}
        ) or 0.0

    async def test_anonymous_403_is_counted_not_stored(self, stored):
        before = self._counter("403")
        async with AsyncClient(
            transport=ASGITransport(app=app, client=("198.51.100.30", 1)), base_url="http://test"
        ) as client:
            resp = await client.post("/api/setup/verify-token", headers={"X-Setup-Token": "nope"})
        assert resp.status_code == 403
        assert await stored() == 0
        assert self._counter("403") == before + 1

    async def test_bogus_bearer_token_is_anonymous_and_counted_not_stored(self, stored):
        before = self._counter("401")
        async with AsyncClient(
            transport=ASGITransport(app=app, client=("198.51.100.32", 1)), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/auth/me", headers={"Authorization": "Bearer not.a.jwt"}
            )
        assert resp.status_code == 401
        assert await stored() == 0
        assert self._counter("401") == before + 1

    async def test_rejection_of_an_identified_caller_is_stored(self, stored):
        from septum_api.services.auth import create_access_token

        token = create_access_token(987654, "ghost@example.com")
        async with AsyncClient(
            transport=ASGITransport(app=app, client=("198.51.100.31", 1)), base_url="http://test"
        ) as client:
            resp = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code in (401, 403)
        assert await stored() == 1


class TestErrorLogWriteBudget:
    async def test_identity_past_its_budget_is_counted_not_stored(
        self, sqlite_sessions, monkeypatch
    ):
        from limits import parse
        from prometheus_client import REGISTRY
        from septum_api import main
        from septum_api.middleware import rate_limit
        from septum_api.services.auth import create_access_token
        from sqlalchemy import func

        limiter = rate_limit.get_limiter()
        monkeypatch.setattr(limiter, "enabled", True)
        limiter.reset()
        monkeypatch.setattr(rate_limit, "ERROR_LOG_WRITE_LIMIT", parse("2/minute"))
        monkeypatch.setattr(main, "engine_is_ready", lambda: True)
        monkeypatch.setattr(main, "get_session_maker", lambda: sqlite_sessions)

        async def _override():
            async with sqlite_sessions() as session:
                yield session

        monkeypatch.setitem(app.dependency_overrides, get_db, _override)

        def dropped() -> float:
            return REGISTRY.get_sample_value(
                "septum_error_log_writes_dropped_total", {"status": "401"}
            ) or 0.0

        before = dropped()
        token = create_access_token(424242, "ghost@example.com")
        async with AsyncClient(
            transport=ASGITransport(app=app, client=("198.51.100.40", 1)), base_url="http://test"
        ) as client:
            codes = [
                (await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})).status_code
                for _ in range(3)
            ]
        async with sqlite_sessions() as db:
            rows = (await db.execute(select(func.count()).select_from(ErrorLog))).scalar_one()
        assert codes == [401, 401, 401]
        assert rows == 2
        assert dropped() == before + 1


class TestBudgetInsideTheLogger:
    """Every request-bound row, wherever it is written from, spends the caller's budget."""

    @pytest.fixture
    def budget(self, monkeypatch):
        from limits import parse
        from septum_api.middleware import rate_limit

        limiter = rate_limit.get_limiter()
        monkeypatch.setattr(limiter, "enabled", True)
        limiter.reset()
        monkeypatch.setattr(rate_limit, "ERROR_LOG_WRITE_LIMIT", parse("2/minute"))
        monkeypatch.setattr(rate_limit, "ERROR_LOG_FAULT_WRITE_LIMIT", parse("1/minute"))
        yield limiter
        limiter.reset()

    @staticmethod
    async def _rows(session_maker) -> list[ErrorLog]:
        async with session_maker() as db:
            return list((await db.execute(select(ErrorLog).order_by(ErrorLog.id))).scalars())

    async def test_llm_failure_rows_stop_at_the_budget(self, sqlite_sessions, budget):
        request = _request()
        for _ in range(4):
            async with sqlite_sessions() as db:
                await log_backend_error(db, request, RuntimeError("llm down"), status_code=400)
        assert len(await self._rows(sqlite_sessions)) == 2

    async def test_budget_holds_during_a_storage_outage(self, sqlite_sessions, budget, monkeypatch):
        class Broken:
            async def hit(self, *args, **kwargs):
                raise ConnectionError("down")

            test = hit

        monkeypatch.setattr(budget.buckets, "_primary", Broken())
        request = _request()
        for _ in range(4):
            async with sqlite_sessions() as db:
                await log_backend_message(db, request, "cloud failure", level="ERROR")
        assert len(await self._rows(sqlite_sessions)) == 2

    async def test_server_faults_have_their_own_budget(self, sqlite_sessions, budget):
        request = _request()
        for _ in range(3):
            async with sqlite_sessions() as db:
                await log_backend_message(db, request, "noise", status_code=422)
        async with sqlite_sessions() as db:
            await log_backend_error(db, request, RuntimeError("fault"), status_code=500)
        async with sqlite_sessions() as db:
            await log_backend_error(db, request, RuntimeError("fault again"), status_code=500)
        rows = await self._rows(sqlite_sessions)
        assert [row.status_code for row in rows] == [422, 422, 500]

    async def test_rows_without_a_request_are_not_budgeted(self, sqlite_sessions, budget):
        for _ in range(4):
            async with sqlite_sessions() as db:
                await log_backend_error(db, None, RuntimeError("ingestion"))
        assert len(await self._rows(sqlite_sessions)) == 4


class TestBackgroundAndFailures:
    @pytest.fixture
    def budget(self, monkeypatch):
        from limits import parse
        from septum_api.middleware import rate_limit

        limiter = rate_limit.get_limiter()
        monkeypatch.setattr(limiter, "enabled", True)
        limiter.reset()
        monkeypatch.setattr(rate_limit, "ERROR_LOG_FAULT_WRITE_LIMIT", parse("2/minute"))
        yield limiter
        limiter.reset()

    async def test_background_failures_spend_the_users_budget(self, sqlite_sessions, budget):
        from septum_api.routers import documents as documents_router

        for doc_id in range(4):
            async with sqlite_sessions() as db:
                await documents_router._record_background_failure(
                    db, doc_id, RuntimeError("ingestion failed"), user_id=7
                )
        async with sqlite_sessions() as db:
            await documents_router._record_background_failure(
                db, 99, RuntimeError("ingestion failed"), user_id=8
            )
        async with sqlite_sessions() as db:
            rows = list((await db.execute(select(ErrorLog))).scalars())
        assert len(rows) == 3

    async def test_failed_error_log_write_is_reported_not_silent(
        self, sqlite_sessions, monkeypatch, caplog
    ):
        from prometheus_client import REGISTRY
        from septum_api import main
        from septum_api.services import error_logger
        from septum_api.services.auth import create_access_token

        class BrokenSession:
            async def __aenter__(self):
                raise ConnectionError("db down")

            async def __aexit__(self, *exc):
                return False

        async def _override():
            async with sqlite_sessions() as session:
                yield session

        monkeypatch.setitem(app.dependency_overrides, get_db, _override)
        monkeypatch.setattr(main, "engine_is_ready", lambda: True)
        monkeypatch.setattr(main, "get_session_maker", lambda: BrokenSession)
        before = REGISTRY.get_sample_value("septum_error_log_write_failures_total") or 0.0
        with caplog.at_level("WARNING", logger=error_logger.logger.name):
            async with AsyncClient(
                transport=ASGITransport(app=app, client=("198.51.100.50", 1)), base_url="http://test"
            ) as client:
                token = create_access_token(515151, "ghost@example.com")
                resp = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401
        assert "Error-log persistence failed: ConnectionError" in caplog.text
        assert REGISTRY.get_sample_value("septum_error_log_write_failures_total") == before + 1
