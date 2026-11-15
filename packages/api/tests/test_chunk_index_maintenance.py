"""A deleted chunk's id leaves its document's FAISS and BM25 indexes; an edited chunk is re-indexed.

An edit drops the chunk's raw text and replaces its entry in both indexes
with one built from the edited sanitized text. The index primitives run on real encrypted files with deterministic stub
embeddings (no model is ever loaded). The delete endpoint runs through ASGI
on SQLite (WAL, the production connect listener) and, when
``SEPTUM_TEST_POSTGRES_URL`` points at a throwaway database, on PostgreSQL
in a schema of its own: the re-read of the document (and, for an edit, of
the chunk's text) under the locks is SQL
whose transaction must be ended before any file is written. Races are forced
at hook points, never with sleeps.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator, List, Sequence

import faiss
import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient
from septum_api import database as database_module
from septum_api import main as main_module
from septum_api.database import _sqlite_wal_connect, get_db
from septum_api.main import app
from septum_api.models import Base
from septum_api.models.document import Chunk, Document
from septum_api.models.entity_detection import EntityDetection
from septum_api.models.error_log import ErrorLog
from septum_api.models.user import User
from septum_api.routers import chat as chat_module
from septum_api.routers import chunks as chunks_module
from septum_api.routers import documents as documents_module
from septum_api.services import document_pipeline as dp
from septum_api.services import index_file_io
from septum_api.services import vector_store as vector_store_module
from septum_api.services.bm25_retriever import TOKENIZER_VERSION, BM25Retriever
from septum_api.services.document_anon_store import DocumentMapLockLost
from septum_api.services.vector_store import VectorStore
from septum_api.utils.auth_dependency import get_current_user
from sqlalchemy import event, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

POSTGRES_URL = os.getenv("SEPTUM_TEST_POSTGRES_URL", "")
TEXTS = [
    "The tenant pays the rent on the first day.",
    "Deposit is held by the landlord until the lease ends.",
    "Either party may end the lease with notice.",
]
RAW_TEXTS = [
    "The tenant pays the rent on the first day.",
    "Deposit is held by Jane Roe until the lease ends.",
    "Either party may end the lease with notice.",
]
EDITED = "The deposit returns within thirty days after move out."
EDITED_AGAIN = "Keys are handed back on the final day of the lease."
QUERIES = ["rent first day", "deposit landlord", "lease notice", "tenant lease", "deposit thirty days"]
FAILURE_DETAIL = {
    "detail": (
        "The chunk was deleted, but the document's search index could not "
        "be updated. Reprocess the document to rebuild it."
    )
}
EDIT_FAILURE_DETAIL = {
    "detail": (
        "The chunk was edited, but the document's search index could not "
        "be updated. Reprocess the document to rebuild it."
    )
}


def _vector(text_value: str) -> np.ndarray:
    seed = int.from_bytes(hashlib.sha256(text_value.encode("utf-8")).digest()[:8], "little")
    return np.random.default_rng(seed).standard_normal(16).astype("float32")


class _StubModel:
    """Deterministic embedding double: one seeded vector per text."""

    def encode(self, texts: Sequence[str], **_: Any) -> np.ndarray:
        return np.stack([_vector(t) for t in texts])


def _no_model(*_: Any, **__: Any) -> Any:
    raise AssertionError("an embedding model was requested")


@pytest.fixture(autouse=True)
def _stub_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vector_store_module, "get_shared_sentence_transformer", _no_model)
    monkeypatch.setattr(VectorStore, "_get_model", lambda self: _StubModel())


def _faiss_ids(store: VectorStore, document_id: int) -> List[int]:
    index = store._load_index(document_id)
    return sorted(int(i) for i in faiss.vector_to_array(index.id_map))


def _unit(text_value: str) -> np.ndarray:
    vector = _vector(text_value)
    return vector / np.linalg.norm(vector)


def _faiss_vectors(store: VectorStore, document_id: int) -> dict:
    index = store._load_index(document_id)
    ids = faiss.vector_to_array(index.id_map)
    return {int(i): v for i, v in zip(ids, index.index.reconstruct_n(0, index.ntotal))}


def _ranked(vectors: dict, query: str) -> List[tuple[int, float]]:
    """Rank stored vectors by inner product with the query, as the flat IP index scores them.

    Computed with numpy: a FAISS ``search`` call aborts inside this test
    process on macOS (two OpenMP runtimes), so the stored vectors are scored
    directly.
    """
    scores = {chunk_id: float(vector @ _unit(query)) for chunk_id, vector in vectors.items()}
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)


def _faiss_bytes(document_id: int) -> bytes:
    return VectorStore()._index_path(document_id).read_bytes()


def _bm25_bytes(document_id: int) -> bytes:
    return BM25Retriever()._index_path(document_id).read_bytes()


# --- T1: index primitives -------------------------------------------------


def test_faiss_remove_ids_drops_only_the_given_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = VectorStore(base_dir=tmp_path / "faiss")
    store.index_document(5, [11, 12, 13], TEXTS)
    monkeypatch.setattr(VectorStore, "_get_model", _no_model)

    store.remove_ids(5, [12, 999])

    index = store._load_index(5)
    assert _faiss_ids(store, 5) == [11, 13]
    kept = index.index.reconstruct_n(0, index.ntotal)
    norms = np.linalg.norm(np.stack([_vector(TEXTS[0]), _vector(TEXTS[2])]), axis=1, keepdims=True)
    expected = np.stack([_vector(TEXTS[0]), _vector(TEXTS[2])]) / norms
    np.testing.assert_allclose(kept, expected, rtol=1e-6)
    assert store._load_index(6) is None
    assert sorted(p.name for p in (tmp_path / "faiss").iterdir()) == ["doc_5.faiss.enc"]


def test_bm25_remove_ids_scores_equal_a_fresh_build(tmp_path: Path) -> None:
    store = BM25Retriever(base_dir=tmp_path / "removed")
    store.index_document(5, [11, 12, 13], TEXTS, "de")
    fresh = BM25Retriever(base_dir=tmp_path / "fresh")
    fresh.index_document(5, [11, 13], [TEXTS[0], TEXTS[2]], "de")

    store.remove_ids(5, [12, 999])

    stored = store._load_index(5)
    assert stored == fresh._load_index(5)
    assert (stored["tokenizer_version"], stored["language"]) == (TOKENIZER_VERSION, "de")
    for query in QUERIES:
        assert store.search(5, query, 10) == fresh.search(5, query, 10)


def test_remove_ids_on_missing_index_is_a_noop(tmp_path: Path) -> None:
    faiss_store = VectorStore(base_dir=tmp_path / "faiss")
    bm25_store = BM25Retriever(base_dir=tmp_path / "bm25")

    faiss_store.remove_ids(5, [1])
    bm25_store.remove_ids(5, [1])

    assert list((tmp_path / "faiss").iterdir()) == []
    assert list((tmp_path / "bm25").iterdir()) == []


def test_remove_ids_of_no_stored_id_leaves_the_file_and_of_every_id_removes_it(
    tmp_path: Path,
) -> None:
    faiss_store = VectorStore(base_dir=tmp_path / "faiss")
    bm25_store = BM25Retriever(base_dir=tmp_path / "bm25")
    faiss_store.index_document(5, [11, 12], TEXTS[:2])
    bm25_store.index_document(5, [11, 12], TEXTS[:2], "en")
    before = (faiss_store._index_path(5).read_bytes(), bm25_store._index_path(5).read_bytes())

    faiss_store.remove_ids(5, [999])
    bm25_store.remove_ids(5, [999])
    assert (faiss_store._index_path(5).read_bytes(), bm25_store._index_path(5).read_bytes()) == before

    faiss_store.remove_ids(5, [11, 12])
    bm25_store.remove_ids(5, [11, 12])
    assert not faiss_store._index_path(5).exists()
    assert not bm25_store._index_path(5).exists()
    assert bm25_store.search(5, "rent", 5) == []


def test_faiss_replace_entry_swaps_only_that_vector(tmp_path: Path) -> None:
    store = VectorStore(base_dir=tmp_path / "faiss")
    store.index_document(5, [11, 12, 13], TEXTS)

    store.replace_entry(5, 12, EDITED)

    vectors = _faiss_vectors(store, 5)
    assert sorted(vectors) == [11, 12, 13]
    np.testing.assert_allclose(vectors[12], _unit(EDITED), rtol=1e-6)
    np.testing.assert_allclose(vectors[11], _unit(TEXTS[0]), rtol=1e-6)
    np.testing.assert_allclose(vectors[13], _unit(TEXTS[2]), rtol=1e-6)
    assert _ranked(vectors, EDITED)[0][0] == 12


def test_bm25_replace_entry_scores_equal_a_fresh_build(tmp_path: Path) -> None:
    store = BM25Retriever(base_dir=tmp_path / "replaced")
    store.index_document(5, [11, 12, 13], TEXTS, "de")
    fresh = BM25Retriever(base_dir=tmp_path / "fresh")
    fresh.index_document(5, [11, 12, 13], [TEXTS[0], EDITED, TEXTS[2]], "de")

    store.replace_entry(5, 12, EDITED)

    stored = store._load_index(5)
    assert stored == fresh._load_index(5)
    assert (stored["tokenizer_version"], stored["language"]) == (TOKENIZER_VERSION, "de")
    for query in QUERIES:
        assert store.search(5, query, 10) == fresh.search(5, query, 10)


def test_replace_entry_on_missing_index_is_a_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    faiss_store = VectorStore(base_dir=tmp_path / "faiss")
    bm25_store = BM25Retriever(base_dir=tmp_path / "bm25")
    monkeypatch.setattr(VectorStore, "_get_model", _no_model)

    faiss_store.replace_entry(5, 1, EDITED)
    bm25_store.replace_entry(5, 1, EDITED)

    assert list((tmp_path / "faiss").iterdir()) == []
    assert list((tmp_path / "bm25").iterdir()) == []


# --- T2: the delete endpoint ----------------------------------------------


@contextlib.asynccontextmanager
async def _engine(backend: str, tmp_path: Path) -> AsyncIterator[Any]:
    if backend == "sqlite":
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'chunk_index.db'}")
        event.listen(engine.sync_engine, "connect", _sqlite_wal_connect)
        try:
            yield engine
        finally:
            await engine.dispose()
        return
    schema = f"chunk_index_{uuid.uuid4().hex[:12]}"
    admin = create_async_engine(POSTGRES_URL)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        POSTGRES_URL, connect_args={"server_settings": {"search_path": schema}}
    )
    try:
        yield engine
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


@pytest.fixture(params=["sqlite", "postgresql"])
async def env(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[SimpleNamespace]:
    if request.param == "postgresql" and not POSTGRES_URL:
        pytest.skip("SEPTUM_TEST_POSTGRES_URL not set")
    async with _engine(request.param, tmp_path) as engine:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(bind=engine, expire_on_commit=False)
        async with maker() as session:
            user = User(email="editor@example.com", hashed_password="x", role="editor")
            session.add(user)
            await session.commit()

        async def _get_db():
            async with maker() as session:
                yield session

        monkeypatch.setattr(database_module, "get_session_maker", lambda: maker)
        monkeypatch.setattr(main_module, "get_session_maker", lambda: maker)
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t"
        ) as client:
            app.dependency_overrides[get_db] = _get_db
            app.dependency_overrides[get_current_user] = lambda: user
            try:
                yield SimpleNamespace(maker=maker, client=client, user=user, monkeypatch=monkeypatch)
            finally:
                app.dependency_overrides.pop(get_db, None)
                app.dependency_overrides.pop(get_current_user, None)


async def _seed(
    env: SimpleNamespace, raw_texts: Sequence[str] = TEXTS
) -> tuple[int, List[int]]:
    """Store a ``completed`` document with three chunks and index their raw texts, as ingestion does; return its ids."""
    async with env.maker() as session:
        document = Document(
            filename="f", original_filename="f", file_type="x", file_format="pdf",
            detected_language="en", encrypted_path="/x", file_size_bytes=1,
            ingestion_status="completed", ingestion_run_id="run-1",
            chunk_count=len(TEXTS), user_id=env.user.id,
        )
        session.add(document)
        await session.flush()
        chunks = [
            Chunk(document_id=document.id, index=i, sanitized_text=t, raw_text=r, char_count=len(t))
            for i, (t, r) in enumerate(zip(TEXTS, raw_texts))
        ]
        session.add_all(chunks)
        await session.commit()
        document_id, chunk_ids = document.id, [c.id for c in chunks]
    VectorStore().index_document(document_id, chunk_ids, list(raw_texts))
    BM25Retriever().index_document(document_id, chunk_ids, list(raw_texts), "en")
    return document_id, chunk_ids


async def _chunk_ids(env: SimpleNamespace, document_id: int) -> List[int]:
    async with env.maker() as session:
        rows = await session.scalars(select(Chunk.id).where(Chunk.document_id == document_id))
        return sorted(rows)


async def test_delete_chunk_removes_its_id_from_both_indexes(env: SimpleNamespace) -> None:
    document_id, (first, middle, last) = await _seed(env)

    response = await env.client.delete(f"/api/chunks/{middle}")

    assert response.status_code == 204
    assert await _chunk_ids(env, document_id) == [first, last]
    assert _faiss_ids(VectorStore(), document_id) == [first, last]
    stored = BM25Retriever()._load_index(document_id)
    assert (stored["chunk_ids"], stored["language"]) == ([first, last], "en")


@pytest.mark.parametrize("claim", ["claimed", "reprocessed"])
async def test_delete_chunk_leaves_index_untouched_when_document_was_reclaimed(
    env: SimpleNamespace, claim: str
) -> None:
    document_id, (_, middle, _) = await _seed(env)
    before = (_faiss_bytes(document_id), _bm25_bytes(document_id))
    real_lock = chunks_module.document_index_lock

    @contextlib.asynccontextmanager
    async def _claim_lands_first(doc_id: int) -> AsyncIterator[None]:
        async with env.maker() as session:
            assert await documents_module.claim_for_reingest(session, doc_id) is not None
            if claim == "reprocessed":
                await session.execute(
                    update(Document).where(Document.id == doc_id).values(ingestion_status="completed")
                )
                await session.commit()
        async with real_lock(doc_id):
            yield

    env.monkeypatch.setattr(chunks_module, "document_index_lock", _claim_lands_first)

    response = await env.client.delete(f"/api/chunks/{middle}")

    assert response.status_code == 204
    assert (_faiss_bytes(document_id), _bm25_bytes(document_id)) == before


async def test_concurrent_chunk_deletes_both_ids_gone(env: SimpleNamespace) -> None:
    """Two workers' deletes: each has its own process-local index lock, only the map lock is shared.

    The first delete is paused between loading its FAISS index and saving it
    until the second delete either finished or waits on the map lock.
    """
    document_id, (first, middle, last) = await _seed(env)
    loop = asyncio.get_running_loop()
    entered: List[int] = []
    second_waiting = asyncio.Event()
    second: dict = {}
    paused: List[bool] = []
    real_map_lock = chunks_module.document_map_lock

    @contextlib.asynccontextmanager
    async def _per_worker_index_lock(doc_id: int) -> AsyncIterator[None]:
        async with asyncio.Lock():
            yield

    @contextlib.asynccontextmanager
    async def _tracked_map_lock(doc_id: int, **kwargs: Any) -> AsyncIterator[Any]:
        if entered:
            second_waiting.set()
        entered.append(doc_id)
        async with real_map_lock(doc_id, **kwargs) as lock:
            yield lock

    async def _second_delete_until_blocked() -> None:
        task = asyncio.ensure_future(env.client.delete(f"/api/chunks/{last}"))
        waiting = asyncio.ensure_future(second_waiting.wait())
        await asyncio.wait({task, waiting}, return_when=asyncio.FIRST_COMPLETED)
        second["task"] = task

    class _PausingStore(VectorStore):
        def _save_index(self, doc_id: int, index: Any) -> None:
            if not paused:
                paused.append(True)
                asyncio.run_coroutine_threadsafe(_second_delete_until_blocked(), loop).result()
            super()._save_index(doc_id, index)

    env.monkeypatch.setattr(chunks_module, "document_index_lock", _per_worker_index_lock)
    env.monkeypatch.setattr(chunks_module, "document_map_lock", _tracked_map_lock)
    env.monkeypatch.setattr(chunks_module, "VectorStore", _PausingStore)

    response = await env.client.delete(f"/api/chunks/{middle}")

    assert response.status_code == 204
    assert (await second["task"]).status_code == 204
    assert _faiss_ids(VectorStore(), document_id) == [first]
    assert BM25Retriever()._load_index(document_id)["chunk_ids"] == [first]


async def test_delete_chunk_index_failure_is_reported_not_swallowed(
    env: SimpleNamespace, caplog: pytest.LogCaptureFixture
) -> None:
    document_id, (first, middle, last) = await _seed(env)
    before = (_faiss_bytes(document_id), _bm25_bytes(document_id))

    def _refused(src: Any, dst: Any) -> None:
        raise PermissionError(13, "Permission denied", f"{dst} {TEXTS[1]}")

    env.monkeypatch.setattr(index_file_io.os, "replace", _refused)

    with caplog.at_level(logging.DEBUG):
        response = await env.client.delete(f"/api/chunks/{middle}")

    assert response.status_code == 500
    assert response.json() == FAILURE_DETAIL
    assert await _chunk_ids(env, document_id) == [first, last]
    assert (_faiss_bytes(document_id), _bm25_bytes(document_id)) == before
    assert not [p for p in VectorStore().base_dir.iterdir() if p.name.endswith(".tmp")]
    errors = [r for r in caplog.records if r.name == chunks_module.logger.name]
    assert [r.getMessage() for r in errors] == [
        "Removing deleted chunk ids from the faiss index of "
        f"document_id={document_id} failed: RuntimeError"
    ]
    assert errors[0].exc_info is None
    for record in caplog.records:
        rendered = record.getMessage() + (str(record.exc_info) if record.exc_info else "")
        assert TEXTS[1] not in rendered
        assert "Permission denied" not in rendered
    async with env.maker() as session:
        stored = [
            {column.name: getattr(row, column.key) for column in ErrorLog.__table__.columns}
            for row in await session.scalars(select(ErrorLog))
        ]
    assert stored and all(row["status_code"] == 500 for row in stored)
    assert TEXTS[1] not in repr(stored) and "Permission denied" not in repr(stored)


async def test_ingestion_faiss_write_takes_the_document_index_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    held: List[tuple[str, bool]] = []

    def _faiss(doc_id: int, chunks: Any, raw: Any) -> None:
        held.append(("faiss", index_file_io.document_index_lock(doc_id).locked()))

    def _bm25(doc_id: int, chunks: Any, raw: Any, language: str) -> None:
        held.append(("bm25", index_file_io.document_index_lock(doc_id).locked()))

    monkeypatch.setattr(dp.DocumentPipeline, "_index_chunks", staticmethod(_faiss))
    monkeypatch.setattr(dp.DocumentPipeline, "_index_chunks_bm25", staticmethod(_bm25))

    await dp.DocumentPipeline(settings="SETTINGS")._index_document(
        7, [SimpleNamespace(id=1)], ["raw"], "en", lambda _: None
    )

    assert held == [("faiss", True), ("bm25", True)]


# --- T3: the edit endpoint ------------------------------------------------


def _allow_edits(env: SimpleNamespace) -> None:
    """Let edited text through the PII gate unchanged (the gate has its own tests)."""

    async def _gate(db: Any, settings: Any, document: Any, texts: Sequence[str], *_: Any, **__: Any):
        return list(texts)

    env.monkeypatch.setattr(chunks_module, "gate_document_texts", _gate)


async def _chunk_row(env: SimpleNamespace, chunk_id: int) -> Any:
    async with env.maker() as session:
        return (
            await session.execute(
                select(Chunk.sanitized_text, Chunk.raw_text).where(Chunk.id == chunk_id)
            )
        ).one()


async def test_edit_chunk_reindexes_its_entry_on_edited_text(env: SimpleNamespace) -> None:
    document_id, (first, middle, last) = await _seed(env, RAW_TEXTS)
    _allow_edits(env)

    response = await env.client.patch(f"/api/chunks/{middle}", json={"sanitized_text": EDITED})

    assert response.status_code == 200
    assert (response.json()["sanitized_text"], response.json()["raw_text"]) == (EDITED, None)
    assert tuple(await _chunk_row(env, middle)) == (EDITED, None)
    vectors = _faiss_vectors(VectorStore(), document_id)
    assert sorted(vectors) == [first, middle, last]
    np.testing.assert_allclose(vectors[middle], _unit(EDITED), rtol=1e-6)
    np.testing.assert_allclose(vectors[first], _unit(RAW_TEXTS[0]), rtol=1e-6)
    np.testing.assert_allclose(vectors[last], _unit(RAW_TEXTS[2]), rtol=1e-6)
    assert _ranked(vectors, EDITED)[0][0] == middle
    assert dict(_ranked(vectors, RAW_TEXTS[1]))[middle] < 0.99
    assert BM25Retriever().search(document_id, "thirty days move", 1)[0][0] == middle
    assert BM25Retriever().search(document_id, "Jane Roe", 3)[0][1] == 0.0


async def test_edit_then_bm25_rebuild_matches_incremental_index(env: SimpleNamespace) -> None:
    document_id, (_, middle, _) = await _seed(env, RAW_TEXTS)
    _allow_edits(env)

    response = await env.client.patch(f"/api/chunks/{middle}", json={"sanitized_text": EDITED})

    assert response.status_code == 200
    retriever = BM25Retriever()
    incremental = retriever._load_index(document_id)
    incremental_hits = [retriever.search(document_id, q, 10) for q in QUERIES]
    retriever.delete_index(document_id)
    async with env.maker() as session:
        await chat_module._rebuild_outdated_bm25_index(session, BM25Retriever(), document_id)
    rebuilt = BM25Retriever()
    assert rebuilt._load_index(document_id) == incremental
    assert [rebuilt.search(document_id, q, 10) for q in QUERIES] == incremental_hits


async def test_edit_chunk_title_only_keeps_raw_text_and_indexes(env: SimpleNamespace) -> None:
    document_id, (_, middle, _) = await _seed(env, RAW_TEXTS)
    _allow_edits(env)
    before = (_faiss_bytes(document_id), _bm25_bytes(document_id))

    response = await env.client.patch(f"/api/chunks/{middle}", json={"section_title": "Deposit"})

    assert response.status_code == 200
    assert tuple(await _chunk_row(env, middle)) == (TEXTS[1], RAW_TEXTS[1])
    assert (_faiss_bytes(document_id), _bm25_bytes(document_id)) == before


@pytest.mark.parametrize("claim", ["claimed", "reprocessed", "run_replaced"])
async def test_edit_chunk_leaves_index_untouched_when_document_was_reclaimed(
    env: SimpleNamespace, claim: str
) -> None:
    """``run_replaced`` keeps the chunk rows and the status: only the run token tells the runs apart."""
    document_id, (_, middle, _) = await _seed(env, RAW_TEXTS)
    _allow_edits(env)
    before = (_faiss_bytes(document_id), _bm25_bytes(document_id))
    real_lock = chunks_module.document_index_lock

    @contextlib.asynccontextmanager
    async def _claim_lands_first(doc_id: int) -> AsyncIterator[None]:
        async with env.maker() as session:
            if claim == "run_replaced":
                await session.execute(
                    update(Document).where(Document.id == doc_id).values(ingestion_run_id="run-2")
                )
                await session.commit()
            else:
                assert await documents_module.claim_for_reingest(session, doc_id) is not None
            if claim == "reprocessed":
                await session.execute(
                    update(Document).where(Document.id == doc_id).values(ingestion_status="completed")
                )
                await session.commit()
        async with real_lock(doc_id):
            yield

    env.monkeypatch.setattr(chunks_module, "document_index_lock", _claim_lands_first)

    response = await env.client.patch(f"/api/chunks/{middle}", json={"sanitized_text": EDITED})

    assert response.status_code == 200
    assert response.json()["sanitized_text"] == EDITED
    assert (_faiss_bytes(document_id), _bm25_bytes(document_id)) == before


async def test_edit_reindex_reads_the_latest_committed_text(env: SimpleNamespace) -> None:
    """An earlier edit whose re-index runs after a later edit's never puts its stale text back."""
    document_id, (_, middle, _) = await _seed(env, RAW_TEXTS)
    _allow_edits(env)
    real_lock = chunks_module.document_index_lock
    later: List[Any] = []

    @contextlib.asynccontextmanager
    async def _later_edit_lands_first(doc_id: int) -> AsyncIterator[None]:
        if not later:
            later.append(None)
            later[0] = await env.client.patch(
                f"/api/chunks/{middle}", json={"sanitized_text": EDITED_AGAIN}
            )
        async with real_lock(doc_id):
            yield

    env.monkeypatch.setattr(chunks_module, "document_index_lock", _later_edit_lands_first)

    response = await env.client.patch(f"/api/chunks/{middle}", json={"sanitized_text": EDITED})

    assert (response.status_code, later[0].status_code) == (200, 200)
    assert tuple(await _chunk_row(env, middle)) == (EDITED_AGAIN, None)
    np.testing.assert_allclose(
        _faiss_vectors(VectorStore(), document_id)[middle], _unit(EDITED_AGAIN), rtol=1e-6
    )
    stored = BM25Retriever()._load_index(document_id)
    fresh = BM25Retriever(base_dir=VectorStore().base_dir.parent / "fresh_bm25")
    fresh.index_document(1, [middle], [EDITED_AGAIN], "en")
    assert stored["corpus"][stored["chunk_ids"].index(middle)] == fresh._load_index(1)["corpus"][0]


