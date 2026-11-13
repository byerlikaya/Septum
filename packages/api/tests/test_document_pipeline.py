from __future__ import annotations

"""Tests for the document pipeline chunk indexing behaviour."""

import re
from types import SimpleNamespace

import pytest

from septum_api.services.document_pipeline import DocumentPipeline
from septum_api.services.ingestion.base import IngestionResult, TextBlock


class _FakeVectorStore:
    """Capture calls to index_document for verification in tests."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, list[int], list[str]]] = []

    def index_document(
        self,
        document_id: int,
        chunk_ids: list[int],
        texts: list[str],
    ) -> None:
        self.calls.append((document_id, list(chunk_ids), list(texts)))


def test_index_chunks_uses_raw_texts_for_embeddings(monkeypatch: object) -> None:
    """
    Ensure that DocumentPipeline._index_chunks forwards the provided raw_texts
    to the underlying VectorStore, rather than reading from sanitized fields.
    """
    from septum_api.services import document_pipeline as dp

    fake_store = _FakeVectorStore()
    monkeypatch.setattr(dp, "VectorStore", lambda: fake_store)

    chunks = [SimpleNamespace(id=1), SimpleNamespace(id=2)]
    raw_texts = ["chunk one raw text", "chunk two raw text"]

    DocumentPipeline._index_chunks(
        document_id=42,
        chunks=chunks,
        raw_texts=raw_texts,
    )

    assert fake_store.calls == [
        (42, [1, 2], ["chunk one raw text", "chunk two raw text"])
    ]


async def test_run_records_entity_types_of_the_policy_it_sanitized_with(
    tmp_path, monkeypatch
) -> None:
    """The snapshot is the policy handed to the sanitizer: regulations plus custom types."""
    from contextlib import asynccontextmanager

    from septum_api.database import build_default_app_settings
    from septum_api.models import Base
    from septum_api.models.document import Document
    from septum_api.models.regulation import CustomRecognizer, RegulationRuleset
    from septum_api.services import document_pipeline as dp
    from septum_api.services.policy_snapshot import build_policy_snapshot
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pipe.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(bind=engine, expire_on_commit=False)

    class _Lock:
        async def ensure_held(self) -> None:
            return None

    @asynccontextmanager
    async def _map_lock(document_id):
        yield _Lock()

    async def _noop(*args, **kwargs):
        return None

    used_policies = []
    used_ollama = []

    async def _create_sanitizer(db, settings, enable_ollama=None, policy=None):
        used_policies.append(policy)
        used_ollama.append(enable_ollama)

    monkeypatch.setattr(dp, "create_sanitizer", _create_sanitizer)
    monkeypatch.setattr(dp, "document_map_lock", _map_lock)
    monkeypatch.setattr(dp, "set_document_map", _noop)
    monkeypatch.setattr(dp, "replace_index_for_document", _noop)
    monkeypatch.setattr(dp, "recompute_relationships_for_document", _noop)
    try:
        async with maker() as session:
            session.add_all([
                RegulationRuleset(
                    id="reg_a", display_name="A", region="X",
                    entity_types=["PHONE_NUMBER", "EMAIL_ADDRESS"], is_active=True,
                ),
                RegulationRuleset(
                    id="reg_b", display_name="B", region="X",
                    entity_types=["DATE"], is_active=False,
                ),
                CustomRecognizer(
                    name="emp", entity_type="EMPLOYEE_ID", detection_method="regex",
                    pattern=r"E\d+", placeholder_label="EMPLOYEE_ID", is_active=True,
                ),
            ])
            doc = Document(
                filename="f", original_filename="f.pdf", file_type="application/pdf",
                file_format="pdf", detected_language="en", encrypted_path="/x",
                file_size_bytes=1, active_regulation_ids=["reg_a"],
                ingestion_status="processing",
            )
            session.add(doc)
            await session.commit()

            await dp.DocumentPipeline(settings=build_default_app_settings()).run(
                db=session, document=doc, file_format="pdf",
                ingestion_result=IngestionResult(text="", metadata={}),
            )

        async with maker() as session:
            stored = await session.get(Document, doc.id)
            recorded, digest = stored.sanitized_entity_types, stored.sanitized_policy_digest
    finally:
        await engine.dispose()

    from septum_api.services.sanitizer_factory import DOCUMENT_SANITIZER_ENABLE_OLLAMA

    assert [sorted(p.entity_types) for p in used_policies] == [recorded]
    assert recorded == ["EMAIL_ADDRESS", "EMPLOYEE_ID", "PHONE_NUMBER"]
    assert used_ollama == [DOCUMENT_SANITIZER_ENABLE_OLLAMA]
    assert digest == build_policy_snapshot(
        [["PHONE_NUMBER", "EMAIL_ADDRESS"]],
        [("EMPLOYEE_ID", "regex", r"E\d+", None, None, [])],
    ).digest


def test_index_chunks_bm25_passes_the_masking_language(monkeypatch: object) -> None:
    """The BM25 index is tokenized in the language the document was masked in."""
    from septum_api.services import document_pipeline as dp

    seen: dict[str, str] = {}

    class _FakeRetriever:
        def index_document(self, document_id, chunk_ids, texts, language="en") -> None:
            seen["language"] = language

    monkeypatch.setattr(dp, "BM25Retriever", _FakeRetriever)

    DocumentPipeline._index_chunks_bm25(1, [SimpleNamespace(id=1)], ["x"], "tr")

    assert seen["language"] == "tr"


def test_ingestion_bm25_index_records_the_document_language(tmp_path, monkeypatch) -> None:
    """The index written at ingestion carries the masking language for its queries."""
    from septum_api.services import document_pipeline as dp
    from septum_api.services.bm25_retriever import BM25Retriever

    monkeypatch.setattr(dp, "BM25Retriever", lambda: BM25Retriever(base_dir=tmp_path))

    DocumentPipeline._index_chunks_bm25(7, [SimpleNamespace(id=1)], ["İŞVEREN"], "tr")

    assert BM25Retriever(base_dir=tmp_path)._load_index(7)["language"] == "tr"


NAME = "Ayşe Yılmazoğlu"
NAME_FRAGMENTS = ("Ayşe", "Yılmaz", "şe Y", "oğlu")


async def _ingest(
    tmp_path,
    monkeypatch,
    blocks,
    file_format="image",
    chunk_records=None,
    index_calls=None,
    sanitizer=None,
    on_failure=None,
    before_run=None,
    **settings,
):
    """Run the real pipeline (real chunker, projection and SQLite) with a pattern detector.

    Returns the stored chunks and detection rows in index order. Indexing and
    the map store are stubbed; nothing else is. ``sanitizer`` replaces the
    pattern detector (for example with the real core detector). When the run
    raises, ``on_failure(session, document_id, exc)`` is awaited in its
    session instead of the error propagating. ``before_run(session, document)``
    is awaited after the document row exists and before the run starts.
    """
    from contextlib import asynccontextmanager

    from sanitizer_doubles import PatternDetector, literal
    from septum_api.database import build_default_app_settings
    from septum_api.models import Base
    from septum_api.models.document import Chunk, Document
    from septum_api.models.entity_detection import EntityDetection
    from septum_api.services import document_pipeline as dp
    from septum_api.services.ingestion.base import join_blocks
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'accept.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(bind=engine, expire_on_commit=False)

    class _Lock:
        async def ensure_held(self) -> None:
            return None

    @asynccontextmanager
    async def _map_lock(document_id):
        yield _Lock()

    async def _noop(*args, **kwargs):
        return None

    detector = sanitizer or PatternDetector(literal(NAME, "PERSON_NAME"))

    async def _create_sanitizer(db, settings, enable_ollama=None, policy=None):
        return detector

    monkeypatch.setattr(dp, "create_sanitizer", _create_sanitizer)
    monkeypatch.setattr(dp, "document_map_lock", _map_lock)
    monkeypatch.setattr(dp, "set_document_map", _noop)
    monkeypatch.setattr(dp, "replace_index_for_document", _noop)
    monkeypatch.setattr(dp, "recompute_relationships_for_document", _noop)

    async def _record_index(self, document_id, chunks, raw_texts, language, emit):
        index_calls.append((document_id, [c.id for c in chunks], list(raw_texts)))

    monkeypatch.setattr(
        dp.DocumentPipeline, "_index_document", _noop if index_calls is None else _record_index
    )
    if chunk_records is not None:
        monkeypatch.setattr(
            dp.DocumentPipeline, "_chunk_records",
            lambda self, fmt, blks, protected: chunk_records(join_blocks(blks)),
        )
    app_settings = build_default_app_settings()
    for key, value in settings.items():
        setattr(app_settings, key, value)
    try:
        async with maker() as session:
            doc = Document(
                filename="f", original_filename="f", file_type="x", file_format=file_format,
                detected_language="tr", encrypted_path="/x", file_size_bytes=1,
                ingestion_status="processing",
            )
            session.add(doc)
            await session.commit()
            doc_id = doc.id
            if before_run is not None:
                await before_run(session, doc)
            try:
                await dp.DocumentPipeline(settings=app_settings).run(
                    db=session, document=doc, file_format=file_format,
                    ingestion_result=IngestionResult(
                        text=join_blocks(blocks), metadata={}, blocks=list(blocks)
                    ),
                )
            except Exception as exc:
                if on_failure is None:
                    raise
                await on_failure(session, doc_id, exc)
        async with maker() as session:
            chunks = list(
                (await session.execute(select(Chunk).order_by(Chunk.index))).scalars()
            )
            rows = list(
                (await session.execute(select(EntityDetection).order_by(EntityDetection.id))).scalars()
            )
            stored = await session.get(Document, doc_id)
    finally:
        await engine.dispose()
    if sanitizer is None:
        assert detector.calls[0][0] == "detect"
        assert [c[0] for c in detector.calls] == ["detect", "apply"]
    return chunks, rows, stored


def _assert_no_raw_name(*texts) -> None:
    for text in texts:
        if text:
            assert NAME not in text
            assert not any(fragment in text for fragment in NAME_FRAGMENTS)


async def test_name_across_an_old_chunk_boundary_is_masked_whole_in_both_chunks(
    tmp_path, monkeypatch
) -> None:
    from septum_api.services.chunking import ChunkRecord

    text = "x" * 95 + " " + NAME + " " + "y" * 100
    cut = text.index(NAME) + 6

    def cut_through_the_name(joined):
        return [
            ChunkRecord(index=0, start=0, end=cut, text=joined[:cut], strategy="sliding"),
            ChunkRecord(index=1, start=cut, end=len(joined), text=joined[cut:], strategy="sliding"),
        ]

    chunks, rows, stored = await _ingest(
        tmp_path, monkeypatch, [TextBlock(text, "text")], chunk_records=cut_through_the_name
    )

    assert [c.raw_text for c in chunks] == [text[:cut], text[cut:]]
    for chunk in chunks:
        _assert_no_raw_name(chunk.sanitized_text)
        assert "[PERSON_NAME_1]" in chunk.sanitized_text
    assert stored.entity_count == 1
    assert [(r.chunk_id, r.placeholder) for r in rows] == [
        (chunks[0].id, "[PERSON_NAME_1]"), (chunks[1].id, "[PERSON_NAME_1]")
    ]


async def test_masked_language_list_is_stored_dominant_first_then_by_share(
    tmp_path, monkeypatch
) -> None:
    """The list round-trips through the database: dominant first, then the order detection reported.

    The share order comes from the detector double (``echoed_segment_languages``),
    so this pins storage and order keeping; the core tests pin the ordering.
    """
    from septum_api.utils import db_helpers

    def candidates(sample):
        if "合同" in sample:
            return [("zh-cn", 0.99)]
        if "Hello" in sample:
            return [("en", 0.99)]
        return [("tr", 0.99)]

    monkeypatch.setattr(db_helpers, "langdetect_candidates", candidates)
    turkish = "Sözleşme taraflar arasındaki yükümlülükleri ayrıntılı olarak düzenler. " * 8
    english = "Hello, this clause sets out the obligations of both parties in detail. " * 8
    chinese = "本合同由双方根据平等自愿的原则订立，双方应当严格履行合同约定的各项义务。" * 5
    blocks = [
        TextBlock(turkish, "paragraph"),
        TextBlock(chinese, "paragraph"),
        TextBlock(english, "paragraph"),
    ]

    _, _, stored = await _ingest(tmp_path, monkeypatch, blocks)

    assert stored.sanitized_language == "tr"
    assert stored.sanitized_languages == ["tr", "en", "zh"]


async def test_chunker_never_cuts_inside_a_detected_name(tmp_path, monkeypatch) -> None:
    text = " ".join(["filler words here"] * 5) + " " + NAME + " " + "tail text " * 20

    chunks, _, _ = await _ingest(
        tmp_path, monkeypatch, [TextBlock(text, "text")], chunk_size=100, chunk_overlap=0
    )

    assert len(chunks) > 1
    assert sum(NAME in c.raw_text for c in chunks) == 1
    for chunk in chunks:
        _assert_no_raw_name(chunk.sanitized_text)


async def test_overlapping_chunks_share_one_placeholder_and_count_once(
    tmp_path, monkeypatch
) -> None:
    text = "lorem ipsum " * 6 + NAME + " " + "dolor sit amet " * 12

    chunks, rows, stored = await _ingest(
        tmp_path, monkeypatch, [TextBlock(text, "text")], chunk_size=100, chunk_overlap=50
    )

    holding = [c for c in chunks if NAME in c.raw_text]
    assert len(holding) >= 2
    for chunk in chunks:
        _assert_no_raw_name(chunk.sanitized_text)
    assert all("[PERSON_NAME_1]" in c.sanitized_text for c in holding)
    assert stored.entity_count == 1
    assert len(rows) == len(holding)


async def test_batched_insert_assigns_ids_that_detection_rows_and_index_use_in_order(
    tmp_path, monkeypatch
) -> None:
    text = "lorem ipsum " * 6 + NAME + " " + "dolor sit amet " * 12
    index_calls: list = []

    chunks, rows, stored = await _ingest(
        tmp_path, monkeypatch, [TextBlock(text, "text")],
        index_calls=index_calls, chunk_size=100, chunk_overlap=50,
    )

    ids = [c.id for c in chunks]
    assert len(chunks) > 2
    assert None not in ids and len(set(ids)) == len(ids)
    assert ids == sorted(ids)
    assert [c.index for c in chunks] == list(range(len(chunks)))
    assert index_calls == [(stored.id, ids, [c.raw_text for c in chunks])]
    holders = [c.id for c in chunks if NAME in c.raw_text]
    assert len(holders) >= 2
    assert [r.chunk_id for r in rows] == holders


async def test_persist_chunks_assigns_ids_in_input_order_on_sqlite(tmp_path) -> None:
    from septum_api.database import build_default_app_settings
    from septum_api.models import Base
    from septum_api.models.document import Chunk, Document
    from septum_api.services import document_pipeline as dp
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'persist.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        async with maker() as session:
            doc = Document(
                filename="f", original_filename="f", file_type="x", file_format="txt",
                detected_language="en", encrypted_path="/x", file_size_bytes=1,
                ingestion_status="processing",
            )
            session.add(doc)
            await session.commit()
            rows = [
                Chunk(document_id=doc.id, index=i, sanitized_text=f"s{i}", raw_text=f"r{i}", char_count=2)
                for i in range(25)
            ]
            returned = await dp.DocumentPipeline(settings=build_default_app_settings())._persist_chunks(
                session, rows
            )
            assert returned == rows
            ids = [c.id for c in returned]
            assert None not in ids and ids == sorted(ids) and len(set(ids)) == 25
            assert [c.raw_text for c in returned] == [f"r{i}" for i in range(25)]
        async with maker() as session:
            stored = (await session.execute(select(Chunk).order_by(Chunk.id))).scalars().all()
            assert [c.id for c in stored] == ids
            assert [c.raw_text for c in stored] == [f"r{i}" for i in range(25)]
    finally:
        await engine.dispose()


async def test_detection_offsets_align_with_the_stored_raw_chunk_text(
    tmp_path, monkeypatch
) -> None:
    blocks = [
        TextBlock(f"Tenant {NAME} signed.", "paragraph", page=1),
        TextBlock("Plain paragraph without names. " * 4, "paragraph", page=1),
        TextBlock(f"Witness: {NAME}.", "paragraph", page=2),
    ]

    chunks, rows, stored = await _ingest(
        tmp_path, monkeypatch, blocks, file_format="pdf", pdf_chunk_size=800
    )

    by_id = {c.id: c for c in chunks}
    assert rows
    for row in rows:
        assert by_id[row.chunk_id].raw_text[row.start_offset : row.end_offset] == NAME
    preview = "\n\n".join(c.raw_text for c in chunks)
    assert stored.transcription_text == preview
    assert [c.source_page for c in chunks] == sorted(c.source_page for c in chunks)
    assert stored.entity_count == 2


async def test_section_titles_and_sheet_names_never_persist_raw(tmp_path, monkeypatch) -> None:
    from septum_api.services.ingestion.sheet_text import render_sheet

    grid = [["h1", "h2"]] + [[f"row {r}", f"value {r}"] for r in range(40)]
    sheet_blocks, _, _ = render_sheet(NAME, grid)
    other_blocks, _, _ = render_sheet("Totals", grid)

    chunks, _, _ = await _ingest(
        tmp_path, monkeypatch, sheet_blocks + other_blocks, file_format="xlsx",
        spreadsheet_chunk_size=20,
    )

    assert len(chunks) > 2
    for chunk in chunks:
        _assert_no_raw_name(chunk.sanitized_text, chunk.section_title)
        assert isinstance(chunk.source_sheet, int)
    assert {c.source_sheet for c in chunks} == {1, 2}
    first = [c for c in chunks if c.source_sheet == 1]
    assert all(c.section_title and "[PERSON_NAME_1]" in c.section_title for c in first)


async def test_heading_title_holding_a_name_is_stored_masked(tmp_path, monkeypatch) -> None:
    blocks = [
        TextBlock(f"Kiracı {NAME}", "heading"),
        *[TextBlock("Sözleşme maddesi metni burada devam eder. " * 3, "paragraph") for _ in range(16)],
    ]

    chunks, _, _ = await _ingest(
        tmp_path, monkeypatch, blocks, file_format="docx", pdf_chunk_size=800
    )

    assert len(chunks) > 1
    for chunk in chunks:
        _assert_no_raw_name(chunk.sanitized_text, chunk.section_title)
        assert chunk.section_title == "Kiracı [PERSON_NAME_1]"


async def test_pipeline_logs_never_carry_document_text(tmp_path, monkeypatch, caplog) -> None:
    import logging

    blocks = [TextBlock(f"Tenant {NAME} signed the lease. " * 10, "text")]

    with caplog.at_level(logging.DEBUG):
        await _ingest(tmp_path, monkeypatch, blocks, chunk_size=100, chunk_overlap=20)

    for record in caplog.records:
        message = record.getMessage()
        _assert_no_raw_name(message)
        assert "signed the lease" not in message


@pytest.mark.parametrize(
    ("file_format", "expected_size"), [("pdf", 1200), ("docx", 1200), ("image", 300), ("txt", 300)]
)
def test_structured_and_sliding_chunks_follow_the_char_settings(
    file_format: str, expected_size: int
) -> None:
    words = "a bb ccc dddd eeeee ffffff ggggggg hhhhhhhh iiiiiiiii jjjjjjjjjj " * 200
    pipeline = DocumentPipeline(
        SimpleNamespace(chunk_size=300, chunk_overlap=60, pdf_chunk_size=1200)
    )

    chunks = pipeline._chunk_records(file_format, [TextBlock(words, "text")], [])

    assert len(chunks) > 5
    assert all(len(c.text) <= expected_size for c in chunks)
    assert max(len(c.text) for c in chunks) > expected_size * 0.8
    gaps = [chunks[i].end - chunks[i + 1].start for i in range(len(chunks) - 1)]
    assert all(0 <= gap <= 60 for gap in gaps)
    assert sum(gaps) / len(gaps) > 30


def test_rows_are_normalized_one_by_one_and_their_offsets_rebuilt() -> None:
    from septum_api.services import document_pipeline as dp

    block = TextBlock("ab\ncd\nef", "rows", sheet="S", row_offsets=[0, 3, 6])

    [normalized] = dp._normalized_blocks([block], lambda text: text.replace("c", "ccc"))

    assert normalized.text == "ab\ncccd\nef"
    assert list(normalized.row_offsets) == [0, 3, 8]
    assert normalized.sheet == "S"


class _NameFinder:
    """Stand-in for a token-classification pipeline: tags the names it is told to."""

    def __init__(self, names: dict[str, str]) -> None:
        self._names = names

    def __call__(self, text: str) -> list[dict]:
        return [
            {"entity_group": label, "score": 0.99, "start": m.start(), "end": m.end()}
            for name, label in self._names.items()
            for m in re.finditer(re.escape(name), text)
        ]


def _segment_registry(emits: dict[str, dict[str, str]]):
    """Real model-set planning, one stub pipeline per model id; no model is downloaded."""
    from septum_core.ner_model_registry import NERModelRegistry

    class _Registry(NERModelRegistry):
        def get_pipelines_for_models(self, model_ids):
            return [_NameFinder(emits.get(model_id, {})) for model_id in model_ids]

    return _Registry(_overrides={})


MINORITY_NAME = "Brown Smith"
DOMINANT_NAME = "Ayse Demir"


@pytest.mark.parametrize("form", ["NFC", "NFD"])
async def test_run_with_the_real_detector_masks_a_minority_language_name(
    tmp_path, monkeypatch, form
) -> None:
    """Two-language blocks, ingested as composed or decomposed text, through the core detector."""
    import unicodedata

    from septum_core.config import SeptumCoreConfig
    from septum_core.detector import Detector
    from septum_core.ner_model_registry import NERModelRegistry
    from septum_api.utils import db_helpers

    monkeypatch.setattr(
        db_helpers,
        "langdetect_candidates",
        lambda sample: [("en", 0.99)] if "committee" in sample else [("tr", 0.99)],
    )
    dominant_models = NERModelRegistry().model_ids("tr")
    minority_models = NERModelRegistry().model_ids("en")
    detector = Detector(
        SeptumCoreConfig(use_presidio_layer=False, use_ner_layer=True),
        ner_registry=_segment_registry({
            dominant_models[0]: {DOMINANT_NAME: "PER"},
            minority_models[0]: {MINORITY_NAME: "PER"},
        }),
    )
    turkish = "Komite yıllık bütçeyi ayrıntılı olarak inceledi. " * 10 + f"Sayin {DOMINANT_NAME} katildi. "
    english = (
        "The committee reviewed the annual budget in detail. " * 10
        + f"Mr {MINORITY_NAME} attended the meeting. "
    )
    blocks = [
        TextBlock(unicodedata.normalize(form, turkish), "paragraph"),
        TextBlock(unicodedata.normalize(form, english), "paragraph"),
    ]

    chunks, rows, stored = await _ingest(tmp_path, monkeypatch, blocks, sanitizer=detector)

    assert stored.ingestion_status == "completed"
    assert stored.sanitized_language == "tr"
    assert stored.sanitized_languages == ["tr", "en"]
    for chunk in chunks:
        assert MINORITY_NAME not in chunk.sanitized_text
        assert DOMINANT_NAME not in chunk.sanitized_text
    assert any(MINORITY_NAME in chunk.raw_text for chunk in chunks)
    assert stored.entity_count == 2


_NESTED = "Zorblaxquin"


def _nested_blocks(levels: int) -> list[TextBlock]:
    """A document naming ``levels`` organisations, each name a prefix of the next one."""
    return [TextBlock(f"Paid by {_NESTED + 'q' * i}.", "paragraph") for i in range(levels)]


def _nested_detector():
    """Detects every name of :func:`_nested_blocks` whole."""
    from sanitizer_doubles import PatternDetector

    return PatternDetector((re.compile(_NESTED + "q*"), "ORGANIZATION"))


def _recording_map_store(monkeypatch) -> list:
    """Replace the map store with one that records every map handed to it."""
    from septum_api.services import document_pipeline as dp

    stored_maps: list = []

    async def _store(db, document_id, anon_map) -> None:
        stored_maps.append(anon_map)

    monkeypatch.setattr(dp, "_store_document_map", _store)
    return stored_maps


async def test_a_map_nesting_past_the_cap_fails_the_document_with_its_code(
    tmp_path, monkeypatch, caplog
) -> None:
    """Refused like an over-long text: coded, content-free, and nothing derived is kept."""
    import logging

    from septum_api.models.error_log import ErrorLog
    from septum_api.routers import documents
    from septum_api.services import pii_gate
    from sqlalchemy import select

    stored_maps = _recording_map_store(monkeypatch)
    failures: list = []
    logged: list = []

    async def _record(session, document_id, exc) -> None:
        failures.append(exc)
        await documents.record_background_failure(session, document_id, exc, user_id=1)
        logged.extend((await session.execute(select(ErrorLog))).scalars())

    with caplog.at_level(logging.DEBUG):
        chunks, rows, stored = await _ingest(
            tmp_path, monkeypatch, _nested_blocks(pii_gate._MAX_FACTORED_DEPTH + 1),
            sanitizer=_nested_detector(), on_failure=_record,
        )

    assert [type(exc).__name__ for exc in failures] == ["OriginalsPatternTooDeepError"]
    assert stored.ingestion_status == "failed"
    assert stored.ingestion_error_code == "originals_pattern_too_deep"
    assert stored.ingestion_error == str(failures[0])
    assert chunks == [] and rows == [] and stored_maps == []
    assert stored.transcription_text is None
    assert (stored.chunk_count, stored.entity_count) == (0, 0)
    assert stored.sanitized_entity_types is None and stored.sanitized_policy_digest is None
    assert stored.sanitized_language is None and stored.sanitized_languages is None
    assert stored.entity_index_version is None
    assert logged
    for text in (
        stored.ingestion_error,
        *(record.getMessage() for record in caplog.records),
        *(f"{entry.message} {entry.stack_trace} {entry.extra}" for entry in logged),
    ):
        assert _NESTED not in text


async def test_a_map_nesting_exactly_to_the_cap_is_ingested(tmp_path, monkeypatch) -> None:
    from septum_api.services import pii_gate

    stored_maps = _recording_map_store(monkeypatch)

    chunks, rows, stored = await _ingest(
        tmp_path, monkeypatch, _nested_blocks(pii_gate._MAX_FACTORED_DEPTH),
        sanitizer=_nested_detector(),
    )

    assert stored.ingestion_status == "completed"
    assert stored.ingestion_error_code is None
    assert len(stored_maps) == 1
    assert len(stored_maps[0].entity_map) == pii_gate._MAX_FACTORED_DEPTH
    assert chunks and stored.entity_count == pii_gate._MAX_FACTORED_DEPTH
    for chunk in chunks:
        assert _NESTED not in chunk.sanitized_text


async def test_the_depth_check_runs_once_on_the_stored_map_and_compiles_nothing(
    tmp_path, monkeypatch
) -> None:
    from septum_api.services import document_pipeline as dp
    from septum_api.services import pii_gate

    stored_maps = _recording_map_store(monkeypatch)
    checked: list = []
    compiles: list = []
    real_check = dp.ensure_originals_searchable

    def _spied_check(anon_map) -> None:
        real_compile = pii_gate.re.compile

        def _counting_compile(*args, **kwargs):
            compiles.append(args[0])
            return real_compile(*args, **kwargs)

        pii_gate.re.compile = _counting_compile
        try:
            real_check(anon_map)
        finally:
            pii_gate.re.compile = real_compile
        checked.append(anon_map)

    monkeypatch.setattr(dp, "ensure_originals_searchable", _spied_check)

    _, _, stored = await _ingest(tmp_path, monkeypatch, [TextBlock(f"Tenant {NAME} signed.", "text")])

    assert stored.ingestion_status == "completed"
    assert len(checked) == 1 and checked[0] is stored_maps[0]
    assert NAME in checked[0].entity_map
    assert compiles == []


async def test_a_refused_reprocess_leaves_no_index_rows_relationships_or_version(
    tmp_path, monkeypatch
) -> None:
    """A good document's old index goes with its chunks when the rebuild is refused."""
    from septum_api.models.document import Document
    from septum_api.models.document_relationship import DocumentRelationship
    from septum_api.models.entity_index import EntityIndex
    from septum_api.request_limits import MAX_PRE_APPROVED_CHUNK_CHARS
    from septum_api.routers import documents
    from septum_api.services.entity_index_service import ENTITY_INDEX_VERSION
    from sanitizer_doubles import PatternDetector, literal
    from sqlalchemy import select

    neighbour_id = 0
    seen: dict = {}

    async def _before(session, doc) -> None:
        nonlocal neighbour_id
        neighbour = Document(
            filename="n", original_filename="n", file_type="x", file_format="image",
            detected_language="tr", encrypted_path="/n", file_size_bytes=1,
            ingestion_status="completed", entity_index_version=ENTITY_INDEX_VERSION,
        )
        session.add(neighbour)
        doc.ingestion_status = "completed"
        doc.entity_index_version = ENTITY_INDEX_VERSION
        await session.commit()
        neighbour_id = neighbour.id
        for owner in (doc.id, neighbour_id):
            session.add(EntityIndex(
                document_id=owner, entity_type="IBAN", value_hash="h" * 64, occurrences=1
            ))
        session.add(DocumentRelationship(
            doc_a_id=doc.id, doc_b_id=neighbour_id, score=1.0, shared_entity_count=1,
            shared_entity_types="{}",
        ))
        await session.commit()
        assert await documents.claim_for_reingest(session, doc.id)
        await session.refresh(doc)

    async def _record(session, document_id, exc) -> None:
        await documents.record_background_failure(session, document_id, exc, user_id=1)
        seen["code"] = (await session.get(Document, document_id)).ingestion_error_code
        seen["index"] = [
            r.document_id for r in (await session.execute(select(EntityIndex))).scalars()
        ]
        seen["pairs"] = list((await session.execute(select(DocumentRelationship))).scalars())

    _, _, stored = await _ingest(
        tmp_path, monkeypatch, [TextBlock("Alice " * 7000, "text")],
        sanitizer=PatternDetector(literal("Alice", "PERSON_NAME")),
        on_failure=_record, before_run=_before,
        chunk_size=MAX_PRE_APPROVED_CHUNK_CHARS, chunk_overlap=0,
    )

    assert seen["code"] == "chunk_too_large"
    assert stored.ingestion_status == "failed"
    assert stored.entity_index_version is None
    assert seen["index"] == [neighbour_id]
    assert seen["pairs"] == []


