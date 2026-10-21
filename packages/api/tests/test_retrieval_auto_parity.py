"""The harness's auto_rag replica must match chat's cross-document retrieval.

Both sides run against the same fake vector store, so a change to the per-document
candidate count, the sort, the threshold or the context cap in chat fails here.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from septum_api.request_limits import MAX_CONTEXT_CHUNKS
from septum_api.routers import chat
from tests import benchmark_retrieval as harness

CHUNKS_PER_DOC = 12


class _FakeStore:
    def __init__(self, doc_ids: list[int]) -> None:
        self.calls: list[tuple[int, int]] = []
        self._scores = {
            doc: [(doc * 100 + i, 0.9 - 0.2 * i - 0.01 * doc) for i in range(CHUNKS_PER_DOC)]
            for doc in doc_ids
        }

    def search(self, doc_id: int, query: str, top_k: int) -> list[tuple[int, float]]:
        self.calls.append((doc_id, top_k))
        return self._scores[doc_id][:top_k]


class _FakeResult:
    def scalars(self) -> "_FakeResult":
        return self

    def all(self) -> list:
        return []


class _FakeDb:
    def __init__(self) -> None:
        self.statements: list = []

    async def execute(self, stmt) -> _FakeResult:
        self.statements.append(stmt)
        return _FakeResult()


def _chat_run(
    store: _FakeStore, doc_ids: list[int], monkeypatch: pytest.MonkeyPatch
) -> tuple[list[int], list[tuple[int, int]]]:
    monkeypatch.setattr(chat, "_get_vector_store", lambda: store)
    db = _FakeDb()
    documents = [SimpleNamespace(id=doc) for doc in doc_ids]
    asyncio.run(chat._retrieve_chunks_all_documents(db, "question", harness.TOP_K, documents))
    chunk_ids: list[int] = []
    for stmt in db.statements:
        params = stmt.compile().params
        chunk_ids.extend(next(iter(params.values())))
    return chunk_ids, sorted(store.calls)


@pytest.mark.parametrize("doc_count", [1, 5, 9, 20])
def test_auto_rag_matches_chat_candidates_and_threshold(
    doc_count: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc_ids = list(range(1, doc_count + 1))
    chat_store = _FakeStore(doc_ids)
    chat_ids, chat_calls = _chat_run(chat_store, doc_ids, monkeypatch)

    harness_store = _FakeStore(doc_ids)
    index = harness.Index(store=harness_store, bm25=None, doc_keys=doc_ids, chunks={})
    merged = harness._dense_all_documents(index, None, "question")
    retriever = harness.RETRIEVERS["auto_rag"]
    kept = harness._apply_threshold(merged, retriever.threshold)

    assert chat_calls and chat_ids
    assert sorted(harness_store.calls) == chat_calls
    assert [cid for cid, _ in kept] == chat_ids
    assert len(kept) <= min(harness.TOP_K * 3, MAX_CONTEXT_CHUNKS)


def test_auto_rag_threshold_is_chat_default() -> None:
    assert harness.RETRIEVERS["auto_rag"].threshold == chat._DEFAULT_RELEVANCE_THRESHOLD


def test_parity_data_exercises_threshold_and_cap() -> None:
    doc_ids = list(range(1, 21))
    store = _FakeStore(doc_ids)
    index = harness.Index(store=store, bm25=None, doc_keys=doc_ids, chunks={})
    merged = harness._dense_all_documents(index, None, "question")
    threshold = harness.RETRIEVERS["auto_rag"].threshold
    assert any(score < threshold for _, score in merged)
    assert len(harness._apply_threshold(merged, threshold)) == min(harness.TOP_K * 3, MAX_CONTEXT_CHUNKS)
