from __future__ import annotations

"""Pytest configuration for septum-api integration tests."""

import asyncio
import ipaddress
import os
import socket
import tempfile
from pathlib import Path
from typing import AsyncIterator

import pytest

# No test reaches the Hugging Face Hub or any non-local host. HF_HUB_OFFLINE
# must be set before anything imports huggingface_hub (it is read once, at
# import); socket connects to non-loopback addresses fail loudly. A test that
# really needs the network opts in with ``@pytest.mark.real_network``.
_SESSION_OFFLINE = os.environ.get("HF_HUB_OFFLINE")
os.environ.setdefault("HF_HUB_OFFLINE", "1")

_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex


_blocked: list[str] = []


class NetworkBlocked(ConnectionRefusedError):
    """A test tried to reach a non-loopback host.

    A connection error, so a library with an offline fallback degrades the
    way it would on an air-gapped host; every attempt is still listed in the
    terminal summary.
    """


def _is_local(sock: socket.socket, address: object) -> bool:
    if sock.family == getattr(socket, "AF_UNIX", None):
        return True
    host = address[0] if isinstance(address, tuple) else address
    try:
        return ipaddress.ip_address(str(host).split("%")[0]).is_loopback
    except ValueError:
        return host == "localhost"


def _guarded(real):
    def connect(self: socket.socket, address: object):
        if not _is_local(self, address):
            _blocked.append(repr(address))
            raise NetworkBlocked(
                f"test tried to reach {address!r}; mark it @pytest.mark.real_network"
            )
        return real(self, address)

    return connect


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "real_network: the test may reach the internet (real model downloads)"
    )


def pytest_terminal_summary(terminalreporter) -> None:
    if _blocked:
        terminalreporter.write_sep("-", "network access blocked by the test guard")
        for address in sorted(set(_blocked)):
            terminalreporter.write_line(f"{address} x{_blocked.count(address)}")


@pytest.fixture(autouse=True)
def _no_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Block non-local connects unless the test is marked ``real_network``."""
    if request.node.get_closest_marker("real_network"):
        monkeypatch.setenv("HF_HUB_OFFLINE", _SESSION_OFFLINE or "0")
        return
    monkeypatch.setattr(socket.socket, "connect", _guarded(_real_connect))
    monkeypatch.setattr(socket.socket, "connect_ex", _guarded(_real_connect_ex))

# Point bootstrap config to a temp directory so tests don't try to write to /app/data/.
if "SEPTUM_CONFIG_PATH" not in os.environ:
    _test_config_dir = tempfile.mkdtemp(prefix="septum_test_config_")
    os.environ["SEPTUM_CONFIG_PATH"] = os.path.join(_test_config_dir, "config.json")

# The lifespan's background model warmup would fetch GBs of weights from the
# Hub on a runner without a model cache; tests call _preload_models directly.
os.environ.setdefault("SEPTUM_DISABLE_MODEL_WARMUP", "1")

from septum_api.database import (
    build_database_url,
    engine_is_ready,
    get_engine,
    initialize_engine,
)


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
    from septum_api.database import get_db
    from septum_api.main import app
    from septum_api.models import Base
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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
