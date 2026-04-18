from __future__ import annotations

"""Tests for auto-RAG routing: cross-document retrieval and mode selection."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from septum_api.routers.chat import (
    _DEFAULT_RELEVANCE_THRESHOLD,
    _retrieve_chunks_all_documents,
)


def _make_doc(doc_id: int, user_id: int = 1, chunk_count: int = 10):
    doc = MagicMock()
    doc.id = doc_id
    doc.user_id = user_id
    doc.ingestion_status = "completed"
    doc.chunk_count = chunk_count
    doc.original_filename = f"doc_{doc_id}.pdf"
    return doc


def _make_chunk(chunk_id: int, document_id: int):
    chunk = MagicMock()
    chunk.id = chunk_id
    chunk.document_id = document_id
    chunk.index = chunk_id
    chunk.sanitized_text = f"chunk {chunk_id} text"
    return chunk


class TestRetrieveChunksAllDocuments:
    """Tests for the _retrieve_chunks_all_documents helper.

    Auto-RAG uses FAISS cosine similarity (not hybrid RRF) so scores
    are absolute [0, 1] — a greeting like "Merhaba" scores <0.2 against
    document text, while a real question scores >0.35.
    """

    @pytest.mark.asyncio
    async def test_no_documents_returns_empty(self) -> None:
        db = AsyncMock(spec=AsyncSession)

        chunks, doc_ids = await _retrieve_chunks_all_documents(
            db=db, query="test", top_k=5, documents=[]
        )
        assert chunks == []
        assert doc_ids == []

    @pytest.mark.asyncio
    async def test_single_doc_high_cosine(self) -> None:
        doc1 = _make_doc(1)
        chunk_obj = _make_chunk(101, 1)

        db = AsyncMock(spec=AsyncSession)
        chunk_result = MagicMock()
        chunk_result.scalars.return_value.all.return_value = [chunk_obj]
        db.execute = AsyncMock(return_value=chunk_result)

        mock_vs = MagicMock()
        mock_vs.search.return_value = [(101, 0.55)]

        with patch("septum_api.routers.chat._get_vector_store", return_value=mock_vs):
            chunks, doc_ids = await _retrieve_chunks_all_documents(
                db=db, query="test", top_k=5,
                documents=[doc1],
                relevance_threshold=0.35,
            )

        assert len(chunks) == 1
        assert chunks[0].id == 101
        assert doc_ids == [1]

    @pytest.mark.asyncio
    async def test_greeting_low_cosine_returns_empty(self) -> None:
        """A greeting like 'Merhaba' gets low cosine similarity and is filtered."""
        doc1 = _make_doc(1)
        db = AsyncMock(spec=AsyncSession)

        mock_vs = MagicMock()
        mock_vs.search.return_value = [(101, 0.12)]

        with patch("septum_api.routers.chat._get_vector_store", return_value=mock_vs):
            chunks, doc_ids = await _retrieve_chunks_all_documents(
                db=db, query="Merhaba", top_k=5,
                documents=[doc1],
                relevance_threshold=0.35,
            )

        assert chunks == []
        assert doc_ids == []

    @pytest.mark.asyncio
    async def test_multi_doc_merges_results(self) -> None:
        doc1 = _make_doc(1)
        doc2 = _make_doc(2)
        chunk1 = _make_chunk(101, 1)
        chunk2 = _make_chunk(201, 2)

        db = AsyncMock(spec=AsyncSession)
        chunk_result = MagicMock()
        chunk_result.scalars.return_value.all.return_value = [chunk1, chunk2]
        db.execute = AsyncMock(return_value=chunk_result)

        mock_vs = MagicMock()
        mock_vs.search.side_effect = [
            [(101, 0.6)],
            [(201, 0.45)],
        ]

        with patch("septum_api.routers.chat._get_vector_store", return_value=mock_vs):
            chunks, doc_ids = await _retrieve_chunks_all_documents(
                db=db, query="test", top_k=5,
                documents=[doc1, doc2],
                relevance_threshold=0.35,
            )

        assert len(chunks) == 2
        assert sorted(doc_ids) == [1, 2]

    @pytest.mark.asyncio
    async def test_only_relevant_doc_survives(self) -> None:
        """Doc1 has high cosine sim, doc2 has low — only doc1's chunks survive."""
        doc1 = _make_doc(1)
        doc2 = _make_doc(2)
        chunk1 = _make_chunk(101, 1)

        db = AsyncMock(spec=AsyncSession)
        chunk_result = MagicMock()
        chunk_result.scalars.return_value.all.return_value = [chunk1]
        db.execute = AsyncMock(return_value=chunk_result)

        mock_vs = MagicMock()
        mock_vs.search.side_effect = [
            [(101, 0.55)],
            [(201, 0.10)],
        ]

        with patch("septum_api.routers.chat._get_vector_store", return_value=mock_vs):
            chunks, doc_ids = await _retrieve_chunks_all_documents(
                db=db, query="test", top_k=5,
                documents=[doc1, doc2],
                relevance_threshold=0.35,
            )

        assert len(chunks) == 1
        assert chunks[0].id == 101
        assert doc_ids == [1]


class TestDefaultThreshold:
    def test_default_threshold_value(self) -> None:
        assert _DEFAULT_RELEVANCE_THRESHOLD == 0.35
