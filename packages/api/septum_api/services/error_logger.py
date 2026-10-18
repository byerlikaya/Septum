from __future__ import annotations

"""Centralized error logging service for backend and frontend errors."""

import traceback
from typing import Any, Optional

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import public_error_text
from ..models.error_log import ErrorLog
from ..utils.crypto import hash_text


def sanitized_stack(exc: BaseException) -> str:
    """Render the exception chain as class names and ``file:line in function`` frames.

    Message lines and source lines are left out on purpose: both can carry
    raw text that the error log must never store.
    """
    blocks: list[str] = []
    seen: set[int] = set()
    current: Optional[BaseException] = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        frames = [
            f"  {frame.filename}:{frame.lineno} in {frame.name}"
            for frame in traceback.extract_tb(current.__traceback__)
        ]
        blocks.append("\n".join([current.__class__.__name__, *frames]))
        current = current.__cause__ or (
            None if current.__suppress_context__ else current.__context__
        )
    return "\n-- caused by --\n".join(blocks)


async def log_backend_error(
    db: AsyncSession,
    request: Optional[Request],
    exc: BaseException,
    level: str = "ERROR",
    status_code: Optional[int] = None,
    extra: Optional[dict[str, Any]] = None,
) -> None:
    """Persist a backend error with contextual metadata."""
    path: Optional[str] = None
    method: Optional[str] = None
    user_agent: Optional[str] = None
    ip_hash: Optional[str] = None
    request_id: Optional[str] = None

    if request is not None:
        path = request.url.path
        method = request.method
        user_agent = request.headers.get("user-agent")
        client_host = request.client.host if request.client else None
        if client_host:
            ip_hash = hash_text(client_host)
        request_id = request.headers.get("x-request-id")

    log_entry = ErrorLog(
        source="backend",
        level=level,
        message=public_error_text(exc),
        exception_type=exc.__class__.__name__,
        stack_trace=sanitized_stack(exc),
        path=path,
        method=method,
        status_code=status_code,
        user_agent=user_agent,
        ip_hash=ip_hash,
        request_id=request_id,
        extra=extra or {},
    )
    db.add(log_entry)
    await db.commit()


async def log_backend_message(
    db: AsyncSession,
    request: Optional[Request],
    message: str,
    level: str = "ERROR",
    path: Optional[str] = None,
    method: Optional[str] = None,
    status_code: Optional[int] = None,
    extra: Optional[dict[str, Any]] = None,
) -> None:
    """Persist a backend message (handled error or warning) without an exception."""
    if request is not None:
        path = path or request.url.path
        method = method or request.method
    user_agent: Optional[str] = None
    ip_hash: Optional[str] = None
    request_id: Optional[str] = None
    if request is not None:
        user_agent = request.headers.get("user-agent")
        if request.client:
            ip_hash = hash_text(request.client.host)
        request_id = request.headers.get("x-request-id")

    log_entry = ErrorLog(
        source="backend",
        level=level,
        message=message,
        exception_type=None,
        stack_trace=None,
        path=path,
        method=method,
        status_code=status_code,
        user_agent=user_agent,
        ip_hash=ip_hash,
        request_id=request_id,
        extra=extra or {},
    )
    db.add(log_entry)
    await db.commit()


async def log_frontend_error(
    db: AsyncSession,
    *,
    message: str,
    stack_trace: Optional[str],
    route: Optional[str],
    user_agent: Optional[str],
    level: str = "ERROR",
    extra: Optional[dict[str, Any]] = None,
    client_ip: Optional[str] = None,
) -> None:
    """Persist a frontend error reported by the browser."""
    ip_hash: Optional[str] = None
    if client_ip:
        ip_hash = hash_text(client_ip)

    log_entry = ErrorLog(
        source="frontend",
        level=level,
        message=message,
        exception_type=None,
        stack_trace=stack_trace,
        path=route,
        method=None,
        status_code=None,
        user_agent=user_agent,
        ip_hash=ip_hash,
        request_id=None,
        extra=extra or {},
    )
    db.add(log_entry)
    await db.commit()