@pytest.mark.parametrize("first_op", ["edit", "delete"])
async def test_concurrent_edit_and_delete_keep_both_indexes_consistent(
    env: SimpleNamespace, first_op: str
) -> None:
    """An edit and a delete in two workers: only the map lock is shared between them.

    The first request is paused between loading its FAISS index and saving
    it until the second one either finished or waits on the map lock.
    """
    document_id, (first, middle, last) = await _seed(env, RAW_TEXTS)
    _allow_edits(env)
    loop = asyncio.get_running_loop()
    entered: List[int] = []
    second_waiting = asyncio.Event()
    second: dict = {}
    paused: List[bool] = []
    real_map_lock = chunks_module.document_map_lock

    def _request(op: str) -> Any:
        if op == "edit":
            return env.client.patch(f"/api/chunks/{middle}", json={"sanitized_text": EDITED})
        return env.client.delete(f"/api/chunks/{last}")

    @contextlib.asynccontextmanager
    async def _per_worker_index_lock(doc_id: int) -> AsyncIterator[None]:
        async with asyncio.Lock():
            yield

    @contextlib.asynccontextmanager
    async def _tracked_map_lock(doc_id: int, **kwargs: Any) -> AsyncIterator[Any]:
        if entered:
            second_waiting.set()
        entered.append(doc_id)
        async with real_map_lock(doc_id, **kwargs) as lock:
            yield lock

    async def _second_until_blocked() -> None:
        task = asyncio.ensure_future(_request("delete" if first_op == "edit" else "edit"))
        waiting = asyncio.ensure_future(second_waiting.wait())
        await asyncio.wait({task, waiting}, return_when=asyncio.FIRST_COMPLETED)
        second["task"] = task

    class _PausingStore(VectorStore):
        def _save_index(self, doc_id: int, index: Any) -> None:
            if not paused:
                paused.append(True)
                asyncio.run_coroutine_threadsafe(_second_until_blocked(), loop).result()
            super()._save_index(doc_id, index)

    env.monkeypatch.setattr(chunks_module, "document_index_lock", _per_worker_index_lock)
    env.monkeypatch.setattr(chunks_module, "document_map_lock", _tracked_map_lock)
    env.monkeypatch.setattr(chunks_module, "VectorStore", _PausingStore)

    response = await _request(first_op)

    assert response.status_code in (200, 204)
    assert (await second["task"]).status_code in (200, 204)
    assert second_waiting.is_set()
    vectors = _faiss_vectors(VectorStore(), document_id)
    assert sorted(vectors) == [first, middle]
    np.testing.assert_allclose(vectors[middle], _unit(EDITED), rtol=1e-6)
    stored = BM25Retriever()._load_index(document_id)
    fresh = BM25Retriever(base_dir=VectorStore().base_dir.parent / "fresh_bm25")
    fresh.index_document(document_id, [first, middle], [RAW_TEXTS[0], EDITED], "en")
    assert stored == fresh._load_index(document_id)


