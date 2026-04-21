"""Tests for the API key service — generate, validate, list, revoke."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from septum_api.models import Base
from septum_api.models.user import User
from septum_api.services.api_key_service import (
    PREFIX,
    generate_api_key,
    list_user_keys,
    revoke_key,
    validate_api_key,
)


@pytest.fixture
async def db_session(tmp_path):
    """Isolated async SQLite session for each test."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with session_maker() as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def test_user(db_session: AsyncSession) -> User:
    """Create a test user and return it."""
    user = User(email="test@example.com", hashed_password="fakehash", role="admin")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.mark.asyncio
async def test_generate_returns_raw_key_and_persists_hash(
    db_session: AsyncSession, test_user: User
):
    raw_key, api_key = await generate_api_key(db_session, test_user.id, "CI key")

    assert raw_key.startswith(PREFIX)
    assert len(raw_key) == len(PREFIX) + 64  # 32 bytes hex
    assert api_key.prefix == raw_key[len(PREFIX) : len(PREFIX) + 8]
    assert api_key.hashed_key != raw_key  # hash, not raw
    assert api_key.is_active is True
    assert api_key.user_id == test_user.id
    assert api_key.name == "CI key"


@pytest.mark.asyncio
async def test_validate_correct_key_returns_user(
    db_session: AsyncSession, test_user: User
):
    raw_key, _ = await generate_api_key(db_session, test_user.id, "valid")

    user = await validate_api_key(db_session, raw_key)
    assert user is not None
    assert user.id == test_user.id


@pytest.mark.asyncio
async def test_validate_wrong_key_returns_none(
    db_session: AsyncSession, test_user: User
):
    await generate_api_key(db_session, test_user.id, "good")

    user = await validate_api_key(db_session, PREFIX + "a" * 64)
    assert user is None


@pytest.mark.asyncio
async def test_validate_expired_key_returns_none(
    db_session: AsyncSession, test_user: User
):
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    raw_key, _ = await generate_api_key(
        db_session, test_user.id, "expired", expires_at=past
    )

    user = await validate_api_key(db_session, raw_key)
    assert user is None


@pytest.mark.asyncio
async def test_validate_revoked_key_returns_none(
    db_session: AsyncSession, test_user: User
):
    raw_key, api_key = await generate_api_key(db_session, test_user.id, "revoked")
    await revoke_key(db_session, api_key.id)

    user = await validate_api_key(db_session, raw_key)
    assert user is None


@pytest.mark.asyncio
async def test_validate_updates_last_used_at(
    db_session: AsyncSession, test_user: User
):
    raw_key, api_key = await generate_api_key(db_session, test_user.id, "track")
    assert api_key.last_used_at is None

    await validate_api_key(db_session, raw_key)

    await db_session.refresh(api_key)
    assert api_key.last_used_at is not None


@pytest.mark.asyncio
async def test_validate_garbage_input_returns_none(db_session: AsyncSession):
    assert await validate_api_key(db_session, "") is None
    assert await validate_api_key(db_session, "not-a-key") is None
    assert await validate_api_key(db_session, PREFIX) is None  # too short


@pytest.mark.asyncio
async def test_list_user_keys(db_session: AsyncSession, test_user: User):
    await generate_api_key(db_session, test_user.id, "key-1")
    await generate_api_key(db_session, test_user.id, "key-2")

    keys = await list_user_keys(db_session, test_user.id)
    assert len(keys) == 2
    assert {k.name for k in keys} == {"key-1", "key-2"}


@pytest.mark.asyncio
async def test_revoke_key_deactivates(db_session: AsyncSession, test_user: User):
    _, api_key = await generate_api_key(db_session, test_user.id, "revoke-me")
    assert api_key.is_active is True

    result = await revoke_key(db_session, api_key.id)
    assert result is True

    await db_session.refresh(api_key)
    assert api_key.is_active is False


@pytest.mark.asyncio
async def test_revoke_key_ownership_check(db_session: AsyncSession, test_user: User):
    _, api_key = await generate_api_key(db_session, test_user.id, "owned")

    # Wrong user_id → not found
    result = await revoke_key(db_session, api_key.id, user_id=9999)
    assert result is False

    # Correct user_id → revoked
    result = await revoke_key(db_session, api_key.id, user_id=test_user.id)
    assert result is True


@pytest.mark.asyncio
async def test_revoke_nonexistent_key(db_session: AsyncSession):
    result = await revoke_key(db_session, key_id=9999)
    assert result is False
