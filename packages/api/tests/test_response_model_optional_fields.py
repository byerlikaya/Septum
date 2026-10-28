"""Optional response fields default to ``None`` when the source omits them."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import BaseModel

from septum_api.routers.audit import AuditEventResponse
from septum_api.routers.chunks import ChunkResponse
from septum_api.routers.documents import DocumentResponse
from septum_api.routers.error_logs import ErrorLogDetailOut, ErrorLogOut

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)

_ERROR_LOG_REQUIRED = {
    "id": 1,
    "created_at": _NOW,
    "source": "backend",
    "level": "ERROR",
    "message": "boom",
}

_CASES: list[tuple[type[BaseModel], dict, tuple[str, ...]]] = [
    (
        AuditEventResponse,
        {
            "id": 1,
            "created_at": _NOW,
            "event_type": "pii_detected",
            "regulation_ids": [],
            "entity_types_detected": {},
            "entity_count": 0,
        },
        ("session_id", "document_id", "extra"),
    ),
    (
        ChunkResponse,
        {"id": 1, "document_id": 1, "index": 0, "sanitized_text": "x", "char_count": 1},
        (
            "source_page",
            "source_slide",
            "source_sheet",
            "source_timestamp_start",
            "source_timestamp_end",
            "section_title",
        ),
    ),
    (
        DocumentResponse,
        {
            "id": 1,
            "filename": "f",
            "original_filename": "f.pdf",
            "file_type": "application/pdf",
            "file_format": "pdf",
            "detected_language": "en",
            "uploaded_at": _NOW,
            "encrypted_path": "/x",
            "chunk_count": 0,
            "entity_count": 0,
            "ingestion_status": "completed",
            "file_size_bytes": 1,
            "active_regulation_ids": [],
        },
        ("language_override", "ingestion_error", "transcription_text", "ocr_confidence"),
    ),
    (
        ErrorLogOut,
        _ERROR_LOG_REQUIRED,
        ("exception_type", "path", "method", "status_code", "user_agent"),
    ),
    (ErrorLogDetailOut, _ERROR_LOG_REQUIRED, ("stack_trace", "extra")),
]


@pytest.mark.parametrize(
    ("model", "required", "field"),
    [(model, required, field) for model, required, fields in _CASES for field in fields],
    ids=lambda v: v.__name__ if isinstance(v, type) else v if isinstance(v, str) else "",
)
def test_optional_field_defaults_to_none(
    model: type[BaseModel], required: dict, field: str
) -> None:
    assert field not in required
    assert getattr(model.model_validate(required), field) is None
