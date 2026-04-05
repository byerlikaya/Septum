"""Tests for the bootstrap configuration module."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from app.bootstrap import (
    BootstrapConfig,
    _invalidate_cache,
    get_config,
    needs_setup,
    save_config,
)


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point bootstrap at a temporary directory and clear env overrides."""
    config_path = tmp_path / "config.json"
    monkeypatch.setenv("SEPTUM_CONFIG_PATH", str(config_path))
    # Clear all env vars that bootstrap reads so tests are deterministic.
    for env_var in (
        "DATABASE_URL", "DB_PATH", "REDIS_URL", "ENCRYPTION_KEY",
        "JWT_SECRET_KEY", "JWT_EXPIRATION_MINUTES", "LOG_LEVEL",
        "RATE_LIMIT_DEFAULT", "FRONTEND_ORIGIN",
    ):
        monkeypatch.delenv(env_var, raising=False)
    _invalidate_cache()
    yield
    _invalidate_cache()


class TestGetConfig:
    """get_config() behaviour."""

    def test_creates_config_file_on_first_call(self, tmp_path: Path):
        path = tmp_path / "config.json"
        config = get_config()
        assert path.exists()
        assert isinstance(config, BootstrapConfig)

    def test_auto_generates_encryption_key(self):
        config = get_config()
        assert config.encryption_key
        assert len(config.encryption_key) > 20

    def test_auto_generates_jwt_secret(self):
        config = get_config()
        assert config.jwt_secret_key
        assert len(config.jwt_secret_key) > 20

    def test_persists_generated_secrets(self, tmp_path: Path):
        path = tmp_path / "config.json"
        config = get_config()
        raw = json.loads(path.read_text())
        assert raw["encryption_key"] == config.encryption_key
        assert raw["jwt_secret_key"] == config.jwt_secret_key

    def test_returns_cached_instance(self):
        first = get_config()
        second = get_config()
        assert first is second

    def test_reads_existing_config_file(self, tmp_path: Path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps({
            "database_url": "postgresql+asyncpg://u:p@host/db",
            "encryption_key": "testkey123",
            "jwt_secret_key": "jwtsecret456",
            "database_configured": True,
        }))
        config = get_config()
        assert config.database_url == "postgresql+asyncpg://u:p@host/db"
        assert config.encryption_key == "testkey123"
        assert config.database_configured is True

    def test_defaults_for_missing_fields(self):
        config = get_config()
        assert config.database_url == ""
        assert config.db_path == "./septum.db"
        assert config.redis_url == ""
        assert config.jwt_expiration_minutes == 1440
        assert config.log_level == "DEBUG"
        assert config.rate_limit == "60/minute"
        assert config.frontend_origin == "http://localhost:3000"
        assert config.database_configured is False


class TestEnvOverrides:
    """Environment variables take precedence over config.json."""

    def test_database_url_override(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://override/db")
        config = get_config()
        assert config.database_url == "postgresql+asyncpg://override/db"

    def test_redis_url_override(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("REDIS_URL", "redis://override:6379/0")
        config = get_config()
        assert config.redis_url == "redis://override:6379/0"

    def test_encryption_key_override(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("ENCRYPTION_KEY", "env-key-override")
        config = get_config()
        assert config.encryption_key == "env-key-override"

    def test_jwt_secret_override(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "env-jwt-override")
        config = get_config()
        assert config.jwt_secret_key == "env-jwt-override"

    def test_int_override(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("JWT_EXPIRATION_MINUTES", "60")
        config = get_config()
        assert config.jwt_expiration_minutes == 60

    def test_invalid_int_keeps_default(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("JWT_EXPIRATION_MINUTES", "not-a-number")
        config = get_config()
        assert config.jwt_expiration_minutes == 1440

    def test_log_level_override(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        config = get_config()
        assert config.log_level == "DEBUG"


class TestSaveConfig:
    """save_config() merges updates and invalidates cache."""

    def test_save_updates_file(self, tmp_path: Path):
        get_config()
        save_config({"database_url": "postgresql+asyncpg://new/db", "database_configured": True})

        _invalidate_cache()
        config = get_config()
        assert config.database_url == "postgresql+asyncpg://new/db"
        assert config.database_configured is True

    def test_save_preserves_existing_keys(self, tmp_path: Path):
        config = get_config()
        original_key = config.encryption_key

        save_config({"redis_url": "redis://localhost:6379"})

        _invalidate_cache()
        config = get_config()
        assert config.encryption_key == original_key
        assert config.redis_url == "redis://localhost:6379"

    def test_save_invalidates_cache(self):
        first = get_config()
        save_config({"log_level": "DEBUG"})
        second = get_config()
        assert first is not second
        assert second.log_level == "DEBUG"


class TestNeedsSetup:
    """needs_setup() reflects database_configured flag."""

    def test_true_on_fresh_install(self):
        assert needs_setup() is True

    def test_false_after_database_configured(self, tmp_path: Path):
        get_config()
        save_config({"database_configured": True})
        assert needs_setup() is False

    def test_true_when_database_configured_false(self, tmp_path: Path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps({
            "encryption_key": "k",
            "jwt_secret_key": "j",
            "database_configured": False,
        }))
        assert needs_setup() is True
