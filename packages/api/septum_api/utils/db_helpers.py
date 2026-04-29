"""Shared database and validation helpers for Septum routers."""
from __future__ import annotations

import re
from typing import Any, Optional, Type, TypeVar

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.settings import AppSettings

T = TypeVar("T")


async def get_or_404(
    db: AsyncSession,
    model: Type[T],
    pk: int,
    detail: str = "Resource not found.",
) -> T:
    """Fetch a row by primary key or raise HTTP 404."""
    result = await db.execute(select(model).where(model.id == pk))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
    return row


async def get_owned_or_404(
    db: AsyncSession,
    model: Type[T],
    pk: int,
    user: Any,
    detail: str = "Resource not found.",
) -> T:
    """Fetch a row by primary key, asserting ownership.

    Returns 404 (never 403) on ownership mismatch so non-owner users
    cannot enumerate which IDs exist for other users. ``admin`` role
    bypasses the check by design — the system operator can read any
    artifact for support purposes.
    """
    result = await db.execute(select(model).where(model.id == pk))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
    if getattr(user, "role", None) == "admin":
        return row
    owner_id = getattr(row, "user_id", None)
    if owner_id is None or owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
    return row


async def load_settings(db: AsyncSession) -> AppSettings:
    """Load the singleton AppSettings row, lazy-seeding it if missing.

    A missing ``AppSettings`` row used to raise HTTP 500 ("Application
    settings have not been initialized"), which piled up in the Error
    Logs UI as a storm of identical 500s whenever the Settings page was
    polled during a transient bootstrap window. Every wizard endpoint
    (``GET``/``PATCH /api/settings``, ``/test-llm``, ``/test-local-models``)
    also goes through this helper, so raising here would leave the
    wizard unable to make progress — it needs a row to update.

    The helper is now idempotent: if the row is missing, build the
    default one via :func:`database.build_default_app_settings` — the
    same factory ``_seed_defaults`` calls at startup — commit it, and
    return it. Fresh databases where the lifespan skipped ``init_db``
    (for example because ``bootstrap.needs_setup()`` returned ``True``
    at startup) self-heal on the first read instead of 500-spamming
    every request until the wizard finishes.
    """
    result = await db.execute(select(AppSettings).where(AppSettings.id == 1))
    settings = result.scalar_one_or_none()
    if settings is None:
        from ..database import build_default_app_settings

        settings = build_default_app_settings()
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
    return settings


def detect_language(text: str, fallback: str = "en") -> str:
    """Detect the language of text using langdetect, with fallback."""
    try:
        from langdetect import detect
        return detect(text)
    except Exception:
        return fallback


def validate_regex(pattern: str) -> Optional[str]:
    """Validate a regex pattern. Returns error message or None if valid."""
    try:
        re.compile(pattern)
        return None
    except re.error as exc:
        return str(exc)
