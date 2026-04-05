"""Bootstrap configuration for Septum.

Manages ``/app/data/config.json`` which stores infrastructure settings that
must be available before the database is initialised (encryption keys, JWT
secrets, database URL, Redis URL, etc.).

This module intentionally avoids importing anything from the ORM or database
layer so it can be loaded safely at the very top of the import chain.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import tempfile
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_DEFAULT_CONFIG_PATH = "/app/data/config.json"

# Mapping from BootstrapConfig field → environment variable override.
_ENV_OVERRIDES: dict[str, str] = {
    "database_url": "DATABASE_URL",
    "db_path": "DB_PATH",
    "redis_url": "REDIS_URL",
    "encryption_key": "ENCRYPTION_KEY",
    "jwt_secret_key": "JWT_SECRET_KEY",
    "jwt_expiration_minutes": "JWT_EXPIRATION_MINUTES",
    "log_level": "LOG_LEVEL",
    "rate_limit": "RATE_LIMIT_DEFAULT",
    "frontend_origin": "FRONTEND_ORIGIN",
}


@dataclass
class BootstrapConfig:
    """Infrastructure configuration persisted across container restarts."""

    database_url: str = ""
    db_path: str = "./septum.db"
    redis_url: str = ""
    encryption_key: str = ""
    jwt_secret_key: str = ""
    jwt_expiration_minutes: int = 1440
    log_level: str = "DEBUG"
    rate_limit: str = "60/minute"
    frontend_origin: str = "http://localhost:3000"
    database_configured: bool = False


def _generate_encryption_key() -> str:
    """Generate a urlsafe-base64-encoded 256-bit AES key."""
    raw = AESGCM.generate_key(bit_length=256)
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _generate_jwt_secret() -> str:
    """Generate a cryptographically secure JWT secret."""
    return secrets.token_urlsafe(48)


def _config_path() -> Path:
    """Resolve the bootstrap config file path."""
    return Path(os.getenv("SEPTUM_CONFIG_PATH", _DEFAULT_CONFIG_PATH))


def _apply_env_overrides(config: BootstrapConfig) -> None:
    """Overlay environment variables onto *config* in-place."""
    for attr, env_var in _ENV_OVERRIDES.items():
        value = os.getenv(env_var)
        if value is None:
            continue
        current = getattr(config, attr)
        if isinstance(current, bool):
            setattr(config, attr, value.strip().lower() in {"1", "true", "yes", "on"})
        elif isinstance(current, int):
            try:
                setattr(config, attr, int(value))
            except ValueError:
                pass
        else:
            setattr(config, attr, value)


def _read_config_file(path: Path) -> dict[str, Any]:
    """Read and parse the JSON config file, returning an empty dict on failure."""
    try:
        return json.loads(path.read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_config_file(path: Path, data: dict[str, Any]) -> None:
    """Atomically write *data* as JSON to *path* (write-to-tmp then rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Cached singleton
# ---------------------------------------------------------------------------
_cached_config: BootstrapConfig | None = None


def _invalidate_cache() -> None:
    """Clear the cached config so the next ``get_config`` re-reads the file."""
    global _cached_config
    _cached_config = None


def get_config() -> BootstrapConfig:
    """Return the current bootstrap configuration (cached after first call).

    On first call the config file is read (or created with auto-generated
    secrets).  Environment variables always take precedence over file values.
    """
    global _cached_config
    if _cached_config is not None:
        return _cached_config

    path = _config_path()
    raw = _read_config_file(path)

    config = BootstrapConfig()

    # Apply persisted values from config.json
    for f in fields(config):
        if f.name in raw:
            setattr(config, f.name, raw[f.name])

    # Auto-generate secrets when missing
    dirty = False
    if not config.encryption_key:
        config.encryption_key = _generate_encryption_key()
        dirty = True
    if not config.jwt_secret_key:
        config.jwt_secret_key = _generate_jwt_secret()
        dirty = True

    # Persist if we generated new secrets or the file did not exist
    if dirty or not path.exists():
        _write_config_file(path, asdict(config))

    # Environment variables always win
    _apply_env_overrides(config)

    _cached_config = config
    return config


def save_config(updates: dict[str, Any]) -> None:
    """Merge *updates* into the persisted config file and invalidate the cache."""
    path = _config_path()
    current = _read_config_file(path)
    current.update(updates)
    _write_config_file(path, current)
    _invalidate_cache()


def needs_setup() -> bool:
    """Return True when the infrastructure wizard has not completed yet."""
    config = get_config()
    return not config.database_configured
