from __future__ import annotations

"""FastAPI router for centralized error log management."""

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import Select, delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..middleware.rate_limit import route_limit
from ..models.error_log import ErrorLog
from ..models.user import User

_MAX_FRONTEND_MESSAGE_LEN = 2000
_MAX_FRONTEND_STACK_LEN = 8000
_MAX_FRONTEND_EXTRA_BYTES = 8 * 1024
_FRONTEND_LEVELS = frozenset({"ERROR", "WARNING", "INFO"})
_DEFAULT_FRONTEND_LEVEL = "ERROR"
from ..utils.auth_dependency import get_optional_user, require_role
from ..utils.client_ip import resolve_client_ip

router = APIRouter(prefix="/api/error-logs", tags=["error-logs"])


class ErrorLogOut(BaseModel):
    """Serialized error log entry for API responses."""

    id: int
    created_at: datetime
    source: str
    level: str
    message: str
    exception_type: Optional[str] = None
    path: Optional[str] = None
    method: Optional[str] = None
    status_code: Optional[int] = None
    user_agent: Optional[str] = None


class ErrorLogDetailOut(ErrorLogOut):
    """Detailed error log entry including stack trace and extra metadata."""

    stack_trace: Optional[str] = None
    extra: Optional[dict[str, Any]] = None


class ErrorLogListResponse(BaseModel):
    """Paginated error log list response."""

    items: list[ErrorLogOut]
    total: int
    page: int
    page_size: int


@router.get("")
async def list_error_logs(
    page: int = 1,
    page_size: int = 50,
    source: Optional[str] = None,
    level: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_role("admin")),
) -> ErrorLogListResponse:
    """Return a paginated list of error logs."""
    page = max(page, 1)
    page_size = max(min(page_size, 200), 1)

    stmt: Select[tuple[ErrorLog]] = select(ErrorLog)
    if source:
        stmt = stmt.where(ErrorLog.source == source)
    if level:
        stmt = stmt.where(ErrorLog.level == level)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_result = await db.execute(count_stmt)
    total = int(total_result.scalar_one() or 0)

    stmt = stmt.order_by(desc(ErrorLog.created_at)).offset(
        (page - 1) * page_size
    ).limit(page_size)
    result = await db.execute(stmt)
    rows = result.scalars().all()

    items = [
        ErrorLogOut(
            id=row.id,
            created_at=row.created_at,
            source=row.source,
            level=row.level,
            message=row.message,
            exception_type=row.exception_type,
            path=row.path,
            method=row.method,
            status_code=row.status_code,
            user_agent=row.user_agent,
        )
        for row in rows
    ]

    return ErrorLogListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{log_id}")
async def get_error_log(
    log_id: int,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_role("admin")),
) -> ErrorLogDetailOut:
    """Return a single error log entry with full details."""
    result = await db.execute(select(ErrorLog).where(ErrorLog.id == log_id))
    row = result.scalar_one_or_none()
    if row is None:
        from fastapi import HTTPException, status

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Error log entry not found.",
        )

    return ErrorLogDetailOut(
        id=row.id,
        created_at=row.created_at,
        source=row.source,
        level=row.level,
        message=row.message,
        exception_type=row.exception_type,
        path=row.path,
        method=row.method,
        status_code=row.status_code,
        user_agent=row.user_agent,
        stack_trace=row.stack_trace,
        extra=row.extra,
    )


class FrontendErrorIn(BaseModel):
    """Payload for frontend-reported errors."""

    message: str
    stack_trace: Optional[str] = None
    route: Optional[str] = None
    level: Optional[str] = "ERROR"
    extra: Optional[dict[str, Any]] = None


@router.post("/frontend", status_code=204)
# Per /48, twice the per-client 60/hour: room for a second browser on the
# same site, while 65 536 rotating /64s cannot each get their own 60/hour.
@route_limit("60/hour", fail_open=False, ipv6_site_limit="120/hour")
async def ingest_frontend_error(
    request: Request,
    payload: FrontendErrorIn,
    db: AsyncSession = Depends(get_db),
    _user: User | None = Depends(get_optional_user),
) -> None:
    """Accept an error report from the frontend and persist it.

    Uses optional auth because frontend error reporting must work for
    pre-login failures (network errors on the login page itself).
    Rate-limited to 60/hour per identity (``user:<id>``, API key prefix,
    or client address) plus 120/hour per IPv6 /48, so a misbehaving client
    cannot flood the error log table and hide real failures under noise.
    Inputs are length-capped at the same time so a single malformed
    request cannot exhaust storage on its own (the logger also cuts every
    bounded column to its width, so long input never fails the insert);
    ``level`` outside the known set becomes ``ERROR``. The limits stay
    enforced from memory while the rate-limit storage is down.
    """
    import json as _json

    from ..services.error_logger import log_frontend_error

    message = (payload.message or "")[:_MAX_FRONTEND_MESSAGE_LEN]
    stack = (
        payload.stack_trace[:_MAX_FRONTEND_STACK_LEN]
        if payload.stack_trace
        else None
    )
    extra = payload.extra
    if extra is not None:
        try:
            if len(_json.dumps(extra)) > _MAX_FRONTEND_EXTRA_BYTES:
                extra = {"truncated": True}
        except (TypeError, ValueError):
            extra = {"invalid": True}

    # Query strings and fragments can carry tokens or personal data.
    route = payload.route.split("?", 1)[0].split("#", 1)[0] if payload.route else None
    level = (payload.level or "").upper()
    if level not in _FRONTEND_LEVELS:
        level = _DEFAULT_FRONTEND_LEVEL
    client_ip = resolve_client_ip(request)
    user_agent = request.headers.get("user-agent")

    await log_frontend_error(
        db=db,
        message=message,
        stack_trace=stack,
        route=route,
        user_agent=user_agent,
        level=level,
        extra=extra,
        client_ip=client_ip,
    )


@router.delete("", status_code=204)
async def clear_error_logs(
    source: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_role("admin")),
) -> None:
    """Delete error log entries, optionally filtered by source."""
    if source:
        await db.execute(delete(ErrorLog).where(ErrorLog.source == source))
    else:
        await db.execute(delete(ErrorLog))
    await db.commit()

