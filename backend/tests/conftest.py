from __future__ import annotations

"""Pytest configuration for backend tests."""

import asyncio

import pytest

from app.database import engine


@pytest.fixture(scope="session", autouse=True)
def dispose_async_engine() -> None:
    """Dispose the global async SQLAlchemy engine before the event loop shuts down.

    This prevents lingering aiosqlite worker threads from attempting to use a closed
    event loop at interpreter shutdown, which would otherwise surface as
    PytestUnhandledThreadExceptionWarning.
    """
    yield
    asyncio.run(engine.dispose())

