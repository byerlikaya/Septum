from __future__ import annotations

"""FastAPI router for document metadata and ingestion.

This router is responsible for:
* Accepting file uploads and storing them encrypted on disk.
* Running the ingestion → sanitization → chunking → vector indexing pipeline.
* Exposing lightweight metadata and chunk counts for the frontend.
"""

import asyncio
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    UploadFile,
    status,
)
from langdetect import DetectorFactory
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..database import get_db
from ..models.audit_event import AuditEvent
from ..models.document import Chunk as DocumentChunk
from ..models.document import Document
from ..models.entity_detection import EntityDetection
from ..models.regulation import RegulationRuleset
from ..models.settings import AppSettings
from ..models.spreadsheet_schema import SpreadsheetColumn, SpreadsheetSchema
from ..models.user import User
from ..services.document_pipeline import DocumentPipeline
from ..services.error_logger import log_backend_error
from ..services.ingestion.audio_ingester import AudioIngester
from ..services.ingestion.docx_ingester import DocxIngester
from ..services.ingestion.image_ingester import ImageIngester
from ..services.ingestion.ods_ingester import OdsIngester
from ..services.ingestion.pdf_ingester import PdfIngester
from ..services.ingestion.router import IngestionRouter
from ..services.ingestion.xlsx_ingester import XlsxIngester
from ..utils.auth_dependency import get_current_user, require_role
from ..utils.crypto import decrypt, encrypt
from ..utils.db_helpers import (
    detect_language,
    get_or_404,
    get_owned_or_404,
    load_settings,
)


def _scope_to_owner(stmt, user: User):
    """Restrict a Document-bearing select to the caller's own rows.

    Admin role bypasses the filter — the operator can list anything for
    support purposes. Every other role only sees documents they
    uploaded.
    """
    if user.role == "admin":
        return stmt
    return stmt.where(Document.user_id == user.id)

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
    "audio/mp4": "audio",
    "audio/m4a": "audio",
    "audio/x-m4a": "audio",
    "video/mp4": "audio",
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

# Limits how many ingestion + sanitization pipelines run at once. Beyond two
# concurrent jobs, SQLite write contention and Python GIL contention on NER
# models make total throughput worse, not better.
_INGESTION_SEMAPHORE = asyncio.Semaphore(2)

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
    user: User = Depends(get_current_user),
) -> SpreadsheetSchemaResponse:
    """Return the spreadsheet schema for a document, if any."""

    await get_owned_or_404(db, Document, document_id, user, "Document not found.")

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
    user: User = Depends(require_role("admin", "editor")),
) -> SpreadsheetSchemaResponse:
    """Update the spreadsheet schema for a document.

    Only ``semantic_label`` and ``is_numeric`` fields are mutable; ``index`` and
    ``technical_label`` must match the existing schema.
    """

    await get_owned_or_404(db, Document, document_id, user, "Document not found.")

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


def _build_ingester_kwargs(
    file_format: str,
    settings: AppSettings,
) -> Optional[Dict[str, Any]]:
    """Build ingester constructor kwargs from settings for the given format."""
    if file_format in {"image", "pdf"}:
        languages = list(settings.image_ocr_languages or [])
        return {
            "languages": languages or (["en"] if file_format == "image" else []),
            "ocr_provider": getattr(settings, "ocr_provider", None) or "paddleocr",
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


def _detect_document_language(file_format: str, ingestion_result: Any) -> str:
    """Detect document language from ingestion result."""
    if file_format == "audio" and ingestion_result.metadata:
        whisper_lang = ingestion_result.metadata.get("detected_language")
        if isinstance(whisper_lang, str) and whisper_lang.strip():
            return whisper_lang.strip().lower()
    return detect_language(ingestion_result.text)


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


async def _record_background_failure(
    bg_db: AsyncSession, doc_id: int, exc: BaseException
) -> None:
    """Record an ingestion failure on a document, recovering the session if needed.

    Background ingestion tasks may hit transient SQLite write-lock contention
    during a query-invoked autoflush, which leaves the session in
    ``PendingRollbackError`` state. A subsequent ``commit()`` issued from an
    ``except`` block then masks the original error with a second exception and
    leaves the row stuck in ``processing`` status. This helper rolls back any
    pending transaction first and then issues a direct ``UPDATE`` (so it does
    not depend on the dirty ORM identity map, which rollback expires) so the
    real error message is preserved on the document row.

    The error is also forwarded to the backend ``errorlog`` table via
    :func:`log_backend_error` so it surfaces in the Error Logs UI; previously
    these failures were only attached to the Document row and the user had no
    way to see the stack trace from the UI without poking at the database.
    """
    try:
        await bg_db.rollback()
    except Exception:  # noqa: BLE001 - best-effort recovery
        pass
    try:
        await bg_db.execute(
            update(Document)
            .where(Document.id == doc_id)
            .values(
                ingestion_status="failed",
                ingestion_error=f"{type(exc).__name__}: {exc}",
            )
        )
        await bg_db.commit()
    except Exception:  # noqa: BLE001
        # If even the recovery UPDATE cannot land, the connection is
        # unsalvageable; the original error is still surfaced via the caller's
        # logs and the document will be picked up by the orphaned-cleanup pass
        # on the next server restart.
        pass

    # Also write the failure to the centralized error log so it shows up in
    # the Error Logs UI alongside HTTP-handler failures. ``log_backend_error``
    # opens its own commit, so we want to call it AFTER the document UPDATE
    # has either landed or been given up on. Best-effort: if logging itself
    # fails (e.g. broken connection), swallow so the original error is still
    # at least attached to the Document row.
    try:
        await log_backend_error(
            bg_db,
            None,
            exc,
            extra={"source": "ingestion", "document_id": doc_id},
        )
    except Exception:  # noqa: BLE001
        pass


_MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(500 * 1024 * 1024)))


