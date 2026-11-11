"""Stored chunk settings can never make the sliding window crawl.

Rows written before the settings bounds existed, or by two PATCHes racing
each other, may hold any size/overlap pair; the chunker clamps them at the
point of use.
"""

from __future__ import annotations

from types import SimpleNamespace

from septum_api.request_limits import MIN_CHUNK_SIZE
from septum_api.services.document_pipeline import DocumentPipeline
from septum_api.services.ingestion.base import TextBlock

TEXT = "x" * 10_000
NO_OVERLAP_CHUNKS = -(-len(TEXT) // MIN_CHUNK_SIZE)


def _pipeline_with(**settings: int) -> DocumentPipeline:
    return DocumentPipeline(SimpleNamespace(**settings))


def test_pipeline_text_chunks_from_a_crawling_stored_pair_stay_bounded() -> None:
    pipeline = _pipeline_with(chunk_size=100, chunk_overlap=99, pdf_chunk_size=1200)

    chunks = pipeline._chunk_records("image", [TextBlock(TEXT, "text")], [])

    assert len(chunks) <= 2 * NO_OVERLAP_CHUNKS + 1
    assert all(len(chunk.text) <= MIN_CHUNK_SIZE for chunk in chunks)
