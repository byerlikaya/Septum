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
                run_token=None,
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
    is awaited after the document row exists and before the run starts; a dict
    it returns is passed on to ``run`` as keyword arguments (for example ``run_token``).
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
    monkeypatch.setenv("VECTOR_INDEX_DIR", str(tmp_path / "faiss"))
    monkeypatch.setenv("BM25_INDEX_DIR", str(tmp_path / "bm25"))

    class _Lock:
        async def ensure_held(self) -> None:
            return None

    @asynccontextmanager
    async def _map_lock(document_id, **kwargs):
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
            run_kwargs = {"run_token": None}
            if before_run is not None:
                run_kwargs.update(await before_run(session, doc) or {})
            try:
                await dp.DocumentPipeline(settings=app_settings).run(
                    db=session, document=doc, file_format=file_format,
                    ingestion_result=IngestionResult(
                        text=join_blocks(blocks), metadata={}, blocks=list(blocks)
                    ),
                    **run_kwargs,
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

    async def _store(db, document_id, anon_map, run_token=None) -> None:
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
        await documents.record_background_failure(
            session, document_id, exc, user_id=1, run_token=None
        )
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

    async def _before(session, doc) -> dict:
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
        seen["token"] = await documents.claim_for_reingest(session, doc.id)
        assert seen["token"]
        await session.refresh(doc)
        return {"run_token": seen["token"]}

    async def _record(session, document_id, exc) -> None:
        await documents.record_background_failure(
            session, document_id, exc, user_id=1, run_token=seen["token"]
        )
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

    async def _index_then_commit(db, document, anon_map, *, run_token) -> bool:
        document_id = document.id
        document.entity_index_version = ENTITY_INDEX_VERSION
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
        await documents.record_background_failure(
            session, document_id, exc, user_id=1, run_token=None
        )
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
        await documents.record_background_failure(
            session, document_id, exc, user_id=1, run_token=None
        )

    async def _fail(self, *args, **kwargs):
        raise RuntimeError("embedding step failed")

    from septum_api.services import document_pipeline as dp

    monkeypatch.setattr(dp.DocumentPipeline, "_clear_refused_override", _fail)

    _, _, stored = await _ingest(
        tmp_path, monkeypatch, [TextBlock("Alice text", "text")], on_failure=_record
    )

    assert stored.ingestion_status == "failed"
    assert stored.ingestion_error


_SENTINEL = "Quorvexdaltine"


def _index_paths(tmp_path, document_id: int):
    """The FAISS and BM25 index files of ``document_id`` under the test's index dirs."""
    from septum_api.services.bm25_retriever import BM25Retriever
    from septum_api.services.vector_store import VectorStore

    return (
        VectorStore(base_dir=tmp_path / "faiss")._index_path(document_id),
        BM25Retriever(base_dir=tmp_path / "bm25")._index_path(document_id),
    )


def _assert_content_free(caplog, tmp_path) -> None:
    for record in caplog.records:
        message = record.getMessage()
        assert _SENTINEL not in message
        assert str(tmp_path) not in message


async def test_a_failure_after_the_artifacts_are_written_purges_every_derived_copy(
    tmp_path, monkeypatch, caplog
) -> None:
    """Chunks, detections, text, counts, policy, map and indexes go; source, status and audit stay."""
    import logging

    from httpx import ASGITransport, AsyncClient
    from septum_api.database import get_db
    from septum_api.main import app
    from septum_api.models.audit_event import AuditEvent
    from septum_api.models.user import User
    from septum_api.routers import documents
    from septum_api.services import document_pipeline as dp
    from septum_api.services.document_anon_store import get_document_map, set_document_map
    from septum_api.utils.auth_dependency import get_current_user
    from sanitizer_doubles import PatternDetector, literal
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    source = tmp_path / "source.enc"
    source.write_bytes(b"ciphertext")
    seen: dict = {}

    async def _index_for_real(self, document_id, chunks, raw_texts, language, emit) -> None:
        dp.DocumentPipeline._index_chunks_bm25(document_id, chunks, raw_texts, language)
        _index_paths(tmp_path, document_id)[0].write_bytes(b"index")

    async def _before(session, doc) -> None:
        doc.encrypted_path = str(source)
        await session.commit()
        monkeypatch.setattr(dp, "set_document_map", set_document_map)
        monkeypatch.setattr(dp.DocumentPipeline, "_index_document", _index_for_real)

    async def _fail(self, *args, **kwargs):
        raise RuntimeError("embedding step failed")

    monkeypatch.setattr(dp.DocumentPipeline, "_clear_refused_override", _fail)

    async def _audit_events(session, document_id) -> list:
        rows = await session.execute(
            select(AuditEvent.event_type).where(AuditEvent.document_id == document_id)
        )
        return list(rows.scalars())

    async def _record(session, document_id, exc) -> None:
        seen["stored"] = (
            await get_document_map(document_id) is not None,
            *(path.exists() for path in _index_paths(tmp_path, document_id)),
        )
        seen["audit_before"] = await _audit_events(session, document_id)
        await documents.record_background_failure(
            session, document_id, exc, user_id=1, run_token=None
        )
        seen["map"] = await get_document_map(document_id)
        seen["audit_after"] = await _audit_events(session, document_id)

    with caplog.at_level(logging.DEBUG):
        chunks, rows, stored = await _ingest(
            tmp_path, monkeypatch, [TextBlock(f"Alice {_SENTINEL} text", "text")],
            sanitizer=PatternDetector(literal("Alice", "PERSON_NAME")),
            on_failure=_record, before_run=_before,
        )

    assert seen["stored"] == (True, True, True)
    assert stored.ingestion_status == "failed"
    assert stored.ingestion_error and stored.ingestion_error_code is None
    assert chunks == [] and rows == []
    assert stored.transcription_text is None
    assert (stored.chunk_count, stored.entity_count) == (0, 0)
    assert stored.sanitized_entity_types is None and stored.sanitized_policy_digest is None
    assert stored.sanitized_language is None and stored.sanitized_languages is None
    assert seen["map"] is None
    assert not any(path.exists() for path in _index_paths(tmp_path, stored.id))
    assert source.exists()
    assert "pii_detected" in seen["audit_before"]
    assert seen["audit_after"] == seen["audit_before"]
    _assert_content_free(caplog, tmp_path)

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'accept.db'}")
    maker = async_sessionmaker(bind=engine, expire_on_commit=False)

    async def _db():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: User(
        id=1, email="admin@example.com", hashed_password="x", role="admin"
    )
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/chunks", params={"document_id": stored.id})
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        await engine.dispose()
    assert response.status_code == 200
    assert response.json()["items"] == []


async def test_a_refusal_before_anything_was_stored_purges_nothing(tmp_path, monkeypatch) -> None:
    from contextlib import asynccontextmanager

    from septum_api.routers import documents
    from septum_api.services import document_pipeline as dp
    from septum_api.services import pii_gate

    _recording_map_store(monkeypatch)
    erased: list = []
    locked: list = []

    async def _erase(*args, **kwargs) -> None:
        erased.append(args)

    monkeypatch.setattr(dp, "erase_document_artifacts", _erase)

    async def _before(session, doc) -> None:
        inner = dp.document_map_lock

        @asynccontextmanager
        async def _spied_lock(document_id, **kwargs):
            locked.append(document_id)
            async with inner(document_id, **kwargs) as handle:
                yield handle

        monkeypatch.setattr(dp, "document_map_lock", _spied_lock)

    async def _record(session, document_id, exc) -> None:
        await documents.record_background_failure(
            session, document_id, exc, user_id=1, run_token=None
        )

    _, _, stored = await _ingest(
        tmp_path, monkeypatch, _nested_blocks(pii_gate._MAX_FACTORED_DEPTH + 1),
        sanitizer=_nested_detector(), on_failure=_record, before_run=_before,
    )

    assert stored.ingestion_status == "failed"
    assert stored.ingestion_error_code == "originals_pattern_too_deep"
    assert erased == [] and locked == []


async def test_a_failed_artifact_erasure_keeps_the_rows_as_the_sweep_marker(
    tmp_path, monkeypatch, caplog
) -> None:
    import logging

    from septum_api.routers import documents
    from septum_api.services import document_pipeline as dp

    async def _erase(*args, **kwargs) -> None:
        raise dp.ArtifactErasureError(
            "vector_index", PermissionError(13, "denied", str(tmp_path / _SENTINEL))
        )

    monkeypatch.setattr(dp, "erase_document_artifacts", _erase)

    async def _fail(self, *args, **kwargs):
        raise RuntimeError("embedding step failed")

    monkeypatch.setattr(dp.DocumentPipeline, "_clear_refused_override", _fail)

    async def _record(session, document_id, exc) -> None:
        await documents.record_background_failure(
            session, document_id, exc, user_id=1, run_token=None
        )

    with caplog.at_level(logging.DEBUG):
        chunks, rows, stored = await _ingest(
            tmp_path, monkeypatch, [TextBlock(f"Alice {_SENTINEL} text", "text")],
            on_failure=_record,
        )

    assert stored.ingestion_status == "failed"
    assert stored.ingestion_error
    assert chunks and stored.transcription_text
    assert "PermissionError" in caplog.text
    _assert_content_free(caplog, tmp_path)


