"""Rebuild entity-index rows written under an older hashing scheme.

Index rows are HMAC hashes of normalized values and tokens, so a change to
the normalization or the tokenizer (``ENTITY_INDEX_VERSION``) leaves the
old rows unable to match new ones. Each document records the version its
rows were written under; this module rebuilds every completed document
whose version is missing or older, from its stored anonymization map.

Documents are rebuilt one at a time and committed one at a time. Each
rebuild also recomputes that document's relationship rows against every
other document's current rows, so once the last stale document is done
every pair has been scored on the new scheme. A document whose map cannot
be loaded is left as it is and retried at the next startup.
"""

from __future__ import annotations

import logging

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..models.document import Document
from .document_anon_store import get_document_map
from .entity_index_service import (
    ENTITY_INDEX_VERSION,
    recompute_relationships_for_document,
    replace_index_for_document,
)

logger = logging.getLogger(__name__)


async def _stale_document_ids(db: AsyncSession) -> list[int]:
    """Completed documents whose index is missing a version or carries an older one."""
    rows = await db.execute(
        select(Document.id)
        .where(
            Document.ingestion_status == "completed",
            or_(
                Document.entity_index_version.is_(None),
                Document.entity_index_version < ENTITY_INDEX_VERSION,
            ),
        )
        .order_by(Document.id)
    )
    return list(rows.scalars().all())


async def _reindex_one(db: AsyncSession, document_id: int) -> bool:
    """Rebuild one document's index and relationships; False when its map is unavailable."""
    anon_map = await get_document_map(document_id)
    if anon_map is None:
        return False
    await replace_index_for_document(db, document_id, anon_map)
    await recompute_relationships_for_document(db, document_id)
    await db.execute(
        update(Document)
        .where(Document.id == document_id)
        .values(entity_index_version=ENTITY_INDEX_VERSION)
    )
    await db.commit()
    return True


async def reindex_stale_documents(session_maker: async_sessionmaker[AsyncSession]) -> int:
    """Rebuild every stale document's index rows; returns how many were rebuilt.

    A failure on one document is logged by error class and rolled back; the
    others still run.
    """
    async with session_maker() as db:
        document_ids = await _stale_document_ids(db)
    rebuilt = 0
    for document_id in document_ids:
        async with session_maker() as db:
            try:
                if await _reindex_one(db, document_id):
                    rebuilt += 1
            except Exception as exc:
                await db.rollback()
                logger.error(
                    "Entity index rebuild failed for document %s: %s",
                    document_id,
                    type(exc).__name__,
                )
    if document_ids:
        logger.info(
            "Entity index rebuilt for %d of %d stale documents", rebuilt, len(document_ids)
        )
    return rebuilt
