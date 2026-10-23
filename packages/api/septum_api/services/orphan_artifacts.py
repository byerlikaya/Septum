"""Startup sweep for stored document artifacts whose document row is gone.

Deletions that predate full erasure (user deletion used to drop only the
rows) left encrypted source files, search indexes and anonymization maps
behind. The sweep finds them by name and erases them; it logs counts,
document ids, tiers and error classes only, never a path or content.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..models.document import Document
from .bm25_retriever import BM25Retriever
from .document_anon_store import (
    DocumentMapErasureError,
    DocumentMapLockLost,
    DocumentMapLockTimeout,
    disk_map_document_ids,
    document_map_lock,
    redis_map_document_ids,
)
from .document_pipeline import (
    ArtifactErasureError,
    erase_document_artifacts,
    log_artifact_erasure_failure,
)
from .source_files import is_source_file_name
from .vector_store import VectorStore

try:
    from redis.exceptions import RedisError
except ImportError:  # Redis is an optional extra.

    class RedisError(Exception):  # type: ignore[no-redef]
        """Stand-in that is never raised when the redis package is absent."""


logger = logging.getLogger(__name__)

# Upload writes the encrypted source before its row is committed; a file
# younger than this may belong to an upload still in flight in another worker.
SOURCE_MIN_AGE_SECONDS = 300
# Startup waits on each orphan in turn, so a stuck lock must not hold the
# API back for the full request-path wait; a timeout is retried next startup.
SWEEP_LOCK_WAIT_SECONDS = 3


@dataclass
class OrphanSweepResult:
    """Counts of what the sweep erased, held back and failed on."""

    documents: int = 0
    source_files: int = 0
    failures: int = 0
    held_back: int = 0


async def sweep_orphan_artifacts(
    session_maker: async_sessionmaker[AsyncSession], source_dir: Path
) -> OrphanSweepResult:
    """Erase every stored artifact that no document row references.

    Id-keyed artifacts (indexes, maps) are erased with
    :func:`erase_document_artifacts` under the document's map lock, after
    the row is confirmed absent inside the lock: the pipeline writes them
    only after its row exists and stores the map under that lock first, so
    a document being ingested is never touched. Source files are matched by
    name against every row and only removed once older than
    :data:`SOURCE_MIN_AGE_SECONDS`; storage is listed before the rows are
    read, so a row committed before the listing always counts.

    With no document row at all but stored artifacts present, nothing is
    erased and an error is logged: that is what pointing the API at another
    or a restored database looks like. A failure is logged and counted, and
    the sweep goes on; the next startup retries it.
    """
    result = OrphanSweepResult()

    candidates: set[int] = set()
    disk_listings: list[tuple[str, Callable[[], set[int]]]] = [
        ("vector_index", lambda: VectorStore().stored_document_ids()),
        ("bm25_index", lambda: BM25Retriever().stored_document_ids()),
        ("anonymization_map", disk_map_document_ids),
    ]
    for tier, listing in disk_listings:
        try:
            candidates |= listing()
        except OSError as exc:
            _listing_failed(result, tier, exc)
    try:
        candidates |= await redis_map_document_ids()
    except (DocumentMapErasureError, RedisError) as exc:
        _listing_failed(result, "anonymization_map_cache", exc)
    sources = _list_source_files(source_dir, result)

    async with session_maker() as db:
        live: set[int] = set()
        referenced: set[str] = set()
        rows = await db.execute(select(Document.id, Document.filename, Document.encrypted_path))
        for document_id, filename, encrypted_path in rows:
            live.add(document_id)
            referenced.add(filename)
            referenced.add(Path(encrypted_path).name)
        if not live and (candidates or sources):
            result.held_back = len(candidates) + len(sources)
            logger.error(
                "The documents table is empty but %d stored document ids and %d source "
                "files exist; erased nothing. Check the database the API is using.",
                len(candidates),
                len(sources),
            )
            return result

        for document_id in sorted(candidates - live):
            outcome = await _erase_orphan(db, document_id)
            if outcome == "erased":
                result.documents += 1
            elif outcome == "failed":
                result.failures += 1

    for entry in sources:
        if entry.name not in referenced:
            _erase_source_file(entry, result)

    if result.documents or result.source_files:
        logger.info(
            "Erased stored artifacts of %d deleted documents and %d orphaned source files",
            result.documents,
            result.source_files,
        )
    if result.failures:
        logger.error(
            "Orphaned artifact sweep left %d failures; the next startup retries them",
            result.failures,
        )
    return result


def _listing_failed(result: OrphanSweepResult, tier: str, exc: BaseException) -> None:
    result.failures += 1
    logger.error("Could not list stored artifacts tier=%s error=%s", tier, type(exc).__name__)


async def _erase_orphan(db: AsyncSession, document_id: int) -> Literal["erased", "kept", "failed"]:
    """Erase the artifacts of ``document_id`` unless its row appeared meanwhile ("kept")."""
    try:
        async with document_map_lock(document_id, wait_seconds=SWEEP_LOCK_WAIT_SECONDS) as lock:
            exists = await db.scalar(select(Document.id).where(Document.id == document_id))
            if exists is not None:
                return "kept"
            await lock.ensure_held()
            await erase_document_artifacts(document_id)
        return "erased"
    except ArtifactErasureError as exc:
        log_artifact_erasure_failure(document_id, exc)
    except (DocumentMapLockTimeout, DocumentMapLockLost, RedisError) as exc:
        logger.error(
            "Could not erase document_id=%s tier=map_lock error=%s",
            document_id,
            type(exc).__name__,
        )
    return "failed"


def _list_source_files(source_dir: Path, result: OrphanSweepResult) -> list[Path]:
    """Return the upload-named source files older than :data:`SOURCE_MIN_AGE_SECONDS`."""
    cutoff = time.time() - SOURCE_MIN_AGE_SECONDS
    try:
        sources = []
        for entry in source_dir.iterdir():
            if not is_source_file_name(entry.name):
                continue
            try:
                if entry.is_file() and entry.stat().st_mtime < cutoff:
                    sources.append(entry)
            except FileNotFoundError:
                continue  # Vanished since the listing: nothing left to erase.
        return sources
    except FileNotFoundError:
        return []
    except OSError as exc:
        _listing_failed(result, "source_file", exc)
        return []


def _erase_source_file(entry: Path, result: OrphanSweepResult) -> None:
    """Remove one orphaned source file, counting the outcome in ``result``."""
    try:
        entry.unlink(missing_ok=True)
        result.source_files += 1
    except OSError as exc:
        result.failures += 1
        logger.error(
            "Could not erase an orphaned source file error=%s errno=%s",
            type(exc).__name__,
            getattr(exc, "errno", None),
        )