async def _sweep_db(tmp_path, monkeypatch):
    """An isolated SQLite store with index dirs under ``tmp_path``; returns ``(engine, maker)``."""
    from septum_api.models import Base
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    monkeypatch.setenv("VECTOR_INDEX_DIR", str(tmp_path / "faiss"))
    monkeypatch.setenv("BM25_INDEX_DIR", str(tmp_path / "bm25"))
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'sweep.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(bind=engine, expire_on_commit=False)


async def _seed_derived(maker, tmp_path, status: str) -> int:
    """A document in ``status`` holding a chunk, a detection, text, map, both indexes and a source."""
    from septum_api.models.document import Chunk, Document
    from septum_api.models.entity_detection import EntityDetection
    from septum_api.services.anonymization_map import AnonymizationMap
    from septum_api.services.bm25_retriever import BM25Retriever
    from septum_api.services.document_anon_store import set_document_map

    async with maker() as session:
        source = tmp_path / f"source-{status}.enc"
        source.write_bytes(b"ciphertext")
        doc = Document(
            filename="f", original_filename="f", file_type="x", file_format="pdf",
            detected_language="en", encrypted_path=str(source), file_size_bytes=1,
            ingestion_status=status, ingestion_error="boom" if status == "failed" else None,
            transcription_text=f"Alice {_SENTINEL}", chunk_count=1, entity_count=1,
            sanitized_entity_types=["PERSON_NAME"], sanitized_policy_digest="d",
            sanitized_language="en", sanitized_languages=["en"],
        )
        session.add(doc)
        await session.flush()
        chunk = Chunk(
            document_id=doc.id, index=0, sanitized_text="[PERSON_NAME_1] x",
            raw_text=f"Alice {_SENTINEL}", char_count=17,
        )
        session.add(chunk)
        await session.flush()
        session.add(EntityDetection(
            document_id=doc.id, chunk_id=chunk.id, entity_type="PERSON_NAME",
            placeholder="[PERSON_NAME_1]", start_offset=0, end_offset=5, score=1.0,
        ))
        await session.commit()
        doc_id = doc.id
    await set_document_map(doc_id, AnonymizationMap(document_id=doc_id, language="en"))
    BM25Retriever(base_dir=tmp_path / "bm25").index_document(doc_id, [1], [f"Alice {_SENTINEL}"])
    _index_paths(tmp_path, doc_id)[0].write_bytes(b"index")
    return doc_id


async def _derived_state(maker, tmp_path, document_id: int) -> dict:
    """What a document still holds: rows, text, counts, policy, map, indexes and source."""
    from pathlib import Path

    from septum_api.models.document import Chunk, Document
    from septum_api.models.entity_detection import EntityDetection
    from septum_api.services.document_anon_store import get_document_map
    from sqlalchemy import func, select

    async with maker() as session:
        doc = await session.get(Document, document_id)
        counts = [
            (await session.execute(
                select(func.count()).select_from(model).where(model.document_id == document_id)
            )).scalar_one()
            for model in (Chunk, EntityDetection)
        ]
    return {
        "status": doc.ingestion_status,
        "rows": counts,
        "text": doc.transcription_text,
        "counts": (doc.chunk_count, doc.entity_count),
        "policy": (
            doc.sanitized_entity_types, doc.sanitized_policy_digest,
            doc.sanitized_language, doc.sanitized_languages,
        ),
        "map": await get_document_map(document_id) is not None,
        "indexes": [path.exists() for path in _index_paths(tmp_path, document_id)],
        "source": Path(doc.encrypted_path).exists(),
        "error": doc.ingestion_error,
    }


def _assert_purged(state: dict) -> None:
    assert state["status"] == "failed"
    assert state["rows"] == [0, 0]
    assert state["text"] is None
    assert state["counts"] == (0, 0)
    assert state["policy"] == (None, None, None, None)
    assert state["map"] is False
    assert state["indexes"] == [False, False]
    assert state["source"] is True
    assert state["error"]


def _assert_untouched(state: dict) -> None:
    assert state["rows"] == [1, 1]
    assert state["text"] == f"Alice {_SENTINEL}"
    assert state["counts"] == (1, 1)
    assert state["map"] is True
    assert state["indexes"] == [True, True]


async def test_the_startup_sweep_purges_failed_documents_once_and_spares_the_rest(
    tmp_path, monkeypatch, caplog
) -> None:
    import logging

    from septum_api.services import document_pipeline as dp

    engine, maker = await _sweep_db(tmp_path, monkeypatch)
    erased: list = []
    real_erase = dp.erase_document_artifacts

    async def _counting_erase(document_id, **kwargs) -> None:
        erased.append(document_id)
        await real_erase(document_id, **kwargs)

    monkeypatch.setattr(dp, "erase_document_artifacts", _counting_erase)
    try:
        failed = await _seed_derived(maker, tmp_path, "failed")
        completed = await _seed_derived(maker, tmp_path, "completed")
        processing = await _seed_derived(maker, tmp_path, "processing")
        with caplog.at_level(logging.DEBUG):
            assert await dp.purge_failed_documents(maker) == 1
            assert erased == [failed]
            assert await dp.purge_failed_documents(maker) == 0
        assert erased == [failed]
        _assert_purged(await _derived_state(maker, tmp_path, failed))
        _assert_untouched(await _derived_state(maker, tmp_path, completed))
        _assert_untouched(await _derived_state(maker, tmp_path, processing))
        _assert_content_free(caplog, tmp_path)
    finally:
        await engine.dispose()


async def test_the_sweep_skips_a_document_reprocessed_after_it_was_selected(
    tmp_path, monkeypatch
) -> None:
    from septum_api.models.document import Document
    from septum_api.services import document_pipeline as dp
    from sqlalchemy import update

    engine, maker = await _sweep_db(tmp_path, monkeypatch)
    real_select = dp._failed_ids_with_derived_data

    async def _select_then_reprocess(session_maker) -> list:
        ids = await real_select(session_maker)
        async with session_maker() as session:
            await session.execute(
                update(Document).where(Document.id.in_(ids)).values(ingestion_status="processing")
            )
            await session.commit()
        return ids

    monkeypatch.setattr(dp, "_failed_ids_with_derived_data", _select_then_reprocess)
    try:
        failed = await _seed_derived(maker, tmp_path, "failed")
        assert await dp.purge_failed_documents(maker) == 0
        _assert_untouched(await _derived_state(maker, tmp_path, failed))
    finally:
        await engine.dispose()


async def test_one_failing_erasure_does_not_stop_the_sweep(tmp_path, monkeypatch, caplog) -> None:
    import logging

    from septum_api.services import document_pipeline as dp

    engine, maker = await _sweep_db(tmp_path, monkeypatch)
    real_erase = dp.erase_document_artifacts
    stuck: list = []

    async def _erase(document_id, **kwargs) -> None:
        if document_id == stuck[0]:
            raise dp.ArtifactErasureError("bm25_index", PermissionError(13, "denied", str(tmp_path)))
        await real_erase(document_id, **kwargs)

    monkeypatch.setattr(dp, "erase_document_artifacts", _erase)
    try:
        stuck.append(await _seed_derived(maker, tmp_path, "failed"))
        other = await _seed_derived(maker, tmp_path, "failed")
        with caplog.at_level(logging.DEBUG):
            assert await dp.purge_failed_documents(maker) == 1
        _assert_purged(await _derived_state(maker, tmp_path, other))
        assert (await _derived_state(maker, tmp_path, stuck[0]))["rows"] == [1, 1]
        _assert_content_free(caplog, tmp_path)
    finally:
        await engine.dispose()


async def test_an_interrupted_run_leaves_no_derived_data_after_reset_and_sweep(
    tmp_path, monkeypatch
) -> None:
    from septum_api import database, main
    from septum_api.services import document_pipeline as dp

    engine, maker = await _sweep_db(tmp_path, monkeypatch)
    monkeypatch.setattr(database, "get_session_maker", lambda: maker)
    try:
        interrupted = await _seed_derived(maker, tmp_path, "processing")
        await main._reset_orphaned_processing_docs()
        assert await dp.purge_failed_documents(maker) == 1
        _assert_purged(await _derived_state(maker, tmp_path, interrupted))
    finally:
        await engine.dispose()


async def test_the_startup_task_runs_each_step_even_when_an_earlier_one_fails(
    monkeypatch, caplog
) -> None:
    import logging

    from septum_api import main
    from septum_api.services import document_pipeline as dp
    from septum_api.services import entity_index_refresh, entity_index_service

    ran: list = []

    def _step(name: str, fail: bool):
        async def _run(session_maker) -> int:
            ran.append(name)
            if fail:
                raise OSError(13, "denied", f"/secret/{_SENTINEL}")
            return 0

        return _run

    monkeypatch.setattr(main, "get_session_maker", lambda: object())
    monkeypatch.setattr(
        entity_index_service, "drop_index_of_failed_documents", _step("drop", True)
    )
    monkeypatch.setattr(dp, "purge_failed_documents", _step("purge", True))
    monkeypatch.setattr(entity_index_refresh, "reindex_stale_documents", _step("rebuild", False))

    with caplog.at_level(logging.DEBUG):
        await main._reindex_entity_index_once()

    assert ran == ["drop", "purge", "rebuild"]
    assert caplog.text.count("PermissionError") == 2
    assert _SENTINEL not in caplog.text