async def test_edit_chunk_index_failure_is_reported_not_swallowed(
    env: SimpleNamespace, caplog: pytest.LogCaptureFixture
) -> None:
    document_id, (_, middle, _) = await _seed(env, RAW_TEXTS)
    _allow_edits(env)
    before = (_faiss_bytes(document_id), _bm25_bytes(document_id))

    def _refused(src: Any, dst: Any) -> None:
        raise PermissionError(13, "Permission denied", f"{dst} {RAW_TEXTS[1]} {EDITED}")

    env.monkeypatch.setattr(index_file_io.os, "replace", _refused)

    with caplog.at_level(logging.DEBUG):
        response = await env.client.patch(f"/api/chunks/{middle}", json={"sanitized_text": EDITED})

    assert response.status_code == 500
    assert response.json() == EDIT_FAILURE_DETAIL
    assert tuple(await _chunk_row(env, middle)) == (EDITED, None)
    assert (_faiss_bytes(document_id), _bm25_bytes(document_id)) == before
    assert not [p for p in VectorStore().base_dir.iterdir() if p.name.endswith(".tmp")]
    assert not [p for p in BM25Retriever().base_dir.iterdir() if p.name.endswith(".tmp")]
    errors = [r for r in caplog.records if r.name == chunks_module.logger.name]
    assert [r.getMessage() for r in errors] == [
        "Re-indexing the edited chunk in the faiss index of "
        f"document_id={document_id} failed: RuntimeError"
    ]
    assert errors[0].exc_info is None
    for record in caplog.records:
        rendered = record.getMessage() + (str(record.exc_info) if record.exc_info else "")
        for secret in (RAW_TEXTS[1], "Jane Roe", EDITED, "Permission denied"):
            assert secret not in rendered
    async with env.maker() as session:
        stored = [
            {column.name: getattr(row, column.key) for column in ErrorLog.__table__.columns}
            for row in await session.scalars(select(ErrorLog))
        ]
    assert stored and all(row["status_code"] == 500 for row in stored)
    for secret in ("Jane Roe", EDITED, "Permission denied"):
        assert secret not in repr(stored)


