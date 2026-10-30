"""Characterization tests for document upload and MIME detection.

Pins the observable behaviour of ``_detect_mime_type`` (every signature,
the libmagic preference rules, the fallback and the 400 refusal) and of
``upload_document`` (the persisted row, the source-file cleanup on a
failed insert, and every branch of the background ingestion task) so the
code can be restructured without changing what an upload does. Ingestion,
the pipeline and the database are replaced by in-memory fakes.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import BackgroundTasks, HTTPException
from septum_api import database as database_module
from septum_api.models.document import Document
from septum_api.routers import documents
from septum_api.services import document_progress

# ---------------------------------------------------------------- _detect_mime_type


class _Magic:
    def __init__(self, result: Any = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[bytes, bool]] = []

    def from_buffer(self, raw: bytes, mime: bool = False) -> Any:
        self.calls.append((raw, mime))
        if self.error is not None:
            raise self.error
        return self.result


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (b"%PDF-1.7 body", "application/pdf"),
        (b"\x89PNG\r\n\x1a\nrest", "image/png"),
        (b"\xff\xd8\xff\xe0", "image/jpeg"),
        (b"RIFF\x00\x00\x00\x00WAVEfmt ", "audio/wav"),
        (b"OggS\x00\x02", "audio/ogg"),
        (b"fLaC\x00\x00", "audio/flac"),
        (b"ID3\x04\x00", "audio/mpeg"),
        (b"\xff\xfb\x90\x00", "audio/mpeg"),
        (b"\xff\xe3\x00", "audio/mpeg"),
        (b"\x00\x00\x00\x20ftypM4A \x00", "audio/mp4"),
        (b"PK\x03\x04mimetypeapplication/vnd.oasis.opendocument.spreadsheet", "application/vnd.oasis.opendocument.spreadsheet"),
    ],
)
def test_structural_signatures_without_magic(monkeypatch, raw, expected):
    monkeypatch.setattr(documents, "_magic", None)
    assert documents._detect_mime_type(raw, "text/plain") == expected


@pytest.mark.parametrize(
    ("raw", "fallback", "expected"),
    [
        (b"RIFF\x00\x00\x00\x00AVI LIST", "  video/x-msvideo  ", "video/x-msvideo"),
        (b"\xff\xe0", "text/csv", "text/csv"),
        (b"\xff\x10\x00", "text/csv", "text/csv"),
        (b"\x00\x00\x00\x20ftypM4A ", "text/csv", "text/csv"),
        (b"PK\x03\x04no marker", "application/zip", "application/zip"),
        (b"plain text", "text/plain", "text/plain"),
    ],
)
def test_unmatched_bytes_use_trimmed_fallback(monkeypatch, raw, fallback, expected):
    monkeypatch.setattr(documents, "_magic", None)
    assert documents._detect_mime_type(raw, fallback) == expected


@pytest.mark.parametrize("fallback", [None, "", "   "])
def test_unrecognised_without_fallback_is_refused(monkeypatch, fallback):
    monkeypatch.setattr(documents, "_magic", None)
    with pytest.raises(HTTPException) as info:
        documents._detect_mime_type(b"plain text", fallback)
    assert info.value.status_code == 400
    assert info.value.detail == "Unsupported or unrecognized file type."


def test_ods_marker_beyond_sniff_window_is_ignored(monkeypatch):
    monkeypatch.setattr(documents, "_magic", None)
    raw = b"PK" + b"\x00" * 4000 + b"vnd.oasis.opendocument.spreadsheet"
    assert documents._detect_mime_type(raw, "application/zip") == "application/zip"


def test_magic_answer_wins(monkeypatch):
    magic = _Magic("text/csv")
    monkeypatch.setattr(documents, "_magic", magic)
    assert documents._detect_mime_type(b"%PDF-1.7", "image/png") == "text/csv"
    assert magic.calls == [(b"%PDF-1.7", True)]


@pytest.mark.parametrize("answer", ["application/octet-stream", "application/zip", "", None, 42])
def test_low_confidence_magic_answer_falls_through(monkeypatch, answer):
    monkeypatch.setattr(documents, "_magic", _Magic(answer))
    assert documents._detect_mime_type(b"%PDF-1.7", None) == "application/pdf"


def test_magic_failure_is_logged_by_type_and_falls_through(monkeypatch, caplog):
    monkeypatch.setattr(documents, "_magic", _Magic(error=ValueError("secret detail")))
    with caplog.at_level(logging.DEBUG, logger=documents.logger.name):
        assert documents._detect_mime_type(b"OggS", None) == "audio/ogg"
    messages = [r.getMessage() for r in caplog.records if r.name == documents.logger.name]
    assert messages == ["Content-based MIME detection failed, using structural checks: ValueError"]


# ---------------------------------------------------------------- upload_document


class _UploadDb:
    """Request session: records added rows, assigns an id on refresh."""

    def __init__(self, fail_commit: BaseException | None = None) -> None:
        self.fail_commit = fail_commit
        self.added: list[Any] = []
        self.commits = 0
        self.refreshed: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commits += 1
        if self.fail_commit is not None:
            raise self.fail_commit

    async def refresh(self, obj: Any) -> None:
        obj.id = 7
        self.refreshed.append(obj)


class _BackgroundDb:
    """Background session: serves one document and counts commits."""

    def __init__(self, document: Any) -> None:
        self.document = document
        self.commits = 0
        self.gets: list[tuple[Any, int]] = []
        self.snapshots: list[dict[str, Any]] = []

    async def get(self, model: Any, doc_id: int) -> Any:
        self.gets.append((model, doc_id))
        return self.document

    async def commit(self) -> None:
        self.commits += 1
        if self.document is not None:
            self.snapshots.append(dict(vars(self.document)))

    async def __aenter__(self) -> "_BackgroundDb":
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        return None


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple] = []


@pytest.fixture
def upload_env(monkeypatch, tmp_path):
    rec = _Recorder()
    encrypted = tmp_path / "sealed.bin"
    state: dict[str, Any] = {
        "seal": ("application/pdf", "pdf", 1234),
        "text": "extracted text",
        "warnings": [],
        "metadata": {"sheets": [{"column_count": 2}]},
        "pipeline_error": None,
        "bg_doc": SimpleNamespace(id=7),
    }

    monkeypatch.setattr(documents, "new_source_file_name", lambda: "sealed.bin")
    monkeypatch.setattr(documents, "_DOC_STORAGE_DIR", tmp_path)

    async def fake_seal(source, fallback, destination):
        rec.events.append(("seal", source, fallback, destination))
        destination.write_bytes(b"cipher")
        return state["seal"]

    async def fake_snapshot(db):
        rec.events.append(("snapshot",))
        return ["gdpr", "kvkk"]

    monkeypatch.setattr(documents, "_seal_upload_in_thread", fake_seal)
    monkeypatch.setattr(documents, "_snapshot_active_regulations", fake_snapshot)
    monkeypatch.setattr(
        documents, "_document_response", lambda doc, current: ("response", doc, current)
    )

    bg_db_holder: dict[str, _BackgroundDb] = {}

    def session_maker_factory():
        def make():
            bg = _BackgroundDb(state["bg_doc"])
            bg_db_holder["db"] = bg
            return bg

        return make

    monkeypatch.setattr(database_module, "get_session_maker", session_maker_factory)

    async def fake_load_settings(db):
        rec.events.append(("load_settings",))
        return "SETTINGS"

    monkeypatch.setattr(documents, "load_settings", fake_load_settings)
    monkeypatch.setattr(
        documents,
        "_build_ingester_kwargs",
        lambda fmt, settings: rec.events.append(("kwargs", fmt, settings)) or {"k": fmt},
    )

    class _Router:
        async def ingest(self, **kwargs):
            rec.events.append(("ingest", kwargs))
            return SimpleNamespace(
                text=state["text"],
                confidence=0.75,
                metadata=state["metadata"],
                warnings=state["warnings"],
            )

    monkeypatch.setattr(documents, "_INGESTION_ROUTER", _Router())

    def fake_detect_language(fmt, result):
        rec.events.append(("detect_language", fmt, result.text))
        return "tr"

    monkeypatch.setattr(documents, "_detect_document_language", fake_detect_language)

    async def fake_schema(db, doc_id, metadata):
        rec.events.append(("schema", doc_id, metadata))

    monkeypatch.setattr(documents, "_ensure_spreadsheet_schema", fake_schema)

    class _Pipeline:
        def __init__(self, settings):
            rec.events.append(("pipeline_init", settings))

        async def run(self, **kwargs):
            on_progress = kwargs.pop("on_progress")
            on_progress(55)
            rec.events.append(("pipeline_run", kwargs))
            if state["pipeline_error"] is not None:
                raise state["pipeline_error"]

    monkeypatch.setattr(documents, "DocumentPipeline", _Pipeline)

    async def fake_failure(bg_db, doc_id, exc, user_id):
        rec.events.append(("failure", doc_id, exc, user_id))

    monkeypatch.setattr(documents, "_record_background_failure", fake_failure)
    monkeypatch.setattr(
        document_progress, "set_progress", lambda doc_id, pct: rec.events.append(("progress", doc_id, pct))
    )
    monkeypatch.setattr(
        document_progress, "clear_progress", lambda doc_id: rec.events.append(("clear", doc_id))
    )

    return SimpleNamespace(rec=rec, state=state, encrypted=encrypted, bg=bg_db_holder)


async def _upload(env, db=None, content_type="application/pdf"):
    tasks = BackgroundTasks()
    upload = SimpleNamespace(file="FILEOBJ", filename="report.pdf", content_type=content_type)
    user = SimpleNamespace(id=5)
    db = db or _UploadDb()
    result = await documents.upload_document(
        background_tasks=tasks, file=upload, db=db, current_user=user
    )
    return result, tasks, db


async def _run_tasks(tasks: BackgroundTasks) -> None:
    for task in tasks.tasks:
        await task()


async def test_upload_persists_row_and_returns_response(upload_env):
    result, tasks, db = await _upload(upload_env)
    assert len(db.added) == 1
    document = db.added[0]
    assert isinstance(document, Document)
    assert result == ("response", document, None)
    assert db.commits == 1 and db.refreshed == [document]
    assert {
        "filename": document.filename,
        "original_filename": document.original_filename,
        "file_type": document.file_type,
        "file_format": document.file_format,
        "detected_language": document.detected_language,
        "language_override": document.language_override,
        "encrypted_path": document.encrypted_path,
        "file_size_bytes": document.file_size_bytes,
        "transcription_text": document.transcription_text,
        "ocr_confidence": document.ocr_confidence,
        "active_regulation_ids": document.active_regulation_ids,
        "ingestion_status": document.ingestion_status,
        "ingestion_error": document.ingestion_error,
        "user_id": document.user_id,
    } == {
        "filename": "sealed.bin",
        "original_filename": "report.pdf",
        "file_type": "application/pdf",
        "file_format": "pdf",
        "detected_language": "en",
        "language_override": None,
        "encrypted_path": str(upload_env.encrypted),
        "file_size_bytes": 1234,
        "transcription_text": None,
        "ocr_confidence": 0.0,
        "active_regulation_ids": ["gdpr", "kvkk"],
        "ingestion_status": "processing",
        "ingestion_error": None,
        "user_id": 5,
    }
    assert upload_env.rec.events[:2] == [
        ("seal", "FILEOBJ", "application/pdf", upload_env.encrypted),
        ("snapshot",),
    ]
    assert len(tasks.tasks) == 1
    assert upload_env.encrypted.exists()


async def test_upload_without_filename_stores_empty_original_name(upload_env):
    tasks = BackgroundTasks()
    upload = SimpleNamespace(file="F", filename=None)
    db = _UploadDb()
    await documents.upload_document(
        background_tasks=tasks, file=upload, db=db, current_user=SimpleNamespace(id=5)
    )
    assert db.added[0].original_filename == ""
    assert upload_env.rec.events[0] == ("seal", "F", None, upload_env.encrypted)


async def test_failed_row_insert_removes_source_and_reraises(upload_env):
    db = _UploadDb(fail_commit=RuntimeError("fk"))
    with pytest.raises(RuntimeError, match="fk"):
        await _upload(upload_env, db=db)
    assert not upload_env.encrypted.exists()


async def test_failed_snapshot_removes_source(upload_env, monkeypatch):
    async def broken(db):
        raise KeyError("x")

    monkeypatch.setattr(documents, "_snapshot_active_regulations", broken)
    db = _UploadDb()
    with pytest.raises(KeyError):
        await _upload(upload_env, db=db)
    assert not upload_env.encrypted.exists()
    assert db.added == []


async def test_failed_unlink_is_logged_and_original_error_kept(upload_env, monkeypatch, caplog):
    def refuse_unlink(self, missing_ok=False):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "unlink", refuse_unlink)
    db = _UploadDb(fail_commit=RuntimeError("fk"))
    with caplog.at_level(logging.DEBUG, logger=documents.logger.name):
        with pytest.raises(RuntimeError, match="fk"):
            await _upload(upload_env, db=db)
    records = [(r.levelno, r.getMessage()) for r in caplog.records if r.name == documents.logger.name]
    assert records == [
        (logging.ERROR, "Could not remove the source file of a failed upload: PermissionError")
    ]


async def test_background_success_runs_ingestion_and_pipeline(upload_env):
    _, tasks, _ = await _upload(upload_env)
    upload_env.rec.events.clear()
    await _run_tasks(tasks)
    bg = upload_env.bg["db"]
    assert bg.gets == [(Document, 7)]
    assert upload_env.rec.events == [
        ("load_settings",),
        ("kwargs", "pdf", "SETTINGS"),
        (
            "ingest",
            {
                "file_path": upload_env.encrypted,
                "mime_type": "application/pdf",
                "file_format": "pdf",
                "ingester_kwargs": {"k": "pdf"},
            },
        ),
        ("progress", 7, 1),
        ("detect_language", "pdf", "extracted text"),
        ("pipeline_init", "SETTINGS"),
        ("progress", 7, 55),
        (
            "pipeline_run",
            {
                "db": bg,
                "document": upload_env.state["bg_doc"],
                "file_format": "pdf",
                "ingested_text": "extracted text",
                "ingestion_confidence": 0.75,
            },
        ),
        ("clear", 7),
    ]
    assert bg.commits == 1
    assert bg.snapshots == [{"id": 7, "detected_language": "tr", "ocr_confidence": 0.75}]


@pytest.mark.parametrize("fmt", ["xlsx", "ods", "csv", "tsv"])
async def test_background_spreadsheet_builds_schema(upload_env, fmt):
    upload_env.state["seal"] = ("text/csv", fmt, 10)
    _, tasks, _ = await _upload(upload_env)
    upload_env.rec.events.clear()
    await _run_tasks(tasks)
    kinds = [e[0] for e in upload_env.rec.events]
    assert kinds == [
        "load_settings", "kwargs", "ingest", "progress", "detect_language",
        "schema", "pipeline_init", "progress", "pipeline_run", "clear",
    ]
    assert ("schema", 7, {"sheets": [{"column_count": 2}]}) in upload_env.rec.events


@pytest.mark.parametrize(
    ("text", "warnings", "error"),
    [
        ("", [], "Audio transcription failed: decoder produced no samples or text."),
        (None, [], "Audio transcription failed: decoder produced no samples or text."),
        (
            "   ",
            ["w1", "w2"],
            "Audio transcription failed: decoder produced no samples or text. (w1; w2)",
        ),
    ],
)
async def test_background_empty_audio_marks_failed(upload_env, text, warnings, error):
    upload_env.state["seal"] = ("audio/mpeg", "audio", 10)
    upload_env.state["text"] = text
    upload_env.state["warnings"] = warnings
    _, tasks, _ = await _upload(upload_env)
    upload_env.rec.events.clear()
    await _run_tasks(tasks)
    kinds = [e[0] for e in upload_env.rec.events]
    assert kinds == ["load_settings", "kwargs", "ingest", "progress", "detect_language", "clear"]
    bg = upload_env.bg["db"]
    assert bg.commits == 2
    assert bg.snapshots[-1] == {
        "id": 7,
        "detected_language": "tr",
        "ocr_confidence": 0.75,
        "ingestion_status": "failed",
        "ingestion_error": error,
        "chunk_count": 0,
        "entity_count": 0,
    }


async def test_background_audio_with_text_runs_pipeline(upload_env):
    upload_env.state["seal"] = ("audio/mpeg", "audio", 10)
    upload_env.state["text"] = " spoken words "
    _, tasks, _ = await _upload(upload_env)
    upload_env.rec.events.clear()
    await _run_tasks(tasks)
    assert [e[0] for e in upload_env.rec.events][-3:] == ["progress", "pipeline_run", "clear"]


async def test_background_missing_document_does_nothing(upload_env):
    upload_env.state["bg_doc"] = None
    _, tasks, _ = await _upload(upload_env)
    upload_env.rec.events.clear()
    await _run_tasks(tasks)
    assert upload_env.rec.events == []
    assert upload_env.bg["db"].commits == 0


async def test_background_failure_is_recorded_and_progress_cleared(upload_env):
    boom = ValueError("pipeline broke")
    upload_env.state["pipeline_error"] = boom
    _, tasks, _ = await _upload(upload_env)
    upload_env.rec.events.clear()
    await _run_tasks(tasks)
    assert upload_env.rec.events[-2:] == [("failure", 7, boom, 5), ("clear", 7)]


async def test_background_base_exception_propagates_after_clearing(upload_env):
    upload_env.state["pipeline_error"] = KeyboardInterrupt()
    _, tasks, _ = await _upload(upload_env)
    upload_env.rec.events.clear()
    with pytest.raises(KeyboardInterrupt):
        await _run_tasks(tasks)
    assert upload_env.rec.events[-1] == ("clear", 7)
    assert all(e[0] != "failure" for e in upload_env.rec.events)
