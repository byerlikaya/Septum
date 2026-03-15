from __future__ import annotations

"""FastAPI router for document metadata and ingestion.

This router is responsible for:
* Accepting file uploads and storing them encrypted on disk.
* Running the ingestion → sanitization → chunking → vector indexing pipeline.
* Exposing lightweight metadata and chunk counts for the frontend.
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
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
from sqlalchemy.orm import selectinload

from ..database import get_db
from ..models.document import Chunk as DocumentChunk, Document
from ..models.spreadsheet_schema import SpreadsheetSchema, SpreadsheetColumn
from ..models.regulation import RegulationRuleset
from ..models.settings import AppSettings
from ..services.chunking_strategy import (
    StructuredDocumentChunker,
    SlidingWindowChunker,
    Chunk as SemanticChunk,
)
from ..services.document_anon_store import pop_document_map, set_document_map
from ..services.ingestion.pdf_ingester import PdfIngester
from ..services.ingestion.docx_ingester import DocxIngester
from ..services.ingestion.xlsx_ingester import XlsxIngester
from ..services.ingestion.ods_ingester import OdsIngester
from ..services.ingestion.audio_ingester import AudioIngester
from ..services.ingestion.image_ingester import ImageIngester
from ..services.ingestion.router import IngestionRouter
from ..services.document_pipeline import DocumentPipeline
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
    "application/vnd.oasis.opendocument.spreadsheet": "ods",
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
        "ods": OdsIngester,
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


class SpreadsheetColumnPayload(BaseModel):
    """Represents a single spreadsheet column mapping in API payloads."""

    index: int
    technical_label: str
    semantic_label: Optional[str] = None
    is_numeric: Optional[bool] = None


class SpreadsheetSchemaResponse(BaseModel):
    """Represents the spreadsheet schema for a document."""

    document_id: int
    columns: List[SpreadsheetColumnPayload]


class SpreadsheetSchemaUpdatePayload(BaseModel):
    """Request payload for updating a spreadsheet schema.

    The document id comes from the path parameter; the body only carries the
    per-column mappings that should be applied.
    """

    columns: List[SpreadsheetColumnPayload]


@router.get(
    "/{document_id}/schema",
    response_model=SpreadsheetSchemaResponse,
    status_code=status.HTTP_200_OK,
)
async def get_spreadsheet_schema(
    document_id: int,
    db: AsyncSession = Depends(get_db),
) -> SpreadsheetSchemaResponse:
    """Return the spreadsheet schema for a document, if any."""

    document_result = await db.execute(
        select(Document).where(Document.id == document_id)
    )
    document = document_result.scalar_one_or_none()
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found.",
        )

    schema_result = await db.execute(
        select(SpreadsheetSchema)
        .options(selectinload(SpreadsheetSchema.columns))
        .where(SpreadsheetSchema.document_id == document_id)
    )
    schema = schema_result.scalar_one_or_none()
    if schema is None:
        # Return an empty schema response rather than 404 so the frontend
        # can initialise mappings lazily.
        return SpreadsheetSchemaResponse(document_id=document_id, columns=[])

    columns_payload = [
        SpreadsheetColumnPayload(
            index=col.index,
            technical_label=col.technical_label,
            semantic_label=col.semantic_label,
            is_numeric=col.is_numeric,
        )
        for col in schema.columns
    ]
    return SpreadsheetSchemaResponse(document_id=document_id, columns=columns_payload)


@router.put(
    "/{document_id}/schema",
    response_model=SpreadsheetSchemaResponse,
    status_code=status.HTTP_200_OK,
)
async def update_spreadsheet_schema(
    document_id: int,
    payload: SpreadsheetSchemaUpdatePayload,
    db: AsyncSession = Depends(get_db),
) -> SpreadsheetSchemaResponse:
    """Update the spreadsheet schema for a document.

    Only ``semantic_label`` and ``is_numeric`` fields are mutable; ``index`` and
    ``technical_label`` must match the existing schema.
    """

    document_result = await db.execute(
        select(Document).where(Document.id == document_id)
    )
    document = document_result.scalar_one_or_none()
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found.",
        )

    schema_result = await db.execute(
        select(SpreadsheetSchema)
        .options(selectinload(SpreadsheetSchema.columns))
        .where(SpreadsheetSchema.document_id == document_id)
    )
    schema = schema_result.scalar_one_or_none()
    if schema is None:
        # If no schema exists yet but the client is sending one, create it.
        schema = SpreadsheetSchema(document_id=document_id)
        db.add(schema)
        await db.flush()

    # Build an index → column mapping for quick lookups.
    columns_by_index = {col.index: col for col in schema.columns}

    for col_payload in payload.columns:
        existing = columns_by_index.get(col_payload.index)
        if existing is None:
            # New column index; create it with the provided mapping.
            new_col = SpreadsheetColumn(
                schema_id=schema.id,
                index=col_payload.index,
                technical_label=col_payload.technical_label,
                semantic_label=col_payload.semantic_label,
                is_numeric=col_payload.is_numeric,
            )
            db.add(new_col)
            continue

        # Ensure technical labels stay aligned; if they differ, keep the
        # existing value to avoid accidental schema drift.
        if existing.technical_label != col_payload.technical_label:
            continue

        existing.semantic_label = col_payload.semantic_label
        existing.is_numeric = col_payload.is_numeric

    await db.commit()

    # Reload and return the updated schema.
    refreshed = await db.execute(
        select(SpreadsheetSchema)
        .options(selectinload(SpreadsheetSchema.columns))
        .where(SpreadsheetSchema.document_id == document_id)
    )
    schema = refreshed.scalar_one()
    columns_payload = [
        SpreadsheetColumnPayload(
            index=col.index,
            technical_label=col.technical_label,
            semantic_label=col.semantic_label,
            is_numeric=col.is_numeric,
        )
        for col in schema.columns
    ]
    return SpreadsheetSchemaResponse(document_id=document_id, columns=columns_payload)


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


def _build_ingester_kwargs(
    file_format: str,
    settings: AppSettings,
) -> Optional[Dict[str, Any]]:
    """Build ingester constructor kwargs from settings for the given format."""
    if file_format in {"image", "pdf"}:
        languages = list(settings.image_ocr_languages or [])
        return {
            "languages": languages or (["en"] if file_format == "image" else []),
            "ocr_provider": getattr(settings, "ocr_provider", None) or "easyocr",
            "ocr_provider_options": getattr(settings, "ocr_provider_options", None)
            or {},
        }
    if file_format == "audio":
        kwargs: Dict[str, Any] = {
            "model_name": (settings.whisper_model or "base").strip(),
        }
        default_audio_lang = getattr(settings, "default_audio_language", None)
        if default_audio_lang and str(default_audio_lang).strip():
            kwargs["language"] = str(default_audio_lang).strip().lower()
        return kwargs
    return None


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
            # Treat "application/octet-stream" and "application/zip" as low-confidence
            # and fall through to structural checks (e.g. ODS is ZIP-based).
            if isinstance(mime, str) and mime and mime not in (
                "application/octet-stream",
                "application/zip",
            ):
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
    if raw_bytes.startswith(b"PK") and b"vnd.oasis.opendocument.spreadsheet" in raw_bytes[:4000]:
        return "application/vnd.oasis.opendocument.spreadsheet"

    if fallback_mime_type and fallback_mime_type.strip():
        return fallback_mime_type.strip()

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Unsupported or unrecognized file type.",
    )


async def _ensure_spreadsheet_schema(
    db: AsyncSession,
    document_id: int,
    metadata: dict[str, Any],
) -> None:
    """Create a generic spreadsheet schema for the document if one does not exist.

    The schema is derived from ingestion metadata and uses generic technical
    labels (e.g., ``COLUMN_1``) without storing any raw header text.
    """

    # Check if a schema already exists.
    existing = await db.execute(
        select(SpreadsheetSchema).where(SpreadsheetSchema.document_id == document_id)
    )
    if existing.scalar_one_or_none() is not None:
        return

    sheets = metadata.get("sheets") or []
    max_columns = 0
    for sheet in sheets:
        try:
            count = int(sheet.get("column_count") or 0)
        except (TypeError, ValueError):
            count = 0
        if count > max_columns:
            max_columns = count

    if max_columns <= 0:
        return

    schema = SpreadsheetSchema(document_id=document_id)
    schema.columns = [
        SpreadsheetColumn(
            index=idx,
            technical_label=f"COLUMN_{idx + 1}",
            semantic_label=None,
            is_numeric=None,
        )
        for idx in range(max_columns)
    ]
    db.add(schema)
    await db.commit()


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

    ingester_kwargs = _build_ingester_kwargs(file_format, settings)
    ingestion_result = await _INGESTION_ROUTER.ingest(
        file_path=encrypted_path,
        mime_type=mime_type,
        file_format=file_format,
        ingester_kwargs=ingester_kwargs,
    )

    if file_format == "audio" and ingestion_result.metadata:
        whisper_lang = ingestion_result.metadata.get("detected_language")
        if isinstance(whisper_lang, str) and whisper_lang.strip():
            detected_language = whisper_lang.strip().lower()
        else:
            detected_language = await _detect_language(ingestion_result.text)
    else:
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

    # If this is a tabular document, initialise a generic spreadsheet schema
    # based on ingestion metadata. This schema can later be refined by the user.
    if file_format in {"xlsx", "ods", "csv", "tsv"}:
        await _ensure_spreadsheet_schema(db, document.id, ingestion_result.metadata)

    try:
        # Short-circuit for audio documents with no transcription.
        if file_format == "audio" and not (ingestion_result.text or "").strip():
            document.ingestion_status = "failed"
            base_msg = "Audio transcription failed: decoder produced no samples or text."
            if ingestion_result.warnings:
                detail = "; ".join(ingestion_result.warnings)
                document.ingestion_error = f"{base_msg} ({detail})"
            else:
                document.ingestion_error = base_msg
            document.chunk_count = 0
            document.entity_count = 0
            await db.commit()
            await db.refresh(document)
            return DocumentResponse.model_validate(document)

        pipeline = DocumentPipeline(settings=settings)
        await pipeline.run(
            db=db,
            document=document,
            file_format=file_format,
            ingested_text=ingestion_result.text,
            ingestion_confidence=ingestion_result.confidence,
        )

    except Exception as exc:  # noqa: BLE001
        document.ingestion_status = "failed"
        document.ingestion_error = f"{type(exc).__name__}: {exc}"
        await db.commit()
        detail = "Document ingestion failed."
        if document.ingestion_error:
            detail = f"{detail} {document.ingestion_error}"
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=detail,
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

