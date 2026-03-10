from __future__ import annotations

"""FastAPI router for document metadata and ingestion.

This router is responsible for:
* Accepting file uploads and storing them encrypted on disk.
* Running the ingestion → sanitization → chunking → vector indexing pipeline.
* Exposing lightweight metadata and chunk counts for the frontend.
"""

import asyncio
import os
from pathlib import Path
from typing import List, Optional
from uuid import uuid4
from datetime import datetime

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    UploadFile,
    status,
)
from langdetect import DetectorFactory, LangDetectException, detect
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.document import Chunk as DocumentChunk, Document
from ..models.regulation import RegulationRuleset
from ..models.settings import AppSettings
from ..services.anonymization_map import AnonymizationMap
from ..services.chunking_strategy import (
    StructuredDocumentChunker,
    SlidingWindowChunker,
    Chunk as SemanticChunk,
)
from ..services.document_anon_store import pop_document_map, set_document_map
from ..services.ingestion.pdf_ingester import PdfIngester
from ..services.ingestion.docx_ingester import DocxIngester
from ..services.ingestion.xlsx_ingester import XlsxIngester
from ..services.ingestion.audio_ingester import AudioIngester
from ..services.ingestion.image_ingester import ImageIngester
from ..services.ingestion.router import IngestionRouter
from ..services.sanitizer import PIISanitizer
from ..services.policy_composer import PolicyComposer
from ..services.vector_store import VectorStore
from ..utils.crypto import encrypt

try:  # python-magic with system libmagic; may fail if libmagic is missing.
    import magic as _magic  # type: ignore[import]
except ImportError:  # pragma: no cover - environment-dependent
    _magic = None


router = APIRouter(prefix="/api/documents", tags=["documents"])


_DOC_STORAGE_DIR = Path(os.getenv("DOCUMENT_STORAGE_DIR", "./documents")).resolve()
_DOC_STORAGE_DIR.mkdir(parents=True, exist_ok=True)


_MIME_TO_FORMAT: dict[str, str] = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "image/png": "image",
    "image/jpeg": "image",
    "image/jpg": "image",
    "image/tiff": "image",
    "image/bmp": "image",
    "audio/mpeg": "audio",
    "audio/mp3": "audio",
    "audio/wav": "audio",
    "audio/x-wav": "audio",
    "audio/flac": "audio",
    "audio/ogg": "audio",
}


_INGESTION_ROUTER = IngestionRouter(
    ingesters={
        "pdf": PdfIngester,
        "docx": DocxIngester,
        "xlsx": XlsxIngester,
        "audio": AudioIngester,
        "image": ImageIngester,
    }
)

DetectorFactory.seed = 42


class DocumentResponse(BaseModel):
    """Serialized view of a document without exposing raw content."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    original_filename: str
    file_type: str
    file_format: str
    detected_language: str
    language_override: Optional[str]
    uploaded_at: datetime
    encrypted_path: str
    chunk_count: int
    entity_count: int
    ingestion_status: str
    ingestion_error: Optional[str]
    file_size_bytes: int
    transcription_text: Optional[str]
    ocr_confidence: Optional[float]
    active_regulation_ids: List[str]


class DocumentListResponse(BaseModel):
    """Wrapper for listing multiple documents."""

    items: List[DocumentResponse]


class LanguageUpdatePayload(BaseModel):
    """Request body for overriding a document's language."""

    language: str


async def _load_settings(db: AsyncSession) -> AppSettings:
    """Return the singleton :class:`AppSettings` row for ingestion settings."""
    result = await db.execute(select(AppSettings).where(AppSettings.id == 1))
    settings = result.scalar_one_or_none()
    if settings is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Application settings have not been initialized.",
        )
    return settings


async def _detect_language(text: str) -> str:
    """Best-effort language detection with a safe fallback."""
    if not text:
        return "en"
    try:
        return detect(text)
    except LangDetectException:
        return "en"


def _mime_to_format(mime_type: str) -> str:
    """Map a MIME type to an internal file format identifier.

    The mapping is primarily driven by ``_MIME_TO_FORMAT`` but falls back to
    generic handlers for whole MIME families such as ``audio/*`` and
    ``image/*`` so that less common subtypes (for example ``audio/m4a``) are
    still ingested correctly.
    """
    fmt = _MIME_TO_FORMAT.get(mime_type)
    if fmt is not None:
        return fmt

    if mime_type.startswith("audio/"):
        return "audio"
    if mime_type.startswith("image/"):
        return "image"

    if mime_type == "application/octet-stream":
        return "audio"

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Unsupported MIME type: {mime_type}",
    )


