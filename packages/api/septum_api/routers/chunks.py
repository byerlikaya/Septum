from __future__ import annotations

"""FastAPI router for accessing sanitized document chunks."""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable, List, NoReturn, Optional, Sequence

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Select, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..errors import OriginalsPatternTooDeepError
from ..models.document import Chunk, Document
from ..models.entity_detection import EntityDetection
from ..models.user import User
from ..request_limits import (
    MAX_PRE_APPROVED_CHUNK_CHARS,
    MAX_SECTION_TITLE_CHARS,
    TOP_K_RETRIEVAL_MAX_CAP,
)
from ..services.bm25_retriever import BM25Retriever
from ..services.document_anon_store import (
    DocumentMapLockLost,
    document_map_lock,
    get_document_map,
)
from ..services.index_file_io import document_index_lock
from ..services.pii_gate import (
    DocumentNotEditableError,
    gate_document_texts,
    hold_completed_document,
    section_title_masker,
)
from ..services.vector_store import VectorStore
from ..utils.auth_dependency import get_current_user, require_role
from ..utils.db_helpers import get_or_404, get_owned_or_404, load_settings


async def _ensure_chunk_owner(db: AsyncSession, chunk: Chunk, user: User) -> None:
    """Reject the request if the caller does not own the chunk's document.

    Admin role bypasses; every other role must own the parent
    ``Document.user_id``. Returns 404 (not 403) so non-owners cannot
    enumerate which chunk ids exist.
    """
    if user.role == "admin":
        return
    from fastapi import HTTPException
    from fastapi import status as _status
    parent = await db.execute(
        select(Document.user_id).where(Document.id == chunk.document_id)
    )
    parent_user_id = parent.scalar_one_or_none()
    if parent_user_id != user.id:
        raise HTTPException(
            status_code=_status.HTTP_404_NOT_FOUND, detail=_CHUNK_NOT_FOUND
        )
from ..utils.text_utils import normalize_unicode

_CHUNK_NOT_FOUND = "Chunk not found."

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chunks", tags=["chunks"])


async def _completed_document(db: AsyncSession, chunk: Chunk) -> Document:
    """Return the chunk's document, or raise HTTP 409 unless its ingestion completed.

    A running ingestion still writes the document's chunks, detections and
    map, and a failed one has had them erased, so neither takes chunk edits.
    The caller holds the document on the run token read here
    (:func:`hold_completed_document`), so a reprocess in between is refused.
    """
    document = await get_or_404(db, Document, chunk.document_id, _CHUNK_NOT_FOUND)
    if document.ingestion_status != "completed":
        raise DocumentNotEditableError()
    return document


