from __future__ import annotations

"""Fire-and-forget audit event logger for privacy compliance.

All functions commit in a separate session so audit failures never block
the main request pipeline. Raw PII values are never stored — only entity
type names and counts.
"""

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_event import AuditEvent

logger = logging.getLogger(__name__)


async def _persist(db: AsyncSession, event: AuditEvent) -> None:
    """Add and commit an audit event, logging errors without raising."""
    try:
        db.add(event)
        await db.commit()
    except Exception:
        logger.exception("Failed to persist audit event")
        await db.rollback()


async def log_pii_detected(
    db: AsyncSession,
    *,
    document_id: int,
    regulation_ids: List[str],
    entity_type_counts: Dict[str, int],
    total_count: int,
    session_id: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Record a PII detection event (document upload or chat query)."""
    await _persist(
        db,
        AuditEvent(
            event_type="pii_detected",
            session_id=session_id,
            document_id=document_id,
            regulation_ids=regulation_ids,
            entity_types_detected=entity_type_counts,
            entity_count=total_count,
            extra=extra,
        ),
    )


async def log_deanonymization(
    db: AsyncSession,
    *,
    document_id: int,
    entity_count: int,
    strategy: str,
    session_id: Optional[str] = None,
) -> None:
    """Record a de-anonymization event."""
    await _persist(
        db,
        AuditEvent(
            event_type="deanonymization_performed",
            session_id=session_id,
            document_id=document_id,
            regulation_ids=[],
            entity_types_detected={},
            entity_count=entity_count,
            extra={"strategy": strategy},
        ),
    )


async def log_document_event(
    db: AsyncSession,
    *,
    document_id: int,
    event_type: str,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Record a document lifecycle event (uploaded, deleted)."""
    await _persist(
        db,
        AuditEvent(
            event_type=event_type,
            document_id=document_id,
            regulation_ids=[],
            entity_types_detected={},
            entity_count=0,
            extra=extra,
        ),
    )


async def log_regulation_change(
    db: AsyncSession,
    *,
    regulation_ids: List[str],
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Record a regulation activation/deactivation event."""
    await _persist(
        db,
        AuditEvent(
            event_type="regulation_changed",
            regulation_ids=regulation_ids,
            entity_types_detected={},
            entity_count=0,
            extra=extra,
        ),
    )
