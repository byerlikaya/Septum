"""Startup announcement of the setup token (generation, printing, upgrade path)."""

from __future__ import annotations

import json
import logging
import stat
from pathlib import Path
from typing import AsyncIterator

import pytest
from septum_api import bootstrap, main
from septum_api.database import build_default_app_settings
from septum_api.models import Base
from septum_api.models.user import User
from septum_api.utils import auth_dependency
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_TOKEN_PREFIX = "Septum setup token: "


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Give each test its own config.json and no operator-supplied token."""
    monkeypatch.setenv("SEPTUM_CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.delenv(bootstrap.SETUP_TOKEN_ENV, raising=False)
    bootstrap._invalidate_cache()
    yield
    bootstrap._invalidate_cache()


@pytest.fixture
async def configured_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[async_sessionmaker]:
    """Mark infrastructure as configured and back the lifespan check with a fresh SQLite DB."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'setup.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    bootstrap.get_config()
    bootstrap.save_config({"database_configured": True})
    monkeypatch.setattr(auth_dependency, "get_session_maker", lambda: session_maker)
    monkeypatch.setattr(auth_dependency, "engine_is_ready", lambda: True)
    yield session_maker
    await engine.dispose()


def _token_lines(out: str) -> list[str]:
    return [line for line in out.splitlines() if line.startswith(_TOKEN_PREFIX)]


def _stored(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "config.json").read_text())


async def test_fresh_install_prints_exactly_one_token_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main.bootstrap, "needs_setup", lambda: True)
    async with main.lifespan(main.app):
        pass
    lines = _token_lines(capsys.readouterr().out)
    assert len(lines) == 1
    token = lines[0].removeprefix(_TOKEN_PREFIX)
    assert token == _stored(tmp_path)["setup_token"]
    assert stat.S_IMODE((tmp_path / "config.json").stat().st_mode) == 0o600


async def test_restart_keeps_the_same_token(capsys: pytest.CaptureFixture[str]) -> None:
    await main._announce_setup_token()
    first = _token_lines(capsys.readouterr().out)
    bootstrap._invalidate_cache()
    await main._announce_setup_token()
    second = _token_lines(capsys.readouterr().out)
    assert first == second and len(first) == 1


async def test_env_override_wins_and_is_not_printed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(bootstrap.SETUP_TOKEN_ENV, "operator-supplied-token-0123456789abcdef")
    await main._announce_setup_token()
    out = capsys.readouterr().out
    assert _token_lines(out) == []
    assert "operator-supplied-token-0123456789abcdef" not in out
    assert bootstrap.SETUP_TOKEN_ENV in out
    assert "setup_token" not in _stored(tmp_path)
    assert bootstrap.get_setup_token() == "operator-supplied-token-0123456789abcdef"


async def test_configured_db_without_users_is_still_open(
    configured_db, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    await main._announce_setup_token()
    assert len(_token_lines(capsys.readouterr().out)) == 1
    assert _stored(tmp_path)["setup_token"]


async def test_install_with_a_user_gets_no_token(
    configured_db, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    async with configured_db() as db:
        db.add(User(email="admin@example.test", hashed_password="x", role="admin"))
        await db.commit()
    await main._announce_setup_token()
    assert capsys.readouterr().out == ""
    assert "setup_token" not in _stored(tmp_path)


async def test_completed_setup_gets_no_token_and_drops_a_stale_one(
    configured_db, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bootstrap.ensure_setup_token()
    async with configured_db() as db:
        row = build_default_app_settings()
        row.setup_completed = True
        db.add(row)
        await db.commit()
    await main._announce_setup_token()
    assert capsys.readouterr().out == ""
    assert "setup_token" not in _stored(tmp_path)
    assert stat.S_IMODE((tmp_path / "config.json").stat().st_mode) == 0o600


async def test_token_never_reaches_the_logger(
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    caplog.set_level(logging.DEBUG)
    await main._announce_setup_token()
    capsys.readouterr()
    token = bootstrap.get_setup_token()
    assert token
    assert all(token not in record.getMessage() for record in caplog.records)
    assert token not in caplog.text


async def test_token_not_in_setup_status_response(
    router_client, capsys: pytest.CaptureFixture[str]
) -> None:
    await main._announce_setup_token()
    token = bootstrap.get_setup_token()
    response = await router_client.get("/api/setup/status")
    assert token and token not in response.text


async def test_short_env_token_refuses_startup(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(bootstrap.SETUP_TOKEN_ENV, "short-operator-token")
    with pytest.raises(bootstrap.WeakSetupTokenError):
        async with main.lifespan(main.app):
            pass
    assert "short-operator-token" not in capsys.readouterr().out
