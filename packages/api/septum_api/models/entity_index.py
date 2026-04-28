from __future__ import annotations

"""Entity index — cross-document lookup of which documents contain a given entity.

Populated at ingestion time from the document's anonymization map. The
``value_hash`` column is a deterministic HMAC-SHA256 of the original
entity text under the local encryption key, so a leak of this table
does not expose original PII (the key never leaves the air-gapped
zone). The index is used by chat-time entity-aware RAG routing and by
the per-document relationship graph.
"""

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from . import Base


class EntityIndex(Base):
    """Document↔entity index for cross-document chat routing."""

    __tablename__ = "entity_index"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    value_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    occurrences: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        # Token-level hashing legitimately produces the same value_hash
        # across different entity types within a single document — e.g.
        # the token "antalya" can appear in a LOCATION span AND inside
        # an ORGANIZATION_NAME like "ANTALYA SAĞLIK MERKEZİ". The
        # uniqueness contract therefore has to include the entity type
        # so two semantically distinct rows can coexist.
        UniqueConstraint(
            "document_id",
            "entity_type",
            "value_hash",
            name="uq_entity_index_doc_type_value",
        ),
        Index("ix_entity_index_value_type", "value_hash", "entity_type"),
    )
