from __future__ import annotations

"""Fire-and-forget audit event logger for privacy compliance.

All functions commit in a separate session so audit failures never block
the main request pipeline. Raw PII values are never stored — only entity
type names, counts, and masked placeholder examples.
"""

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ..models.audit_event import AuditEvent

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
    document_name: Optional[str] = None,
    masked_query: Optional[str] = None,
    placeholder_samples: Optional[List[str]] = None,
) -> None:
    """Record a PII detection event (document upload or chat query)."""
    merged_extra: Dict[str, Any] = extra or {}
    if document_name:
        merged_extra["document_name"] = document_name
    if masked_query:
        merged_extra["masked_query"] = masked_query[:200]
    if placeholder_samples:
        merged_extra["placeholder_samples"] = placeholder_samples[:5]

    await _persist(
        db,
        AuditEvent(
            event_type="pii_detected",
            session_id=session_id,
            document_id=document_id,
            regulation_ids=regulation_ids,
            entity_types_detected=entity_type_counts,
            entity_count=total_count,
            extra=merged_extra,
        ),
    )


async def log_deanonymization(
    db: AsyncSession,
    *,
    document_id: int,
    entity_count: int,
    strategy: str,
    session_id: Optional[str] = None,
    document_name: Optional[str] = None,
) -> None:
    """Record a de-anonymization event."""
    extra: Dict[str, Any] = {"strategy": strategy}
    if document_name:
        extra["document_name"] = document_name

    await _persist(
        db,
        AuditEvent(
            event_type="deanonymization_performed",
            session_id=session_id,
            document_id=document_id,
            regulation_ids=[],
            entity_types_detected={},
            entity_count=entity_count,
            extra=extra,
        ),
    )


async def log_document_event(
    db: AsyncSession,
    *,
    document_id: int,
    event_type: str,
    document_name: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Record a document lifecycle event (uploaded, deleted)."""
    merged_extra: Dict[str, Any] = extra or {}
    if document_name:
        merged_extra["document_name"] = document_name

    await _persist(
        db,
        AuditEvent(
            event_type=event_type,
            document_id=document_id,
            regulation_ids=[],
            entity_types_detected={},
            entity_count=0,
            extra=merged_extra,
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
