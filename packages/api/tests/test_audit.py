"""Tests for the audit event system.

Verifies audit events are created correctly and never contain raw PII.
Uses an in-memory SQLite database.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from septum_api.models import Base
from septum_api.models.audit_event import AuditEvent
from septum_api.models.document import Chunk, Document
from septum_api.models.entity_detection import EntityDetection
from septum_api.services.audit_logger import (
    log_deanonymization,
    log_document_event,
    log_pii_detected,
    log_regulation_change,
)


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_log_pii_detected_creates_event(db_session: AsyncSession) -> None:
    """log_pii_detected should create an AuditEvent with correct fields."""
    await log_pii_detected(
        db_session,
        document_id=1,
        regulation_ids=["gdpr", "kvkk"],
        entity_type_counts={"PERSON_NAME": 3, "PHONE_NUMBER": 1},
        total_count=4,
        session_id="test-session-123",
        extra={"source": "document_ingestion"},
    )

    result = await db_session.execute(select(AuditEvent))
    events = result.scalars().all()

    assert len(events) == 1
    event = events[0]
    assert event.event_type == "pii_detected"
    assert event.document_id == 1
    assert event.session_id == "test-session-123"
    assert event.regulation_ids == ["gdpr", "kvkk"]
    assert event.entity_types_detected == {"PERSON_NAME": 3, "PHONE_NUMBER": 1}
    assert event.entity_count == 4
    assert event.extra == {"source": "document_ingestion"}


@pytest.mark.asyncio
async def test_log_deanonymization_creates_event(db_session: AsyncSession) -> None:
    """log_deanonymization should create an AuditEvent with strategy info."""
    await log_deanonymization(
        db_session,
        document_id=2,
        entity_count=5,
        strategy="simple",
        session_id="session-456",
    )

    result = await db_session.execute(select(AuditEvent))
    events = result.scalars().all()

    assert len(events) == 1
    event = events[0]
    assert event.event_type == "deanonymization_performed"
    assert event.entity_count == 5
    assert event.extra == {"strategy": "simple"}


@pytest.mark.asyncio
async def test_log_document_event_creates_event(db_session: AsyncSession) -> None:
    """log_document_event should create a document lifecycle event."""
    await log_document_event(
        db_session,
        document_id=3,
        event_type="document_uploaded",
        extra={"detected_language": "en"},
    )

    result = await db_session.execute(select(AuditEvent))
    events = result.scalars().all()

    assert len(events) == 1
    assert events[0].event_type == "document_uploaded"
    assert events[0].document_id == 3


@pytest.mark.asyncio
async def test_log_regulation_change_creates_event(db_session: AsyncSession) -> None:
    """log_regulation_change should create a regulation change event."""
    await log_regulation_change(
        db_session,
        regulation_ids=["gdpr", "hipaa"],
        extra={"action": "activated"},
    )

    result = await db_session.execute(select(AuditEvent))
    events = result.scalars().all()

    assert len(events) == 1
    assert events[0].event_type == "regulation_changed"
    assert events[0].regulation_ids == ["gdpr", "hipaa"]


@pytest.mark.asyncio
async def test_audit_events_never_contain_pii_values(db_session: AsyncSession) -> None:
    """Audit events must never contain raw PII values in any field."""
    raw_pii_samples = ["John Smith", "john@example.com", "+1-555-0123", "123-45-6789"]

    await log_pii_detected(
        db_session,
        document_id=1,
        regulation_ids=["gdpr"],
        entity_type_counts={"PERSON_NAME": 1, "EMAIL": 1, "PHONE_NUMBER": 1},
        total_count=3,
        extra={"source": "test"},
    )

    result = await db_session.execute(select(AuditEvent))
    event = result.scalars().first()
    assert event is not None

    event_str = str(event.entity_types_detected) + str(event.regulation_ids) + str(event.extra or "")

    for pii in raw_pii_samples:
        assert pii not in event_str, f"Raw PII '{pii}' found in audit event"


@pytest.mark.asyncio
async def test_multiple_audit_events_in_sequence(db_session: AsyncSession) -> None:
    """Multiple audit events should be stored independently."""
    await log_pii_detected(
        db_session,
        document_id=1,
        regulation_ids=["gdpr"],
        entity_type_counts={"PERSON_NAME": 2},
        total_count=2,
    )
    await log_deanonymization(
        db_session,
        document_id=1,
        entity_count=2,
        strategy="simple",
    )
    await log_document_event(
        db_session,
        document_id=1,
        event_type="document_deleted",
    )

    result = await db_session.execute(select(AuditEvent))
    events = result.scalars().all()

    assert len(events) == 3
    event_types = {e.event_type for e in events}
    assert event_types == {"pii_detected", "deanonymization_performed", "document_deleted"}


@pytest.mark.asyncio
async def test_log_pii_detected_returns_persisted_event(db_session: AsyncSession) -> None:
    """log_pii_detected returns the persisted event so callers can link
    EntityDetection rows via ``audit_event_id`` for provenance."""
    event = await log_pii_detected(
        db_session,
        document_id=42,
        regulation_ids=["gdpr"],
        entity_type_counts={"EMAIL_ADDRESS": 1},
        total_count=1,
    )

    assert event is not None
    assert event.id is not None
    assert event.event_type == "pii_detected"
    assert event.document_id == 42


@pytest.mark.asyncio
async def test_entity_detection_audit_event_link(db_session: AsyncSession) -> None:
    """EntityDetection.audit_event_id FK stamps the triggering event."""
    document = Document(
        filename="x.pdf",
        original_filename="x.pdf",
        encrypted_path="/tmp/x",
        file_type="application/pdf",
        file_format="pdf",
        detected_language="en",
        file_size_bytes=1024,
    )
    db_session.add(document)
    await db_session.flush()
    chunk = Chunk(
        document_id=document.id,
        index=0,
        sanitized_text="[PERSON_1] lives in [LOCATION_1].",
        char_count=32,
    )
    db_session.add(chunk)
    await db_session.flush()

    event = await log_pii_detected(
        db_session,
        document_id=document.id,
        regulation_ids=["gdpr"],
        entity_type_counts={"PERSON_NAME": 1, "LOCATION": 1},
        total_count=2,
    )
    assert event is not None

    detections = [
        EntityDetection(
            document_id=document.id,
            chunk_id=chunk.id,
            entity_type="PERSON_NAME",
            placeholder="[PERSON_1]",
            start_offset=0,
            end_offset=10,
            score=0.95,
            audit_event_id=event.id,
        ),
        EntityDetection(
            document_id=document.id,
            chunk_id=chunk.id,
            entity_type="LOCATION",
            placeholder="[LOCATION_1]",
            start_offset=20,
            end_offset=32,
            score=0.90,
            audit_event_id=event.id,
        ),
    ]
    db_session.add_all(detections)
    await db_session.commit()

    result = await db_session.execute(
        select(EntityDetection).where(EntityDetection.audit_event_id == event.id)
    )
    linked = result.scalars().all()
    assert len(linked) == 2
    assert {d.entity_type for d in linked} == {"PERSON_NAME", "LOCATION"}
