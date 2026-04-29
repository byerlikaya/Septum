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


class WeakPasswordError(ValueError):
    """Raised when a password does not satisfy the strength policy."""


def validate_password_strength(password: str) -> None:
    """Raise :class:`WeakPasswordError` if *password* is too short."""
    if len(password) < PASSWORD_MIN_LENGTH:
        raise WeakPasswordError(
            f"Password must be at least {PASSWORD_MIN_LENGTH} characters long"
        )


def _jwt_secret() -> str:
    return get_config().jwt_secret_key


def _jwt_expiration_minutes() -> int:
    return get_config().jwt_expiration_minutes


def hash_password(plain: str) -> str:
    """Hash a plaintext password with bcrypt."""
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    return bcrypt.checkpw(plain.encode(), hashed.encode())


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
