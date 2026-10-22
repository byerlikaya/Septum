from __future__ import annotations

"""FastAPI router for document metadata and ingestion.

This router is responsible for:
* Accepting file uploads and storing them encrypted on disk.
* Running the ingestion → sanitization → chunking → vector indexing pipeline.
* Exposing lightweight metadata and chunk counts for the frontend.
"""

import asyncio
import logging
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
    Query,
    UploadFile,
    status,
)
from langdetect import DetectorFactory
from pydantic import BaseModel, ConfigDict
from septum_core.model_integrity import ModelRefusal
from sqlalchemy import select, update
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..database import get_db
from ..errors import public_error_text
from ..models.audit_event import AuditEvent
from ..models.document import Chunk as DocumentChunk
from ..models.document import Document
from ..models.entity_detection import EntityDetection
from ..models.regulation import RegulationRuleset
from ..models.settings import AppSettings
from ..models.spreadsheet_schema import SpreadsheetColumn, SpreadsheetSchema
from ..models.user import User
from ..request_limits import MAX_DOCUMENT_ID, MAX_PROGRESS_IDS
from ..services.document_anon_store import (
    DocumentMapErasureError,
    DocumentMapLockHandle,
    DocumentMapLockLost,
    document_map_lock,
)
from ..services.document_pipeline import (
    ArtifactErasureError,
    DocumentPipeline,
    erase_document_artifacts,
)
from ..services.error_logger import log_backend_error, report_error_log_failure
from ..services.ingestion.audio_ingester import AudioIngester
from ..services.ingestion.docx_ingester import DocxIngester
from ..services.ingestion.image_ingester import ImageIngester
from ..services.ingestion.ods_ingester import OdsIngester
from ..services.ingestion.pdf_ingester import PdfIngester
from ..services.ingestion.router import IngestionRouter
from ..services.ingestion.xlsx_ingester import XlsxIngester
from ..services.pii_gate import gate_document_texts
from ..services.policy_snapshot import (
    PolicySnapshot,
    current_policy_snapshot,
    document_staleness,
)
from ..utils.auth_dependency import get_current_user, require_role
from ..utils.crypto import decrypt, encrypt
from ..utils.db_helpers import (
    detect_language,
    get_owned_or_404,
    load_settings,
    supported_language_code,
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


logger = logging.getLogger(__name__)

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
    ingestion_error_code: Optional[str] = None
    file_size_bytes: int
    transcription_text: Optional[str]
    ocr_confidence: Optional[float]
    active_regulation_ids: List[str]
    needs_reprocessing: bool = False
    missing_entity_types: List[str] = []
    sanitized_policy_known: bool = True


def _document_response(
    document: Document, current: Optional[PolicySnapshot]
) -> DocumentResponse:
    """Serialize ``document`` with its staleness against the ``current`` policy.

    ``current`` may be ``None`` only for a document that is not completed
    (just uploaded or claimed for reprocessing).
    """
    staleness = document_staleness(document, current)
    return DocumentResponse.model_validate(document).model_copy(
        update={
            "needs_reprocessing": staleness.needs_reprocessing,
            "missing_entity_types": staleness.missing_entity_types,
            "sanitized_policy_known": staleness.sanitized_policy_known,
        }
    )


async def _single_document_response(
    db: AsyncSession, document: Document
) -> DocumentResponse:
    """Serialize one document, reading the current policy only when it is judged."""
    current = (
        await current_policy_snapshot(db)
        if document.ingestion_status == "completed"
        else None
    )
    return _document_response(document, current)


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
    ``technical_label`` must match the existing schema. Semantic labels are
    user-typed and end up in the cloud prompt, so they pass the PII gate
    before they are stored. ``technical_label`` is never taken from the
    client: new columns get the server-generated ``COLUMN_<index+1>``.
    """

    document = await get_owned_or_404(
        db, Document, document_id, user, "Document not found."
    )
    typed_labels = [
        c.semantic_label for c in payload.columns if c.semantic_label
    ]
    if typed_labels:
        settings = await load_settings(db)
        gated_labels = iter(
            await gate_document_texts(db, settings, document, typed_labels)
        )
        for c in payload.columns:
            if c.semantic_label:
                c.semantic_label = next(gated_labels)

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
        columns_by_index = {}
    else:
        columns_by_index = {col.index: col for col in schema.columns}

    for col_payload in payload.columns:
        existing = columns_by_index.get(col_payload.index)
        if existing is None:
            new_col = SpreadsheetColumn(
                schema_id=schema.id,
                index=col_payload.index,
                technical_label=f"COLUMN_{col_payload.index + 1}",
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
        code = supported_language_code(whisper_lang) if isinstance(whisper_lang, str) else None
        if code is not None:
            return code
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
        except Exception as exc:
            logger.debug("Content-based MIME detection failed, using structural checks: %s", type(exc).__name__)

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
    bg_db: AsyncSession, doc_id: int, exc: BaseException, user_id: int
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
    The row draws on the requesting user's error-log budget, so repeated
    failing uploads cannot flood the table either.
    """
    try:
        await bg_db.rollback()
    except Exception as log_exc:
        logger.warning("Rollback during ingestion failure handling failed: %s", type(log_exc).__name__)
    try:
        await bg_db.execute(
            update(Document)
            .where(Document.id == doc_id)
            .values(
                ingestion_status="failed",
                ingestion_error=public_error_text(exc, "Ingestion failed"),
                ingestion_error_code=exc.code if isinstance(exc, ModelRefusal) else None,
            )
        )
        await bg_db.commit()
    except Exception as log_exc:
        # If even the recovery UPDATE cannot land, the connection is
        # unsalvageable; the original error is still surfaced via the caller's
        # logs and the document will be picked up by the orphaned-cleanup pass
        # on the next server restart.
        logger.warning("Recovery update for failed ingestion did not land: %s", type(log_exc).__name__)

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
            identity=f"user:{user_id}",
        )
    except Exception as log_exc:
        report_error_log_failure(log_exc)


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
                bg_doc.detected_language = await asyncio.to_thread(
                    _detect_document_language, file_format, ingestion_result
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
                await _record_background_failure(bg_db, doc_id, bg_exc, current_user.id)
            finally:
                clear_progress(doc_id)

    background_tasks.add_task(_run_full_background, document.id)

    return _document_response(document, None)


_PROGRESS_ID_RE = re.compile(r"[0-9]{1,10}")


@router.get(
    "/progress",
    status_code=status.HTTP_200_OK,
)
async def get_processing_progress(
    ids: str = Query("", max_length=MAX_PROGRESS_IDS * 20),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Return processing progress for the caller's documents being processed.

    Accepts a comma-separated list of at most ``MAX_PROGRESS_IDS`` document
    IDs (more is HTTP 422) and returns ``document_id → percent (0-99)`` for
    those the caller owns, with the same scoping as the list endpoint
    (``admin`` sees every document). Ids of other users' documents, unknown
    ids and malformed entries (including values beyond the 32-bit id range
    of ``documents.id``) are left out.
    """
    from ..services.document_progress import get_progress

    parts = [p.strip() for p in ids.split(",") if p.strip()]
    if len(parts) > MAX_PROGRESS_IDS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"At most {MAX_PROGRESS_IDS} document ids per request.",
        )
    requested = {
        int(p)
        for p in parts
        if _PROGRESS_ID_RE.fullmatch(p) and int(p) <= MAX_DOCUMENT_ID
    }
    if not requested:
        return {}
    owned = await db.execute(
        _scope_to_owner(select(Document.id).where(Document.id.in_(requested)), user)
    )
    return {did: get_progress(did) for did in sorted(owned.scalars().all())}


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
    current = await current_policy_snapshot(db)
    return DocumentListResponse(
        items=[_document_response(d, current) for d in docs]
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
    return await _single_document_response(db, document)


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
    return await _single_document_response(db, document)


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
    """Delete a document, its chunks, encrypted file, indexes and map.

    Every stored copy is erased before the record goes: if any of them
    cannot be removed the record stays and 500 is returned, so the delete
    can be retried. Each step treats a missing file as already erased,
    which makes a retry after a partial failure succeed.

    A document still being ingested is refused with 409: the pipeline
    would otherwise write its map and indexes after the erasure. The
    erasure and the row delete run under the document's map lock, so a
    concurrent map edit either finishes before the erasure or finds the
    row gone and writes nothing. A concurrent delete that removed the row
    first yields 404; a map lock found lost before the commit aborts with
    500 and keeps the record, and one that expires on release after the
    delete was committed is logged and the delete still succeeds.
    """
    document = await get_owned_or_404(
        db, Document, document_id, user, "Document not found."
    )
    _refuse_while_processing(document)

    committed = False
    try:
        async with document_map_lock(document_id) as lock:
            try:
                await db.refresh(document)
            except InvalidRequestError:
                # A concurrent delete removed the row while this request
                # waited for the lock.
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Document not found.",
                ) from None
            _refuse_while_processing(document)
            await _erase_document_locked(db, document, lock)
            committed = True
    except DocumentMapLockLost:
        if not committed:
            raise
        # The erasure and the row delete are already committed; the lock
        # expiring on release cannot undo them.
        logger.warning(
            "Map lock of document_id=%s expired after a committed delete",
            document_id,
        )


async def _erase_document_locked(
    db: AsyncSession, document: Document, lock: DocumentMapLockHandle
) -> None:
    """Erase every stored copy of ``document``, then delete and commit its row.

    Caller holds the document's map lock. Raises HTTP 500 if a stored copy
    survives, or if the lock is no longer held right before the commit —
    a writer that took it over could store a map the erasure never saw.
    Either way the row stays in place so the delete can be retried.
    """
    document_id = document.id

    try:
        await erase_document_artifacts(document_id, source_path=document.encrypted_path)
    except ArtifactErasureError as exc:
        error = exc.error
        if not isinstance(error, DocumentMapErasureError):
            logger.error(
                "Could not erase document_id=%s tier=%s error=%s errno=%s",
                document_id,
                exc.tier,
                type(error).__name__,
                getattr(error, "errno", None),
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The document's stored data could not be fully erased; "
                "the document was not deleted. Try again."
            ),
        ) from error

    from sqlalchemy import delete as sa_delete
    await db.execute(
        sa_delete(AuditEvent).where(AuditEvent.document_id == document_id)
    )
    await db.delete(document)
    try:
        await lock.ensure_held()
    except DocumentMapLockLost:
        await db.rollback()
        logger.error(
            "Map lock of document_id=%s lost before the delete was committed",
            document_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The document could not be deleted because a concurrent "
                "update interfered. Try again."
            ),
        ) from None
    await db.commit()


def _refuse_while_processing(document: Document) -> None:
    """Raise 409 if the document is still being ingested."""
    if document.ingestion_status == "processing":
        raise _processing_conflict()


def _processing_conflict() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="The document is still being processed. Try again when it has finished.",
    )


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
    """Re-run the sanitization, chunking, and indexing pipeline for an existing document.

    A document still being ingested is refused with 409, like delete; the
    status is claimed with a conditional update so two concurrent requests
    cannot both start a pipeline run.
    """
    document = await get_owned_or_404(
        db, Document, document_id, user, "Document not found."
    )
    _refuse_while_processing(document)

    encrypted_path = Path(document.encrypted_path)
    if not encrypted_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Encrypted source file not found on disk.",
        )

    claimed = await db.execute(
        update(Document)
        .where(Document.id == document_id, Document.ingestion_status != "processing")
        .values(
            ingestion_status="processing",
            ingestion_error=None,
            ingestion_error_code=None,
            chunk_count=0,
            entity_count=0,
        )
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount == 0:
        await db.rollback()
        raise _processing_conflict()

    from sqlalchemy import delete as sa_delete

    await db.execute(
        sa_delete(EntityDetection).where(EntityDetection.document_id == document_id)
    )
    await db.execute(
        sa_delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
    )
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

                bg_doc.detected_language = await asyncio.to_thread(
                    _detect_document_language, file_format, ingestion_result
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
                await _record_background_failure(bg_db, doc_id, bg_exc, user.id)
            finally:
                clear_progress(doc_id)

    background_tasks.add_task(_reprocess_background, document.id)

    return _document_response(document, None)

