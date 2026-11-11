from __future__ import annotations

"""FastAPI router for accessing sanitized document chunks."""

import asyncio
from typing import List, Optional, Sequence

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.document import Chunk, Document
from ..models.user import User
from ..request_limits import MAX_PRE_APPROVED_CHUNK_CHARS, MAX_SECTION_TITLE_CHARS
from ..services.document_anon_store import get_document_map
from ..services.pii_gate import gate_document_texts, section_title_masker
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

router = APIRouter(prefix="/api/chunks", tags=["chunks"])


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
    the chat path masks it, and dropped when the document has no map.
    """
    maskers = {}
    for document_id in {c.document_id for c in chunks if c.section_title}:
        maskers[document_id] = section_title_masker(await get_document_map(document_id))
    responses: List[ChunkResponse] = []
    for chunk in chunks:
        response = ChunkResponse.model_validate(chunk)
        if chunk.section_title:
            response.section_title = maskers[chunk.document_id](
                chunk.section_title, chunk.raw_text or ""
            )
        responses.append(response)
    return responses


class ChunkListResponse(BaseModel):
    """Wrapper for listing chunks."""

    items: List[ChunkResponse]


class ChunkSearchRequest(BaseModel):
    """Payload for searching chunks by semantic similarity."""

    document_id: int
    query: str
    top_k: int = 10


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
    added to the document's (encrypted) anonymization map.
    """
    chunk = await get_or_404(db, Chunk, chunk_id, _CHUNK_NOT_FOUND)
    await _ensure_chunk_owner(db, chunk, user)

    edits = {
        name: normalize_unicode(value)
        for name, value in (
            ("sanitized_text", payload.sanitized_text),
            ("section_title", payload.section_title),
        )
        if value is not None
    }
    if edits:
        document = await get_or_404(db, Document, chunk.document_id, _CHUNK_NOT_FOUND)
        settings = await load_settings(db)
        gated = dict(
            zip(edits, await gate_document_texts(db, settings, document, list(edits.values())))
        )
        if "sanitized_text" in gated:
            chunk.sanitized_text = gated["sanitized_text"]
            chunk.char_count = len(chunk.sanitized_text)
        if "section_title" in gated:
            chunk.section_title = gated["section_title"]

    await db.commit()
    await db.refresh(chunk)
    [response] = await _chunk_responses([chunk])
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
    """Delete a chunk."""
    chunk = await get_or_404(db, Chunk, chunk_id, _CHUNK_NOT_FOUND)
    await _ensure_chunk_owner(db, chunk, user)

    await db.delete(chunk)
    await db.commit()


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
    stmt = select(Chunk).where(Chunk.id.in_(chunk_ids))
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


