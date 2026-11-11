"""Characterization tests for :meth:`DocumentPipeline.run`.

Records the full ordered sequence of side effects of a pipeline run
(progress ticks, one detection over the whole document, persisted chunks,
entity detection rows, commits, map storage, entity index, audit event,
FAISS/BM25 indexing and final document state) against in-memory fakes, so
the method can be split into helpers without changing what an ingestion
writes or in which order.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from sanitizer_doubles import CharTokenCounter
from septum_api.errors import ChunkTooLargeError
from septum_api.models.entity_detection import EntityDetection
from septum_api.request_limits import MAX_PRE_APPROVED_CHUNK_CHARS
from septum_api.services import document_pipeline as dp
from septum_api.services.chunking import ChunkRecord
from septum_api.services.ingestion.base import IngestionResult, TextBlock, block_starts
from septum_core.spans import DetectedSpan, DetectionResult, SanitizeResult


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


NAMES = ("Alice", "Bob", "Carl")
BLOCKS = [
    TextBlock("alpha Alice", "paragraph", page=1),
    TextBlock("beta", "paragraph", page=2),
    TextBlock("gamma Bob Carl", "paragraph", page=3),
]
TEXT = "N:alpha Alice\nN:beta\nN:gamma Bob Carl"


class _Sanitizer:
    """Finds ``NAMES`` once over the whole text; ``apply`` is the core replacement step."""

    def __init__(self, events: list[tuple]) -> None:
        self.events = events
        self.maps: list[Any] = []

    def detect(self, text: str, language: str, *, boundaries=()) -> DetectionResult:
        self.events.append(("detect", text, language, tuple(boundaries)))
        spans = []
        for name in NAMES:
            start = text.find(name)
            while start >= 0:
                spans.append(DetectedSpan(start, start + len(name), "PERSON_NAME", 0.9))
                start = text.find(name, start + 1)
        return DetectionResult(text=text, language=language, spans=tuple(spans), semantic_enabled=False)

    def apply(self, detection: DetectionResult, anon_map: Any) -> SanitizeResult:
        from septum_core.detector import Detector

        self.events.append(("apply", len(detection.spans)))
        self.maps.append(anon_map)
        masked, count, resolved = Detector._apply_replacements(
            detection.text, list(detection.spans), anon_map, detection.language
        )
        counts: dict[str, int] = {}
        for span in detection.spans:
            counts[span.entity_type] = counts.get(span.entity_type, 0) + 1
        return SanitizeResult(
            sanitized_text=masked, entity_count=count, entity_type_counts=counts,
            detected_spans=resolved,
        )


def _block_records(text: str, blocks: list[TextBlock]) -> list[ChunkRecord]:
    """One chunk per block; the last block's chunk carries a section title."""
    starts = block_starts(blocks)
    ends = starts[1:] + [len(text)]
    return [
        ChunkRecord(
            index=i, start=a, end=b, text=text[a:b], strategy="structured", page=blocks[i].page,
            section_title="Intro" if i == 0 else None,
            section_title_start=0 if i == 0 else None,
            section_title_end=len(blocks[0].text) if i == 0 else None,
        )
        for i, (a, b) in enumerate(zip(starts, ends))
    ]


@pytest.fixture
def env(monkeypatch):
    events: list[tuple] = []
    state: dict[str, Any] = {
        "blocks": list(BLOCKS),
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
    sanitizer = _Sanitizer(events)

    async def create_sanitizer(db, settings, enable_ollama=None, policy=None):
        events.append(("create_sanitizer", enable_ollama, policy))
        return sanitizer

    monkeypatch.setattr(dp, "create_sanitizer", create_sanitizer)

    class _Normalizer:
        async def compile(self, db):
            events.append(("normalizer",))
            return lambda text: f"N:{text}"

    monkeypatch.setattr(dp, "TextNormalizer", _Normalizer)
    monkeypatch.setattr(dp, "supported_language_code", lambda code: f"sup:{code}")
    monkeypatch.setattr(dp, "_token_counter", CharTokenCounter)

    def chunk_records(self, file_format, blocks, protected, counter):
        events.append(("chunk", file_format, list(protected)))
        text = "\n".join(b.text for b in blocks)
        return state.get("records") or _block_records(text, blocks)

    monkeypatch.setattr(dp.DocumentPipeline, "_chunk_records", chunk_records)

    def masking_language(document, text):
        events.append(("masking_language", text))
        return state.get("masking_language", "en"), state.get("language_source", "detected")

    monkeypatch.setattr(dp, "masking_language", masking_language)

    persisted: dict[str, Any] = {}

    async def persist(self, db, chunks):
        persisted["stored"] = [
            (c.sanitized_text, c.raw_text, c.index, c.source_page, c.section_title, c.char_count)
            for c in chunks
        ]
        events.append(("persist", len(chunks)))
        for chunk in chunks:
            chunk.id = 11 + chunk.index
        return list(chunks)

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
        staticmethod(lambda doc_id, chunks, raw, language: (
            state.__setitem__("bm25_language", language),
            events.append(("bm25", doc_id, [c.id for c in chunks], list(raw))),
        )),
    )

    async def clear_override(db, document, text, judged=None):
        events.append(("clear_override", text, judged, document.ingestion_status))

    monkeypatch.setattr(dp.DocumentPipeline, "_clear_refused_override", staticmethod(clear_override))

    return SimpleNamespace(events=events, state=state, persisted=persisted, sanitizer=sanitizer)