def _recording_lock(events: list, *, enter_error=None, held_error=None):
    """A map lock double that records each enter (with its wait), ``ensure_held`` and exit."""
    from contextlib import asynccontextmanager

    class _Handle:
        async def ensure_held(self) -> None:
            events.append(("ensure_held",))
            if held_error is not None:
                raise held_error

    @asynccontextmanager
    async def _lock(document_id, **kwargs):
        events.append(("enter", document_id, kwargs.get("wait_seconds")))
        if enter_error is not None:
            raise enter_error
        try:
            yield _Handle()
        finally:
            events.append(("exit", document_id))

    return _lock


def _fail_at_the_override_step(monkeypatch) -> None:
    from septum_api.services import document_pipeline as dp

    async def _fail(self, *args, **kwargs):
        raise RuntimeError("embedding step failed")

    monkeypatch.setattr(dp.DocumentPipeline, "_clear_refused_override", _fail)


async def test_the_failure_handler_erases_under_a_short_held_lock_without_the_reply_cache(
    tmp_path, monkeypatch
) -> None:
    from septum_api.routers import documents
    from septum_api.services import document_pipeline as dp
    from septum_api.services.document_anon_store import SWEEP_LOCK_WAIT_SECONDS

    events: list = []

    async def _erase(document_id, **kwargs) -> None:
        events.append(("erase", document_id, kwargs))

    monkeypatch.setattr(dp, "erase_document_artifacts", _erase)
    _fail_at_the_override_step(monkeypatch)

    async def _record(session, document_id, exc) -> None:
        monkeypatch.setattr(dp, "document_map_lock", _recording_lock(events))
        await documents.record_background_failure(
            session, document_id, exc, user_id=1, run_token=None
        )

    chunks, rows, stored = await _ingest(
        tmp_path, monkeypatch, [TextBlock(f"{NAME} text", "text")], on_failure=_record,
    )

    assert events == [
        ("enter", stored.id, SWEEP_LOCK_WAIT_SECONDS),
        ("ensure_held",),
        ("erase", stored.id, {"clear_reply_cache": False}),
        ("exit", stored.id),
    ]
    assert stored.ingestion_status == "failed"
    assert chunks == [] and rows == []


@pytest.mark.parametrize(
    "lock_kwargs, error_class",
    [
        ({"enter_error": "timeout"}, "DocumentMapLockTimeout"),
        ({"held_error": "lost"}, "DocumentMapLockLost"),
    ],
)
async def test_a_lock_that_times_out_or_is_lost_erases_nothing_and_keeps_the_rows(
    tmp_path, monkeypatch, caplog, lock_kwargs, error_class
) -> None:
    import logging

    from septum_api.services import document_pipeline as dp
    from septum_api.services.document_anon_store import (
        DocumentMapLockLost,
        DocumentMapLockTimeout,
    )

    errors = {
        "timeout": DocumentMapLockTimeout(f"lock {tmp_path / _SENTINEL}"),
        "lost": DocumentMapLockLost(f"lock {tmp_path / _SENTINEL}"),
    }
    events: list = []
    erased: list = []

    async def _erase(document_id, **kwargs) -> None:
        erased.append(document_id)

    engine, maker = await _sweep_db(tmp_path, monkeypatch)
    monkeypatch.setattr(dp, "erase_document_artifacts", _erase)
    monkeypatch.setattr(
        dp, "document_map_lock",
        _recording_lock(events, **{key: errors[value] for key, value in lock_kwargs.items()}),
    )
    try:
        failed = await _seed_derived(maker, tmp_path, "failed")
        with caplog.at_level(logging.DEBUG):
            async with maker() as session:
                assert await dp.purge_failed_derived_data(session, failed) is False
        assert erased == []
        state = await _derived_state(maker, tmp_path, failed)
        _assert_untouched(state)
        assert (state["status"], state["error"]) == ("failed", "boom")
        assert error_class in caplog.text
        _assert_content_free(caplog, tmp_path)
    finally:
        await engine.dispose()


async def test_the_sweep_erases_without_the_reply_cache_and_clears_it_once_only_after_an_erasure(
    tmp_path, monkeypatch
) -> None:
    from septum_api.services import document_pipeline as dp

    engine, maker = await _sweep_db(tmp_path, monkeypatch)
    real_erase = dp.erase_document_artifacts
    calls: list = []
    cleared: list = []
    stuck: list = []

    async def _erase(document_id, **kwargs) -> None:
        calls.append((document_id, kwargs))
        if document_id in stuck:
            raise dp.ArtifactErasureError("bm25_index", PermissionError(13, "denied"))
        await real_erase(document_id, **kwargs)

    async def _clear() -> None:
        cleared.append(True)

    monkeypatch.setattr(dp, "erase_document_artifacts", _erase)
    monkeypatch.setattr(dp, "clear_detection_cache", _clear)
    try:
        first = await _seed_derived(maker, tmp_path, "failed")
        second = await _seed_derived(maker, tmp_path, "failed")
        assert await dp.purge_failed_documents(maker) == 2
        assert calls == [
            (first, {"clear_reply_cache": False}),
            (second, {"clear_reply_cache": False}),
        ]
        assert cleared == [True]

        assert await dp.purge_failed_documents(maker) == 0
        assert cleared == [True]

        stuck.append(await _seed_derived(maker, tmp_path, "failed"))
        assert await dp.purge_failed_documents(maker) == 0
        assert calls[-1] == (stuck[0], {"clear_reply_cache": False})
        assert cleared == [True]
    finally:
        await engine.dispose()


async def test_a_completed_document_is_never_purged_directly(tmp_path, monkeypatch) -> None:
    from septum_api.services import document_pipeline as dp

    engine, maker = await _sweep_db(tmp_path, monkeypatch)
    try:
        completed = await _seed_derived(maker, tmp_path, "completed")
        async with maker() as session:
            assert await dp.purge_failed_derived_data(session, completed) is False
        state = await _derived_state(maker, tmp_path, completed)
        _assert_untouched(state)
        assert state["status"] == "completed"
    finally:
        await engine.dispose()


@pytest.mark.parametrize("new_status", ["processing", "completed"])
async def test_the_row_purge_skips_a_document_claimed_after_its_artifacts_were_erased(
    tmp_path, monkeypatch, new_status
) -> None:
    """The status re-read under the lock passes; only the UPDATE's guard is left to stop the purge."""
    from septum_api.models.document import Document
    from septum_api.services import document_pipeline as dp
    from sqlalchemy import update

    engine, maker = await _sweep_db(tmp_path, monkeypatch)
    erased: list = []

    async def _erase_then_claim(document_id, **kwargs) -> None:
        erased.append(document_id)
        async with maker() as other:
            await other.execute(
                update(Document)
                .where(Document.id == document_id)
                .values(ingestion_status=new_status)
            )
            await other.commit()

    monkeypatch.setattr(dp, "erase_document_artifacts", _erase_then_claim)
    try:
        failed = await _seed_derived(maker, tmp_path, "failed")
        async with maker() as session:
            assert await dp.purge_failed_derived_data(session, failed) is False
        assert erased == [failed]
        state = await _derived_state(maker, tmp_path, failed)
        assert state["status"] == new_status
        _assert_untouched(state)
        assert state["policy"] == (["PERSON_NAME"], "d", "en", ["en"])
    finally:
        await engine.dispose()


async def test_a_reingest_claim_resets_every_derived_column(tmp_path, monkeypatch) -> None:
    from septum_api.models.document import Document
    from septum_api.routers import documents
    from septum_api.services import document_pipeline as dp

    assert set(dp.DERIVED_DATA_RESET) == {
        "chunk_count", "entity_count", "transcription_text", "sanitized_entity_types",
        "sanitized_policy_digest", "sanitized_language", "sanitized_languages",
    }
    engine, maker = await _sweep_db(tmp_path, monkeypatch)
    try:
        completed = await _seed_derived(maker, tmp_path, "completed")
        async with maker() as session:
            seeded = await session.get(Document, completed)
            for column, reset in dp.DERIVED_DATA_RESET.items():
                assert getattr(seeded, column) != reset, column
        async with maker() as session:
            token = await documents.claim_for_reingest(session, completed)
            assert isinstance(token, str) and token
        async with maker() as session:
            claimed = await session.get(Document, completed)
            for column, reset in dp.DERIVED_DATA_RESET.items():
                assert getattr(claimed, column) == reset, column
            assert claimed.ingestion_status == "processing"
    finally:
        await engine.dispose()


def _leaked_error() -> OSError:
    return PermissionError(13, "denied", f"/secret/{_SENTINEL}")


def _break_candidate_select(monkeypatch, session) -> None:
    from septum_api.services import document_pipeline as dp

    def _raise():
        raise _leaked_error()

    monkeypatch.setattr(dp, "_holds_derived_data", _raise)


