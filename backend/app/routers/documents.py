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
from ..models.document import Chunk, Document
from ..models.regulation import RegulationRuleset
from ..models.settings import AppSettings
from ..services.anonymization_map import AnonymizationMap
from ..services.ingestion.pdf_ingester import PdfIngester
from ..services.ingestion.docx_ingester import DocxIngester
from ..services.ingestion.xlsx_ingester import XlsxIngester
from ..services.ingestion.audio_ingester import AudioIngester
from ..services.ingestion.image_ingester import ImageIngester
from ..services.ingestion.router import IngestionRouter
from ..services.sanitizer import PIISanitizer
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
    # Word / text-like
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    # Spreadsheets
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    # Images
    "image/png": "image",
    "image/jpeg": "image",
    "image/jpg": "image",
    "image/tiff": "image",
    "image/bmp": "image",
    # Audio
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


# langdetect is non-deterministic by default; fix the seed for stability.
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

    # Generic fallbacks for MIME families where the ingester only cares about
    # the high-level category rather than the exact subtype.
    if mime_type.startswith("audio/"):
        return "audio"
    if mime_type.startswith("image/"):
        return "image"

    # Some browsers/uploaders default to application/octet-stream for audio
    # uploads. Treat this as audio so that common formats such as .m4a are
    # still handled by the audio ingester.
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
    # 1) Best-effort: python-magic if the environment provides libmagic.
    if _magic is not None:
        try:
            mime = _magic.from_buffer(raw_bytes, mime=True)  # type: ignore[arg-type]
            if isinstance(mime, str) and mime:
                return mime
        except Exception:  # noqa: BLE001
            # Fall through to manual detection.
            pass

    # 2) Minimal manual signatures (still content-based, no extensions used).
    # Documents / images
    if raw_bytes.startswith(b"%PDF-"):
        return "application/pdf"
    if raw_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw_bytes.startswith(b"\xff\xd8"):
        return "image/jpeg"

    # Audio formats
    # WAV: "RIFF" .... "WAVE"
    if raw_bytes.startswith(b"RIFF") and raw_bytes[8:12] == b"WAVE":
        return "audio/wav"
    # OGG: "OggS"
    if raw_bytes.startswith(b"OggS"):
        return "audio/ogg"
    # FLAC: "fLaC"
    if raw_bytes.startswith(b"fLaC"):
        return "audio/flac"
    # MP3: ID3 header or MPEG frame sync (0xFFE)
    if raw_bytes.startswith(b"ID3"):
        return "audio/mpeg"
    if len(raw_bytes) > 2 and raw_bytes[0] == 0xFF and (raw_bytes[1] & 0xE0) == 0xE0:
        return "audio/mpeg"
    # MP4/M4A and similar ISO-BMFF containers: "ftyp" box near the start.
    if len(raw_bytes) > 12 and raw_bytes[4:8] == b"ftyp":
        return "audio/mp4"

    # 3) Last-resort: trust the provided fallback MIME type (usually the
    # Content-Type header from the upload). This keeps detection primarily
    # content-based, while still allowing ingestion of formats our simple
    # signatures do not yet recognize.
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

    # Detect MIME type from content only (python-magic if available, otherwise
    # a small set of manual signatures for common formats such as PDF and audio).
    # As a last resort, fall back to the client-provided Content-Type header.
    mime_type = _detect_mime_type(raw_bytes, getattr(file, "content_type", None))

    file_format = _mime_to_format(mime_type)

    # Encrypt and persist the file.
    encrypted_bytes = encrypt(raw_bytes)
    safe_name = f"{uuid4().hex}"
    encrypted_path = _DOC_STORAGE_DIR / safe_name
    encrypted_path.write_bytes(encrypted_bytes)

    active_reg_ids = await _snapshot_active_regulations(db)

    # Ingest, sanitize, chunk, and index.
    settings = await _load_settings(db)

    # Use the ingestion router to extract raw text and metadata.
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
        # Build anonymization map and sanitizer.
        anon_map = AnonymizationMap(document_id=document.id, language=detected_language)
        sanitizer = PIISanitizer(settings=settings)

        sanitize_result = await asyncio.to_thread(
            sanitizer.sanitize,
            ingestion_result.text,
            detected_language,
            anon_map,
        )

        sanitized_text = sanitize_result.sanitized_text
        entity_count = sanitize_result.entity_count

        # Persist sanitized transcription text for downstream consumers such as
        # audio preview UIs. This never stores raw PII, only the sanitized form.
        document.transcription_text = sanitized_text

        # Simple character-based chunking; can be replaced with a more
        # structure-aware chunker without changing API contracts.
        chunks: List[Chunk] = []
        chunk_size = max(settings.chunk_size, 1)
        overlap = max(min(settings.chunk_overlap, chunk_size - 1), 0)

        text_len = len(sanitized_text)
        index = 0
        chunk_index = 0

        while index < text_len:
            end = min(index + chunk_size, text_len)
            segment = sanitized_text[index:end]
            chunk = Chunk(
                document_id=document.id,
                index=chunk_index,
                sanitized_text=segment,
                char_count=len(segment),
                source_page=None,
                source_slide=None,
                source_sheet=None,
                source_timestamp_start=None,
                source_timestamp_end=None,
                section_title=None,
            )
            db.add(chunk)
            chunks.append(chunk)

            if end >= text_len:
                break

            index = end - overlap
            chunk_index += 1

        await db.commit()
        for chunk in chunks:
            await db.refresh(chunk)

        # Update document statistics.
        document.chunk_count = len(chunks)
        document.entity_count = entity_count
        document.ingestion_status = "completed"
        document.ingestion_error = None
        await db.commit()
        await db.refresh(document)

        # Build the FAISS index in a worker thread to keep the event loop
        # responsive.
        if chunks:
            vector_store = VectorStore()
            await asyncio.to_thread(
                vector_store.index_document,
                document.id,
                [c.id for c in chunks],
                [c.sanitized_text for c in chunks],
            )

    except Exception as exc:  # noqa: BLE001
        # Best-effort error handling: mark the document as failed without
        # leaking any raw content in the stored message.
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

    # Remove encrypted file from disk.
    try:
        path = Path(document.encrypted_path)
        if path.exists():
            path.unlink()
    except OSError:
        # Best-effort; failures should not prevent DB cleanup.
        pass

    # Remove FAISS index for this document.
    try:
        vector_store = VectorStore()
        vector_store.delete_index(document_id)
    except Exception:
        # Best-effort; ignore index deletion errors.
        pass

    await db.delete(document)
    await db.commit()

