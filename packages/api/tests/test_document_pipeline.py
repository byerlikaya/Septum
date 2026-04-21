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