@router.post(
    "/upload",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin", "editor")),
) -> DocumentResponse:
    """Upload a document, ingest it, and build its vector index.

    The raw file is:
    * Read into memory in chunks with a running size guard.
    * Typed via python-magic.
    * Encrypted with AES-256-GCM and written to disk.
    * Passed through the ingestion → sanitize → chunk → embed pipeline.

    The total payload is capped at ``MAX_UPLOAD_BYTES`` (default 500 MB).
    A larger upload is refused with HTTP 413 before the encrypt step
    so the bytes never copy through the AES-GCM input buffer.
    """
    raw_bytes = bytearray()
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        if len(raw_bytes) + len(chunk) > _MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=(
                    f"Upload exceeds {_MAX_UPLOAD_BYTES} bytes. "
                    "Set MAX_UPLOAD_BYTES to raise the cap."
                ),
            )
        raw_bytes.extend(chunk)
    raw_bytes = bytes(raw_bytes)
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

    document = Document(
        filename=str(encrypted_path.name),
        original_filename=file.filename or "",
        file_type=mime_type,
        file_format=file_format,
        detected_language="en",
        language_override=None,
        encrypted_path=str(encrypted_path),
        file_size_bytes=len(raw_bytes),
        transcription_text=None,
        ocr_confidence=0.0,
        active_regulation_ids=active_reg_ids,
        ingestion_status="processing",
        ingestion_error=None,
        user_id=current_user.id,
    )
    db.add(document)
    await db.commit()
    await db.refresh(document)

    async def _run_full_background(doc_id: int) -> None:
        """Run ingestion + pipeline in a background task."""
        from ..database import get_session_maker
        from ..services.document_progress import clear_progress, set_progress

        async with _INGESTION_SEMAPHORE, get_session_maker()() as bg_db:
            bg_doc = await bg_db.get(Document, doc_id)
            if bg_doc is None:
                return
            try:
                bg_settings = await load_settings(bg_db)

                ingester_kwargs = _build_ingester_kwargs(file_format, bg_settings)
                ingestion_result = await _INGESTION_ROUTER.ingest(
                    file_path=encrypted_path,
                    mime_type=mime_type,
                    file_format=file_format,
                    ingester_kwargs=ingester_kwargs,
                )
                set_progress(doc_id, 1)
                bg_doc.detected_language = _detect_document_language(
                    file_format, ingestion_result
                )
                bg_doc.ocr_confidence = ingestion_result.confidence
                # Flush these two fields immediately so they cannot become
                # stale dirty state that a query-invoked autoflush inside the
                # pipeline tries to write under SQLite write-lock contention.
                await bg_db.commit()

                if file_format in {"xlsx", "ods", "csv", "tsv"}:
                    await _ensure_spreadsheet_schema(
                        bg_db, bg_doc.id, ingestion_result.metadata
                    )

                if (
                    file_format == "audio"
                    and not (ingestion_result.text or "").strip()
                ):
                    bg_doc.ingestion_status = "failed"
                    base_msg = (
                        "Audio transcription failed: "
                        "decoder produced no samples or text."
                    )
                    if ingestion_result.warnings:
                        detail = "; ".join(ingestion_result.warnings)
                        bg_doc.ingestion_error = f"{base_msg} ({detail})"
                    else:
                        bg_doc.ingestion_error = base_msg
                    bg_doc.chunk_count = 0
                    bg_doc.entity_count = 0
                    await bg_db.commit()
                    return

                bg_pipeline = DocumentPipeline(settings=bg_settings)
                await bg_pipeline.run(
                    db=bg_db,
                    document=bg_doc,
                    file_format=file_format,
                    ingested_text=ingestion_result.text,
                    ingestion_confidence=ingestion_result.confidence,
                    on_progress=lambda pct: set_progress(doc_id, pct),
                )
            except Exception as bg_exc:  # noqa: BLE001
                await _record_background_failure(bg_db, doc_id, bg_exc)
            finally:
                clear_progress(doc_id)

    background_tasks.add_task(_run_full_background, document.id)

    return DocumentResponse.model_validate(document)


