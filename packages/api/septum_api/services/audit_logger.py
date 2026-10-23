from __future__ import annotations

"""Fire-and-forget audit event logger for privacy compliance.

All functions commit in a separate session so audit failures never block
the main request pipeline. Raw PII values are never stored — only entity
type names, counts, and masked placeholder examples. Documents are named
by id only: their filename (which can carry personal data) is resolved at
read time by the audit router.
"""

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import Text, cast, select, update
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
    masked_query: Optional[str] = None,
    placeholder_samples: Optional[List[str]] = None,
) -> Optional[AuditEvent]:
    """Record a PII detection event (document upload or chat query).

    Returns the persisted :class:`AuditEvent` so callers can link the
    associated :class:`EntityDetection` rows via ``audit_event_id`` —
    this is the provenance trail the dashboard uses to jump from an
    audit log entry to the exact highlighted entities.
    """
    merged_extra: Dict[str, Any] = dict(extra or {})
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


def build_document_event(
    *,
    document_id: int,
    event_type: str,
    extra: Optional[Dict[str, Any]] = None,
) -> AuditEvent:
    """Build, without persisting, a document event for the caller's transaction.

    For changes whose audit row must commit or fail together with the
    change itself; unlike :func:`log_document_event` nothing is swallowed.
    """
    return AuditEvent(
        event_type=event_type,
        document_id=document_id,
        regulation_ids=[],
        entity_types_detected={},
        entity_count=0,
        extra=dict(extra or {}),
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
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Record a document lifecycle event (uploaded, deleted)."""
    await _persist(
        db,
        build_document_event(
            document_id=document_id,
            event_type=event_type,
            extra=extra,
        ),
    )


# Event types whose ``extra`` names the acting user as ``user_id``.
USER_REFERENCING_EVENT_TYPES = (DOCUMENT_LANGUAGE_CHANGED,)


async def forget_user_in_audit_events(db: AsyncSession, user_id: int) -> int:
    """Set ``extra.user_id`` to null in every audit event that names ``user_id``.

    Runs in the caller's transaction (no commit), so a user deletion and
    the unlinking of its audit trail commit together. Callers delete the
    user row first: a concurrent language ``PATCH`` re-reads its user under
    a lock, so it either commits before this runs and is unlinked by it, or
    finds the user gone and writes nothing. Returns the number of events
    changed, each written back with only ``user_id`` changed.

    SQL reads no key of ``extra`` (PostgreSQL rejects stored ``\\u0000`` and
    lone-surrogate escapes, and a non-integer cast aborts): it pre-filters
    on text and :func:`_names_user` decides. Rows the app never writes, such
    as a number outside the float range or extreme nesting, may still fail
    when the ORM parses them.
    """
    rows = await db.execute(
        select(AuditEvent.id, AuditEvent.extra)
        .where(
            AuditEvent.event_type.in_(USER_REFERENCING_EVENT_TYPES),
            cast(AuditEvent.extra, Text).like(f"%{user_id}%"),
        )
        .order_by(AuditEvent.id)
        .with_for_update()
    )
    unlinked = [
        {"id": event_id, "extra": {**extra, "user_id": None}}
        for event_id, extra in rows
        if _names_user(extra, user_id)
    ]
    if unlinked:
        await db.execute(update(AuditEvent), unlinked)
    return len(unlinked)


def _names_user(extra: Any, user_id: int) -> bool:
    """Whether ``extra.user_id`` is ``user_id`` as an integer or its decimal string."""
    value = extra.get("user_id") if isinstance(extra, dict) else None
    return (type(value) is int and value == user_id) or value == str(user_id)


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
