from __future__ import annotations

"""Audit trail endpoints for GDPR/KVKK compliance reporting.

All endpoints are read-only. Audit events are append-only — no update or
delete operations are exposed.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.audit_event import AuditEvent

router = APIRouter(prefix="/api/audit", tags=["audit"])


class AuditEventResponse(BaseModel):
    """Serialized audit event for API responses."""

    id: int
    created_at: datetime
    event_type: str
    session_id: Optional[str]
    document_id: Optional[int]
    regulation_ids: List[str]
    entity_types_detected: Dict[str, int]
    entity_count: int
    extra: Optional[Dict[str, Any]]


class AuditListResponse(BaseModel):
    """Paginated list of audit events."""

    items: List[AuditEventResponse]
    total: int
    page: int
    page_size: int


class ComplianceReportResponse(BaseModel):
    """Aggregated compliance report for a document."""

    document_id: int
    total_pii_events: int
    total_deanonymization_events: int
    total_entities_detected: int
    entity_type_breakdown: Dict[str, int]
    regulation_ids_used: List[str]
    events: List[AuditEventResponse]


@router.get("/", response_model=AuditListResponse)
async def list_audit_events(
    db: AsyncSession = Depends(get_db),
    event_type: Optional[str] = Query(None),
    document_id: Optional[int] = Query(None),
    session_id: Optional[str] = Query(None),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> AuditListResponse:
    """List audit events with optional filters and pagination."""
    stmt = select(AuditEvent)
    count_stmt = select(func.count(AuditEvent.id))

    if event_type:
        stmt = stmt.where(AuditEvent.event_type == event_type)
        count_stmt = count_stmt.where(AuditEvent.event_type == event_type)
    if document_id is not None:
        stmt = stmt.where(AuditEvent.document_id == document_id)
        count_stmt = count_stmt.where(AuditEvent.document_id == document_id)
    if session_id:
        stmt = stmt.where(AuditEvent.session_id == session_id)
        count_stmt = count_stmt.where(AuditEvent.session_id == session_id)
    if date_from:
        stmt = stmt.where(AuditEvent.created_at >= date_from)
        count_stmt = count_stmt.where(AuditEvent.created_at >= date_from)
    if date_to:
        stmt = stmt.where(AuditEvent.created_at <= date_to)
        count_stmt = count_stmt.where(AuditEvent.created_at <= date_to)

    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    stmt = stmt.order_by(AuditEvent.created_at.desc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    events = result.scalars().all()

    return AuditListResponse(
        items=[_to_response(e) for e in events],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{document_id}/report", response_model=ComplianceReportResponse)
async def get_document_compliance_report(
    document_id: int,
    db: AsyncSession = Depends(get_db),
) -> ComplianceReportResponse:
    """Generate a compliance report for a specific document."""
    stmt = (
        select(AuditEvent)
        .where(AuditEvent.document_id == document_id)
        .order_by(AuditEvent.created_at.asc())
    )
    result = await db.execute(stmt)
    events = result.scalars().all()

    pii_events = [e for e in events if e.event_type == "pii_detected"]
    deanon_events = [e for e in events if e.event_type == "deanonymization_performed"]

    total_entities = sum(e.entity_count for e in pii_events)
    type_breakdown: Dict[str, int] = {}
    regulation_ids: set[str] = set()

    for event in pii_events:
        for etype, ecount in (event.entity_types_detected or {}).items():
            type_breakdown[etype] = type_breakdown.get(etype, 0) + ecount
        regulation_ids.update(event.regulation_ids or [])

    return ComplianceReportResponse(
        document_id=document_id,
        total_pii_events=len(pii_events),
        total_deanonymization_events=len(deanon_events),
        total_entities_detected=total_entities,
        entity_type_breakdown=type_breakdown,
        regulation_ids_used=sorted(regulation_ids),
        events=[_to_response(e) for e in events],
    )


@router.get("/session/{session_id}", response_model=List[AuditEventResponse])
async def get_session_audit(
    session_id: str,
    db: AsyncSession = Depends(get_db),
) -> List[AuditEventResponse]:
    """Get all audit events for a chat session."""
    stmt = (
        select(AuditEvent)
        .where(AuditEvent.session_id == session_id)
        .order_by(AuditEvent.created_at.asc())
    )
    result = await db.execute(stmt)
    events = result.scalars().all()
    return [_to_response(e) for e in events]


def _to_response(event: AuditEvent) -> AuditEventResponse:
    return AuditEventResponse(
        id=event.id,
        created_at=event.created_at,
        event_type=event.event_type,
        session_id=event.session_id,
        document_id=event.document_id,
        regulation_ids=event.regulation_ids or [],
        entity_types_detected=event.entity_types_detected or {},
        entity_count=event.entity_count,
        extra=event.extra,
    )
