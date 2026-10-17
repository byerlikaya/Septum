from __future__ import annotations

"""JWT token and password hashing utilities."""

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from ..bootstrap import get_config

JWT_ALGORITHM = "HS256"
# 12 chars + bcrypt cost ~12 bring brute-force outside trivial GPU
# range while still being typeable. Combined with the per-route
# rate-limit on /api/auth/login this is the practical defence.
PASSWORD_MIN_LENGTH = 12
# bcrypt only reads the first 72 bytes and bcrypt>=5 raises beyond that.
PASSWORD_MAX_BYTES = 72


class WeakPasswordError(ValueError):
    """Raised when a password does not satisfy the strength policy."""


def validate_password_strength(password: str) -> None:
    """Raise :class:`WeakPasswordError` if *password* is too short, too long or not encodable."""
    if len(password) < PASSWORD_MIN_LENGTH:
        raise WeakPasswordError(
            f"Password must be at least {PASSWORD_MIN_LENGTH} characters long"
        )
    try:
        encoded = password.encode()
    except UnicodeEncodeError as exc:
        raise WeakPasswordError("Password contains invalid characters") from exc
    if len(encoded) > PASSWORD_MAX_BYTES:
        raise WeakPasswordError(
            f"Password must be at most {PASSWORD_MAX_BYTES} bytes long"
        )


def _jwt_secret() -> str:
    return get_config().jwt_secret_key


def _jwt_expiration_minutes() -> int:
    return get_config().jwt_expiration_minutes


def hash_password(plain: str) -> str:
    """Hash a plaintext password with bcrypt."""
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against a bcrypt hash.

    Input is cut to :data:`PASSWORD_MAX_BYTES` exactly as bcrypt<5 did
    implicitly, so hashes created from longer passwords keep verifying
    and an over-long login attempt fails cleanly instead of raising.
    Input that cannot be UTF-8 encoded (lone surrogates) never matches.
    """
    try:
        encoded = plain.encode()
    except UnicodeEncodeError:
        return False
    return bcrypt.checkpw(encoded[:PASSWORD_MAX_BYTES], hashed.encode())


def create_access_token(user_id: int, email: str) -> str:
    """Create a signed JWT access token."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=_jwt_expiration_minutes())
    payload = {
        "sub": str(user_id),
        "email": email,
        "exp": expire,
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Decode and verify a JWT access token.

    Raises ``jwt.InvalidTokenError`` on failure.
    """
    return jwt.decode(token, _jwt_secret(), algorithms=[JWT_ALGORITHM])
