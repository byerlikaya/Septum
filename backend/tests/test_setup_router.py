"""Tests for the setup wizard router."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.bootstrap import _invalidate_cache
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolated_bootstrap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolate bootstrap config for each test."""
    config_path = tmp_path / "config.json"
    monkeypatch.setenv("SEPTUM_CONFIG_PATH", str(config_path))
    for env_var in (
        "DATABASE_URL", "DB_PATH", "REDIS_URL", "ENCRYPTION_KEY",
        "JWT_SECRET_KEY", "JWT_EXPIRATION_MINUTES", "LOG_LEVEL",
        "RATE_LIMIT_DEFAULT", "FRONTEND_ORIGIN",
    ):
        monkeypatch.delenv(env_var, raising=False)
    _invalidate_cache()
    yield
    _invalidate_cache()


class TestSetupStatus:
    """GET /api/setup/status."""

    def test_needs_infrastructure_on_fresh_install(self):
        resp = client.get("/api/setup/status")
        assert resp.status_code == 200
        assert resp.json()["status"] == "needs_infrastructure"

    def test_needs_application_setup_when_db_configured(self, tmp_path: Path):
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({
            "encryption_key": "test",
            "jwt_secret_key": "test",
            "database_configured": True,
        }))
        _invalidate_cache()

        with patch("app.routers.setup.engine_is_ready", return_value=True), \
             patch("app.routers.setup.get_session_maker") as mock_sm:
            mock_session = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = False
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.execute = AsyncMock(return_value=mock_result)
            mock_sm.return_value = MagicMock(return_value=mock_session)

            resp = client.get("/api/setup/status")
            assert resp.status_code == 200
            assert resp.json()["status"] == "needs_application_setup"

    def test_complete_when_setup_finished(self, tmp_path: Path):
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({
            "encryption_key": "test",
            "jwt_secret_key": "test",
            "database_configured": True,
        }))
        _invalidate_cache()

        with patch("app.routers.setup.engine_is_ready", return_value=True), \
             patch("app.routers.setup.get_session_maker") as mock_sm:
            mock_session = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = True
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session.execute = AsyncMock(return_value=mock_result)
            mock_sm.return_value = MagicMock(return_value=mock_session)

            resp = client.get("/api/setup/status")
            assert resp.status_code == 200
            assert resp.json()["status"] == "complete"


class TestTestDatabase:
    """POST /api/setup/test-database."""

    def test_rejects_empty_url(self):
        resp = client.post("/api/setup/test-database", json={"database_url": ""})
        assert resp.status_code == 400

    def test_returns_ok_on_success(self):
        with patch("app.routers.setup.create_async_engine") as mock_engine:
            mock_conn = AsyncMock()
            mock_conn.execute = AsyncMock()
            mock_eng_instance = AsyncMock()
            mock_eng_instance.connect = MagicMock(return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_conn),
                __aexit__=AsyncMock(return_value=False),
            ))
            mock_eng_instance.dispose = AsyncMock()
            mock_engine.return_value = mock_eng_instance

            resp = client.post(
                "/api/setup/test-database",
                json={"database_url": "postgresql+asyncpg://u:p@host/db"},
            )
            assert resp.status_code == 200
            assert resp.json()["ok"] is True

    def test_returns_error_on_failure(self):
        with patch("app.routers.setup.create_async_engine") as mock_engine:
            mock_engine.side_effect = Exception("Connection refused")
            resp = client.post(
                "/api/setup/test-database",
                json={"database_url": "postgresql+asyncpg://bad/url"},
            )
            assert resp.status_code == 200
            assert resp.json()["ok"] is False
            assert "Connection refused" in resp.json()["message"]


class TestTestRedis:
    """POST /api/setup/test-redis."""

    def test_rejects_empty_url(self):
        resp = client.post("/api/setup/test-redis", json={"redis_url": ""})
        assert resp.status_code == 400

    def test_returns_ok_on_success(self):
        mock_client = MagicMock()
        mock_client.ping = AsyncMock(return_value=True)
        mock_client.aclose = AsyncMock()

        mock_aioredis = MagicMock()
        mock_aioredis.from_url = MagicMock(return_value=mock_client)

        with patch("app.routers.setup._get_redis_module", return_value=mock_aioredis):
            resp = client.post(
                "/api/setup/test-redis",
                json={"redis_url": "redis://localhost:6379/0"},
            )
            assert resp.status_code == 200
            assert resp.json()["ok"] is True


class TestInitialize:
    """POST /api/setup/initialize."""

    def test_sqlite_initialization(self, tmp_path: Path):
        with patch("app.routers.setup.engine_is_ready", return_value=False), \
             patch("app.routers.setup.initialize_engine") as mock_init, \
             patch("app.routers.setup.init_db", new_callable=AsyncMock) as mock_seed:
            resp = client.post("/api/setup/initialize", json={
                "database_type": "sqlite",
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["ok"] is True

            mock_init.assert_called_once()
            mock_seed.assert_called_once()

        _invalidate_cache()
        from app.bootstrap import get_config
        config = get_config()
        assert config.database_configured is True
        assert config.database_url == ""

    def test_postgresql_initialization(self, tmp_path: Path):
        with patch("app.routers.setup.engine_is_ready", return_value=False), \
             patch("app.routers.setup.initialize_engine") as mock_init, \
             patch("app.routers.setup.init_db", new_callable=AsyncMock), \
             patch("app.routers.setup._run_alembic_upgrade", new_callable=AsyncMock) as mock_alembic:
            resp = client.post("/api/setup/initialize", json={
                "database_type": "postgresql",
                "database_url": "postgresql+asyncpg://u:p@host/db",
                "redis_url": "redis://localhost:6379/0",
            })
            assert resp.status_code == 200
            assert resp.json()["ok"] is True

            mock_alembic.assert_called_once()
            mock_init.assert_called_once()

        _invalidate_cache()
        from app.bootstrap import get_config
        config = get_config()
        assert config.database_configured is True
        assert config.database_url == "postgresql+asyncpg://u:p@host/db"
        assert config.redis_url == "redis://localhost:6379/0"

    def test_already_initialized_returns_ok(self):
        with patch("app.routers.setup.engine_is_ready", return_value=True):
            resp = client.post("/api/setup/initialize", json={
                "database_type": "sqlite",
            })
            assert resp.status_code == 200
            assert resp.json()["ok"] is True
            assert "Already" in resp.json()["message"]
