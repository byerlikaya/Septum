"""The document pipeline composes Unicode to NFC before detection and chunking.

The detector composes its input before computing span offsets, so if the
preview text (``transcription_text``), the normalization rules and the
indexes saw the decomposed (NFD) form, every stored detection offset
would point at the wrong characters in the preview, and a rule written
in composed form would never match.
"""

from __future__ import annotations

import unicodedata
from contextlib import asynccontextmanager

from septum_core.spans import DetectionResult, SanitizeResult
from septum_core.text_utils import normalize_unicode

from septum_api.services.ingestion.base import IngestionResult

_COMPOSED = "Şule Çelik İzmir'de oturuyor."
_DECOMPOSED = unicodedata.normalize("NFD", _COMPOSED)


async def _run_pipeline(tmp_path, monkeypatch, rules=()):
    """Run the pipeline on decomposed text and return what each stage saw."""
    from septum_api.database import build_default_app_settings
    from septum_api.models import Base
    from septum_api.models.document import Chunk, Document
    from septum_api.services import document_pipeline as dp
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    assert _DECOMPOSED != _COMPOSED
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'nfc.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(bind=engine, expire_on_commit=False)

    seen: dict[str, list[str]] = {"sanitized": [], "chunked": []}

    class _Lock:
        async def ensure_held(self) -> None:
            return None

    @asynccontextmanager
    async def _map_lock(document_id):
        yield _Lock()

    async def _noop(*args, **kwargs):
        return None

    class _Sanitizer:
        def detect(self, text, language, *, boundaries=()):
            seen["sanitized"].append(text)
            return DetectionResult(
                text=normalize_unicode(text), language=language, semantic_enabled=False
            )

        def apply(self, detection, anon_map):
            return SanitizeResult(sanitized_text=detection.text, entity_count=0)

    async def _create_sanitizer(db, settings, enable_ollama=None, policy=None):
        return _Sanitizer()

    real_chunk_records = dp.DocumentPipeline._chunk_records

    def _spy_chunk_records(self, file_format, blocks, protected):
        seen["chunked"].append("".join(block.text for block in blocks))
        return real_chunk_records(self, file_format, blocks, protected)

    monkeypatch.setattr(dp, "create_sanitizer", _create_sanitizer)
    monkeypatch.setattr(dp.DocumentPipeline, "_chunk_records", _spy_chunk_records)
    monkeypatch.setattr(dp.DocumentPipeline, "_index_document", _noop)
    monkeypatch.setattr(dp, "document_map_lock", _map_lock)
    monkeypatch.setattr(dp, "set_document_map", _noop)
    monkeypatch.setattr(dp, "replace_index_for_document", _noop)
    monkeypatch.setattr(dp, "recompute_relationships_for_document", _noop)
    try:
        async with maker() as session:
            session.add_all(list(rules))
            doc = Document(
                filename="f", original_filename="f.pdf", file_type="application/pdf",
                file_format="pdf", detected_language="tr", encrypted_path="/x",
                file_size_bytes=1, ingestion_status="processing",
            )
            session.add(doc)
            await session.commit()

            await dp.DocumentPipeline(settings=build_default_app_settings()).run(
                db=session, document=doc, file_format="pdf",
                ingestion_result=IngestionResult(text=_DECOMPOSED, metadata={}),
            )

        async with maker() as session:
            stored = await session.get(Document, doc.id)
            seen["transcription"] = [stored.transcription_text]
            chunks = (await session.execute(select(Chunk).order_by(Chunk.index))).scalars()
            seen["persisted"] = [c.raw_text for c in chunks]
    finally:
        await engine.dispose()
    return seen


async def test_every_stage_sees_the_composed_text(tmp_path, monkeypatch) -> None:
    seen = await _run_pipeline(tmp_path, monkeypatch)
    assert seen["chunked"] == [_COMPOSED]
    assert seen["sanitized"] == [_COMPOSED]
    assert seen["persisted"] == [_COMPOSED]
    assert seen["transcription"] == [_COMPOSED]


async def test_detection_offsets_line_up_with_the_preview(tmp_path, monkeypatch) -> None:
    seen = await _run_pipeline(tmp_path, monkeypatch)
    [detector_input] = seen["sanitized"]
    [preview] = seen["transcription"]
    start = detector_input.index("Çelik")
    assert preview[start : start + len("Çelik")] == "Çelik"


async def test_composed_normalization_rule_applies_to_decomposed_input(
    tmp_path, monkeypatch
) -> None:
    from septum_api.models.text_normalization import TextNormalizationRule

    rule = TextNormalizationRule(
        name="city", pattern="İzmir", replacement="Izmir", is_active=True, priority=0
    )
    seen = await _run_pipeline(tmp_path, monkeypatch, rules=[rule])
    assert seen["sanitized"] == [_COMPOSED.replace("İzmir", "Izmir")]
