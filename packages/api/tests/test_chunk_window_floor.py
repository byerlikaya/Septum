"""Stored chunk settings can never make the sliding window crawl.

Rows written before the settings bounds existed, or by two PATCHes racing
each other, may hold any size/overlap pair; the chunker clamps them at the
point of use.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from septum_api.request_limits import (
    MAX_PRE_APPROVED_CHUNK_CHARS,
    MIN_CHUNK_SIZE,
)
from septum_api.services.chunking_strategy import SlidingWindowChunker
from septum_api.services.document_pipeline import DocumentPipeline

TEXT = "x" * 10_000
NO_OVERLAP_CHUNKS = -(-len(TEXT) // MIN_CHUNK_SIZE)


@pytest.mark.parametrize(
    "chunk_size,overlap",
    [(1, 0), (100, 99), (2_000, 1_999), (3 * MIN_CHUNK_SIZE, 3 * MIN_CHUNK_SIZE - 1)],
)
def test_every_step_advances_at_least_half_the_smallest_chunk(chunk_size: int, overlap: int) -> None:
    chunks = SlidingWindowChunker(chunk_size=chunk_size, overlap=overlap).chunk(TEXT)

    assert len(chunks) <= 2 * NO_OVERLAP_CHUNKS + 1
    assert all(len(chunk.text) <= max(chunk_size, MIN_CHUNK_SIZE) for chunk in chunks)
    assert "".join(chunk.text for chunk in chunks).startswith("x")


def test_oversized_chunk_size_is_capped_at_the_approval_limit() -> None:
    chunks = SlidingWindowChunker(chunk_size=10**9, overlap=0).chunk("x" * 50_000)

    assert max(len(chunk.text) for chunk in chunks) == MAX_PRE_APPROVED_CHUNK_CHARS


def test_valid_settings_are_used_unchanged() -> None:
    chunks = SlidingWindowChunker(chunk_size=800, overlap=200).chunk("y" * 2000)

    assert [len(chunk.text) for chunk in chunks] == [800, 800, 800]


def _pipeline_with(**settings: int) -> SimpleNamespace:
    return SimpleNamespace(
        _settings=SimpleNamespace(**settings),
        _merge_title_chunks=DocumentPipeline._merge_title_chunks,
    )


async def test_pipeline_text_chunks_from_a_crawling_stored_pair_stay_bounded() -> None:
    pipeline = _pipeline_with(chunk_size=100, chunk_overlap=99, pdf_chunk_size=1200)

    chunks = await DocumentPipeline._build_chunks(pipeline, "txt", TEXT)

    assert len(chunks) <= 2 * NO_OVERLAP_CHUNKS + 1
