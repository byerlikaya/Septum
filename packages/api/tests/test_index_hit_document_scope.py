"""Index hits resolve only to chunks of the document whose index produced them.

FAISS and BM25 store bare chunk ids. A deleted chunk's id stays in its
document's index, and SQLite hands a freed top id to the next insert, which may
be another user's chunk. Every lookup of index hits must therefore be scoped to
the document the hit came from.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from document_env_fixtures import _Env
from septum_api.models.document import Chunk, Document
from septum_api.models.user import User
from septum_api.request_limits import TOP_K_RETRIEVAL_MAX_CAP
from septum_api.routers import chat
from septum_api.routers import chunks as chunks_router

_FOREIGN_RAW_TEXT = "Jane Roe, account 4111 1111 1111 1111"


async def _add_foreign_document(env: _Env) -> tuple[int, int]:
    """Store another user's document with one chunk; return ``(document_id, chunk_id)``."""
    async with env.session_maker() as session:
        other = User(email="other@example.com", hashed_password="x", role="editor")
        session.add(other)
        await session.flush()
        doc = Document(
            filename="foreign",
            original_filename="foreign.pdf",
            file_type="application/pdf",
            file_format="pdf",
            detected_language="en",
            encrypted_path=str(env.tmp / "foreign"),
            file_size_bytes=10,
            ocr_confidence=0.0,
            active_regulation_ids=[],
            ingestion_status="completed",
            chunk_count=1,
            user_id=other.id,
        )
        session.add(doc)
        await session.flush()
        chunk = Chunk(
            document_id=doc.id,
            index=0,
            sanitized_text="[PERSON_1], account [CARD_1]",
            raw_text=_FOREIGN_RAW_TEXT,
            char_count=29,
        )
        session.add(chunk)
        await session.commit()
        return doc.id, chunk.id


async def _reused_id_setup(env: _Env) -> tuple[int, int, int]:
    """Delete the owner's chunk, let another user's chunk take its id.

    Returns ``(owner_document_id, stale_chunk_id, foreign_document_id)``.
    """
    owner_doc, stale_id = await env.add_document()
    deleted = await env.client.delete(f"/api/chunks/{stale_id}")
    assert deleted.status_code == 204
    foreign_doc, foreign_chunk = await _add_foreign_document(env)
    assert foreign_chunk == stale_id, "SQLite must reuse the freed top chunk id"
    return owner_doc, stale_id, foreign_doc


class _StaleVectorStore:
    """Answers every search with the ids each document's index still holds."""

    def __init__(self, hits: dict[int, list[tuple[int, float]]]) -> None:
        self._hits = hits

    def search(self, document_id: int, query: str, top_k: int = 10) -> list[tuple[int, float]]:
        return self._hits.get(document_id, [])[:top_k]

    def hybrid_search(
        self, document_id: int, query: str, top_k: int, **_: Any
    ) -> list[tuple[int, float]]:
        return self.search(document_id, query, top_k)


async def _add_chunk(env: _Env, document_id: int, index: int) -> int:
    """Append a chunk to ``document_id`` and return its id."""
    async with env.session_maker() as session:
        chunk = Chunk(
            document_id=document_id,
            index=index,
            sanitized_text=f"[PERSON_1] clause {index}.",
            char_count=20,
        )
        session.add(chunk)
        await session.commit()
        return chunk.id


class _RecordingStore(_StaleVectorStore):
    def __init__(self, hits: dict[int, list[tuple[int, float]]]) -> None:
        super().__init__(hits)
        self.top_ks: list[int] = []

    def search(self, document_id: int, query: str, top_k: int = 10) -> list[tuple[int, float]]:
        self.top_ks.append(top_k)
        return super().search(document_id, query, top_k)


async def _no_bm25_rebuild(*_: Any) -> None:
    return None


def _stub_approval_indexes(monkeypatch: pytest.MonkeyPatch, store: _StaleVectorStore) -> None:
    monkeypatch.setattr(chat, "_get_vector_store", lambda: store)
    monkeypatch.setattr(chat, "_get_bm25_retriever", lambda: None)
    monkeypatch.setattr(chat, "_rebuild_outdated_bm25_index", _no_bm25_rebuild)


