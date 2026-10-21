"""Error log write failures are reported, never fatal; background rows spend a per-user budget."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from limits import parse
from septum_api import main
from septum_api.middleware import rate_limit
from septum_api.models import Base
from septum_api.models.error_log import ErrorLog
from septum_api.routers import documents, settings as settings_router
from septum_api.services import error_logger
from septum_api.services.error_logger import (
    ERROR_LOG_WRITE_FAILURES,
    ERROR_LOG_WRITES_DROPPED,
    log_backend_error,
    report_error_log_failure,
)
from septum_api.services.llm_router import LLMRouter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _failures() -> float:
    return ERROR_LOG_WRITE_FAILURES._value.get()


@pytest.fixture
async def sessions(tmp_path: Path) -> AsyncIterator[async_sessionmaker]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'f.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(bind=engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def budget(monkeypatch):
    limiter = rate_limit.get_limiter()
    monkeypatch.setattr(limiter, "enabled", True)
    limiter.reset()
    monkeypatch.setattr(rate_limit, "ERROR_LOG_WRITE_LIMIT", parse("2/minute"))
    monkeypatch.setattr(rate_limit, "ERROR_LOG_FAULT_WRITE_LIMIT", parse("1/minute"))
    yield limiter
    limiter.reset()


async def _count(sessions: async_sessionmaker) -> int:
    async with sessions() as db:
        return (await db.execute(select(func.count()).select_from(ErrorLog))).scalar_one()


def test_report_logs_the_class_name_only_and_counts(caplog):
    before = _failures()
    with caplog.at_level("WARNING"):
        report_error_log_failure(RuntimeError("secret-dsn-detail"))
    assert "RuntimeError" in caplog.text
    assert "secret-dsn-detail" not in caplog.text
    assert _failures() == before + 1


class TestIdentityBudget:
    async def test_background_rows_stop_at_the_users_budget(self, sessions, budget):
        before = ERROR_LOG_WRITES_DROPPED.labels(status="none")._value.get()
        for _ in range(4):
            async with sessions() as db:
                await log_backend_error(db, None, RuntimeError("x"), status_code=400, identity="user:1")
        assert await _count(sessions) == 2
        assert ERROR_LOG_WRITES_DROPPED.labels(status="400")._value.get() >= 2
        assert ERROR_LOG_WRITES_DROPPED.labels(status="none")._value.get() == before

    async def test_identities_have_separate_budgets(self, sessions, budget):
        for _ in range(3):
            async with sessions() as db:
                await log_backend_error(db, None, RuntimeError("x"), status_code=400, identity="user:1")
        async with sessions() as db:
            await log_backend_error(db, None, RuntimeError("x"), status_code=400, identity="user:2")
        assert await _count(sessions) == 3

    async def test_background_fault_uses_the_smaller_fault_budget(self, sessions, budget):
        for _ in range(3):
            async with sessions() as db:
                await log_backend_error(db, None, RuntimeError("x"), identity="user:1")
        assert await _count(sessions) == 1

    async def test_system_work_without_identity_is_not_budgeted(self, sessions, budget):
        for _ in range(5):
            async with sessions() as db:
                await log_backend_error(db, None, RuntimeError("x"))
        assert await _count(sessions) == 5


async def test_ingestion_recovery_survives_a_failing_error_log(sessions, monkeypatch):
    from septum_api.models.document import Document

    async with sessions() as db:
        doc = Document(
            filename="f", original_filename="f.pdf", file_type="application/pdf", file_format="pdf",
            detected_language="en", encrypted_path="/x", file_size_bytes=1, ocr_confidence=0.0,
            active_regulation_ids=[], ingestion_status="processing", chunk_count=0, user_id=None,
        )
        db.add(doc)
        await db.commit()
        doc_id = doc.id

    monkeypatch.setattr(documents, "log_backend_error", AsyncMock(side_effect=OSError("disk")))
    before = _failures()
    async with sessions() as bg_db:
        await documents._record_background_failure(bg_db, doc_id, ValueError("boom"), 7)
    assert _failures() == before + 1
    async with sessions() as db:
        stored = await db.get(Document, doc_id)
    assert stored.ingestion_status == "failed"


async def test_ingestion_failure_row_is_charged_to_the_owner(sessions, monkeypatch):
    spy = AsyncMock()
    monkeypatch.setattr(documents, "log_backend_error", spy)
    async with sessions() as bg_db:
        await documents._record_background_failure(bg_db, 999, ValueError("boom"), 7)
    assert spy.await_args.kwargs["identity"] == "user:7"
    assert spy.await_args.args[1] is None


async def test_global_handler_still_answers_500_when_logging_fails(monkeypatch):
    monkeypatch.setattr(main, "engine_is_ready", lambda: True)
    monkeypatch.setattr(main, "get_session_maker", lambda: (_ for _ in ()).throw(OSError("db gone")))
    before = _failures()
    request = SimpleNamespace(scope={}, headers={}, client=None, method="GET")
    response = await main.global_exception_handler(request, RuntimeError("boom"))
    assert response.status_code == 500
    assert _failures() == before + 1


async def test_model_refusal_handler_still_answers_503_when_logging_fails(monkeypatch):
    from septum_core.model_integrity import ModelRefusal

    monkeypatch.setattr(main, "engine_is_ready", lambda: True)
    monkeypatch.setattr(main, "get_session_maker", lambda: (_ for _ in ()).throw(OSError("db gone")))
    before = _failures()
    request = SimpleNamespace(scope={}, headers={}, client=None, method="GET")
    response = await main.model_refusal_handler(request, ModelRefusal("m"))
    assert response.status_code == 503
    assert _failures() == before + 1


async def test_settings_cloud_failure_log_error_is_reported_not_raised(monkeypatch):
    async def complete(self, messages, **kwargs):
        await kwargs["on_cloud_failure"]("cloud down", {})
        return "ok"

    monkeypatch.setattr(LLMRouter, "complete", complete)
    monkeypatch.setattr(settings_router, "load_settings", AsyncMock(return_value=SimpleNamespace()))
    monkeypatch.setattr(settings_router, "LLMRouter", lambda s: LLMRouter.__new__(LLMRouter))
    monkeypatch.setattr(settings_router, "log_backend_message", AsyncMock(side_effect=OSError("db")))
    before = _failures()
    result = await settings_router.test_llm_connection_endpoint(
        SimpleNamespace(provider=None, model=None), SimpleNamespace(), db=object(), _user=None
    )
    assert result.ok is False
    assert _failures() == before + 1


@pytest.mark.parametrize("status_code", [400, 503])
async def test_http_exception_handler_keeps_its_response_when_logging_fails(monkeypatch, status_code):
    from septum_api.errors import CodedHTTPError

    monkeypatch.setattr(main, "engine_is_ready", lambda: True)
    monkeypatch.setattr(main, "get_session_maker", lambda: (_ for _ in ()).throw(OSError("db gone")))
    request = SimpleNamespace(
        scope={}, headers={}, client=None, method="POST", state=SimpleNamespace(auth_user_id=5)
    )
    exc = CodedHTTPError(status_code, "visible detail", "some_code")
    before = _failures()

    response = await main.http_exception_handler(request, exc)

    assert response.status_code == status_code
    assert response.body == b'{"detail":"visible detail","code":"some_code"}'
    assert _failures() == before + 1
