"""Chunker v2: pure, block-aware chunking that respects protected ranges."""

from .records import ChunkRecord
from .strategies import Strategy, chunk_blocks

CHUNKER_VERSION = "2026.11.11.2"
"""Names the chunking behaviour; part of the policy digest, so a change flags documents stale."""

__all__ = ["CHUNKER_VERSION", "ChunkRecord", "Strategy", "chunk_blocks"]