@router.get(
    "/progress",
    status_code=status.HTTP_200_OK,
)
async def get_processing_progress(
    ids: str = "",
    _user: User = Depends(get_current_user),
) -> dict:
    """Return processing progress for documents being processed.

    Accepts a comma-separated list of document IDs.
    Returns a mapping of document_id → percent (0-99).
    """
    from ..services.document_progress import get_progress

    if not ids.strip():
        return {}
    doc_ids = [int(x) for x in ids.split(",") if x.strip().isdigit()]
    return {did: get_progress(did) for did in doc_ids}


@router.get(
    "",
    response_model=DocumentListResponse,
    status_code=status.HTTP_200_OK,
)
async def list_documents(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DocumentListResponse:
    """Return the caller's own documents, newest first.

    Per-user isolation is the default: every non-admin role sees only
    rows they uploaded. ``admin`` role bypasses the filter so operators
    can run support queries across the whole corpus.
    """
    stmt = _scope_to_owner(
        select(Document).order_by(Document.uploaded_at.desc()), user
    )
    result = await db.execute(stmt)
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
    user: User = Depends(get_current_user),
) -> DocumentResponse:
    """Return metadata for a single document."""
    document = await get_owned_or_404(
        db, Document, document_id, user, "Document not found."
    )
    return DocumentResponse.model_validate(document)


