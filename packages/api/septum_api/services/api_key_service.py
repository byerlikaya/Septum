"""API key generation, validation, and lifecycle management."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.api_key import ApiKey
from ..models.user import User

PREFIX = "sk-septum-"
_RAW_KEY_BYTES = 32


def _generate_raw_key() -> str:
    """Return a fresh ``sk-septum-<64 hex chars>`` key."""
    return PREFIX + secrets.token_hex(_RAW_KEY_BYTES)


def _hash_key(raw_key: str) -> str:
    """SHA-256 hash of the full raw key."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


def _extract_prefix(raw_key: str) -> str:
    """Return the first 8 characters after the ``sk-septum-`` prefix."""
    return raw_key[len(PREFIX) : len(PREFIX) + 8]


async def generate_api_key(
    db: AsyncSession,
    user_id: int,
    name: str,
    expires_at: datetime | None = None,
) -> tuple[str, ApiKey]:
    """Create a new API key and persist its hash.

    Returns ``(raw_key, api_key_row)`` — the raw key is shown once and
    never stored. The caller must surface it to the user immediately.
    """
    raw_key = _generate_raw_key()
    api_key = ApiKey(
        user_id=user_id,
        name=name,
        prefix=_extract_prefix(raw_key),
        hashed_key=_hash_key(raw_key),
        expires_at=expires_at,
    )
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)
    return raw_key, api_key


async def validate_api_key(db: AsyncSession, raw_key: str) -> User | None:
    """Validate a raw API key and return the owning ``User``, or ``None``.

    Lookup is by 8-char prefix (indexed), then full SHA-256 comparison.
    Expired and inactive keys are rejected. On success the
    ``last_used_at`` timestamp is updated.
    """
    if not raw_key.startswith(PREFIX) or len(raw_key) < len(PREFIX) + 8:
        return None

    prefix = _extract_prefix(raw_key)
    result = await db.execute(
        select(ApiKey).where(ApiKey.prefix == prefix, ApiKey.is_active.is_(True))
    )
    candidates = result.scalars().all()

    hashed = _hash_key(raw_key)
    now = datetime.now(timezone.utc)

    for candidate in candidates:
        if not secrets.compare_digest(candidate.hashed_key, hashed):
            continue
        if candidate.expires_at is not None:
            # SQLite returns naive datetimes — treat as UTC for comparison.
            expires = candidate.expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if expires <= now:
                return None
        candidate.last_used_at = now
        await db.commit()
        return candidate.user  # type: ignore[return-value]

    return None


async def list_user_keys(db: AsyncSession, user_id: int) -> Sequence[ApiKey]:
    """Return all API keys owned by the given user."""
    result = await db.execute(
        select(ApiKey)
        .where(ApiKey.user_id == user_id)
        .order_by(ApiKey.created_at.desc())
    )
    return result.scalars().all()


async def revoke_key(
    db: AsyncSession,
    key_id: int,
    user_id: int | None = None,
) -> bool:
    """Deactivate an API key. Returns ``True`` if the key was found and revoked.

    When *user_id* is provided, only keys owned by that user are
    affected (ownership check). Pass ``None`` to allow admin revocation
    of any key.
    """
    stmt = select(ApiKey).where(ApiKey.id == key_id)
    if user_id is not None:
        stmt = stmt.where(ApiKey.user_id == user_id)

    result = await db.execute(stmt)
    api_key = result.scalar_one_or_none()
    if api_key is None:
        return False

    api_key.is_active = False
    await db.commit()
    return True
