from __future__ import annotations

"""Append-only audit event model for GDPR/KVKK compliance tracking.

Stores privacy-relevant events (PII detection, de-anonymization, regulation
changes) without ever recording raw PII values — only entity type names and
counts.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import DateTime, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from . import Base


class AuditEvent(Base):
    """Immutable record of a privacy-relevant system event."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    session_id: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True, index=True
    )
    document_id: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, index=True
    )
    regulation_ids: Mapped[List[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    entity_types_detected: Mapped[Dict[str, int]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    entity_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    extra: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
