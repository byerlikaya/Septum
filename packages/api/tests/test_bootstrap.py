"""Tests for the bootstrap configuration module."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
from septum_api.bootstrap import (
    SETUP_TOKEN_ENV,
    BootstrapConfig,
    WeakSetupTokenError,
    _invalidate_cache,
    clear_setup_token,
    ensure_setup_token,
    get_config,
    get_setup_token,
    needs_setup,
    save_config,
    validate_setup_token_env,
)

OPERATOR_TOKEN = "operator-token-0123456789abcdef0123"


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point bootstrap at a temporary directory and clear env overrides."""
    config_path = tmp_path / "config.json"
    monkeypatch.setenv("SEPTUM_CONFIG_PATH", str(config_path))
    # Clear all env vars that bootstrap reads so tests are deterministic.
    for env_var in (
        "DATABASE_URL", "DB_PATH", "REDIS_URL", "ENCRYPTION_KEY",
        "JWT_SECRET_KEY", "JWT_EXPIRATION_MINUTES", "LOG_LEVEL",
        "RATE_LIMIT_DEFAULT", "FRONTEND_ORIGIN", SETUP_TOKEN_ENV, "ERROR_LOG_RETENTION_DAYS",
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
        assert config.log_level == "INFO"
        assert config.rate_limit == "600/minute"
        assert config.frontend_origin == "*"
        assert config.database_configured is False

    def test_unenforced_legacy_rate_limit_is_upgraded(self, tmp_path: Path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps({
            "encryption_key": "k", "jwt_secret_key": "j", "rate_limit": "60/minute",
        }))
        assert get_config().rate_limit == "600/minute"
        assert json.loads(path.read_text())["rate_limit"] == "600/minute"

    def test_custom_rate_limit_is_kept(self, tmp_path: Path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps({
            "encryption_key": "k", "jwt_secret_key": "j", "rate_limit": "120/minute",
        }))
        assert get_config().rate_limit == "120/minute"


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


class TestSetupToken:
    """Setup token persistence, env override and removal."""

    def test_no_token_until_ensured(self, tmp_path: Path):
        get_config()
        assert get_setup_token() is None
        assert "setup_token" not in json.loads((tmp_path / "config.json").read_text())

    def test_ensure_generates_and_persists(self, tmp_path: Path):
        get_config()
        token = ensure_setup_token()
        assert len(token) >= 43
        assert json.loads((tmp_path / "config.json").read_text())["setup_token"] == token
        assert get_setup_token() == token

    def test_ensure_is_stable_across_restarts(self):
        get_config()
        first = ensure_setup_token()
        _invalidate_cache()
        get_config()
        assert ensure_setup_token() == first

    def test_tokens_differ_between_installs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        first = ensure_setup_token()
        monkeypatch.setenv("SEPTUM_CONFIG_PATH", str(tmp_path / "other" / "config.json"))
        assert ensure_setup_token() != first

    def test_env_override_wins(self, monkeypatch: pytest.MonkeyPatch):
        ensure_setup_token()
        monkeypatch.setenv(SETUP_TOKEN_ENV, f"  {OPERATOR_TOKEN}  ")
        assert get_setup_token() == OPERATOR_TOKEN

    def test_short_env_token_is_never_accepted(self, monkeypatch: pytest.MonkeyPatch):
        stored = ensure_setup_token()
        monkeypatch.setenv(SETUP_TOKEN_ENV, "short-operator-token")
        assert get_setup_token() == stored

    def test_short_env_token_refuses_startup_without_echoing_it(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv(SETUP_TOKEN_ENV, "short-operator-token")
        with pytest.raises(WeakSetupTokenError) as excinfo:
            validate_setup_token_env()
        assert "short-operator-token" not in str(excinfo.value)
        assert SETUP_TOKEN_ENV in str(excinfo.value)

    @pytest.mark.parametrize("value", ["a" * 64, "ab" * 32, "012345678" * 4])
    def test_low_diversity_env_token_refuses_startup(self, monkeypatch: pytest.MonkeyPatch, value: str):
        monkeypatch.setenv(SETUP_TOKEN_ENV, value)
        with pytest.raises(WeakSetupTokenError) as excinfo:
            validate_setup_token_env()
        assert value not in str(excinfo.value)
        assert "distinct" in str(excinfo.value)
        assert get_setup_token() != value

    @pytest.mark.parametrize(
        ("value", "accepted"),
        [
            ("0123456789" * 3 + "ab", True),
            ("0123456789" * 3 + "a", False),
            ("012345678" * 3 + "0" * 5, False),
            ("012345678a" + "0" * 22, True),
        ],
    )
    def test_setup_token_length_and_diversity_boundaries(
        self, monkeypatch: pytest.MonkeyPatch, value: str, accepted: bool
    ):
        monkeypatch.setenv(SETUP_TOKEN_ENV, value)
        if accepted:
            validate_setup_token_env()
            assert get_setup_token() == value
        else:
            with pytest.raises(WeakSetupTokenError):
                validate_setup_token_env()

    def test_openssl_style_hex_token_passes(self, monkeypatch: pytest.MonkeyPatch):
        import secrets

        monkeypatch.setenv(SETUP_TOKEN_ENV, secrets.token_hex(32))
        validate_setup_token_env()

    def test_long_env_token_passes_startup_check(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv(SETUP_TOKEN_ENV, OPERATOR_TOKEN)
        validate_setup_token_env()

    def test_blank_env_is_ignored(self, monkeypatch: pytest.MonkeyPatch):
        stored = ensure_setup_token()
        monkeypatch.setenv(SETUP_TOKEN_ENV, "   ")
        assert get_setup_token() == stored

    def test_clear_removes_token_and_keeps_secrets_and_mode(self, tmp_path: Path):
        path = tmp_path / "config.json"
        config = get_config()
        ensure_setup_token()
        clear_setup_token()
        raw = json.loads(path.read_text())
        assert "setup_token" not in raw
        assert raw["encryption_key"] == config.encryption_key
        assert get_setup_token() is None
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_clear_without_token_does_not_rewrite(self, tmp_path: Path):
        path = tmp_path / "config.json"
        get_config()
        before = path.stat().st_mtime_ns, path.stat().st_ino
        clear_setup_token()
        assert (path.stat().st_mtime_ns, path.stat().st_ino) == before

    def test_save_config_keeps_token(self):
        token = ensure_setup_token()
        save_config({"log_level": "DEBUG"})
        assert get_setup_token() == token

    def test_regenerating_secrets_keeps_token(self, tmp_path: Path):
        path = tmp_path / "config.json"
        get_config()
        token = ensure_setup_token()
        raw = json.loads(path.read_text())
        del raw["jwt_secret_key"]
        path.write_text(json.dumps(raw))
        _invalidate_cache()
        get_config()
        assert get_setup_token() == token

    def test_token_not_on_config_object(self):
        ensure_setup_token()
        _invalidate_cache()
        assert not hasattr(get_config(), "setup_token")
