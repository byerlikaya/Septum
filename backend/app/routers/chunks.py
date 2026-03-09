from __future__ import annotations

"""FastAPI router for accessing sanitized document chunks."""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.document import Chunk, Document
from ..utils.text_utils import normalize_unicode


router = APIRouter(prefix="/api/chunks", tags=["chunks"])


class ChunkResponse(BaseModel):
    """Serialized view of a sanitized chunk."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    document_id: int
    index: int
    sanitized_text: str
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
) -> ChunkListResponse:
    """Return sanitized chunks, optionally filtered by document."""
    stmt = select(Chunk)
    if document_id is not None:
        # Ensure the document exists to provide a clearer error on invalid IDs.
        doc_result = await db.execute(
            select(Document).where(Document.id == document_id)
        )
        doc = doc_result.scalar_one_or_none()
        if doc is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document not found.",
            )
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
) -> ChunkResponse:
    """Return a single sanitized chunk by id."""
    result = await db.execute(select(Chunk).where(Chunk.id == chunk_id))
    chunk = result.scalar_one_or_none()
    if chunk is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chunk not found.",
        )
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
) -> ChunkResponse:
    """Update mutable fields of a chunk (inline edit support)."""
    result = await db.execute(select(Chunk).where(Chunk.id == chunk_id))
    chunk = result.scalar_one_or_none()
    if chunk is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chunk not found.",
        )

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
)
async def delete_chunk(
    chunk_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a chunk."""
    result = await db.execute(select(Chunk).where(Chunk.id == chunk_id))
    chunk = result.scalar_one_or_none()
    if chunk is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chunk not found.",
        )

    await db.delete(chunk)
    await db.commit()

