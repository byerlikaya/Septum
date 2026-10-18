"""Tests for the setup wizard router."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from septum_api.bootstrap import _invalidate_cache
from septum_api.main import app

client = TestClient(app)

SENTINEL_PASSWORD = "s3ntinel-Pa55w0rd"


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

        with patch("septum_api.routers.setup.engine_is_ready", return_value=True), \
             patch("septum_api.routers.setup.get_session_maker") as mock_sm:
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

        with patch("septum_api.routers.setup.engine_is_ready", return_value=True), \
             patch("septum_api.routers.setup.get_session_maker") as mock_sm:
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
        with patch("septum_api.routers.setup.create_async_engine") as mock_engine:
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
        with patch("septum_api.routers.setup.create_async_engine") as mock_engine:
            mock_engine.side_effect = Exception("Connection refused")
            resp = client.post(
                "/api/setup/test-database",
                json={"database_url": "postgresql+asyncpg://bad/url"},
            )
            assert resp.status_code == 200
            assert resp.json()["ok"] is False
            assert resp.json()["message"] == "Database connection failed (Exception)"

    def test_failure_does_not_echo_dsn_password(self, caplog: pytest.LogCaptureFixture):
        dsn = f"postgresql+asyncpg://u:{SENTINEL_PASSWORD}@host/db"
        with patch("septum_api.routers.setup.create_async_engine") as mock_engine, \
             caplog.at_level(logging.DEBUG):
            mock_engine.side_effect = ConnectionError(f"could not connect to {dsn}")
            resp = client.post("/api/setup/test-database", json={"database_url": dsn})

        assert resp.json()["ok"] is False
        assert SENTINEL_PASSWORD not in resp.text
        assert SENTINEL_PASSWORD not in caplog.text
        assert "ConnectionError" in caplog.text


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

        with patch("septum_api.routers.setup._get_redis_module", return_value=mock_aioredis):
            resp = client.post(
                "/api/setup/test-redis",
                json={"redis_url": "redis://localhost:6379/0"},
            )
            assert resp.status_code == 200
            assert resp.json()["ok"] is True


    def test_failure_does_not_echo_url_password(self, caplog: pytest.LogCaptureFixture):
        url = f"redis://:{SENTINEL_PASSWORD}@localhost:6379/0"
        mock_aioredis = MagicMock()
        mock_aioredis.from_url = MagicMock(side_effect=ValueError(f"bad url {url}"))

        with patch("septum_api.routers.setup._get_redis_module", return_value=mock_aioredis), \
             caplog.at_level(logging.DEBUG):
            resp = client.post("/api/setup/test-redis", json={"redis_url": url})

        assert resp.json() == {"ok": False, "message": "Redis connection failed (ValueError)"}
        assert SENTINEL_PASSWORD not in caplog.text
        assert "ValueError" in caplog.text


class TestInitialize:
    """POST /api/setup/initialize."""

    def test_sqlite_initialization(self, tmp_path: Path):
        with patch("septum_api.routers.setup.engine_is_ready", return_value=False), \
             patch("septum_api.routers.setup.initialize_engine") as mock_init, \
             patch("septum_api.routers.setup.init_db", new_callable=AsyncMock) as mock_seed:
            resp = client.post("/api/setup/initialize", json={
                "database_type": "sqlite",
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["ok"] is True

            mock_init.assert_called_once()
            mock_seed.assert_called_once()

        _invalidate_cache()
        from septum_api.bootstrap import get_config
        config = get_config()
        assert config.database_configured is True
        assert config.database_url == ""

    def test_postgresql_initialization(self, tmp_path: Path):
        with patch("septum_api.routers.setup.engine_is_ready", return_value=False), \
             patch("septum_api.routers.setup.initialize_engine") as mock_init, \
             patch("septum_api.routers.setup.init_db", new_callable=AsyncMock), \
             patch("septum_api.routers.setup._run_alembic_upgrade", new_callable=AsyncMock) as mock_alembic:
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
        from septum_api.bootstrap import get_config
        config = get_config()
        assert config.database_configured is True
        assert config.database_url == "postgresql+asyncpg://u:p@host/db"
        assert config.redis_url == "redis://localhost:6379/0"

    def test_already_initialized_returns_ok(self):
        with patch("septum_api.routers.setup.engine_is_ready", return_value=True):
            resp = client.post("/api/setup/initialize", json={
                "database_type": "sqlite",
            })
            assert resp.status_code == 200
            assert resp.json()["ok"] is True
            assert "Already" in resp.json()["message"]

    def test_failure_does_not_echo_dsn_password(self, caplog: pytest.LogCaptureFixture):
        dsn = f"postgresql+asyncpg://u:{SENTINEL_PASSWORD}@host/db"
        with patch("septum_api.routers.setup.engine_is_ready", return_value=False), \
             patch("septum_api.routers.setup.initialize_engine"), \
             patch("septum_api.routers.setup.init_db", new_callable=AsyncMock), \
             patch(
                 "septum_api.routers.setup._run_alembic_upgrade",
                 new_callable=AsyncMock,
                 side_effect=RuntimeError(f"Alembic migration failed: cannot reach {dsn}"),
             ), \
             caplog.at_level(logging.DEBUG):
            resp = client.post("/api/setup/initialize", json={
                "database_type": "postgresql",
                "database_url": dsn,
            })

        assert resp.json() == {"ok": False, "message": "Infrastructure initialisation failed (RuntimeError)"}
        assert SENTINEL_PASSWORD not in caplog.text
        assert all(record.exc_info is None for record in caplog.records)


class TestStatusLogging:
    """GET /api/setup/status must not log driver error text."""

    def test_query_failure_logs_class_only(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({"database_configured": True}))
        _invalidate_cache()

        with patch("septum_api.routers.setup.engine_is_ready", return_value=True), \
             patch(
                 "septum_api.routers.setup.get_session_maker",
                 side_effect=ConnectionError(f"password={SENTINEL_PASSWORD}"),
             ), \
             caplog.at_level(logging.DEBUG):
            resp = client.get("/api/setup/status")

        assert resp.json()["status"] == "needs_application_setup"
        assert SENTINEL_PASSWORD not in caplog.text
        assert "ConnectionError" in caplog.text