async def _snapshot_active_regulations(db: AsyncSession) -> List[str]:
    result = await db.execute(
        select(RegulationRuleset.id).where(RegulationRuleset.is_active.is_(True))
    )
    return [row[0] for row in result.all()]


def _detect_mime_type(raw_bytes: bytes, fallback_mime_type: Optional[str] = None) -> str:
    """Detect MIME type from raw bytes using content-based heuristics.

    Preference order:
    1. python-magic/libmagic if available.
    2. Lightweight manual signatures for common formats (PDF, PNG, JPEG, audio).
    3. Fallback to the provided ``fallback_mime_type`` (typically the client-
       supplied ``Content-Type``) if detection fails.
    """
    if _magic is not None:
        try:
            mime = _magic.from_buffer(raw_bytes, mime=True)  # type: ignore[arg-type]
            # Treat "application/octet-stream" as low-confidence and fall through
            # to structural signature checks so that containers such as MP4/M4A
            # are still detected correctly by content.
            if isinstance(mime, str) and mime and mime != "application/octet-stream":
                return mime
        except Exception:  # noqa: BLE001
            pass

    if raw_bytes.startswith(b"%PDF-"):
        return "application/pdf"
    if raw_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw_bytes.startswith(b"\xff\xd8"):
        return "image/jpeg"

    if raw_bytes.startswith(b"RIFF") and raw_bytes[8:12] == b"WAVE":
        return "audio/wav"
    if raw_bytes.startswith(b"OggS"):
        return "audio/ogg"
    if raw_bytes.startswith(b"fLaC"):
        return "audio/flac"
    if raw_bytes.startswith(b"ID3"):
        return "audio/mpeg"
    if len(raw_bytes) > 2 and raw_bytes[0] == 0xFF and (raw_bytes[1] & 0xE0) == 0xE0:
        return "audio/mpeg"
    if len(raw_bytes) > 12 and raw_bytes[4:8] == b"ftyp":
        return "audio/mp4"

    if fallback_mime_type and fallback_mime_type.strip():
        return fallback_mime_type.strip()

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Unsupported or unrecognized file type.",
    )


