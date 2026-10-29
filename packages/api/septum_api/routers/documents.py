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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Dict, List, Optional, Sequence, Tuple

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
from pydantic import BaseModel, ConfigDict, Field, field_validator
from septum_core.model_integrity import ModelRefusal
from sqlalchemy import Select, func, select, update
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import IntegrityError, InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer, selectinload
from sqlalchemy.orm.attributes import set_committed_value

from ..database import get_db
from ..errors import public_error_text
from ..middleware.upload_limits import MAX_UPLOAD_BYTES, TOO_LARGE_DETAIL
from ..models.audit_event import AuditEvent
from ..models.document import Chunk as DocumentChunk
from ..models.document import Document
from ..models.entity_detection import EntityDetection
from ..models.regulation import RegulationRuleset
from ..models.settings import AppSettings
from ..models.spreadsheet_schema import (
    SpreadsheetColumn,
    SpreadsheetSchema,
    generated_technical_label,
)
from ..models.user import User
from ..request_limits import (
    MAX_DOCUMENT_ID,
    MAX_PROGRESS_IDS,
    MAX_SCHEMA_LABEL_CHARS,
    MAX_SCHEMA_LABELED_COLUMNS,
    MAX_SHEET_COLUMNS,
    MAX_TECHNICAL_LABEL_CHARS,
)
from ..services.audit_logger import (
    DOCUMENT_LANGUAGE_CHANGED,
    OVERRIDE_REFUSAL_REASONS,
    language_override_event,
)
from ..services.document_anon_store import (
    DocumentMapLockHandle,
    DocumentMapLockLost,
    document_map_lock,
)
from ..services.document_pipeline import (
    ArtifactErasureError,
    DocumentPipeline,
    erase_document_artifacts,
    log_artifact_erasure_failure,
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
from ..services.source_files import SOURCE_FILE_DIR, new_source_file_name
from ..utils.auth_dependency import get_current_user, require_role
from ..utils.crypto import decrypt, encrypt_buffer, new_encrypt_buffer, plaintext_region
from ..utils.db_helpers import (
    detect_language,
    get_owned_or_404,
    load_settings,
    override_fits_text,
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

_DOCUMENT_NOT_FOUND = "Document not found."
_AUDIO_MPEG_MIME = "audio/mpeg"

router = APIRouter(prefix="/api/documents", tags=["documents"])


_DOC_STORAGE_DIR = SOURCE_FILE_DIR


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
    _AUDIO_MPEG_MIME: "audio",
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
    language_override: Optional[str] = None
    uploaded_at: datetime
    encrypted_path: str
    chunk_count: int
    entity_count: int
    ingestion_status: str
    ingestion_error: Optional[str] = None
    ingestion_error_code: Optional[str] = None
    file_size_bytes: int
    transcription_text: Optional[str] = None
    ocr_confidence: Optional[float] = None
    active_regulation_ids: List[str]
    needs_reprocessing: bool = False
    missing_entity_types: List[str] = []
    sanitized_policy_known: bool = True
    sanitized_language: Optional[str] = None
    override_refused_language: Optional[str] = None
    override_refused_reason: Optional[str] = None


async def _override_refusals(
    db: AsyncSession, documents: Sequence[int] | Select
) -> Dict[int, Tuple[str, str]]:
    """Per document, the override the pipeline last refused and the reason code.

    Reported only while the document's latest override event is that refusal.
    ``documents`` is a list of ids or a select of ``Document.id`` (the
    list endpoint passes its owner-scoped query, so no id is bound per
    document). One query picks the latest ``document_language_changed``
    event per document in SQL. Derived from the audit trail, so no column
    is needed: a later ``PATCH`` writes a newer event and the refusal stops
    being reported. Only reason codes in ``OVERRIDE_REFUSAL_REASONS`` are
    reported.
    """
    if not isinstance(documents, Select) and not documents:
        return {}
    latest = (
        select(func.max(AuditEvent.id).label("id"))
        .where(
            AuditEvent.event_type == DOCUMENT_LANGUAGE_CHANGED,
            AuditEvent.document_id.in_(documents),
        )
        .group_by(AuditEvent.document_id)
        .subquery()
    )
    rows = await db.execute(
        select(AuditEvent.document_id, AuditEvent.extra).join(
            latest, AuditEvent.id == latest.c.id
        )
    )
    refusals: Dict[int, Tuple[str, str]] = {}
    for document_id, extra in rows.all():
        if not isinstance(extra, dict):
            continue
        reason = extra.get("reason")
        previous = extra.get("previous_override")
        if not isinstance(reason, str) or not isinstance(previous, str):
            continue
        refused = supported_language_code(previous)
        if reason in OVERRIDE_REFUSAL_REASONS and refused:
            refusals[document_id] = (refused, reason)
    return refusals


def _document_response(
    document: Document,
    current: Optional[PolicySnapshot],
    refusal: Optional[Tuple[str, str]] = None,
) -> DocumentResponse:
    """Serialize ``document`` with its staleness against the ``current`` policy.

    ``current`` may be ``None`` only for a document that is not completed
    (just uploaded or claimed for reprocessing). ``refusal`` is the
    ``(language, reason)`` of an override the last run refused.
    """
    staleness = document_staleness(document, current)
    refused_language, refused_reason = refusal or (None, None)
    return DocumentResponse.model_validate(document).model_copy(
        update={
            "needs_reprocessing": staleness.needs_reprocessing,
            "missing_entity_types": staleness.missing_entity_types,
            "sanitized_policy_known": staleness.sanitized_policy_known,
            "override_refused_language": refused_language,
            "override_refused_reason": refused_reason,
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
    refusals = await _override_refusals(db, [document.id])
    return _document_response(document, current, refusals.get(document.id))


class DocumentListResponse(BaseModel):
    """Wrapper for listing multiple documents."""

    items: List[DocumentResponse]


class LanguageUpdatePayload(BaseModel):
    """Request body for overriding a document's language.

    Accepts a BCP 47-style tag (``tr``, ``zh-CN``, ``pt_BR``) for a
    language detection supports; the stored override is its ISO 639 base
    code.
    """

    language: str = Field(
        max_length=35, pattern=r"^[A-Za-z]{2,3}([-_][A-Za-z0-9]{2,8})*$"
    )

    @field_validator("language")
    @classmethod
    def _supported(cls, value: str) -> str:
        """Reject languages detection and masking do not support; return the base code."""
        code = supported_language_code(value)
        if code is None:
            raise ValueError("language not supported")
        return code


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


class SpreadsheetColumnUpdate(BaseModel):
    """A column mapping a client may store; bounds are enforced before any gate work.

    Kept apart from :class:`SpreadsheetColumnPayload` so the response model
    still renders labels stored before these bounds existed.
    """

    index: int = Field(ge=0, le=MAX_SHEET_COLUMNS - 1)
    technical_label: str = Field(max_length=MAX_TECHNICAL_LABEL_CHARS)
    semantic_label: Optional[str] = Field(
        default=None, max_length=MAX_SCHEMA_LABEL_CHARS
    )
    is_numeric: Optional[bool] = None


class SpreadsheetSchemaUpdatePayload(BaseModel):
    """Request payload for updating a spreadsheet schema.

    The document id comes from the path parameter; the body only carries the
    per-column mappings that should be applied. Each column index may appear
    once per payload.
    """

    columns: List[SpreadsheetColumnUpdate]

    @field_validator("columns")
    @classmethod
    def _bounded_and_unique(
        cls, columns: List[SpreadsheetColumnUpdate]
    ) -> List[SpreadsheetColumnUpdate]:
        """Reject duplicate indexes and more labelled columns than the gate budget allows."""
        indexes = [column.index for column in columns]
        if len(set(indexes)) != len(indexes):
            raise ValueError("column indexes must be unique")
        labelled = sum(1 for column in columns if column.semantic_label)
        if labelled > MAX_SCHEMA_LABELED_COLUMNS:
            raise ValueError(
                f"at most {MAX_SCHEMA_LABELED_COLUMNS} columns may carry a semantic label"
            )
        return columns


@router.get(
    "/{document_id}/schema",
    status_code=status.HTTP_200_OK,
)
async def get_spreadsheet_schema(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SpreadsheetSchemaResponse:
    """Return the spreadsheet schema for a document, if any."""

    await get_owned_or_404(db, Document, document_id, user, _DOCUMENT_NOT_FOUND)

    schema = await _read_schema(db, document_id)
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


async def _read_schema(
    db: AsyncSession, document_id: int, *, required: bool = False
) -> Optional[SpreadsheetSchema]:
    """Return the document's schema and columns, re-read from the database.

    ``populate_existing`` replaces objects already in the session, so a
    re-read after the PII gate sees writes committed while the gate ran.
    ``required`` makes a missing schema an error instead of ``None``.
    """
    result = await db.execute(
        select(SpreadsheetSchema)
        .options(selectinload(SpreadsheetSchema.columns))
        .where(SpreadsheetSchema.document_id == document_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one() if required else result.scalar_one_or_none()


async def _lock_and_read_schema(
    db: AsyncSession, document_id: int, *, required: bool = False
) -> Optional[SpreadsheetSchema]:
    """Take the schema row's write lock, then re-read the schema.

    The no-op write makes concurrent updates of an existing schema
    serialise. A schema that does not exist yet has no row to lock; its
    first creation is guarded by the unique index on ``document_id``.
    """
    await db.execute(
        update(SpreadsheetSchema)
        .where(SpreadsheetSchema.document_id == document_id)
        .values(updated_at=datetime.now(timezone.utc))
    )
    return await _read_schema(db, document_id, required=required)


_SCHEMA_UNIQUE_INDEX = "uq_spreadsheet_schemas_document_id"
_UNIQUE_VIOLATION_SQLSTATE = "23505"
# SQLite names the indexed column, not the index, and has no constraint field.
_SQLITE_SCHEMA_CONFLICT = "UNIQUE constraint failed: spreadsheet_schemas.document_id"


def _is_schema_conflict(exc: IntegrityError) -> bool:
    """Return whether ``exc`` is a second schema row for one document.

    PostgreSQL reports the SQLSTATE and the violated index on the driver
    error; SQLite only in its message.
    """
    sqlstate = getattr(exc.orig, "sqlstate", None)
    if sqlstate is not None:
        constraint = getattr(exc.orig.__cause__, "constraint_name", None)
        return sqlstate == _UNIQUE_VIOLATION_SQLSTATE and constraint == _SCHEMA_UNIQUE_INDEX
    return _SQLITE_SCHEMA_CONFLICT in str(exc.orig)


async def _insert_schema_unless_raced(
    db: AsyncSession, schema: SpreadsheetSchema
) -> bool:
    """Insert ``schema`` (and its columns) inside a savepoint.

    Returns ``False`` when a concurrent writer committed the document's
    schema first: the savepoint is rolled back and the caller re-reads the
    winner. Any other integrity error is raised.
    """
    try:
        async with db.begin_nested():
            db.add(schema)
            await db.flush()
    except IntegrityError as exc:
        if not _is_schema_conflict(exc):
            raise
        return False
    return True


def _resulting_semantic_label(
    existing: SpreadsheetColumn, update: Optional[SpreadsheetColumnUpdate]
) -> Optional[str]:
    """Return the semantic label a column holds after an update.

    A technical label that differs from the stored one is never applied, so
    the stored label stays (the update loop skips such a column).
    """
    if update is None or update.technical_label != existing.technical_label:
        return existing.semantic_label
    return update.semantic_label


def _check_update_fits_schema(
    payload: SpreadsheetSchemaUpdatePayload,
    columns_by_index: Dict[int, SpreadsheetColumn],
) -> None:
    """Refuse an update that adds columns or leaves too many labelled columns.

    Runs before the PII gate, so an over-limit update costs no NER work. A
    column whose technical label differs from the payload keeps its stored
    label, as the update loop does.
    """
    payload_by_index = {col.index: col for col in payload.columns}
    if any(index not in columns_by_index for index in payload_by_index):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Column index is not part of the document's spreadsheet.",
        )
    labelled_after_update = sum(
        1
        for index, existing in columns_by_index.items()
        if _resulting_semantic_label(existing, payload_by_index.get(index))
    )
    if labelled_after_update > MAX_SCHEMA_LABELED_COLUMNS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"At most {MAX_SCHEMA_LABELED_COLUMNS} columns may carry a "
                "semantic label."
            ),
        )


async def _schema_for_update(
    db: AsyncSession, document_id: int, payload: SpreadsheetSchemaUpdatePayload
) -> Tuple[SpreadsheetSchema, Dict[int, SpreadsheetColumn]]:
    """Return the schema an update writes to, with its columns by index.

    A stored schema is locked, re-read and the update checked against it.
    Without one the update creates it, with no columns yet; if a concurrent
    writer created it first, that schema is locked, re-read and checked
    instead, as if this update had arrived after it.
    """
    schema = await _lock_and_read_schema(db, document_id)
    if schema is None:
        created = SpreadsheetSchema(document_id=document_id)
        if await _insert_schema_unless_raced(db, created):
            return created, {}
        schema = await _lock_and_read_schema(db, document_id, required=True)
    columns_by_index = {col.index: col for col in schema.columns}
    _check_update_fits_schema(payload, columns_by_index)
    return schema, columns_by_index


@router.put(
    "/{document_id}/schema",
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
    Once a schema exists the update may only touch its existing columns, and
    may not leave more labelled columns than ``MAX_SCHEMA_LABELED_COLUMNS``;
    both are refused with HTTP 422 before the gate runs. A first create that
    loses to a concurrent one is applied to the winner's schema under the
    same rules, as if it had arrived after it.
    """

    document = await get_owned_or_404(
        db, Document, document_id, user, _DOCUMENT_NOT_FOUND
    )
    schema = await _read_schema(db, document_id)
    if schema is not None:
        _check_update_fits_schema(
            payload, {col.index: col for col in schema.columns}
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

    schema, columns_by_index = await _schema_for_update(db, document_id, payload)

    for col_payload in payload.columns:
        existing = columns_by_index.get(col_payload.index)
        if existing is None:
            new_col = SpreadsheetColumn(
                schema_id=schema.id,
                index=col_payload.index,
                technical_label=generated_technical_label(col_payload.index),
                semantic_label=col_payload.semantic_label,
                is_numeric=col_payload.is_numeric,
            )
            db.add(new_col)
            continue

        existing.semantic_label = _resulting_semantic_label(existing, col_payload)
        # A mismatched technical label leaves the column untouched.
        if existing.technical_label == col_payload.technical_label:
            existing.is_numeric = col_payload.is_numeric

    await db.commit()

    # Reload and return the updated schema.
    schema = await _read_schema(db, document_id, required=True)
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
        return _AUDIO_MPEG_MIME
    if len(raw_bytes) > 2 and raw_bytes[0] == 0xFF and (raw_bytes[1] & 0xE0) == 0xE0:
        return _AUDIO_MPEG_MIME
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
    labels (e.g., ``COLUMN_1``) without storing any raw header text. A schema
    a concurrent update created first is kept, as if it had existed already.
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
            technical_label=generated_technical_label(idx),
            semantic_label=None,
            is_numeric=None,
        )
        for idx in range(max_columns)
    ]
    await _insert_schema_unless_raced(db, schema)
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


_MIME_SNIFF_BYTES = 7 * 1024 * 1024
"""Bytes from the start of an upload handed to MIME detection (7 MiB).

This equals libmagic's default ``bytes_max`` (7,340,032), the most it reads
of any buffer, and the structural fallback checks only look at the first few
thousand bytes, so typing the head gives the same answer as typing the whole
file."""


def _seal_upload(
    source: BinaryIO, fallback_mime_type: Optional[str], destination: Path
) -> Tuple[str, str, int]:
    """Type, encrypt in place and store an upload held in memory.

    Returns (MIME type, file format, size). Runs in a worker thread. The
    spooled upload is closed as soon as it has been copied, so peak memory
    is about twice the file size. Nothing is written unless the type is
    supported, and a failed write leaves no partial file behind.
    """
    size = source.seek(0, os.SEEK_END)
    if size > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=TOO_LARGE_DETAIL,
        )
    if size == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )
    source.seek(0)
    buffer = new_encrypt_buffer(size)
    region = plaintext_region(buffer)
    filled = 0
    while filled < size:
        read = source.readinto(region[filled:])
        if not read:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded file ended early.",
            )
        filled += read
    source.close()

    mime_type = _detect_mime_type(bytes(region[:_MIME_SNIFF_BYTES]), fallback_mime_type)
    file_format = _mime_to_format(mime_type)
    region.release()
    sealed = encrypt_buffer(buffer)
    try:
        destination.write_bytes(sealed)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return mime_type, file_format, size


async def _seal_upload_in_thread(
    source: BinaryIO, fallback_mime_type: Optional[str], destination: Path
) -> Tuple[str, str, int]:
    """Run :func:`_seal_upload` in a worker thread without leaking a file on cancellation.

    A cancelled request cannot stop the thread, so the thread is waited for
    and anything it wrote is removed before the cancellation propagates.
    """
    task = asyncio.ensure_future(
        asyncio.to_thread(_seal_upload, source, fallback_mime_type, destination)
    )
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.wait({task})
        destination.unlink(missing_ok=True)
        raise


@router.post(
    "/upload",
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin", "editor")),
) -> DocumentResponse:
    """Upload a document, ingest it, and build its vector index.

    The raw file, already held in RAM by the multipart parser, is:
    * Copied once into a buffer that is encrypted in place with AES-256-GCM,
      so plaintext and ciphertext never coexist as separate full copies.
    * Typed via python-magic from its first 7 MiB before encryption; an
      unsupported type is refused before anything is written.
    * Written to disk encrypted, then passed through the ingestion →
      sanitize → chunk → embed pipeline.

    The file is capped at ``MAX_UPLOAD_BYTES`` (default 500 MB); a larger
    one is refused with HTTP 413 before anything is copied.
    """
    encrypted_path = _DOC_STORAGE_DIR / new_source_file_name()
    mime_type, file_format, file_size = await _seal_upload_in_thread(
        file.file, getattr(file, "content_type", None), encrypted_path
    )

    try:
        active_reg_ids = await _snapshot_active_regulations(db)

        document = Document(
            filename=str(encrypted_path.name),
            original_filename=file.filename or "",
            file_type=mime_type,
            file_format=file_format,
            detected_language="en",
            language_override=None,
            encrypted_path=str(encrypted_path),
            file_size_bytes=file_size,
            transcription_text=None,
            ocr_confidence=0.0,
            active_regulation_ids=active_reg_ids,
            ingestion_status="processing",
            ingestion_error=None,
            user_id=current_user.id,
        )
        db.add(document)
        await db.commit()
    except BaseException:
        # No row will ever point at the file, so it must not outlive the failure.
        try:
            encrypted_path.unlink(missing_ok=True)
        except OSError as unlink_exc:
            logger.error(
                "Could not remove the source file of a failed upload: %s",
                type(unlink_exc).__name__,
            )
        raise
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


_PROGRESS_ID_RE = re.compile(r"\d{1,10}", re.ASCII)


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
    refusals = await _override_refusals(db, _scope_to_owner(select(Document.id), user))
    return DocumentListResponse(
        items=[_document_response(d, current, refusals.get(d.id)) for d in docs]
    )


@router.get(
    "/{document_id}",
    status_code=status.HTTP_200_OK,
)
async def get_document(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DocumentResponse:
    """Return metadata for a single document."""
    document = await get_owned_or_404(
        db, Document, document_id, user, _DOCUMENT_NOT_FOUND
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

    await get_owned_or_404(db, Document, document_id, user, _DOCUMENT_NOT_FOUND)
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
    await get_owned_or_404(db, Document, document_id, user, _DOCUMENT_NOT_FOUND)

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
        db, Document, document_id, user, _DOCUMENT_NOT_FOUND
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


_DOCUMENT_COLUMNS_BUT_TEXT = [
    column.key
    for column in sa_inspect(Document).column_attrs
    if column.key != "transcription_text"
]


async def _require_acting_user(db: AsyncSession, user: User) -> None:
    """Share-lock the acting user's row, or roll back with 401 if it is gone."""
    acting = await db.execute(
        select(User.id).where(User.id == user.id).with_for_update(read=True)
    )
    if acting.scalar_one_or_none() is None:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive"
        )


@router.patch(
    "/{document_id}/language",
    status_code=status.HTTP_200_OK,
)
async def update_document_language(
    document_id: int,
    payload: LanguageUpdatePayload,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin", "editor")),
) -> DocumentResponse:
    """Override the detected language for a document.

    The language is checked against the document's extracted text: one its
    script contradicts is refused with 422. A document still being
    processed has no text yet; its override is accepted and the pipeline
    applies the same check (and clears an override it refuses). The
    override takes effect through reprocessing or chat re-masking, since
    staleness compares it with the language the document was masked in.
    The change is audited (user id and the two codes) in the same
    transaction, and the acting user is re-read under a lock so a user
    deleted meanwhile cannot leave an audit row naming it.
    """
    # The text is read once, after the flush below; the lookup skips it.
    document = await get_owned_or_404(
        db, Document, document_id, user, _DOCUMENT_NOT_FOUND,
        options=(defer(Document.transcription_text),),
    )
    # The acting user is locked before the document row: ``delete_user``
    # deletes the user and its ``ON DELETE CASCADE`` then locks documents,
    # so taking them in the same order rules out a deadlock on PostgreSQL.
    await _require_acting_user(db, user)
    previous_override = supported_language_code(document.language_override)
    document.language_override = payload.language
    db.add(language_override_event(document.id, user.id, previous_override, payload.language))
    await db.flush()
    # The flush's UPDATE holds the document row's write lock (PostgreSQL) or
    # the database write lock (SQLite), so no further write can land until
    # commit, and this plain read sees any text committed before the lock:
    # an override is never stored unchecked, and no second lock is needed.
    text = (
        await db.execute(
            select(Document.transcription_text).where(Document.id == document.id)
        )
    ).scalar_one_or_none()
    if text and not override_fits_text(payload.language, text):
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="language does not match the document's script",
        )
    # Re-checked under the write lock: SQLite has no row locks, so the check
    # above did not hold the user; on PostgreSQL the share lock is still held.
    await _require_acting_user(db, user)
    await db.commit()
    await db.refresh(document, attribute_names=_DOCUMENT_COLUMNS_BUT_TEXT)
    set_committed_value(document, "transcription_text", text)
    return await _single_document_response(db, document)


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
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
        db, Document, document_id, user, _DOCUMENT_NOT_FOUND
    )
    refuse_while_processing(document)
    if not await erase_document(db, document):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_DOCUMENT_NOT_FOUND,
        )


class _DocumentGone(Exception):
    """A concurrent delete removed the row while the map lock was awaited."""


async def erase_document(db: AsyncSession, document: Document) -> bool:
    """Erase every stored copy of ``document`` under its map lock, then delete its row.

    Returns ``False`` when a concurrent delete removed the row while this
    call waited for the lock, ``True`` once the erasure and the row delete
    are committed. Raises 409 if the document is (again) being processed
    and 500 if a stored copy survives or the lock was lost before the
    commit; in both cases the row stays so the delete can be retried. A
    lock that expires on release after the commit is logged only.
    """
    document_id = document.id
    committed = False
    try:
        async with document_map_lock(document_id) as lock:
            try:
                await db.refresh(document)
            except InvalidRequestError:
                # Raised inside the lock so a lock lost meanwhile is only
                # logged, as for any other failed body.
                raise _DocumentGone from None
            refuse_while_processing(document)
            await _erase_document_locked(db, document, lock)
            committed = True
    except _DocumentGone:
        return False
    except DocumentMapLockLost:
        if not committed:
            raise
        # The erasure and the row delete are already committed; the lock
        # expiring on release cannot undo them.
        logger.warning(
            "Map lock of document_id=%s expired after a committed delete",
            document_id,
        )
    return True


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
        log_artifact_erasure_failure(document_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The document's stored data could not be fully erased; "
                "the document was not deleted. Try again."
            ),
        ) from exc.error

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


def refuse_while_processing(document: Document) -> None:
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
        db, Document, document_id, user, _DOCUMENT_NOT_FOUND
    )
    refuse_while_processing(document)

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

