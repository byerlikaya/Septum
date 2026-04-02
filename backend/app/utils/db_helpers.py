"""Shared database and validation helpers for Septum routers."""
from __future__ import annotations

from typing import Optional, Type, TypeVar
import re

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


async def load_settings(db: AsyncSession) -> AppSettings:
    """Load the singleton AppSettings row or raise HTTP 500."""
    result = await db.execute(select(AppSettings).where(AppSettings.id == 1))
    settings = result.scalar_one_or_none()
    if settings is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Application settings have not been initialized.",
        )
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