def _break_erasure(monkeypatch, session) -> None:
    from septum_api.services import document_pipeline as dp

    async def _raise(document_id, **kwargs) -> None:
        raise _leaked_error()

    monkeypatch.setattr(dp, "erase_document_artifacts", _raise)


def _break_row_purge(monkeypatch, session) -> None:
    from septum_api.services import document_pipeline as dp

    async def _raise(db, document_ids):
        raise _leaked_error()

    monkeypatch.setattr(dp, "_purge_failed_rows", _raise)


def _break_commit(monkeypatch, session) -> None:
    async def _raise() -> None:
        raise _leaked_error()

    monkeypatch.setattr(session, "commit", _raise)


def _break_rollback(monkeypatch, session) -> None:
    async def _raise() -> None:
        raise _leaked_error()

    monkeypatch.setattr(session, "rollback", _raise)


@pytest.mark.parametrize(
    "breakage",
    [_break_candidate_select, _break_erasure, _break_row_purge, _break_commit, _break_rollback],
)
async def test_a_failing_purge_step_never_raises_nor_changes_the_failure(
    tmp_path, monkeypatch, caplog, breakage
) -> None:
    import logging

    from septum_api.services import document_pipeline as dp

    engine, maker = await _sweep_db(tmp_path, monkeypatch)
    try:
        failed = await _seed_derived(maker, tmp_path, "failed")
        with caplog.at_level(logging.DEBUG):
            async with maker() as session:
                breakage(monkeypatch, session)
                assert await dp.purge_failed_derived_data(session, failed) is False
        state = await _derived_state(maker, tmp_path, failed)
        assert (state["status"], state["error"]) == ("failed", "boom")
        assert state["rows"] == [1, 1] and state["text"] == f"Alice {_SENTINEL}"
        assert "PermissionError" in caplog.text
        assert _SENTINEL not in caplog.text
    finally:
        await engine.dispose()


async def test_a_failing_batch_row_purge_in_the_sweep_is_logged_not_raised(
    tmp_path, monkeypatch, caplog
) -> None:
    import logging

    from septum_api.services import document_pipeline as dp

    engine, maker = await _sweep_db(tmp_path, monkeypatch)
    try:
        failed = await _seed_derived(maker, tmp_path, "failed")
        _break_row_purge(monkeypatch, None)
        with caplog.at_level(logging.DEBUG):
            assert await dp.purge_failed_documents(maker) == 0
        state = await _derived_state(maker, tmp_path, failed)
        assert (state["status"], state["rows"]) == ("failed", [1, 1])
        assert "PermissionError" in caplog.text and _SENTINEL not in caplog.text
    finally:
        await engine.dispose()


async def _strip_rows(maker, document_id: int) -> None:
    """Leave only the stored map and index files, as a reprocess whose cleanup failed does."""
    from septum_api.models.document import Chunk, Document
    from septum_api.models.entity_detection import EntityDetection
    from septum_api.services.document_pipeline import DERIVED_DATA_RESET
    from sqlalchemy import delete, update

    async with maker() as session:
        await session.execute(delete(EntityDetection).where(EntityDetection.document_id == document_id))
        await session.execute(delete(Chunk).where(Chunk.document_id == document_id))
        await session.execute(
            update(Document).where(Document.id == document_id).values(**DERIVED_DATA_RESET)
        )
        await session.commit()


async def test_the_sweep_purges_a_failed_document_that_kept_only_its_files(
    tmp_path, monkeypatch
) -> None:
    from septum_api.services import document_pipeline as dp

    engine, maker = await _sweep_db(tmp_path, monkeypatch)
    try:
        leftover = await _seed_derived(maker, tmp_path, "failed")
        await _strip_rows(maker, leftover)
        completed = await _seed_derived(maker, tmp_path, "completed")
        await _strip_rows(maker, completed)
        before = await _derived_state(maker, tmp_path, leftover)
        assert before["rows"] == [0, 0] and before["map"] is True
        assert before["indexes"] == [True, True]

        assert await dp._failed_ids_with_derived_data(maker) == [leftover]
        assert await dp.purge_failed_documents(maker) == 1
        _assert_purged(await _derived_state(maker, tmp_path, leftover))
        assert await dp._failed_ids_with_derived_data(maker) == []
        assert await dp.purge_failed_documents(maker) == 0

        kept = await _derived_state(maker, tmp_path, completed)
        assert kept["status"] == "completed"
        assert kept["map"] is True and kept["indexes"] == [True, True]
    finally:
        await engine.dispose()


async def test_the_sweep_lists_no_stored_files_when_no_document_failed(
    tmp_path, monkeypatch
) -> None:
    from septum_api.services import document_pipeline as dp

    engine, maker = await _sweep_db(tmp_path, monkeypatch)
    listed: list = []

    async def _listing() -> set:
        listed.append(True)
        return set()

    monkeypatch.setattr(dp, "_stored_artifact_ids", _listing)
    try:
        await _seed_derived(maker, tmp_path, "completed")
        assert await dp.purge_failed_documents(maker) == 0
        assert listed == []
    finally:
        await engine.dispose()


async def test_a_failing_file_listing_still_counts_the_other_tiers(
    tmp_path, monkeypatch, caplog
) -> None:
    import logging

    from septum_api.services import document_pipeline as dp

    engine, maker = await _sweep_db(tmp_path, monkeypatch)

    def _broken_listing() -> set:
        raise _leaked_error()

    monkeypatch.setattr(dp, "disk_map_document_ids", _broken_listing)
    try:
        leftover = await _seed_derived(maker, tmp_path, "failed")
        await _strip_rows(maker, leftover)
        with caplog.at_level(logging.DEBUG):
            assert await dp._failed_ids_with_derived_data(maker) == [leftover]
        assert "tier=anonymization_map error=PermissionError" in caplog.text
        assert _SENTINEL not in caplog.text
    finally:
        await engine.dispose()


async def _flip_status_before_completion(monkeypatch, status: str, error: str) -> None:
    """Make another session set ``status`` on the document just before the run's final write."""
    from septum_api.models.document import Document
    from septum_api.services import document_pipeline as dp
    from sqlalchemy import update
    from sqlalchemy.ext.asyncio import AsyncSession

    real_clear = dp.DocumentPipeline._clear_refused_override

    async def _flip_then_clear(db, document, text, judged=None) -> None:
        async with AsyncSession(db.bind) as other:
            await other.execute(
                update(Document)
                .where(Document.id == document.id)
                .values(ingestion_status=status, ingestion_error=error)
            )
            await other.commit()
        await real_clear(db, document, text, judged=judged)

    monkeypatch.setattr(dp.DocumentPipeline, "_clear_refused_override", staticmethod(_flip_then_clear))


async def test_a_run_reset_to_failed_mid_run_never_ends_completed(tmp_path, monkeypatch) -> None:
    from septum_api.routers import documents
    from septum_api.services import document_pipeline as dp

    restart_error = "Processing interrupted by server restart. Please reprocess."
    await _flip_status_before_completion(monkeypatch, "failed", restart_error)
    raised: list = []

    async def _record(session, document_id, exc) -> None:
        raised.append(type(exc))
        await documents.record_background_failure(
            session, document_id, exc, user_id=1, run_token=None
        )

    chunks, rows, stored = await _ingest(
        tmp_path, monkeypatch, [TextBlock(f"{NAME} text", "text")], on_failure=_record,
    )

    assert raised == [dp.DocumentRunSupersededError]
    assert stored.ingestion_status == "failed"
    assert stored.ingestion_error == restart_error
    assert stored.sanitized_policy_digest is None and stored.sanitized_language is None
    assert chunks == [] and rows == [] and stored.transcription_text is None


async def test_a_superseded_run_leaves_a_newer_runs_status_and_data_alone(
    tmp_path, monkeypatch
) -> None:
    from septum_api.routers import documents
    from septum_api.services import document_pipeline as dp

    await _flip_status_before_completion(monkeypatch, "completed", None)
    erased: list = []
    raised: list = []

    async def _erase(document_id, **kwargs) -> None:
        erased.append(document_id)

    monkeypatch.setattr(dp, "erase_document_artifacts", _erase)

    async def _record(session, document_id, exc) -> None:
        raised.append(type(exc))
        await documents.record_background_failure(
            session, document_id, exc, user_id=1, run_token=None
        )

    chunks, rows, stored = await _ingest(
        tmp_path, monkeypatch, [TextBlock(f"{NAME} text", "text")], on_failure=_record,
    )

    assert raised == [dp.DocumentRunSupersededError]
    assert stored.ingestion_status == "completed"
    assert stored.ingestion_error is None
    assert chunks and stored.transcription_text
    assert erased == []


def _name_detector():
    from sanitizer_doubles import PatternDetector, literal

    return PatternDetector(literal(NAME, "PERSON_NAME"))