def _operation(env: SimpleNamespace, op: str, chunk_id: int) -> Any:
    if op == "edit":
        _allow_edits(env)
        return env.client.patch(f"/api/chunks/{chunk_id}", json={"sanitized_text": EDITED})
    return env.client.delete(f"/api/chunks/{chunk_id}")


@pytest.mark.parametrize("op", ["delete", "edit"])
async def test_map_lock_lost_before_the_index_writes_answers_500(
    env: SimpleNamespace, op: str, caplog: pytest.LogCaptureFixture
) -> None:
    document_id, (_, middle, _) = await _seed(env, RAW_TEXTS)
    before = (_faiss_bytes(document_id), _bm25_bytes(document_id))
    real_map_lock = chunks_module.document_map_lock

    async def _lost() -> None:
        raise DocumentMapLockLost("lost")

    @contextlib.asynccontextmanager
    async def _losing_map_lock(doc_id: int, **kwargs: Any) -> AsyncIterator[Any]:
        async with real_map_lock(doc_id, **kwargs) as lock:
            lock.ensure_held = _lost
            yield lock

    env.monkeypatch.setattr(chunks_module, "document_map_lock", _losing_map_lock)

    with caplog.at_level(logging.DEBUG):
        response = await _operation(env, op, middle)

    assert response.status_code == 500
    assert response.json() == (EDIT_FAILURE_DETAIL if op == "edit" else FAILURE_DETAIL)
    assert (_faiss_bytes(document_id), _bm25_bytes(document_id)) == before
    errors = [r.getMessage() for r in caplog.records if r.name == chunks_module.logger.name]
    assert len(errors) == 1
    assert errors[0].endswith(f"faiss index of document_id={document_id} failed: DocumentMapLockLost")


