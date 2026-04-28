from __future__ import annotations

"""FastAPI router for accessing sanitized document chunks."""

import asyncio
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.document import Chunk, Document
from ..models.user import User
from ..services.vector_store import VectorStore
from ..utils.auth_dependency import get_current_user, require_role
from ..utils.db_helpers import get_or_404
from ..utils.text_utils import normalize_unicode

router = APIRouter(prefix="/api/chunks", tags=["chunks"])


class ChunkResponse(BaseModel):
    """Serialized view of a sanitized chunk."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    document_id: int
    index: int
    sanitized_text: str
    raw_text: Optional[str] = None
    char_count: int
    source_page: Optional[int]
    source_slide: Optional[int]
    source_sheet: Optional[int]
    source_timestamp_start: Optional[float]
    source_timestamp_end: Optional[float]
    section_title: Optional[str]


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

    sanitized_text: Optional[str] = None
    section_title: Optional[str] = None


@router.get(
    "",
    response_model=ChunkListResponse,
    status_code=status.HTTP_200_OK,
)
async def list_chunks(
    document_id: Optional[int] = Query(
        default=None,
        description="If provided, only chunks for this document are returned.",
    ),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> ChunkListResponse:
    """Return sanitized chunks, optionally filtered by document."""
    stmt = select(Chunk)
    if document_id is not None:
        await get_or_404(db, Document, document_id, "Document not found.")
        stmt = stmt.where(Chunk.document_id == document_id)

    stmt = stmt.order_by(Chunk.document_id, Chunk.index)
    result = await db.execute(stmt)
    chunks = list(result.scalars().all())

    return ChunkListResponse(
        items=[ChunkResponse.model_validate(c) for c in chunks]
    )


@router.get(
    "/{chunk_id}",
    response_model=ChunkResponse,
    status_code=status.HTTP_200_OK,
)
async def get_chunk(
    chunk_id: int,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> ChunkResponse:
    """Return a single sanitized chunk by id."""
    chunk = await get_or_404(db, Chunk, chunk_id, "Chunk not found.")
    return ChunkResponse.model_validate(chunk)


@router.patch(
    "/{chunk_id}",
    response_model=ChunkResponse,
    status_code=status.HTTP_200_OK,
)
async def update_chunk(
    chunk_id: int,
    payload: ChunkUpdateRequest,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_role("admin", "editor")),
) -> ChunkResponse:
    """Update mutable fields of a chunk (inline edit support)."""
    chunk = await get_or_404(db, Chunk, chunk_id, "Chunk not found.")

    if payload.sanitized_text is not None:
        normalized = normalize_unicode(payload.sanitized_text)
        chunk.sanitized_text = normalized
        chunk.char_count = len(normalized)

    if payload.section_title is not None:
        chunk.section_title = payload.section_title

    await db.commit()
    await db.refresh(chunk)
    return ChunkResponse.model_validate(chunk)


@router.delete(
    "/{chunk_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def delete_chunk(
    chunk_id: int,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_role("admin", "editor")),
) -> None:
    """Delete a chunk."""
    chunk = await get_or_404(db, Chunk, chunk_id, "Chunk not found.")

    await db.delete(chunk)
    await db.commit()


@router.post(
    "/search",
    response_model=ChunkSearchResponse,
    status_code=status.HTTP_200_OK,
)
async def search_chunks(
    payload: ChunkSearchRequest,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> ChunkSearchResponse:
    """Search chunks for a given document using the vector index."""
    await get_or_404(db, Document, payload.document_id, "Document not found.")

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

    items: List[ChunkSearchHit] = []
    for chunk_id, score in search_results:
        chunk = chunk_by_id.get(chunk_id)
        if chunk is None:
            continue
        items.append(
            ChunkSearchHit(
                chunk=ChunkResponse.model_validate(chunk),
                score=float(score),
            )
        )

    return ChunkSearchResponse(items=items)