def _maker_of(session):
    """A session maker on the engine of ``session``: a second, independent session."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    return async_sessionmaker(bind=session.bind, expire_on_commit=False)


async def _restart_and_claim(maker, monkeypatch, document_id: int) -> str:
    """Another worker restarts (every ``processing`` row goes ``failed``), then a reprocess claims.

    Returns the run token the claim handed out, which the claiming run passes
    as ``run_token`` to everything it writes.
    """
    from septum_api import database, main
    from septum_api.routers import documents

    monkeypatch.setattr(database, "get_session_maker", lambda: maker)
    await main._reset_orphaned_processing_docs()
    async with maker() as other:
        token = await documents.claim_for_reingest(other, document_id)
    assert isinstance(token, str) and token, "a claim must return its run token"
    return token


async def _run_with_token(maker, document_id: int, text: str, token):
    """Run the real pipeline for ``token`` in its own session; returns the error it raised, if any."""
    from septum_api.database import build_default_app_settings
    from septum_api.models.document import Document
    from septum_api.services import document_pipeline as dp

    async with maker() as session:
        document = await session.get(Document, document_id)
        try:
            await dp.DocumentPipeline(settings=build_default_app_settings()).run(
                db=session,
                document=document,
                file_format="image",
                ingestion_result=IngestionResult(
                    text=text, metadata={}, blocks=[TextBlock(text, "text")]
                ),
                run_token=token,
            )
        except Exception as exc:
            return exc
    return None


async def _row_state(maker, document_id: int) -> dict:
    from septum_api.models.document import Chunk, Document
    from sqlalchemy import select

    async with maker() as session:
        document = await session.get(Document, document_id)
        chunk_texts = list(
            (
                await session.execute(
                    select(Chunk.sanitized_text).where(Chunk.document_id == document_id)
                )
            ).scalars()
        )
    return {
        "status": document.ingestion_status,
        "error": document.ingestion_error,
        "token": document.ingestion_run_id,
        "detected_language": document.detected_language,
        "ocr_confidence": document.ocr_confidence,
        "text": document.transcription_text,
        "chunk_count": document.chunk_count,
        "chunks": chunk_texts,
    }


def _before_mask(monkeypatch, action) -> None:
    """Await ``action()`` in the run, after detection input is ready and before any chunk is stored."""
    from septum_api.services import document_pipeline as dp

    real = dp.DocumentPipeline._mask_document

    async def _hooked(self, *args, **kwargs):
        await action()
        return await real(self, *args, **kwargs)

    monkeypatch.setattr(dp.DocumentPipeline, "_mask_document", _hooked)


def _before_completion(monkeypatch, action) -> None:
    """Await ``action()`` in the run after its chunks, map and indexes are written, before completion."""
    from septum_api.services import document_pipeline as dp

    real = dp.DocumentPipeline._clear_refused_override

    async def _hooked(db, document, text, judged=None) -> None:
        await action()
        await real(db, document, text, judged=judged)

    monkeypatch.setattr(dp.DocumentPipeline, "_clear_refused_override", staticmethod(_hooked))


async def test_a_run_superseded_by_an_orphan_reset_and_a_newer_claim_is_refused_and_the_newer_completes(
    tmp_path, monkeypatch
) -> None:
    from septum_api.services import document_pipeline as dp

    seen: dict = {}

    async def _before(session, doc) -> dict:
        maker = _maker_of(session)
        token_a = await _restart_and_claim(maker, monkeypatch, doc.id)
        token_b = await _restart_and_claim(maker, monkeypatch, doc.id)
        assert token_a != token_b
        seen["a_error"] = await _run_with_token(maker, doc.id, f"{NAME} A_STALE", token_a)
        seen["after_a"] = await _row_state(maker, doc.id)
        seen["token_b"] = token_b
        return {"run_token": token_b}

    chunks, _rows, stored = await _ingest(
        tmp_path, monkeypatch, [TextBlock(f"{NAME} B_TEXT", "text")],
        sanitizer=_name_detector(), before_run=_before,
    )

    assert type(seen["a_error"]) is dp.DocumentRunSupersededError
    assert seen["after_a"]["status"] == "processing"
    assert seen["after_a"]["token"] == seen["token_b"]
    assert seen["after_a"]["chunks"] == [] and seen["after_a"]["text"] is None
    assert stored.ingestion_status == "completed"
    assert "B_TEXT" in stored.transcription_text and "A_STALE" not in stored.transcription_text
    assert chunks and all("B_TEXT" in c.sanitized_text for c in chunks)
    assert not any("A_STALE" in c.sanitized_text for c in chunks)
    assert stored.chunk_count == len(chunks)


async def test_a_stale_run_that_fails_leaves_the_newer_runs_status_and_data_alone(
    tmp_path, monkeypatch
) -> None:
    from septum_api.routers import documents
    from septum_api.services import document_pipeline as dp

    erased: list = []

    async def _erase(document_id, **kwargs) -> None:
        erased.append(document_id)

    monkeypatch.setattr(dp, "erase_document_artifacts", _erase)
    ctx: dict = {}

    async def _stale_run_fails() -> None:
        async with ctx["maker"]() as other:
            await documents.record_background_failure(
                other, ctx["doc_id"], RuntimeError("stale run failed"), user_id=1,
                run_token=ctx["token_a"],
            )
        ctx["after_failure"] = await _row_state(ctx["maker"], ctx["doc_id"])

    _before_completion(monkeypatch, _stale_run_fails)

    async def _before(session, doc) -> dict:
        ctx.update(maker=_maker_of(session), doc_id=doc.id)
        ctx["token_a"] = await _restart_and_claim(ctx["maker"], monkeypatch, doc.id)
        ctx["token_b"] = await _restart_and_claim(ctx["maker"], monkeypatch, doc.id)
        return {"run_token": ctx["token_b"]}

    chunks, _rows, stored = await _ingest(
        tmp_path, monkeypatch, [TextBlock(f"{NAME} B_TEXT", "text")],
        sanitizer=_name_detector(), before_run=_before,
    )

    survived = ctx["after_failure"]
    assert survived["status"] == "processing" and survived["error"] is None
    assert survived["token"] == ctx["token_b"]
    assert survived["chunks"] and survived["text"] and survived["chunk_count"] > 0
    assert erased == []
    assert stored.ingestion_status == "completed" and chunks


async def test_a_stale_runs_chunk_commit_inserts_no_rows(tmp_path, monkeypatch) -> None:
    from septum_api.services import document_pipeline as dp

    ctx: dict = {}
    raised: list = []

    async def _newer_claim() -> None:
        ctx["token_b"] = await _restart_and_claim(ctx["maker"], monkeypatch, ctx["doc_id"])

    _before_mask(monkeypatch, _newer_claim)

    async def _before(session, doc) -> dict:
        ctx.update(maker=_maker_of(session), doc_id=doc.id)
        token_a = await _restart_and_claim(ctx["maker"], monkeypatch, doc.id)
        return {"run_token": token_a}

    async def _record(session, document_id, exc) -> None:
        raised.append(type(exc))

    chunks, rows, stored = await _ingest(
        tmp_path, monkeypatch, [TextBlock(f"{NAME} A_STALE", "text")],
        sanitizer=_name_detector(), before_run=_before, on_failure=_record,
    )

    assert raised == [dp.DocumentRunSupersededError]
    assert chunks == [] and rows == []
    assert stored.ingestion_status == "processing"
    assert stored.ingestion_run_id == ctx["token_b"]
    assert stored.transcription_text is None and stored.chunk_count == 0


async def test_a_stale_runs_document_map_store_is_refused(tmp_path, monkeypatch) -> None:
    from contextlib import asynccontextmanager

    from septum_api.services import document_pipeline as dp
    from septum_api.services.anonymization_map import AnonymizationMap

    class _Lock:
        async def ensure_held(self) -> None:
            return None

    @asynccontextmanager
    async def _map_lock(document_id, **kwargs):
        yield _Lock()

    stored_maps: list = []

    async def _set_map(document_id, anon_map) -> None:
        stored_maps.append(document_id)

    monkeypatch.setattr(dp, "document_map_lock", _map_lock)
    monkeypatch.setattr(dp, "set_document_map", _set_map)
    engine, maker = await _sweep_db(tmp_path, monkeypatch)
    try:
        document_id = await _seed_derived(maker, tmp_path, "processing")
        token_a = await _restart_and_claim(maker, monkeypatch, document_id)
        token_b = await _restart_and_claim(maker, monkeypatch, document_id)
        anon_map = AnonymizationMap(document_id=document_id, language="en")

        async with maker() as session:
            with pytest.raises(dp.DocumentRunSupersededError):
                await dp._store_document_map(session, document_id, anon_map, run_token=token_a)
        assert stored_maps == []

        async with maker() as session:
            await dp._store_document_map(session, document_id, anon_map, run_token=token_b)
        assert stored_maps == [document_id]
    finally:
        await engine.dispose()


async def test_a_run_without_a_token_completes_while_the_row_has_none(tmp_path, monkeypatch) -> None:
    async def _before(session, doc) -> dict:
        assert doc.ingestion_run_id is None
        return {"run_token": None}

    chunks, _rows, stored = await _ingest(
        tmp_path, monkeypatch, [TextBlock(f"{NAME} text", "text")],
        sanitizer=_name_detector(), before_run=_before,
    )

    assert stored.ingestion_status == "completed" and chunks
    assert stored.ingestion_run_id is None


async def test_a_run_without_a_token_is_refused_once_a_claim_set_one(tmp_path, monkeypatch) -> None:
    from septum_api.services import document_pipeline as dp

    raised: list = []
    ctx: dict = {}

    async def _before(session, doc) -> dict:
        maker = _maker_of(session)
        ctx["token_b"] = await _restart_and_claim(maker, monkeypatch, doc.id)
        return {"run_token": None}

    async def _record(session, document_id, exc) -> None:
        raised.append(type(exc))

    chunks, _rows, stored = await _ingest(
        tmp_path, monkeypatch, [TextBlock(f"{NAME} text", "text")],
        sanitizer=_name_detector(), before_run=_before, on_failure=_record,
    )

    assert raised == [dp.DocumentRunSupersededError]
    assert chunks == []
    assert stored.ingestion_status == "processing"
    assert stored.ingestion_run_id == ctx["token_b"]


class _SignalledGate:
    """A one-slot gate that reports the moment a task starts waiting on it."""

    def __init__(self) -> None:
        import asyncio

        self.waiting = asyncio.Event()
        self._slot = asyncio.Semaphore(1)

    async def acquire(self) -> None:
        await self._slot.acquire()

    def release(self) -> None:
        self._slot.release()

    async def __aenter__(self) -> None:
        self.waiting.set()
        await self._slot.acquire()

    async def __aexit__(self, *exc) -> None:
        self._slot.release()


async def test_an_upload_task_that_starts_after_a_newer_claim_is_refused_on_its_own_token(
    tmp_path, monkeypatch
) -> None:
    """The task waits on the semaphore while a newer claim lands; it must not adopt that claim's token.

    The stale task also read a language and an OCR confidence; neither may land on the newer claim's row.
    """
    import asyncio

    from septum_api import database
    from septum_api.database import build_default_app_settings
    from septum_api.routers import documents

    ctx: dict = {}
    gate = _SignalledGate()

    async def _settings(db):
        return build_default_app_settings()

    async def _stale_text(**kwargs) -> IngestionResult:
        text = f"{NAME} A_STALE"
        return IngestionResult(
            text=text, metadata={}, blocks=[TextBlock(text, "text")], confidence=0.91
        )

    async def _before(session, doc) -> dict:
        maker = _maker_of(session)
        monkeypatch.setattr(database, "get_session_maker", lambda: maker)
        monkeypatch.setattr(documents, "_INGESTION_SEMAPHORE", gate)
        monkeypatch.setattr(documents, "load_settings", _settings)
        monkeypatch.setattr(documents._INGESTION_ROUTER, "ingest", _stale_text)
        monkeypatch.setattr(documents, "_detect_document_language", lambda fmt, result: "zz")
        token_a = await _restart_and_claim(maker, monkeypatch, doc.id)
        source = documents._UploadedSource(tmp_path / "source.enc", "image/png", "image")

        await gate.acquire()
        task = asyncio.create_task(
            documents._run_upload_ingestion(doc.id, source, 1, run_token=token_a)
        )
        await asyncio.wait_for(gate.waiting.wait(), timeout=10)
        ctx["token_b"] = await _restart_and_claim(maker, monkeypatch, doc.id)
        ctx["before_a"] = await _row_state(maker, doc.id)
        gate.release()
        await task
        ctx["after_a"] = await _row_state(maker, doc.id)
        return {"run_token": ctx["token_b"]}

    chunks, _rows, stored = await _ingest(
        tmp_path, monkeypatch, [TextBlock(f"{NAME} B_TEXT", "text")],
        sanitizer=_name_detector(), before_run=_before,
    )

    after_a = ctx["after_a"]
    assert after_a["status"] == "processing" and after_a["token"] == ctx["token_b"]
    assert after_a["chunks"] == [] and after_a["text"] is None
    assert after_a["detected_language"] == ctx["before_a"]["detected_language"] != "zz"
    assert after_a["ocr_confidence"] == ctx["before_a"]["ocr_confidence"] != 0.91
    assert stored.ingestion_status == "completed"
    assert chunks and not any("A_STALE" in c.sanitized_text for c in chunks)


def _claiming_before(ctx: dict, monkeypatch):
    """A ``before_run`` that claims the document (run A) and hands the run its token."""

    async def _before(session, doc) -> dict:
        ctx.update(maker=_maker_of(session), doc_id=doc.id)
        ctx["token_a"] = await _restart_and_claim(ctx["maker"], monkeypatch, doc.id)
        return {"run_token": ctx["token_a"]}

    return _before


async def _land_newer_claim(ctx: dict, monkeypatch) -> None:
    ctx["token_b"] = await _restart_and_claim(ctx["maker"], monkeypatch, ctx["doc_id"])


def _record_into(raised: list):
    async def _record(session, document_id, exc) -> None:
        raised.append(type(exc))

    return _record


async def test_a_run_superseded_after_its_writes_is_not_marked_completed(
    tmp_path, monkeypatch
) -> None:
    from septum_api.services import document_pipeline as dp

    ctx: dict = {}
    raised: list = []
    _before_completion(monkeypatch, lambda: _land_newer_claim(ctx, monkeypatch))

    chunks, _rows, stored = await _ingest(
        tmp_path, monkeypatch, [TextBlock(f"{NAME} A_TEXT", "text")],
        sanitizer=_name_detector(), before_run=_claiming_before(ctx, monkeypatch),
        on_failure=_record_into(raised),
    )

    assert raised == [dp.DocumentRunSupersededError]
    assert stored.ingestion_status == "processing"
    assert stored.ingestion_run_id == ctx["token_b"] != ctx["token_a"]
    assert stored.ingestion_error is None and stored.sanitized_policy_digest is None


async def test_a_stale_runs_detection_commit_adds_no_rows_and_keeps_the_counts(
    tmp_path, monkeypatch
) -> None:
    """A claim lands between the chunk commit and the detection commit of the run."""
    from septum_api.services import document_pipeline as dp

    ctx: dict = {}
    raised: list = []
    real = dp.DocumentPipeline._persist_chunks

    async def _persist_then_claim(self, db, chunks):
        stored = await real(self, db, chunks)
        await _land_newer_claim(ctx, monkeypatch)
        return stored

    monkeypatch.setattr(dp.DocumentPipeline, "_persist_chunks", _persist_then_claim)

    chunks, rows, stored = await _ingest(
        tmp_path, monkeypatch, [TextBlock(f"{NAME} A_STALE", "text")],
        sanitizer=_name_detector(), before_run=_claiming_before(ctx, monkeypatch),
        on_failure=_record_into(raised),
    )

    assert raised == [dp.DocumentRunSupersededError]
    assert rows == [] and chunks == []
    assert (stored.chunk_count, stored.entity_count) == (0, 0)
    assert stored.ingestion_status == "processing"
    assert stored.ingestion_run_id == ctx["token_b"]


async def test_a_stale_runs_audit_link_leaves_the_newer_claims_detection_rows_unlinked(
    tmp_path, monkeypatch
) -> None:
    """The newer run's rows reuse the freed ids, so an unfenced link would land on them."""
    from septum_api.models.document import Chunk
    from septum_api.models.entity_detection import EntityDetection
    from septum_api.services import document_pipeline as dp

    ctx: dict = {}
    raised: list = []
    real = dp._audit_detections

    async def _audit_after_newer_rows(
        db, document, type_counts, total, anon_map, extra, detection_rows, run_token
    ) -> None:
        ctx["a_ids"] = [row.id for row in detection_rows]
        await _land_newer_claim(ctx, monkeypatch)
        async with ctx["maker"]() as other:
            chunk = Chunk(
                document_id=ctx["doc_id"], index=0, sanitized_text="B", raw_text="B", char_count=1
            )
            other.add(chunk)
            await other.flush()
            other.add_all([
                EntityDetection(
                    document_id=ctx["doc_id"], chunk_id=chunk.id, entity_type="PERSON_NAME",
                    placeholder="[PERSON_NAME_1]", start_offset=0, end_offset=1, score=1.0,
                )
                for _ in detection_rows
            ])
            await other.commit()
        await real(
            db, document, type_counts, total, anon_map, extra, detection_rows, run_token
        )

    monkeypatch.setattr(dp, "_audit_detections", _audit_after_newer_rows)

    _chunks, rows, stored = await _ingest(
        tmp_path, monkeypatch, [TextBlock(f"{NAME} A_STALE", "text")],
        sanitizer=_name_detector(), before_run=_claiming_before(ctx, monkeypatch),
        on_failure=_record_into(raised),
    )

    assert raised == [dp.DocumentRunSupersededError]
    assert ctx["a_ids"] and [row.id for row in rows] == ctx["a_ids"]
    assert [row.audit_event_id for row in rows] == [None] * len(rows)
    assert stored.ingestion_status == "processing" and stored.ingestion_run_id == ctx["token_b"]


