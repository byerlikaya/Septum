from __future__ import annotations

"""Document-relationship visualization endpoint.

Powers the ``/relationships`` page in the dashboard. Returns the
document graph derived from :class:`EntityIndex` and
:class:`DocumentRelationship` so the front-end can render a
force-directed view with edge thickness proportional to entity-overlap
score and per-edge breakdown of which entity types are shared. The
underlying tables only contain HMAC-keyed value hashes — no original
PII is ever serialised through this endpoint.
"""

import json
from typing import Dict, List

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.document import Document
from ..models.document_relationship import DocumentRelationship
from ..models.entity_index import EntityIndex
from ..models.user import User
from ..services.entity_index_service import (
    RELATIONSHIP_THRESHOLD_MEDIUM,
    RELATIONSHIP_THRESHOLD_STRONG,
)
from ..utils.auth_dependency import get_current_user

router = APIRouter(prefix="/api/relationships", tags=["relationships"])


class RelationshipDocumentNode(BaseModel):
    """Per-document node payload for the relationship graph."""

    id: int
    filename: str
    entity_count: int
    distinct_entity_count: int


class RelationshipEdge(BaseModel):
    """Pairwise edge with score, breakdown, and a categorical strength tag."""

    source: int
    target: int
    score: float
    shared_entity_count: int
    shared_entity_types: Dict[str, int]
    strength: str  # "strong" | "medium" | "weak"


class RelationshipGraph(BaseModel):
    """Top-level payload for the relationship visualization."""

    nodes: List[RelationshipDocumentNode]
    edges: List[RelationshipEdge]


def _classify_strength(score: float) -> str:
    if score >= RELATIONSHIP_THRESHOLD_STRONG:
        return "strong"
    if score >= RELATIONSHIP_THRESHOLD_MEDIUM:
        return "medium"
    return "weak"


@router.get(
    "/graph",
    response_model=RelationshipGraph,
    status_code=status.HTTP_200_OK,
)
async def get_relationship_graph(
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RelationshipGraph:
    """Return the full document-relationship graph the user can see.

    Only documents owned by the requesting user (or unowned legacy
    documents) are included. Each node carries a distinct entity count
    so the visualization can size nodes proportionally; each edge
    carries the weighted score, the per-type shared entity breakdown,
    and a strength bucket the front-end uses for colouring.
    """
    docs_q = await db.execute(
        select(Document.id, Document.original_filename, Document.entity_count).where(
            or_(
                Document.user_id == _user.id,
                Document.user_id.is_(None),
            ),
            Document.ingestion_status == "completed",
        )
    )
    doc_rows = docs_q.all()
    visible_doc_ids = {doc_id for doc_id, _, _ in doc_rows}

    # Per-document distinct entity count (independent of ``entity_count``
    # which counts every detection occurrence). The index dedupes by
    # (entity_type, value_hash) per document so this gives the unique
    # entity surface area — a useful sizing signal for the graph node.
    counts_q = await db.execute(
        select(EntityIndex.document_id, func.count(EntityIndex.id))
        .where(EntityIndex.document_id.in_(visible_doc_ids) if visible_doc_ids else False)
        .group_by(EntityIndex.document_id)
    )
    distinct_counts: Dict[int, int] = {
        doc_id: int(count) for doc_id, count in counts_q.all()
    }

    nodes = [
        RelationshipDocumentNode(
            id=doc_id,
            filename=filename or f"document_{doc_id}",
            entity_count=int(entity_count or 0),
            distinct_entity_count=distinct_counts.get(doc_id, 0),
        )
        for doc_id, filename, entity_count in doc_rows
    ]

    if not visible_doc_ids:
        return RelationshipGraph(nodes=nodes, edges=[])

    rels_q = await db.execute(
        select(
            DocumentRelationship.doc_a_id,
            DocumentRelationship.doc_b_id,
            DocumentRelationship.score,
            DocumentRelationship.shared_entity_count,
            DocumentRelationship.shared_entity_types,
        ).where(
            DocumentRelationship.doc_a_id.in_(visible_doc_ids),
            DocumentRelationship.doc_b_id.in_(visible_doc_ids),
        )
    )

    edges: List[RelationshipEdge] = []
    for doc_a_id, doc_b_id, score, shared_count, shared_types_json in rels_q.all():
        try:
            shared_types = json.loads(shared_types_json) if shared_types_json else {}
        except json.JSONDecodeError:
            shared_types = {}
        edges.append(
            RelationshipEdge(
                source=doc_a_id,
                target=doc_b_id,
                score=float(score),
                shared_entity_count=int(shared_count),
                shared_entity_types={str(k): int(v) for k, v in shared_types.items()},
                strength=_classify_strength(float(score)),
            )
        )

    return RelationshipGraph(nodes=nodes, edges=edges)
