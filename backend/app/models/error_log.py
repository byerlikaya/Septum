from __future__ import annotations

"""Error logging models for centralized backend and frontend error tracking."""

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from . import Base


class ErrorLog(Base):
    """Persistent error log entry for backend and frontend sources."""

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, index=True
    )
    source: Mapped[str] = mapped_column(String(32), index=True)
    level: Mapped[str] = mapped_column(String(16), default="ERROR", index=True)
    message: Mapped[str] = mapped_column(Text)
    exception_type: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    stack_trace: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True, index=True)
    method: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    status_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    request_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    ip_hash: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    extra: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

