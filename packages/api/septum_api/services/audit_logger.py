from __future__ import annotations

"""Fire-and-forget audit event logger for privacy compliance.

All functions commit in a separate session so audit failures never block
the main request pipeline. Raw PII values are never stored — only entity
type names, counts, and masked placeholder examples.
"""

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import JSON, cast, func, literal_column, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.audit_event import AuditEvent

logger = logging.getLogger(__name__)

DOCUMENT_LANGUAGE_CHANGED = "document_language_changed"

# ``extra.reason`` of a ``document_language_changed`` event the pipeline
# writes when it clears an override. Readers report only these codes.
OVERRIDE_REFUSED_SCRIPT_MISMATCH = "script_mismatch"
OVERRIDE_REFUSAL_REASONS = frozenset({OVERRIDE_REFUSED_SCRIPT_MISMATCH})


async def _persist(db: AsyncSession, event: AuditEvent) -> Optional[AuditEvent]:
    """Add and commit an audit event, logging errors without raising.

    Returns the committed event (with ``id`` populated) on success,
    ``None`` on failure so callers can skip follow-up work like linking
    EntityDetection rows to the event.
    """
    try:
        db.add(event)
        await db.commit()
        await db.refresh(event)
        return event
    except Exception as exc:
        logger.error("Failed to persist audit event: %s", type(exc).__name__)
        await db.rollback()
        return None


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
) -> Optional[AuditEvent]:
    """Record a PII detection event (document upload or chat query).

    Returns the persisted :class:`AuditEvent` so callers can link the
    associated :class:`EntityDetection` rows via ``audit_event_id`` —
    this is the provenance trail the dashboard uses to jump from an
    audit log entry to the exact highlighted entities.
    """
    merged_extra: Dict[str, Any] = extra or {}
    if document_name:
        merged_extra["document_name"] = document_name
    if masked_query:
        merged_extra["masked_query"] = masked_query[:200]
    if placeholder_samples:
        merged_extra["placeholder_samples"] = placeholder_samples[:5]

    return await _persist(
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


def build_document_event(
    *,
    document_id: int,
    event_type: str,
    document_name: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> AuditEvent:
    """Build, without persisting, a document event for the caller's transaction.

    For changes whose audit row must commit or fail together with the
    change itself; unlike :func:`log_document_event` nothing is swallowed.
    """
    merged_extra: Dict[str, Any] = dict(extra or {})
    if document_name:
        merged_extra["document_name"] = document_name
    return AuditEvent(
        event_type=event_type,
        document_id=document_id,
        regulation_ids=[],
        entity_types_detected={},
        entity_count=0,
        extra=merged_extra,
    )


def language_override_event(
    document_id: int,
    user_id: Optional[int],
    previous: Optional[str],
    new: Optional[str],
    reason: Optional[str] = None,
) -> AuditEvent:
    """Build the audit row of a language override change (codes only).

    ``user_id`` is ``None`` when the pipeline cleared the override, with
    ``reason`` naming why (one of :data:`OVERRIDE_REFUSAL_REASONS`).
    """
    extra: Dict[str, Any] = {"user_id": user_id, "previous_override": previous, "override": new}
    if reason is not None:
        extra["reason"] = reason
    return build_document_event(
        document_id=document_id, event_type=DOCUMENT_LANGUAGE_CHANGED, extra=extra
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
    await _persist(
        db,
        build_document_event(
            document_id=document_id,
            event_type=event_type,
            document_name=document_name,
            extra=extra,
        ),
    )


# Event types whose ``extra`` names the acting user as ``user_id``.
USER_REFERENCING_EVENT_TYPES = (DOCUMENT_LANGUAGE_CHANGED,)


async def forget_user_in_audit_events(db: AsyncSession, user_id: int) -> int:
    """Set ``extra.user_id`` to null in every audit event that names ``user_id``.

    One ``UPDATE`` in the caller's transaction (no commit), so a user
    deletion and the unlinking of its audit trail commit together. Callers
    delete the user row first: a concurrent language ``PATCH`` re-reads its
    user under a lock, so it either commits before this statement runs and
    is unlinked by it, or finds the user gone and writes nothing. Returns
    the number of events changed.
    """
    if db.get_bind().dialect.name == "postgresql":
        unlinked = cast(
            func.jsonb_set(
                cast(AuditEvent.extra, JSONB),
                literal_column("'{user_id}'"),
                literal_column("'null'::jsonb"),
            ),
            JSON,
        )
    else:
        unlinked = func.json_set(AuditEvent.extra, "$.user_id", func.json("null"))
    result = await db.execute(
        update(AuditEvent)
        .where(
            AuditEvent.event_type.in_(USER_REFERENCING_EVENT_TYPES),
            AuditEvent.extra["user_id"].as_integer() == user_id,
        )
        .values(extra=unlinked)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount


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
