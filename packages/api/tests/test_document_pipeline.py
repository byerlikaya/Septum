from __future__ import annotations

"""Tests for the document pipeline chunk indexing behaviour."""

from types import SimpleNamespace

from septum_api.services.document_pipeline import DocumentPipeline


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

    async def _no_chunks(self, file_format, text):
        return []

    async def _persist(self, db, document_id, chunks, raw_texts):
        return []

    monkeypatch.setattr(dp, "create_sanitizer", _create_sanitizer)
    monkeypatch.setattr(dp.DocumentPipeline, "_build_chunks", _no_chunks)
    monkeypatch.setattr(dp.DocumentPipeline, "_persist_chunks", _persist)
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
                ingested_text="", ingestion_confidence=None,
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