async def test_a_run_whose_row_was_deleted_after_the_claim_stores_nothing(
    tmp_path, monkeypatch
) -> None:
    from septum_api.models.document import Document
    from septum_api.services import document_pipeline as dp
    from sqlalchemy import delete

    ctx: dict = {}
    raised: list = []
    stored_maps: list = []

    async def _delete_row() -> None:
        async with ctx["maker"]() as other:
            await other.execute(delete(Document).where(Document.id == ctx["doc_id"]))
            await other.commit()

    _before_mask(monkeypatch, _delete_row)

    async def _before(session, doc) -> dict:
        async def _spy(document_id, anon_map) -> None:
            stored_maps.append(document_id)

        monkeypatch.setattr(dp, "set_document_map", _spy)
        return await _claiming_before(ctx, monkeypatch)(session, doc)

    chunks, rows, stored = await _ingest(
        tmp_path, monkeypatch, [TextBlock(f"{NAME} A_TEXT", "text")],
        sanitizer=_name_detector(), before_run=_before, on_failure=_record_into(raised),
    )

    assert raised == [dp.DocumentRunSupersededError]
    assert stored is None and chunks == [] and rows == []
    assert stored_maps == []


@pytest.mark.parametrize("scenario", ["deleted", "newer_claim"])
async def test_a_refused_fence_releases_the_write_lock_of_the_session(
    tmp_path, monkeypatch, scenario
) -> None:
    import asyncio

    from septum_api.models.document import Document
    from septum_api.services import document_pipeline as dp
    from sqlalchemy import delete, func, select

    engine, maker = await _sweep_db(tmp_path, monkeypatch)
    try:
        document_id = await _seed_derived(maker, tmp_path, "processing")
        token_a = await _restart_and_claim(maker, monkeypatch, document_id)
        if scenario == "deleted":
            async with maker() as other:
                await other.execute(delete(Document).where(Document.id == document_id))
                await other.commit()
        else:
            await _restart_and_claim(maker, monkeypatch, document_id)

        async with maker() as session:
            with pytest.raises(dp.DocumentRunSupersededError):
                await dp.fence_run(session, document_id, token_a, require_processing=True)
            assert await session.scalar(select(func.count()).select_from(Document)) == (
                0 if scenario == "deleted" else 1
            )
            async with maker() as other:
                other.add(Document(
                    filename="g", original_filename="g", file_type="x", file_format="pdf",
                    detected_language="en", encrypted_path="/g", file_size_bytes=1,
                    ingestion_status="completed",
                ))
                await asyncio.wait_for(other.commit(), timeout=15)
    finally:
        await engine.dispose()