async def test_a_failure_after_the_index_commit_drops_the_run_index_with_the_failed_status(
    tmp_path, monkeypatch
) -> None:
    """A failed run cannot stay routable: its committed index goes in the failure transaction."""
    from septum_api.models.document import Document
    from septum_api.models.document_relationship import DocumentRelationship
    from septum_api.models.entity_index import EntityIndex
    from septum_api.routers import documents
    from septum_api.services import document_pipeline as dp
    from septum_api.services.entity_index_service import ENTITY_INDEX_VERSION
    from sanitizer_doubles import PatternDetector, literal
    from sqlalchemy import select

    neighbour_id = 0
    seen: dict = {}

    async def _before(session, doc) -> None:
        nonlocal neighbour_id
        neighbour = Document(
            filename="n", original_filename="n", file_type="x", file_format="image",
            detected_language="tr", encrypted_path="/n", file_size_bytes=1,
            ingestion_status="completed", entity_index_version=ENTITY_INDEX_VERSION,
        )
        other = Document(
            filename="o", original_filename="o", file_type="x", file_format="image",
            detected_language="tr", encrypted_path="/o", file_size_bytes=1,
            ingestion_status="completed", entity_index_version=ENTITY_INDEX_VERSION,
        )
        session.add_all([neighbour, other])
        await session.commit()
        neighbour_id = neighbour.id
        session.add_all([
            EntityIndex(
                document_id=neighbour_id, entity_type="IBAN", value_hash="h" * 64, occurrences=1
            ),
            DocumentRelationship(
                doc_a_id=neighbour_id, doc_b_id=other.id, score=1.0, shared_entity_count=1,
                shared_entity_types="{}",
            ),
        ])
        await session.commit()

    async def _index_then_commit(db, document_id, anon_map) -> bool:
        db.add(EntityIndex(
            document_id=document_id, entity_type="IBAN", value_hash="a" * 64, occurrences=1
        ))
        db.add(DocumentRelationship(
            doc_a_id=document_id, doc_b_id=neighbour_id, score=1.0, shared_entity_count=1,
            shared_entity_types="{}",
        ))
        await db.commit()
        return True

    async def _fail(self, *args, **kwargs):
        raise RuntimeError("embedding step failed")

    monkeypatch.setattr(dp, "_refresh_entity_index", _index_then_commit)
    monkeypatch.setattr(dp.DocumentPipeline, "_clear_refused_override", _fail)

    async def _record(session, document_id, exc) -> None:
        assert isinstance(exc, RuntimeError)
        await documents.record_background_failure(session, document_id, exc, user_id=1)
        seen["index"] = sorted(
            r.document_id for r in (await session.execute(select(EntityIndex))).scalars()
        )
        seen["pairs"] = [
            (r.doc_a_id, r.doc_b_id)
            for r in (await session.execute(select(DocumentRelationship))).scalars()
        ]

    _, _, stored = await _ingest(
        tmp_path, monkeypatch, [TextBlock("Alice text", "text")],
        sanitizer=PatternDetector(literal("Alice", "PERSON_NAME")),
        on_failure=_record, before_run=_before,
    )

    assert stored.ingestion_status == "failed"
    assert stored.entity_index_version is None
    assert seen["index"] == [neighbour_id]
    assert seen["pairs"] == [(neighbour_id, seen["pairs"][0][1])]
    assert all(stored.id not in pair for pair in seen["pairs"])


async def test_a_failing_index_drop_still_leaves_the_document_failed_not_processing(
    tmp_path, monkeypatch
) -> None:
    from septum_api.routers import documents

    async def _broken_drop(db, document_id) -> int:
        raise RuntimeError("drop failed")

    monkeypatch.setattr(documents, "drop_index_for_document", _broken_drop)

    async def _record(session, document_id, exc) -> None:
        await documents.record_background_failure(session, document_id, exc, user_id=1)

    async def _fail(self, *args, **kwargs):
        raise RuntimeError("embedding step failed")

    from septum_api.services import document_pipeline as dp

    monkeypatch.setattr(dp.DocumentPipeline, "_clear_refused_override", _fail)

    _, _, stored = await _ingest(
        tmp_path, monkeypatch, [TextBlock("Alice text", "text")], on_failure=_record
    )

    assert stored.ingestion_status == "failed"
    assert stored.ingestion_error