class ChunkResponse(BaseModel):
    """Serialized view of a sanitized chunk."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    document_id: int
    index: int
    sanitized_text: str
    raw_text: Optional[str] = None
    start_char: Optional[int] = None
    end_char: Optional[int] = None
    char_count: int
    source_page: Optional[int] = None
    source_slide: Optional[int] = None
    source_sheet: Optional[int] = None
    source_timestamp_start: Optional[float] = None
    source_timestamp_end: Optional[float] = None
    section_title: Optional[str] = None


async def _chunk_responses(chunks: Sequence[Chunk]) -> List[ChunkResponse]:
    """Serialize chunks with every section title masked by its document map.

    Rows ingested before titles were masked at ingestion still hold the raw
    heading, so a stored title is never returned as it is: it is masked like
    the chat path masks it, and dropped when the document has no map. A map
    stored with originals nested past the masking depth cap cannot be
    searched (:class:`OriginalsPatternTooDeepError`): that document's titles
    are dropped the same way, so one such map never fails the whole listing
    and no raw title is shown.
    """
    maskers = {}
    for document_id in {c.document_id for c in chunks if c.section_title}:
        maskers[document_id] = section_title_masker(await get_document_map(document_id))
    unsearchable: set[int] = set()
    responses: List[ChunkResponse] = []
    for chunk in chunks:
        response = ChunkResponse.model_validate(chunk)
        if chunk.section_title:
            response.section_title = None
            if chunk.document_id not in unsearchable:
                try:
                    response.section_title = maskers[chunk.document_id](
                        chunk.section_title, chunk.raw_text or ""
                    )
                except OriginalsPatternTooDeepError:
                    unsearchable.add(chunk.document_id)
        responses.append(response)
    return responses


class ChunkListResponse(BaseModel):
    """Wrapper for listing chunks."""

    items: List[ChunkResponse]


class ChunkSearchRequest(BaseModel):
    """Payload for searching chunks by semantic similarity."""

    document_id: int
    query: str
    top_k: int = Field(default=10, le=TOP_K_RETRIEVAL_MAX_CAP)


class ChunkSearchHit(BaseModel):
    """Single search hit with similarity score."""

    chunk: ChunkResponse
    score: float


class ChunkSearchResponse(BaseModel):
    """Wrapper for chunk search results."""

    items: List[ChunkSearchHit]


class ChunkUpdateRequest(BaseModel):
    """Payload for updating a chunk inline."""

    sanitized_text: Optional[str] = Field(default=None, max_length=MAX_PRE_APPROVED_CHUNK_CHARS)
    section_title: Optional[str] = Field(default=None, max_length=MAX_SECTION_TITLE_CHARS)


@router.get(
    "",
    status_code=status.HTTP_200_OK,
)
async def list_chunks(
    document_id: Optional[int] = Query(
        default=None,
        description="If provided, only chunks for this document are returned.",
    ),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ChunkListResponse:
    """Return sanitized chunks for documents the caller owns."""
    if document_id is not None:
        await get_owned_or_404(db, Document, document_id, user, "Document not found.")
        stmt = select(Chunk).where(Chunk.document_id == document_id)
    elif user.role == "admin":
        stmt = select(Chunk)
    else:
        stmt = (
            select(Chunk)
            .join(Document, Document.id == Chunk.document_id)
            .where(Document.user_id == user.id)
        )

    stmt = stmt.order_by(Chunk.document_id, Chunk.index)
    result = await db.execute(stmt)
    chunks = list(result.scalars().all())

    return ChunkListResponse(items=await _chunk_responses(chunks))


@router.get(
    "/{chunk_id}",
    status_code=status.HTTP_200_OK,
)
async def get_chunk(
    chunk_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ChunkResponse:
    """Return a single sanitized chunk by id."""
    chunk = await get_or_404(db, Chunk, chunk_id, _CHUNK_NOT_FOUND)
    await _ensure_chunk_owner(db, chunk, user)
    [response] = await _chunk_responses([chunk])
    return response


@router.patch(
    "/{chunk_id}",
    status_code=status.HTTP_200_OK,
)
async def update_chunk(
    chunk_id: int,
    payload: ChunkUpdateRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin", "editor")),
) -> ChunkResponse:
    """Update mutable fields of a chunk (inline edit support).

    Edited text and section title are user-typed, so both pass the PII gate
    before they are stored: neither ever holds raw PII (the title is sent
    to the approval modal with the chunk), and entities first seen here are
    added to the document's (encrypted) anonymization map. Only a chunk of
    a ``completed`` document may be edited; any other status, or one that
    changes before the edit commits, is refused with 409.

    Edited text clears the chunk's ``raw_text`` and deletes its stored
    entity detections in the same commit (the raw text and the detections'
    offsets into it no longer match the edit and must not linger), and once
    committed the chunk's entry in the document's FAISS and BM25 indexes is
    rebuilt from the edited sanitized text (:func:`_reindex_chunk`); if that
    fails the edit stays committed and the request answers 500. A
    title-only edit touches none of them.
    """
    chunk = await get_or_404(db, Chunk, chunk_id, _CHUNK_NOT_FOUND)
    await _ensure_chunk_owner(db, chunk, user)
    document = await _completed_document(db, chunk)
    run_token = document.ingestion_run_id

    edits = {
        name: normalize_unicode(value)
        for name, value in (
            ("sanitized_text", payload.sanitized_text),
            ("section_title", payload.section_title),
        )
        if value is not None
    }
    if edits:
        settings = await load_settings(db)
        gated = dict(
            zip(edits, await gate_document_texts(db, settings, document, list(edits.values())))
        )
        await hold_completed_document(db, document.id, run_token)
        if "sanitized_text" in gated:
            chunk.sanitized_text = gated["sanitized_text"]
            chunk.char_count = len(chunk.sanitized_text)
            chunk.raw_text = None
            await db.execute(
                delete(EntityDetection)
                .where(
                    EntityDetection.chunk_id == chunk_id,
                    EntityDetection.document_id == document.id,
                )
                .execution_options(synchronize_session=False)
            )
        if "section_title" in gated:
            chunk.section_title = gated["section_title"]

    await db.commit()
    await db.refresh(chunk)
    [response] = await _chunk_responses([chunk])
    if "sanitized_text" in edits:
        await _reindex_chunk(db, document.id, run_token, chunk_id)
    return response


@router.delete(
    "/{chunk_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_chunk(
    chunk_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin", "editor")),
) -> None:
    """Delete a chunk, recount its document's ``chunk_count`` and drop its id from the indexes.

    Only a chunk of a ``completed`` document may be deleted; any other
    status, or one that changes before the delete commits, is refused with
    409. The document row is held first, then the chunk deleted (the order
    a reprocess claim takes them in); a chunk a concurrent delete removed
    meanwhile yields 404, and ``chunk_count`` is set to the rows that remain.
    Once the delete is committed, the chunk's id is removed from the
    document's FAISS and BM25 indexes (:func:`_drop_from_indexes`), under
    the protocol of :func:`_update_indexes`; if that fails the row stays
    deleted and the request answers 500.
    """
    chunk = await get_or_404(db, Chunk, chunk_id, _CHUNK_NOT_FOUND)
    await _ensure_chunk_owner(db, chunk, user)
    document = await _completed_document(db, chunk)
    document_id = document.id
    run_token = document.ingestion_run_id

    await hold_completed_document(db, document_id, run_token)
    deleted = await db.scalar(
        delete(Chunk)
        .where(Chunk.id == chunk_id, Chunk.document_id == document_id)
        .returning(Chunk.id)
        .execution_options(synchronize_session=False)
    )
    if deleted is None:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_CHUNK_NOT_FOUND)
    remaining = (
        select(func.count()).select_from(Chunk).where(Chunk.document_id == document_id)
    ).scalar_subquery()
    await db.execute(
        update(Document)
        .where(Document.id == document_id)
        .values(chunk_count=remaining)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    await _drop_from_indexes(db, document_id, run_token, [chunk_id])


@dataclass(frozen=True)
class _IndexChange:
    """What an index update does, as named in its failure log line and 500 answer."""

    action: str
    detail: str


_CHUNK_DELETED = _IndexChange(
    "Removing deleted chunk ids from",
    "The chunk was deleted, but the document's search index could not "
    "be updated. Reprocess the document to rebuild it.",
)
_CHUNK_EDITED = _IndexChange(
    "Re-indexing the edited chunk in",
    "The chunk was edited, but the document's search index could not "
    "be updated. Reprocess the document to rebuild it.",
)


async def _drop_from_indexes(
    db: AsyncSession, document_id: int, run_token: Optional[str], chunk_ids: List[int]
) -> None:
    """Remove committed-deleted ``chunk_ids`` from the document's FAISS and BM25 indexes."""
    await _update_indexes(
        db,
        document_id,
        run_token,
        _CHUNK_DELETED,
        select(Document.id),
        lambda store, _: store.remove_ids(document_id, chunk_ids),
    )