@router.get(
    "/{document_id}/anon-summary",
    status_code=status.HTTP_200_OK,
)
async def get_anon_summary(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Return a privacy-safe summary of the anonymization map (entity types and counts only)."""
    from ..services.document_anon_store import get_document_map

    await get_owned_or_404(db, Document, document_id, user, "Document not found.")
    anon_map = await get_document_map(document_id)
    if anon_map is None:
        return {"document_id": document_id, "entities": {}, "total": 0}

    type_counts: dict[str, int] = {}
    for placeholder in anon_map.entity_map.values():
        entity_type = placeholder.strip("[]").rsplit("_", 1)[0] if "_" in placeholder else placeholder.strip("[]")
        type_counts[entity_type] = type_counts.get(entity_type, 0) + 1

    return {
        "document_id": document_id,
        "entities": type_counts,
        "total": sum(type_counts.values()),
    }


class EntityDetectionResponse(BaseModel):
    """Serialized view of a single detected entity with position data."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    chunk_id: int
    entity_type: str
    placeholder: str
    start_offset: int
    end_offset: int
    score: float


class EntityDetectionListResponse(BaseModel):
    """Wrapper for entity detection results."""

    items: List[EntityDetectionResponse]
    total: int


@router.get(
    "/{document_id}/entity-detections",
    response_model=EntityDetectionListResponse,
    status_code=status.HTTP_200_OK,
)
async def get_entity_detections(
    document_id: int,
    chunk_id: Optional[int] = None,
    entity_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> EntityDetectionListResponse:
    """Return per-entity detection records with positions for a document."""
    await get_owned_or_404(db, Document, document_id, user, "Document not found.")

    stmt = (
        select(EntityDetection)
        .where(EntityDetection.document_id == document_id)
        .order_by(EntityDetection.chunk_id, EntityDetection.start_offset)
    )
    if chunk_id is not None:
        stmt = stmt.where(EntityDetection.chunk_id == chunk_id)
    if entity_type is not None:
        stmt = stmt.where(EntityDetection.entity_type == entity_type)

    result = await db.execute(stmt)
    rows = list(result.scalars().all())
    return EntityDetectionListResponse(
        items=[EntityDetectionResponse.model_validate(r) for r in rows],
        total=len(rows),
    )


@router.get(
    "/{document_id}/raw",
    status_code=status.HTTP_200_OK,
)
async def get_document_raw(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Stream the decrypted original file for in-browser preview."""
    from fastapi.responses import Response

    document = await get_owned_or_404(
        db, Document, document_id, user, "Document not found."
    )
    encrypted_path = Path(document.encrypted_path)
    if not encrypted_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Encrypted file not found on disk.",
        )
    raw_bytes = decrypt(encrypted_path.read_bytes())
    return Response(
        content=raw_bytes,
        media_type=document.file_type,
        headers={"Content-Disposition": 'inline; filename="{}"'.format(
            re.sub(r'["\\\r\n]', "_", document.original_filename)
        )},
    )


@router.patch(
    "/{document_id}/language",
    response_model=DocumentResponse,
    status_code=status.HTTP_200_OK,
)
async def update_document_language(
    document_id: int,
    payload: LanguageUpdatePayload,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin", "editor")),
) -> DocumentResponse:
    """Override the detected language for a document."""
    document = await get_owned_or_404(
        db, Document, document_id, user, "Document not found."
    )

    document.language_override = payload.language
    await db.commit()
    await db.refresh(document)
    return DocumentResponse.model_validate(document)


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def delete_document(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin", "editor")),
) -> None:
    """Delete a document, its chunks, encrypted file, and vector index."""
    document = await get_owned_or_404(
        db, Document, document_id, user, "Document not found."
    )

    try:
        path = Path(document.encrypted_path)
        if path.exists():
            path.unlink()
    except OSError:
        pass

    await DocumentPipeline.cleanup_artifacts(document_id)

    from sqlalchemy import delete as sa_delete
    await db.execute(
        sa_delete(AuditEvent).where(AuditEvent.document_id == document_id)
    )
    await db.delete(document)
    await db.commit()


@router.post(
    "/{document_id}/reprocess",
    response_model=DocumentResponse,
    status_code=status.HTTP_200_OK,
)
async def reprocess_document(
    document_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin", "editor")),
) -> DocumentResponse:
    """Re-run the sanitization, chunking, and indexing pipeline for an existing document."""
    document = await get_owned_or_404(
        db, Document, document_id, user, "Document not found."
    )

    encrypted_path = Path(document.encrypted_path)
    if not encrypted_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Encrypted source file not found on disk.",
        )

    from sqlalchemy import delete as sa_delete

    await db.execute(
        sa_delete(EntityDetection).where(EntityDetection.document_id == document_id)
    )
    await db.execute(
        sa_delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
    )
    document.ingestion_status = "processing"
    document.ingestion_error = None
    document.chunk_count = 0
    document.entity_count = 0
    await db.commit()
    await db.refresh(document)

    file_format = document.file_format
    mime_type = document.file_type

    async def _reprocess_background(doc_id: int) -> None:
        from ..database import get_session_maker
        from ..services.document_progress import clear_progress, set_progress

        async with _INGESTION_SEMAPHORE, get_session_maker()() as bg_db:
            bg_doc = await bg_db.get(Document, doc_id)
            if bg_doc is None:
                return
            try:
                await DocumentPipeline.cleanup_artifacts(doc_id)

                bg_doc.active_regulation_ids = await _snapshot_active_regulations(bg_db)
                bg_settings = await load_settings(bg_db)

                ingester_kwargs = _build_ingester_kwargs(file_format, bg_settings)
                ingestion_result = await _INGESTION_ROUTER.ingest(
                    file_path=encrypted_path,
                    mime_type=mime_type,
                    file_format=file_format,
                    ingester_kwargs=ingester_kwargs,
                )
                set_progress(doc_id, 1)

                bg_doc.detected_language = _detect_document_language(
                    file_format, ingestion_result
                )
                bg_doc.ocr_confidence = ingestion_result.confidence
                await bg_db.commit()

                if file_format == "audio" and not (ingestion_result.text or "").strip():
                    bg_doc.ingestion_status = "failed"
                    bg_doc.ingestion_error = "Audio transcription produced no text."
                    await bg_db.commit()
                    return

                pipeline = DocumentPipeline(settings=bg_settings)
                await pipeline.run(
                    db=bg_db,
                    document=bg_doc,
                    file_format=file_format,
                    ingested_text=ingestion_result.text,
                    ingestion_confidence=ingestion_result.confidence,
                    on_progress=lambda pct: set_progress(doc_id, pct),
                )
            except Exception as bg_exc:  # noqa: BLE001
                await _record_background_failure(bg_db, doc_id, bg_exc)
            finally:
                clear_progress(doc_id)

    background_tasks.add_task(_reprocess_background, document.id)

    return DocumentResponse.model_validate(document)