async def test_a_stale_entity_index_refresh_is_refused_and_the_newer_claim_keeps_no_version(
    tmp_path, monkeypatch
) -> None:
    from septum_api.models.document import Document
    from septum_api.services import document_pipeline as dp
    from septum_api.services.anonymization_map import AnonymizationMap

    replaced: list = []

    async def _replace(db, document_id, anon_map) -> None:
        replaced.append(document_id)

    async def _recompute(db, document_id) -> None:
        replaced.append(document_id)

    monkeypatch.setattr(dp, "replace_index_for_document", _replace)
    monkeypatch.setattr(dp, "recompute_relationships_for_document", _recompute)
    engine, maker = await _sweep_db(tmp_path, monkeypatch)
    try:
        document_id = await _seed_derived(maker, tmp_path, "processing")
        token_a = await _restart_and_claim(maker, monkeypatch, document_id)
        token_b = await _restart_and_claim(maker, monkeypatch, document_id)
        anon_map = AnonymizationMap(document_id=document_id, language="en")

        async with maker() as session:
            document = await session.get(Document, document_id)
            with pytest.raises(dp.DocumentRunSupersededError):
                await dp._refresh_entity_index(session, document, anon_map, run_token=token_a)
        async with maker() as session:
            assert (await session.get(Document, document_id)).entity_index_version is None
        assert replaced == []

        async with maker() as session:
            document = await session.get(Document, document_id)
            assert await dp._refresh_entity_index(
                session, document, anon_map, run_token=token_b
            ) is True
        async with maker() as session:
            assert (
                await session.get(Document, document_id)
            ).entity_index_version == dp.ENTITY_INDEX_VERSION
        assert replaced == [document_id, document_id]
    finally:
        await engine.dispose()


async def test_an_orphan_reset_that_kept_the_token_refuses_data_writes_but_not_the_failure_write(
    tmp_path, monkeypatch
) -> None:
    from septum_api import database, main
    from septum_api.models.document import Document
    from septum_api.routers import documents
    from septum_api.services import document_pipeline as dp

    engine, maker = await _sweep_db(tmp_path, monkeypatch)
    try:
        document_id = await _seed_derived(maker, tmp_path, "processing")
        async with maker() as session:
            token_a = await documents.claim_for_reingest(session, document_id)
        monkeypatch.setattr(database, "get_session_maker", lambda: maker)
        await main._reset_orphaned_processing_docs()
        restart_error = "Processing interrupted by server restart. Please reprocess."
        async with maker() as session:
            row = await session.get(Document, document_id)
            assert (row.ingestion_status, row.ingestion_run_id) == ("failed", token_a)
            assert row.ingestion_error == restart_error

        async with maker() as session:
            with pytest.raises(dp.DocumentRunSupersededError):
                await dp.fence_run(session, document_id, token_a, require_processing=True)
        async with maker() as session:
            await dp.fence_run(session, document_id, token_a, require_processing=False)

        async with maker() as session:
            await documents.record_background_failure(
                session, document_id, RuntimeError("late failure"), user_id=1, run_token=token_a
            )
        async with maker() as session:
            row = await session.get(Document, document_id)
            assert row.ingestion_status == "failed"
            assert row.ingestion_error and row.ingestion_error != restart_error
    finally:
        await engine.dispose()


async def test_each_claim_hands_out_its_own_token_and_a_lost_claim_changes_nothing(
    tmp_path, monkeypatch
) -> None:
    from septum_api.models.document import Document
    from septum_api.routers import documents

    engine, maker = await _sweep_db(tmp_path, monkeypatch)

    async def _token_and_status() -> tuple:
        async with maker() as session:
            row = await session.get(Document, document_id)
            return row.ingestion_run_id, row.ingestion_status

    try:
        document_id = await _seed_derived(maker, tmp_path, "failed")
        assert (await _token_and_status())[0] is None

        async with maker() as session:
            first = await documents.claim_for_reingest(session, document_id)
        assert isinstance(first, str) and first
        assert await _token_and_status() == (first, "processing")

        async with maker() as session:
            assert await documents.claim_for_reingest(session, document_id) is None
        async with maker() as session:
            assert await documents.claim_for_reingest(
                session, document_id, only_completed=True
            ) is None
        assert await _token_and_status() == (first, "processing")

        second = await _restart_and_claim(maker, monkeypatch, document_id)
        assert second != first
        assert await _token_and_status() == (second, "processing")
    finally:
        await engine.dispose()


async def test_a_failure_recovery_write_is_fenced_after_the_index_drop_fails(
    tmp_path, monkeypatch
) -> None:
    """The first fence passes, the drop fails, and a newer claim lands before the recovery write."""
    from septum_api.routers import documents

    engine, maker = await _sweep_db(tmp_path, monkeypatch)
    ctx: dict = {"armed": False}
    real_drop = documents.drop_index_for_document

    async def _drop(db, document_id):
        if db is ctx.get("session"):
            ctx["armed"] = True
            raise RuntimeError("index drop failed")
        await real_drop(db, document_id)

    try:
        document_id = await _seed_derived(maker, tmp_path, "processing")
        ctx["token_a"] = await _restart_and_claim(maker, monkeypatch, document_id)
        monkeypatch.setattr(documents, "drop_index_for_document", _drop)

        async with maker() as session:
            ctx["session"] = session
            real_rollback = session.rollback

            async def _rollback_then_claim() -> None:
                await real_rollback()
                if ctx["armed"] and "token_b" not in ctx:
                    ctx["token_b"] = await _restart_and_claim(maker, monkeypatch, document_id)

            session.rollback = _rollback_then_claim
            await documents.record_background_failure(
                session, document_id, RuntimeError("boom"), user_id=1, run_token=ctx["token_a"]
            )

        assert "token_b" in ctx
        state = await _row_state(maker, document_id)
        assert (state["status"], state["error"]) == ("processing", None)
        assert state["token"] == ctx["token_b"] != ctx["token_a"]
        kept = await _derived_state(maker, tmp_path, document_id)
        assert kept["map"] is True and kept["indexes"] == [True, True]
    finally:
        await engine.dispose()


async def test_a_reingest_with_a_stale_token_erases_nothing_and_ingests_nothing(
    tmp_path, monkeypatch
) -> None:
    from septum_api.models.document import Document
    from septum_api.routers import documents
    from septum_api.services import document_pipeline as dp

    ingested: list = []

    async def _ingest_spy(**kwargs):
        ingested.append(kwargs)

    monkeypatch.setattr(documents._INGESTION_ROUTER, "ingest", _ingest_spy)
    engine, maker = await _sweep_db(tmp_path, monkeypatch)
    try:
        document_id = await _seed_derived(maker, tmp_path, "processing")
        token_a = await _restart_and_claim(maker, monkeypatch, document_id)
        token_b = await _restart_and_claim(maker, monkeypatch, document_id)
        before = await _derived_state(maker, tmp_path, document_id)
        assert before["map"] is True and before["indexes"] == [True, True]

        async with maker() as session:
            stale = await session.get(Document, document_id)
            with pytest.raises(dp.DocumentRunSupersededError):
                await documents.reingest_document(session, stale, run_token=token_a)

        assert ingested == []
        assert await _derived_state(maker, tmp_path, document_id) == before
        state = await _row_state(maker, document_id)
        assert (state["status"], state["token"], state["error"]) == ("processing", token_b, None)

        async with maker() as session:
            await dp.DocumentPipeline.cleanup_artifacts(
                document_id, db=session, run_token=token_b
            )
        erased = await _derived_state(maker, tmp_path, document_id)
        assert erased["map"] is False and erased["indexes"] == [False, False]
    finally:
        await engine.dispose()


