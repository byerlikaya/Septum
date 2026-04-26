from __future__ import annotations

"""Tests for the document-cluster service.

The service drives the disambiguation picker — when a query matches
documents that fall into 2+ disconnected components on the
relationship graph, the chat router asks the user which cluster they
meant before retrieval. The tests verify the connected-components
algorithm against representative shapes and the score-threshold cut.
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
from septum_api.models.entity_index import EntityIndex  # noqa: F401
from septum_api.models.user import User  # noqa: F401
from septum_api.services.document_cluster_service import (
    cluster_documents_by_relationship,
)


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
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


def _seed_relationship(
    db: AsyncSession,
    doc_a: int,
    doc_b: int,
    score: float,
    types: dict[str, int] | None = None,
) -> None:
    db.add(
        DocumentRelationship(
            doc_a_id=min(doc_a, doc_b),
            doc_b_id=max(doc_a, doc_b),
            score=score,
            shared_entity_count=sum((types or {}).values()) or 1,
            shared_entity_types=json.dumps(types or {}),
        )
    )


@pytest.mark.asyncio
async def test_single_document_returns_single_cluster(db: AsyncSession) -> None:
    """An isolated document forms a single-element cluster on its own."""
    clusters = await cluster_documents_by_relationship(db, [42])
    assert clusters == [[42]]


@pytest.mark.asyncio
async def test_strongly_connected_docs_collapse_into_one_cluster(
    db: AsyncSession,
) -> None:
    """Documents linked by an above-threshold edge belong to one cluster."""
    _seed_relationship(db, 1, 2, score=1.5, types={"IBAN": 1})
    await db.commit()
    clusters = await cluster_documents_by_relationship(db, [1, 2])
    assert clusters == [[1, 2]]


@pytest.mark.asyncio
async def test_disconnected_matches_form_separate_clusters(
    db: AsyncSession,
) -> None:
    """Reproduces the disambiguation trigger: two disjoint document sets
    matched the same query (e.g. two different "Mehmet"s) and there is
    no above-threshold edge between them, so each forms its own cluster."""
    _seed_relationship(db, 1, 2, score=2.0, types={"NATIONAL_ID": 1})
    _seed_relationship(db, 3, 4, score=1.2, types={"PERSON_NAME": 1, "EMAIL_ADDRESS": 1})
    await db.commit()
    clusters = await cluster_documents_by_relationship(db, [1, 2, 3, 4])
    assert clusters == [[1, 2], [3, 4]]


@pytest.mark.asyncio
async def test_below_threshold_edge_does_not_merge_clusters(
    db: AsyncSession,
) -> None:
    """An edge with a weak score (only common LOCATION) must NOT merge
    two clusters. Otherwise sharing a city name would collapse every
    document into one big "everything is one cluster" group."""
    _seed_relationship(db, 1, 2, score=0.10, types={"LOCATION": 1})  # weak
    await db.commit()
    clusters = await cluster_documents_by_relationship(db, [1, 2])
    assert clusters == [[1], [2]]


@pytest.mark.asyncio
async def test_transitive_links_collapse_chain(db: AsyncSession) -> None:
    """A→B and B→C with above-threshold scores must collapse the whole
    chain into a single component."""
    _seed_relationship(db, 1, 2, score=1.0, types={"PERSON_NAME": 1, "IBAN": 1})
    _seed_relationship(db, 2, 3, score=1.5, types={"NATIONAL_ID": 1})
    await db.commit()
    clusters = await cluster_documents_by_relationship(db, [1, 2, 3])
    assert clusters == [[1, 2, 3]]
