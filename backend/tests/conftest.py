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

# Point bootstrap config to a temp directory so tests don't try to write to /app/data/.
if "SEPTUM_CONFIG_PATH" not in os.environ:
    _test_config_dir = tempfile.mkdtemp(prefix="septum_test_config_")
    os.environ["SEPTUM_CONFIG_PATH"] = os.path.join(_test_config_dir, "config.json")

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import build_database_url, engine_is_ready, get_engine, initialize_engine


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
