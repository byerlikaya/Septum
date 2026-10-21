"""Error log retention: rows past the configured age are purged."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import AsyncIterator
from unittest.mock import patch

import pytest
from septum_api import bootstrap, main
from septum_api.models import Base
from septum_api.models.error_log import ErrorLog
from septum_api.services.error_logger import purge_expired_error_logs
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
async def session_maker(tmp_path: Path) -> AsyncIterator[async_sessionmaker]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'logs.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(bind=engine, expire_on_commit=False)
    await engine.dispose()


async def _add_rows(
    session_maker: async_sessionmaker, ages_in_days: list[int], now: datetime = NOW
) -> None:
    async with session_maker() as db:
        for age in ages_in_days:
            db.add(ErrorLog(source="backend", message=f"age {age}", created_at=now - timedelta(days=age)))
        await db.commit()


async def _remaining(session_maker: async_sessionmaker) -> list[str]:
    async with session_maker() as db:
        return sorted((await db.execute(select(ErrorLog.message))).scalars())


async def test_rows_past_retention_are_deleted(session_maker):
    await _add_rows(session_maker, [1, 89, 91, 400])
    async with session_maker() as db:
        removed = await purge_expired_error_logs(db, 90, now=NOW)
    assert removed == 2
    assert await _remaining(session_maker) == ["age 1", "age 89"]


async def test_purge_runs_in_batches(session_maker):
    await _add_rows(session_maker, [100] * 7 + [1])
    async with session_maker() as db:
        with patch.object(db, "commit", wraps=db.commit) as commit:
            removed = await purge_expired_error_logs(db, 90, now=NOW, batch_size=3)
    assert removed == 7
    assert commit.await_count == 3
    assert await _remaining(session_maker) == ["age 1"]


async def test_zero_retention_keeps_everything(session_maker):
    await _add_rows(session_maker, [1, 400])
    async with session_maker() as db:
        assert await purge_expired_error_logs(db, 0, now=NOW) == 0
    assert len(await _remaining(session_maker)) == 2


async def test_scheduled_purge_uses_configured_days(session_maker, monkeypatch, tmp_path):
    monkeypatch.setenv("SEPTUM_CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setenv("ERROR_LOG_RETENTION_DAYS", "1")
    bootstrap._invalidate_cache()
    await _add_rows(session_maker, [0, 2], now=datetime.now(timezone.utc))
    try:
        with patch.object(main, "engine_is_ready", return_value=True), \
             patch.object(main, "get_session_maker", return_value=session_maker):
            await main._purge_error_logs_once()
    finally:
        bootstrap._invalidate_cache()
    assert await _remaining(session_maker) == ["age 0"]


async def test_scheduled_purge_waits_for_a_database():
    with patch.object(main, "engine_is_ready", return_value=False), \
         patch.object(main, "get_session_maker") as session_maker:
        await main._purge_error_logs_once()
    session_maker.assert_not_called()


def test_retention_defaults_to_ninety_days(monkeypatch, tmp_path):
    monkeypatch.setenv("SEPTUM_CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.delenv("ERROR_LOG_RETENTION_DAYS", raising=False)
    bootstrap._invalidate_cache()
    try:
        assert bootstrap.get_config().error_log_retention_days == 90
    finally:
        bootstrap._invalidate_cache()


async def test_retention_loop_survives_a_failed_run(caplog):
    attempts = []

    async def flaky_purge():
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("secret-dsn-detail")

    async def stop_after_second_tick(_seconds):
        if len(attempts) >= 2:
            raise asyncio.CancelledError

    with patch.object(main, "_purge_error_logs_once", flaky_purge), \
         patch.object(main.asyncio, "sleep", stop_after_second_tick), \
         pytest.raises(asyncio.CancelledError):
        await main._error_log_retention_loop()

    assert len(attempts) == 2
    assert "RuntimeError" in caplog.text
    assert "secret-dsn-detail" not in caplog.text


async def test_disabled_retention_warns_and_stops(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("SEPTUM_CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setenv("ERROR_LOG_RETENTION_DAYS", "0")
    bootstrap._invalidate_cache()
    try:
        with patch.object(main, "_purge_error_logs_once") as purge, caplog.at_level("WARNING"):
            await main._error_log_retention_loop()
    finally:
        bootstrap._invalidate_cache()
    purge.assert_not_called()
    assert "retention is disabled" in caplog.text


@pytest.mark.parametrize(
    ("stored", "expected"),
    [("30", 30), (45, 45), (0, 0), ("not-a-number", 90), (True, 90), (None, 90), (-1, 90), (36501, 90)],
)
def test_config_file_value_is_coerced(monkeypatch, tmp_path, caplog, stored, expected):
    import json

    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "encryption_key": "k", "jwt_secret_key": "j", "error_log_retention_days": stored,
    }))
    monkeypatch.setenv("SEPTUM_CONFIG_PATH", str(path))
    monkeypatch.delenv("ERROR_LOG_RETENTION_DAYS", raising=False)
    bootstrap._invalidate_cache()
    try:
        with caplog.at_level("WARNING"):
            assert bootstrap.get_config().error_log_retention_days == expected
    finally:
        bootstrap._invalidate_cache()
    if expected == 90:
        assert "error_log_retention_days" in caplog.text
        assert "not-a-number" not in caplog.text
        assert "36501" not in caplog.text


def test_invalid_env_value_keeps_the_file_value(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("SEPTUM_CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setenv("ERROR_LOG_RETENTION_DAYS", "ninety")
    bootstrap._invalidate_cache()
    try:
        with caplog.at_level("WARNING"):
            assert bootstrap.get_config().error_log_retention_days == 90
    finally:
        bootstrap._invalidate_cache()
    assert "ERROR_LOG_RETENTION_DAYS" in caplog.text
    assert "ninety" not in caplog.text


def test_engines_hide_bound_parameters_and_aiosqlite_is_quiet():
    import logging

    from septum_api.database import _engine_kwargs
    from septum_api.utils.logging_config import setup_structured_logging

    assert _engine_kwargs("sqlite+aiosqlite:///x.db")["hide_parameters"] is True
    assert _engine_kwargs("postgresql+asyncpg://u@h/db")["hide_parameters"] is True
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    try:
        setup_structured_logging("DEBUG")
        assert logging.getLogger("aiosqlite").getEffectiveLevel() == logging.ERROR
    finally:
        root.handlers[:] = handlers
        root.setLevel(level)



@pytest.mark.parametrize("value", ["0", "43201", "-5"])
def test_out_of_range_jwt_lifetime_is_refused(monkeypatch, tmp_path, caplog, value):
    monkeypatch.setenv("SEPTUM_CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setenv("JWT_EXPIRATION_MINUTES", value)
    bootstrap._invalidate_cache()
    try:
        with caplog.at_level("WARNING"):
            assert bootstrap.get_config().jwt_expiration_minutes == 1440
    finally:
        bootstrap._invalidate_cache()
    assert "Ignoring invalid JWT_EXPIRATION_MINUTES from the environment; using 1440" in caplog.text