@pytest.mark.parametrize("op", ["delete", "edit"])
async def test_map_lock_found_expired_on_release_succeeds_with_a_warning(
    env: SimpleNamespace, op: str, caplog: pytest.LogCaptureFixture
) -> None:
    document_id, (first, middle, last) = await _seed(env, RAW_TEXTS)
    real_map_lock = chunks_module.document_map_lock

    @contextlib.asynccontextmanager
    async def _expiring_map_lock(doc_id: int, **kwargs: Any) -> AsyncIterator[Any]:
        async with real_map_lock(doc_id, **kwargs) as lock:
            yield lock
        raise DocumentMapLockLost("expired")

    env.monkeypatch.setattr(chunks_module, "document_map_lock", _expiring_map_lock)

    with caplog.at_level(logging.DEBUG):
        response = await _operation(env, op, middle)

    assert response.status_code == (200 if op == "edit" else 204)
    if op == "edit":
        np.testing.assert_allclose(
            _faiss_vectors(VectorStore(), document_id)[middle], _unit(EDITED), rtol=1e-6
        )
    else:
        assert _faiss_ids(VectorStore(), document_id) == [first, last]
    records = [r for r in caplog.records if r.name == chunks_module.logger.name]
    assert [(r.levelno, r.getMessage()) for r in records] == [
        (logging.WARNING, f"Map lock of document_id={document_id} expired after its index update")
    ]