async def _reindex_chunk(
    db: AsyncSession, document_id: int, run_token: Optional[str], chunk_id: int
) -> None:
    """Rebuild an edited chunk's entry in the document's FAISS and BM25 indexes.

    The text indexed is the chunk's ``sanitized_text`` as re-read under the
    locks, not the text this request committed: of two edits of one chunk,
    whichever re-indexes last reads the edit committed last, so an earlier
    edit never puts its stale text back. A chunk deleted meanwhile is left
    to its delete. The embedding model is loaded before the locks are
    taken, so a cold load cannot spend the map lock's lifetime.
    """
    await _update_indexes(
        db,
        document_id,
        run_token,
        _CHUNK_EDITED,
        select(Chunk.sanitized_text)
        .join(Document, Document.id == Chunk.document_id)
        .where(Chunk.id == chunk_id),
        lambda store, text: store.replace_entry(document_id, chunk_id, text),
        prepare=lambda: VectorStore().warm_model(document_id),
    )


async def _update_indexes(
    db: AsyncSession,
    document_id: int,
    run_token: Optional[str],
    change: _IndexChange,
    reread: Select,
    write: Callable[[Any, Any], None],
    prepare: Optional[Callable[[], None]] = None,
) -> None:
    """Apply a committed chunk change to the document's FAISS and BM25 indexes.

    Lock order: :func:`document_index_lock`, then :func:`document_map_lock`,
    the order the chat-time BM25 rebuild takes them in; the session's
    transaction (a read the caller made after its commit, if any) is ended
    first, so none is open while either is awaited, and ``prepare``, if
    given, then runs in a worker thread before either lock is taken. The
    map lock spans workers when Redis is configured, so two changes on the
    same document never lose each other's load-modify-write, and a reprocess's artifact cleanup (which erases
    under the map lock) cannot interleave. Under both locks ``reread`` runs,
    narrowed to the document while it is still ``completed`` on
    ``run_token``, as a plain SELECT whose transaction is ended at once: no
    row means a reprocess claimed the document (its cleanup replaces both
    files) or the row is gone, so nothing is written. Otherwise
    ``write(store, value)`` runs once per index, ``value`` being the
    re-read column.

    A failure is logged with the document id, the tier (``model``,
    ``lock``, ``reread``, ``faiss`` or ``bm25``) and the error class only,
    and answered with ``change``'s content-free 500; the row change stays
    committed. A map lock found expired only on release, after both
    writes, is logged as a warning.
    """
    tier = "reread"
    try:
        await db.rollback()
        if prepare is not None:
            tier = "model"
            await asyncio.to_thread(prepare)
        tier = "lock"
        async with document_index_lock(document_id):
            async with document_map_lock(document_id) as lock:
                tier = "reread"
                held = (
                    await db.execute(
                        reread.where(
                            Document.id == document_id,
                            Document.ingestion_status == "completed",
                            Document.ingestion_run_id.is_not_distinct_from(run_token),
                        )
                    )
                ).first()
                await db.rollback()
                if held is None:
                    return
                for tier, store in (("faiss", VectorStore), ("bm25", BM25Retriever)):
                    await lock.ensure_held()
                    await asyncio.to_thread(write, store(), held[0])
                tier = "release"
    except DocumentMapLockLost:
        if tier != "release":
            _raise_index_update_failed(document_id, change, tier, DocumentMapLockLost)
        logger.warning(
            "Map lock of document_id=%s expired after its index update", document_id
        )
    except Exception as exc:
        _raise_index_update_failed(document_id, change, tier, type(exc))


