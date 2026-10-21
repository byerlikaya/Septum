from __future__ import annotations

"""Centralized error logging service for backend and frontend errors."""

import logging
import traceback
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import Request
from prometheus_client import Counter
from sqlalchemy import String, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import public_error_text
from ..middleware.rate_limit import error_log_write_allowed
from ..models.error_log import ErrorLog
from ..utils.client_ip import resolve_client_ip
from ..utils.crypto import hash_text

logger = logging.getLogger(__name__)


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


# Width of every bounded text column, read from the model so the two stay in step.
_COLUMN_WIDTHS: dict[str, int] = {
    column.name: column.type.length
    for column in ErrorLog.__table__.columns
    if isinstance(column.type, String) and column.type.length
}


def _error_log(**fields: Any) -> ErrorLog:
    """Build an ``ErrorLog`` whose text values always fit the table.

    URLs, headers and client-supplied fields can be arbitrarily long and
    can contain NUL characters. Every text value loses its NULs (PostgreSQL
    rejects them in text columns) and every bounded one is cut to its
    column width (PostgreSQL enforces ``VARCHAR(n)``, SQLite does not), so
    such input never fails the insert.
    """
    for name, value in fields.items():
        if isinstance(value, str):
            value = value.replace("\x00", "")
            width = _COLUMN_WIDTHS.get(name)
            fields[name] = value[:width] if width else value
    return ErrorLog(**fields)


UNMATCHED_PATH = "<unmatched>"


def _request_path(request: Request) -> str:
    """Return the matched route's template, or ``<unmatched>`` when no route matched.

    The template (``/api/documents/{document_id}``) keeps ids and other
    values from the URL out of the error log; an unmatched URL is never
    stored, since it can be anything a client typed.
    """
    route = request.scope.get("route")
    template = getattr(route, "path_format", None) or getattr(route, "path", None)
    return template or UNMATCHED_PATH


# Rows past a caller's error-log write budget are counted instead.
ERROR_LOG_WRITES_DROPPED = Counter(
    "septum_error_log_writes_dropped_total",
    "Error log rows not written because the caller spent its error-log write budget",
    ["status"],
)


ERROR_LOG_WRITE_FAILURES = Counter(
    "septum_error_log_write_failures_total",
    "Error log rows that could not be stored (the response was still sent)",
)


def report_error_log_failure(exc: BaseException) -> None:
    """Record that an error log row could not be stored: a warning by class name and a counter.

    For callers that must keep their own response or task going when the
    log write fails; the failure itself is never silent.
    """
    logger.warning("Error-log persistence failed: %s", type(exc).__name__)
    ERROR_LOG_WRITE_FAILURES.inc()


async def _within_budget(
    request: Optional[Request], identity: Optional[str], status_code: Optional[int], fault: bool
) -> bool:
    """Spend one row of the caller's budget; count and refuse once it is spent.

    The single gate for every row tied to a caller: the request's identity,
    or an explicit *identity* (``user:<id>``) for background work started
    on someone's behalf. Server faults draw on their own smaller budget, so
    a flood of 4xx noise cannot crowd them out. Rows with neither (system
    work) are not budgeted.
    """
    if request is None and identity is None:
        return True
    if await error_log_write_allowed(request, identity=identity, server_fault=fault):
        return True
    ERROR_LOG_WRITES_DROPPED.labels(status=str(status_code) if status_code else "none").inc()
    return False


def _is_fault(status_code: Optional[int], default: bool) -> bool:
    return default if status_code is None else status_code >= 500


async def log_backend_error(
    db: AsyncSession,
    request: Optional[Request],
    exc: BaseException,
    level: str = "ERROR",
    status_code: Optional[int] = None,
    extra: Optional[dict[str, Any]] = None,
    identity: Optional[str] = None,
) -> None:
    """Persist a backend error with contextual metadata, within the caller's budget.

    Without a status code the error counts as a server fault. *identity*
    budgets a row written without a request (background work for a user).
    """
    if not await _within_budget(request, identity, status_code, _is_fault(status_code, default=True)):
        return
    path: Optional[str] = None
    method: Optional[str] = None
    user_agent: Optional[str] = None
    ip_hash: Optional[str] = None
    request_id: Optional[str] = None

    if request is not None:
        path = _request_path(request)
        method = request.method
        user_agent = request.headers.get("user-agent")
        client_host = resolve_client_ip(request)
        if client_host:
            ip_hash = hash_text(client_host)
        request_id = request.headers.get("x-request-id")

    log_entry = _error_log(
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
    """Persist a backend message (handled error or warning) without an exception, within budget.

    Without a status code the message counts against the 4xx/noise budget.
    """
    if not await _within_budget(request, None, status_code, _is_fault(status_code, default=False)):
        return
    if request is not None:
        path = path or _request_path(request)
        method = method or request.method
    user_agent: Optional[str] = None
    ip_hash: Optional[str] = None
    request_id: Optional[str] = None
    if request is not None:
        user_agent = request.headers.get("user-agent")
        client_host = resolve_client_ip(request)
        if client_host:
            ip_hash = hash_text(client_host)
        request_id = request.headers.get("x-request-id")

    log_entry = _error_log(
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

    log_entry = _error_log(
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



ERROR_LOG_PURGE_BATCH_SIZE = 5000


async def purge_expired_error_logs(
    db: AsyncSession,
    retention_days: int,
    now: Optional[datetime] = None,
    batch_size: int = ERROR_LOG_PURGE_BATCH_SIZE,
) -> int:
    """Delete error log rows older than *retention_days*; return how many went.

    Rows carry hashed client addresses and user agents, so they are not
    kept forever. ``retention_days`` below 1 keeps everything. Rows go in
    batches of *batch_size* ids with a commit after each, so SQLite's
    write lock is only held briefly even after a long backlog.
    """
    if retention_days < 1:
        return 0
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=retention_days)
    removed = 0
    while True:
        ids = (
            await db.execute(
                select(ErrorLog.id).where(ErrorLog.created_at < cutoff).limit(batch_size)
            )
        ).scalars().all()
        if not ids:
            return removed
        await db.execute(delete(ErrorLog).where(ErrorLog.id.in_(ids)))
        await db.commit()
        removed += len(ids)