@pytest.mark.parametrize("op", ["delete", "edit"])
async def test_reread_failure_is_logged_with_its_own_tier(
    env: SimpleNamespace, op: str, caplog: pytest.LogCaptureFixture
) -> None:
    document_id, (_, middle, _) = await _seed(env, RAW_TEXTS)
    before = (_faiss_bytes(document_id), _bm25_bytes(document_id))
    real_map_lock = chunks_module.document_map_lock
    armed: List[bool] = []

    def _fail_select(conn: Any, cursor: Any, statement: str, *_: Any) -> None:
        if armed and statement.lstrip().upper().startswith("SELECT"):
            raise RuntimeError("reread refused")

    @contextlib.asynccontextmanager
    async def _arming_map_lock(doc_id: int, **kwargs: Any) -> AsyncIterator[Any]:
        async with real_map_lock(doc_id, **kwargs) as lock:
            armed.append(True)
            try:
                yield lock
            finally:
                armed.clear()

    engine = env.maker.kw["bind"]
    event.listen(engine.sync_engine, "before_cursor_execute", _fail_select)
    env.monkeypatch.setattr(chunks_module, "document_map_lock", _arming_map_lock)
    try:
        with caplog.at_level(logging.DEBUG):
            response = await _operation(env, op, middle)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _fail_select)

    assert response.status_code == 500
    assert (_faiss_bytes(document_id), _bm25_bytes(document_id)) == before
    errors = [r.getMessage() for r in caplog.records if r.name == chunks_module.logger.name]
    assert len(errors) == 1
    assert errors[0].endswith(f"reread index of document_id={document_id} failed: RuntimeError")