async def test_a_claim_landing_while_the_cleanup_waits_for_the_map_lock_erases_nothing(
    tmp_path, monkeypatch
) -> None:
    from contextlib import asynccontextmanager

    from septum_api.services import document_pipeline as dp

    real_lock = dp.document_map_lock
    ctx: dict = {}

    @asynccontextmanager
    async def _lock_after_a_claim(document_id, *args, **kwargs):
        ctx["token_b"] = await _restart_and_claim(ctx["maker"], monkeypatch, document_id)
        async with real_lock(document_id, *args, **kwargs) as lock:
            yield lock

    engine, maker = await _sweep_db(tmp_path, monkeypatch)
    try:
        ctx["maker"] = maker
        document_id = await _seed_derived(maker, tmp_path, "processing")
        token_a = await _restart_and_claim(maker, monkeypatch, document_id)
        monkeypatch.setattr(dp, "document_map_lock", _lock_after_a_claim)

        async with maker() as session:
            with pytest.raises(dp.DocumentRunSupersededError):
                await dp.DocumentPipeline.cleanup_artifacts(
                    document_id, db=session, run_token=token_a
                )

        kept = await _derived_state(maker, tmp_path, document_id)
        assert kept["map"] is True and kept["indexes"] == [True, True]
        assert (await _row_state(maker, document_id))["token"] == ctx["token_b"]
    finally:
        await engine.dispose()


async def _stale_reingest(tmp_path, monkeypatch, *, file_format: str, text: str, claim_at: str):
    """Reingest as run A while run B claims the document at ``claim_at``.

    ``claim_at`` is ``"ingest"`` (before A's metadata fence) or ``"metadata_commit"``
    (after A's metadata commit). Returns ``(error, state_before_a, state_after_a)``.
    """
    from septum_api.database import build_default_app_settings
    from septum_api.models.document import Document
    from septum_api.routers import documents
    from septum_api.services import document_pipeline as dp
    from sqlalchemy import update

    async def _no_cleanup(document_id, *, db=None, run_token=None) -> None:
        return None

    async def _settings(db):
        return build_default_app_settings()

    monkeypatch.setattr(dp.DocumentPipeline, "cleanup_artifacts", staticmethod(_no_cleanup))
    monkeypatch.setattr(documents, "load_settings", _settings)
    engine, maker = await _sweep_db(tmp_path, monkeypatch)
    ctx: dict = {"armed": False, "fired": False}
    try:
        document_id = await _seed_derived(maker, tmp_path, "processing")
        async with maker() as session:
            await session.execute(
                update(Document).where(Document.id == document_id).values(file_format=file_format)
            )
            await session.commit()
        token_a = await _restart_and_claim(maker, monkeypatch, document_id)
        ctx["before"] = await _row_state(maker, document_id)

        async def _fire() -> None:
            if not ctx["fired"]:
                ctx["fired"] = True
                await _restart_and_claim(maker, monkeypatch, document_id)

        async def _ingest_stub(**kwargs) -> IngestionResult:
            if claim_at == "ingest":
                await _fire()
            return IngestionResult(text=text, metadata={}, blocks=[], confidence=0.91)

        def _detect(fmt, result) -> str:
            ctx["armed"] = True
            return "zz"

        monkeypatch.setattr(documents._INGESTION_ROUTER, "ingest", _ingest_stub)
        monkeypatch.setattr(documents, "_detect_document_language", _detect)

        error = None
        async with maker() as session:
            stale = await session.get(Document, document_id)
            real_commit = session.commit

            async def _commit_then_claim() -> None:
                await real_commit()
                if claim_at == "metadata_commit" and ctx["armed"]:
                    await _fire()

            session.commit = _commit_then_claim
            try:
                await documents.reingest_document(session, stale, run_token=token_a)
            except Exception as exc:
                error = exc
        assert ctx["fired"], "the newer claim never landed"
        after = await _row_state(maker, document_id)
        after["token_a"] = token_a
        return error, ctx["before"], after
    finally:
        await engine.dispose()


async def test_a_stale_reingest_does_not_commit_its_metadata_onto_the_newer_claim(
    tmp_path, monkeypatch
) -> None:
    from septum_api.services import document_pipeline as dp

    error, before, after = await _stale_reingest(
        tmp_path, monkeypatch, file_format="pdf", text="plain words", claim_at="ingest"
    )

    assert type(error) is dp.DocumentRunSupersededError
    assert after["token"] != after["token_a"]
    assert after["status"] == "processing" and after["error"] is None
    assert after["detected_language"] == before["detected_language"] != "zz"
    assert after["ocr_confidence"] == before["ocr_confidence"] != 0.91


async def test_a_stale_reingest_of_an_empty_audio_does_not_fail_the_newer_claim(
    tmp_path, monkeypatch
) -> None:
    from septum_api.services import document_pipeline as dp

    error, _before, after = await _stale_reingest(
        tmp_path, monkeypatch, file_format="audio", text="", claim_at="metadata_commit"
    )

    assert type(error) is dp.DocumentRunSupersededError
    assert after["token"] != after["token_a"]
    assert after["status"] == "processing" and after["error"] is None


def _patch_upload_task(monkeypatch, maker, *, text: str) -> None:
    """Point ``_run_upload_ingestion`` at ``maker`` with a fixed ingestion result and detection."""
    import asyncio

    from septum_api import database
    from septum_api.database import build_default_app_settings
    from septum_api.routers import documents

    async def _settings(db):
        return build_default_app_settings()

    async def _fixed(**kwargs) -> IngestionResult:
        return IngestionResult(text=text, metadata={}, blocks=[], confidence=0.91)

    monkeypatch.setattr(database, "get_session_maker", lambda: maker)
    monkeypatch.setattr(documents, "_INGESTION_SEMAPHORE", asyncio.Semaphore(1))
    monkeypatch.setattr(documents, "load_settings", _settings)
    monkeypatch.setattr(documents._INGESTION_ROUTER, "ingest", _fixed)
    monkeypatch.setattr(documents, "_detect_document_language", lambda fmt, result: "zz")


async def _empty_audio_upload(tmp_path, monkeypatch, *, newer_claim: bool) -> tuple:
    """Run an empty-audio upload task; with ``newer_claim`` a newer run claims after its metadata commit."""
    from septum_api.models.document import Document
    from septum_api.routers import documents
    from sqlalchemy import update
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    engine, plain = await _sweep_db(tmp_path, monkeypatch)
    ctx: dict = {"fired": False}

    class _SessionClaimingAfterItsFirstCommit(AsyncSession):
        async def commit(self) -> None:
            await super().commit()
            if newer_claim and not ctx["fired"]:
                ctx["fired"] = True
                ctx["token_b"] = await _restart_and_claim(plain, monkeypatch, ctx["doc_id"])

    try:
        ctx["doc_id"] = await _seed_derived(plain, tmp_path, "processing")
        async with plain() as session:
            await session.execute(
                update(Document).where(Document.id == ctx["doc_id"]).values(file_format="audio")
            )
            await session.commit()
        token_a = await _restart_and_claim(plain, monkeypatch, ctx["doc_id"])
        _patch_upload_task(
            monkeypatch,
            async_sessionmaker(
                bind=engine, class_=_SessionClaimingAfterItsFirstCommit, expire_on_commit=False
            ),
            text="",
        )
        source = documents._UploadedSource(tmp_path / "source.enc", "audio/mpeg", "audio")
        await documents._run_upload_ingestion(ctx["doc_id"], source, 1, run_token=token_a)
        return await _row_state(plain, ctx["doc_id"]), token_a, ctx.get("token_b")
    finally:
        await engine.dispose()


async def test_an_empty_audio_upload_with_the_current_token_fails_its_row(
    tmp_path, monkeypatch
) -> None:
    from septum_api.routers import documents

    state, token_a, _ = await _empty_audio_upload(tmp_path, monkeypatch, newer_claim=False)

    assert state["status"] == "failed" and state["token"] == token_a
    assert state["error"].startswith(documents._EMPTY_AUDIO_ERROR)
    assert state["chunk_count"] == 0


async def test_a_stale_empty_audio_upload_does_not_fail_the_newer_run(
    tmp_path, monkeypatch
) -> None:
    state, token_a, token_b = await _empty_audio_upload(tmp_path, monkeypatch, newer_claim=True)

    assert token_b and token_b != token_a
    assert state["status"] == "processing" and state["token"] == token_b
    assert state["error"] is None
