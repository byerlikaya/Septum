"""A deleted chunk's id leaves its document's FAISS and BM25 indexes.

The index primitives run on real encrypted files with deterministic stub
embeddings (no model is ever loaded). The delete endpoint runs through ASGI
on SQLite (WAL, the production connect listener) and, when
``SEPTUM_TEST_POSTGRES_URL`` points at a throwaway database, on PostgreSQL
in a schema of its own: the re-read of the document under the locks is SQL
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
from septum_api.models.error_log import ErrorLog
from septum_api.models.user import User
from septum_api.routers import chunks as chunks_module
from septum_api.routers import documents as documents_module
from septum_api.services import document_pipeline as dp
from septum_api.services import index_file_io
from septum_api.services import vector_store as vector_store_module
from septum_api.services.bm25_retriever import TOKENIZER_VERSION, BM25Retriever
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
QUERIES = ["rent first day", "deposit landlord", "lease notice", "tenant lease"]
FAILURE_DETAIL = {
    "detail": (
        "The chunk was deleted, but the document's search index could not "
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


async def _seed(env: SimpleNamespace) -> tuple[int, List[int]]:
    """Store a ``completed`` document with three chunks and index them; return its ids."""
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
            Chunk(document_id=document.id, index=i, sanitized_text=t, raw_text=t, char_count=len(t))
            for i, t in enumerate(TEXTS)
        ]
        session.add_all(chunks)
        await session.commit()
        document_id, chunk_ids = document.id, [c.id for c in chunks]
    VectorStore().index_document(document_id, chunk_ids, TEXTS)
    BM25Retriever().index_document(document_id, chunk_ids, TEXTS, "en")
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