@pytest.mark.parametrize("op", ["delete", "edit"])
async def test_no_transaction_is_open_while_the_index_locks_are_awaited(
    env: SimpleNamespace, op: str
) -> None:
    document_id, (_, middle, _) = await _seed(env, RAW_TEXTS)
    engine = env.maker.kw["bind"].sync_engine
    open_transactions: set = set()
    seen: List[set] = []
    real_lock = chunks_module.document_index_lock

    def _began(conn: Any) -> None:
        open_transactions.add(id(conn.connection.dbapi_connection))

    def _ended(conn: Any) -> None:
        open_transactions.discard(id(conn.connection.dbapi_connection))

    @contextlib.asynccontextmanager
    async def _checking_lock(doc_id: int) -> AsyncIterator[None]:
        seen.append(set(open_transactions))
        async with real_lock(doc_id):
            yield

    listeners = (("begin", _began), ("commit", _ended), ("rollback", _ended))
    for name, listener in listeners:
        event.listen(engine, name, listener)
    env.monkeypatch.setattr(chunks_module, "document_index_lock", _checking_lock)
    try:
        response = await _operation(env, op, middle)
    finally:
        for name, listener in listeners:
            event.remove(engine, name, listener)

    assert response.status_code == (200 if op == "edit" else 204)
    assert seen == [set()]


