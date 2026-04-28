from __future__ import annotations

"""Tests for the document-relationship visualization endpoint.

Confirms that the graph payload reflects the persisted entity index
and relationship cache rows, that the strength bucket maps
correctly to the configured thresholds, and that the per-edge
shared-entity-types map is hydrated from the JSON column.
"""

import json
from typing import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from septum_api.models import Base
from septum_api.models.audit_event import AuditEvent  # noqa: F401
from septum_api.models.document import Chunk, Document  # noqa: F401
from septum_api.models.document_relationship import DocumentRelationship
from septum_api.models.entity_detection import EntityDetection  # noqa: F401
from septum_api.models.entity_index import EntityIndex
from septum_api.models.user import User  # noqa: F401
from septum_api.routers.relationships import (
    _classify_strength,
    get_relationship_graph,
)
from septum_api.services.entity_index_service import (
    RELATIONSHIP_THRESHOLD_MEDIUM,
    RELATIONSHIP_THRESHOLD_STRONG,
)


class _FakeUser:
    id = 1


@pytest.fixture
async def db(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncSession]:
    monkeypatch.setattr(
        "septum_api.services.entity_index_service.get_encryption_key",
        lambda: b"\x00" * 32,
    )
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        yield session
    await engine.dispose()


async def _seed_doc(db: AsyncSession, doc_id: int, name: str, entity_count: int) -> None:
    db.add(
        Document(
            id=doc_id,
            filename=name,
            original_filename=name,
            file_type="application/pdf",
            file_format="pdf",
            detected_language="tr",
            encrypted_path=f"/tmp/{name}.enc",
            ingestion_status="completed",
            chunk_count=0,
            entity_count=entity_count,
            file_size_bytes=0,
            user_id=None,
        )
    )


def test_classify_strength_buckets() -> None:
    """Strength classifier must align with the configured thresholds."""
    assert _classify_strength(RELATIONSHIP_THRESHOLD_STRONG) == "strong"
    assert _classify_strength(RELATIONSHIP_THRESHOLD_STRONG + 0.5) == "strong"
    assert _classify_strength(RELATIONSHIP_THRESHOLD_MEDIUM) == "medium"
    assert _classify_strength(RELATIONSHIP_THRESHOLD_MEDIUM + 0.1) == "medium"
    assert _classify_strength(RELATIONSHIP_THRESHOLD_MEDIUM - 0.01) == "weak"
    assert _classify_strength(0.0) == "weak"


@pytest.mark.asyncio
async def test_graph_returns_nodes_and_edges_from_index(db: AsyncSession) -> None:
    """The endpoint must surface every visible doc node and every edge
    whose endpoints are also visible."""
    await _seed_doc(db, 1, "a.pdf", entity_count=5)
    await _seed_doc(db, 2, "b.pdf", entity_count=3)
    await _seed_doc(db, 3, "c.pdf", entity_count=7)
    db.add_all(
        [
            EntityIndex(document_id=1, entity_type="PERSON_NAME", value_hash="aaa", occurrences=1),
            EntityIndex(document_id=1, entity_type="IBAN", value_hash="bbb", occurrences=1),
            EntityIndex(document_id=2, entity_type="IBAN", value_hash="bbb", occurrences=1),
            EntityIndex(document_id=3, entity_type="LOCATION", value_hash="ccc", occurrences=1),
            DocumentRelationship(
                doc_a_id=1,
                doc_b_id=2,
                score=1.0,
                shared_entity_count=1,
                shared_entity_types=json.dumps({"IBAN": 1}, sort_keys=True),
            ),
        ]
    )
    await db.commit()

    graph = await get_relationship_graph(_user=_FakeUser(), db=db)

    assert {n.id for n in graph.nodes} == {1, 2, 3}
    a_node = next(n for n in graph.nodes if n.id == 1)
    assert a_node.distinct_entity_count == 2
    assert a_node.entity_count == 5

    assert len(graph.edges) == 1
    edge = graph.edges[0]
    assert (edge.source, edge.target) == (1, 2)
    assert edge.shared_entity_types == {"IBAN": 1}
    assert edge.strength == "strong"


@pytest.mark.asyncio
async def test_graph_filters_edges_to_visible_documents(db: AsyncSession) -> None:
    """Edges whose endpoints belong to documents the user cannot see
    must be omitted — defensive even though the row insert flow already
    enforces this."""
    await _seed_doc(db, 10, "owned.pdf", entity_count=1)
    # Stray relationship pointing at a non-existent document; should be
    # filtered out by the visibility guard.
    db.add(
        DocumentRelationship(
            doc_a_id=10,
            doc_b_id=999,
            score=0.7,
            shared_entity_count=1,
            shared_entity_types=json.dumps({"PERSON_NAME": 1}),
        )
    )
    await db.commit()

    graph = await get_relationship_graph(_user=_FakeUser(), db=db)
    assert {n.id for n in graph.nodes} == {10}
    assert graph.edges == []
