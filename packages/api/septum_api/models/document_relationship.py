from __future__ import annotations

"""Document relationship cache — pre-computed entity-overlap scores
between every pair of documents in the corpus.

Each row represents a single ordered pair ``(doc_a_id < doc_b_id)`` so
queries do not have to consider both directions. The score is the
weighted sum of shared entities between the two documents using the
``ENTITY_UNIQUENESS_WEIGHTS`` table — a shared NATIONAL_ID is worth far
more than a shared LOCATION because location names commonly appear
across unrelated documents while a national ID effectively binds two
documents to the same person.
"""

from sqlalchemy import Float, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from . import Base


class DocumentRelationship(Base):
    """Pairwise document relationship strength based on shared entities."""

    __tablename__ = "document_relationships"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doc_a_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    doc_b_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    score: Mapped[float] = mapped_column(Float, nullable=False)
    shared_entity_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # JSON object: {entity_type: count_of_shared_entities_of_that_type}
    # Stored as text so the visualization endpoint can hydrate it without
    # an extra join, and so SIEM exports can ship the relationship cache
    # alongside the documents themselves.
    shared_entity_types: Mapped[str] = mapped_column(String, nullable=False, default="{}")

    __table_args__ = (
        UniqueConstraint("doc_a_id", "doc_b_id", name="uq_doc_relationship_pair"),
        Index("ix_doc_relationship_score", "score"),
        Index("ix_doc_relationship_doc_a", "doc_a_id"),
        Index("ix_doc_relationship_doc_b", "doc_b_id"),
    )
