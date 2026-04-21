from __future__ import annotations

"""Pytest configuration for septum-api integration tests."""

import asyncio
import os
import tempfile
from pathlib import Path
from typing import AsyncIterator

import pytest

# Set TLDEXTRACT_CACHE before any import that may use tldextract (e.g. Presidio,
# langdetect), so tests do not touch the user cache and avoid permission errors.
if "TLDEXTRACT_CACHE" not in os.environ:
    os.environ["TLDEXTRACT_CACHE"] = tempfile.mkdtemp(prefix="tldextract_cache_")

# Point bootstrap config to a temp directory so tests don't try to write to /app/data/.
if "SEPTUM_CONFIG_PATH" not in os.environ:
    _test_config_dir = tempfile.mkdtemp(prefix="septum_test_config_")
    os.environ["SEPTUM_CONFIG_PATH"] = os.path.join(_test_config_dir, "config.json")

from septum_api.database import build_database_url, engine_is_ready, get_engine, initialize_engine


def _ensure_test_engine() -> None:
    """Initialise the database engine for tests if not already done."""
    if not engine_is_ready():
        url = build_database_url()
        initialize_engine(url)


_ensure_test_engine()


@pytest.fixture(scope="session", autouse=True)
def dispose_async_engine() -> None:
    """Dispose the global async engine before the event loop shuts down.

    This prevents lingering aiosqlite worker threads from attempting to
    use a closed event loop at interpreter shutdown.
    """
    yield
    if engine_is_ready():
        asyncio.run(get_engine().dispose())


@pytest.fixture
async def router_client(tmp_path: Path) -> AsyncIterator["AsyncClient"]:  # noqa: F821
    """FastAPI test client backed by an isolated per-test SQLite database.

    Router tests that need to exercise real DB behaviour (users, auth, etc.)
    use this fixture to get a fresh engine + session override, with the real
    application engine left untouched.
    """
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from septum_api.database import get_db
    from septum_api.main import app
    from septum_api.models import Base

    db_path = tmp_path / "router_test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _override_get_db():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db

    # Disable per-route rate limiting during tests so that repeated
    # calls to login/register across the suite don't trip the limits.
    limiter = getattr(app.state, "limiter", None)
    if limiter is not None:
        limiter.enabled = False

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as http_client:
            yield http_client
    finally:
        if limiter is not None:
            limiter.enabled = True
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()
