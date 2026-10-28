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
import logging
import os
import secrets
import tempfile
import threading
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = "/app/data/config.json"

SETUP_TOKEN_ENV = "SEPTUM_SETUP_TOKEN"
_SETUP_TOKEN_KEY = "setup_token"
_SETUP_TOKEN_BYTES = 32
SETUP_TOKEN_MIN_LENGTH = 32
SETUP_TOKEN_MIN_DISTINCT_CHARS = 10

# Every config.json written before the default limit was enforced carries
# this value; it was never applied, so it is read as the current default.
_UNENFORCED_RATE_LIMIT = "60/minute"

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
    "error_log_retention_days": "ERROR_LOG_RETENTION_DAYS",
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
    log_level: str = "INFO"
    rate_limit: str = "600/minute"
    error_log_retention_days: int = 90
    frontend_origin: str = "*"
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


# Accepted ranges for int settings; anything outside falls back with a warning.
_INT_RANGES: dict[str, tuple[int, int]] = {
    "error_log_retention_days": (0, 36500),
    "jwt_expiration_minutes": (1, 43200),
}


def _coerce_int(field: str, label: str, value: Any, fallback: int, source: str) -> int:
    """Return *value* as an int within *field*'s range, else *fallback* with a warning.

    The warning names the setting (*label*) and its source, never the value.
    """
    parsed: int | None = None
    if isinstance(value, int) and not isinstance(value, bool):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = int(value.strip())
        except ValueError:
            parsed = None
    low, high = _INT_RANGES.get(field, (None, None))
    if parsed is not None and (low is None or low <= parsed <= high):
        return parsed
    logger.warning("Ignoring invalid %s from %s; using %s", label, source, fallback)
    return fallback


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
            setattr(config, attr, _coerce_int(attr, env_var, value, current, "the environment"))
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
    os.fchmod(fd, 0o600)
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
_config_write_lock = threading.Lock()


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
    for f in fields(BootstrapConfig):
        if f.name not in raw:
            continue
        default = getattr(config, f.name)
        value = raw[f.name]
        if isinstance(default, int) and not isinstance(default, bool):
            value = _coerce_int(f.name, f.name, value, default, "config.json")
        setattr(config, f.name, value)

    # Auto-generate secrets when missing
    dirty = False
    if config.rate_limit == _UNENFORCED_RATE_LIMIT:
        config.rate_limit = BootstrapConfig.rate_limit
        dirty = True
    if not config.encryption_key:
        config.encryption_key = _generate_encryption_key()
        dirty = True
    if not config.jwt_secret_key:
        config.jwt_secret_key = _generate_jwt_secret()
        dirty = True

    # Persist if we generated new secrets or the file did not exist
    if dirty or not path.exists():
        with _config_write_lock:
            _write_config_file(path, {**_read_config_file(path), **asdict(config)})

    # Environment variables always win
    _apply_env_overrides(config)

    _cached_config = config
    return config


def save_config(updates: dict[str, Any]) -> None:
    """Merge *updates* into the persisted config file and invalidate the cache."""
    path = _config_path()
    with _config_write_lock:
        current = _read_config_file(path)
        current.update(updates)
        _write_config_file(path, current)
    _invalidate_cache()


def needs_setup() -> bool:
    """Return True when the infrastructure wizard has not completed yet."""
    config = get_config()
    return not config.database_configured


class WeakSetupTokenError(RuntimeError):
    """``SEPTUM_SETUP_TOKEN`` is set but too weak to resist guessing."""


def _weak_setup_token(value: str) -> bool:
    """Too short, or too few distinct characters (``aaaa…`` / ``1212…``)."""
    return (
        len(value) < SETUP_TOKEN_MIN_LENGTH
        or len(set(value)) < SETUP_TOKEN_MIN_DISTINCT_CHARS
    )


def validate_setup_token_env() -> None:
    """Refuse to start with a weak ``SEPTUM_SETUP_TOKEN``.

    Weak means shorter than the minimum length or built from fewer than
    the minimum number of distinct characters; ``openssl rand -hex 32``
    passes. The message names the variable and the rules, never the value.
    """
    value = os.getenv(SETUP_TOKEN_ENV, "").strip()
    if value and _weak_setup_token(value):
        raise WeakSetupTokenError(
            f"{SETUP_TOKEN_ENV} must be at least {SETUP_TOKEN_MIN_LENGTH} characters with "
            f"at least {SETUP_TOKEN_MIN_DISTINCT_CHARS} distinct characters; generate one "
            "with `openssl rand -hex 32` or unset it to get a generated token."
        )


def setup_token_from_env() -> str | None:
    """Return the operator-supplied ``SEPTUM_SETUP_TOKEN``, or None when unset, blank or weak.

    A weak value is never accepted as a token; startup refuses it first
    through :func:`validate_setup_token_env`.
    """
    value = os.getenv(SETUP_TOKEN_ENV, "").strip()
    if not value or _weak_setup_token(value):
        return None
    return value


def get_setup_token() -> str | None:
    """Return the setup token the setup-window gate must compare against.

    ``SEPTUM_SETUP_TOKEN`` wins over the value stored in ``config.json``.
    Returns None when neither exists, which means no setup-time request can
    be authorised by a token. The value is a secret: it must never be logged,
    persisted anywhere but ``config.json``, or returned in an API response.
    Whether setup is still open is not decided here; callers check that
    against the database themselves.
    """
    from_env = setup_token_from_env()
    if from_env:
        return from_env
    stored = _read_config_file(_config_path()).get(_SETUP_TOKEN_KEY)
    return stored if isinstance(stored, str) and stored else None


def ensure_setup_token() -> str:
    """Return the stored setup token, generating and persisting one if absent.

    Call this only once setup is positively known to be open; an install that
    already has a user or a completed setup must never get a token. The token
    survives restarts and ``--reload`` because it lives in ``config.json``
    (mode 0600, written atomically). It is independent of
    ``SEPTUM_SETUP_TOKEN``: callers that honour the override check
    :func:`setup_token_from_env` first.
    """
    path = _config_path()
    with _config_write_lock:
        current = _read_config_file(path)
        stored = current.get(_SETUP_TOKEN_KEY)
        if isinstance(stored, str) and stored:
            return stored
        token = secrets.token_urlsafe(_SETUP_TOKEN_BYTES)
        current[_SETUP_TOKEN_KEY] = token
        _write_config_file(path, current)
    return token


def clear_setup_token() -> None:
    """Remove the stored setup token from ``config.json``; a no-op when none is stored.

    Called once setup closes (first admin created, or found closed at start).
    The rewrite is atomic and keeps the file at mode 0600.
    ``SEPTUM_SETUP_TOKEN`` lives in the environment and is not affected.
    """
    path = _config_path()
    with _config_write_lock:
        current = _read_config_file(path)
        if _SETUP_TOKEN_KEY not in current:
            return
        del current[_SETUP_TOKEN_KEY]
        _write_config_file(path, current)