@router.post(
    "/upload",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> DocumentResponse:
    """Upload a document, ingest it, and build its vector index.

    The raw file is:
    * Read into memory.
    * Typed via python-magic.
    * Encrypted with AES-256-GCM and written to disk.
    * Passed through the ingestion → sanitize → chunk → embed pipeline.
    """
    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )

    mime_type = _detect_mime_type(raw_bytes, getattr(file, "content_type", None))

    file_format = _mime_to_format(mime_type)

    encrypted_bytes = encrypt(raw_bytes)
    safe_name = f"{uuid4().hex}"
    encrypted_path = _DOC_STORAGE_DIR / safe_name
    encrypted_path.write_bytes(encrypted_bytes)

    active_reg_ids = await _snapshot_active_regulations(db)

    settings = await _load_settings(db)

    ingestion_result = await _INGESTION_ROUTER.ingest(
        file_path=encrypted_path,
        mime_type=mime_type,
        file_format=file_format,
    )

    detected_language = await _detect_language(ingestion_result.text)

    document = Document(
        filename=str(encrypted_path.name),
        original_filename=file.filename or "",
        file_type=mime_type,
        file_format=file_format,
        detected_language=detected_language,
        language_override=None,
        encrypted_path=str(encrypted_path),
        file_size_bytes=len(raw_bytes),
        transcription_text=None,
        ocr_confidence=ingestion_result.confidence,
        active_regulation_ids=active_reg_ids,
        ingestion_status="processing",
        ingestion_error=None,
    )
    db.add(document)
    await db.commit()
    await db.refresh(document)

    try:
        # Short-circuit for audio documents with no transcription.
        if file_format == "audio" and not (ingestion_result.text or "").strip():
            document.ingestion_status = "failed"
            document.ingestion_error = (
                "Audio transcription failed: decoder produced no samples or text."
            )
            document.chunk_count = 0
            document.entity_count = 0
            await db.commit()
            await db.refresh(document)
            return DocumentResponse.model_validate(document)

        anon_map = AnonymizationMap(document_id=document.id, language=detected_language)
        
        policy_composer = PolicyComposer()
        policy = await policy_composer.compose(db)
        
        sanitizer = PIISanitizer(settings=settings, policy=policy)

        sanitize_result = await asyncio.to_thread(
            sanitizer.sanitize,
            ingestion_result.text,
            detected_language,
            anon_map,
        )

        sanitized_text = sanitize_result.sanitized_text
        entity_count = sanitize_result.entity_count

        document.transcription_text = sanitized_text

        # Use semantic chunking for structured documents (PDF, DOCX)
        # and sliding window for unstructured formats
        if file_format in {"pdf", "docx"}:
            chunker = StructuredDocumentChunker(
                max_chunk_size=max(settings.pdf_chunk_size, 800)
            )
        else:
            chunker = SlidingWindowChunker(
                chunk_size=max(settings.chunk_size, 1),
                overlap=max(min(settings.chunk_overlap, settings.chunk_size - 1), 0),
            )

        semantic_chunks = await asyncio.to_thread(chunker.chunk, sanitized_text)

        merged_chunks: List[SemanticChunk] = []
        i = 0
        while i < len(semantic_chunks):
            current = semantic_chunks[i]

            # Detect very short, title-like chunks and merge them with the next chunk.
            if (
                current.char_count < 50
                and current.section_title
                and i + 1 < len(semantic_chunks)
            ):
                next_chunk = semantic_chunks[i + 1]
                merged_text = f"{current.text.strip()}\n{next_chunk.text}"
                merged_title = current.section_title or next_chunk.section_title

                merged_chunk = SemanticChunk(
                    text=merged_text,
                    index=current.index,
                    source_page=current.source_page or next_chunk.source_page,
                    section_title=merged_title,
                    char_count=len(merged_text),
                )
                merged_chunks.append(merged_chunk)
                i += 2
            else:
                merged_chunks.append(current)
                i += 1

        semantic_chunks = merged_chunks

        chunks: List[DocumentChunk] = []
        for semantic_chunk in semantic_chunks:
            chunk = DocumentChunk(
                document_id=document.id,
                index=semantic_chunk.index,
                sanitized_text=semantic_chunk.text,
                char_count=semantic_chunk.char_count,
                source_page=semantic_chunk.source_page,
                source_slide=None,
                source_sheet=None,
                source_timestamp_start=None,
                source_timestamp_end=None,
                section_title=semantic_chunk.section_title,
            )
            db.add(chunk)
            chunks.append(chunk)

        await db.commit()
        for chunk in chunks:
            await db.refresh(chunk)

        document.chunk_count = len(chunks)
        document.entity_count = entity_count
        document.ingestion_status = "completed"
        document.ingestion_error = None
        await db.commit()
        await db.refresh(document)

        set_document_map(document.id, anon_map)

        if chunks:
            vector_store = VectorStore()
            await asyncio.to_thread(
                vector_store.index_document,
                document.id,
                [c.id for c in chunks],
                [c.sanitized_text for c in chunks],
            )

    except Exception as exc:  # noqa: BLE001
        document.ingestion_status = "failed"
        document.ingestion_error = f"{type(exc).__name__}: {exc}"
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Document ingestion failed.",
        ) from exc

    return DocumentResponse.model_validate(document)


@router.get(
    "",
    response_model=DocumentListResponse,
    status_code=status.HTTP_200_OK,
)
async def list_documents(
    db: AsyncSession = Depends(get_db),
) -> DocumentListResponse:
    """Return all documents ordered by upload time (newest first)."""
    result = await db.execute(select(Document).order_by(Document.uploaded_at.desc()))
    docs = list(result.scalars().all())
    return DocumentListResponse(
        items=[DocumentResponse.model_validate(d) for d in docs]
    )


@router.get(
    "/{document_id}",
    response_model=DocumentResponse,
    status_code=status.HTTP_200_OK,
)
async def get_document(
    document_id: int,
    db: AsyncSession = Depends(get_db),
) -> DocumentResponse:
    """Return metadata for a single document."""
    result = await db.execute(select(Document).where(Document.id == document_id))
    document = result.scalar_one_or_none()
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found.",
        )
    return DocumentResponse.model_validate(document)


@router.patch(
    "/{document_id}/language",
    response_model=DocumentResponse,
    status_code=status.HTTP_200_OK,
)
async def update_document_language(
    document_id: int,
    payload: LanguageUpdatePayload,
    db: AsyncSession = Depends(get_db),
) -> DocumentResponse:
    """Override the detected language for a document."""
    result = await db.execute(select(Document).where(Document.id == document_id))
    document = result.scalar_one_or_none()
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found.",
        )

    document.language_override = payload.language
    await db.commit()
    await db.refresh(document)
    return DocumentResponse.model_validate(document)


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_document(
    document_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a document, its chunks, encrypted file, and vector index."""
    result = await db.execute(select(Document).where(Document.id == document_id))
    document = result.scalar_one_or_none()
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found.",
        )

    try:
        path = Path(document.encrypted_path)
        if path.exists():
            path.unlink()
    except OSError:
        pass

    try:
        vector_store = VectorStore()
        vector_store.delete_index(document_id)
    except Exception:
        pass

    pop_document_map(document_id)

    await db.delete(document)
    await db.commit()