async def test_chunk_search_never_returns_another_documents_chunk(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_doc, stale_id, _ = await _reused_id_setup(env)
    store = _StaleVectorStore({owner_doc: [(stale_id, 0.9)]})
    monkeypatch.setattr(chunks_router, "VectorStore", lambda: store)

    response = await env.client.post(
        "/api/chunks/search", json={"document_id": owner_doc, "query": "account"}
    )

    assert response.status_code == 200
    assert response.json()["items"] == []
    assert _FOREIGN_RAW_TEXT not in response.text


async def test_chunk_search_keeps_the_documents_own_hits(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_doc, own_id = await env.add_document()
    store = _StaleVectorStore({owner_doc: [(own_id, 0.8)]})
    monkeypatch.setattr(chunks_router, "VectorStore", lambda: store)

    response = await env.client.post(
        "/api/chunks/search", json={"document_id": owner_doc, "query": "signed"}
    )

    items = response.json()["items"]
    assert [(i["chunk"]["id"], i["chunk"]["document_id"]) for i in items] == [(own_id, owner_doc)]


async def test_approval_retrieval_never_returns_another_documents_chunk(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_doc, stale_id, _ = await _reused_id_setup(env)
    _stub_approval_indexes(monkeypatch, _StaleVectorStore({owner_doc: [(stale_id, 1.0)]}))

    async with env.session_maker() as db:
        found = await chat._retrieve_chunks(db, owner_doc, "account", top_k=5)

    assert found == []


async def test_cross_document_retrieval_matches_hits_to_their_own_document(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_doc, stale_id, foreign_doc = await _reused_id_setup(env)
    second_doc, second_id = await env.add_document()
    store = _StaleVectorStore({owner_doc: [(stale_id, 0.9)], second_doc: [(second_id, 0.8)]})
    monkeypatch.setattr(chat, "_get_vector_store", lambda: store)

    async with env.session_maker() as db:
        owner_docs = [await db.get(Document, owner_doc), await db.get(Document, second_doc)]
        found, matched = await chat._retrieve_chunks_all_documents(
            db, "account", top_k=5, documents=owner_docs, relevance_threshold=0.1
        )

    assert [(c.id, c.document_id) for c in found] == [(second_id, second_doc)]
    assert matched == [second_doc]
    assert foreign_doc not in {c.document_id for c in found}


async def test_cross_document_retrieval_drops_a_hit_whose_id_moved_to_another_candidate(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_doc, stale_id = await env.add_document()
    assert (await env.client.delete(f"/api/chunks/{stale_id}")).status_code == 204
    second_doc, second_id = await env.add_document()
    assert second_id == stale_id
    store = _StaleVectorStore({first_doc: [(stale_id, 0.9)], second_doc: [(second_id, 0.05)]})
    monkeypatch.setattr(chat, "_get_vector_store", lambda: store)

    async with env.session_maker() as db:
        candidates = [await db.get(Document, first_doc), await db.get(Document, second_doc)]
        found, matched = await chat._retrieve_chunks_all_documents(
            db, "account", top_k=5, documents=candidates, relevance_threshold=0.1
        )

    assert found == [] and matched == []


async def test_cross_document_retrieval_pair_match_not_id_and_document_in_sets(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc_a, id_x = await env.add_document()
    assert (await env.client.delete(f"/api/chunks/{id_x}")).status_code == 204
    doc_b, b_first = await env.add_document()
    assert b_first == id_x, "SQLite must reuse the freed top chunk id"
    id_y = await _add_chunk(env, doc_b, 1)
    store = _StaleVectorStore({doc_a: [(id_x, 0.9)], doc_b: [(id_y, 0.8)]})
    monkeypatch.setattr(chat, "_get_vector_store", lambda: store)

    async with env.session_maker() as db:
        candidates = [await db.get(Document, doc_a), await db.get(Document, doc_b)]
        found, matched = await chat._retrieve_chunks_all_documents(
            db, "account", top_k=5, documents=candidates, relevance_threshold=0.1
        )

    assert [(c.id, c.document_id) for c in found] == [(id_y, doc_b)]
    assert matched == [doc_b]


async def test_approval_retrieval_keeps_own_hit_and_drops_stale_hit(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_doc, stale_id, _ = await _reused_id_setup(env)
    own_id = await _add_chunk(env, owner_doc, 1)
    _stub_approval_indexes(
        monkeypatch, _StaleVectorStore({owner_doc: [(stale_id, 1.0), (own_id, 0.9)]})
    )

    async with env.session_maker() as db:
        found = await chat._retrieve_chunks(db, owner_doc, "account", top_k=5)

    assert [c.id for c in found] == [own_id]


async def test_approval_retrieval_all_hits_stale_yields_empty_context(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_doc, stale_id, _ = await _reused_id_setup(env)
    _stub_approval_indexes(
        monkeypatch,
        _StaleVectorStore({owner_doc: [(stale_id, 1.0), (stale_id, 0.9)]}),
    )

    async with env.session_maker() as db:
        found = await chat._retrieve_chunks(db, owner_doc, "account", top_k=5)

    assert found == []


async def test_chunk_search_rejects_non_owner_with_404(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    foreign_doc, foreign_chunk = await _add_foreign_document(env)
    store = _RecordingStore({foreign_doc: [(foreign_chunk, 0.9)]})
    monkeypatch.setattr(chunks_router, "VectorStore", lambda: store)

    foreign = await env.client.post(
        "/api/chunks/search", json={"document_id": foreign_doc, "query": "account"}
    )
    unknown = await env.client.post(
        "/api/chunks/search", json={"document_id": 987654, "query": "account"}
    )

    assert foreign.status_code == 404
    assert _FOREIGN_RAW_TEXT not in foreign.text
    assert "[PERSON_1]" not in foreign.text
    assert unknown.status_code == 404
    assert foreign.json() == unknown.json()
    assert store.top_ks == []


async def test_chunk_search_skips_deleted_id_without_reuse(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc, kept_id = await env.add_document()
    deleted_id = await _add_chunk(env, doc, 1)
    assert (await env.client.delete(f"/api/chunks/{deleted_id}")).status_code == 204
    store = _StaleVectorStore({doc: [(deleted_id, 0.9), (kept_id, 0.8)]})
    monkeypatch.setattr(chunks_router, "VectorStore", lambda: store)

    response = await env.client.post(
        "/api/chunks/search", json={"document_id": doc, "query": "signed"}
    )

    assert response.status_code == 200
    assert [i["chunk"]["id"] for i in response.json()["items"]] == [kept_id]


async def test_chunk_search_top_k_above_the_cap_is_rejected(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc, chunk_id = await env.add_document()
    store = _RecordingStore({doc: [(chunk_id, 0.9)]})
    monkeypatch.setattr(chunks_router, "VectorStore", lambda: store)

    async def search(**extra: Any) -> int:
        body = {"document_id": doc, "query": "signed", **extra}
        return (await env.client.post("/api/chunks/search", json=body)).status_code

    assert await search(top_k=TOP_K_RETRIEVAL_MAX_CAP + 1) == 422
    assert await search(top_k=10**7) == 422
    assert store.top_ks == []
    assert await search(top_k=TOP_K_RETRIEVAL_MAX_CAP) == 200
    assert await search() == 200
    assert store.top_ks == [TOP_K_RETRIEVAL_MAX_CAP, 10]


async def test_chunk_search_non_positive_top_k_is_served_as_the_default(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc, chunk_id = await env.add_document()
    store = _RecordingStore({doc: [(chunk_id, 0.9)]})
    monkeypatch.setattr(chunks_router, "VectorStore", lambda: store)

    for value in (0, -3):
        response = await env.client.post(
            "/api/chunks/search", json={"document_id": doc, "query": "signed", "top_k": value}
        )
        assert response.status_code == 200

    assert store.top_ks == [10, 10]


async def test_auto_retrieval_orders_by_id_and_document_pair(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc_a, id_x = await env.add_document()
    assert (await env.client.delete(f"/api/chunks/{id_x}")).status_code == 204
    doc_b, b_first = await env.add_document()
    assert b_first == id_x
    id_y = await _add_chunk(env, doc_b, 1)
    store = _StaleVectorStore(
        {doc_a: [(id_x, 0.1)], doc_b: [(id_x, 0.9), (id_y, 0.85)]}
    )
    monkeypatch.setattr(chat, "_get_vector_store", lambda: store)

    async with env.session_maker() as db:
        candidates = [await db.get(Document, doc_a), await db.get(Document, doc_b)]
        found, matched = await chat._retrieve_chunks_all_documents(
            db, "account", top_k=5, documents=candidates, relevance_threshold=0.05
        )

    assert [(c.id, c.document_id) for c in found] == [(id_x, doc_b), (id_y, doc_b)]
    assert matched == [doc_b]


async def test_manual_multi_document_retrieval_returns_a_reused_id_once_under_its_owner(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc_a, id_x = await env.add_document()
    assert (await env.client.delete(f"/api/chunks/{id_x}")).status_code == 204
    doc_b, b_first = await env.add_document()
    assert b_first == id_x
    _stub_approval_indexes(
        monkeypatch, _StaleVectorStore({doc_a: [(id_x, 1.0)], doc_b: [(id_x, 1.0)]})
    )

    async with env.session_maker() as db:
        documents = {d: await db.get(Document, d) for d in (doc_a, doc_b)}
        turn = SimpleNamespace(
            documents_by_id=documents,
            request=SimpleNamespace(top_k=None),
            settings=SimpleNamespace(rag_relevance_threshold=0.1, top_k_retrieval=10),
            session_id="s",
            sanitized_query="account",
            chunks=[],
        )
        await chat._retrieve_manual_chunks(turn, db)

    assert [(c.id, c.document_id) for c in turn.chunks] == [(id_x, doc_b)]
