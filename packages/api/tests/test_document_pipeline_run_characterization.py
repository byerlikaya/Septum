"""Characterization tests for :meth:`DocumentPipeline.run`.

Records the full ordered sequence of side effects of a pipeline run
(progress ticks, sanitizer calls, persisted chunks, entity detection rows,
commits, map storage, entity index, audit event, FAISS/BM25 indexing and
final document state) against in-memory fakes, so the method can be split
into helpers without changing what an ingestion writes or in which order.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from septum_api.models.entity_detection import EntityDetection
from septum_api.services import document_pipeline as dp
from septum_api.services.sanitizer import ResolvedSpan


class _Db:
    def __init__(self, events: list[tuple], exists: bool = True) -> None:
        self.events = events
        self.exists = exists
        self.added: list[Any] = []

    def add_all(self, rows: list[Any]) -> None:
        for i, row in enumerate(rows):
            row.id = 100 + i
        self.added.extend(rows)
        self.events.append(("add_all", len(rows)))

    async def commit(self) -> None:
        self.events.append(("commit",))

    async def refresh(self, obj: Any) -> None:
        self.events.append(("refresh",))

    async def scalar(self, stmt: Any) -> Any:
        self.events.append(("exists_check",))
        return 7 if self.exists else None

    async def execute(self, stmt: Any) -> None:
        self.events.append(("execute", str(stmt).split()[0]))

    async def rollback(self) -> None:
        self.events.append(("rollback",))


class _Sanitizer:
    def __init__(self, events: list[tuple], results: dict[str, Any]) -> None:
        self.events = events
        self.results = results
        self.maps: list[Any] = []

    def sanitize(self, text: str, language: str, anon_map: Any) -> Any:
        self.events.append(("sanitize", text, language))
        self.maps.append(anon_map)
        return self.results[text]


def _result(sanitized: str, count: int, types: dict[str, int], spans: list[ResolvedSpan]) -> Any:
    return SimpleNamespace(
        sanitized_text=sanitized,
        entity_count=count,
        entity_type_counts=types,
        detected_spans=spans,
    )


def _span(start: int, end: int, etype: str, ph: str) -> ResolvedSpan:
    return ResolvedSpan(start=start, end=end, entity_type=etype, placeholder=ph, score=0.9)


def _chunk(text: str, index: int, page: Any = None, title: Any = None) -> Any:
    return dp.SemanticChunk(
        text=text, index=index, source_page=page, section_title=title, char_count=len(text)
    )


DEFAULT_RESULTS = {
    "N:alpha Alice": _result("alpha [PERSON_1]", 1, {"PERSON_NAME": 1}, [_span(6, 11, "PERSON_NAME", "[PERSON_1]")]),
    "N:beta": _result("beta", 0, {}, []),
    "N:gamma Bob Carl": _result(
        "gamma [PERSON_2] [PERSON_3]",
        2,
        {"PERSON_NAME": 2},
        [_span(6, 9, "PERSON_NAME", "[PERSON_2]"), _span(10, 14, "PERSON_NAME", "[PERSON_3]")],
    ),
}


@pytest.fixture
def env(monkeypatch):
    events: list[tuple] = []
    state: dict[str, Any] = {
        "chunks": [
            _chunk("alpha Alice", 0, page=1, title="Intro"),
            _chunk("beta", 1, page=2),
            _chunk("gamma Bob Carl", 3, title="End"),
        ],
        "results": dict(DEFAULT_RESULTS),
        "persisted_ids": {0: 11, 1: 12, 3: None},
        "index_error": None,
        "audit_event": SimpleNamespace(id=555),
        "exists": True,
    }

    monkeypatch.setattr(dp.remask_cache, "forget_document", lambda doc_id: events.append(("forget", doc_id)))

    class _Composer:
        async def compose_with_snapshot(self, db, settings):
            events.append(("compose", settings))
            return "POLICY", SimpleNamespace(entity_types=["PERSON_NAME"], digest="d1")

    monkeypatch.setattr(dp, "PolicyComposer", _Composer)
    sanitizer = _Sanitizer(events, state["results"])

    async def create_sanitizer(db, settings, enable_ollama=None, policy=None):
        events.append(("create_sanitizer", enable_ollama, policy))
        return sanitizer

    monkeypatch.setattr(dp, "create_sanitizer", create_sanitizer)

    async def build_chunks(self, file_format, text):
        events.append(("build_chunks", file_format, text))
        return state["chunks"]

    monkeypatch.setattr(dp.DocumentPipeline, "_build_chunks", build_chunks)

    class _Normalizer:
        async def normalize(self, db, text):
            return f"N:{text}"

    monkeypatch.setattr(dp, "TextNormalizer", _Normalizer)
    monkeypatch.setattr(dp, "supported_language_code", lambda code: f"sup:{code}")

    def masking_language(document, text):
        events.append(("masking_language", text))
        return "en", state.get("language_source", "detected")

    monkeypatch.setattr(dp, "masking_language", masking_language)

    persisted: dict[str, Any] = {}

    async def persist(self, db, document_id, stored_chunks, raw_texts):
        persisted["stored"] = [
            (c.text, c.index, c.source_page, c.section_title, c.char_count) for c in stored_chunks
        ]
        persisted["raw"] = list(raw_texts)
        events.append(("persist", document_id))
        return [
            SimpleNamespace(index=c.index, id=state["persisted_ids"].get(c.index))
            for c in stored_chunks
        ]

    monkeypatch.setattr(dp.DocumentPipeline, "_persist_chunks", persist)

    class _Lock:
        async def ensure_held(self):
            events.append(("ensure_held",))

    @asynccontextmanager
    async def map_lock(document_id):
        events.append(("lock_enter", document_id))
        yield _Lock()
        events.append(("lock_exit", document_id))

    monkeypatch.setattr(dp, "document_map_lock", map_lock)

    async def set_map(document_id, anon_map):
        persisted["map"] = anon_map
        events.append(("set_map", document_id))

    monkeypatch.setattr(dp, "set_document_map", set_map)

    async def replace_index(db, document_id, anon_map):
        events.append(("replace_index", document_id))
        if state["index_error"] is not None:
            raise state["index_error"]

    async def recompute(db, document_id):
        events.append(("recompute", document_id))

    monkeypatch.setattr(dp, "replace_index_for_document", replace_index)
    monkeypatch.setattr(dp, "recompute_relationships_for_document", recompute)

    async def log_pii(db, **kwargs):
        persisted["audit"] = kwargs
        events.append(("log_pii",))
        return state["audit_event"]

    monkeypatch.setattr(dp, "log_pii_detected", log_pii)
    monkeypatch.setattr(
        dp.DocumentPipeline,
        "_index_chunks",
        staticmethod(lambda doc_id, chunks, raw: events.append(("faiss", doc_id, [c.id for c in chunks], list(raw)))),
    )
    monkeypatch.setattr(
        dp.DocumentPipeline,
        "_index_chunks_bm25",
        staticmethod(lambda doc_id, chunks, raw: events.append(("bm25", doc_id, [c.id for c in chunks], list(raw)))),
    )

    async def clear_override(db, document, text, judged=None):
        events.append(("clear_override", text, judged, document.ingestion_status))

    monkeypatch.setattr(dp.DocumentPipeline, "_clear_refused_override", staticmethod(clear_override))

    return SimpleNamespace(events=events, state=state, persisted=persisted, sanitizer=sanitizer)


def _document() -> Any:
    return SimpleNamespace(
        id=7,
        language_override="tr",
        active_regulation_ids=["gdpr"],
        ingestion_status="processing",
        ingestion_error="old",
        ingestion_error_code="old_code",
    )


async def _run(env, document=None, progress=True, confidence=0.5):
    ticks: list[int] = []
    db = _Db(env.events, exists=env.state["exists"])
    document = document or _document()
    await dp.DocumentPipeline(settings="SETTINGS").run(
        db=db,
        document=document,
        file_format="pdf",
        ingested_text="RAW",
        ingestion_confidence=confidence,
        on_progress=ticks.append if progress else None,
    )
    return document, ticks, db


TRANSCRIPT = "N:alpha Alice\n\nN:beta\n\nN:gamma Bob Carl"


async def test_full_run_sequence(env):
    document, ticks, db = await _run(env)
    assert ticks == [30, 60, 90, 92, 96]
    assert env.events == [
        ("forget", 7),
        ("compose", "SETTINGS"),
        ("create_sanitizer", dp.DOCUMENT_SANITIZER_ENABLE_OLLAMA, "POLICY"),
        ("build_chunks", "pdf", "RAW"),
        ("masking_language", TRANSCRIPT),
        ("sanitize", "N:alpha Alice", "en"),
        ("sanitize", "N:beta", "en"),
        ("sanitize", "N:gamma Bob Carl", "en"),
        ("persist", 7),
        ("add_all", 1),
        ("commit",),
        ("refresh",),
        ("lock_enter", 7),
        ("exists_check",),
        ("ensure_held",),
        ("set_map", 7),
        ("lock_exit", 7),
        ("replace_index", 7),
        ("recompute", 7),
        ("commit",),
        ("log_pii",),
        ("execute", "UPDATE"),
        ("commit",),
        ("faiss", 7, [11, 12, None], ["N:alpha Alice", "N:beta", "N:gamma Bob Carl"]),
        ("bm25", 7, [11, 12, None], ["N:alpha Alice", "N:beta", "N:gamma Bob Carl"]),
        ("clear_override", TRANSCRIPT, ("sup:tr", False), "processing"),
        ("commit",),
    ]
    assert env.persisted["stored"] == [
        ("alpha [PERSON_1]", 0, 1, "Intro", 16),
        ("beta", 1, 2, None, 4),
        ("gamma [PERSON_2] [PERSON_3]", 3, None, "End", 27),
    ]
    assert env.persisted["raw"] == ["N:alpha Alice", "N:beta", "N:gamma Bob Carl"]
    rows = db.added
    assert all(isinstance(r, EntityDetection) for r in rows)
    assert [
        (r.document_id, r.chunk_id, r.entity_type, r.placeholder, r.start_offset, r.end_offset, r.score)
        for r in rows
    ] == [(7, 11, "PERSON_NAME", "[PERSON_1]", 6, 11, 0.9)]
    anon_map = env.persisted["map"]
    assert all(m is anon_map for m in env.sanitizer.maps)
    assert (anon_map.document_id, anon_map.language) == (7, "en")
    assert env.persisted["audit"] == {
        "document_id": 7,
        "regulation_ids": ["gdpr"],
        "entity_type_counts": {"PERSON_NAME": 3},
        "total_count": 3,
        "extra": {"source": "document_ingestion", "language": "en", "language_source": "detected"},
        "placeholder_samples": [],
    }
    assert {
        k: getattr(document, k)
        for k in (
            "transcription_text", "ocr_confidence", "chunk_count", "entity_count",
            "sanitized_entity_types", "sanitized_policy_digest", "sanitized_language",
            "ingestion_status", "ingestion_error", "ingestion_error_code",
        )
    } == {
        "transcription_text": TRANSCRIPT,
        "ocr_confidence": 0.5,
        "chunk_count": 3,
        "entity_count": 3,
        "sanitized_entity_types": ["PERSON_NAME"],
        "sanitized_policy_digest": "d1",
        "sanitized_language": "en",
        "ingestion_status": "completed",
        "ingestion_error": None,
        "ingestion_error_code": None,
    }


async def test_override_source_and_placeholder_samples(env, monkeypatch):
    env.state["language_source"] = "override"

    def fill_map(text, language, anon_map):
        for i in range(7):
            anon_map.entity_map[f"orig{i}"] = f"[P_{i}]"
        return _result(text, 1, {"X": 1}, [])

    monkeypatch.setattr(env.sanitizer, "sanitize", fill_map)
    env.state["chunks"] = [_chunk("only", 0)]
    env.state["persisted_ids"] = {0: 11}
    await _run(env)
    assert env.persisted["audit"]["placeholder_samples"] == [f"[P_{i}]" for i in range(5)]
    assert env.persisted["audit"]["extra"]["language_source"] == "override"
    assert ("clear_override", "N:only", ("sup:tr", True), "processing") in env.events
    assert ("add_all", 0) not in env.events
    assert all(e[0] != "execute" for e in env.events)


async def test_no_entities_skips_audit_and_detection_rows(env):
    env.state["chunks"] = [_chunk("beta", 1)]
    document, ticks, db = await _run(env)
    kinds = [e[0] for e in env.events]
    assert "log_pii" not in kinds and "add_all" not in kinds and "execute" not in kinds
    assert ticks == [90, 92, 96]
    assert document.entity_count == 0 and document.chunk_count == 1


async def test_audit_event_none_skips_detection_link(env):
    env.state["audit_event"] = None
    await _run(env)
    kinds = [e[0] for e in env.events]
    assert "log_pii" in kinds and "execute" not in kinds


async def test_empty_document_skips_indexing(env):
    env.state["chunks"] = []
    document, ticks, _ = await _run(env, progress=True, confidence=None)
    assert ticks == []
    kinds = [e[0] for e in env.events]
    assert "faiss" not in kinds and "bm25" not in kinds and "sanitize" not in kinds
    assert ("masking_language", "") in env.events
    assert document.transcription_text == "" and document.ocr_confidence is None
    assert document.chunk_count == 0 and document.ingestion_status == "completed"


async def test_progress_callback_optional(env):
    document, ticks, _ = await _run(env, progress=False)
    assert ticks == [] and document.ingestion_status == "completed"


async def test_deleted_document_aborts_before_indexes(env):
    env.state["exists"] = False
    document = _document()
    with pytest.raises(dp.DocumentDeletedDuringIngestionError, match="^Document 7 was deleted during ingestion$"):
        await _run(env, document=document)
    kinds = [e[0] for e in env.events]
    assert kinds[-2:] == ["lock_enter", "exists_check"]
    assert document.ingestion_status == "processing"


async def test_entity_index_failure_is_logged_and_rolled_back(env, caplog):
    env.state["index_error"] = RuntimeError("secret detail")
    with caplog.at_level(logging.DEBUG, logger=dp.logger.name):
        document, _, _ = await _run(env)
    i = env.events.index(("replace_index", 7))
    assert env.events[i + 1] == ("rollback",)
    assert ("recompute", 7) not in env.events
    messages = [(r.levelno, r.getMessage()) for r in caplog.records if r.name == dp.logger.name]
    assert messages == [
        (logging.ERROR, "Failed to populate entity index / relationships for document 7: RuntimeError")
    ]
    assert document.ingestion_status == "completed"


async def test_sanitizer_failure_propagates_before_persist(env, monkeypatch):
    def boom(text, language, anon_map):
        raise ValueError("x")

    monkeypatch.setattr(env.sanitizer, "sanitize", boom)
    with pytest.raises(ValueError):
        await _run(env)
    assert all(e[0] != "persist" for e in env.events)