def _document() -> Any:
    return SimpleNamespace(
        id=7,
        user_id=None,
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
    blocks = env.state["blocks"]
    await dp.DocumentPipeline(settings="SETTINGS").run(
        db=db,
        document=document,
        file_format="pdf",
        ingestion_result=IngestionResult(
            text="\n".join(b.text for b in blocks), metadata={}, confidence=confidence,
            blocks=blocks,
        ),
        on_progress=ticks.append if progress else None,
    )
    return document, ticks, db


TRANSCRIPT = "N:alpha Alice\n\n\nN:beta\n\n\nN:gamma Bob Carl"
RAW_CHUNKS = ["N:alpha Alice\n", "N:beta\n", "N:gamma Bob Carl"]


async def test_full_run_sequence(env):
    document, ticks, db = await _run(env)
    assert ticks == [60, 70, 90, 92, 96]
    assert document.entity_index_version == dp.ENTITY_INDEX_VERSION
    assert env.events == [
        ("forget", 7),
        ("compose", "SETTINGS"),
        ("create_sanitizer", dp.DOCUMENT_SANITIZER_ENABLE_OLLAMA, "POLICY"),
        ("normalizer",),
        ("masking_language", TEXT),
        ("detect", TEXT, "en", (0, 14, 21)),
        ("apply", 3),
        ("chunk", "pdf", [(8, 13), (29, 32), (33, 37)]),
        ("persist", 3),
        ("add_all", 3),
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
        ("faiss", 7, [11, 12, 13], RAW_CHUNKS),
        ("bm25", 7, [11, 12, 13], RAW_CHUNKS),
        ("clear_override", TEXT, ("sup:tr", False), "processing"),
        ("commit",),
    ]
    assert env.persisted["stored"] == [
        ("N:alpha [PERSON_NAME_1]\n", RAW_CHUNKS[0], 0, 1, "N:alpha [PERSON_NAME_1]", 24),
        ("N:beta\n", RAW_CHUNKS[1], 1, 2, None, 7),
        ("N:gamma [PERSON_NAME_2] [PERSON_NAME_3]", RAW_CHUNKS[2], 2, 3, None, 39),
    ]
    rows = db.added
    assert all(isinstance(r, EntityDetection) for r in rows)
    assert [
        (r.document_id, r.chunk_id, r.entity_type, r.placeholder, r.start_offset, r.end_offset, r.score)
        for r in rows
    ] == [
        (7, 11, "PERSON_NAME", "[PERSON_NAME_1]", 8, 13, 0.9),
        (7, 13, "PERSON_NAME", "[PERSON_NAME_2]", 8, 11, 0.9),
        (7, 13, "PERSON_NAME", "[PERSON_NAME_3]", 12, 16, 0.9),
    ]
    for row, raw in zip(rows, [RAW_CHUNKS[0], RAW_CHUNKS[2], RAW_CHUNKS[2]]):
        assert raw[row.start_offset : row.end_offset] in NAMES
    anon_map = env.persisted["map"]
    assert env.sanitizer.maps == [anon_map]
    assert (anon_map.document_id, anon_map.language) == (7, "en")
    assert env.persisted["audit"] == {
        "document_id": 7,
        "regulation_ids": ["gdpr"],
        "entity_type_counts": {"PERSON_NAME": 3},
        "total_count": 3,
        "extra": {"source": "document_ingestion", "language": "en", "language_source": "detected"},
        "placeholder_samples": ["[PERSON_NAME_1]", "[PERSON_NAME_2]", "[PERSON_NAME_3]"],
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


async def test_bm25_index_is_tokenized_in_the_masking_language(env):
    env.state["masking_language"] = "de"

    await _run(env)

    assert env.state["bm25_language"] == "de"


async def test_bm25_write_waits_for_the_index_lock(monkeypatch):
    written: list[int] = []
    monkeypatch.setattr(
        dp.DocumentPipeline, "_index_chunks", staticmethod(lambda doc_id, chunks, raw: None)
    )
    monkeypatch.setattr(
        dp.DocumentPipeline,
        "_index_chunks_bm25",
        staticmethod(lambda doc_id, chunks, raw, language: written.append(doc_id)),
    )
    pipeline = dp.DocumentPipeline(settings="SETTINGS")

    async with dp.bm25_index_lock(7):
        task = asyncio.create_task(
            pipeline._index_document(7, [SimpleNamespace(id=1)], ["raw"], "en", lambda _: None)
        )
        await asyncio.sleep(0.05)
        assert not task.done()
        assert written == []
    await task

    assert written == [7]


async def test_override_source_is_recorded(env):
    env.state["language_source"] = "override"
    env.state["blocks"] = [TextBlock("only", "text")]
    await _run(env)
    assert ("clear_override", "N:only", ("sup:tr", True), "processing") in env.events
    kinds = [e[0] for e in env.events]
    assert "add_all" not in kinds and "log_pii" not in kinds and "execute" not in kinds


async def test_no_entities_skips_audit_and_detection_rows(env):
    env.state["blocks"] = [TextBlock("beta", "text")]
    document, ticks, db = await _run(env)
    kinds = [e[0] for e in env.events]
    assert "log_pii" not in kinds and "add_all" not in kinds and "execute" not in kinds
    assert ticks == [60, 70, 90, 92, 96]
    assert document.entity_count == 0 and document.chunk_count == 1


async def test_audit_event_none_skips_detection_link(env):
    env.state["audit_event"] = None
    await _run(env)
    kinds = [e[0] for e in env.events]
    assert "log_pii" in kinds and "execute" not in kinds


async def test_empty_document_skips_detection_and_indexing(env):
    env.state["blocks"] = []
    document, ticks, _ = await _run(env, progress=True, confidence=None)
    assert ticks == []
    kinds = [e[0] for e in env.events]
    assert not {"faiss", "bm25", "detect", "apply", "chunk"} & set(kinds)
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
    assert not hasattr(document, "entity_index_version")
    messages = [(r.levelno, r.getMessage()) for r in caplog.records if r.name == dp.logger.name]
    assert messages == [
        (logging.ERROR, "Failed to populate entity index / relationships for document 7: RuntimeError")
    ]
    assert document.ingestion_status == "completed"


async def test_detector_failure_propagates_before_persist(env, monkeypatch):
    def boom(text, language, *, boundaries=()):
        raise ValueError("x")

    monkeypatch.setattr(env.sanitizer, "detect", boom)
    with pytest.raises(ValueError):
        await _run(env)
    assert all(e[0] != "persist" for e in env.events)


async def test_detector_text_that_differs_from_the_blocks_fails_closed(env, monkeypatch):
    real = env.sanitizer.detect

    def shifted(text, language, *, boundaries=()):
        result = real(text, language, boundaries=boundaries)
        return DetectionResult(
            text=result.text + " ", language=language, spans=result.spans, semantic_enabled=False
        )

    monkeypatch.setattr(env.sanitizer, "detect", shifted)
    with pytest.raises(dp.DocumentTextMismatchError) as caught:
        await _run(env)
    assert "Alice" not in str(caught.value)
    kinds = [e[0] for e in env.events]
    assert "apply" not in kinds and "persist" not in kinds


async def test_masked_chunk_over_the_limit_fails_without_text(env):
    name = "Alice" * (MAX_PRE_APPROVED_CHUNK_CHARS // 5 + 1)
    env.state["blocks"] = [TextBlock(name, "text")]
    env.sanitizer.detect = lambda text, language, *, boundaries=(): DetectionResult(
        text=text, language=language, semantic_enabled=False,
        spans=tuple(
            DetectedSpan(i, i + 5, "PERSON_NAME", 0.9) for i in range(2, len(text), 5)
        ),
    )
    with pytest.raises(ChunkTooLargeError) as caught:
        await _run(env)
    assert caught.value.code == "chunk_too_large"
    assert "Alice" not in str(caught.value)
    assert all(e[0] != "persist" for e in env.events)


async def test_oversize_raw_chunk_is_allowed_when_its_masked_text_fits(env):
    name = "Alice" * (MAX_PRE_APPROVED_CHUNK_CHARS // 5 + 1)
    env.state["blocks"] = [TextBlock(name, "text")]
    env.sanitizer.detect = lambda text, language, *, boundaries=(): DetectionResult(
        text=text, language=language, semantic_enabled=False,
        spans=(DetectedSpan(2, len(text), "PERSON_NAME", 0.9),),
    )
    await _run(env)
    [(masked, raw, *_)] = env.persisted["stored"]
    assert len(raw) > MAX_PRE_APPROVED_CHUNK_CHARS
    assert masked == "N:[PERSON_NAME_1]"


async def test_ingestion_runs_without_a_local_model_call_budget(env):
    from septum_api.services import ollama_call_budget as budget_module
    from septum_api.services.ollama_detection_runner import DetectionCall, run_detection_calls
    from septum_api.services.prompts import DetectionPrompt

    seen: list[Any] = []
    real = env.sanitizer.detect

    def send(**kwargs: Any) -> str:
        return "[]"

    def detect(text: str, language: str, *, boundaries=()) -> Any:
        seen.append(budget_module._current_budget.get())
        calls = [
            DetectionCall(
                prompt=lambda nonce, i=i: DetectionPrompt("s", f"<data-{nonce}>\n{text} {i}\n</data-{nonce}>"),
                parse=lambda reply: reply,
                base_url=None,
                model=None,
            )
            for i in range(MANY_CALLS)
        ]
        assert len(run_detection_calls(calls, send=send, refusal=RuntimeError)) == MANY_CALLS
        return real(text, language, boundaries=boundaries)

    env.sanitizer.detect = detect

    await _run(env)

    assert seen and all(budget is None for budget in seen)


MANY_CALLS = 200
