from __future__ import annotations

"""Pytest configuration for backend tests."""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Set TLDEXTRACT_CACHE before any import that may use tldextract (e.g. Presidio,
# langdetect), so tests do not touch the user cache and avoid permission errors.
if "TLDEXTRACT_CACHE" not in os.environ:
    os.environ["TLDEXTRACT_CACHE"] = tempfile.mkdtemp(prefix="tldextract_cache_")

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

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

