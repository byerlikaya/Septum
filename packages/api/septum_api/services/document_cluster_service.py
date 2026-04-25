from __future__ import annotations

"""Group entity-matching documents into ambiguity clusters.

When the user's question matches several documents through the entity
index, we still want to know whether those matches really refer to the
same real-world entity (Ahmet Çelik's contract + Ahmet Çelik's KVKK
form belong together) or to genuinely different entities that happen
to share a low-uniqueness signal (a common first name across two
unrelated cases). Connected components on the document-relationship
graph answer that question: documents linked by at least a
medium-strength relationship collapse into one cluster, isolated
matches each form their own cluster, and a query that produces more
than one cluster is the trigger for the disambiguation UX.
"""

from collections import defaultdict
from typing import Iterable, List, Sequence, Set, Tuple

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.document_relationship import DocumentRelationship
from .entity_index_service import RELATIONSHIP_THRESHOLD_MEDIUM


def _connected_components(
    nodes: Iterable[int],
    edges: Iterable[Tuple[int, int]],
) -> List[Set[int]]:
    """Return the connected components of the undirected graph (nodes, edges)."""
    node_set = set(nodes)
    adjacency: defaultdict[int, set[int]] = defaultdict(set)
    for a, b in edges:
        if a in node_set and b in node_set and a != b:
            adjacency[a].add(b)
            adjacency[b].add(a)

    visited: set[int] = set()
    clusters: list[set[int]] = []
    for start in node_set:
        if start in visited:
            continue
        component: set[int] = set()
        stack = [start]
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            component.add(current)
            for neighbour in adjacency[current]:
                if neighbour not in visited:
                    stack.append(neighbour)
        clusters.append(component)
    return clusters


async def cluster_documents_by_relationship(
    db: AsyncSession,
    document_ids: Sequence[int],
    *,
    score_threshold: float = RELATIONSHIP_THRESHOLD_MEDIUM,
) -> List[List[int]]:
    """Group ``document_ids`` into clusters of documents linked by an
    entity-overlap score >= ``score_threshold``.

    Each returned cluster is a list of document IDs sorted ascending so
    callers can compare clusters by canonical form. Clusters themselves
    are sorted by smallest contained id so the output order is stable
    across runs.
    """
    if not document_ids:
        return []
    unique_ids = list({int(d) for d in document_ids})
    if len(unique_ids) == 1:
        return [unique_ids]

    rels_q = await db.execute(
        select(
            DocumentRelationship.doc_a_id,
            DocumentRelationship.doc_b_id,
            DocumentRelationship.score,
        ).where(
            DocumentRelationship.score >= score_threshold,
            or_(
                DocumentRelationship.doc_a_id.in_(unique_ids),
                DocumentRelationship.doc_b_id.in_(unique_ids),
            ),
        )
    )
    edges = [(a, b) for a, b, _ in rels_q.all()]
    components = _connected_components(unique_ids, edges)
    clusters = [sorted(c) for c in components]
    clusters.sort(key=lambda c: c[0])
    return clusters
