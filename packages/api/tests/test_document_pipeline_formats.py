"""End-to-end ingestion runs per file format, reingest, and the reprocess script.

The real pipeline runs (normalization, chunker, projection, SQLite rows) with a
pattern detector and a character token counter; no model is loaded and indexing
and the map store are stubbed.
"""

from __future__ import annotations

import importlib.util
import re
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from sanitizer_doubles import PatternDetector, literal
from septum_api.database import build_default_app_settings
from septum_api.models import Base
from septum_api.models.document import Chunk, Document
from septum_api.models.entity_detection import EntityDetection
from septum_api.services import document_pipeline as dp
from septum_api.services.ingestion.base import (
    IngestionResult,
    TextBlock,
    block_starts,
    join_blocks,
)
from septum_api.services.ingestion.sheet_text import render_sheet
from septum_core.spans import DetectionResult
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

NAME = "Ayşe Yılmazoğlu"


class _Lock:
    async def ensure_held(self) -> None:
        return None


async def _noop(*args, **kwargs):
    return None


@pytest.fixture
async def env(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'formats.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(bind=engine, expire_on_commit=False)

    @asynccontextmanager
    async def _map_lock(document_id):
        yield _Lock()

    state = SimpleNamespace(
        maker=maker,
        detector=PatternDetector(literal(NAME, "PERSON_NAME")),
        rule=None,
        settings=build_default_app_settings(),
    )

    async def _create_sanitizer(db, settings, enable_ollama=None, policy=None):
        return state.detector

    class _Normalizer:
        async def compile(self, db):
            return state.rule or (lambda text: text)

    monkeypatch.setattr(dp, "create_sanitizer", _create_sanitizer)
    monkeypatch.setattr(dp, "TextNormalizer", _Normalizer)
    monkeypatch.setattr(dp, "document_map_lock", _map_lock)
    monkeypatch.setattr(dp, "set_document_map", _noop)
    monkeypatch.setattr(dp, "replace_index_for_document", _noop)
    monkeypatch.setattr(dp, "recompute_relationships_for_document", _noop)
    monkeypatch.setattr(dp.DocumentPipeline, "_index_document", _noop)
    try:
        yield state
    finally:
        await engine.dispose()


async def _new_document(session, file_format: str) -> Document:
    doc = Document(
        filename="f", original_filename="f", file_type="x", file_format=file_format,
        detected_language="en", encrypted_path="/x", file_size_bytes=1,
        ingestion_status="processing",
    )
    session.add(doc)
    await session.commit()
    return doc


async def _read(maker):
    async with maker() as session:
        chunks = list((await session.execute(select(Chunk).order_by(Chunk.index))).scalars())
        rows = list(
            (await session.execute(select(EntityDetection).order_by(EntityDetection.id))).scalars()
        )
        doc = (await session.execute(select(Document))).scalars().one()
    return chunks, rows, doc


async def _ingest(env, blocks, file_format, **settings):
    for key, value in settings.items():
        setattr(env.settings, key, value)
    async with env.maker() as session:
        doc = await _new_document(session, file_format)
        await dp.DocumentPipeline(settings=env.settings).run(
            db=session, document=doc, file_format=file_format,
            ingestion_result=IngestionResult(
                text=join_blocks(blocks), metadata={}, blocks=list(blocks)
            ),
        )
    return await _read(env.maker)


def _assert_masked(chunks, rows) -> None:
    by_id = {c.id: c for c in chunks}
    for chunk in chunks:
        assert NAME not in chunk.sanitized_text
        assert NAME not in (chunk.section_title or "")
    for row in rows:
        assert by_id[row.chunk_id].raw_text[row.start_offset : row.end_offset] == NAME


async def test_pdf_page_blocks_keep_their_page_numbers_on_stored_chunks(env) -> None:
    pages = [
        f"pg{number} " + ("filler words fill the page. " * 20)
        + (f"Signed by {NAME}. " if number == 2 else "")
        for number in (1, 2, 3, 4)
    ]
    blocks = [TextBlock(text, "page", page=number) for number, text in enumerate(pages, 1)]

    chunks, rows, doc = await _ingest(env, blocks, "pdf", pdf_chunk_size=800)

    assert len(chunks) >= 3
    _assert_masked(chunks, rows)
    seen = [c.source_page for c in chunks]
    assert seen == sorted(seen) and set(seen) == {1, 2, 3, 4}
    assert all(f"pg{c.source_page}" in c.raw_text for c in chunks)
    assert all(c.source_sheet is None and c.source_timestamp_start is None for c in chunks)
    assert doc.entity_count == 1 and doc.chunk_count == len(chunks)


async def test_xlsx_rows_stay_whole_after_a_length_changing_rule(env) -> None:
    grid = [["h1", "h2"]] + [[f"id & {r}", f"{NAME if r % 7 == 3 else 'plain'} {r}"] for r in range(40)]
    first, _, _ = render_sheet("Sales", grid)
    second, _, _ = render_sheet(NAME, grid)
    env.rule = lambda text: text.replace("&", " and then also ").replace("plain", "p")

    chunks, rows, _ = await _ingest(
        env, first + second, "xlsx", spreadsheet_chunk_size=20
    )

    assert len(chunks) > 3
    _assert_masked(chunks, rows)
    assert {c.source_sheet for c in chunks} == {1, 2}
    assert [c.source_sheet for c in chunks] == sorted(c.source_sheet for c in chunks)
    for chunk in chunks:
        lines = [ln for ln in chunk.raw_text.split("\n") if ln and not ln.startswith("# Sheet")]
        assert lines and all(ln.count("\t") == 1 for ln in lines)
        assert "&" not in chunk.raw_text
    assert any(NAME in c.raw_text for c in chunks) and rows


async def test_audio_segments_keep_their_time_range_on_stored_chunks(env) -> None:
    texts = [
        " Welcome to the call. ",
        " We spoke about the lease. ",
        f" {NAME} agreed to the terms. ",
        " Then we closed the call. ",
        " Thanks everyone for joining. ",
        " Goodbye for now. ",
    ]
    blocks = [
        TextBlock(text, "segment", t_start=i * 10.0, t_end=(i + 1) * 10.0)
        for i, text in enumerate(texts)
    ]

    chunks, rows, doc = await _ingest(env, blocks, "audio", audio_chunk_size=20)

    assert len(chunks) >= 2
    _assert_masked(chunks, rows)
    starts = [c.source_timestamp_start for c in chunks]
    ends = [c.source_timestamp_end for c in chunks]
    assert starts[0] == 0.0 and ends[-1] == 60.0
    assert starts == sorted(starts)
    assert all(s < e for s, e in zip(starts, ends))
    assert all(c.source_page is None and c.source_sheet is None for c in chunks)
    assert doc.entity_count == 1


async def test_a_rule_that_shrinks_text_keeps_detection_offsets_aligned(env) -> None:
    blocks = [
        TextBlock("first    page    with    wide    gaps", "page", page=1),
        TextBlock(f"second page   mentions   {NAME}   here", "page", page=2),
        TextBlock("a   b\nc   d\ne   f", "rows", sheet="S", row_offsets=[0, 6, 12]),
        TextBlock(f"last   {NAME}", "page", page=3),
    ]
    env.rule = lambda text: re.sub(r" {2,}", " ", text)

    chunks, rows, doc = await _ingest(env, blocks, "image", chunk_size=400)

    _assert_masked(chunks, rows)
    assert len(rows) == 2
    assert "   " not in doc.transcription_text
    assert all("  " not in c.raw_text for c in chunks)


def test_block_starts_follow_normalized_blocks_after_a_shrinking_rule() -> None:
    blocks = [
        TextBlock("aa    bb", "heading"),
        TextBlock("x   y\nz   w", "rows", sheet="S", row_offsets=[0, 6]),
        TextBlock("tail    end", "paragraph"),
    ]

    normalized = dp._normalized_blocks(blocks, lambda text: re.sub(r" {2,}", " ", text))
    joined = join_blocks(normalized)
    starts = block_starts(normalized)

    assert normalized[1].text == "x y\nz w" and list(normalized[1].row_offsets) == [0, 4]
    for block, start in zip(normalized, starts):
        assert joined[start : start + len(block.text)] == block.text


async def test_detector_text_that_differs_from_the_blocks_stores_nothing(env) -> None:
    class _Diverging(PatternDetector):
        def detect(self, text, language, *, boundaries=()):
            return DetectionResult(
                text=text + " ", language=language, spans=(), semantic_enabled=False
            )

    env.detector = _Diverging(literal(NAME, "PERSON_NAME"))
    blocks = [TextBlock(f"Tenant {NAME} signed.", "text")]

    async with env.maker() as session:
        doc = await _new_document(session, "image")
        with pytest.raises(dp.DocumentTextMismatchError) as caught:
            await dp.DocumentPipeline(settings=env.settings).run(
                db=session, document=doc, file_format="image",
                ingestion_result=IngestionResult(
                    text=join_blocks(blocks), metadata={}, blocks=blocks
                ),
            )
    assert NAME not in str(caught.value) and "Tenant" not in str(caught.value)
    chunks, rows, stored = await _read(env.maker)
    assert chunks == [] and rows == []
    assert stored.ingestion_status == "processing"
    assert env.detector.calls == []


@pytest.mark.parametrize(
    "file_format, strategy",
    [
        ("pdf", "structured"),
        ("docx", "structured"),
        ("txt", "structured"),
        ("xlsx", "table"),
        ("ods", "table"),
        ("csv", "table"),
        ("tsv", "table"),
        ("audio", "audio"),
        ("image", "sliding"),
        ("unknown_format", "sliding"),
    ],
)
def test_each_format_is_cut_with_its_strategy(file_format: str, strategy: str) -> None:
    pipeline = _pipeline()
    if strategy == "table":
        blocks, _, _ = render_sheet("S", [["a", "b"]] * 30)
    elif strategy == "audio":
        blocks = [TextBlock(f" word {i} ", "segment", t_start=i * 5.0, t_end=i * 5.0 + 5) for i in range(12)]
    else:
        blocks = [TextBlock("para one is here.", "paragraph"), TextBlock("para two is here. " * 80, "paragraph")]

    records = pipeline._chunk_records(file_format, blocks, [])

    assert records and {r.strategy for r in records} == {strategy}


def _pipeline() -> dp.DocumentPipeline:
    settings = build_default_app_settings()
    settings.spreadsheet_chunk_size = 10
    settings.audio_chunk_size = 20
    settings.chunk_size = 300
    settings.pdf_chunk_size = 800
    return dp.DocumentPipeline(settings)


@pytest.fixture
async def reingest_env(env, monkeypatch):
    from septum_api.routers import documents

    ingested: list[Path] = []
    state = SimpleNamespace(blocks=[], error=None, cleaned=[])

    class _Router:
        async def ingest(self, *, file_path, mime_type, file_format, ingester_kwargs):
            ingested.append(file_path)
            if state.error is not None:
                raise state.error
            return IngestionResult(
                text=join_blocks(state.blocks), metadata={}, blocks=list(state.blocks)
            )

    async def _cleanup(document_id):
        state.cleaned.append(document_id)

    async def _load_settings(db):
        return env.settings

    monkeypatch.setattr(documents, "_INGESTION_ROUTER", _Router())
    monkeypatch.setattr(dp.DocumentPipeline, "cleanup_artifacts", staticmethod(_cleanup))
    monkeypatch.setattr(documents, "load_settings", _load_settings)
    state.documents = documents
    state.ingested = ingested
    state.env = env
    return state


async def test_reingest_document_runs_the_pipeline_and_finishes_the_row(reingest_env) -> None:
    reingest_env.blocks = [
        TextBlock(f"Tenant {NAME} signed the lease.", "page", page=1),
        TextBlock("Second page body text.", "page", page=2),
    ]
    env = reingest_env.env
    async with env.maker() as session:
        doc = await _new_document(session, "pdf")
        await reingest_env.documents.reingest_document(session, doc)

    chunks, rows, stored = await _read(env.maker)
    assert reingest_env.cleaned == [doc.id]
    assert reingest_env.ingested == [Path("/x")]
    assert stored.ingestion_status == "completed" and stored.ingestion_error is None
    assert stored.chunk_count == len(chunks) and stored.entity_count == 1
    assert stored.sanitized_policy_digest
    _assert_masked(chunks, rows)
    assert [c.source_page for c in chunks][0] == 1


async def test_reingest_document_fails_an_audio_file_without_text(reingest_env) -> None:
    reingest_env.blocks = []
    env = reingest_env.env
    async with env.maker() as session:
        doc = await _new_document(session, "audio")
        await reingest_env.documents.reingest_document(session, doc)

    chunks, _, stored = await _read(env.maker)
    assert chunks == []
    assert stored.ingestion_status == "failed"
    assert stored.ingestion_error == "Audio transcription produced no text."


def _load_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "reprocess_entity_detections.py"
    spec = importlib.util.spec_from_file_location("reprocess_entity_detections_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def script(reingest_env, monkeypatch):
    from septum_api import bootstrap, database
    from septum_api.routers import documents

    monkeypatch.chdir(Path.cwd())
    module = _load_script()
    monkeypatch.setattr(bootstrap, "needs_setup", lambda: False)
    monkeypatch.setattr(
        bootstrap, "get_config", lambda: SimpleNamespace(database_url="sqlite://", db_path="x", redis_url=None)
    )
    monkeypatch.setattr(database, "build_database_url", lambda url, path: url)
    monkeypatch.setattr(database, "initialize_engine", lambda url: None)
    monkeypatch.setattr(database, "init_db", _noop)
    monkeypatch.setattr(database, "get_session_maker", lambda: reingest_env.env.maker)

    async def _no_error_log(*args, **kwargs):
        return None

    monkeypatch.setattr(documents, "log_backend_error", _no_error_log, raising=False)
    return module


async def test_reprocess_script_rebuilds_completed_documents_and_prints_no_text(
    script, reingest_env, capsys
) -> None:
    reingest_env.blocks = [TextBlock(f"Tenant {NAME} signed the lease.", "page", page=1)]
    env = reingest_env.env
    async with env.maker() as session:
        doc = await _new_document(session, "pdf")
        doc.ingestion_status = "completed"
        doc.entity_count = 9
        session.add(Chunk(document_id=doc.id, index=0, sanitized_text="old", raw_text="old", char_count=3))
        await session.commit()
        doc_id = doc.id

    failed = await script._reprocess_all()

    out = capsys.readouterr().out
    assert failed == 0
    assert str(doc_id) in out and "completed" in out
    assert NAME not in out and "lease" not in out and "Tenant" not in out
    chunks, rows, stored = await _read(env.maker)
    assert [c.raw_text for c in chunks] == [f"Tenant {NAME} signed the lease."]
    assert "old" not in [c.sanitized_text for c in chunks]
    assert stored.entity_count == 1 and stored.ingestion_status == "completed"
    _assert_masked(chunks, rows)


async def test_reprocess_script_counts_a_failure_and_prints_only_its_type(
    script, reingest_env, capsys
) -> None:
    reingest_env.error = ValueError(f"cannot read {NAME}")
    env = reingest_env.env
    async with env.maker() as session:
        doc = await _new_document(session, "pdf")
        doc.ingestion_status = "completed"
        await session.commit()

    failed = await script._reprocess_all()

    out = capsys.readouterr().out
    assert failed == 1
    assert "failed (ValueError)" in out
    assert NAME not in out