async def _seed_detections(env: SimpleNamespace, document_id: int, chunk_ids: List[int]) -> None:
    """Store two detections per chunk, offsets into its raw text, as ingestion does."""
    async with env.maker() as session:
        session.add_all(
            EntityDetection(
                document_id=document_id, chunk_id=chunk_id, entity_type="PERSON_NAME",
                placeholder="[PERSON_NAME_1]", start_offset=start, end_offset=start + 8, score=0.9,
            )
            for chunk_id in chunk_ids
            for start in (0, 19)
        )
        await session.commit()


async def _detections_per_chunk(env: SimpleNamespace, document_id: int) -> dict:
    async with env.maker() as session:
        rows = await session.scalars(
            select(EntityDetection.chunk_id).where(EntityDetection.document_id == document_id)
        )
        counts: dict = {}
        for chunk_id in rows:
            counts[chunk_id] = counts.get(chunk_id, 0) + 1
        return counts


async def test_edit_chunk_deletes_only_its_own_detections(env: SimpleNamespace) -> None:
    document_id, (first, middle, last) = await _seed(env, RAW_TEXTS)
    await _seed_detections(env, document_id, [first, middle, last])
    _allow_edits(env)

    response = await env.client.patch(f"/api/chunks/{middle}", json={"sanitized_text": EDITED})

    assert response.status_code == 200
    assert await _detections_per_chunk(env, document_id) == {first: 2, last: 2}
    listed = await env.client.get(
        f"/api/documents/{document_id}/entity-detections", params={"chunk_id": middle}
    )
    assert listed.status_code == 200
    assert listed.json()["items"] == []


async def test_edit_chunk_title_only_keeps_its_detections(env: SimpleNamespace) -> None:
    document_id, (first, middle, last) = await _seed(env, RAW_TEXTS)
    await _seed_detections(env, document_id, [first, middle, last])
    _allow_edits(env)

    response = await env.client.patch(f"/api/chunks/{middle}", json={"section_title": "Deposit"})

    assert response.status_code == 200
    assert await _detections_per_chunk(env, document_id) == {first: 2, middle: 2, last: 2}


async def test_refused_edit_keeps_the_chunks_detections(env: SimpleNamespace) -> None:
    document_id, (first, middle, last) = await _seed(env, RAW_TEXTS)
    await _seed_detections(env, document_id, [first, middle, last])

    async def _run_replaced_while_gating(
        db: Any, settings: Any, document: Any, texts: Sequence[str], *_: Any, **__: Any
    ):
        async with env.maker() as session:
            await session.execute(
                update(Document).where(Document.id == document_id).values(ingestion_run_id="run-2")
            )
            await session.commit()
        return list(texts)

    env.monkeypatch.setattr(chunks_module, "gate_document_texts", _run_replaced_while_gating)

    response = await env.client.patch(f"/api/chunks/{middle}", json={"sanitized_text": EDITED})

    assert response.status_code == 409
    assert await _detections_per_chunk(env, document_id) == {first: 2, middle: 2, last: 2}
    assert tuple(await _chunk_row(env, middle)) == (TEXTS[1], RAW_TEXTS[1])