def _raise_index_update_failed(
    document_id: int, change: _IndexChange, tier: str, error: type
) -> NoReturn:
    """Log a failed index update and raise ``change``'s content-free 500."""
    logger.error(
        "%s the %s index of document_id=%s failed: %s",
        change.action,
        tier,
        document_id,
        error.__name__,
    )
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=change.detail
    ) from None


@router.post(
    "/search",
    status_code=status.HTTP_200_OK,
)
async def search_chunks(
    payload: ChunkSearchRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ChunkSearchResponse:
    """Search chunks for a given document using the vector index."""
    await get_owned_or_404(db, Document, payload.document_id, user, "Document not found.")

    effective_top_k = payload.top_k if payload.top_k > 0 else 10

    vector_store = VectorStore()
    search_results = await asyncio.to_thread(
        vector_store.search,
        document_id=payload.document_id,
        query=payload.query,
        top_k=effective_top_k,
    )

    if not search_results:
        return ChunkSearchResponse(items=[])

    chunk_ids = [chunk_id for chunk_id, _ in search_results]
    stmt = select(Chunk).where(
        Chunk.id.in_(chunk_ids), Chunk.document_id == payload.document_id
    )
    db_result = await db.execute(stmt)
    chunks = list(db_result.scalars().all())

    chunk_by_id = {chunk.id: chunk for chunk in chunks}

    hits = [
        (chunk_by_id[chunk_id], score)
        for chunk_id, score in search_results
        if chunk_id in chunk_by_id
    ]
    responses = await _chunk_responses([chunk for chunk, _ in hits])
    items = [
        ChunkSearchHit(chunk=response, score=float(score))
        for response, (_, score) in zip(responses, hits)
    ]

    return ChunkSearchResponse(items=items)


