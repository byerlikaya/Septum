from __future__ import annotations

"""FastAPI router for centralized error log management."""

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import Select, delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..middleware.rate_limit import get_limiter
from ..models.error_log import ErrorLog
from ..models.user import User

_limiter = get_limiter()

_MAX_FRONTEND_MESSAGE_LEN = 2000
_MAX_FRONTEND_STACK_LEN = 8000
_MAX_FRONTEND_EXTRA_BYTES = 8 * 1024
from ..utils.auth_dependency import get_optional_user, require_role

router = APIRouter(prefix="/api/error-logs", tags=["error-logs"])


class ErrorLogOut(BaseModel):
    """Serialized error log entry for API responses."""

    id: int
    created_at: datetime
    source: str
    level: str
    message: str
    exception_type: Optional[str]
    path: Optional[str]
    method: Optional[str]
    status_code: Optional[int]
    user_agent: Optional[str]


class ErrorLogDetailOut(ErrorLogOut):
    """Detailed error log entry including stack trace and extra metadata."""

    stack_trace: Optional[str]
    extra: Optional[dict[str, Any]]


class ErrorLogListResponse(BaseModel):
    """Paginated error log list response."""

    items: list[ErrorLogOut]
    total: int
    page: int
    page_size: int


@router.get("", response_model=ErrorLogListResponse)
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


@router.get("/{log_id}", response_model=ErrorLogDetailOut)
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


@router.post("/frontend", status_code=204, response_model=None)
@_limiter.limit("60/hour")
async def ingest_frontend_error(
    request: Request,
    payload: FrontendErrorIn,
    db: AsyncSession = Depends(get_db),
    _user: User | None = Depends(get_optional_user),
) -> None:
    """Accept an error report from the frontend and persist it.

    Uses optional auth because frontend error reporting must work for
    pre-login failures (network errors on the login page itself).
    Rate-limited to 60/hour per identity (IP for unauthenticated
    callers, API key prefix otherwise) so a misbehaving client cannot
    flood the error log table and hide real failures under noise.
    Inputs are length-capped at the same time so a single malformed
    request cannot exhaust storage on its own.
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

    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    await log_frontend_error(
        db=db,
        message=message,
        stack_trace=stack,
        route=payload.route,
        user_agent=user_agent,
        level=payload.level or "ERROR",
        extra=extra,
        client_ip=client_ip,
    )


@router.delete("", status_code=204, response_model=None)
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

